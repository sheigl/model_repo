#!/usr/bin/env python3
"""Diagnostic: verify the hub drawer renders Files/quantizations as a table with sizes.

Boots the app in-process on an ephemeral port, opens the index at phone width, runs a
hub search, opens the first repo's detail drawer, waits for the file table to populate,
then reads each row's filename + size from the DOM and asserts sizes are non-empty. A
screenshot is written to /tmp/ui_files.png for visual verification (vision is unreliable
in this session). Run with:

    uv run --frozen python scripts/ui_files_sizes.py
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      "/home/openchamber/code/model_repo/.pwb")

from playwright.sync_api import sync_playwright  # noqa: E402
import uvicorn  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _boot_app() -> tuple[str, int]:
    from app.main import app
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    def _serve():
        asyncio.run(server.serve())

    threading.Thread(target=_serve, daemon=True).start()
    for _ in range(300):
        if getattr(server, "started", False):
            break
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("app did not start in time")
    return "127.0.0.1", port


def main() -> int:
    host, port = _boot_app()
    base_url = f"http://{host}:{port}"
    print(f"Booted app on {base_url}; checking hub drawer file table")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            device_scale_factor=1,
            viewport={"width": 390, "height": 844},
            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
                        "Mobile/15E148 Safari/604.1"),
        )
        page = context.new_page()
        page.on("pageerror", lambda e: print("PAGEERROR:", e))
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        # Run a hub search, then open repos until one has GGUF files with sizes.
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

        if found is None:
            print("FAIL — no searched repo returned a file table with sizes")
            page.screenshot(path="/tmp/ui_files.png", full_page=True)
            context.close()
            browser.close()
            return 1

        rows = found["rows"]
        print("\n-- file table for", found["title"], "--")
        for r in rows:
            print(f"  | {r['size']:>10} | {r['name']}")

        thead = page.evaluate("""() => {
            const ths = document.querySelectorAll('#panel-files table thead th');
            return Array.from(ths).map(t => t.textContent.trim());
        }""")

        no_dash = all(r["size"] and r["size"] not in ("—", "") for r in rows)
        print(f"\nrows: {len(rows)} | header cols: {thead}")

        ok = no_dash
        print("RESULT:", "PASS — file sizes rendered" if ok else "FAIL — missing/empty sizes")

        shot = "/tmp/ui_files.png"
        page.screenshot(path=shot, full_page=True)
        context.close()
        browser.close()

    print(f"screenshot: {shot}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
