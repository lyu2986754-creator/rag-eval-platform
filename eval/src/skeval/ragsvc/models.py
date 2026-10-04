"""问答过程中产生的结构化数据。字段对齐 project.md R4。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from ..constants import RecordStatus


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """一个被召回的片段。

    两个来源共用这个结构：
      - 被评测服务的 diagnostics 事件（真实召回，首选）
      - 评测平台的独立检索探针（可选交叉验证）
    """

    rank: int
    content: str
    source: str
    distance: float | None = None


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """被评测服务在 done 之前推送的只读诊断信息。"""

    chunks: tuple[RetrievedChunk, ...] = ()
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    generate_ms: int | None = None
    first_token_ms: int | None = None

    @classmethod
    def from_json(cls, raw: str) -> Diagnostics | None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        chunks = []
        for item in data.get("retrievedChunks") or []:
            if not isinstance(item, dict):
                continue
            chunks.append(
                RetrievedChunk(
                    rank=int(item.get("rank") or len(chunks) + 1),
                    content=str(item.get("content") or ""),
                    source=str(item.get("source") or ""),
                    distance=None,
                )
            )
        return cls(
            chunks=tuple(chunks),
            prompt_tokens=_as_int(data.get("promptTokens")),
            completion_tokens=_as_int(data.get("completionTokens")),
            generate_ms=_as_int(data.get("generateMs")),
            first_token_ms=_as_int(data.get("firstTokenMs")),
        )


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class ContextInfo:
    """SSE 第一帧 chatContextIdVo 的内容。"""

    user_question_id: str
    assistant_id: str
    chat_window_id: str
    chat_window_title: str

    @classmethod
    def from_json(cls, raw: str) -> ContextInfo | None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return cls(
            user_question_id=str(data.get("userQuestionId", "")),
            assistant_id=str(data.get("assistantId", "")),
            chat_window_id=str(data.get("chatWindowId", "")),
            chat_window_title=str(data.get("chatWindowTittle", "")),
        )


@dataclass(frozen=True, slots=True)
class RagAnswer:
    """一轮问答的完整记录素材。"""

    question: str
    answer: str
    chat_window_id: str | None
    latency_ms: int
    status: RecordStatus
    context: ContextInfo | None = None
    diagnostics: Diagnostics | None = None
    error: str | None = None
    raw_done: dict[str, Any] | None = None

    @property
    def retrieved_chunks(self) -> tuple[RetrievedChunk, ...]:
        return self.diagnostics.chunks if self.diagnostics else ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload
