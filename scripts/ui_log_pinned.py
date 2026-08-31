#!/usr/bin/env python3
"""Verify the /jobs log panel only auto-scrolls when already at the bottom.

Drives the LIVE app on 127.0.0.1:9999 with Playwright. A fake EventSource +
fetch interceptor (installed via addInitScript) injects a synthetic "running"
job and feeds controlled log lines, so no real download is needed.

Checks:
  1. freshly opened log starts at the bottom (latest line visible)
  2. scrolled up + new lines arrive  -> view stays put (the bug being fixed)
  3. scrolled to bottom + new lines  -> view follows the bottom
  4. scrolled up + 3s DOM rebuild    -> position restored, no yank

Run: uv run --frozen python scripts/ui_log_pinned.py
"""
from __future__ import annotations

import os

# Override any inherited value — the repo .pwb holds the real browser binaries.
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/home/openchamber/code/model_repo/.pwb"

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:9999"
JOB_ID = "hf_download-testpinned1"

INIT_JS = """
window.__esList = [];
const __payload = {jobs: [{
  id: %s, kind: 'hf_download', description: 'Pinned-scroll test job',
  status: 'running', progress: 42.0, log: [], result: {},
  created_at: Date.now() / 1000, started_at: Date.now() / 1000,
  finished_at: null, meta: {},
}]};
const __fetch = window.fetch.bind(window);
window.fetch = (url, opts) => {
  if (typeof url === 'string' && url === '/api/jobs') {
    return Promise.resolve(new Response(JSON.stringify(__payload),
      {status: 200, headers: {'Content-Type': 'application/json'}}));
  }
  return __fetch(url, opts);
};
class FakeES {
  constructor(url) { this.url = url; this.onmessage = null; this.onerror = null;
                     this.closed = false; window.__esList.push(this); }
  close() { this.closed = true; }
  emit(o) { if (!this.closed && this.onmessage) this.onmessage({data: JSON.stringify(o)}); }
}
window.EventSource = FakeES;
""" % ("'" + JOB_ID + "'",)

FEED_JS = """async (n) => {
  const es = window.__esList[0];
  if (!es) throw new Error('no fake EventSource created');
  const t0 = Date.now();
  for (let i = 0; i < n; i++) {
    es.emit({line: 'line ' + t0 + '-' + i + ' ' + 'x'.repeat(48)});
    await new Promise(r => setTimeout(r, 15));
  }
}"""

METRIC_JS = """() => {
  const pre = document.querySelector('[data-stream="%s"]');
  if (!pre) return null;
  return {top: pre.scrollTop, bottom: pre.scrollHeight, height: pre.clientHeight,
          atBottom: pre.scrollHeight - pre.scrollTop - pre.clientHeight};
}""" % (JOB_ID,)


def main() -> int:
    fails = []

    def check(name: str, ok: bool, detail: str) -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} — {detail}")
        if not ok:
            fails.append(name)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.on("pageerror", lambda e: print("PAGEERROR:", e))
        page.add_init_script(INIT_JS)
        page.goto(f"{BASE}/jobs", wait_until="domcontentloaded")
        page.wait_for_selector(f'[data-job="{JOB_ID}"]', timeout=8000)
        page.wait_for_selector(f'[data-stream="{JOB_ID}"]', state="attached", timeout=8000)
        page.wait_for_function("window.__esList.length > 0", timeout=8000)

        # Open the log panel (fresh open should start at the bottom).
        page.click(f'[data-job="{JOB_ID}"] button[onclick^="toggleLog"]')
        page.wait_for_timeout(100)

        print("feed 200 lines (panel overflows, should sit at bottom)")
        page.evaluate(FEED_JS, 200)
        m = page.evaluate(METRIC_JS)
        check("opens at bottom", m and m["atBottom"] < 5,
              f"atBottom gap={m['atBottom']:.1f}px")

        print("scroll to a mid position, feed 100 more lines (must stay put)")
        page.evaluate(f"""() => {{
            const pre = document.querySelector('[data-stream="{JOB_ID}"]');
            pre.scrollTop = 150;
        }}""")
        page.wait_for_timeout(80)  # let the scroll event update the pin state
        before = page.evaluate(METRIC_JS)
        page.evaluate(FEED_JS, 100)
        after = page.evaluate(METRIC_JS)
        check("stays put when scrolled up", abs(after["top"] - 150) <= 2,
              f"scrollTop {before['top']} -> {after['top']} "
              f"(bottom grew {before['bottom']} -> {after['bottom']})")

        print("scroll to bottom, feed 50 more lines (should follow)")
        page.evaluate(f"""() => {{
            const pre = document.querySelector('[data-stream="{JOB_ID}"]');
            pre.scrollTop = pre.scrollHeight;
        }}""")
        page.wait_for_timeout(80)
        page.evaluate(FEED_JS, 50)
        m = page.evaluate(METRIC_JS)
        check("follows bottom when pinned", m["atBottom"] < 5,
              f"atBottom gap={m['atBottom']:.1f}px")

        print("scroll to top-ish, wait past the 3s DOM rebuild (position kept)")
        page.evaluate(f"""() => {{
            const pre = document.querySelector('[data-stream="{JOB_ID}"]');
            pre.scrollTop = 300;
        }}""")
        page.wait_for_timeout(80)
        page.wait_for_timeout(3600)  # one full refresh() rebuild cycle
        m = page.evaluate(METRIC_JS)
        check("position survives DOM rebuild", m is not None and abs(m["top"] - 300) <= 2,
              f"scrollTop={m['top']} (expected ~300)")

        shot = "/tmp/log_pinned.png"
        page.screenshot(path=shot, full_page=True)
        print(f"screenshot: {shot}")
        browser.close()

    if fails:
        print(f"FAIL — {len(fails)} check(s) failed: {', '.join(fails)}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
