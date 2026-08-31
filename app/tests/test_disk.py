"""Tests for disk-space gauges: remote df parsing + per-filesystem grouping.

``_refresh_disk`` pulls usage per category path on a target, then de-duplicates
into one gauge per *filesystem* (many categories often share a disk). These tests
exercise that grouping with fake disk sources — no SSH/network required.
"""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app.main as m
import app.sync as s


def _fake_cfg():
    return SimpleNamespace(
        registry=SimpleNamespace(repo_root="/cache/models"),
        targets={
            "ai.home": SimpleNamespace(host="h1", user="u", ssh_key=None, categories={
                "llm": SimpleNamespace(remote_root="/mnt/1TB/AI/chat_models"),
                "vae": SimpleNamespace(remote_root="/mnt/1TB/AI/ComfyUI/models/vae"),
            }),
            # No categories -> no gauges.
            "empty": SimpleNamespace(host="h3", user="u", ssh_key=None, categories={}),
        },
    )


def _fake_remote(host, user, path, key=None, timeout=20):
    if host == "h1":
        # Two different category paths on the SAME mount -> collapse to one gauge.
        return {"total_bytes": 1000, "used_bytes": 600, "free_bytes": 400,
                "percent": 60, "mounted": "/mnt/1TB/AI"}
    return None


def _fake_local(path):
    return {"total_bytes": 500, "used_bytes": 250, "free_bytes": 250, "percent": 50}


class RemoteDiskUsageParseTests(unittest.TestCase):
    def test_parses_posix_df_and_captures_mount(self):
        out = ("Filesystem 1024-blocks Used Available Capacity Mounted on\n"
               "/dev/sda1 100000000 65000000 35000000 65% /srv/models\n")
        with patch.object(s.subprocess, "run",
                          return_value=SimpleNamespace(returncode=0, stdout=out, stderr="")):
            d = s.remote_disk_usage("h", "u", "/srv/models")
        self.assertEqual(d["total_bytes"], 100000000 * 1024)
        self.assertEqual(d["percent"], 65)
        self.assertEqual(d["mounted"], "/srv/models")

    def test_failure_returns_none(self):
        with patch.object(s.subprocess, "run", side_effect=subprocess.TimeoutExpired("ssh", 5)):
            self.assertIsNone(s.remote_disk_usage("h", "u", "/srv/models"))
        with patch.object(s.subprocess, "run",
                          return_value=SimpleNamespace(returncode=1, stdout="", stderr="x")):
            self.assertIsNone(s.remote_disk_usage("h", "u", "/srv/models"))


class RefreshDiskGroupingTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch.object(m, "_cfg", side_effect=_fake_cfg),
            patch.object(m, "remote_disk_usage", side_effect=_fake_remote),
            patch.object(m, "local_disk_usage", side_effect=_fake_local),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_collapses_categories_on_same_filesystem(self):
        m._refresh_disk()
        fss = m._disk_cache["targets"]["ai.home"]["filesystems"]
        self.assertEqual(len(fss), 1)
        self.assertEqual(sorted(fss[0]["categories"]), ["llm", "vae"])
        self.assertEqual(fss[0]["free"], 400)
        self.assertEqual(fss[0]["mounted"], "/mnt/1TB/AI")

    def test_empty_target_has_no_filesystems(self):
        m._refresh_disk()
        self.assertEqual(m._disk_cache["targets"]["empty"]["filesystems"], [])

    def test_local_usage_present(self):
        m._refresh_disk()
        self.assertEqual(m._disk_cache["local"]["pct"], 50)


if __name__ == "__main__":
    unittest.main()
