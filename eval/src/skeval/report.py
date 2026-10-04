"""把一轮评测结果渲染成 Markdown 报告。

报告的重点不是"总分多少"，而是**失败归因**：
分不清"没召回到"和"召回到了但没答对"，就不知道该改检索还是改 prompt。
"""

from __future__ import annotations

from collections import Counter, defaultdict

from .runner import CaseRecord, RunResult
from .scorers import SCORER_NAMES

# 失败归因，按优先级从高到低判定
FAILURE_SHOULD_HAVE_REFUSED = "该拒答没拒答"
FAILURE_SPURIOUS_REFUSAL = "无端拒答"
FAILURE_HALLUCINATION = "幻觉（答案无召回依据）"
FAILURE_NOT_RETRIEVED = "没召回到"
FAILURE_RETRIEVED_WRONG = "召回到了但没答对"
FAILURE_CALL_ERROR = "调用失败"

_PRIORITY = (
    FAILURE_SHOULD_HAVE_REFUSED,
    FAILURE_SPURIOUS_REFUSAL,
    FAILURE_HALLUCINATION,
    FAILURE_NOT_RETRIEVED,
    FAILURE_RETRIEVED_WRONG,
)


def classify(record: CaseRecord, should_refuse: bool) -> str | None:
    """给单条记录定一个失败原因；完全通过则返回 None。"""
    if record.status != "ok":
        return FAILURE_CALL_ERROR

    refusal = record.score("refusal") or {}
    retrieval = record.score("retrieval_hit") or {}
    points = record.score("points_hit") or {}
    citation = record.score("citation_support") or {}
    retrieval_failed = not retrieval.get("passed", True)
    points_hit = float(points.get("value") or 0.0)

    if refusal and not refusal.get("passed"):
        return FAILURE_SHOULD_HAVE_REFUSED if should_refuse else FAILURE_SPURIOUS_REFUSAL
    # 幻觉的直接证据优先于间接推断：答案引用了召回片段里**根本不存在的条文号**。
    # 旧 run 没有这个评分器，score() 返回 None，这里自然跳过——历史结论不受影响。
    if citation and not citation.get("passed", True):
        return FAILURE_HALLUCINATION
    if retrieval_failed:
        return FAILURE_HALLUCINATION if points_hit > 0 else FAILURE_NOT_RETRIEVED
    if points_hit < 1.0:
        return FAILURE_RETRIEVED_WRONG
    return None


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _applicable_pass_rate(records: list[CaseRecord], name: str) -> str:
    """某个评分器在给定记录上的通过率，**只统计适用的条目**。

    不适用的条目（如拒答题上的检索命中率）不参与计算——把它们当通过计入，
    等于白送分数，会让这一行的数字虚高。没有适用条目时返回 "—"。
    """
    items = [
        score
        for record in records
        for score in record.scores
        if score["name"] == name and score.get("applicable", True)
    ]
    if not items:
        return "—"
    return _pct(_mean([1.0 if i["passed"] else 0.0 for i in items]))


def render(
    result: RunResult,
    *,
    should_refuse_by_id: dict[str, bool] | None = None,
) -> str:
    should_refuse_by_id = should_refuse_by_id or {}
    records = result.records
    total = len(records)

    failures: dict[str, str | None] = {
        r.case_id: classify(r, should_refuse_by_id.get(r.case_id, False)) for r in records
    }
    passed = sum(1 for v in failures.values() if v is None)
    # 调用失败是**平台侧**的失败（网络、上游 5xx、流被掐断），不是被测系统的能力问题。
    # 把它算进分母等于把平台自己的问题算到对方头上，所以单独摘出来，只统计有效样本。
    call_errors = sum(1 for v in failures.values() if v == FAILURE_CALL_ERROR)
    effective = total - call_errors

    lines: list[str] = []
    lines.append(f"# 评测报告 {result.run_id}")
    lines.append("")
    lines.append(f"- 用例数：**{total}**")
    if call_errors:
        lines.append(f"- 调用失败：**{call_errors}** 条（平台侧故障，不计入通过率）")
        lines.append(f"- 有效样本：**{effective}** 条")
    lines.append(
        f"- 全部通过：**{passed}/{effective}**（{_pct(passed / effective if effective else 0)}）"
    )
    lines.append(f"- 平均耗时：**{_mean([r.latency_ms for r in records]) / 1000:.1f} 秒**")
    multi_turn = sum(1 for r in records if getattr(r, "prior_turns", None))
    if multi_turn:
        lines.append(
            f"- 多轮用例：**{multi_turn}** 条（其余 {effective - multi_turn} 条为单轮；"
            "多轮的耗时与 token 是**所有轮次之和**）"
        )
    lines.append(f"- 开始：{result.started_at}")
    lines.append(f"- 结束：{result.finished_at}")
    lines.append("")

    lines.append("## 配置快照")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("| --- | --- |")
    for key, value in result.config_snapshot.items():
        lines.append(f"| {key} | `{value}` |")
    lines.append("")

    lines.append("## 分层得分")
    lines.append("")
    if call_errors:
        lines.append(f"> 已剔除 {call_errors} 条调用失败（平台侧故障），下表只统计有效样本。")
        lines.append("")
    lines.append(
        "> 拒答等**指标不适用**的条目也已从分子分母中剔除——把它们当「通过」计入，等于白送分数。"
        "「适用样本」列即真正参与该指标计算的条目数。"
    )
    lines.append("")
    lines.append("| 层 | 指标 | 平均分 | 通过率 | 适用样本 |")
    lines.append("| --- | --- | --- | --- | --- |")
    layer_of = {s["name"]: s["layer"] for record in records for s in record.scores}
    by_name: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        # 调用失败会产出全 0 的分数，混进来会把检索命中率也一起拉低
        if failures[record.case_id] == FAILURE_CALL_ERROR:
            continue
        for score in record.scores:
            if not score.get("applicable", True):
                continue
            by_name[score["name"]].append(score)
    for name in SCORER_NAMES:
        items = by_name.get(name, [])
        layer = layer_of.get(name, "")
        if not items:
            lines.append(f"| {layer} | `{name}` | — | — | 0 |")
            continue
        avg = _mean([float(i["value"]) for i in items])
        pass_rate = _mean([1.0 if i["passed"] else 0.0 for i in items])
        lines.append(f"| {layer} | `{name}` | {avg:.2f} | {_pct(pass_rate)} | {len(items)} |")
    lines.append("")

    lines.append("## 按题型分解")
    lines.append("")
    lines.append("| 题型 | 有效样本 | 通过 | 检索命中率 | 要点全中率 |")
    lines.append("| --- | --- | --- | --- | --- |")
    by_category: dict[str, list[CaseRecord]] = defaultdict(list)
    for record in records:
        by_category[record.category].append(record)
    for category in sorted(by_category):
        group = [r for r in by_category[category] if failures[r.case_id] != FAILURE_CALL_ERROR]
        if not group:
            lines.append(f"| {category} | 0 | — | — | — |")
            continue
        ok = sum(1 for r in group if failures[r.case_id] is None)
        retrieval = _applicable_pass_rate(group, "retrieval_hit")
        points = _applicable_pass_rate(group, "points_hit")
        lines.append(
            f"| {category} | {len(group)} | {ok} | {retrieval} | {points} |"
        )
    lines.append("")

    lines.append("## 失败归因")
    lines.append("")
    counter = Counter(v for v in failures.values() if v)
    if not counter:
        lines.append("全部通过，无失败。")
    else:
        lines.append("| 归因 | 数量 |")
        lines.append("| --- | --- |")
        for name in _PRIORITY:
            if counter.get(name):
                lines.append(f"| {name} | {counter[name]} |")
        if counter.get(FAILURE_CALL_ERROR):
            lines.append(f"| {FAILURE_CALL_ERROR} | {counter[FAILURE_CALL_ERROR]} |")
            lines.append("")
            lines.append(
                "> 「调用失败」是**平台侧**故障（网络超时 / 上游 5xx / 流被掐断），"
                "与模型能力无关，**不计入通过率**；它同样不进上面的分层得分与题型统计。"
            )
    lines.append("")

    lines.append("## 逐条明细")
    lines.append("")
    lines.append("| 用例 | 题型 | 难度 | 检索 | 要点 | 拒答 | 耗时 | 归因 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for record in records:
        retrieval = record.score("retrieval_hit") or {}
        points = record.score("points_hit") or {}
        refusal = record.score("refusal") or {}
        lines.append(
            "| `{id}` | {cat} | {diff} | {r} | {p} | {f} | {ms}ms | {fail} |".format(
                id=record.case_id,
                cat=record.category,
                diff=record.difficulty,
                r="✓" if retrieval.get("passed") else "✗",
                p=_pct(float(points.get("value") or 0)),
                f="✓" if refusal.get("passed") else "✗",
                ms=record.latency_ms,
                fail=failures[record.case_id] or "",
            )
        )
    lines.append("")

    lines.append("## 失败详情")
    lines.append("")
    any_detail = False
    for record in records:
        reason = failures[record.case_id]
        if reason is None:
            continue
        any_detail = True
        lines.append(f"### `{record.case_id}` — {reason}")
        lines.append("")
        lines.append(f"- 问题：{record.question}")
        lines.append(f"- 答案：{record.answer[:400]}")
        for score in record.scores:
            lines.append(f"- `{score['name']}`：{score['reason']}")
        if record.error:
            lines.append(f"- 错误：{record.error}")
        lines.append("")
    if not any_detail:
        lines.append("无。")

    return "\n".join(lines)
