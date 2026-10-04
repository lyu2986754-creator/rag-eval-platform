"""评测条目的结构定义与校验。

存储用 JSONL（每行一个条目）：标准库即可读写、便于逐条增删、diff 也看得懂。
不用 YAML 是为了少一个第三方依赖（project.md 第 8 节）。

为什么要有 expected_sources：
    结果层评分只知道"答案对不对"，无法回答"检索有没有召回被问到的那一条"。
    评测平台的核心差异就是过程层归因，而条文号是这件事的锚点。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..constants import CaseCategory, Difficulty
from ..corpus.articles import SourceRefError, find_article, load_corpus_articles, parse_source


class DatasetError(RuntimeError):
    """数据集本身有问题（缺字段、条文号对不上语料等）。"""


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    category: str
    difficulty: str
    question: str
    expected_sources: tuple[str, ...] = ()
    expected_points: tuple[str, ...] = ()
    reference_answer: str = ""
    should_refuse: bool = False
    notes: str = ""
    # 多轮用例：当前问题**之前**已经问过的轮次（按顺序）。
    # 只记用户说过的话，不记模型回过的内容——上下文由被测系统自己按 windowId 维护，
    # 评测平台只负责"把这几句话按顺序问出去"。`question` 始终是**被评分的那一轮**。
    prior_turns: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict) -> Case:
        missing = [k for k in ("id", "category", "difficulty", "question") if not raw.get(k)]
        if missing:
            raise DatasetError(f"条目缺少必填字段 {missing}: {raw!r}")
        return cls(
            id=str(raw["id"]),
            category=str(raw["category"]),
            difficulty=str(raw["difficulty"]),
            question=str(raw["question"]),
            expected_sources=tuple(raw.get("expected_sources") or ()),
        expected_points=tuple(raw.get("expected_points") or ()),
        reference_answer=str(raw.get("reference_answer") or ""),
        should_refuse=bool(raw.get("should_refuse", False)),
        notes=str(raw.get("notes") or ""),
        prior_turns=tuple(raw.get("prior_turns") or ()),
    )

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("expected_sources", "expected_points", "prior_turns"):
            data[key] = list(data[key])
        return data


def load_cases(path: Path) -> list[Case]:
    if not path.is_file():
        raise DatasetError(f"数据集文件不存在：{path}")
    cases: list[Case] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            cases.append(Case.from_dict(json.loads(line)))
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path}:{lineno} 不是合法 JSON：{exc}") from exc
    if not cases:
        raise DatasetError(f"{path} 里没有任何条目")
    return cases


def save_cases(path: Path, cases: list[Case]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(c.to_dict(), ensure_ascii=False) for c in cases]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_cases(cases: list[Case], corpus_dir: Path) -> list[str]:
    """把条目跟语料交叉校验，返回问题列表（空列表表示全部通过）。

    这一步很关键：条文号写错、或答案要点其实不在该条文里，
    都会让后续评分变成噪音，而且很难在报告里被发现。
    """
    problems: list[str] = []
    articles = load_corpus_articles(corpus_dir)

    seen_ids: set[str] = set()
    categories = {c.value for c in CaseCategory}
    difficulties = {d.value for d in Difficulty}

    for case in cases:
        where = f"[{case.id}]"
        if case.id in seen_ids:
            problems.append(f"{where} id 重复")
        seen_ids.add(case.id)

        if any(not turn.strip() for turn in case.prior_turns):
            problems.append(f"{where} prior_turns 里存在空的问题")
        if case.should_refuse and case.prior_turns:
            problems.append(f"{where} 应拒答条目不支持多轮（prior_turns 必须为空）")

        if case.category not in categories:
            problems.append(f"{where} 未知 category：{case.category}")
        if case.difficulty not in difficulties:
            problems.append(f"{where} 未知 difficulty：{case.difficulty}")

        if case.should_refuse:
            if case.expected_sources:
                problems.append(f"{where} 标为应拒答，却指定了 expected_sources")
            continue

        if not case.expected_sources:
            problems.append(f"{where} 非拒答条目必须给出 expected_sources")
        parsed: list[tuple[str, str]] = []
        for source in case.expected_sources:
            try:
                law, article = parse_source(source)
            except SourceRefError as exc:
                problems.append(f"{where} {exc}")
                continue
            if not find_article(articles, law, article):
                problems.append(f"{where} 条文号「{source}」在语料中不存在")
                continue
            parsed.append((law, article))

        if not case.expected_points:
            problems.append(f"{where} 非拒答条目必须给出 expected_points")

        # 每个答案要点至少要能在**某一条**所引条文的原文里找到。
        # 跨条文条目的要点天然分散在不同条里，不能要求每条都包含全部要点。
        bodies = [find_article(articles, law, article) for law, article in parsed]
        for point in case.expected_points:
            if point and not any(point in body for body in bodies):
                preview = " / ".join(b[:50] for b in bodies)
                problems.append(
                    f"{where} 要点「{point}」未出现在所引条文原文中（原文前 50 字：{preview}…）"
                )
    return problems
