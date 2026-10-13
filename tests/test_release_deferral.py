"""A deferred release is loud (DRE-5235, epic DRE-5234).

THE GAP THIS CLOSES. A surface script that exits 0 printing `deferred: …` is a
no-op, and a no-op concludes the job green. On 2026-09-22 Portico's `portals`
surface deferred seven times, every run green, and the CEO found out by asking
why a portal was behind. The surface script cannot file anything itself;
the train's process still holds the Linear key.

WHAT IT DOES. `scripts/release_deferral.py`, called from `_cmd_release` once
the decision is final:

  * a deferral with no re-arm minute prints a `Release deferred` warning, a
    `## Deferred` block on the summary page and one `release-deferred:` log
    line, then files ONE card per repo and surface in Green Light — or
    comments on the open one when the reason's fingerprint changed, or files
    nothing when an open `RELEASE ROLLED BACK:` / `RELEASE BROKEN:` card on the
    repo already speaks for it;
  * a verified tag closes the open card `Done` under `Released as <tag>`.

AND WHAT IT MAY NEVER DO. Change the decision, the receipt, the decision
record or the job's conclusion.

Run: python3 -m pytest tests/test_release_deferral.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import linear_ops  # noqa: E402
import release_decision  # noqa: E402
import release_deferral  # noqa: E402
import release_train  # noqa: E402

PORTICO = "dreadnought-foundry/portico"
RUN_URL = "https://github.com/dreadnought-foundry/portico/actions/runs/38415883166"
SHA = "3f2a1bc" + "0" * 33

#: The deferred line the epic's proof makes the train meet.
LINE = ("deferred: portals at 3f2a1bc — deployed and published, but the "
        "document half is unproven…")
REASON = LINE[len("deferred: "):]
#: The same reason at another commit: the same fingerprint.
LINE_NEXT_COMMIT = LINE.replace("3f2a1bc", "9c8d7e6")
#: Another reason.
LINE_OTHER = ("deferred: portals at 9c8d7e6 — the documents arm refused "
              "d-portico-brand-guide")

#: DRE-5464's two deferrals, which Portico's own train already files a card for.
ROLLED_BACK = ("deferred: dreadnought rolled-back at 3f2a1bc — fleet hold until "
               "the rollback card is closed")
HELD = "deferred: fleet hold — dreadnought rolled back at 3f2a1bc"

PREFIX = "portico: portals release deferred"


# --------------------------------------------------------------------------
# The fake Linear: the five reads and writes the module makes, and a ledger.
# --------------------------------------------------------------------------

class FakeLinear:
    """`linear_ops` as `release_deferral` reaches it: every call recorded."""

    window_nodes = staticmethod(linear_ops.window_nodes)
    TERMINAL = ("Done", "Canceled", "Duplicate")

    def __init__(self, *, fail: Exception | None = None):
        self.cards: list[dict] = []
        self.calls: list[tuple] = []
        self.created: list[dict] = []
        self.comments: list[tuple] = []
        self.moves: list[tuple] = []
        self.fail = fail
        self._next = 9000

    def add(self, title, *, repo="portico", state="Green Light",
            created="2026-10-10T12:00:00.000Z", description="", comments=()):
        self._next += 1
        card = {"identifier": f"DRE-{self._next}", "title": title,
                "repo": repo, "state": state, "createdAt": created,
                "description": description, "comments": list(comments)}
        self.cards.append(card)
        return card["identifier"]

    def card(self, ident):
        return next(c for c in self.cards if c["identifier"] == ident)

    def _raise(self):
        if self.fail is not None:
            raise self.fail

    def _node(self, card):
        return {"identifier": card["identifier"], "createdAt": card["createdAt"],
                "description": card["description"],
                "url": f"https://linear.app/x/issue/{card['identifier']}",
                # Linear answers newest first; window_nodes turns it round.
                "comments": {"pageInfo": {"hasNextPage": False},
                             "nodes": [{"body": b} for b in reversed(card["comments"])]}}

    def find_open_prefix(self, prefix):
        self.calls.append(("find_open_prefix", prefix))
        self._raise()
        found = sorted((c for c in self.cards
                        if c["title"].startswith(prefix)
                        and c["state"] not in self.TERMINAL),
                       key=lambda c: c["createdAt"])
        if not found:
            return None
        return {**self._node(found[0]),
                "duplicates": [self._node(c) for c in found[1:]]}

    def repo_cards_by_title_prefix(self, prefix, *, repo_slug, since):
        self.calls.append(("repo_cards_by_title_prefix", prefix, repo_slug, since))
        self._raise()
        return [{"identifier": c["identifier"], "description": c["description"],
                 "state": c["state"], "createdAt": c["createdAt"]}
                for c in self.cards
                if c["title"].startswith(prefix) and c["repo"] == repo_slug
                and datetime.fromisoformat(c["createdAt"].replace("Z", "+00:00")) >= since]

    def create_card(self, title, description, *, repo_slug, labels=(),
                    lane="Intake"):
        self.calls.append(("create_card", title))
        self._raise()
        ident = self.add(title, repo=repo_slug, state=lane,
                         created="2026-10-13T20:00:00.000Z",
                         description=description)
        self.created.append({"identifier": ident, "title": title,
                             "description": description, "repo_slug": repo_slug,
                             "labels": tuple(labels), "lane": lane})
        return {"identifier": ident, "url": f"https://linear.app/x/issue/{ident}"}

    def cmd_comment(self, ident, body, *flags):
        self.calls.append(("cmd_comment", ident))
        self._raise()
        self.comments.append((ident, body))
        self.card(ident)["comments"].append(body)
        return None

    def cmd_state(self, ident, state, *flags, **kw):
        self.calls.append(("cmd_state", ident, state))
        self._raise()
        self.moves.append((ident, state))
        self.card(ident)["state"] = state
        return True

    def writes(self):
        return [c for c in self.calls
                if c[0] in ("create_card", "cmd_comment", "cmd_state")]


@pytest.fixture
def fake(monkeypatch):
    linear = FakeLinear()
    monkeypatch.setattr(release_deferral, "linear_ops", linear)
    return linear


@pytest.fixture
def summary(tmp_path, monkeypatch):
    path = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", PORTICO)
    monkeypatch.setenv("GITHUB_RUN_ID", "38415883166")
    return path


def deferred(line, **over):
    return release_train.Decision(release_train.NO_OP, "deferred", line, **over)


def released(tag="portals/v1.4.0"):
    return release_train.Decision(release_train.RELEASE, "released",
                                  f"portals released at 3f2a1bc as {tag}",
                                  tag=tag)


def report(decision, *, repo=PORTICO, surface="portals", sha=SHA):
    """One lap through the module, its printed lines returned."""
    lines: list[str] = []
    release_deferral.report(decision, repo=repo, surface=surface, sha=sha,
                            warning=release_train._warning,
                            step_summary=release_train._step_summary,
                            out=lines.append)
    return lines


def tagged(lines):
    return [l for l in lines if l.startswith("release-deferred: ")]


def warnings(lines, title=None):
    out = [l for l in lines if l.startswith("::warning")]
    if title is not None:
        out = [l for l in out if l.startswith(f"::warning title={title}::")]
    return out


def blocks(path):
    if not Path(path).exists():
        return []
    return [b for b in Path(path).read_text().split("## ")[1:]
            if b.startswith("Deferred\n")]


# --------------------------------------------------------------------------
# 1. The deferred path is loud: a warning, a block, a log line.
# --------------------------------------------------------------------------

def test_a_deferral_warns_writes_the_block_and_prints_the_line(fake, summary):
    lines = report(deferred(LINE))

    assert warnings(lines, "Release deferred") == [
        f"::warning title=Release deferred::portals release deferred — {REASON}"]
    assert len(blocks(summary)) == 1
    block = blocks(summary)[0]
    for fact in ("portals", SHA, REASON, RUN_URL):
        assert fact in block, block
    assert tagged(lines)[0] == (
        f"release-deferred: portico portals at 3f2a1bc — {REASON} — {RUN_URL}")


def test_the_module_states_its_tag_and_title():
    assert release_deferral.TAG == "release-deferred"
    assert release_deferral.WARNING_TITLE == "Release deferred"
    assert release_deferral.DEFERRAL_LANE == "Green Light"
    assert release_deferral.CLOSED_LANE == "Done"


def test_the_strings_shared_with_the_train_are_the_trains():
    """No import of `release_train` (it imports this): the shared strings are
    bound here instead."""
    assert release_deferral.DEFERRAL_PREFIX == release_train.DEFERRAL_PREFIX
    assert release_deferral.DEFERRED_CODE == "deferred"
    assert release_deferral.RELEASE_ACT == release_train.RELEASE


# --------------------------------------------------------------------------
# 2. The decision, the receipt and the record are what they were.
# --------------------------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _caller(tmp_path, script):
    repo = tmp_path / "caller"
    (repo / "infra").mkdir(parents=True)
    (repo / "portals").mkdir()
    (repo / ".github" / "bureau").mkdir(parents=True)
    (repo / "portals" / "app.txt").write_text("v1\n")
    (repo / "infra" / "release-portals.sh").write_text(script)
    (repo / "infra" / "release-portals.sh").chmod(0o755)
    (repo / ".github" / "bureau" / "release.json").write_text(json.dumps({
        "surfaces": {"portals": {
            "tag_series": ["portals/v*"], "paths": ["portals/"],
            "script": "infra/release-portals.sh",
            "rollback": "make rollback-portals VERSION=<tag>",
            "spacing_minutes": 30, "window": "always", "auto": True,
            "identity": "portals-release", "record": "tag",
        }}}) + "\n")
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "train@example.com")
    _git(repo, "config", "user.name", "Release Train")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the caller")
    return repo


def _deferring(line):
    return f"#!/usr/bin/env bash\necho {json.dumps(line, ensure_ascii=False)}\n"


RELEASING = """#!/usr/bin/env bash
set -euo pipefail
git tag -a "portals/v1.4.0" -m "portals release of $RELEASE_SHA" "$RELEASE_SHA"
"""


def test_run_surface_still_returns_the_deferral_byte_for_byte(tmp_path):
    repo = _caller(tmp_path, _deferring(LINE))
    data = release_train.load(repo / ".github" / "bureau" / "release.json")
    entry = release_train.surfaces(data)["portals"]
    sha = _git(repo, "rev-parse", "HEAD")

    decision = release_train.run_surface(entry, repo_root=repo, sha=sha,
                                         out=lambda _l: None)

    assert decision == release_train.Decision(release_train.NO_OP, "deferred", LINE)
    assert decision.re_arm_at is None
    assert decision.receipt(PORTICO, "portals", sha) == (
        f"release-train: no-op {PORTICO} portals — {LINE}")
    record = release_decision.record(
        decision, repo=PORTICO, surface=entry, phase="release", sha=sha,
        head=sha, deployed=None, now=datetime(2026, 10, 13, 10, tzinfo=release_train.PT),
        env={})
    assert (record["act"], record["code"], record["reason"]) == (
        "no-op", "deferred", LINE)
    assert record["hand_act"].startswith(release_decision.DEFERRAL_PREFIX)


_GH_OK = """#!/usr/bin/env bash
cat > /dev/null
case "$2" in
  *statuses) echo '{}' ;;
  *) echo '{"id": 5235}' ;;
esac
"""


def _release_cli(repo, tmp_path, monkeypatch, capsys):
    binary = tmp_path / "bin"
    binary.mkdir(exist_ok=True)
    (binary / "gh").write_text(_GH_OK)
    (binary / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary}:{os.environ['PATH']}")
    monkeypatch.delenv(release_train.ENV_HOLD, raising=False)
    monkeypatch.setattr(release_train, "fetch_checks",
                        lambda _repo, _sha: release_train.read_checks(
                            [{"name": "ci", "status": "completed",
                              "conclusion": "success"}]))
    monkeypatch.setattr(release_train, "_now",
                        lambda: datetime(2026, 10, 13, 10, tzinfo=release_train.PT))
    # The publish and the Linear release are not this card's.
    monkeypatch.setattr(release_train.whats_new_release, "write",
                        lambda **_kw: {})
    monkeypatch.setattr(release_train.release_linear, "write",
                        lambda **_kw: None)
    code = release_train.main([
        "--repo", PORTICO, "--repo-root", str(repo),
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "release", "--sha", _git(repo, "rev-parse", "HEAD"),
        "--surface", "portals",
    ])
    return code, capsys.readouterr().out.splitlines()


def test_the_release_job_files_the_card_and_keeps_its_receipt_last(
        tmp_path, monkeypatch, capsys, fake, summary):
    repo = _caller(tmp_path, _deferring(LINE))
    code, lines = _release_cli(repo, tmp_path, monkeypatch, capsys)

    assert code == 0
    assert len(warnings(lines, "Release deferred")) == 1
    assert len(blocks(summary)) == 1
    assert [c["title"] for c in fake.created] == [f"{PREFIX} — {REASON[:80]}"]
    assert lines[-1] == f"release-train: no-op {PORTICO} portals — {LINE}"


def test_the_release_job_closes_the_card_on_the_verified_tag(
        tmp_path, monkeypatch, capsys, fake, summary):
    ident = fake.add(f"{PREFIX} — {REASON[:80]}")
    repo = _caller(tmp_path, RELEASING)
    code, lines = _release_cli(repo, tmp_path, monkeypatch, capsys)

    assert code == 0
    assert fake.comments == [(ident, "Released as portals/v1.4.0")]
    assert fake.moves == [(ident, "Done")]
    assert lines[-1].startswith(f"release-train: released {PORTICO} portals as portals/v1.4.0")


def test_a_linear_failure_leaves_the_release_jobs_return_code(
        tmp_path, monkeypatch, capsys, summary):
    monkeypatch.setattr(release_deferral, "linear_ops",
                        FakeLinear(fail=linear_ops.LinearError("budget spent")))
    repo = _caller(tmp_path, _deferring(LINE))
    code, lines = _release_cli(repo, tmp_path, monkeypatch, capsys)

    assert code == 0
    assert len(warnings(lines, "Release deferred")) == 1
    not_filed = [l for l in lines if l.startswith("release-deferred: card not filed — ")]
    assert len(not_filed) == 1 and "budget spent" in not_filed[0]
    assert len(warnings(lines)) == 2
    assert lines[-1] == f"release-train: no-op {PORTICO} portals — {LINE}"


# --------------------------------------------------------------------------
# 3. A no-op the train made itself, and a deferral that re-arms, are quiet.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("decision", [
    release_train.Decision(release_train.NO_OP, "window",
                           "outside the window 06:00-18:00 PT",
                           re_arm_at=datetime(2026, 10, 14, 6, tzinfo=release_train.PT)),
    release_train.Decision(release_train.NO_OP, "spacing",
                           "the newest tag is 4 minutes old",
                           re_arm_at=datetime(2026, 10, 13, 10, 26, tzinfo=release_train.PT)),
    deferred("deferred: another writer is mid-release — re-arm in 5 minutes",
             re_arm_at=datetime(2026, 10, 13, 10, 5, tzinfo=release_train.PT)),
], ids=["window", "spacing", "re-arm"])
def test_a_no_op_the_train_owns_is_quiet(decision, fake, summary):
    lines = report(decision)
    assert warnings(lines) == []
    assert blocks(summary) == []
    assert tagged(lines) == []
    assert fake.calls == []


# --------------------------------------------------------------------------
# 4. One card per repo and surface, however many laps defer.
# --------------------------------------------------------------------------

def test_three_deferrals_in_a_row_file_one_card_and_one_comment(fake, summary):
    first = tagged(report(deferred(LINE)))
    assert len(fake.created) == 1
    made = fake.created[0]
    ident = made["identifier"]
    assert made["title"] == f"{PREFIX} — {REASON[:80]}"
    assert made["labels"] == ("repo:portico", "no-code", "needs-human", "agent:ops")
    assert made["lane"] == "Green Light"
    assert made["repo_slug"] == "portico"
    assert LINE in made["description"] and SHA in made["description"]
    assert RUN_URL in made["description"]
    assert fake.comments == []          # no routing verdict, nothing at all
    assert first[1:] == [
        "release-deferred: open cards for portico portals — none",
        "release-deferred: surface-card check — none open on repo:portico",
        f'release-deferred: card {ident} created in Green Light — title '
        f'"{made["title"]}" — labels {", ".join(made["labels"])}',
    ]

    fake.calls.clear()
    second = tagged(report(deferred(LINE_NEXT_COMMIT)))
    assert fake.writes() == []
    assert [c[0] for c in fake.calls] == ["find_open_prefix"]
    assert second[1:] == [
        f"release-deferred: open cards for portico portals — {ident}",
        f"release-deferred: card {ident} already open — same reason",
    ]

    third = tagged(report(deferred(LINE_OTHER)))
    assert len(fake.created) == 1
    assert len(fake.comments) == 1
    assert fake.comments[0][0] == ident and LINE_OTHER in fake.comments[0][1]
    assert third[-1] == f"release-deferred: card {ident} commented — the reason changed"

    # The comment is the card's newest reason: the same line again is quiet.
    again = tagged(report(deferred(LINE_OTHER)))
    assert len(fake.comments) == 1
    assert again[-1] == f"release-deferred: card {ident} already open — same reason"


def test_two_open_cards_act_on_the_older_alone(fake, summary):
    newer = fake.add(f"{PREFIX} — second", created="2026-10-12T00:00:00.000Z",
                     description=f"Deferred line: {LINE}")
    older = fake.add(f"{PREFIX} — first", created="2026-10-11T00:00:00.000Z",
                     description=f"Deferred line: {LINE}")

    lines = tagged(report(deferred(LINE_OTHER)))
    assert lines[1] == (
        f"release-deferred: open cards for portico portals — {older}, {newer}")
    assert [c[0] for c in fake.comments] == [older]

    closed = tagged(report(released()))
    assert closed[0] == (
        f"release-deferred: open cards for portico portals — {older}, {newer}")
    assert fake.moves == [(older, "Done")]


# --------------------------------------------------------------------------
# 5. A deferral the surface's own train already filed a card for.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("surface_prefix", ["RELEASE ROLLED BACK:", "RELEASE BROKEN:"])
@pytest.mark.parametrize("line", [ROLLED_BACK, HELD], ids=["rolled-back", "held"])
def test_an_open_surface_card_is_loud_and_files_nothing(surface_prefix, line,
                                                        fake, summary):
    hold = fake.add(f"{surface_prefix} dreadnought — 3f2a1bc put back after "
                    "migrate failed at 9c8d7e6", state="Hand-work",
                    created="2026-10-02T09:00:00.000Z")
    lines = report(deferred(line))

    assert len(warnings(lines, "Release deferred")) == 1
    assert len(warnings(lines)) == 1        # a decision, not a failure
    assert len(blocks(summary)) == 1
    assert tagged(lines)[0].startswith("release-deferred: portico portals at 3f2a1bc — ")
    assert fake.writes() == []
    assert tagged(lines)[-1] == (
        f"release-deferred: card not filed — {hold} (Hand-work) is open for "
        "this deferral")


def test_a_held_line_adds_no_comment_to_an_open_notice(fake, summary):
    notice = fake.add(f"{PREFIX} — {REASON[:80]}",
                      description=f"Deferred line: {LINE}")
    fake.add("RELEASE ROLLED BACK: dreadnought — 3f2a1bc put back after "
             "migrate failed at 9c8d7e6", created="2026-10-02T09:00:00.000Z")
    lines = tagged(report(deferred(HELD)))
    assert fake.writes() == []
    assert lines[1] == f"release-deferred: open cards for portico portals — {notice}"
    assert lines[-1].startswith("release-deferred: card not filed — ")


@pytest.mark.parametrize("setup", ["none", "Done", "Canceled", "Duplicate",
                                   "other-repo", "RELEASE HOLD:",
                                   "RELEASE UNCHECKED:"])
@pytest.mark.parametrize("line", [ROLLED_BACK, HELD], ids=["rolled-back", "held"])
def test_with_no_open_surface_card_the_deferral_files_its_notice(setup, line,
                                                                 fake, summary):
    title = "RELEASE ROLLED BACK: dreadnought — 3f2a1bc put back"
    if setup in ("Done", "Canceled", "Duplicate"):
        fake.add(title, state=setup, created="2026-10-02T09:00:00.000Z")
    elif setup == "other-repo":
        fake.add(title, repo="agent-bureau", created="2026-10-02T09:00:00.000Z")
    elif setup.startswith("RELEASE"):
        fake.add(f"{setup} dreadnought — drop table pending",
                 created="2026-10-02T09:00:00.000Z")
    lines = tagged(report(deferred(line)))
    assert len(fake.created) == 1
    assert fake.created[0]["title"].startswith(f"{PREFIX} — ")
    assert "release-deferred: surface-card check — none open on repo:portico" in lines


def test_the_surface_card_read_is_both_titles_back_to_their_day(fake, summary):
    assert release_deferral.SURFACE_CARD_PREFIXES == (
        "RELEASE ROLLED BACK:", "RELEASE BROKEN:")
    assert release_deferral.SURFACE_CARD_SINCE == datetime(2026, 10, 1, tzinfo=UTC)
    report(deferred(LINE))
    reads = [c for c in fake.calls if c[0] == "repo_cards_by_title_prefix"]
    assert reads == [
        ("repo_cards_by_title_prefix", p, "portico", release_deferral.SURFACE_CARD_SINCE)
        for p in release_deferral.SURFACE_CARD_PREFIXES]


# --------------------------------------------------------------------------
# 6. The fingerprint: numbers, times and commit ids aside.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("1 of 4 comment rows missing after the lap: d-portico-design-system",
     "3 of 4 comment rows missing after the lap: d-portico-design-system"),
    ("held since 2026-10-13T14:44:16Z: the documents arm is held",
     "held since 2026-10-13T16:02:51Z: the documents arm is held"),
    ("staged since 2026-10-13T14:44:16Z by run 38415883166: rows not yet compared",
     "staged since 2026-10-13T14:44:16Z by run 38420017745: rows not yet compared"),
    ("the documents arm gave up after 12m", "the documents arm gave up after 45m"),
    (LINE[len("deferred: "):], LINE_NEXT_COMMIT[len("deferred: "):]),
])
def test_a_reason_differing_only_in_a_number_a_time_or_a_commit_is_one(a, b):
    assert release_deferral.deferral_fingerprint(a) == \
        release_deferral.deferral_fingerprint(b)


@pytest.mark.parametrize("a,b", [
    ("refused d-portico-design-system", "refused d-portico-brand-guide"),
    ("refused d-2024-plan", "refused d-2025-plan"),
])
def test_two_documents_are_two_reasons(a, b):
    assert release_deferral.deferral_fingerprint(a) != \
        release_deferral.deferral_fingerprint(b)


def test_a_count_that_changed_adds_no_comment(fake, summary):
    ident = fake.add(f"{PREFIX} — 1 of 4", description=(
        "Deferred line: deferred: 1 of 4 comment rows missing after the lap: d-x"))
    lines = tagged(report(deferred(
        "deferred: 3 of 4 comment rows missing after the lap: d-x")))
    assert fake.writes() == []
    assert lines[-1] == f"release-deferred: card {ident} already open — same reason"


# --------------------------------------------------------------------------
# 7. The next verified tag closes the card, wherever it sits.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("lane", ["Green Light", "Hand-work"])
def test_a_verified_tag_closes_the_open_card(lane, fake, summary):
    ident = fake.add(f"{PREFIX} — {REASON[:80]}", state=lane)
    lines = report(released("portals/v1.4.0"))
    assert tagged(lines) == [
        f"release-deferred: open cards for portico portals — {ident}",
        f"release-deferred: card {ident} closed — Released as portals/v1.4.0",
    ]
    assert fake.comments == [(ident, "Released as portals/v1.4.0")]
    assert fake.moves == [(ident, "Done")]
    assert warnings(lines) == [] and blocks(summary) == []


def test_a_verified_tag_with_no_open_card_writes_nothing(fake, summary):
    lines = report(released())
    assert tagged(lines) == ["release-deferred: open cards for portico portals — none"]
    assert fake.writes() == []
    assert [c[0] for c in fake.calls] == ["find_open_prefix"]


# --------------------------------------------------------------------------
# 8. A card that cannot be filed says so once, and changes nothing else.
# --------------------------------------------------------------------------

def test_a_linear_exception_is_one_warning_and_one_line(monkeypatch, summary):
    monkeypatch.setattr(release_deferral, "linear_ops",
                        FakeLinear(fail=RuntimeError("Linear answered 500")))
    lines = report(deferred(LINE))
    assert len(warnings(lines, "Release deferred")) == 1
    assert len(warnings(lines)) == 2
    not_filed = [l for l in tagged(lines) if l.startswith("release-deferred: card not filed — ")]
    assert len(not_filed) == 1 and "Linear answered 500" in not_filed[0]


def test_a_repository_the_map_does_not_name_files_nothing(fake, summary):
    lines = report(deferred(LINE), repo="someone-else/elsewhere")
    assert fake.calls == []
    assert len(warnings(lines, "Release deferred")) == 1
    assert len(warnings(lines)) == 2
    not_filed = [l for l in tagged(lines) if l.startswith("release-deferred: card not filed — ")]
    assert len(not_filed) == 1 and "someone-else/elsewhere" in not_filed[0]


def test_the_repo_is_the_maps_key():
    assert release_deferral.repo_key(PORTICO) == "portico"
    assert release_deferral.repo_key("EveryBite/atlas") == "atlas"
    assert release_deferral.repo_key("someone-else/elsewhere") is None


# --------------------------------------------------------------------------
# 9. The declarations that ride with the site.
# --------------------------------------------------------------------------

def test_the_log_tag_is_declared_where_a_proof_reads_it():
    doc = json.loads((ROOT / "config" / "proof-reach.json").read_text())
    entry = next(e for e in doc["log_lines"] if e["tag"] == "release-deferred:")
    assert entry["printed_by"] == "scripts/release_deferral.py"
    assert entry["constant"] == "TAG"


def test_the_green_light_arrival_is_declared_at_the_site():
    contract = json.loads((ROOT / "config" / "lane-contract.json").read_text())
    green = next(l for l in contract["lanes"] if l["name"] == "Green Light")
    records = [r for r in green["clauses"]["entrance"]["arrivals"]
               if r["kind"] == "deferred-release"]
    assert len(records) == 1
    record = records[0]
    assert record["writer"] == "release-train.yml"
    assert record["where"] == "release_deferral.py#file_deferral"
    assert record["card"] == "DRE-5235"
    for word in ("release deferred", "repo:<repo>", "no-code", "needs-human",
                 "agent:ops"):
        assert word in record["evidence"], word


def test_the_standard_and_the_document_say_the_train_files_the_card():
    standard = " ".join((ROOT / "standards" / "release-train.md").read_text().split())
    rendered = " ".join(release_train.render_markdown().split())
    for text in (standard, rendered):
        assert "RELEASE ROLLED BACK:" in text and "RELEASE BROKEN:" in text
        assert "Released as <tag>" in text
        assert "`## Deferred`" in text
