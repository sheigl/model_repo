"""Tests for the MCP server tools (app/mcp_server.py).

Uses the SDK's in-process ``Client`` against a built ``MCPServer`` so no HTTP
transport is needed. Hub/cache tools avoid the network by feeding a fake
provider; package tools use an isolated SQLite DB. Job tools run against the
real in-memory JobQueue (safe: no worker started here).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import anyio
from mcp import Client

from app import mcp_server
from app import model_store
from app.config import Config, Source, TargetMachine, PathMapping, RegistryConfig


class _FakeProvider:
    """Mimics HuggingFaceProvider without any network."""

    id = "huggingface"

    def __init__(self, files_by_repo, sizes_by_repo=None):
        self._files = {r: list(fs) for r, fs in files_by_repo.items()}
        self._sizes = {r: dict((sizes_by_repo or {}).get(r, {})) for r in files_by_repo}

    def resolve(self, model: str) -> str:
        return model if "/" in model else f"unsloth/{model}-GGUF"

    def model_name(self, repo_id: str) -> str:
        return repo_id.split("/")[-1]

    def list_files(self, repo_id: str):
        return sorted(self._files.get(repo_id, []))

    def list_files_with_sizes(self, repo_id: str):
        return self._sizes.get(repo_id, {})

    def search(self, query, *, limit=12, cursor=None, gguf_only=False, pipeline=None):
        return {"results": [{"id": "org/demo", "author": "org", "downloads": 10,
                             "likes": 2, "pipeline": "text-generation",
                             "tags": ["gguf"], "last_modified": None}],
                "next_cursor": None, "has_more": False}


def _remote_files():
    return {"org/demo": ["model-Q4_K_M.gguf", "model-Q8_0.gguf", "mmproj-f16.gguf"]}


def _cfg() -> Config:
    cfg = Config()
    cfg.registry = RegistryConfig(provider="huggingface", repo_root="/tmp/mcp-root")
    cfg.targets = {
        "ai.home": TargetMachine(name="ai.home", host="ai.home", user="sheigl",
                                 ntfs=True, categories={"llm": PathMapping(remote_root="/mnt/1TB/x")}),
    }
    return cfg


def _run(async_fn):
    anyio.run(async_fn)


class McpToolSetup(unittest.TestCase):
    def setUp(self):
        self._old_db = model_store._DB_PATH
        self._tmp = tempfile.mkdtemp(prefix="mcp-test-")
        model_store._DB_PATH = Path(self._tmp) / "models.db"


class McpServerConstructionTests(McpToolSetup):
    def test_create_mcp_server_lists_all_tools(self):
        async def run():
            mcp = mcp_server.create_mcp_server()
            async with Client(mcp, raise_exceptions=True) as client:
                tools = await client.list_tools()
                names = {t.name for t in tools.tools}
                expected = {
                    "hub_search", "hub_files",
                    "cache_list", "cache_status",
                    "job_list", "job_status", "job_cancel",
                    "download_model", "deploy_model",
                    "target_list", "target_status", "disk_usage",
                    "package_list", "package_create", "package_update",
                    "package_status", "package_fetch", "package_delete",
                }
                self.assertTrue(expected <= names, f"missing: {expected - names}")
                # Every tool advertises an output schema (structured output).
                for t in tools.tools:
                    self.assertIsNotNone(t.output_schema, f"{t.name} lacks output_schema")
        _run(run)

    def test_build_mcp_app_returns_mount(self):
        mount = mcp_server.build_mcp_app(_cfg())
        self.assertIsNotNone(mount.app)
        self.assertIsNotNone(mount.session_manager)


class HubAndCacheToolTests(McpToolSetup):
    def test_hub_search(self):
        async def run():
            mcp = mcp_server.create_mcp_server()
            async with Client(mcp, raise_exceptions=True) as client:
                with patch("app.mcp_server._provider", return_value=_FakeProvider(_remote_files())):
                    res = await client.call_tool("hub_search", {"query": "demo"})
                self.assertFalse(res.is_error)
                self.assertEqual(res.structured_content["results"][0]["id"], "org/demo")
        _run(run)

    def test_hub_files_lists_with_sizes(self):
        sizes = {"org/demo": {"model-Q4_K_M.gguf": 1234}}
        async def run():
            mcp = mcp_server.create_mcp_server()
            async with Client(mcp, raise_exceptions=True) as client:
                with patch("app.mcp_server._provider", return_value=_FakeProvider(_remote_files(), sizes)):
                    res = await client.call_tool("hub_files", {"repo": "org/demo"})
                self.assertFalse(res.is_error)
                sc = res.structured_content
                self.assertEqual(sc["repo"], "org/demo")
                self.assertIn("model-Q4_K_M.gguf", sc["files"])
                self.assertEqual(sc["sizes"]["model-Q4_K_M.gguf"], 1234)
        _run(run)

    def test_cache_status(self):
        async def run():
            mcp = mcp_server.create_mcp_server()
            async with Client(mcp, raise_exceptions=True) as client:
                with patch("app.mcp_server._provider", return_value=_FakeProvider(_remote_files())):
                    res = await client.call_tool("cache_status", {"repo": "org/demo"})
                self.assertFalse(res.is_error)
                self.assertEqual(res.structured_content["repo"], "org/demo")
        _run(run)


class JobToolTests(McpToolSetup):
    def test_job_list_and_status(self):
        async def run():
            mcp = mcp_server.create_mcp_server()
            async with Client(mcp, raise_exceptions=True) as client:
                res = await client.call_tool("job_list", {})
                self.assertFalse(res.is_error)
                self.assertIn("jobs", res.structured_content)
                res2 = await client.call_tool("download_model", {"model": "org/demo", "quants": ["q4_k_m"]})
                self.assertFalse(res2.is_error)
                job_id = res2.structured_content["job_id"]
                self.assertEqual(res2.structured_content["mode"], "cache")
                st = await client.call_tool("job_status", {"job_id": job_id})
                self.assertEqual(st.structured_content["id"], job_id)
                self.assertEqual(st.structured_content["kind"], "hf_download")
        _run(run)

    def test_job_status_unknown_raises(self):
        async def run():
            mcp = mcp_server.create_mcp_server()
            async with Client(mcp, raise_exceptions=True) as client:
                res = await client.call_tool("job_status", {"job_id": "nope"})
                self.assertTrue(res.is_error)
        _run(run)

    def test_deploy_unknown_target_raises(self):
        async def run():
            mcp = mcp_server.create_mcp_server()
            async with Client(mcp, raise_exceptions=True) as client:
                res = await client.call_tool("deploy_model", {"model": "org/demo", "target": "ghost"})
                self.assertTrue(res.is_error)
                self.assertIn("unknown target", res.content[0].text)
        _run(run)

    def test_deploy_valid_target_queues_deploy_job(self):
        async def run():
            mcp = mcp_server.create_mcp_server()
            async with Client(mcp, raise_exceptions=True) as client:
                res = await client.call_tool("deploy_model", {"model": "org/demo", "target": "ai.home"})
                self.assertFalse(res.is_error)
                self.assertEqual(res.structured_content["mode"], "deploy")
                job_id = res.structured_content["job_id"]
                st = await client.call_tool("job_status", {"job_id": job_id})
                self.assertEqual(st.structured_content["kind"], "deploy")
        _run(run)


class TargetDiskToolTests(McpToolSetup):
    def test_target_list(self):
        async def run():
            mcp = mcp_server.create_mcp_server()
            async with Client(mcp, raise_exceptions=True) as client:
                with patch("app.mcp_server._cfg", return_value=_cfg()):
                    res = await client.call_tool("target_list", {})
                self.assertFalse(res.is_error)
                targets = res.structured_content["targets"]
                self.assertIn("ai.home", targets)
                self.assertEqual(targets["ai.home"]["host"], "ai.home")
                self.assertEqual(targets["ai.home"]["categories"]["llm"]["remote_root"], "/mnt/1TB/x")
        _run(run)

    def test_target_status_disk_usage(self):
        from app import main as main_app
        main_app._conn_cache["ai.home"] = (True, "ok", 123.0)
        main_app._disk_cache = {"local": None, "targets": {}}
        async def run():
            mcp = mcp_server.create_mcp_server()
            async with Client(mcp, raise_exceptions=True) as client:
                res = await client.call_tool("target_status", {})
                self.assertFalse(res.is_error)
                self.assertEqual(res.structured_content["targets"]["ai.home"]["ok"], True)
                self.assertEqual(res.structured_content["targets"]["ai.home"]["detail"], "ok")
                res2 = await client.call_tool("disk_usage", {})
                self.assertFalse(res2.is_error)
                self.assertIn("local", res2.structured_content)
                self.assertIn("targets", res2.structured_content)
        _run(run)

    def tearDown(self):
        super().tearDown()
        from app import main as main_app
        main_app._conn_cache.clear()
        main_app._disk_cache.clear()


class PackageToolTests(McpToolSetup):
    def test_package_create_list_delete(self):
        async def run():
            mcp = mcp_server.create_mcp_server()
            async with Client(mcp, raise_exceptions=True) as client:
                res = await client.call_tool(
                    "package_create",
                    {"name": "My Pkg", "task": "t2i",
                     "components": [{"repo": "org/a", "files": ["m.safetensors"]}]},
                )
                self.assertFalse(res.is_error)
                pkg_id = res.structured_content["package"]["id"]

                lst = await client.call_tool("package_list", {})
                names = [p["name"] for p in lst.structured_content["packages"]]
                self.assertIn("My Pkg", names)

                # status_for needs a provider; stub it to avoid network.
                with patch("app.packages.status_for") as sf:
                    sf.return_value = {"package_id": pkg_id, "components": [], "counts": {}}
                    st = await client.call_tool("package_status", {"package_id": pkg_id})
                self.assertFalse(st.is_error)
                self.assertEqual(st.structured_content["package_id"], pkg_id)

                fetch = await client.call_tool("package_fetch", {"package_id": pkg_id})

                up = await client.call_tool("package_update", {"package_id": pkg_id, "name": "New"})
                self.assertFalse(up.is_error)
                self.assertEqual(up.structured_content["package"]["name"], "New")

                dl = await client.call_tool("package_delete", {"package_id": pkg_id})
                self.assertFalse(dl.is_error)
                self.assertEqual(dl.structured_content["removed"], pkg_id)
        _run(run)

    def test_package_validation_errors(self):
        async def run():
            mcp = mcp_server.create_mcp_server()
            async with Client(mcp, raise_exceptions=True) as client:
                res = await client.call_tool("package_create", {"name": "x", "components": []})
                self.assertTrue(res.is_error)
                res2 = await client.call_tool("package_status", {"package_id": "ghost"})
                self.assertTrue(res2.is_error)
                res3 = await client.call_tool("package_delete", {"package_id": "ghost"})
                self.assertTrue(res3.is_error)
        _run(run)

    def tearDown(self):
        super().tearDown()
        from app import main as main_app
        main_app._conn_cache.clear()


if __name__ == "__main__":
    unittest.main()