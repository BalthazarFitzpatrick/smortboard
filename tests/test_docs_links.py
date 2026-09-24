"""every relative link and image in README.md and docs/ lands on a real file, and every #anchor on a
heading github generates for it - a reader clicking through on github is the only check they get"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]

FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")
MD_LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HTML_SRC = re.compile(r"""(?:src|srcset|href)="([^"]+)\"""")


def github_slug(heading: str) -> str:
    """github's anchor for a heading: lowercase, markup and punctuation dropped, spaces to hyphens"""
    text = re.sub(r"`|\*\*|__", "", heading.strip().lower())
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(page: Path) -> set[str]:
    seen: dict[str, int] = {}
    found = set()
    for line in FENCE.sub("", page.read_text()).splitlines():
        match = re.match(r"^#{1,6}\s+(.*)$", line)
        if not match:
            continue
        slug = github_slug(match.group(1))
        # a repeated heading gets -1, -2 ... on github
        count = seen.get(slug, 0)
        found.add(slug if count == 0 else f"{slug}-{count}")
        seen[slug] = count + 1
    return found


def links(page: Path) -> list[str]:
    text = INLINE_CODE.sub("", FENCE.sub("", page.read_text()))
    return MD_LINK.findall(text) + HTML_SRC.findall(text)


def relative(target: str) -> bool:
    return not re.match(r"^[a-z][a-z0-9+.-]*:", target) and not target.startswith("//")


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relative_link_and_anchor_resolves(page):
    broken = []
    for target in filter(relative, links(page)):
        path, _, fragment = target.partition("#")
        dest = (page.parent / path).resolve() if path else page
        if not dest.exists():
            broken.append(f"{target}: no such file")
        elif fragment and dest.suffix == ".md" and fragment not in anchors(dest):
            broken.append(f"{target}: no heading for #{fragment}")
    assert broken == []


def test_the_slug_matches_github_for_the_shapes_the_docs_use():
    assert github_slug("3. Give cards a credential") == "3-give-cards-a-credential"
    assert github_slug('Statuses, and why there is no "blocked"') == (
        "statuses-and-why-there-is-no-blocked"
    )
    assert github_slug("A card stopped on `LEASE_CONFLICT`") == "a-card-stopped-on-lease_conflict"
