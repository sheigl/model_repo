"""Tests for custom model packages (app/packages.py) + its FastAPI routes."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as main_app
from app import model_store
from app import packages


class StubProvider:
    """Mimics HuggingFaceProvider: files land at <local_dir>/<repo>/<file>."""

    def __init__(self, remote):
        self.remote = remote  # {repo: {filename: size_bytes}}
        self.downloads = []

    def list_files(self, repo_id):
        return list(self.remote.get(repo_id, {}))

    def list_files_with_sizes(self, repo_id):
        return dict(self.remote.get(repo_id, {}))

    def download(self, repo_id, filename, local_dir):
        self.downloads.append((repo_id, filename))
        dest_dir = Path(local_dir) / repo_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        dest.write_bytes(b"x" * self.remote[repo_id][filename])
        return str(dest)


def _remote():
    return {
        "org/base": {"model.safetensors": 1000, "config.json": 10},
        "org/lora": {"adapter.safetensors": 500},
    }


def _components():
    return [
        {"label": "base", "repo": "org/base", "files": ["model.safetensors", "config.json"]},
        {"label": "lora", "repo": "org/lora", "files": ["adapter.safetensors"]},
    ]


class PackageStoreTests(unittest.TestCase):
    def setUp(self):
        self._old_db = model_store._DB_PATH
        self._tmp = tempfile.mkdtemp(prefix="pkg-test-")
        model_store._DB_PATH = Path(self._tmp) / "models.db"

    def tearDown(self):
        model_store._DB_PATH = self._old_db

    def test_create_and_list(self):
        pkg = packages.create_package("Z-Image 6B", "t2i", "notes", _components())
        self.assertTrue(pkg["id"].startswith("z-image-6b-"))
        self.assertEqual(pkg["name"], "Z-Image 6B")
        self.assertEqual(len(pkg["components"]), 2)
        names = [p["name"] for p in packages.list_packages()]
        self.assertIn("Z-Image 6B", names)
        self.assertIsNone(packages.get_package("nope"))

    def test_create_rejects_duplicate_name_case_insensitive(self):
        packages.create_package("Z-Image", "t2i", "", _components())
        with self.assertRaises(ValueError):
            packages.create_package("z-image", "t2i", "", _components())

    def test_component_validation(self):
        with self.assertRaises(ValueError):
            packages.create_package("", "t2i", "", _components())
        with self.assertRaises(ValueError):
            packages.create_package("x", "t2i", "", [])
        with self.assertRaises(ValueError):
            packages.create_package("x", "t2i", "", [{"repo": "org/a", "files": []}])
        with self.assertRaises(ValueError):
            packages.create_package("x", "t2i", "", [{"repo": "", "files": ["a.bin"]}])
        with self.assertRaises(ValueError):
            packages.create_package("x", "t2i", "",
                                    [{"repo": "org/a", "files": ["../evil.bin"]}])

    def test_component_normalization(self):
        pkg = packages.create_package("norm", "t2i", "",
                                      [{"repo": "/org/a/", "files": ["//a.bin", "a.bin", "sub\\b.bin"]}])
        c = pkg["components"][0]
        self.assertEqual(c["repo"], "org/a")
        self.assertEqual(c["files"], ["a.bin", "sub/b.bin"])
        self.assertEqual(c["label"], "a")

    def test_update(self):
        pkg = packages.create_package("old", "t2i", "", _components())
        upd = packages.update_package(pkg["id"], "new", "img2img", "d", _components())
        self.assertEqual(upd["name"], "new")
        self.assertEqual(upd["task"], "img2img")
        other = packages.create_package("taken", "", "", _components())
        with self.assertRaises(ValueError):
            packages.update_package(pkg["id"], "taken", "", "", _components())
        self.assertIsNone(packages.update_package("ghost", "x", "", "", _components()))
        self.assertIsNotNone(other)

    def test_delete(self):
        pkg = packages.create_package("doomed", "t2i", "", _components())
        self.assertTrue(packages.delete_package(pkg["id"]))
        self.assertFalse(packages.delete_package(pkg["id"]))
        self.assertIsNone(packages.get_package(pkg["id"]))


class FetchPackageTests(unittest.TestCase):
    def setUp(self):
        self._old_db = model_store._DB_PATH
        self._tmp = tempfile.mkdtemp(prefix="pkg-test-")
        model_store._DB_PATH = Path(self._tmp) / "models.db"
        self.root = Path(self._tmp) / "repo_root"
        self.root.mkdir()
        self.provider = StubProvider(_remote())
        self.pkg = packages.create_package("fetchme", "t2i", "", _components())

    def tearDown(self):
        model_store._DB_PATH = self._old_db

    def _files(self, *names):
        return [self.root / "org" / r / f for r, f in names]

    def test_fetch_downloads_all_missing(self):
        summary = packages.fetch_package(self.pkg, str(self.root), self.provider)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["downloaded"], 3)
        self.assertEqual(summary["cached"], 0)
        self.assertEqual(len(self.provider.downloads), 3)
        a, b, c = self._files(("base", "model.safetensors"),
                              ("base", "config.json"),
                              ("lora", "adapter.safetensors"))
        self.assertEqual(a.read_bytes(), b"x" * 1000)
        self.assertEqual(b.read_bytes(), b"x" * 10)
        self.assertEqual(c.read_bytes(), b"x" * 500)

    def test_fetch_second_run_is_all_cached(self):
        packages.fetch_package(self.pkg, str(self.root), self.provider)
        n_downloads = len(self.provider.downloads)
        summary = packages.fetch_package(self.pkg, str(self.root), self.provider)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["cached"], 3)
        self.assertEqual(summary["downloaded"], 0)
        self.assertEqual(len(self.provider.downloads), n_downloads)

    def test_fetch_dry_run_downloads_nothing(self):
        summary = packages.fetch_package(self.pkg, str(self.root), self.provider, dry_run=True)
        self.assertTrue(summary["ok"])
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["downloaded"], 0)
        self.assertEqual(len(self.provider.downloads), 0)
        self.assertFalse((self.root / "org" / "base" / "model.safetensors").exists())

    def test_fetch_refreshes_stale_file(self):
        packages.fetch_package(self.pkg, str(self.root), self.provider)
        # Upstream re-released the base weights bigger.
        self.provider.remote["org/base"]["model.safetensors"] = 2000
        summary = packages.fetch_package(self.pkg, str(self.root), self.provider)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["cached"], 2)
        local = self.root / "org" / "base" / "model.safetensors"
        self.assertEqual(len(local.read_bytes()), 2000)

    def test_fetch_stops_when_cancelled(self):
        with self.assertRaises(RuntimeError):
            packages.fetch_package(self.pkg, str(self.root), self.provider,
                                   should_cancel=lambda: True)
        self.assertEqual(len(self.provider.downloads), 0)

    def test_fetch_records_errors_but_keeps_going(self):
        real = self.provider.download

        def flaky(repo_id, filename, local_dir):
            if filename == "config.json":
                raise OSError("boom")
            return real(repo_id, filename, local_dir)

        self.provider.download = flaky
        summary = packages.fetch_package(self.pkg, str(self.root), self.provider)
        self.assertFalse(summary["ok"])
        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(summary["errors"][0]["file"], "config.json")
        self.assertEqual("boom", summary["errors"][0]["error"])
        self.assertEqual(summary["downloaded"], 2)


class StatusForTests(unittest.TestCase):
    def setUp(self):
        self._old_db = model_store._DB_PATH
        self._tmp = tempfile.mkdtemp(prefix="pkg-test-")
        model_store._DB_PATH = Path(self._tmp) / "models.db"
        self.root = Path(self._tmp) / "repo_root"
        self.root.mkdir()
        self.provider = StubProvider(_remote())
        self.pkg = packages.create_package("status", "t2i", "", _components())

    def tearDown(self):
        model_store._DB_PATH = self._old_db

    def test_all_missing(self):
        st = packages.status_for(self.pkg, str(self.root), self.provider)
        self.assertEqual(st["counts"]["total"], 3)
        self.assertEqual(st["counts"]["missing"], 3)
        self.assertEqual(st["counts"]["bytes_remote"], 1510)

    def test_all_cached_after_fetch(self):
        packages.fetch_package(self.pkg, str(self.root), self.provider)
        st = packages.status_for(self.pkg, str(self.root), self.provider)
        self.assertEqual(st["counts"]["cached"], 3)
        self.assertEqual(st["counts"]["missing"], 0)
        self.assertEqual(st["counts"]["bytes_local"], 1510)

    def test_update_available_flagged(self):
        packages.fetch_package(self.pkg, str(self.root), self.provider)
        self.provider.remote["org/lora"]["adapter.safetensors"] = 999
        st = packages.status_for(self.pkg, str(self.root), self.provider)
        self.assertEqual(st["counts"]["cached"], 2)
        self.assertEqual(st["counts"]["update_available"], 1)
        lora = [c for c in st["components"] if c["repo"] == "org/lora"][0]
        self.assertEqual(lora["files"][0]["state"], "update-available")

    def test_unknown_remote_size_treated_as_cached(self):
        packages.fetch_package(self.pkg, str(self.root), self.provider)

        def no_sizes(repo_id):
            return {}

        self.provider.list_files_with_sizes = no_sizes
        st = packages.status_for(self.pkg, str(self.root), self.provider)
        self.assertEqual(st["counts"]["cached"], 3)
        self.assertEqual(st["counts"]["update_available"], 0)


class _FakeJob:
    """Mirrors the real Job dataclass: unexpected constructor kwargs raise,
    and meta is an empty dict the caller fills in after creation."""
    def __init__(self, job_id, kind, description, **meta):
        if meta:
            raise TypeError(
                f"Job.__init__() got an unexpected keyword argument {next(iter(meta))!r}")
        self.id = job_id
        self.kind = kind
        self.description = description
        self.meta = {}
        self.result = {}
        self.progress = 0.0

    def set_progress(self, pct):
        self.progress = min(100.0, max(0.0, float(pct)))


class _FakeQueue:
    def __init__(self):
        self.created = []

    def create(self, kind, description, **meta):
        job = _FakeJob(f"fake-{len(self.created)}", kind, description, **meta)
        self.created.append(job)
        return job

    def is_cancelled(self, job_id):
        return False


class PackageRouteTests(unittest.TestCase):
    def setUp(self):
        self._old_db = model_store._DB_PATH
        self._tmp = tempfile.mkdtemp(prefix="pkg-test-")
        model_store._DB_PATH = Path(self._tmp) / "models.db"
        self.root = Path(self._tmp) / "repo_root"
        self.root.mkdir()
        self.provider = StubProvider(_remote())
        self.fake_queue = _FakeQueue()
        self.cfg = type("Cfg", (), {})()
        self.cfg.registry = type("Reg", (), {})()
        self.cfg.registry.provider = "huggingface"
        self.cfg.registry.token_env = "HF_TOKEN"
        self.cfg.registry.repo_root = str(self.root)

        patches = [
            patch.object(main_app, "_cfg", return_value=self.cfg),
            patch.object(main_app, "get_provider", return_value=self.provider),
            patch.object(main_app, "queue", self.fake_queue),
        ]
        for p in patches:
            p.start()
        self._patches = patches
        self.client = TestClient(main_app.app)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        model_store._DB_PATH = self._old_db

    def _create(self):
        r = self.client.post("/api/packages", json={
            "name": "Route Pkg", "task": "t2i", "description": "d",
            "components": _components()})
        self.assertEqual(r.status_code, 200)
        return r.json()["package"]

    def test_page_renders(self):
        r = self.client.get("/packages")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Packages", r.text)

    def test_create_validate_400(self):
        r = self.client.post("/api/packages", json={
            "name": "bad", "components": [{"repo": "org/a", "files": []}]})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()["ok"])

    def test_crud_roundtrip(self):
        pkg = self._create()
        data = self.client.get("/api/packages").json()
        self.assertTrue(data["ok"])
        self.assertEqual([p["name"] for p in data["packages"]], ["Route Pkg"])

        r = self.client.post(f"/api/packages/{pkg['id']}", json={
            "name": "Renamed", "task": "img2img", "description": "",
            "components": _components()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["package"]["name"], "Renamed")

        r = self.client.post(f"/api/packages/{pkg['id']}/delete")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/packages").json()["packages"], [])
        self.assertEqual(self.client.post(f"/api/packages/{pkg['id']}/delete").status_code, 404)

    def test_status_endpoint(self):
        pkg = self._create()
        packages.fetch_package(pkg, str(self.root), self.provider)
        r = self.client.get(f"/api/packages/{pkg['id']}/status")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["counts"]["cached"], 3)
        self.assertEqual(self.client.get("/api/packages/ghost/status").status_code, 404)

    def test_fetch_endpoint_queues_job(self):
        pkg = self._create()
        r = self.client.post(f"/api/packages/{pkg['id']}/fetch?dry_run=1")
        self.assertEqual(r.status_code, 200)
        job_id = r.json()["job_id"]
        job = self.fake_queue.created[-1]
        self.assertEqual(job.kind, "package_fetch")
        self.assertEqual(job.meta, {"package_id": pkg["id"], "dry_run": True})
        self.assertTrue(job_id.startswith("fake-"))
        self.assertEqual(self.client.post("/api/packages/ghost/fetch").status_code, 404)

    def test_runner_downloads_and_records_result(self):
        pkg = self._create()
        job = main_app.queue.create("package_fetch", "t")
        job.meta = {"package_id": pkg["id"], "dry_run": False}
        lines = []
        main_app._runner(job, lines.append)
        self.assertTrue(job.result.get("ok"))
        self.assertEqual(job.result["package"], "Route Pkg")
        self.assertEqual(job.result["downloaded"], 3)
        self.assertEqual(job.progress, 100.0)
        self.assertTrue(any("download" in ln for ln in lines))


if __name__ == "__main__":
    unittest.main()
