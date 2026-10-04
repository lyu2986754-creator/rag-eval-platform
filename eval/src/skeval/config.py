"""配置加载。所有密钥只从环境变量读取，禁止硬编码（project.md 第 7 节）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .constants import DEFAULT_TIMEOUT_S


class ConfigError(RuntimeError):
    """环境变量缺失或非法。"""


@dataclass(frozen=True, slots=True)
class RagConfig:
    """连接被评测对象 sk-knowledge 所需的配置。"""

    base_url: str
    username: str
    password: str
    knowledge_id: int
    model_id: int
    weaviate_url: str = "http://127.0.0.1:18088"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    # 单价（元 / 百万 token）。刻意不设默认值：宁可留空，也不写一个猜的数字。
    price_input_per_mtoken: float | None = None
    price_output_per_mtoken: float | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    # 可恢复错误（超时 / 连接失败 / 5xx / 限流 / 未收到 done 帧）的重试次数。
    # 评测平台自己必须扛得住抖动：一轮 50 条，任何一次网络失败如果被记成
    # "这条没答对"，就等于把平台的问题算到了被测系统头上，整份报告失真。
    max_retries: int = 2
    # 首次重试前的等待秒数，之后按 2 的幂退避。
    retry_backoff_s: float = 1.0


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"缺少环境变量 {name}，请参考 eval/.env.example")
    return value


def _require_int(name: str) -> int:
    raw = _require(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 必须是整数，当前值：{raw!r}") from exc


def load_rag_config() -> RagConfig:
    def _optional_float(name: str) -> float | None:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError as exc:
            raise ConfigError(f"环境变量 {name} 必须是数字，当前值：{raw!r}") from exc

    return RagConfig(
        base_url=_require("SK_RAG_BASE_URL").rstrip("/"),
        username=_require("SK_RAG_USERNAME"),
        password=_require("SK_RAG_PASSWORD"),
        knowledge_id=_require_int("SK_RAG_KNOWLEDGE_ID"),
        model_id=_require_int("SK_RAG_MODEL_ID"),
        weaviate_url=os.environ.get("SK_WEAVIATE_URL", "http://127.0.0.1:18088").rstrip("/"),
        embedding_base_url=_require("SK_EMBEDDING_BASE_URL").rstrip("/"),
        embedding_api_key=_require("SK_EMBEDDING_API_KEY"),
        embedding_model=os.environ.get("SK_EMBEDDING_MODEL", "BAAI/bge-m3"),
        price_input_per_mtoken=_optional_float("SK_PRICE_INPUT_PER_MTOKEN"),
        price_output_per_mtoken=_optional_float("SK_PRICE_OUTPUT_PER_MTOKEN"),
        timeout_s=float(os.environ.get("SK_RAG_TIMEOUT_S") or DEFAULT_TIMEOUT_S),
        max_retries=int(os.environ.get("SK_RAG_MAX_RETRIES") or "2"),
        retry_backoff_s=float(os.environ.get("SK_RAG_RETRY_BACKOFF") or "1"),
    )


def load_dotenv(path: Path) -> None:
    """极简 .env 加载器。

    只为省掉 python-dotenv 依赖：支持 KEY=VALUE、# 注释、两侧引号。
    已存在的环境变量优先级更高，不会被覆盖。
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
