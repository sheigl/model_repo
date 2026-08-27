"""Background job queue for sync + HF download jobs.

Jobs run on a single worker thread (rsync is not cheap to parallelize against the
same target, and this keeps progress output ordered). Each job records its state,
progress percentage, and streamed log lines so the UI can poll /api/jobs/<id> or
subscribe via SSE for live updates.

Job kinds:
  - "sync": push a source path to a target's remote deployment path.
  - "hf_download": run an HF GGUF download into the repo.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    kind: str
    description: str
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0
    log: list[str] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class JobQueue:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

    # ---- registration -----------------------------------------------------

    def create(self, kind: str, description: str, **meta) -> Job:
        job_id = f"{kind}-{int(time.time()*1000)}"
        job = Job(id=job_id, kind=kind, description=description, **meta)
        with self._lock:
            self._jobs[job_id] = job
            self._order.append(job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        with self._lock:
            return [self._jobs[i] for i in self._order if i in self._jobs]

    # ---- worker -----------------------------------------------------------

    def start_worker(self, runner):
        """Start the background worker. *runner* is a callable(job) -> None."""
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run_loop, args=(runner,), daemon=True)
        self._worker.start()

    def _emit(self, job: Job, line: str):
        job.log.append(line)

    def _run_loop(self, runner):
        while not self._stop.is_set():
            with self._lock:
                next_id = next((i for i in self._order if self._jobs[i].status == JobStatus.QUEUED), None)
            if next_id is None:
                time.sleep(0.2)
                continue
            job = self._jobs[next_id]
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            try:
                runner(job, lambda line: self._emit(job, line))
                if job.status == JobStatus.RUNNING:
                    job.status = JobStatus.DONE
                    job.progress = 100.0
            except Exception as e:
                job.status = JobStatus.FAILED
                job.result = {"error": str(e)}
            job.finished_at = time.time()

    def stop(self):
        self._stop.set()


# Global queue instance used by the app.
queue = JobQueue()
