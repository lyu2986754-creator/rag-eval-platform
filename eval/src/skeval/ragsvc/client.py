"""被评测对象 sk-knowledge 的 HTTP 客户端。

按 project.md R1：评测平台只通过 HTTP 观测 RAG 服务，不侵入其业务逻辑。

调用链是两步式的：
    1. POST /api/chat/completions                    取 sessionId（需要 JWT）
    2. GET  /api/chat/stream/{sessionId}/{knowledgeId}  收 SSE（免鉴权）

问题内容在第一步被写进服务端 Redis，第二步从 Redis 取出后真正执行。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from ..config import RagConfig
from ..constants import RecordStatus, SseEvent
from .models import ContextInfo, Diagnostics, RagAnswer
from .sse import SseFrame, iter_sse_from_response

# 值得重试的 HTTP 状态：限流与服务器端临时故障。
# 4xx 不在此列——重试一个 401 只是把失败延后，没有任何成功可能。
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class RagServiceError(RuntimeError):
    """RAG 服务返回非成功响应，或协议与预期不符。"""


class RetryableError(RagServiceError):
    """临时性故障，值得重试（超时、连接失败、5xx、限流、未收到 done 帧）。"""


class RagServiceClient:
    def __init__(self, config: RagConfig) -> None:
        self._config = config
        self._token: str | None = None
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(config.timeout_s),
        )

    async def __aenter__(self) -> RagServiceClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------- 鉴权 ----------

    async def login(self) -> str:
        """表单登录并缓存 JWT。

        token 同时被服务端写入 Redis，校验时会比对，因此无法自行签发绕过。

        登录是整轮评测的单点：它失败意味着后面 50 条一条都跑不了，
        所以这里也做重试，避免一次瞬时 5xx 白费一整轮。
        """
        attempt = 0
        while True:
            try:
                response = await self._http.post(
                    "/api/login",
                    data={"username": self._config.username, "password": self._config.password},
                )
                payload = _expect_success(response, "登录")
                break
            except (httpx.TransportError, RetryableError) as exc:
                if attempt >= self._config.max_retries:
                    raise RagServiceError(
                        f"登录重试 {self._config.max_retries} 次后仍失败：{exc}"
                    ) from exc
                delay = self._config.retry_backoff_s * (2**attempt)
                print(f"[retry] 登录失败（{exc}），{delay:.0f}s 后第 {attempt + 1} 次重试")
                await asyncio.sleep(delay)
                attempt += 1
        token = payload.get("data")
        if not isinstance(token, str) or not token:
            raise RagServiceError(f"登录响应里没有 token：{payload!r}")
        self._token = token
        return token

    @property
    def auth_headers(self) -> dict[str, str]:
        if self._token is None:
            raise RagServiceError("尚未登录，请先调用 login()")
        return {"Authorization": self._token}

    # ---------- 两步式问答 ----------

    async def create_session(self, question: str, *, window_id: str | None = None) -> str:
        body: dict[str, Any] = {
            "content": question,
            "modelId": self._config.model_id,
            "knowledgeId": self._config.knowledge_id,
        }
        if window_id:
            body["windowId"] = window_id
        response = await self._http.post(
            "/api/chat/completions", json=body, headers=self.auth_headers
        )
        payload = _expect_success(response, "创建会话")
        session_id = payload.get("data")
        if not isinstance(session_id, str) or not session_id:
            raise RagServiceError(f"创建会话响应里没有 sessionId：{payload!r}")
        return session_id

    async def iter_stream(self, session_id: str) -> AsyncIterator[SseFrame]:
        url = f"/api/chat/stream/{session_id}/{self._config.knowledge_id}"
        async with self._http.stream("GET", url) as response:
            if response.status_code != 200:
                body = await response.aread()
                message = f"SSE 连接失败 HTTP {response.status_code}: {body[:300]!r}"
                if response.status_code in RETRYABLE_STATUS:
                    raise RetryableError(message)
                raise RagServiceError(message)
            async for frame in iter_sse_from_response(response):
                yield frame

    async def ask(self, question: str, *, window_id: str | None = None) -> RagAnswer:
        """跑完整的一轮问答，带可恢复错误重试。

        为什么评测平台自己也要重试：
            一轮评测有 50 条。任何一次网络抖动如果被记成"这条没答对"，
            就等于把平台的问题算到了被测系统头上，整份报告都会失真。
            所以这里只重试**与被测系统能力无关**的失败
            （超时 / 连接失败 / 5xx / 限流 / 未收到 done 帧），4xx 与业务错误不重试。
        """
        attempt = 0
        while True:
            answer, retryable = await self._ask_once(question, window_id=window_id)
            if not retryable or attempt >= self._config.max_retries:
                return answer
            delay = self._config.retry_backoff_s * (2**attempt)
            print(f"[retry] {answer.error} —— {delay:.0f}s 后第 {attempt + 1} 次重试")
            await asyncio.sleep(delay)
            attempt += 1

    async def _ask_once(
        self, question: str, *, window_id: str | None = None
    ) -> tuple[RagAnswer, bool]:
        """执行一次完整问答，返回 (记录, 是否属于可重试失败)。"""
        started = time.perf_counter()
        status = RecordStatus.OK
        error: str | None = None
        retryable = False
        context: ContextInfo | None = None
        diagnostics: Diagnostics | None = None
        done_payload: dict[str, Any] | None = None
        chunks: list[str] = []

        try:
            session_id = await self.create_session(question, window_id=window_id)
            async for frame in self.iter_stream(session_id):
                if frame.event == SseEvent.CONTEXT.value:
                    context = ContextInfo.from_json(frame.data)
                elif frame.event == SseEvent.CONTENT.value:
                    chunks.append(frame.data)
                elif frame.event == SseEvent.DIAGNOSTICS.value:
                    diagnostics = Diagnostics.from_json(frame.data)
                elif frame.event == SseEvent.DONE.value:
                    done_payload = _try_json(frame.data)

            if done_payload is None:
                # 没有 done 帧 = 流在回答结束前被掐断（例如服务端抛异常后直接关连接）。
                # **必须当失败处理**：否则一段被截断的答案会被正常评分，
                # points_hit / refusal 全按不完整文本算，而且报告里完全看不出来。
                status = RecordStatus.RAG_ERROR
                error = "未收到 done 帧，连接在回答完成前被关闭（答案可能不完整）"
                retryable = True
        except httpx.TimeoutException as exc:
            # 超时单独记一类：它与"服务返回了错误"的排查方向不同
            status = RecordStatus.TIMEOUT
            error = f"{type(exc).__name__}: {exc}"
            retryable = True
        except (httpx.TransportError, RetryableError) as exc:
            # 网络层故障与上游 5xx/限流：与被测系统的回答能力无关，可重试
            status = RecordStatus.RAG_ERROR
            error = f"{type(exc).__name__}: {exc}"
            retryable = True
        except Exception as exc:  # noqa: BLE001 - 任何异常都要落到记录里，不允许静默吞掉
            status = RecordStatus.RAG_ERROR
            error = f"{type(exc).__name__}: {exc}"

        latency_ms = int((time.perf_counter() - started) * 1000)
        answer = (done_payload or {}).get("content")
        if not isinstance(answer, str):
            answer = "".join(chunks)

        return (
            RagAnswer(
                question=question,
                answer=answer,
                chat_window_id=(done_payload or {}).get("windowId"),
                latency_ms=latency_ms,
                status=status,
                context=context,
                diagnostics=diagnostics,
                error=error,
                raw_done=done_payload,
            ),
            retryable,
        )

    # ---------- 语料灌入 ----------

    async def upload_document(self, path: Path, *, knowledge_id: int) -> None:
        """上传一个文档到知识库。

        注意：这个接口是**同步阻塞**的。workflowStub.processKnowledgeDoc(...) 调用的是
        @WorkflowMethod，Temporal Java SDK 的 stub 会一直等到工作流跑完才返回
        （实测确认：KnowledgeDocWorkflow.processKnowledgeDoc 返回 String）。
        所以超时必须按"整条解析+分块+向量化链路"的耗时来设，不能按普通上传接口设。
        """
        with path.open("rb") as handle:
            response = await self._http.post(
                "/api/knowledge/doc",
                params={"knowledgeId": knowledge_id},
                files={"file": (path.name, handle, "text/plain")},
                headers=self.auth_headers,
                timeout=httpx.Timeout(1800.0),
            )
        _expect_success(response, f"上传 {path.name}")

    async def list_documents(self, *, knowledge_id: int) -> list[dict[str, Any]]:
        """列出知识库下的文档及其向量化状态。"""
        response = await self._http.get(
            "/api/knowledge/doc",
            params={"pageNum": 1, "pageSize": 200, "knowledgeId": knowledge_id},
            headers=self.auth_headers,
        )
        payload = _expect_success(response, "查询文档列表")
        data = payload.get("data") or {}
        items = data.get("list") if isinstance(data, dict) else data
        return list(items or [])


def _expect_success(response: httpx.Response, action: str) -> dict[str, Any]:
    if response.status_code in RETRYABLE_STATUS:
        raise RetryableError(f"{action}失败 HTTP {response.status_code}（临时故障，可重试）")
    if response.status_code != 200:
        raise RagServiceError(f"{action}失败 HTTP {response.status_code}: {response.text[:300]}")
    payload = _try_json(response.text)
    if payload is None:
        raise RagServiceError(f"{action}返回的不是 JSON：{response.text[:300]!r}")
    if payload.get("code") != 200:
        raise RagServiceError(f"{action}失败 code={payload.get('code')} msg={payload.get('msg')!r}")
    return payload


def _try_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None
