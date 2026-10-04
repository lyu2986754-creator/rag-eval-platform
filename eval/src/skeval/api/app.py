"""评测平台 HTTP 接口。

启动：
    python -m skeval.api.app
或：
    uvicorn skeval.api.app:app --port 8090

根目录由环境变量 SKEVAL_ROOT 指定（默认当前工作目录），
数据集、运行记录、语料都在该目录下解析。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..config import ConfigError, load_dotenv, load_rag_config
from ..runner import run_dataset, save_run
from ..store import (
    StoreError,
    get_run_meta,
    list_datasets,
    list_runs,
    load_dataset,
    load_report_markdown,
    load_run_records,
    summarize,
    validate_dataset,
)
from .jobs import registry

app = FastAPI(title="RAG 评测平台", version="0.1.0")

# 本地单用户工具，前端跑在另一个端口，直接放开跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _root() -> Path:
    return Path(os.environ.get("SKEVAL_ROOT") or Path.cwd()).resolve()


def _should_refuse_map(dataset_name: str) -> dict[str, bool]:
    """从数据集取「应拒答」标记，失败归因与报告口径都依赖它。"""
    root = _root()
    if dataset_name:
        try:
            _, cases = load_dataset(root, dataset_name)
        except Exception as exc:  # noqa: BLE001 - 读不到就退到兜底，但不能静默
            print(f"[warn] 读取数据集 {dataset_name} 失败，改用合并兜底：{exc}")
        else:
            return {c.id: c.should_refuse for c in cases}
    # 兜底：早期运行没记录 dataset 字段，把所有数据集合并起来查。
    # 少了这一步，拒答类条目会被全部归成"无端拒答"（实测踩过）。
    merged: dict[str, bool] = {}
    for item in list_datasets(root):
        try:
            _, cases = load_dataset(root, item["name"])
        except Exception as exc:  # noqa: BLE001 - 单个坏数据集不该让整个兜底失败
            print(f"[warn] 兜底时跳过数据集 {item['name']}：{exc}")
            continue
        for case in cases:
            merged.setdefault(case.id, case.should_refuse)
    return merged


@app.get("/api/health")
def health() -> dict:
    root = _root()
    return {"status": "ok", "root": str(root), "datasets": len(list_datasets(root)),
            "runs": len(list_runs(root))}


@app.get("/api/datasets")
def api_list_datasets() -> list[dict]:
    return list_datasets(_root())


@app.get("/api/datasets/{name}")
def api_dataset_detail(name: str) -> dict:
    try:
        _, cases = load_dataset(_root(), name)
    except StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "name": name,
        "case_count": len(cases),
        "cases": [c.to_dict() for c in cases],
    }


@app.post("/api/datasets/{name}/validate")
def api_validate_dataset(name: str) -> dict:
    try:
        problems = validate_dataset(_root(), name)
    except StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"name": name, "problems": problems, "ok": not problems}


@app.get("/api/runs")
def api_list_runs() -> list[dict]:
    root = _root()
    out: list[dict] = []
    for meta in list_runs(root):
        try:
            records = load_run_records(root, meta.run_id)
            summary = summarize(records, _should_refuse_map(meta.dataset))
        except StoreError:
            summary = None
        out.append(
            {
                "run_id": meta.run_id,
                "dataset": meta.dataset,
                "case_count": meta.case_count,
                "started_at": meta.started_at,
                "finished_at": meta.finished_at,
                "passed": (summary or {}).get("passed"),
                "total": (summary or {}).get("total"),
                # 有效样本已剔除调用失败（平台侧故障），列表页与报告口径保持一致
                "effective_total": (summary or {}).get("effective_total"),
                "call_errors": (summary or {}).get("call_errors"),
            }
        )
    return out


@app.get("/api/runs/{run_id}")
def api_run_detail(run_id: str) -> dict:
    root = _root()
    meta = get_run_meta(root, run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"运行不存在：{run_id}")
    try:
        records = load_run_records(root, run_id)
    except StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "run_id": meta.run_id,
        "dataset": meta.dataset,
        "config": meta.config,
        "started_at": meta.started_at,
        "finished_at": meta.finished_at,
        "summary": summarize(records, _should_refuse_map(meta.dataset)),
        "records": records,
    }


@app.get("/api/runs/{run_id}/report")
def api_run_report(run_id: str) -> dict:
    markdown = load_report_markdown(_root(), run_id)
    if not markdown:
        raise HTTPException(status_code=404, detail="该运行没有 Markdown 报告")
    return {"run_id": run_id, "markdown": markdown}


@app.get("/api/runs/{run_id}/diff/{other_id}")
def api_run_diff(run_id: str, other_id: str) -> dict:
    root = _root()

    def _index(rid: str) -> dict[str, dict]:
        return {r["case_id"]: r for r in load_run_records(root, rid)}

    try:
        left, right = _index(run_id), _index(other_id)
    except StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # 只算一次。原实现在 _pick 里每条 case 各算两次，而传空串会触发
    # "合并所有数据集"的兜底分支——27 条 case 就是 50+ 次全量扫描。
    refuse_map = _should_refuse_map("")

    def _pick(record: dict | None) -> dict | None:
        if record is None:
            return None
        summary = summarize([record], refuse_map)
        return {
            "answer": record.get("answer", ""),
            "latency_ms": record.get("latency_ms"),
            "status": record.get("status"),
            "retrieval_passed": next(
                (s["passed"] for s in record.get("scores", []) if s["name"] == "retrieval_hit"),
                None,
            ),
            "citation_passed": next(
                (
                    s["passed"]
                    for s in record.get("scores", [])
                    if s["name"] == "citation_support"
                ),
                None,
            ),
            "points_value": next(
                (s["value"] for s in record.get("scores", []) if s["name"] == "points_hit"),
                None,
            ),
            "refusal_passed": next(
                (s["passed"] for s in record.get("scores", []) if s["name"] == "refusal"),
                None,
            ),
            "failure": next(iter(summary["failures"].values()), None),
        }

    rows = []
    for case_id in sorted(set(left) | set(right)):
        a, b = _pick(left.get(case_id)), _pick(right.get(case_id))
        # 比对"结论"而不只是分数：状态从 ok 变 error、引用从有据变无据，都算变化。
        def _fingerprint(side: dict | None) -> tuple:
            if side is None:
                return ("<缺>",)
            return (
                side["status"],
                side["retrieval_passed"],
                side["citation_passed"],
                side["points_value"],
                side["refusal_passed"],
            )

        changed = _fingerprint(a) != _fingerprint(b)
        rows.append({"case_id": case_id, "left": a, "right": b, "changed": changed})
    return {"left": run_id, "right": other_id, "rows": rows}


class StartRunRequest(BaseModel):
    dataset: str
    limit: int | None = None
    probe_top_k: int = 10


@app.post("/api/jobs")
async def api_start_job(req: StartRunRequest) -> dict:
    root = _root()
    try:
        _, cases = load_dataset(root, req.dataset)
    except StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if req.limit:
        cases = cases[: req.limit]

    job = registry.create(req.dataset, total=len(cases))

    async def _execute() -> None:
        job.status = "running"
        try:
            config = load_rag_config()

            def _on_case_done(index: int, total: int, case_id: str) -> None:
                # 真实进度：每跑完一条回调一次。没有这个回调，
                # 前端的进度条只会从 0% 直接跳到 100%（文档里写着"实时进度"）。
                job.finished = index
                job.current_case = case_id

            result = await run_dataset(
                cases,
                config,
                dataset_name=req.dataset,
                probe_top_k=req.probe_top_k,
                progress=False,
                on_case_done=_on_case_done,
            )
            save_run(result, root / "runs")

            from ..report import render

            markdown = render(
                result, should_refuse_by_id={c.id: c.should_refuse for c in cases}
            )
            (root / "runs" / f"{result.run_id}.md").write_text(markdown, encoding="utf-8")

            job.run_id = result.run_id
            job.finished = len(cases)
            job.status = "done"
            job.log.append(f"完成，run_id={result.run_id}")
        except (ConfigError, Exception) as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.log.append(job.error)

    asyncio.create_task(_execute())
    return job.to_dict()


@app.get("/api/jobs")
def api_list_jobs() -> list[dict]:
    return registry.list()


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")
    return job.to_dict()


def main() -> int:
    import uvicorn

    root = _root()
    load_dotenv(root / ".env")
    os.chdir(root)
    print(f"评测平台 API 启动，根目录 {root}")
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("SKEVAL_PORT", "8090")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
