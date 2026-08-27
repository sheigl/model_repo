#!/usr/bin/env python3
"""Proof: drive the LIVE app on 127.0.0.1:9999 with Playwright, open a repo drawer,
and screenshot the Files/quantizations table (with sizes). Also dumps DOM rows as proof.
Run: uv run --frozen python scripts/ui_live_proof.py
"""
from __future__ import annotations

import os

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      "/home/openchamber/code/model_repo/.pwb")

from playwright.sync_api import sync_playwright  # noqa: E402


def main() -> int:
    base = "http://127.0.0.1:9999"
    print(f"D driving live app at {base}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.on("pageerror", lambda e: print("PAGEERROR:", e))
        page.goto(f"{base}/", wait_until="domcontentloaded")
        page.wait_for_timeout(400)

        page.fill("#hub-q", "qwen")
        page.get_by_role("button", name="Search").click()
        page.wait_for_selector("#hub-grid .card", state="visible", timeout=45000)

        found = None
        cards = page.query_selector_all("#hub-grid .card")
        for i, card in enumerate(cards[:8]):
            if i > 0:
                page.evaluate("() => closeDrawer()")
                page.wait_for_timeout(150)
            card.click()
            try:
                page.wait_for_selector("#dw-files tr", state="attached", timeout=30000)
            except Exception:
                continue
            page.wait_for_timeout(200)
            data = page.evaluate("""() => {
                const tb = document.getElementById('dw-files');
                return {
                    title: document.getElementById('dw-title').textContent,
                    rows: Array.from(tb.querySelectorAll('tr')).map(r => ({
                        name: (r.children[1] || {}).textContent || '',
                        size: (r.children[2] || {}).textContent || '',
                    })),
                };
            }""")
            sizes = [r for r in data["rows"] if r["size"] and r["size"] not in ("—", "")]
            if len(data["rows"]) > 1 and sizes:
                found = data
                break

        shot = "/tmp/ui_live_proof.png"
        if found is None:
            page.screenshot(path=shot, full_page=True)
            print("FAIL — no repo returned a file table with sizes")
            context = page.context
            browser.close()
            return 1

        for r in found["rows"][:6]:
            print(f"  | {r['size']:>10} | {r['name']}")
        print(f"  ... total rows: {len(found['rows'])}")

        page.screenshot(path=shot, full_page=True)
        context = page.context
        browser.close()

    print(f"screenshot: {shot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
