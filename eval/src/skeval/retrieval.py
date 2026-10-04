"""独立检索探针：评测平台自己去向量库查"这条问题本该召回什么"。

为什么需要它（也是这个平台相对通用评测框架的核心差异）：
    只看最终答案，无法区分「模型不懂法」和「检索漏了那一条」。
    这两者的修法完全不同——前者改 prompt 或换模型，后者改分块或换检索策略。

探针结果的可靠性：
    与被评测服务共用同一个 Weaviate 实例、同一份向量、同一个嵌入模型
    以及同样的 topK 与 knowledgeId 过滤，因此检索结果应当一致。
    唯一的假设是 RAG 用**问题原文**做查询向量（已核对 QuestionAnswerAdvisor
    取的是 userMessage 的文本），这一点在 project.md 里有记录。
"""

from __future__ import annotations

import httpx

from .ragsvc.embedding import EmbeddingClient
from .ragsvc.models import RetrievedChunk


class RetrievalProbe:
    def __init__(
        self,
        *,
        weaviate_url: str,
        class_name: str,
        embedding: EmbeddingClient,
        top_k: int = 10,
        knowledge_id: int | None = None,
    ) -> None:
        self._weaviate_url = weaviate_url.rstrip("/")
        self._class_name = class_name
        self._embedding = embedding
        self._top_k = top_k
        self._knowledge_id = knowledge_id
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(60.0))

    async def __aenter__(self) -> RetrievalProbe:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._http.aclose()

    async def search(self, question: str) -> list[RetrievedChunk]:
        vectors = await self._embedding.embed([question])
        return await self.search_by_vector(vectors[0])

    async def search_by_vector(self, vector: list[float]) -> list[RetrievedChunk]:
        where = ""
        if self._knowledge_id is not None:
            where = (
                ', where: {path: ["meta_knowledgeId"], operator: Equal, '
                f'valueText: "{self._knowledge_id}"}}'
            )
        vector_literal = "[" + ",".join(f"{v:.8f}" for v in vector) + "]"
        query = (
            "{ Get { %s(nearVector: {vector: %s}%s) { content meta_source "
            "_additional { distance } } } }" % (self._class_name, vector_literal, where)
        )
        response = await self._http.post(f"{self._weaviate_url}/v1/graphql", json={"query": query})
        if response.status_code != 200:
            raise RuntimeError(f"Weaviate 查询失败 HTTP {response.status_code}: {response.text[:200]}")
        body = response.json()
        if body.get("errors"):
            raise RuntimeError(f"Weaviate 返回错误：{body['errors']}")

        items = (body.get("data", {}).get("Get", {}) or {}).get(self._class_name, []) or []
        return [
            RetrievedChunk(
                rank=index + 1,
                content=str(item.get("content") or ""),
                source=str(item.get("meta_source") or ""),
                distance=(item.get("_additional") or {}).get("distance"),
            )
            for index, item in enumerate(items[: self._top_k])
        ]
