"""后台评测任务的进度跟踪。

评测一轮要一到两分钟，接口不能阻塞等待，所以用内存里的任务表 +
后台 asyncio 任务。进程重启会丢任务，这对本地单用户工具是可以接受的取舍。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Job:
    job_id: str
    dataset: str
    status: str = "pending"  # pending / running / done / error
    total: int = 0
    # finished / current_case 由 run_dataset 的 on_case_done 回调驱动（见 api/app.py）。
    # 它们必须真的会变——一个永远停在 0 的进度字段比没有更糟，因为它制造"有进度"的错觉。
    finished: int = 0
    current_case: str = ""
    run_id: str = ""
    error: str = ""
    log: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "dataset": self.dataset,
            "status": self.status,
            "total": self.total,
            "finished": self.finished,
            "current_case": self.current_case,
            "run_id": self.run_id,
            "error": self.error,
            "log": self.log[-40:],
            "started_at": self.started_at,
        }


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, dataset: str, total: int) -> Job:
        job = Job(job_id=uuid.uuid4().hex[:12], dataset=dataset, total=total)
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[dict]:
        return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda x: x.started_at, reverse=True)]


registry = JobRegistry()
