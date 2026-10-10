"""RED-first tests: the `branch-without-pr` class's action module (DRE-6447).

DRE-6056's run pushed its finished work to `agent/DRE-6056-retire-split-ledger`,
wrote a blocker instead of opening the pull request, and the card sat in
Backlog until a person opened the pull request by hand. The push rescue reads a
blocker note as the agent choosing not to open one, so it did nothing by
design. `scripts/blocker_branch_pr.py` overrules that for this one class, from
the sweep: it opens or finds the pull request for the card's newest
`agent/<card>-…` branch.

WHAT THESE TESTS PIN, with `_run`, `_find` and `linear_ops` replaced and the
module driven alone — the run through the real resolver and one real sweep
pass is DRE-6509's:

  * The kind of note is read off the live thread: a STAMPED marker
    (`class=branch-without-pr`) gets a READY pull request and the In Review
    move, a LEGACY marker (no `class=`) gets a DRAFT and moves nothing — the
    `None` return hands the card to the ask in Green Light. `--draft` is the one
    difference in the create's argv, pinned both ways.
  * A found pull request: open and ready → `pr-found` and the In Review move;
    open and draft → `None`; merged → `None`; unreadable → `NotNow`.
  * A pull request a person closed unmerged gets no successor.
  * Every unreadable `gh` read is `NotNow`, never "no branch" or "nothing to
    open"; a refused create is a `RuntimeError` carrying GitHub's words.
  * Every lane move is read back before it is reported.
  * The module holds no copy of the marker prefix, posts no comment, and never
    names the resolver's tag.

Run: cd bureau-pipeline && python3 -m pytest tests/test_blocker_branch_pr.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import blocker_branch_pr as module  # noqa: E402
import blocker_class  # noqa: E402
import card_pr  # noqa: E402
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import push_rescue  # noqa: E402

SCRIPT = ROOT / "scripts" / "blocker_branch_pr.py"
FIXTURE = ROOT / "tests" / "fixtures" / "blocker-reasons.json"
REPO = "dreadnought-foundry/bureau-pipeline"
CARD = "DRE-9001"
TITLE = "Retire the split ledger"
BRANCH = f"agent/{CARD}-retire-split-ledger"
URL = f"https://github.com/{REPO}/pull/901"
CARD_URL = f"https://linear.app/dreadnoughtfoundry/issue/{CARD}"
PARKED = (" — parked in Backlog until the blocker is resolved (a Todo return here "
          "would redispatch agents into the same wall). Run: "
          "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/1")
STAMPED_REASON = ("The work is finished and pushed on the branch "
                  f"{BRANCH}, but gh pr create was refused, so the pull request "
                  "is not open.")
STAMPED_MARKER = (f"🛑 Agent blocked: class=branch-without-pr · {STAMPED_REASON}"
                  f"{PARKED}")


def _legacy_entry():
    return next(row for row in json.loads(FIXTURE.read_text(encoding="utf-8"))
                if row["card"] == "DRE-6056")


LEGACY_MARKER = _legacy_entry()["body"]
LEGACY_REASON = _legacy_entry()["reason"]


def _card(*bodies):
    """The sweep's card dict; `bodies` oldest→newest, stored newest-first the
    way the API answers."""
    return {
        "identifier": CARD,
        "title": TITLE,
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
        "description": "Retire it.",
        "comments": {"nodes": [{"body": body} for body in reversed(bodies)]},
    }


def _ref(branch):
    return {"ref": f"refs/heads/{branch}", "object": {"sha": "a" * 40}}


class StubRun:
    """`_run` as the module sees it: every argv recorded, each answered by
    the kind of read it is."""

    def __init__(self, *, default=(0, "main\n", ""), refs=None, closed=(0, "[]", ""),
                 compare=None, create=(0, f"{URL}\n", "")):
        self.calls = []
        self.default = default
        self.refs = refs if refs is not None else (0, json.dumps([_ref(BRANCH)]), "")
        self.closed = closed
        # One answer for every branch, or a dict keyed by branch.
        self.compare = compare if compare is not None else (
            0, json.dumps({"ahead_by": 3, "last": "2026-10-09T15:00:00Z"}), "")
        self.create = create

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:2] == ["gh", "pr"] and argv[2] == "create":
            return self.create
        if argv[:3] == ["gh", "pr", "list"]:
            return self.closed
        assert argv[:2] == ["gh", "api"], argv
        path = argv[2]
        if path == f"repos/{REPO}":
            return self.default
        if "/git/matching-refs/" in path:
            return self.refs
        if "/compare/" in path:
            if isinstance(self.compare, dict):
                return self.compare[path.split("...", 1)[1]]
            return self.compare
        raise AssertionError(f"unexpected gh call {argv}")

    def creates(self):
        return [argv for argv in self.calls if argv[:3] == ["gh", "pr", "create"]]

    def kinds(self):
        out = []
        for argv in self.calls:
            if argv[:3] == ["gh", "pr", "create"]:
                out.append("create")
            elif argv[:3] == ["gh", "pr", "list"]:
                out.append("closed")
            elif "/git/matching-refs/" in argv[2]:
                out.append("refs")
            elif "/compare/" in argv[2]:
                out.append("compare")
            else:
                out.append("default")
        return out


class StubFind:
    def __init__(self, answer=None, *, raises=None):
        self.answer = answer
        self.raises = raises
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.answer


class StubLinear:
    """`linear_ops` as the module sees it: the advance, the fresh read and
    the window reader, the first two recorded — anything else is an
    AttributeError, so "no other write" is a fact the stub enforces."""

    def __init__(self, *, lands=True, lane="Backlog"):
        self.calls = []
        self.lands = lands
        self.lane = lane

    def cmd_advance(self, identifier, to_state, from_states_csv, *flags, **kwargs):
        self.calls.append(("cmd_advance", identifier, to_state, from_states_csv, flags, kwargs))
        if self.lands and self.lane == "Backlog":
            self.lane = to_state
        else:
            print(f"{identifier} is in {self.lane}, not {from_states_csv} — not advancing")

    def get_issue(self, identifier, **kwargs):
        self.calls.append(("get_issue", identifier, kwargs))
        return {"identifier": identifier, "state": {"name": self.lane}}

    def window_nodes(self, comments):
        return linear_ops.window_nodes(comments)


ADVANCED = [
    ("cmd_advance", CARD, "In Review", "Backlog", (), {"held": True}),
    ("get_issue", CARD, {"fresh": True}),
]


@pytest.fixture
def stubs(monkeypatch):
    """Install a fresh trio; returns a function that takes overrides."""

    def install(*, run=None, find=None, linear=None):
        run = run or StubRun()
        find = find or StubFind()
        linear = linear or StubLinear()
        monkeypatch.setattr(module, "_run", run)
        monkeypatch.setattr(module, "_find", find)
        monkeypatch.setattr(module, "linear_ops", linear)
        return run, find, linear

    return install


def _arg(argv, flag):
    return argv[argv.index(flag) + 1]


def _second_paragraph(body):
    return body.split("\n\n")[1]


class TestTheStampedCreate:
    def test_a_ready_pull_request_then_the_in_review_move(self, stubs):
        run, find, linear = stubs()
        result = module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO)

        [argv] = run.creates()
        assert _arg(argv, "--repo") == REPO
        assert _arg(argv, "--base") == "main"
        assert _arg(argv, "--head") == BRANCH
        assert _arg(argv, "--title").startswith(f"feat({CARD}):")
        assert _arg(argv, "--title") == f"feat({CARD}): {TITLE}"
        assert "--draft" not in argv

        body = _arg(argv, "--body")
        assert body.split("\n", 1)[0] == f"{CARD} — {CARD_URL}"
        second = _second_paragraph(body)
        assert second.startswith(
            "OPENED BY THE SWEEP, not by the agent — read this before approving:")
        assert "branch-without-pr" in second
        assert STAMPED_REASON[:60] in body
        assert "HELD BY THE AGENT" not in body
        assert module.CLOSING in body

        assert linear.calls == ADVANCED
        action, note = result
        assert action == "pr-opened"
        assert note.startswith(f"{URL} opened from {BRANCH} — the agent's note: ")
        assert STAMPED_REASON[:40] in note

    def test_the_reads_happen_in_the_cards_order(self, stubs):
        run, _, _ = stubs()
        module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO)
        assert run.kinds() == ["default", "refs", "closed", "compare", "create"]
        assert run.calls[0] == ["gh", "api", f"repos/{REPO}", "--jq", ".default_branch"]
        assert run.calls[1] == ["gh", "api",
                                f"repos/{REPO}/git/matching-refs/heads/agent/{CARD}-"]
        assert run.calls[2] == ["gh", "pr", "list", "--repo", REPO, "--head", BRANCH,
                                "--state", "closed", "--json", "number,url,mergedAt",
                                "--limit", "5"]
        assert run.calls[3][2] == f"repos/{REPO}/compare/main...{BRANCH}"

    def test_the_note_quotes_at_most_120_characters_of_the_reason_on_one_line(self, stubs):
        stubs()
        reason = "line one of the note\nline two " + "x" * 400
        _, note = module.resolve(_card(STAMPED_MARKER), reason, repo=REPO)
        quoted = note.split("the agent's note: ", 1)[1]
        assert "\n" not in note
        assert len(quoted) <= 120
        assert quoted.startswith("line one of the note line two")

    def test_the_body_quotes_at_most_500_characters_of_the_reason(self, stubs):
        run, _, _ = stubs()
        reason = "a" * 500 + "ZZZZ"
        module.resolve(_card(STAMPED_MARKER), reason, repo=REPO)
        body = _arg(run.creates()[0], "--body")
        assert "a" * 500 in body
        assert "ZZZZ" not in body

    def test_the_closing_sentence_is_the_push_rescues_own(self):
        assert module.CLOSING in push_rescue._pr_body(CARD, CARD_URL)


class TestTheLegacyCreate:
    def test_a_draft_and_no_move(self, stubs):
        run, _, linear = stubs()
        assert blocker_class.marker_class(LEGACY_MARKER) is None
        result = module.resolve(_card(LEGACY_MARKER), LEGACY_REASON, repo=REPO)

        [argv] = run.creates()
        assert "--draft" in argv
        body = _arg(argv, "--body")
        assert body.split("\n", 1)[0] == f"{CARD} — {CARD_URL}"
        second = _second_paragraph(body)
        assert second.startswith(
            "OPENED AS A DRAFT BY THE SWEEP — the agent held this branch:")
        assert LEGACY_REASON[:60] in body
        assert "Green Light" in second
        assert "ready for review" in second
        assert "HELD BY THE AGENT" not in body
        assert module.CLOSING in body

        assert linear.calls == []
        assert result is None

    def test_the_argv_differs_from_the_stamped_one_by_draft_alone(self, stubs):
        legacy, _, _ = stubs()
        module.resolve(_card(LEGACY_MARKER), LEGACY_REASON, repo=REPO)
        stamped, _, _ = stubs()
        module.resolve(_card(STAMPED_MARKER), LEGACY_REASON, repo=REPO)
        [a], [b] = legacy.creates(), stamped.creates()
        # The bodies differ by design; every other word of the argv is the same.
        a[a.index("--body") + 1] = b[b.index("--body") + 1] = "<body>"
        assert a == b + ["--draft"]

    def test_the_newest_marker_decides_the_kind(self, stubs):
        run, _, linear = stubs()
        # An old legacy marker under a newer stamped one: the newer wins.
        module.resolve(_card(LEGACY_MARKER, "⏳ 1/5 plan", STAMPED_MARKER),
                       STAMPED_REASON, repo=REPO)
        assert "--draft" not in run.creates()[0]
        run, _, linear = stubs()
        module.resolve(_card(STAMPED_MARKER, LEGACY_MARKER), LEGACY_REASON, repo=REPO)
        assert "--draft" in run.creates()[0]
        assert linear.calls == []


class TestAFoundPullRequest:
    FIELDS = card_pr.PR_FIELDS + ",isDraft"

    def _pr(self, state, draft=False):
        return {"number": 901, "url": URL, "headRefName": BRANCH, "state": state,
                "isDraft": draft}

    def test_open_and_ready_is_pr_found_with_the_move(self, stubs):
        run, find, linear = stubs(find=StubFind(self._pr("OPEN")))
        action, note = module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO)
        assert action == "pr-found"
        assert note == f"{URL} was already open"
        assert run.creates() == []
        assert linear.calls == ADVANCED

    def test_open_and_ready_on_a_legacy_marker_is_pr_found_too(self, stubs):
        run, _, linear = stubs(find=StubFind(self._pr("OPEN")))
        action, _ = module.resolve(_card(LEGACY_MARKER), LEGACY_REASON, repo=REPO)
        assert action == "pr-found"
        assert linear.calls == ADVANCED

    @pytest.mark.parametrize("marker,reason", [(STAMPED_MARKER, STAMPED_REASON),
                                               (LEGACY_MARKER, LEGACY_REASON)])
    def test_open_draft_is_a_persons_call_for_either_kind(self, stubs, marker, reason):
        run, _, linear = stubs(find=StubFind(self._pr("OPEN", draft=True)))
        assert module.resolve(_card(marker), reason, repo=REPO) is None
        assert run.creates() == []
        assert "closed" not in run.kinds()
        assert linear.calls == []

    def test_merged_is_a_persons_call(self, stubs):
        run, _, linear = stubs(find=StubFind(self._pr("MERGED")))
        assert module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO) is None
        assert run.creates() == []
        assert linear.calls == []

    def test_an_unreadable_lookup_is_not_now_naming_the_branch(self, stubs):
        error = card_pr.PrLookupError("gh pr list failed rc=1: HTTP 502\nmore detail")
        run, _, linear = stubs(find=StubFind(raises=error))
        with pytest.raises(blocker_class.NotNow) as raised:
            module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO)
        assert BRANCH in str(raised.value)
        assert "HTTP 502" in str(raised.value)
        assert "more detail" not in str(raised.value)
        assert run.creates() == []
        assert linear.calls == []

    def test_find_is_called_with_the_card_the_branch_the_repo_and_the_draft_flag(self, stubs):
        _, find, _ = stubs()
        module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO)
        assert find.calls == [((CARD,), {"branch": BRANCH, "repo": REPO,
                                         "fields": self.FIELDS})]


class TestAPullRequestAPersonClosed:
    @pytest.mark.parametrize("marker,reason", [(STAMPED_MARKER, STAMPED_REASON),
                                               (LEGACY_MARKER, LEGACY_REASON)])
    def test_closed_unmerged_gets_no_successor(self, stubs, marker, reason):
        closed = (0, json.dumps([{"number": 880, "url": URL, "mergedAt": None}]), "")
        run, _, linear = stubs(run=StubRun(closed=closed))
        assert module.resolve(_card(marker), reason, repo=REPO) is None
        assert run.creates() == []
        assert "compare" not in run.kinds()
        assert linear.calls == []

    def test_a_merged_row_alone_lets_the_compare_and_create_proceed(self, stubs):
        closed = (0, json.dumps([{"number": 880, "url": URL,
                                  "mergedAt": "2026-10-01T00:00:00Z"}]), "")
        run, _, _ = stubs(run=StubRun(closed=closed))
        action, _ = module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO)
        assert action == "pr-opened"
        assert run.kinds()[-2:] == ["compare", "create"]

    @pytest.mark.parametrize("closed", [(1, "", "HTTP 403: forbidden\n"),
                                        (0, "not json", ""), (0, "{}", "")])
    def test_an_unreadable_closed_listing_is_not_now(self, stubs, closed):
        run, _, linear = stubs(run=StubRun(closed=closed))
        with pytest.raises(blocker_class.NotNow):
            module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO)
        assert run.creates() == []
        assert linear.calls == []


class TestTheMoveIsReadBack:
    def test_a_ready_create_whose_move_did_not_land_is_not_now(self, stubs):
        run, _, linear = stubs(linear=StubLinear(lands=False))
        with pytest.raises(blocker_class.NotNow) as raised:
            module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO)
        assert "In Review" in str(raised.value)
        assert "Backlog" in str(raised.value)
        assert len(run.creates()) == 1
        assert linear.calls == ADVANCED

    def test_a_found_pull_request_whose_move_did_not_land_is_not_now(self, stubs):
        pr = {"number": 901, "url": URL, "headRefName": BRANCH, "state": "OPEN",
              "isDraft": False}
        run, _, linear = stubs(find=StubFind(pr), linear=StubLinear(lands=False))
        with pytest.raises(blocker_class.NotNow) as raised:
            module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO)
        assert "In Review" in str(raised.value) and "Backlog" in str(raised.value)
        assert run.creates() == []
        assert linear.calls == ADVANCED

    def test_the_review_workflow_moving_it_first_still_passes(self, stubs, capsys):
        # qa-review.yml's "Card → In Review" step won the race: the advance
        # prints "not advancing" and the fresh read answers In Review.
        _, _, linear = stubs(linear=StubLinear(lane="In Review"))
        action, _ = module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO)
        assert action == "pr-opened"
        assert "not advancing" in capsys.readouterr().out
        assert linear.calls == ADVANCED


class TestNothingToOpen:
    def test_no_branch_on_origin_is_a_persons_call(self, stubs):
        run, find, linear = stubs(run=StubRun(refs=(0, "[]", "")))
        assert module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO) is None
        assert run.creates() == []
        assert find.calls == []
        assert linear.calls == []

    def test_a_branch_with_nothing_ahead_is_a_persons_call(self, stubs):
        compare = (0, json.dumps({"ahead_by": 0, "last": None}), "")
        run, _, linear = stubs(run=StubRun(compare=compare))
        assert module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO) is None
        assert run.creates() == []
        assert linear.calls == []

    def test_the_newest_branch_by_its_last_commit_is_taken(self, stubs):
        old, new = f"agent/{CARD}-first-try", f"agent/{CARD}-second-try"
        refs = (0, json.dumps([_ref(old), _ref(new)]), "")
        compare = {
            old: (0, json.dumps({"ahead_by": 5, "last": "2026-10-01T00:00:00Z"}), ""),
            new: (0, json.dumps({"ahead_by": 2, "last": "2026-10-09T00:00:00Z"}), ""),
        }
        run, find, _ = stubs(run=StubRun(refs=refs, compare=compare))
        module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO)
        assert _arg(run.creates()[0], "--head") == new
        assert find.calls[0][1]["branch"] == new

    def test_the_branch_is_never_read_out_of_the_note(self, stubs):
        run, _, _ = stubs()
        reason = "The work is pushed on the branch agent/DRE-1-somebody-else."
        module.resolve(_card(STAMPED_MARKER), reason, repo=REPO)
        assert _arg(run.creates()[0], "--head") == BRANCH


class TestUnreadableReadsAreNotNow:
    @pytest.mark.parametrize("which,answer", [
        ("default", (1, "", "HTTP 502: bad gateway\n")),
        ("default", (0, "\n", "")),
        ("refs", (1, "", "HTTP 403: rate limit exceeded\n")),
        ("refs", (0, "not json", "")),
        ("refs", (0, "{}", "")),
        ("compare", (1, "", "HTTP 404: Not Found\n")),
        ("compare", (0, "not json", "")),
        ("compare", (0, "[]", "")),
    ])
    def test_each_read_raises_not_now_and_writes_nothing(self, stubs, which, answer):
        run, _, linear = stubs(run=StubRun(**{which: answer}))
        with pytest.raises(blocker_class.NotNow) as raised:
            module.resolve(_card(STAMPED_MARKER), STAMPED_REASON, repo=REPO)
        assert CARD in str(raised.value)
        if answer[2]:
            assert answer[2].strip() in str(raised.value)
        assert run.creates() == []
        assert linear.calls == []


class TestARefusedCreate:
    @pytest.mark.parametrize("marker,reason", [(STAMPED_MARKER, STAMPED_REASON),
                                               (LEGACY_MARKER, LEGACY_REASON)])
    def test_a_refused_create_is_a_runtime_error_with_githubs_words(self, stubs,
                                                                     marker, reason):
        create = (1, "", "some preamble\npull request create failed: GraphQL: "
                         "Draft pull requests are not supported in this repository\n")
        run, _, linear = stubs(run=StubRun(create=create))
        with pytest.raises(RuntimeError) as raised:
            module.resolve(_card(marker), reason, repo=REPO)
        assert not isinstance(raised.value, blocker_class.NotNow)
        assert "Draft pull requests are not supported" in str(raised.value)
        assert "some preamble" not in str(raised.value)
        assert len(run.creates()) == 1
        assert linear.calls == []


class TestTheMarkerIsReadOffTheLiveThread:
    def test_no_marker_in_the_window_is_not_now_naming_the_card(self, stubs):
        run, find, linear = stubs()
        with pytest.raises(blocker_class.NotNow) as raised:
            module.resolve(_card("⏳ 1/5 plan", "a person's reply"), STAMPED_REASON,
                           repo=REPO)
        assert CARD in str(raised.value)
        assert run.calls == []
        assert find.calls == []
        assert linear.calls == []

    def test_the_prefix_and_the_kind_come_from_the_vocabulary(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert 'blocker_class.load()["marker"]' in source
        assert "blocker_class.marker_class" in source
        assert "🛑 Agent blocked" not in source
        assert "🛑" not in source


class TestTheModulePostsNothingAndNamesNoTag:
    def test_the_module_never_names_the_resolvers_tag(self):
        assert "agent-blocker-resolved" not in SCRIPT.read_text(encoding="utf-8")

    def test_the_module_never_calls_a_comment_writer(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for writer in ("cmd_comment", "post_comment", "comment_once",
                       "linear_ops.comment", "gh pr comment", '"comment"'):
            assert writer not in source, writer

    def test_the_seams_are_the_shared_helpers(self):
        assert module._run is push_rescue._subprocess_run
        assert module._find is card_pr.find

    def test_the_vocabulary_names_this_module_for_the_class(self):
        row = blocker_class.load()["classes"]["branch-without-pr"]
        assert row["action"] == SCRIPT.stem

    def test_check_act_receipts_is_green(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_act_receipts.py")],
            cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")},
        )
        assert done.returncode == 0, done.stdout + done.stderr


class TestTheLaneContract:
    def _clause(self, key):
        return lane_contract.lane("In Review")["clauses"][key]["text"]

    def test_the_entrance_names_the_stamped_blockers_ready_pull_request(self):
        text = self._clause("entrance")
        sentence = text[text.index("branch-without-pr") - 300:]
        assert "DRE-6447" in text
        assert "stamped `branch-without-pr` blocker" in sentence
        assert "ready pull request" in sentence
        assert "already open and ready for review" in sentence

    def test_the_entrance_says_the_legacy_draft_moves_the_card_nowhere(self):
        text = self._clause("entrance")
        tail = text[text.index("branch-without-pr"):]
        assert "legacy note" in tail
        assert "draft" in tail
        assert "moves the card nowhere" in tail
        assert "Green Light" in tail

    def test_the_writers_text_names_the_sweeps_pull_request(self):
        text = self._clause("writers")
        assert "branch-without-pr" in text
        assert "DRE-6447" in text

    def test_the_sweep_stays_in_who(self):
        assert "reconcile.py" in lane_contract.lane_writers("In Review")
