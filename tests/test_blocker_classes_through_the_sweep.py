"""The blocker classes, each through one real sweep pass (DRE-6509).

The epic's first criterion: "a test per mechanical class: a card carrying that
blocker is resolved or re-routed by one sweep and says so once". Every other
card of the epic stubs the piece it does not own — the gate's tests stub the
resolver (`tests/test_promote_skips_blocked.py`), the resolver's stub the
modules (`tests/test_blocker_resolve.py`), each module's stub `linear_ops` —
so none of them can show that the pieces fit. This file is that harness with
NOTHING of the epic stubbed: the real `blocker_resolve`, `blocker_class`,
`blocker_nothing_to_change`, `blocker_wrong_repo`, `blocker_branch_pr` and
`blocker_ask`, imported by the resolver exactly as the sweep imports them,
driven by `reconcile.promote_ready(..., resolve_blockers=True)`.

WHAT IS REPLACED, and only this:

  * `reconcile.backlog_children` and `reconcile._fetch_backlog_linear` answer
    the candidates, off the fake board below.
  * `linear_ops` is one module object, so the `Board` fake installed on it —
    `cmd_comment`, `cmd_advance`, `cmd_state`, `add_label`, `remove_label`,
    `get_issue` — is what the gate and every module see. It keeps a lane, a
    label set and a thread per card, and `get_issue(fresh=True)` answers what
    it now holds. `linear_ops.gql` fails the test: nothing reads past it.
  * `blocker_branch_pr._run` and `blocker_branch_pr._find`, for GitHub.
  * The sweep under test is agent-bureau's: `reconcile.REPO_SLUG` and
    `reconcile.REPO` are pinned, because both are bound when `reconcile` is
    imported and `REPO` is the `repo=` every module receives.

Seven cases, each ONE `promote_ready` pass, asserted on the recorded writes
and the pass's stdout. The legacy markers are the `body` of the real entries
in `tests/fixtures/blocker-reasons.json` (DRE-6438), `Run:` URL and all. After
each pass, two assertions that the receipt really resolved the marker:
`reconcile.open_agent_blocker` over the thread the fake left answers None, and
a SECOND pass over what the fake now holds in Backlog calls no action module
and records no write.

Which action module ran is observed, never stubbed: `_watching` sets a
profile hook that records each call of a real module's `resolve` code object.

Run: cd bureau-pipeline && python3 -m pytest tests/test_blocker_classes_through_the_sweep.py -v
"""
from __future__ import annotations

import contextlib
import importlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import blocker_ask  # noqa: E402
import blocker_branch_pr  # noqa: E402
import blocker_class  # noqa: E402
import blocker_nothing_to_change  # noqa: E402
import blocker_resolve  # noqa: E402
import blocker_wrong_repo  # noqa: E402
import console_escalation  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import push_rescue  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

#: The sweep under test.
REPO = "dreadnought-foundry/agent-bureau"
SLUG = "agent-bureau"

#: The receipt's tag, read off the registry the gate reads it from.
TAG = pipeline_act.tag("agent-blocker-resolved")

#: The action module per class, by the name `config/blocker-classes.json`
#: gives it — the name the resolver imports.
ACTIONS = {cls: row["action"] for cls, row in blocker_class.load()["classes"].items()}
MODULES = {name: sys.modules[name] for name in ACTIONS.values()}

ROUTING_FLEET = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")
CRITERIA = (
    "The budget table retries a run whose log is not yet readable.\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] a run whose log is unreadable is retried\n"
    "- [ ] a run still unreadable after the retries is named in the footer\n"
    "- [ ] the tests pass\n"
)

_FIXTURE = json.loads((_ROOT / "tests" / "fixtures" / "blocker-reasons.json").read_text())


def _legacy(card, run=None) -> str:
    """The `body` of a real fixture entry: a marker posted before the poster
    stamped `class=`."""
    row = next(r for r in _FIXTURE if r["card"] == card and (run is None or r["run"] == run))
    assert "class=" not in row["body"]
    return row["body"]


def _stamped(cls: str, reason: str, until: str, run: int) -> str:
    """A marker in the poster's grammar (`report_agent_result.sh`, DRE-6444)."""
    return (f"🛑 Agent blocked: class={cls} · {reason} — parked in Backlog until "
            f"{until}. Run: https://github.com/{REPO}/actions/runs/{run}")


# --------------------------------------------------------------------------- #
# the fake                                                                     #
# --------------------------------------------------------------------------- #


class Board:
    """Linear and GitHub as one sweep sees them. Every write lands in `writes`
    in the order it was made, and changes what the next read answers."""

    def __init__(self):
        self.cards: dict[str, dict] = {}
        self.writes: list[tuple] = []
        self.reads: list[str] = []
        self.fetched: list[list[str]] = []
        self.github: list[list[str]] = []
        self.found: list[str] = []

    def add(self, identifier, marker, *, title, labels=(f"repo:{SLUG}",),
            description=CRITERIA, branch=None):
        self.cards[identifier] = {
            "lane": "Backlog", "title": title, "description": description,
            "labels": list(labels), "thread": [ROUTING_FLEET, marker],
            "branch": branch,
        }

    def _card(self, identifier) -> dict:
        if identifier not in self.cards:
            pytest.fail(f"{identifier} was read past the fake board")
        return self.cards[identifier]

    def node(self, identifier) -> dict:
        """The card in `backlog_children`'s query shape — comments served
        NEWEST FIRST, the order Linear answers a window in (DRE-3250)."""
        card = self._card(identifier)
        return {
            "id": f"id-{identifier}", "identifier": identifier,
            "title": card["title"], "description": card["description"],
            "createdAt": "2026-10-09T16:00:00.000Z", "priority": 3,
            "parent": None, "children": {"nodes": []},
            "labels": {"nodes": [{"name": name} for name in card["labels"]]},
            "comments": {"nodes": [{"body": body} for body in reversed(card["thread"])]},
            "inverseRelations": {"nodes": []},
        }

    def lane(self, identifier) -> str:
        return self._card(identifier)["lane"]

    # reconcile --------------------------------------------------------------

    def backlog_children(self, only=None, **_kw):
        return [self.node(i) for i, card in self.cards.items()
                if card["lane"] == "Backlog" and (only is None or i in only)]

    def fetch_backlog_linear(self, only, *, stamped=False):
        self.fetched.append(list(only))
        return self.backlog_children(only)

    # linear_ops -------------------------------------------------------------

    def cmd_comment(self, identifier, body, *flags):
        self._card(identifier)["thread"].append(body)
        self.writes.append(("comment", identifier, body))
        return None

    def cmd_advance(self, identifier, to_state, from_states_csv, *flags, held=False):
        self.writes.append(("advance", identifier, to_state, from_states_csv, held))
        card = self._card(identifier)
        if card["lane"] in [s.strip() for s in from_states_csv.split(",")]:
            card["lane"] = to_state

    def cmd_state(self, identifier, state_name, *flags, expect=None,
                  labels_absent=(), held=False):
        self.writes.append(("state", identifier, state_name, expect, tuple(labels_absent)))
        card = self._card(identifier)
        if expect is not None and card["lane"] not in expect:
            return False
        if any(label in card["labels"] for label in labels_absent):
            return False
        card["lane"] = state_name
        return True

    def add_label(self, identifier, label_name):
        self.writes.append(("add_label", identifier, label_name))
        labels = self._card(identifier)["labels"]
        if label_name not in labels:
            labels.append(label_name)

    def remove_label(self, identifier, label_name):
        self.writes.append(("remove_label", identifier, label_name))
        labels = self._card(identifier)["labels"]
        if label_name in labels:
            labels.remove(label_name)

    def get_issue(self, identifier, *, fresh=False):
        card = self._card(identifier)
        self.reads.append(identifier)
        return {
            "id": f"id-{identifier}", "identifier": identifier, "title": card["title"],
            "team": {"id": "team-dre"}, "state": {"id": card["lane"], "name": card["lane"]},
            "labels": {"nodes": [{"name": name} for name in card["labels"]]},
            "children": {"nodes": []},
        }

    @staticmethod
    def gql(query, variables=None):
        pytest.fail(f"a Linear read went past the fake: {' '.join(query.split())[:80]}")

    # GitHub -----------------------------------------------------------------

    def run(self, argv):
        """`blocker_branch_pr._run`: the default branch, the card's one branch,
        an empty closed listing, a compare 3 ahead and a successful create."""
        argv = list(argv)
        self.github.append(argv)
        if argv[:2] == ["gh", "api"] and argv[2] == f"repos/{REPO}":
            return 0, "main\n", ""
        if argv[:2] == ["gh", "api"] and argv[2].startswith(
                f"repos/{REPO}/git/matching-refs/heads/agent/"):
            identifier = argv[2].rsplit("/agent/", 1)[1].rstrip("-")
            branch = self._card(identifier)["branch"]
            return 0, json.dumps([{"ref": f"refs/heads/{branch}"}]), ""
        if argv[:3] == ["gh", "pr", "list"]:
            return 0, "[]", ""
        if argv[:2] == ["gh", "api"] and argv[2].startswith(f"repos/{REPO}/compare/main..."):
            return 0, json.dumps({"ahead_by": 3, "last": "2026-10-09T15:00:00Z"}), ""
        if argv[:3] == ["gh", "pr", "create"]:
            self.writes.append(("create", _card_of(argv[argv.index("--head") + 1]), argv))
            return 0, f"https://github.com/{REPO}/pull/4242\n", ""
        pytest.fail(f"a GitHub call the fake does not answer: {argv}")

    def find(self, identifier, **_kw):
        """`blocker_branch_pr._find`: no pull request counts for the card."""
        self.found.append(identifier)
        return None


def _card_of(branch: str) -> str:
    """`agent/DRE-6056-retire-split-ledger` → `DRE-6056`."""
    return "-".join(branch.split("/", 1)[1].split("-")[:2])


@pytest.fixture
def board(monkeypatch):
    fake = Board()
    monkeypatch.setattr(reconcile, "REPO_SLUG", SLUG)
    monkeypatch.setattr(reconcile, "REPO", REPO)
    monkeypatch.setattr(reconcile, "backlog_children", fake.backlog_children)
    monkeypatch.setattr(reconcile, "_fetch_backlog_linear", fake.fetch_backlog_linear)
    for name in ("cmd_comment", "cmd_advance", "cmd_state", "add_label",
                 "remove_label", "get_issue", "gql"):
        monkeypatch.setattr(linear_ops, name, getattr(fake, name))
    monkeypatch.setattr(blocker_branch_pr, "_run", fake.run)
    monkeypatch.setattr(blocker_branch_pr, "_find", fake.find)

    def offline(*args, **_kw):
        pytest.fail(f"a network or process call went past the fakes: {args!r:.120}")

    monkeypatch.setattr(socket.socket, "connect", offline)
    monkeypatch.setattr(subprocess, "run", offline)
    monkeypatch.setattr(subprocess, "Popen", offline)
    return fake


# --------------------------------------------------------------------------- #
# one pass                                                                     #
# --------------------------------------------------------------------------- #


@contextlib.contextmanager
def _watching():
    """Every call of a real action module's `resolve`, by the module's name
    and the file its code came from — observed, the function untouched."""
    targets = {module.resolve.__code__: name for name, module in MODULES.items()}
    called: list[tuple[str, str]] = []

    def profile(frame, event, _arg):
        if event == "call" and frame.f_code in targets:
            called.append((targets[frame.f_code], frame.f_code.co_filename))

    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        yield called
    finally:
        sys.setprofile(previous)


class Pass:
    def __init__(self, promoted, out, called):
        self.promoted = promoted
        self.out = out
        self.called = called

    def gate_lines(self, identifier) -> list[str]:
        return [line for line in self.out.splitlines()
                if line.startswith(f"promotion: {identifier} agent-blocker class=")]


def _sweep(capsys) -> Pass:
    reconcile._write_failures.clear()
    reconcile._card_skips.clear()
    capsys.readouterr()
    with _watching() as called:
        promoted = reconcile.promote_ready(active_count=0, resolve_blockers=True)
    out = capsys.readouterr().out
    assert reconcile._write_failures == []
    return Pass(promoted, out, called)


def _summary(writes) -> list[tuple]:
    """The writes in order, a comment named by what it is."""
    summary = []
    for write in writes:
        if write[0] == "comment":
            body = write[2]
            kind = ("receipt" if TAG in body
                    else "ask" if body.startswith(blocker_ask.FIRST_LINE) else "comment")
            summary.append((kind, write[1]))
        elif write[0] == "create":
            summary.append(("create", write[1]))
        else:
            summary.append(write)
    return summary


def _comments(board, kind) -> list[str]:
    return [w[2] for w, s in zip(board.writes, _summary(board.writes)) if s[0] == kind]


def _resolved_once(board, run, identifier, cls, action, writes, modules) -> str:
    """What every case asserts after its pass; the receipt's body back."""
    assert run.promoted == 0
    # One gate line, and it is the `resolved —` one.
    (line,) = run.gate_lines(identifier)
    assert line.startswith(
        f"promotion: {identifier} agent-blocker class={cls} resolved — {action}: "), line
    # The writes in order: the module's own, then the ONE receipt after them.
    assert _summary(board.writes) == writes
    assert writes[-1] == ("receipt", identifier)
    (receipt,) = _comments(board, "receipt")
    assert receipt.startswith(f"🧹 agent-blocker-resolved: class={cls} action={action} — ")
    assert pipeline_act.read_trailer(receipt)["act"] == "agent-blocker-resolved"
    # Never a Todo or Hand-work write.
    assert not [w for w in board.writes
                if w[0] in ("advance", "state") and w[2] in ("Todo", "Hand-work")]
    # The real modules ran, from the real files.
    assert [name for name, _file in run.called] == modules
    for name, filename in run.called:
        assert Path(filename).resolve() == _SCRIPTS / f"{name}.py"
    return receipt


def _closed_for_good(board, capsys, identifier):
    """The receipt really resolved the marker: the gate's own reader finds no
    open blocker on the thread the fake left, and a second pass over what the
    fake holds in Backlog calls no module and writes nothing."""
    assert reconcile.open_agent_blocker(board.node(identifier)) is None
    before = (list(board.writes), list(board.fetched), list(board.github), list(board.reads))
    again = _sweep(capsys)
    assert again.called == []
    assert again.gate_lines(identifier) == []
    assert again.promoted == 0
    assert (board.writes, board.fetched, board.github, board.reads) == before


def _three_lines(ask: str) -> console_escalation.Escalation:
    parsed = console_escalation.parse(ask)
    assert parsed is not None, ask
    for prefix in (console_escalation.FINDING_PREFIX, console_escalation.QUESTION_PREFIX,
                   console_escalation.RECOMMENDATION_PREFIX):
        assert f"\n{prefix} " in ask
    return parsed


# --------------------------------------------------------------------------- #
# the seven cases                                                              #
# --------------------------------------------------------------------------- #


def test_1_a_stamped_nothing_to_change_attesting_every_criterion_cancels_the_card(
        board, capsys):
    ident = "DRE-6601"
    reason = ("There is nothing for this card to change.\n"
              "- [x] a run whose log is unreadable is retried — the retry loop in "
              "scripts/check_linear_budget.py on main\n"
              "- [x] a run still unreadable after the retries is named in the footer — "
              "the footer line on main\n"
              "- [x] the tests pass — tests/test_check_linear_budget.py on main")
    board.add(ident, _stamped(
        "nothing-to-change", reason,
        "the sweep acts on it (it cancels the card once every criterion is attested "
        "met, and sends the rest to the planner)", 37900000001),
        title="bureau-pipeline: retry an unreadable run log")

    run = _sweep(capsys)

    _resolved_once(board, run, ident, "nothing-to-change", "canceled", [
        ("state", ident, "Canceled", ("Backlog",), ("needs-human",)),
        ("receipt", ident),
    ], ["blocker_nothing_to_change"])
    assert board.lane(ident) == "Canceled"
    _closed_for_good(board, capsys, ident)


def test_2_the_dre_5195_legacy_marker_sends_the_card_to_planning(board, capsys):
    ident = "DRE-5195"
    board.add(ident, _legacy(ident), title="agent-bureau: set the epic cap to 15")

    run = _sweep(capsys)

    receipt = _resolved_once(board, run, ident, "nothing-to-change", "replanned", [
        ("advance", ident, "Planning", "Backlog", True),
        ("receipt", ident),
    ], ["blocker_nothing_to_change"])
    assert "0 of 3 criteria attested" in receipt
    assert board.lane(ident) == "Planning"
    _closed_for_good(board, capsys, ident)


def test_3_the_dre_3242_legacy_marker_relabels_the_card_and_moves_no_lane(board, capsys):
    ident = "DRE-3242"
    board.add(ident, _legacy(ident, run=37542061691),
              title="Linear budget table: retry a run whose log is not yet readable",
              labels=(f"repo:{SLUG}", "agent:engineer"))

    run = _sweep(capsys)

    receipt = _resolved_once(board, run, ident, "wrong-repo", "relabeled", [
        ("add_label", ident, "repo:bureau-pipeline"),
        ("remove_label", ident, f"repo:{SLUG}"),
        ("receipt", ident),
    ], ["blocker_wrong_repo"])
    assert "repo:agent-bureau → repo:bureau-pipeline" in receipt
    assert board.lane(ident) == "Backlog"
    assert board.cards[ident]["labels"] == ["agent:engineer", "repo:bureau-pipeline"]
    # Still in Backlog, now another repo's card: this sweep skips it silently.
    assert [c["identifier"] for c in board.backlog_children()] == [ident]
    _closed_for_good(board, capsys, ident)


def test_4_the_dre_6056_legacy_marker_opens_a_draft_and_asks_in_green_light(board, capsys):
    ident = "DRE-6056"
    marker = _legacy(ident)
    board.add(ident, marker, title="retire the split ledger",
              branch="agent/DRE-6056-retire-split-ledger")

    run = _sweep(capsys)

    _resolved_once(board, run, ident, "branch-without-pr", "asked", [
        ("create", ident),
        ("ask", ident),
        ("advance", ident, "Green Light", "Backlog", True),
        ("receipt", ident),
    ], ["blocker_branch_pr", "blocker_ask"])
    (create,) = [w[2] for w in board.writes if w[0] == "create"]
    assert create[create.index("--repo") + 1] == REPO
    assert create[create.index("--head") + 1] == "agent/DRE-6056-retire-split-ledger"
    assert "--draft" in create
    assert board.found == [ident]
    assert not [w for w in board.writes if w[0] == "advance" and w[2] == "In Review"]

    (ask,) = _comments(board, "ask")
    _three_lines(ask)
    assert blocker_class.marker_reason(marker).splitlines()[0] in ask
    assert ("(class=branch-without-pr: the sweep could not act on this "
            "mechanically)") in ask
    # The agent's run, read off the marker it posted.
    assert ask.endswith(
        "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37800258309")
    assert board.lane(ident) == "Green Light"
    _closed_for_good(board, capsys, ident)


def test_5_a_stamped_branch_without_pr_opens_a_ready_pull_request_and_moves_to_review(
        board, capsys):
    ident = "DRE-6605"
    branch = "agent/DRE-6605-widget-retry"
    reason = (f"The work is finished and pushed on the branch {branch}, failing tests "
              "first. The pull request create was refused by GitHub, so I opened no "
              "pull request.")
    board.add(ident, _stamped(
        "branch-without-pr", reason,
        "the sweep acts on it (it opens or finds the pull request for the work on "
        "the branch)", 37900000005),
        title="retry the widget fetch", branch=branch)

    run = _sweep(capsys)

    receipt = _resolved_once(board, run, ident, "branch-without-pr", "pr-opened", [
        ("create", ident),
        ("advance", ident, "In Review", "Backlog", True),
        ("receipt", ident),
    ], ["blocker_branch_pr"])
    (create,) = [w[2] for w in board.writes if w[0] == "create"]
    assert create[create.index("--repo") + 1] == REPO
    assert create[create.index("--head") + 1] == branch
    assert "--draft" not in create
    assert f"https://github.com/{REPO}/pull/4242 opened from {branch}" in receipt
    assert ("the agent's note: "
            + push_rescue.one_line(reason, blocker_branch_pr.NOTE_QUOTE)) in receipt
    assert board.lane(ident) == "In Review"
    _closed_for_good(board, capsys, ident)


def test_6_the_synthetic_legacy_question_is_asked_in_green_light(board, capsys):
    ident = "DRE-6606"
    marker = _legacy(None)
    board.add(ident, marker, title="a retry budget")

    run = _sweep(capsys)

    receipt = _resolved_once(board, run, ident, "question", "asked", [
        ("ask", ident),
        ("advance", ident, "Green Light", "Backlog", True),
        ("receipt", ident),
    ], ["blocker_ask"])
    (ask,) = _comments(board, "ask")
    parsed = _three_lines(ask)
    assert blocker_class.marker_reason(marker) in ask
    assert "class=" not in ask
    assert (f"action=asked — asked in Green Light: "
            f"{parsed.question[:blocker_ask.NOTE_QUOTE]}") in receipt
    assert board.lane(ident) == "Green Light"
    _closed_for_good(board, capsys, ident)


def test_7_a_wrong_repo_naming_two_repositories_is_asked_with_nothing_stubbed(
        board, capsys):
    """The mechanical-None → ask seam: the real wrong-repo module answers None
    for a reason naming two rail slugs besides the dispatched one, and the
    real ask posts."""
    ident = "DRE-6607"
    marker = (
        "🛑 Agent blocked: This card is pointed at the wrong repo. The sweep's "
        "script it changes lives in bureau-pipeline and the screen it feeds lives "
        "in portico, so no one repository holds the work. — parked in Backlog "
        "until the blocker is resolved (a Todo return here would redispatch agents "
        f"into the same wall). Run: https://github.com/{REPO}/actions/runs/37900000007"
    )
    reason = blocker_class.marker_reason(marker)
    assert blocker_wrong_repo.named_slug(reason, SLUG) is None
    board.add(ident, marker, title="show the budget on the portal")

    run = _sweep(capsys)

    _resolved_once(board, run, ident, "wrong-repo", "asked", [
        ("ask", ident),
        ("advance", ident, "Green Light", "Backlog", True),
        ("receipt", ident),
    ], ["blocker_wrong_repo", "blocker_ask"])
    (ask,) = _comments(board, "ask")
    _three_lines(ask)
    assert "(class=wrong-repo: the sweep could not act on this mechanically)" in ask
    assert board.cards[ident]["labels"] == [f"repo:{SLUG}"]
    assert board.lane(ident) == "Green Light"
    _closed_for_good(board, capsys, ident)


# --------------------------------------------------------------------------- #
# nothing of the epic is a stub, and nothing reads past the fakes              #
# --------------------------------------------------------------------------- #


def test_the_resolver_and_every_action_module_are_the_real_files_under_scripts():
    assert Path(blocker_resolve.__file__).resolve() == _SCRIPTS / "blocker_resolve.py"
    assert Path(blocker_class.__file__).resolve() == _SCRIPTS / "blocker_class.py"
    assert set(MODULES) == {"blocker_nothing_to_change", "blocker_wrong_repo",
                            "blocker_branch_pr", "blocker_ask"}
    for name, module in MODULES.items():
        assert Path(module.__file__).resolve() == _SCRIPTS / f"{name}.py"
        # The module the resolver imports by name is this one.
        assert importlib.import_module(name) is module
    # The ask the resolver imports statically is the same module object.
    assert blocker_resolve.blocker_ask is blocker_ask
    assert reconcile.blocker_resolve is blocker_resolve


def test_the_fakes_fail_the_test_on_any_read_past_them(board):
    with pytest.raises(pytest.fail.Exception, match="went past the fake"):
        linear_ops.gql("query { viewer { id } }")
    with pytest.raises(pytest.fail.Exception, match="read past the fake board"):
        linear_ops.get_issue("DRE-1", fresh=True)
    with pytest.raises(pytest.fail.Exception, match="does not answer"):
        blocker_branch_pr._run(["gh", "api", "user"])
    assert reconcile.REPO == REPO and reconcile.REPO_SLUG == SLUG
