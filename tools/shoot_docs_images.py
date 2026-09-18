"""retakes every screenshot in docs/images against the DEMO board.

the published images are the readme's and the github page's, so they must never show a real card.
this drives the demo board only, which is why it takes the demo server's port rather than defaulting
to 8000 - pointing it at the operator's own board is then a deliberate act, not an accident.

run it in two terminals, or background the first:

    uv run smortboard --demo --port 8721 --no-browser
    uv run python tools/shoot_docs_images.py docs/images --port 8721 --key <key from the printed link>

each image keeps the name and the pixel size the readme already references - the viewport per shot
is what fixes the size, so do not change one without the other.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

# jpeg quality: the published files sit around 100-160 KB at this setting
QUALITY = 82

# the two viewports the published set is shot at - everything but the open card and the state strip
BOARD_VIEWPORT = {"width": 1600, "height": 1000}
CARD_VIEWPORT = {"width": 1440, "height": 860}

# card-states.jpg is a band cut across the columns rather than a whole screen
STATES_STRIP = {"width": 1043, "height": 120}


def settle(page: Page, ms: int = 1200) -> None:
    page.wait_for_timeout(ms)


def shoot(page: Page, out: Path, name: str, clip: dict | None = None) -> None:
    page.screenshot(path=str(out / name), type="jpeg", quality=QUALITY, clip=clip)
    print("wrote", out / name)


def focus_card(page: Page, card_id: str) -> None:
    """focus(), never click(): in a fanned column the card above overlaps and swallows the click"""
    found = page.evaluate(
        """(id) => {
          const el = document.querySelector(`.row.card[data-card-id="${id}"]`);
          if (!el) return false;
          el.tabIndex = 0;
          el.focus();
          return true;
        }""",
        card_id,
    )
    if not found:
        raise SystemExit(f"card {card_id} is not drawn")
    settle(page, 900)


def focus_first_card(page: Page, column: str) -> None:
    found = page.evaluate(
        """(column) => {
          const el = document.querySelector(`.bucket[data-status="${column}"] .row.card`);
          if (!el) return false;
          el.tabIndex = 0;
          el.focus();
          return true;
        }""",
        column,
    )
    if not found:
        raise SystemExit(f"no card in the {column} column")
    settle(page, 900)


def dressed_card_id(page: Page) -> str:
    """the demo's fully dressed card - the one carrying the attachment, which is also the one with
    a dependency, both comments and three attempts behind it"""
    return page.evaluate(
        """async () => {
          const boards = await (await fetch('/api/boards')).json();
          const body = await (await fetch(`/api/boards/${boards[0].id}/cards`)).json();
          const cards = body.cards || body;
          return (cards.find(c => (c.attachments || []).length) || {}).id;
        }"""
    )


def shoot_board_set(page: Page, out: Path) -> None:
    settle(page, 2500)
    page.keyboard.press("Digit1")
    settle(page)
    shoot(page, out, "hero-board.jpg")

    # the state strip: one band across the columns, starting at the first card's top edge
    band = page.evaluate(
        """() => {
          const bucket = document.querySelector('.bucket[data-status="doing"]');
          return {x: bucket.getBoundingClientRect().x,
                  y: bucket.querySelector('.row').getBoundingClientRect().y};
        }"""
    )
    shoot(page, out, "card-states.jpg", clip={**band, **STATES_STRIP})

    dressed = dressed_card_id(page)
    focus_card(page, dressed)
    page.keyboard.press("Space")
    settle(page, 1600)
    shoot(page, out, "hero-card.jpg")
    page.keyboard.press("Escape")
    settle(page, 900)

    # both drawers at once, then mission control alone, then workforce alone
    focus_first_card(page, "doing")
    page.keyboard.press("Comma")
    settle(page, 900)
    page.keyboard.press("Period")
    settle(page, 1400)
    shoot(page, out, "hero-agents.jpg")
    page.keyboard.press("Comma")
    settle(page, 700)
    shoot(page, out, "mission-control.jpg")
    page.keyboard.press("Period")
    settle(page, 700)

    focus_first_card(page, "doing")
    page.keyboard.press("Comma")
    settle(page, 1400)
    shoot(page, out, "workforce.jpg")
    page.keyboard.press("Comma")
    settle(page, 700)

    for key, name in (
        ("KeyN", "inbox.jpg"),
        ("KeyD", "digest.jpg"),
        ("KeyC", "costs.jpg"),
        ("KeyU", "usage.jpg"),
    ):
        page.keyboard.press(key)
        settle(page, 1500)
        shoot(page, out, name)
        page.keyboard.press("Escape")
        settle(page, 800)

    shoot_settings_groups(page, out)

    # card cost and replay are both per attempt, so they need the card with a history
    for key, name in (("KeyI", "telemetry.jpg"), ("KeyT", "replay.jpg")):
        focus_card(page, dressed)
        page.keyboard.press(key)
        settle(page, 1800)
        shoot(page, out, name)
        page.keyboard.press("Escape")
        settle(page, 800)


# the settings panel is one scrolling column of three groups, and it has grown too tall to read as
# one image - so it is shot as three, each scrolled to its own group and clipped to the panel
SETTINGS_GROUPS = (
    ("general", "settings-general.jpg"),
    ("labs", "settings-labs.jpg"),
)


def shoot_settings_groups(page: Page, out: Path) -> None:
    # blur whatever holds focus first: a key binding is dead while a field has it, so pressing `o`
    # straight after a panel with an input silently does nothing
    page.evaluate("() => document.activeElement && document.activeElement.blur()")
    settle(page, 400)
    page.keyboard.press("KeyO")
    page.wait_for_selector(".settings-panel", timeout=8000)
    settle(page, 1500)
    for group, name in SETTINGS_GROUPS:
        # scrollIntoView on the group, not a pixel offset: the sections above it change height as
        # labs and boards are added, and a fixed offset would drift with them
        page.evaluate(
            """(group) => {
              const el = document.querySelector(`.settings-group[data-group="${group}"]`);
              if (el) el.scrollIntoView({block: 'start'});
            }""",
            group,
        )
        settle(page, 700)
        # clip to the group itself, clamped to what the panel actually shows: clipping to the whole
        # panel caught the tail of the section above and the head of the one below, which is what
        # makes a set of three read as three arbitrary crops rather than three things
        box = page.evaluate(
            """(group) => {
              const panel = document.querySelector('.settings-panel');
              const el = document.querySelector(`.settings-group[data-group="${group}"]`);
              const p = panel.getBoundingClientRect();
              const g = el.getBoundingClientRect();
              const top = Math.max(p.y + 1, g.y - 6);
              const bottom = Math.min(p.y + p.height - 1, g.y + g.height + 6);
              return {x: Math.round(p.x + 1), y: Math.round(top),
                      width: Math.round(p.width - 2),
                      height: Math.round(Math.max(bottom - top, 80))};
            }""",
            group,
        )
        shoot(page, out, name, clip=box)

    # cost control is two buttons that open their own lists, so the group itself shows nothing worth
    # a picture - open the per-run caps and shoot that instead
    page.evaluate(
        """() => {
          const wanted = 'spend caps per run';
          const button = [...document.querySelectorAll('.settings-panel button, .settings-panel .toggle')]
            .find(el => el.textContent.trim() === wanted);
          if (button) button.click();
        }"""
    )
    settle(page, 1200)
    caps = page.evaluate(
        """() => {
          const panel = document.querySelector('.menu-panel') || document.querySelector('.settings-panel');
          const r = panel.getBoundingClientRect();
          return {x: Math.round(r.x), y: Math.round(r.y),
                  width: Math.round(r.width), height: Math.round(r.height)};
        }"""
    )
    shoot(page, out, "settings-cost.jpg", clip=caps)
    page.keyboard.press("Escape")
    settle(page, 500)
    page.keyboard.press("Escape")
    settle(page, 800)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="directory to write the images into")
    parser.add_argument("--port", type=int, required=True, help="the demo server's port")
    parser.add_argument("--key", required=True, help="the api key from the demo's printed link")
    args = parser.parse_args()
    # each page is its own browser context, so each one swaps the key for its own cookie
    url = f"http://127.0.0.1:{args.port}/ui/index.html?key={args.key}"
    args.out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport=BOARD_VIEWPORT)
            page.goto(url)
            shoot_board_set(page, args.out)
            page.close()

            page = browser.new_page(viewport=CARD_VIEWPORT)
            page.goto(url)
            settle(page, 2500)
            page.keyboard.press("Digit1")
            settle(page)
            focus_card(page, dressed_card_id(page))
            page.keyboard.press("Space")
            settle(page, 1800)
            shoot(page, args.out, "card.jpg")
            page.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
