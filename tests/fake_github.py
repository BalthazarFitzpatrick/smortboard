"""a stand-in for github's side of `gh pr merge`, against a test's bare origin.

the board lands a card by merging its pull request (review/integrate.py). there is no github in a
test, so this does what github would: refuse when the head moved past --match-head-commit, else a
real two-parent merge of the pull request's head into its base, pushed to the bare origin. every
other gh call behaves as if gh answered nothing.
"""

import subprocess
import tempfile
from pathlib import Path


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


class FakeGithub:
    """`prs` maps a pull request url to its base branch; `merges` records each merge asked for"""

    def __init__(self, origin: Path, prs: dict[str, str]):
        self.origin = Path(origin)
        self.prs = prs
        self.merges: list[list[str]] = []
        self.refuse: str | None = None

    def __call__(self, args, cwd):
        if list(args[:2]) != ["pr", "merge"]:
            return subprocess.CompletedProcess(args, 1, "", "no github in tests")
        self.merges.append(list(args))
        if self.refuse:
            return subprocess.CompletedProcess(args, 1, "", self.refuse)
        url = args[2]
        head = args[args.index("--match-head-commit") + 1]
        subject = args[args.index("--subject") + 1]
        base = self.prs[url]
        branches = _git(
            self.origin, "for-each-ref", "--format=%(refname:short)", "--points-at", head
        ).stdout.split()
        if not [name for name in branches if name != base]:
            return subprocess.CompletedProcess(
                args, 1, "", "Head branch was modified. Review and try the merge again."
            )
        with tempfile.TemporaryDirectory() as scratch:
            clone = Path(scratch) / "merge"
            subprocess.run(
                ["git", "clone", "-q", "-b", base, str(self.origin), str(clone)],
                check=True,
                capture_output=True,
            )
            _git(clone, "config", "user.email", "github@test")
            _git(clone, "config", "user.name", "github")
            merged = _git(clone, "merge", "--no-ff", "-m", subject, head, check=False)
            if merged.returncode != 0:
                return subprocess.CompletedProcess(args, 1, "", "Pull request is not mergeable")
            _git(clone, "push", "-q", "origin", base)
        return subprocess.CompletedProcess(args, 0, "", "")


def install(monkeypatch, origin, prs):
    """routes review/integrate.py's gh through a FakeGithub over `origin` and returns it"""
    fake = FakeGithub(origin, prs)
    monkeypatch.setattr("smortboard.review.integrate._gh", fake)
    return fake
