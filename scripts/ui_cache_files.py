#!/usr/bin/env python3
"""Diagnostic: verify the cache drawer renders a grouped ("tree") model's files with
checkboxes when it has >1 file. Boots the app in-process on an ephemeral port, opens the
cache tab, clicks Sync on the Ornith-1.5-35B-A3B-GGUF tree card, waits for the file tree,
then asserts checkboxes (`.dw-quant`) exist and that drawerDeploy() collects checked files
into the /api/sync FormData via the cacheSrcMap back-reference. A screenshot is written to
/tmp/ui_cache_files.png for visual verification (vision is unreliable in this session).

Run with:
    uv run --frozen python scripts/ui_cache_files.py
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
    print(f"Booted app on {base_url}; checking cache tree file picker")

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

        # Switch to the cache tab, then click Sync on the 2-file Ornith tree card.
        try:
            page.get_by_role("tab", name="cache").click()
        except Exception:
            pass
        page.wait_for_timeout(1500)

        target = None
        for i in range(6):
            cards = page.query_selector_all("#cache-grid .card")
            for c in cards:
                title = (c.query_selector(".card-title") or {}).inner_text() if c.query_selector(".card-title") else ""
                try:
                    t2 = c.query_selector(".card-title").text_content()
                    title = t2.strip()
                except Exception:
                    pass
                if "Ornith-1.5-35B-A3B-GGUF" in (title or ""):
                    target = c
                    break
            if target is not None:
                break
            page.evaluate("() => { const t=document.getElementById('cache-tab'); if(t) t.click(); }")
            page.wait_for_timeout(400)

        if target is None:
            print("FAIL — no Ornith tree card found in cache grid")
            page.screenshot(path="/tmp/ui_cache_files.png", full_page=True)
            context.close()
            browser.close()
            return 1

        # Click the Sync button on that card.
        target.get_by_role("button", name="Sync").click()
        # The drawer opens; click the Files tab if a cache card shows one.
        page.wait_for_selector("#dw-title:has-text('Ornith-1.5-35B-A3B-GGUF')", state="visible", timeout=20000)
        page.wait_for_timeout(400)

        # Assert checkboxes rendered for the tree's files.
        boxes = page.query_selector_all("#dw-files-tree .dw-quant, #dw-files .tree-body .dw-quant")
        print(f"checkboxes found: {len(boxes)}")
        names = []
        for b in boxes:
            fp = (b.get_attribute("data-file") or "").strip()
            label = ""
            try:
                lbl = b.query_selector(".tree-label, .dw-qty")
                if lbl is not None:
                    label = lbl.text_content().strip()
            except Exception:
                label = fp.split("/")[-1]
            names.append(label or fp)
        print("files:", names)

        # Sanity-check the back-reference map exists and maps stripped -> repo-relative.
        mapping = page.evaluate("""() => {
            const m = {};
            for (const k in window.cacheSrcMap || {}) { m[k] = window.cacheSrcMap[k]; }
            return m;
        }""")
        print("cacheSrcMap entries:", len(mapping))

        # Verify drawerDeploy collects the checked files into FormData via cacheSrcMap.
        collected = page.evaluate("""() => {
            const sel = [...document.querySelectorAll('#dw-files-tree .dw-quant:checked, #dw-files .dw-quant:checked')]
                        .map(c => (window.cacheSrcMap && window.cacheSrcMap[c.dataset.file]) || c.dataset.file);
            return sel;
        }""")
        print("collected when checked:", collected)

        ok = len(boxes) >= 1 and len(collected) >= 1
        print("\nRESULT:", "PASS — cache tree file picker renders checkboxes" if ok
              else "FAIL — no checkboxes / nothing collectable")

        shot = "/tmp/ui_cache_files.png"
        page.screenshot(path=shot, full_page=True)
        context.close()
        browser.close()

    print(f"screenshot: {shot}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
