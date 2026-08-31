"""Tests for the co-located-manifest grouping in ``app.scanner.scan_source``.

A downloaded repo carries a ``.modelmeta.json`` sidecar in its folder; every model
file beneath that folder must collapse into a single tree card, while files without
a manifest keep the existing natural grouping (no over-grouping).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from app import scanner
from app.scanner import MANIFEST_NAME, scan_source

SOURCE = "my source"


def _write(root: str, rel: str, body: bytes = b"x" * 1024) -> None:
    path = os.path.join(root, rel)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(body)


def _manifest(root: str, rel_dir: str, repo_id: str, model_name: str | None = None) -> None:
    path = os.path.join(root, rel_dir, MANIFEST_NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"repo_id": repo_id, "model_name": model_name, "tags": ["llm"]}, fh)


class ManifestGroupingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_flat_files_with_manifest_collapse_into_one_card(self):
        _write(self.tmp, "org/repo/model-Q4_K_M.gguf")
        _write(self.tmp, "org/repo/model-IQ3_XS.gguf")
        _manifest(self.tmp, "org/repo", "org/repo", model_name="MyModel")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 1)
        m = models[0]
        self.assertEqual(m.kind, "tree")
        self.assertEqual(m.name, "MyModel")
        self.assertEqual(m.file_count, 2)
        self.assertEqual(m.path, "org/repo")
        self.assertEqual(sorted(m.files),
                         ["org/repo/model-IQ3_XS.gguf", "org/repo/model-Q4_K_M.gguf"])

    def test_subfolder_files_are_owned_by_repo_root_manifest(self):
        # HF repos keep upstream subpaths; the manifest at the repo root must own
        # every file beneath it, not just immediate children.
        _write(self.tmp, "org/repo/model-Q4_K_M.gguf")
        _write(self.tmp, "org/repo/quant/model-IQ3_XS.gguf")
        _write(self.tmp, "org/repo/other/deep/model-f16.gguf")
        _manifest(self.tmp, "org/repo", "org/repo")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].kind, "tree")
        self.assertEqual(models[0].file_count, 3)
        self.assertEqual(models[0].path, "org/repo")
        self.assertEqual(models[0].name, "repo")  # basename fallback when no model_name

    def test_distinct_repos_with_manifests_stay_separate(self):
        _write(self.tmp, "org/a/m-Q4.gguf")
        _write(self.tmp, "org/a/m-IQ3.gguf")
        _write(self.tmp, "org/b/n-Q4.gguf")
        _manifest(self.tmp, "org/a", "org/a")
        _manifest(self.tmp, "org/b", "org/b")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 2)
        paths = {m.path for m in models}
        self.assertEqual(paths, {"org/a", "org/b"})

    def test_files_without_manifest_stay_independent(self):
        # No sidecar -> natural grouping; a flat dir of GGUFs is one card per file.
        _write(self.tmp, "org/a/m-Q4.gguf")
        _write(self.tmp, "org/a/m-IQ3.gguf")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 2)
        self.assertTrue(all(m.kind == "file" for m in models))

    def test_invalid_manifest_is_ignored(self):
        _write(self.tmp, "org/a/m-Q4.gguf")
        _write(self.tmp, "org/a/m-IQ3.gguf")
        _write(self.tmp, "org/a/" + MANIFEST_NAME, b"{not json")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 2)
        self.assertTrue(all(m.kind == "file" for m in models))

    def test_single_file_with_manifest_is_still_a_tree_card(self):
        _write(self.tmp, "org/repo/model-Q4_K_M.gguf")
        _manifest(self.tmp, "org/repo", "org/repo", model_name="Solo")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].kind, "tree")
        self.assertEqual(models[0].name, "Solo")


if __name__ == "__main__":
    unittest.main()
