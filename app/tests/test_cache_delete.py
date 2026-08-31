"""Tests for deleting a cached model's files from disk (``app.main._remove_cache_files``
and the ``POST /api/cache/delete`` route).

Only the target model's own files are ever removed — sibling models living in the same
folder must survive. The store is isolated to a temp DB by repointing ``model_store._DB_PATH``
and the cache-delete route is driven with a Config whose source root points at a temp dir.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import main as main_app
from app import model_store
from app.config import Config, Source

SOURCE = "my source"


def _write(root: str, rel: str, body: bytes) -> str:
    path = os.path.join(root, rel)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(body)
    return path


class RemoveFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_removes_single_flat_file_and_reports_freed_bytes(self):
        _write(self.tmp, "foo-Q4_K_M.gguf", b"123456")
        result = main_app._remove_cache_files(self.tmp, ["foo-Q4_K_M.gguf"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "foo-Q4_K_M.gguf")))
        self.assertEqual(result["freed_bytes"], 6)
        self.assertEqual(result["deleted"], ["foo-Q4_K_M.gguf"])
        self.assertEqual(result["errors"], [])

    def test_removes_tree_and_prunes_empty_dirs_but_keeps_siblings(self):
        # Two models sharing a parent package dir; deleting one quant subdir must not
        # touch the other model's files.
        _write(self.tmp, "pkg/UD-IQ2_XXS/config.json", b"cfg")
        _write(self.tmp, "pkg/UD-IQ2_XXS/w-00001-of-00002.safetensors", b"weight")
        _write(self.tmp, "pkg/Q4_K_M/config.json", b"other-cfg")
        result = main_app._remove_cache_files(
            self.tmp, ["pkg/UD-IQ2_XXS/config.json", "pkg/UD-IQ2_XXS/w-00001-of-00002.safetensors"])
        # The emptied quant dir is pruned...
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "pkg/UD-IQ2_XXS")))
        # ...but the sibling model's files remain.
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "pkg/Q4_K_M/config.json")))
        self.assertEqual(result["freed_bytes"], len(b"cfg") + len(b"weight"))

    def test_missing_files_are_skipped_without_error(self):
        _write(self.tmp, "present.gguf", b"abc")
        result = main_app._remove_cache_files(
            self.tmp, ["missing.gguf", "present.gguf"])
        self.assertEqual(result["deleted"], ["present.gguf"])
        self.assertEqual(result["errors"], [])

    def test_never_leaves_a_source_root_behind(self):
        # A lone flat file at the root: after removal nothing should be deleted under root,
        # and pruning must not remove the root itself.
        _write(self.tmp, "lonely.gguf", b"x")
        main_app._remove_cache_files(self.tmp, ["lonely.gguf"])
        self.assertTrue(os.path.isdir(self.tmp))


class CacheDeleteRouteTests(unittest.TestCase):
    def setUp(self):
        self._old = model_store._DB_PATH
        model_store._DB_PATH = Path(tempfile.mkdtemp()) / "models.db"
        self.tmp = tempfile.mkdtemp()
        self.cfg = Config(sources=[Source(name=SOURCE, root=self.tmp)])

    def tearDown(self):
        model_store._DB_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_route_deletes_files_and_clears_metadata(self):
        _write(self.tmp, "foo-Q4_K_M.gguf", b"123456")
        key = model_store.key_for(SOURCE, "foo-Q4_K_M.gguf")
        model_store.save(key, {"display_name": "Foo", "category": "llm"}, provider="huggingface")

        with patch.object(main_app, "_cfg", return_value=self.cfg):
            data = main_app.api_cache_delete(key=key)
        self.assertTrue(data["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "foo-Q4_K_M.gguf")))
        # Metadata overlay is cleared too.
        self.assertEqual(model_store.get(key), {})

    def test_route_removes_manifest_sidecar_from_repo_root(self):
        # Manifest card whose files sit in a subfolder of the repo dir: deleting the
        # card must also remove the sidecar (found in an ancestor of the files' common
        # dir) and prune the now-empty repo folder.
        import json as _json
        _write(self.tmp, "org/repo/quant/model-Q4_K_M.gguf", b"123456")
        _write(self.tmp, "org/repo/quant/model-IQ3_XS.gguf", b"abcdef")
        manifest = os.path.join(self.tmp, "org/repo", ".modelmeta.json")
        with open(manifest, "w", encoding="utf-8") as fh:
            _json.dump({"repo_id": "org/repo", "model_name": "Repo", "tags": []}, fh)
        key = model_store.key_for(SOURCE, "org/repo")

        with patch.object(main_app, "_cfg", return_value=self.cfg):
            data = main_app.api_cache_delete(key=key)
        self.assertTrue(data["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "org/repo/quant")))
        self.assertFalse(os.path.exists(manifest))
        self.assertEqual(model_store.get(key), {})

    def test_route_404s_for_unknown_key(self):
        import json as _json
        with patch.object(main_app, "_cfg", return_value=self.cfg):
            resp = main_app.api_cache_delete(key=model_store.key_for(SOURCE, "nope.gguf"))
        self.assertEqual(resp.status_code, 404)
        data = _json.loads(resp.body)
        self.assertFalse(data["ok"])

    def test_route_groups_folder_writes_sidecar_and_scan_collapses(self):
        # Manual grouping: a folder of loose files (including a subfolder) becomes one
        # tree card once the sidecar is written — mirroring what a fresh download does.
        import json as _json
        from app.scanner import scan_source
        _write(self.tmp, "org/repo/model-f16.gguf", b"123456")
        _write(self.tmp, "org/repo/quant/model-Q4_K_M.gguf", b"abcdef")
        _write(self.tmp, "elsewhere/flat.gguf", b"zzz")

        class _Stub:
            def repo_meta(self, repo_id):
                return {"tags": ["llm", "gguf"]}
        with patch.object(main_app, "_cfg", return_value=self.cfg), \
             patch.object(main_app, "get_provider", return_value=_Stub()):
            data = main_app.api_cache_group(folder="org/repo", repo="org/repo", name="Repo")
        self.assertTrue(data["ok"])
        self.assertEqual(data["repo_id"], "org/repo")

        manifest = os.path.join(self.tmp, "org/repo", ".modelmeta.json")
        self.assertTrue(os.path.exists(manifest))
        stored = _json.loads(Path(manifest).read_text(encoding="utf-8"))
        self.assertEqual(stored["repo_id"], "org/repo")
        self.assertEqual(stored["model_name"], "Repo")
        self.assertEqual(stored["tags"], ["llm", "gguf"])
        key = model_store.key_for(SOURCE, "org/repo")
        self.assertEqual(model_store.get(key).get("upstream_repo"), "org/repo")

        models = scan_source(SOURCE, self.tmp)
        cards = {m.path for m in models}
        self.assertIn("org/repo", cards)
        self.assertNotIn("org/repo/quant", cards)  # subfolder collapsed into the repo card
        card = next(m for m in models if m.path == "org/repo")
        self.assertEqual(card.kind, "tree")
        self.assertEqual(card.name, "Repo")
        self.assertEqual(len(card.files), 2)

    def test_route_group_404s_for_unknown_folder(self):
        with patch.object(main_app, "_cfg", return_value=self.cfg):
            resp = main_app.api_cache_group(folder="no/such/dir", repo="x/y", name="")
        self.assertEqual(resp.status_code, 404)

    def test_route_group_rejects_the_source_root_itself(self):
        # Grouping the whole source root would collapse every model into one card.
        _write(self.tmp, "a.gguf", b"x")
        with patch.object(main_app, "_cfg", return_value=self.cfg):
            resp = main_app.api_cache_group(folder=".", repo="x/y", name="")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
