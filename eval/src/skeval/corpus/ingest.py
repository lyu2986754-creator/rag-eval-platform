"""把抓下来的语料灌入被评测的知识库。

链路：POST /api/knowledge/doc → MinIO → Temporal 工作流 → Tika 解析
      → 语义分块 → 向量化 → Weaviate

两个实测得到的、与直觉相反的结论（写在这里以免重踩）：

1. **上传接口是同步阻塞的。**
   `KnowledgeDocWorkflow.processKnowledgeDoc` 是 `@WorkflowMethod` 且返回 String，
   Temporal Java SDK 的 stub 调用会一直等整个工作流跑完才返回。
   所以「上传返回」就等于「处理完成」，不需要轮询。

2. **不能用 `knowledge_doc.vector_status` 判断是否完成。**
   实测该字段恒为 1（未开始）：工作流先跑 `storeTextToDB`（真正的向量化），
   再跑 `saveToDB`，而 `saveToDB` 只做 `insert`，从不设置 `vector_status`。
   可靠信号是直接问 Weaviate 的对象数。

   另外 `knowledge_fragment` 表在这条链路上也从不写入，分块文本只存在于 Weaviate。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from ..config import ConfigError, RagConfig, load_dotenv, load_rag_config
from ..ragsvc.client import RagServiceClient

VECTOR_CLASS = "Knowledge"


async def vector_object_count(weaviate_url: str, class_name: str = VECTOR_CLASS) -> int:
    """直接问 Weaviate 有多少个向量对象。"""
    payload = {"query": "{ Aggregate { %s { meta { count } } } }" % class_name}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(f"{weaviate_url}/v1/graphql", json=payload)
        response.raise_for_status()
        body = response.json()
    try:
        return int(body["data"]["Aggregate"][class_name][0]["meta"]["count"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Weaviate 返回了意料之外的结构：{body}") from exc


def _doc_name(doc: dict) -> str:
    return str(doc.get("docName") or doc.get("doc_name") or "?")


async def ingest(raw_dir: Path, config: RagConfig) -> int:
    files = sorted(raw_dir.glob("*.txt"))
    if not files:
        print(f"在 {raw_dir} 下没有找到任何 .txt 语料", file=sys.stderr)
        return 2

    before = await vector_object_count(config.weaviate_url)
    print(f"灌库前，向量库对象数：{before}\n")

    async with RagServiceClient(config) as client:
        await client.login()
        existing = {
            _doc_name(d) for d in await client.list_documents(knowledge_id=config.knowledge_id)
        }

        uploaded = 0
        for path in files:
            if path.name in existing:
                print(f"跳过（已存在）: {path.name}")
                continue
            print(f"上传并等待处理: {path.name}  ({path.stat().st_size} 字节) ...", flush=True)
            await client.upload_document(path, knowledge_id=config.knowledge_id)
            uploaded += 1
            print("  完成", flush=True)

    after = await vector_object_count(config.weaviate_url)

    print("\n=== 结果 ===")
    print(f"  新上传文件: {uploaded}")
    print(f"  向量库对象数: {before} -> {after}   （新增 {after - before}）")

    if uploaded and after <= before:
        print("  警告：上传成功但向量库对象数没有增长，请检查后端日志。", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skeval.corpus.ingest", description="把语料灌入知识库")
    parser.add_argument("--raw-dir", default="corpus/raw", help="语料目录（默认 corpus/raw）")
    parser.add_argument("--env-file", default=".env", help="环境变量文件（默认 .env）")
    args = parser.parse_args(argv)

    load_dotenv(Path(args.env_file))
    try:
        return asyncio.run(ingest(Path(args.raw_dir), load_rag_config()))
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"灌库失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

