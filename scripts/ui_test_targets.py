#!/usr/bin/env python3
"""Diagnostic: detect unwanted horizontal/vertical overflow on /targets at phone width.

Boots the app in-process (uvicorn on an ephemeral port), opens /targets on an iPhone
viewport, measures scrollWidth vs clientWidth of html/body and every card + category row,
and writes a screenshot to /tmp/ui_targets.png for visual verification. Run with:

    uv run --frozen python scripts/ui_test_targets.py
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

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
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
    path = os.environ.get("TARGET_PATH", "/targets")
    print(f"Booted app on {base_url}; checking {path}")

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
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        page.wait_for_timeout(500)

        report = page.evaluate("""() => {
            const out = [];
            for (const el of [document.documentElement, document.body]) {
                if (!el) continue;
                out.push({ what: el.tagName + '(root/body)',
                    cw: el.clientWidth, sw: el.scrollWidth,
                    overflow: getComputedStyle(el).overflowX });
            }
            const cards = document.querySelectorAll('.card');
            for (const c of cards) {
                const r = c.getBoundingClientRect();
                out.push({ what: 'card', x: Math.round(r.x), w: Math.round(r.width), sw: c.scrollWidth, cw: c.clientWidth });
                for (const row of c.querySelectorAll('.flex')) {
                    if (row.scrollWidth > row.clientWidth) {
                        out.push({ what: 'overflow-row', sw: row.scrollWidth, cw: row.clientWidth, gap: getComputedStyle(row).gap });
                    }
                }
            }
            return out;
        }""")

        print("\n-- overflow report --")
        for row in report:
            print(" ", row)

        h_overflow = [r for r in report if "sw" in r and r["sw"] > r["cw"]]
        xroot = [r for r in report if r.get("what") in ("HTML(root)", "BODY")]
        print("\nhorizontal overflow (scrollWidth>clientWidth):", len(h_overflow))
        for r in h_overflow:
            print("  OVERFLOW:", r)

        screenshot_path = "/tmp/ui_targets.png"
        page.screenshot(path=screenshot_path, full_page=True)
        context.close()
        browser.close()

    print(f"screenshot: {screenshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
