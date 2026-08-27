import os
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:9999"
REPO = "unsloth/Qwen3.8-27B-GGUF"


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1280, "height": 860})
        page.goto(URL)
        page.wait_for_function("typeof renderHubTree === 'function'")
        page.evaluate("""() => { if (typeof openDrawer === 'function') openDrawer(); switchDrawerTab('files'); }""")

        res = page.evaluate(
            """async () => {
              const d = await fetch('/api/hub/files?repo=' + encodeURIComponent(REPO_PLACEHOLDER))
                            .then(r => r.json());
              document.getElementById('dw-files').innerHTML = renderHubTree(d.files, d.sizes);
              let out;
              setTimeout(() => {
                const folderBoxes = [...document.querySelectorAll('[data-folder-select]')];
                const leafCount = document.querySelectorAll('#dw-files .dw-quant').length;
                const mmprojCount = document.querySelectorAll('#dw-files .dw-mmproj').length;
                const fb = folderBoxes[0];
                const fp = fb.dataset.folderSelect;
                const body = document.querySelector('.tree-body[data-for="' + fp + '"]');
                const leaves = [...body.querySelectorAll('.dw-quant')];
                const beforeChecked = leaves.filter(l => l.checked).length;
                // collapsed by default?
                const collapsedByDefault = body.classList.contains('hidden');
                // click caret -> expand
                document.querySelector('[data-caret="' + fp + '"]').click();
                const expandedAfterClick = !body.classList.contains('hidden');
                document.querySelector('[data-caret="' + fp + '"]').click();
                const collapsedAgain = body.classList.contains('hidden');
                out = {
                  filesReturned: d.files.length,
                  sizesPopulated: d.files.filter(f => d.sizes[f] != null).length,
                  folderCount: folderBoxes.length,
                  folderNames: folderBoxes.map(e => e.dataset.folderSelect),
                  leafCount, mmprojCount,
                  selFolder: fp,
                  folderLeavesBefore: leaves.length,
                  folderCheckedBefore: beforeChecked,
                  collapsedByDefault, expandedAfterClick, collapsedAgain,
                };
              }, 300);
              return new Promise(r => setTimeout(() => r(out), 600));
            }"""
            .replace("REPO_PLACEHOLDER", repr(REPO))
        )
        fp = res["folderNames"][0]
        page.locator('[data-folder-select="%s"]' % fp).click()
        afterChecked = page.evaluate(
            """() => {
              const body = document.querySelector('.tree-body[data-for="FOLDER_PLACEHOLDER"]');
              return [...body.querySelectorAll('.dw-quant')].filter(l => l.checked).length;
            }""".replace("FOLDER_PLACEHOLDER", fp)
        )
        page.screenshot(path="/tmp/ui_tree.png")
        b.close()

    print(res, "afterCheck=", afterChecked)
    assert res["filesReturned"] == 30, res["filesReturned"]
    assert res["sizesPopulated"] == 30, res["sizesPopulated"]
    assert res["leafCount"] + res["mmprojCount"] == res["filesReturned"], res
    assert res["folderCount"] >= 1, res
    assert res["folderCheckedBefore"] == 0, res
    assert afterChecked == res["folderLeavesBefore"] and afterChecked > 0, (afterChecked, res)
    assert res["collapsedByDefault"] is True and res["expandedAfterClick"] and res["collapsedAgain"], res
    print("TREE CHECK: PASS")


if __name__ == "__main__":
    main()
