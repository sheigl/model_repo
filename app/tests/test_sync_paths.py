"""Tests for structure preservation in cache-mode deploys (/api/sync).

The LLM cache mirrors HF's layout under <root>/<repo_id>/...; deploying any of
its files must keep that relative path on the target instead of flattening to
the bare filename. Category-style sources (ComfyUI) keep deploying flat into
the per-category remote_root, which is where that app expects the file.

The route only enqueues a job (no network), so these tests assert the computed
``rel`` (the remote target subpath) carried on each selected file.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from app import main as main_app
from app.config import Config, PathMapping, Source, TargetMachine


class SyncStructureTests(unittest.TestCase):
    def setUp(self):
        self.llm_root = tempfile.mkdtemp()
        self.cui_root = tempfile.mkdtemp()
        self.cfg = Config(
            sources=[
                Source(name="LLM cache", root=self.llm_root, kind="flat"),
                Source(name="ComfyUI store", root=self.cui_root, kind="comfyui"),
            ],
            targets={
                "box": TargetMachine(
                    name="box", host="h", user="u",
                    categories={"llm": PathMapping(remote_root="/dest")}),
            },
        )

    def _sync(self, source_root, local_path, files):
        with patch.object(main_app, "_cfg", return_value=self.cfg):
            resp = main_app.api_sync(
                source_root=source_root, local_path=local_path,
                target="box", remote_root="/dest", category="llm", files=files)
        job = main_app.queue.get(resp["job_id"])
        main_app.queue.cancel_job(resp["job_id"])
        return job.meta["selected"]

    def test_llm_single_file_keeps_full_relative_path(self):
        selected = self._sync(self.llm_root, "mradermacher/X/Model-Q6_K.gguf",
                              ["mradermacher/X/Model-Q6_K.gguf"])
        self.assertEqual(selected[0]["rel"], "mradermacher/X/Model-Q6_K.gguf")

    def test_llm_tree_files_mirror_cache_structure(self):
        selected = self._sync(self.llm_root, "org/repo",
                              ["org/repo/sub/w.gguf", "org/repo/top.gguf"])
        self.assertEqual([s["rel"] for s in selected],
                         ["org/repo/sub/w.gguf", "org/repo/top.gguf"])

    def test_llm_root_flat_file_stays_flat(self):
        selected = self._sync(self.llm_root, "Bonsai-27B-Q1_0.gguf",
                              ["Bonsai-27B-Q1_0.gguf"])
        self.assertEqual(selected[0]["rel"], "Bonsai-27B-Q1_0.gguf")

    def test_comfyui_single_file_stays_flat(self):
        selected = self._sync(self.cui_root, "text_encoders/t5.safetensors",
                              ["text_encoders/t5.safetensors"])
        self.assertEqual(selected[0]["rel"], "t5.safetensors")

    def test_comfyui_tree_keeps_current_behavior(self):
        selected = self._sync(self.cui_root, "loras/SDXL",
                              ["loras/SDXL/one.safetensors",
                               "loras/other.safetensors"])
        self.assertEqual([s["rel"] for s in selected],
                         ["one.safetensors", "other.safetensors"])

    def test_local_entry_points_at_cached_file(self):
        selected = self._sync(self.llm_root, "a/repo/m.gguf", ["a/repo/m.gguf"])
        self.assertEqual(
            selected[0]["local"],
            os.path.abspath(os.path.join(self.llm_root, "a/repo/m.gguf")))


if __name__ == "__main__":
    unittest.main()