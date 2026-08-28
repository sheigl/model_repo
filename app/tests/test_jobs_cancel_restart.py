"""Tests for job cancellation, restart, and the cancel/restart API endpoints.

Exercises the background worker's early-stop behaviour with fake runners (no
network), plus the FastAPI /cancel and /restart routes via TestClient.
"""

from __future__ import annotations

import threading
import time
import unittest

from fastapi.testclient import TestClient

import app.jobs as jobs
from app.main import app


class WorkerCancelTests(unittest.TestCase):
    def test_cancelled_queued_job_is_skipped(self):
        q = jobs.JobQueue()
        job = q.create("hf_download", "x")
        self.assertTrue(q.cancel_job(job.id))

        called = []
        runner = lambda j, emit: called.append(j)  # noqa: E731
        q.start_worker(runner)
        try:
            deadline = time.time() + 5
            while q.get(job.id).status == jobs.JobStatus.QUEUED and time.time() < deadline:
                time.sleep(0.02)
        finally:
            q.stop()

        self.assertEqual(q.get(job.id).status, jobs.JobStatus.CANCELLED)
        self.assertEqual(called, [])  # runner never ran a cancelled-while-queued job

    def test_running_job_stops_early_when_cancelled(self):
        q = jobs.JobQueue()
        job = q.create("sync", "x")

        stop_at = {"t": time.time() + 1.0}
        calls = []

        def runner(j, emit):
            while time.time() < stop_at["t"]:
                if q.is_cancelled(job.id):
                    # Cooperative early return, like the real download loops do.
                    calls.append(("aborted", time.time()))
                    return
                time.sleep(0.01)
            calls.append(("finished", time.time()))

        q.start_worker(runner)
        try:
            time.sleep(0.2)
            self.assertTrue(q.cancel_job(job.id))
            deadline = time.time() + 5
            while q.get(job.id).status == jobs.JobStatus.RUNNING and time.time() < deadline:
                time.sleep(0.02)
        finally:
            q.stop()

        self.assertEqual(q.get(job.id).status, jobs.JobStatus.CANCELLED)
        self.assertTrue(calls[0][0] == "aborted")

    def test_terminal_job_cannot_be_re_cancelled(self):
        q = jobs.JobQueue()
        job = q.create("deploy", "x")
        job.status = jobs.JobStatus.DONE
        # Terminal: nothing to cancel, returns False and leaves status untouched.
        self.assertFalse(q.cancel_job(job.id))
        self.assertEqual(q.get(job.id).status, jobs.JobStatus.DONE)


class CancelRestartApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_cancel_endpoint_409_for_terminal(self):
        job = jobs.queue.create("deploy", "x")
        jobs.queue.get(job.id).status = jobs.JobStatus.DONE
        r = self.client.post(f"/api/job/{job.id}/cancel")
        self.assertEqual(r.status_code, 409)

    def test_cancel_endpoint_ok_for_queued(self):
        job = jobs.queue.create("sync", "x")
        r = self.client.post(f"/api/job/{job.id}/cancel")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        # Worker isn't running here, so status flips to cancelled only when picked up;
        # the important contract is the cancel flag is set.
        self.assertTrue(jobs.queue.is_cancelled(job.id))

    def test_restart_endpoint_rejects_running(self):
        job = jobs.queue.create("hf_download", "x")
        jobs.queue.get(job.id).status = jobs.JobStatus.RUNNING
        r = self.client.post(f"/api/job/{job.id}/restart")
        self.assertEqual(r.status_code, 409)

    def test_restart_endpoint_creates_new_job_with_meta(self):
        job = jobs.queue.create("sync", "original")
        jobs.queue.get(job.id).status = jobs.JobStatus.DONE
        jobs.queue.get(job.id).meta = {"host": "1.2.3.4", "user": "ubuntu"}

        r = self.client.post(f"/api/job/{job.id}/restart")
        self.assertEqual(r.status_code, 200)
        new_id = r.json()["job_id"]
        self.assertNotEqual(new_id, job.id)
        new_job = jobs.queue.get(new_id)
        self.assertEqual(new_job.kind, "sync")
        self.assertTrue(new_job.description.startswith("Restarted:"))
        self.assertEqual(new_job.meta, {"host": "1.2.3.4", "user": "ubuntu"})

    def test_restart_unknown_job_404(self):
        r = self.client.post("/api/job/does-not-exist/restart")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
