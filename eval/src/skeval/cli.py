"""命令行入口。Phase 0 的验收工具。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import ConfigError, load_dotenv, load_rag_config
from .constants import RecordStatus
from .ragsvc.client import RagServiceClient
from .dataset import DatasetError, load_cases, validate_cases


def _force_utf8_output() -> None:
    """Windows 控制台默认使用 GBK，中文输出会乱码。强制走 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skeval", description="RAG 应用评测平台")
    parser.add_argument("--env-file", default=".env", help="环境变量文件路径（默认 .env）")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="向被评测的 RAG 服务提一个问题")
    ask.add_argument("question", help="问题内容")
    ask.add_argument("--dump", help="把本轮记录以 JSON 写入指定文件")

    sub.add_parser("ping", help="检查配置与登录是否可用")

    run = sub.add_parser("run", help="对数据集跑一轮评测")
    run.add_argument("--dataset", default="datasets/law_qa.jsonl", help="数据集路径（JSONL）")
    run.add_argument("--corpus-dir", default="corpus/raw", help="语料目录，用于校验条文号")
    run.add_argument("--out-dir", default="runs", help="结果输出目录")
    run.add_argument("--probe-top-k", type=int, default=10, help="检索探针的 topK")
    run.add_argument("--limit", type=int, help="只跑前 N 条（调试用）")
    run.add_argument("--skip-validate", action="store_true", help="跳过数据集校验")
    return parser


async def _run_ask(question: str, dump: str | None) -> int:
    config = load_rag_config()
    async with RagServiceClient(config) as client:
        await client.login()
        answer = await client.ask(question)

    print(f"问题: {answer.question}")
    print(f"耗时: {answer.latency_ms} ms")
    print(f"状态: {answer.status.value}")
    if answer.error:
        print(f"错误: {answer.error}")
    print(f"答案: {answer.answer}")

    if dump:
        dump_path = Path(dump)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(
            json.dumps(answer.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"记录已写入: {dump_path}")
    return 0 if answer.status is RecordStatus.OK else 1


async def _run_ping() -> int:
    config = load_rag_config()
    print(f"目标服务: {config.base_url}")
    print(f"知识库: {config.knowledge_id}    模型: {config.model_id}")
    async with RagServiceClient(config) as client:
        token = await client.login()
    print(f"登录成功，token 长度 {len(token)}")
    return 0


async def _run_dataset(args: argparse.Namespace) -> int:
    from .report import render
    from .runner import run_dataset, save_run

    dataset_path = Path(args.dataset)
    cases = load_cases(dataset_path)

    if not args.skip_validate:
        problems = validate_cases(cases, Path(args.corpus_dir))
        if problems:
            print(f"数据集校验未通过（{len(problems)} 个问题）：", file=sys.stderr)
            for problem in problems[:20]:
                print(f"  - {problem}", file=sys.stderr)
            return 2
        print(f"数据集校验通过：{len(cases)} 条\n")

    if args.limit:
        cases = cases[: args.limit]
        print(f"仅执行前 {len(cases)} 条\n")

    config = load_rag_config()
    result = await run_dataset(
        cases, config, dataset_name=dataset_path.name, probe_top_k=args.probe_top_k
    )

    out_dir = Path(args.out_dir)
    records_path = save_run(result, out_dir)

    should_refuse = {c.id: c.should_refuse for c in cases}
    markdown = render(result, should_refuse_by_id=should_refuse)
    report_path = out_dir / f"{result.run_id}.md"
    report_path.write_text(markdown, encoding="utf-8")

    print(f"\n记录: {records_path}")
    print(f"报告: {report_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    args = _build_parser().parse_args(argv)
    load_dotenv(Path(args.env_file))
    try:
        if args.command == "ask":
            return asyncio.run(_run_ask(args.question, args.dump))
        if args.command == "ping":
            return asyncio.run(_run_ping())
        if args.command == "run":
            return asyncio.run(_run_dataset(args))
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    except DatasetError as exc:
        print(f"数据集错误: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
