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
        _write(self.tmp, "org/repo/model-Q5_K_M.gguf")
        _manifest(self.tmp, "org/repo", "org/repo", model_name="MyModel")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 1)
        m = models[0]
        self.assertEqual(m.kind, "tree")
        self.assertEqual(m.name, "MyModel")
        self.assertEqual(m.file_count, 2)
        self.assertEqual(m.path, "org/repo")
        self.assertEqual(sorted(m.files),
                         ["org/repo/model-Q4_K_M.gguf", "org/repo/model-Q5_K_M.gguf"])

    def test_subfolder_files_are_owned_by_repo_root_manifest(self):
        # HF repos keep upstream subpaths; the manifest at the repo root must own
        # every file beneath it, not just immediate children.
        _write(self.tmp, "org/repo/model-Q4_K_M.gguf")
        _write(self.tmp, "org/repo/quant/model-Q5_K_M.gguf")
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


class NaturalGroupingTests(unittest.TestCase):
    """GGUF repos with auxiliary files (mmproj / MTP subdirs) group without a
    manifest, while a flat directory of independent quants stays one-per-file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mmproj_plus_quants_groups_into_one_tree(self):
        # Exactly the unsloth/gemma case: main quants + mmproj projector.
        _write(self.tmp, "u/model/model-Q4_K_M.gguf")
        _write(self.tmp, "u/model/model-Q5_K_M.gguf")
        _write(self.tmp, "u/model/mmproj-F16-model.gguf")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 1)
        m = models[0]
        self.assertEqual(m.kind, "tree")
        self.assertEqual(m.file_count, 3)
        self.assertEqual(m.path, "u/model")

    def test_mtp_subdir_groups_with_quants_into_one_tree(self):
        _write(self.tmp, "u/model/model-Q4_K_M.gguf")
        _write(self.tmp, "u/model/model-Q5_K_M.gguf")
        _write(self.tmp, "u/model/MTP/mtp-model-BF16.gguf")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].kind, "tree")
        self.assertEqual(models[0].file_count, 3)

    def test_flat_quant_dir_stays_one_per_file(self):
        # Pure flat quant repo without aux files: each quant is independent.
        _write(self.tmp, "u/model/model-Q4_K_M.gguf")
        _write(self.tmp, "u/model/model-Q5_K_M.gguf")
        _write(self.tmp, "u/model/other-quant-Q2_K.gguf")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 3)
        self.assertTrue(all(ma.kind == "file" for ma in models))

    def test_mmproj_only_same_model_groups_into_one_tree(self):
        # A folder holding only projectors for one model is still one model's
        # components, so it groups rather than showing as two stray cards.
        _write(self.tmp, "u/proj/mmproj-F16-model.gguf")
        _write(self.tmp, "u/proj/mmproj-BF16-model.gguf")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].kind, "tree")
        self.assertEqual(models[0].file_count, 2)

    def test_mmproj_for_different_models_stays_separate(self):
        # Projectors for two distinct models are not one tree.
        _write(self.tmp, "u/proj/mmproj-F16-alpha.gguf")
        _write(self.tmp, "u/proj/mmproj-BF16-beta.gguf")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 2)
        self.assertTrue(all(ma.kind == "file" for ma in models))

    def test_multi_model_flat_dir_does_not_over_group(self):
        # A loose dir with several distinct models (even with mmproj present)
        # keeps one card per file: no single base model to group around.
        _write(self.tmp, "u/gemma/gemma-4-12b-it-Q4_K_M.gguf")
        _write(self.tmp, "u/gemma/gemma-4-31B-it-Q8_0.gguf")
        _write(self.tmp, "u/gemma/mmproj-F16-gemma-4-12b-it.gguf")
        _write(self.tmp, "u/gemma/mmproj-F16-gemma-4-31B-it.gguf")
        models = scan_source(SOURCE, self.tmp)
        self.assertEqual(len(models), 4)
        self.assertTrue(all(ma.kind == "file" for ma in models))


if __name__ == "__main__":
    unittest.main()
