"""flow_ui's render wait against a real browser, on a page that re-renders its video grid
all the time like Flow does (the cause of "Element is not attached to the DOM")."""
import os
import time

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from filmmaker import flow_ui  # noqa: E402

UUID = "0123abcd-0000-4000-8000-0123456789ab"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 60_000

PAGE = """
<style>.cdk-overlay-container [role=menu] { display: block }</style>
<cdk-virtual-scroll-viewport id="grid"></cdk-virtual-scroll-viewport>
<div class="cdk-overlay-container"></div>
<script>
  const READY = %(ready)s;
  function render() {  // a fresh node every time, like Flow's virtual grid
    document.getElementById('grid').innerHTML = `
      <flow-grid-tile-container aria-label="New clip"><flow-video-tile>
        ${READY ? '<button aria-label="More options" onclick="openMenu()">…</button>' : ''}
      </flow-video-tile></flow-grid-tile-container>
      <flow-grid-tile-container aria-label="Old clip"><flow-video-tile></flow-video-tile></flow-grid-tile-container>`;
  }
  render(); setInterval(render, 40);
  function openMenu() {
    document.querySelector('.cdk-overlay-container').innerHTML = `<div role="menu">
      <div role="menuitem" onclick="this.parentNode.insertAdjacentHTML('beforeend',
        '<div role=menuitem onclick=grab()>Original size</div>')">Download</div></div>`;
  }
  function grab() { fetch('https://flow-content.google/video/%(uuid)s'); }
</script>
"""


@pytest.fixture
def page():
    with playwright.sync_playwright() as pw:
        try:
            exe = os.environ.get("PW_CHROMIUM")  # optional: a specific Chromium binary
            browser = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        except Exception as exc:  # no browser installed
            pytest.skip(f"no Chromium: {exc}")
        pg = browser.new_page()
        pg.route("https://flow-content.google/**", lambda r: r.fulfill(status=200, body=MP4, content_type="video/mp4"))
        yield pg
        browser.close()


def _poll(page, out, seconds=6.0):
    """The same loop body run_i2v uses while it waits for the render."""
    last_try, end = 0.0, time.time() + seconds
    while time.time() < end:
        try:
            if flow_ui._poll_download(page, ["Old clip"], out, last_try, every=0.5):
                return True
        except flow_ui.PollTried:
            last_try = time.time()
        except Exception as exc:
            assert flow_ui._transient(exc), f"would have aborted the render wait: {exc}"
            flow_ui._close_menus(page)
        time.sleep(0.1)
    return False


def test_rerendering_grid_does_not_abort_and_clip_downloads(page, tmp_path):
    page.set_content(PAGE % {"ready": "true", "uuid": UUID})
    out = tmp_path / "s02.mp4"
    assert _poll(page, out)
    assert out.read_bytes() == MP4
    assert flow_ui.LAST_MEDIA["id"] == UUID


def test_clip_still_rendering_keeps_waiting(page, tmp_path):
    page.set_content(PAGE % {"ready": "false", "uuid": UUID})
    out = tmp_path / "s02.mp4"
    assert not _poll(page, out, seconds=2.5)
    assert not out.exists()


def test_old_clip_is_never_taken(page, tmp_path):
    page.set_content(PAGE % {"ready": "true", "uuid": UUID})
    assert flow_ui._new_tile(page, ["New clip", "Old clip"]) is None
    assert flow_ui._new_tile(page, ["Old clip"]) == "New clip"


# --- attaching an upload: never "the first item", only the one just uploaded -----------------------------------

ATTACH_PAGE = """
<flow-creative-agent-prompt-box>
  <button aria-label="Add ingredients to the prompt box" onclick="draw()">+</button>
  <div id="chips"></div>
</flow-creative-agent-prompt-box>
<div id="pop"></div>
<input type="file" id="file" style="display:none" onchange="uploaded(this.files[0].name)">
<script>
  // Like Flow (seen 2026-11-11): an "Uploading" placeholder on top, the new upload listed below it with a
  // spinner for PROCESS_MS, the last-used OLDER upload still selected (so "Add to prompt" is on before we pick
  // ours), selection shown by the class asset-item-active (aria-selected stays "false"), one selected at a time
  // (a click moves it, a click on the selected one keeps it), and a click on a processing upload does nothing.
  const PROCESS_MS = %(process_ms)d;
  let items = [{name: "s08.png", ready: true, on: %(preselected)s}];
  function draw() {
    const any = items.some(it => it.on);
    document.getElementById('pop').innerHTML = `<flow-add-menu-popover-content>
      <div role="tab">Uploads</div>
      <button onclick="document.getElementById('file').click()">Upload media</button>
      ${items.map((it, i) => `<button role="option" class="asset-item${it.on ? ' asset-item-active' : ''}"
          aria-selected="false" onclick="pick(${i})"><span class="asset-title">${it.name}</span>
          ${it.ready ? '' : '<mat-spinner></mat-spinner>'}</button>`).join('')}
      <button id="add" ${any ? '' : 'disabled'} onclick="addIt()">Add to prompt</button>
    </flow-add-menu-popover-content>`;
  }
  function pick(i) { if (!items[i].ready) return; items.forEach(it => it.on = false); items[i].on = true; draw(); }
  function uploaded(name) {
    items.unshift({name: "Uploading", ready: false, on: false}); draw();
    setTimeout(() => { items[0] = {name, ready: false, on: false}; items.unshift({name: "image", ready: true, on: false});
      draw(); setTimeout(() => { items[1].ready = true; draw(); }, PROCESS_MS); }, 500);
  }
  function addIt() {
    items.filter(it => it.on).forEach(it => document.getElementById('chips').insertAdjacentHTML('beforeend',
      `<flow-ingredient-chip>${it.name}</flow-ingredient-chip>`));
    document.getElementById('pop').innerHTML = '';
  }
</script>
"""


def test_attach_picks_only_the_new_upload_never_an_older_one(page, tmp_path):
    img = tmp_path / "s07.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    page.set_content(ATTACH_PAGE % {"process_ms": 6000, "preselected": "true"})
    flow_ui._attach(page, img, flow_ui.Deadline(120))
    chips = page.locator(flow_ui.CHIP).all_inner_texts()
    assert len(chips) == 1
    assert chips[0].startswith("fm-s07-") and chips[0].endswith(".png"), chips  # never "s08.png"
