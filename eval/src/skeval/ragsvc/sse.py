"""最小 SSE 解析器。

刻意不引入第三方 SSE 库：project.md R5 要求评测平台核心逻辑自持。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class SseFrame:
    event: str
    data: str


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[SseFrame]:
    """把 SSE 文本行序列解析成事件帧。

    兼容 `event:x` 与 `event: x` 两种写法（Spring SseEmitter 不保证空格）。
    """
    event = "message"
    data_lines: list[str] = []

    async for line in lines:
        if line == "":
            if data_lines:
                yield SseFrame(event=event, data="\n".join(data_lines))
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue  # 注释行/心跳
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)

    if data_lines:  # 流结束时未以空行收尾
        yield SseFrame(event=event, data="\n".join(data_lines))


def iter_sse_from_response(response: httpx.Response) -> AsyncIterator[SseFrame]:
    return iter_sse(response.aiter_lines())

