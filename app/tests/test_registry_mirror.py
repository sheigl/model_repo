"""Tests for HF-structure mirroring in the registry layer.

Covers the fix that stopped listing nested GGUFs, mmproj detection on basenames,
and ``ensure_cached`` storing files at their HF-relative path (with a conditional
move for friendly-named aux files). Downloads are simulated by ``_FakeProvider``
so no network is required; the real ``list_files`` uses a mocked hub call.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from app.registry import (HuggingFaceProvider, build_deploy_plan, ensure_cached,
                          find_aux)


class _FakeProvider(HuggingFaceProvider):
    """Backed by in-memory file lists; download writes the file at its
    HF-relative path under local_dir, like ``hf_hub_download``."""

    def __init__(self, files_by_repo, token=None):
        super().__init__(token=token)
        self._files = {r: [f for f in fs if f.endswith(".gguf")]
                       for r, fs in files_by_repo.items()}

    def list_files(self, repo_id):
        return sorted(self._files.get(repo_id, []))

    def download(self, repo_id, filename, local_dir):
        dest = os.path.join(local_dir, filename)
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(b"gguf-bytes-" + filename.encode())
        return dest


class ListFilesTests(unittest.TestCase):
    def test_nested_gguf_included(self):
        siblings = [
            "README.md",
            "Q4K_M.gguf",
            "UD-Q4_K_M/Qwen3-Flash-Q4_K_M-0001of0001.gguf",
        ]
        with patch("app.registry.list_repo_files", return_value=siblings):
            result = HuggingFaceProvider().list_files("author/repo")
        # Only .gguf files, and the nested one is kept (no longer filtered out).
        self.assertEqual(result, [
            "Q4K_M.gguf",
            "UD-Q4_K_M/Qwen3-Flash-Q4_K_M-0001of0001.gguf",
        ])


class FindAuxTests(unittest.TestCase):
    def test_prefers_flat_preferred_variant(self):
        files = ["sub/mmproj-q4_k_m.gguf", "mmproj-f16.gguf"]
        self.assertEqual(find_aux(files), "mmproj-f16.gguf")

    def test_finds_nested_only_mmproj(self):
        self.assertEqual(
            find_aux(["foo/bar/mmproj-q8_0.gguf"]), "foo/bar/mmproj-q8_0.gguf")

    def test_absent_returns_none(self):
        self.assertIsNone(find_aux(["model-Q4_K_M.gguf"]))


class PlanAndCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_model_local_name_is_hf_relative_path(self):
        provider = _FakeProvider({"o/m": ["model-Q4_K_M.gguf", "mmproj-f16.gguf"]})
        plan = build_deploy_plan(provider, "o/m", quants=["q4_k_m"])
        model_items = [p for p in plan["plan"] if p["kind"] == "model"]
        self.assertEqual(len(model_items), 1)
        # local_name mirrors the source path (not a flattened basename).
        self.assertEqual(model_items[0]["remote_file"], model_items[0]["local_name"])

    def test_cache_mirrors_structure_and_is_idempotent(self):
        provider = _FakeProvider({"o/m": ["sub/model-Q4_K_M.gguf", "mmproj-f16.gguf"]})
        plan = build_deploy_plan(provider, "o/m", quants=["q4_k_m"])

        steps = []
        paths = ensure_cached(provider, plan, self.tmp, on_step=steps.append)

        model_path = os.path.join(self.tmp, "sub/model-Q4_K_M.gguf")
        self.assertTrue(os.path.exists(model_path))
        self.assertIn(model_path, paths)
        # No flat basename was left behind in the cache root.
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "model-Q4_K_M.gguf")))

        steps.clear()
        paths2 = ensure_cached(provider, plan, self.tmp, on_step=steps.append)
        self.assertEqual(paths, paths2)
        self.assertTrue(all(s["action"] == "cached" for s in steps))

    def test_mmproj_moved_to_friendly_name(self):
        provider = _FakeProvider({"o/m": ["nested/mmproj-q4_k_m.gguf"]})
        plan = build_deploy_plan(provider, "o/m", quants=[], no_mmproj=False)
        mm = [p for p in plan["plan"] if p["kind"] == "mmproj"]
        self.assertEqual(len(mm), 1)
        friendly = mm[0]["local_name"]

        ensure_cached(provider, plan, self.tmp, on_step=lambda s: None)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, friendly)))
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp, "nested/mmproj-q4_k_m.gguf")))


if __name__ == "__main__":
    unittest.main()
