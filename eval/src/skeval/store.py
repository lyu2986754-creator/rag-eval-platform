"""数据集与运行记录的读写。CLI 与 API 共用同一份实现，避免两边逻辑漂移。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .dataset import Case, load_cases, validate_cases

# 均相对 eval/ 目录
DATASETS_DIR = Path("datasets")
RUNS_DIR = Path("runs")
CORPUS_DIR = Path("corpus/raw")


class StoreError(RuntimeError):
    """数据集或运行记录不存在／格式不对。"""


@dataclass(frozen=True, slots=True)
class RunMeta:
    run_id: str
    dataset: str
    case_count: int
    started_at: str
    finished_at: str
    config: dict


def _safe_name(name: str) -> str:
    """只允许文件名本身，防止路径穿越。"""
    if not name or Path(name).name != name:
        raise StoreError(f"非法的名称：{name!r}")
    return name


def list_datasets(root: Path) -> list[dict]:
    directory = root / DATASETS_DIR
    if not directory.is_dir():
        return []
    items: list[dict] = []
    for path in sorted(directory.glob("*.jsonl")):
        try:
            cases = load_cases(path)
        except Exception as exc:  # noqa: BLE001 - 列表页遇到坏文件不应整体失败
            items.append({"name": path.name, "case_count": 0, "error": str(exc)})
            continue
        items.append(
            {
                "name": path.name,
                "case_count": len(cases),
                "categories": sorted({c.category for c in cases}),
                "difficulties": sorted({c.difficulty for c in cases}),
                "refuse_count": sum(1 for c in cases if c.should_refuse),
            }
        )
    return items


def load_dataset(root: Path, name: str) -> tuple[Path, list[Case]]:
    path = root / DATASETS_DIR / _safe_name(name)
    if not path.is_file():
        raise StoreError(f"数据集不存在：{name}")
    return path, load_cases(path)


def validate_dataset(root: Path, name: str) -> list[str]:
    _, cases = load_dataset(root, name)
    return validate_cases(cases, root / CORPUS_DIR)


def list_runs(root: Path) -> list[RunMeta]:
    directory = root / RUNS_DIR
    if not directory.is_dir():
        return []
    metas: list[RunMeta] = []
    for path in directory.glob("*.meta.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        config = raw.get("config") or {}
        metas.append(
            RunMeta(
                run_id=str(raw.get("run_id") or path.name.removesuffix(".meta.json")),
                dataset=str(config.get("dataset") or ""),
                case_count=int(raw.get("case_count") or 0),
                started_at=str(raw.get("started_at") or ""),
                finished_at=str(raw.get("finished_at") or ""),
                config=config,
            )
        )
    metas.sort(key=lambda m: m.started_at, reverse=True)
    return metas


def load_run_records(root: Path, run_id: str) -> list[dict]:
    path = root / RUNS_DIR / f"{_safe_name(run_id)}.jsonl"
    if not path.is_file():
        raise StoreError(f"运行记录不存在：{run_id}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_report_markdown(root: Path, run_id: str) -> str:
    path = root / RUNS_DIR / f"{_safe_name(run_id)}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def get_run_meta(root: Path, run_id: str) -> RunMeta | None:
    return next((m for m in list_runs(root) if m.run_id == run_id), None)


def summarize(records: list[dict], should_refuse_by_id: dict[str, bool]) -> dict:
    """从逐条记录算出报告要用的汇总。与 report.render 的口径保持一致。

    汇总有两条剔除规则，都是"不要白送分数"：
      1. **调用失败**是平台侧故障，与被测系统能力无关 → 剔除；
      2. **不适用的评分**（拒答题在 retrieval_hit / citation_support / points_hit 上
         本就无从判定）→ 剔除。把它们当"通过"计进分母，会让通过率虚高。
    """
    from .report import FAILURE_CALL_ERROR, classify
    from .scorers import SCORER_NAMES

    class _Record:
        """让 classify 能直接吃 dict，避免为了汇总再把记录反序列化成 dataclass。"""

        def __init__(self, raw: dict) -> None:
            self.__dict__.update(raw)

        def score(self, name: str) -> dict | None:
            return next((s for s in self.scores if s["name"] == name), None)

    failures = {
        raw["case_id"]: classify(_Record(raw), should_refuse_by_id.get(raw["case_id"], False))
        for raw in records
    }
    call_errors = sum(1 for v in failures.values() if v == FAILURE_CALL_ERROR)
    effective = [raw for raw in records if failures[raw["case_id"]] != FAILURE_CALL_ERROR]

    def _metric(items: list[dict]) -> dict:
        if not items:
            return {"average": 0.0, "pass_rate": 0.0, "count": 0}
        return {
            "average": sum(float(i["value"]) for i in items) / len(items),
            "pass_rate": sum(1 for i in items if i["passed"]) / len(items),
            "count": len(items),
        }

    def _applicable_scores(raws: list[dict], name: str) -> list[dict]:
        return [
            s
            for raw in raws
            for s in raw.get("scores", [])
            # 旧 run 没有 applicable 字段，按"适用"处理，保持历史口径
            if s["name"] == name and s.get("applicable", True)
        ]

    by_category: dict[str, dict] = {}
    for raw in effective:
        bucket = by_category.setdefault(raw["category"], {"records": [], "call_errors": 0})
        bucket["records"].append(raw)
    for raw in records:
        if failures[raw["case_id"]] == FAILURE_CALL_ERROR:
            bucket = by_category.setdefault(raw["category"], {"records": [], "call_errors": 0})
            bucket["call_errors"] += 1

    effective_latency = [int(raw.get("latency_ms") or 0) for raw in effective]

    return {
        "total": len(records),
        "effective_total": len(effective),
        "call_errors": call_errors,
        "multi_turn_total": sum(1 for raw in records if raw.get("prior_turns")),
        "passed": sum(1 for v in failures.values() if v is None),
        "average_latency_ms": (
            sum(effective_latency) / len(effective_latency) if effective_latency else 0
        ),
        "layers": {name: _metric(_applicable_scores(effective, name)) for name in SCORER_NAMES},
        "by_category": {
            category: {
                "total": len(bucket["records"]),
                "passed": sum(1 for r in bucket["records"] if failures[r["case_id"]] is None),
                "call_errors": bucket["call_errors"],
                "layers": {
                    name: _metric(_applicable_scores(bucket["records"], name))
                    for name in SCORER_NAMES
                },
            }
            for category, bucket in by_category.items()
        },
        "failures": {k: v for k, v in failures.items() if v},
    }
