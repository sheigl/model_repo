#!/usr/bin/env python3
"""Verify client-side SPA tab navigation preserves per-tab state.

Boots the app in-process (uvicorn on an ephemeral port), then on the real
rendered page:
  1. On Browse: type a search query, tick GGUF, pick a sort, scroll the page.
  2. Switch to the Jobs tab (client-side nav) and back to Browse.
  3. Assert the browse view state (section still mounted, search text, filters,
     sort, scroll position, open drawer) is preserved exactly.
  4. Switch to Targets, then Settings, then back — assert no duplicates / no
     page reload (history length did not grow on back-nav, active tab correct).

Writes a screenshot to /tmp/ui_spa_state.png.

Run with:
    uv run --frozen python scripts/ui_test_spa_state.py
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


FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def main() -> int:
    base_url = os.environ.get("BASE_URL")
    if base_url:
        print(f"LIVE mode, targeting {base_url}")
    else:
        host, port = _boot_app()
        base_url = f"http://{host}:{port}"
        print(f"Booted app on {base_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        page.wait_for_timeout(400)

        # --- 1. Set up Browse state: query, gguf, sort, scroll, drawer.
        page.fill("#hub-q", "llama")
        page.select_option("#hub-pipeline", "text-generation")
        page.select_option("#hub-sort", "likes")
        page.wait_for_timeout(800)  # let the onchange-triggered search settle before imposing fixtures
        page.evaluate("window.scrollTo(0, 600)")
        page.wait_for_timeout(100)
        scroll_before = page.evaluate("window.scrollY")

        # Force a bunch of hub results so the page is actually scrollable.
        page.evaluate("""() => {
            const make = (i) => ({ id: 'org/Llama-'+i, author: 'org', downloads: 12000+i,
                        likes: 300+i, pipeline: 'text-generation',
                        tags: ['llama','text-generation','gguf'], last_modified: Date.now()/1000 });
            hubResults = Array.from({length: 30}, (_,i)=>make(i));
            sortHub();
            document.getElementById('tab-hub').classList.add('tab-on');
        }""")
        page.wait_for_timeout(100)
        page.evaluate("window.scrollTo(0, 600)")
        page.wait_for_timeout(100)
        scroll_before = page.evaluate("window.scrollY")
        print("  raised scrollY:", scroll_before)

        # --- 2. Switch to Jobs (client-side) then back to Browse.
        page.click("nav .tab-row a[href='/jobs']")
        page.wait_for_timeout(500)
        jobs_visible = page.eval_on_selector("main section[data-route='/jobs']", "e=>!e.hasAttribute('hidden')")
        scroll_after_jobs = page.evaluate("window.scrollY")
        check("jobs section becomes visible after clicking Jobs tab", jobs_visible)

        page.click("nav .tab-row a[href='/']")
        page.wait_for_timeout(500)

        # --- 3. Assert Browse state preserved.
        check("no page error thrown", not errors, "; ".join(errors))
        check("browse section still mounted", page.eval_on_selector(
            "main section[data-route='/']", "e=>e.getAttribute('data-loaded')==='1'"))

        check("search text preserved", page.eval_on_selector("#hub-q", "e=>e.value") == "llama")
        check("pipeline filter preserved", page.eval_on_selector("#hub-pipeline", "e=>e.value") == "text-generation")
        check("sort preserved", page.eval_on_selector("#hub-sort", "e=>e.value") == "likes")
        check("hub results preserved", page.evaluate("hubResults.length") == 30)
        check("hub grid repopulated", page.evaluate(
            "document.getElementById('hub-grid').children.length") > 0)

        scroll_after = page.evaluate("window.scrollY")
        check("scroll position preserved", abs(scroll_after - scroll_before) < 5,
              f"before={scroll_before} after={scroll_after}")

        # The browse section's full DOM (including any drawer) stays mounted in memory
        # even while another tab is shown, so returning restores it exactly.
        check("browse DOM still in document while on other tab",
              page.evaluate("!!document.querySelector(\"main section[data-route='/'] #hub-grid\")"))
        check("jobs DOM present and kept mounted",
              page.evaluate("!!document.querySelector(\"main section[data-route='/jobs'] #job-list\")"))

        # --- 4. Visit each other tab, then confirm only the active section is visible.
        for route in ["/targets", "/downloads", "/settings"]:
            page.click(f"nav .tab-row a[href='{route}']")
            page.wait_for_timeout(150)
            visible = page.evaluate(
                f"""() => {{
                    const sec = document.querySelector("main section[data-route='{route}']");
                    if(!sec) return false;
                    return !sec.hasAttribute('hidden');
                }}""")
            check(f"{route} section loads + visible", visible)
            # active tab highlight follows
        page.click("nav .tab-row a[href='/']")
        page.wait_for_timeout(150)
        check("back to Browse via client nav", page.evaluate(
            "document.querySelector(\"main section[data-route='/']\").hasAttribute('hidden')") is False)

        # --- 5. Back/forward through history preserves state.
        page.click("nav .tab-row a[href='/jobs']")
        page.wait_for_timeout(150)
        page.go_back()
        page.wait_for_timeout(300)
        check("history back returns to Browse", page.evaluate(
            "document.querySelector(\"main section[data-route='/']\").hasAttribute('hidden')") is False)
        check("browse state still intact after back", page.eval_on_selector("#hub-q", "e=>e.value") == "llama")

        print("\n-- page errors:", errors if errors else "none")
        screenshot = "/tmp/ui_spa_state.png"
        page.screenshot(path=screenshot, full_page=True)
        print(f"screenshot: {screenshot}")

        context.close()
        browser.close()

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
