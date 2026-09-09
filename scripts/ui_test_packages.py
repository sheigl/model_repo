#!/usr/bin/env python3
"""Proof: drive the LIVE app on 127.0.0.1:9999 with Playwright and exercise the
Packages page end-to-end — open editor, browse a real repo's files, pick one,
save the package, verify the card + status render, dry-run it (jumps to Jobs),
then delete it via the UI. Screenshots land in /tmp.
Run: uv run --frozen python scripts/ui_test_packages.py
"""
from __future__ import annotations

import os
import time
import urllib.request

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      "/home/sheigl/code/model_repo/.pwb")

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:9999"
REPO = "Qwen/Qwen3-0.6B-GGUF"
PICK = "params"
NAME = f"UI smoke {time.strftime('%H%M%S')} (safe to delete)"


def _cleanup_stale() -> None:
    """Remove leftovers from earlier (crashed) runs so names never collide."""
    try:
        data = urllib.request.urlopen(f"{BASE}/api/packages", timeout=10).read()
    except Exception:
        return
    for p in __import__("json").loads(data).get("packages", []):
        if p["name"].startswith("UI smoke"):
            urllib.request.urlopen(urllib.request.Request(
                f"{BASE}/api/packages/{p['id']}/delete", method="POST"), timeout=10)


def main() -> int:
    _cleanup_stale()
    failures: list[str] = []
    page_errors: list[str] = []
    console_errors: list[str] = []

    def check(cond: bool, what: str) -> None:
        print(("ok  " if cond else "FAIL") + f" — {what}")
        if not cond:
            failures.append(what)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("dialog", lambda d: d.accept())

        # 1 — page loads, empty state
        page.goto(f"{BASE}/packages", wait_until="domcontentloaded")
        page.wait_for_selector("#pkg-empty, #pkg-list .card", state="visible", timeout=20000)
        check(True, "GET /packages loads and renders")
        check(bool(page.get_by_role("link", name="Packages").count()), "nav has Packages tab")
        page.screenshot(path="/tmp/packages_ui_1_list.png", full_page=True)

        # 2 — open editor
        page.click("#new-package")
        page.wait_for_selector("#pkg-editor:not(.hidden)", state="visible")
        check(True, "New package opens the editor")
        page.fill("#pkg-name", NAME)

        # 3 — fill repo + browse files
        page.fill("#comp-rows .comp-repo", REPO)
        page.click("#comp-rows [data-browse]")
        page.wait_for_selector("#picker:not(.hidden)", state="visible")
        page.wait_for_selector(f"#picker-files label:has-text('{PICK}')", state="visible",
                               timeout=30000)
        check(True, f"file picker lists files from {REPO}")
        page.screenshot(path="/tmp/packages_ui_2_picker.png", full_page=True)

        # 4 — pick one file, add it
        page.locator(f"#picker-files label:has-text('{PICK}') input.pk-file").check()
        page.click("#picker-add")
        page.wait_for_selector("#picker", state="hidden")
        page.wait_for_selector("#comp-rows .comp-chip", state="visible")
        check(page.locator("#comp-rows .comp-chip", has_text=PICK).count() == 1,
              f"picked file '{PICK}' shows as a chip")

        # 5 — save
        page.click("#pkg-save")
        page.wait_for_selector("#pkg-editor", state="hidden")
        page.wait_for_function(
            f"() => Array.from(document.querySelectorAll('#pkg-list .card'))"
            f".some(c => c.textContent.includes('{NAME}'))", timeout=15000)
        card = page.locator("#pkg-list .card", has_text=NAME)
        check(True, f"package '{NAME}' saved and card rendered")
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll('[id^=status-]'))"
            ".some(b => b.textContent.includes('cached'))", timeout=30000)
        check(True, "per-file status line rendered (cached/missing counts)")
        page.screenshot(path="/tmp/packages_ui_3_saved.png", full_page=True)

        # 6 — dry run jumps to Jobs
        card.locator("button", has_text="Dry run").click()
        page.wait_for_function("() => location.pathname === '/jobs'", timeout=15000)
        page.wait_for_timeout(1500)
        check("/jobs" in page.url, "dry run queues a job and navigates to /jobs")
        page.screenshot(path="/tmp/packages_ui_4_jobs.png", full_page=True)

        # 7 — delete via UI
        page.get_by_role("link", name="Packages").click()
        my_card = page.locator("#pkg-list .card", has_text=NAME)
        page.wait_for_selector("#pkg-list .card", state="visible", timeout=15000)
        my_card.locator("button", has_text="Delete").click()
        my_card.wait_for(state="detached", timeout=15000)
        check(True, "delete removes the card")
        page.screenshot(path="/tmp/packages_ui_5_after_delete.png", full_page=True)

        browser.close()

    print("\nconsole errors:", console_errors or "none")
    print("page errors:", page_errors or "none")
    failures.extend(page_errors)
    if failures:
        print(f"RESULT: FAIL ({len(failures)})")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
