"""SSE 解析器单测。刻意用假的行序列，不碰网络。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from skeval.ragsvc.sse import SseFrame, iter_sse


async def _lines(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


async def test_parses_event_and_data() -> None:
    frames = [f async for f in iter_sse(_lines("event:content", "data:你好", ""))]
    assert frames == [SseFrame(event="content", data="你好")]


async def test_tolerates_space_after_colon() -> None:
    frames = [f async for f in iter_sse(_lines("event: done", "data: {}", ""))]
    assert frames == [SseFrame(event="done", data="{}")]


async def test_ignores_comment_lines() -> None:
    frames = [f async for f in iter_sse(_lines(": keep-alive", "event:content", "data:x", ""))]
    assert frames == [SseFrame(event="content", data="x")]


async def test_multiline_data_is_joined() -> None:
    frames = [f async for f in iter_sse(_lines("data:a", "data:b", ""))]
    assert frames == [SseFrame(event="message", data="a\nb")]


async def test_flushes_trailing_frame_without_blank_line() -> None:
    frames = [f async for f in iter_sse(_lines("event:done", "data:last"))]
    assert frames == [SseFrame(event="done", data="last")]


async def test_empty_input_yields_nothing() -> None:
    assert [f async for f in iter_sse(_lines())] == []


@pytest.mark.parametrize("payload", ["{}", '{"a": 1}', "not-json"])
async def test_never_raises_on_arbitrary_data(payload: str) -> None:
    frames = [f async for f in iter_sse(_lines("event:content", f"data:{payload}", ""))]
    assert frames[0].data == payload

