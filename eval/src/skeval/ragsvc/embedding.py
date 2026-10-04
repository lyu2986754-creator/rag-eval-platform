"""直接调用嵌入模型，供检索探针使用。

评测平台需要独立把问题向量化，才能自己去向量库里查"这条问题本该召回什么"。
必须与被评测服务用同一个嵌入模型，否则两边向量不在同一空间，探针结果无意义。
"""

from __future__ import annotations

import httpx


class EmbeddingError(RuntimeError):
    """嵌入调用失败。"""


class EmbeddingClient:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_s: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout_s))

    async def __aenter__(self) -> EmbeddingClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._http.post(
            f"{self._base_url}/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "input": texts},
        )
        if response.status_code != 200:
            raise EmbeddingError(f"嵌入失败 HTTP {response.status_code}: {response.text[:200]}")
        body = response.json()
        vectors = [item.get("embedding") for item in body.get("data", [])]
        if len(vectors) != len(texts) or any(v is None for v in vectors):
            raise EmbeddingError(f"嵌入返回条数与请求不符：期望 {len(texts)}，实际 {len(vectors)}")
        return vectors

