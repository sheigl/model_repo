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


class ClearFinishedTests(unittest.TestCase):
    def test_clear_finished_removes_only_terminal(self):
        # Distinct kinds so the millisecond-based ids can't collide.
        q = jobs.JobQueue()
        done = q.create("sync", "x")
        done.status = jobs.JobStatus.DONE
        failed = q.create("deploy", "x")
        failed.status = jobs.JobStatus.FAILED
        cancelled = q.create("hf_download", "x")
        cancelled.status = jobs.JobStatus.CANCELLED
        queued = q.create("k4", "x")
        running = q.create("k5", "x")
        running.status = jobs.JobStatus.RUNNING

        self.assertEqual(q.clear_finished(), 3)
        self.assertIsNone(q.get(done.id))
        self.assertIsNone(q.get(failed.id))
        self.assertIsNone(q.get(cancelled.id))
        self.assertIsNotNone(q.get(queued.id))
        self.assertIsNotNone(q.get(running.id))
        # Order of the survivors is preserved.
        self.assertEqual([j.id for j in q.all()], [queued.id, running.id])
        # Second pass is a no-op.
        self.assertEqual(q.clear_finished(), 0)


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

    def test_clear_endpoint_removes_terminal_but_keeps_active(self):
        done = jobs.queue.create("sync", "x")
        jobs.queue.get(done.id).status = jobs.JobStatus.DONE
        queued = jobs.queue.create("deploy", "y")

        r = self.client.post("/api/jobs/clear")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        # Other tests may leave terminal jobs on the shared queue.
        self.assertGreaterEqual(r.json()["removed"], 1)
        self.assertIsNone(jobs.queue.get(done.id))
        self.assertIsNotNone(jobs.queue.get(queued.id))

        ids = [j["id"] for j in self.client.get("/api/jobs").json()["jobs"]]
        self.assertNotIn(done.id, ids)
        self.assertIn(queued.id, ids)


if __name__ == "__main__":
    unittest.main()
