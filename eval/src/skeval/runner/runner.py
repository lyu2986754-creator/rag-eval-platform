"""跑批：对数据集逐条执行、采集、评分、落盘。

记录字段对齐 project.md R4。其中 input/output tokens 与成本目前拿不到——
被评测服务 SSE 的 done 事件里不含 usage，需要在 Java 侧加诊断埋点才能补上
（Phase 2）。这里显式置为 None 而不是 0，避免把"未知"误当成"零消耗"。
"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from ..config import RagConfig
from ..constants import RecordStatus
from ..dataset import Case
from ..ragsvc.client import RagAnswer, RagServiceClient
from ..ragsvc.embedding import EmbeddingClient
from ..ragsvc.models import RetrievedChunk
from ..retrieval import RetrievalProbe
from ..scorers import SCORER_NAMES, Score, build_source_law_map, score_all


@dataclass(slots=True)
class CaseRecord:
    run_id: str
    case_id: str
    category: str
    difficulty: str
    question: str
    answer: str
    latency_ms: int
    status: str
    error: str | None
    # 召回片段的来源：diagnostics（被评测服务实测推送）或 probe（评测平台独立探测）
    retrieval_source: str = "none"
    # 多轮用例：这一轮之前问过的问题（按顺序）。单轮条目为空列表。
    prior_turns: list[str] = field(default_factory=list)
    retrieved_sources: list[str] = field(default_factory=list)
    retrieved_chunks: list[dict] = field(default_factory=list)
    scores: list[dict] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_amount: float | None = None
    cost_currency: str = "CNY"

    def score(self, name: str) -> dict | None:
        return next((s for s in self.scores if s["name"] == name), None)


@dataclass(slots=True)
class RunResult:
    run_id: str
    records: list[CaseRecord]
    config_snapshot: dict
    started_at: str
    finished_at: str


def _snapshot(config: RagConfig, probe_top_k: int, dataset_name: str) -> dict:
    """冻结本次运行的配置。project.md R3：缺任一项则分数不可比。"""
    return {
        "dataset": dataset_name,
        "rag_base_url": config.base_url,
        "knowledge_id": config.knowledge_id,
        "chat_model_id": config.model_id,
        "embedding_model": config.embedding_model,
        "weaviate_url": config.weaviate_url,
        "probe_top_k": probe_top_k,
        # 评分器清单也要冻结：换了评分器，两次 run 的分就不再可比（project.md R3）
        "scorers": list(SCORER_NAMES),
        "price_input_per_mtoken": config.price_input_per_mtoken,
        "price_output_per_mtoken": config.price_output_per_mtoken,
    }


def _estimate_cost(
    prompt_tokens: int | None, completion_tokens: int | None, config: RagConfig
) -> float | None:
    """按单价把 token 换算成金额；单价未配置时返回 None。

    返回 None 而不是 0：把"未配置"和"零消耗"混为一谈会让成本报表失去意义。
    """
    if config.price_input_per_mtoken is None or config.price_output_per_mtoken is None:
        return None
    if prompt_tokens is None and completion_tokens is None:
        return None
    return round(
        (prompt_tokens or 0) / 1_000_000 * config.price_input_per_mtoken
        + (completion_tokens or 0) / 1_000_000 * config.price_output_per_mtoken,
        6,
    )


async def _ask_case(client: RagServiceClient, case: Case) -> RagAnswer:
    """按顺序问完一个 case 的所有轮次，返回**最后一轮**的记录。

    多轮的实现要点：**必须复用同一个 windowId**。否则被测系统看不到上文，
    追问里的指代（"那它有什么例外"）根本无从解析，这个 case 也就失去了意义——
    它考的正是"能不能记住上文"。

    耗时与 token 取**所有轮次之和**：多轮的真实成本本来就包含前面几轮，
    只算最后一轮会低估它。

    召回片段也**跨轮合并**：一串追问里，期望条文可能是在**前面某一轮**被召回的。
    只看最后一轮会得出"没召回到"的错误结论——实测踩过（mt-004：第二轮问
    "如果这些信息泄露了"，单看这句话根本不知道该查第四十二条）。
    合并后 retrieval_hit 的语义是"**整段对话**里有没有摸到这条"。
    """
    window_id: str | None = None
    answer: RagAnswer | None = None
    total_ms = 0
    prompt_tokens = 0
    completion_tokens = 0
    diagnostics = None
    merged: list[RetrievedChunk] = []
    seen_contents: set[str] = set()

    for turn in (*case.prior_turns, case.question):
        answer = await client.ask(turn, window_id=window_id)
        total_ms += answer.latency_ms
        if answer.diagnostics is not None:
            diagnostics = answer.diagnostics
            prompt_tokens += diagnostics.prompt_tokens or 0
            completion_tokens += diagnostics.completion_tokens or 0
            for chunk in diagnostics.chunks:
                if chunk.content in seen_contents:
                    continue
                seen_contents.add(chunk.content)
                merged.append(chunk)
        if answer.chat_window_id:
            window_id = answer.chat_window_id
        if answer.status is not RecordStatus.OK:
            break  # 前面这轮就失败了，后面的追问不必再问

    assert answer is not None  # prior_turns 可能为空，但 question 永远至少有一条
    if not case.prior_turns:
        return answer

    if diagnostics is not None:
        diagnostics = replace(
            diagnostics,
            chunks=tuple(
                replace(chunk, rank=index) for index, chunk in enumerate(merged, start=1)
            ),
            prompt_tokens=prompt_tokens or None,
            completion_tokens=completion_tokens or None,
        )
    return replace(answer, latency_ms=total_ms, diagnostics=diagnostics)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _semantic_similarity(
    embedding: EmbeddingClient, case: Case, answer: str
) -> float | None:
    """算答案与参考答案的余弦相似度；缺任一侧或嵌入失败时返回 None（不适用）。

    嵌入算不出来不该让整条 case 失败——它是**附加信号**，
    缺了它还有要点命中与拒答判定，所以这里降级而不是抛出。
    """
    if case.should_refuse or not case.reference_answer or not answer.strip():
        return None
    try:
        vectors = await embedding.embed([case.reference_answer, answer])
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] 语义相似度计算失败（不影响其他评分）：{type(exc).__name__}: {exc}")
        return None
    return _cosine(vectors[0], vectors[1])


async def run_dataset(
    cases: list[Case],
    config: RagConfig,
    *,
    dataset_name: str = "",
    probe_top_k: int = 10,
    progress: bool = True,
    on_case_done: Callable[[int, int, str], None] | None = None,
) -> RunResult:
    """对数据集跑一轮评测。

    `on_case_done(index, total, case_id)` 在**每条跑完时**回调一次，
    供 API 层更新任务进度——否则前端进度条只能从 0% 直接跳到 100%。
    """
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    started_at = datetime.now(timezone.utc).isoformat()
    records: list[CaseRecord] = []

    embedding = EmbeddingClient(
        base_url=config.embedding_base_url,
        api_key=config.embedding_api_key,
        model=config.embedding_model,
    )
    try:
        async with RagServiceClient(config) as client, RetrievalProbe(
            weaviate_url=config.weaviate_url,
            class_name="Knowledge",
            embedding=embedding,
            top_k=probe_top_k,
            knowledge_id=config.knowledge_id,
        ) as probe:
            await client.login()

            # MinIO 会把文件重命名成 MD5，召回片段的 meta_source 里没有原始文件名。
            # 必须先从 knowledge_doc 建好 url -> 法律代号 的映射，过程评分才有依据。
            documents = await client.list_documents(knowledge_id=config.knowledge_id)
            source_law_map = build_source_law_map(documents)
            if progress:
                print(f"已载入 {len(source_law_map)} 份文档的来源映射：{source_law_map}")

            for index, case in enumerate(cases, start=1):
                if progress:
                    print(f"[{index}/{len(cases)}] {case.id} …", end="", flush=True)

                answer = await _ask_case(client, case)

                # 首选被评测服务自己推来的真实召回片段（diagnostics 事件）。
                # 探针只在需要交叉验证时作为兜底。
                chunks = list(answer.retrieved_chunks)
                retrieval_source = "diagnostics"
                probe_error: str | None = None
                if not chunks:
                    if case.prior_turns:
                        # 探针只会"照着问题原文"去检索，拿不到多轮才有的上文。
                        # 用它兜底多轮 case 必然得出错误结论，所以直接跳过。
                        retrieval_source = "none"
                    else:
                        retrieval_source = "probe"
                        try:
                            chunks = await probe.search(case.question)
                        except Exception as exc:  # noqa: BLE001 - 探针失败不应中断整轮评测
                            probe_error = f"{type(exc).__name__}: {exc}"

                semantic = await _semantic_similarity(embedding, case, answer.answer)
                scores: list[Score] = score_all(
                    case, answer.answer, chunks, source_law_map, semantic_similarity=semantic
                )
                record = CaseRecord(
                    run_id=run_id,
                    case_id=case.id,
                    category=case.category,
                    difficulty=case.difficulty,
                    question=case.question,
                    answer=answer.answer,
                    latency_ms=answer.latency_ms,
                    status=answer.status.value,
                    error=answer.error or probe_error,
                    retrieval_source=retrieval_source,
                    prior_turns=list(case.prior_turns),
                    retrieved_sources=[c.source for c in chunks],
                    # 片段正文也存下来：Trace 页面要展示"到底召回了什么"，
                    # 这是过程层归因唯一能被人眼复核的证据。
                    retrieved_chunks=[
                        {"rank": c.rank, "source": c.source, "content": c.content}
                        for c in chunks
                    ],
                    scores=[asdict(s) for s in scores],
                    input_tokens=(
                        answer.diagnostics.prompt_tokens if answer.diagnostics else None
                    ),
                    output_tokens=(
                        answer.diagnostics.completion_tokens if answer.diagnostics else None
                    ),
                    cost_amount=_estimate_cost(
                        answer.diagnostics.prompt_tokens if answer.diagnostics else None,
                        answer.diagnostics.completion_tokens if answer.diagnostics else None,
                        config,
                    ),
                )
                records.append(record)
                if on_case_done is not None:
                    on_case_done(index, len(cases), case.id)

                if progress:
                    retrieval = record.score("retrieval_hit") or {}
                    points = record.score("points_hit") or {}
                    flag = "✓" if retrieval.get("passed") else "✗"
                    print(
                        f" 检索{flag} 要点{points.get('value', 0):.0%} {answer.latency_ms}ms",
                        flush=True,
                    )

                # 轻量节流，避免把被评测服务的连接池打满
                await asyncio.sleep(0.2)
    finally:
        await embedding.close()

    return RunResult(
        run_id=run_id,
        records=records,
        config_snapshot=_snapshot(config, probe_top_k, dataset_name),
        started_at=started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


def save_run(result: RunResult, out_dir: Path) -> Path:
    """把一轮评测落盘：逐条记录一个 JSONL，配置快照一个 meta.json。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result.run_id}.jsonl"
    lines = [json.dumps(asdict(r), ensure_ascii=False) for r in result.records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    (out_dir / f"{result.run_id}.meta.json").write_text(
        json.dumps(
            {
                "run_id": result.run_id,
                "started_at": result.started_at,
                "finished_at": result.finished_at,
                "case_count": len(result.records),
                "config": result.config_snapshot,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
