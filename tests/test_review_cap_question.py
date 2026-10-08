"""RED-first tests: the question a spent review budget asks (DRE-6189).

When the review lane's re-trigger budget is spent on a head, the sweep parks
the card in Green Light (DRE-6181) with a question somebody can answer, and
posts the same question on the pull request as a fix-loop blocker. This card
is the composer alone: `scripts/review_cap_question.py`, pure, building one
`console_escalation.Escalation` from the evidence the sweep already holds and
rendering it through `console_escalation.render` — the one Green Light format
(DRE-3908) — into two texts: the Linear comment (`compose`) and the pull
request blocker (`pr_blocker`).

WHAT THIS PINS, one section per acceptance criterion:

  1. The module does no I/O at import and no network call, imports only
     `console_escalation` and `fix_context`, and runs with `linear_ops`
     and `gh` absent.
  2. Each fixture's comment passes `console_escalation.problems`, parses with
     a recommendation and no choices, opens with the notice and ends with the
     key; the hygiene Green Light lane reads its Recommendation line.
  3. No Green Light prefix is restated in the module or in this file.
  4. Three fixtures, three questions and recommendations; the way back, the
     two ways to a new head, and dropping named for what it is.
  5. The pull request blocker: its first line, its key, the decision example,
     the same three lines — and the real fix-loop readers over a thread.
  6. The Finding for a cap of 0, for the merge-gate budget and for the
     review budget.
  7. Neither text carries a phrase a reader already counts.

Run: cd bureau-pipeline && python3 -m pytest tests/test_review_cap_question.py -v
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import console_escalation as ce  # noqa: E402
import fix_context  # noqa: E402
import review_cap_question as rcq  # noqa: E402

SCRIPT = ROOT / "scripts" / "review_cap_question.py"
RECONCILE = ROOT / "scripts" / "reconcile.py"
HEAD = "4f1c0de9a2b7e6d5c4b3a2918070605040302010"
WORKER_LOGIN = "bureau-worker[bot]"
QA_LOGIN = "bureau-qa[bot]"
GATE_NOTE = (f"⏸️ Merge gate: declined @{HEAD} — required check "
             "tests concluded failure")


def args(**over) -> dict:
    """The keyword arguments the sweep passes, one fixture's worth."""
    base = dict(card="DRE-9001", pr_number=812, head=HEAD,
                tag=rcq.GATE_NUDGE_KEY,
                critic="REQUEST_CHANGES", verifier="none", spent=3,
                hours=6.25, gate_note_line=None, cap=3)
    base.update(over)
    return base


#: The three cases the card names, by what stands on the head.
FIXTURES = {
    "request-changes": args(),
    "approve-red-checks": args(critic="APPROVE", verifier="PASS",
                               gate_note_line=GATE_NOTE),
    "no-verdict": args(tag=rcq.REVIEW_NUDGE_KEY, critic="none", verifier="none",
                       hours=7.5),
}

#: Every shape the sweep can hand over, the three above among them.
ALL = {
    **FIXTURES,
    "verifier-fail": args(critic="APPROVE", verifier="FAIL"),
    "earlier-head-approve": args(critic="APPROVE from earlier head 1a2b3c4",
                                 tag=rcq.REVIEW_NUDGE_KEY),
    "cap-off-gate": args(cap=0, spent=0, hours=0.0, critic="APPROVE",
                         verifier="PASS", gate_note_line=GATE_NOTE),
    "cap-off-review": args(cap=0, spent=0, hours=0.0, tag=rcq.REVIEW_NUDGE_KEY,
                           critic="none"),
    "one-re-trigger": args(spent=1, hours=2.0, cap=1),
}

FORBIDDEN = ("budget exhausted", "holding for a human", "held-for-human",
             "needs-human", "dead-run-requeue", "turn-exhaustion-requeue")

#: Verdict-shaped text is an approval credential (standards/untrusted-content.md).
VERDICT_MARKERS = ("VERDICT:", "QA Critic", "QA Verifier")


def lines(text: str) -> list[str]:
    return text.split("\n")


def declared_recommendation(text: str) -> str:
    """The Recommendation line's own text, read off the line the module's
    prefix opens — never a prefix spelled here."""
    line = next(ln for ln in lines(text)
                if ln.startswith(ce.RECOMMENDATION_PREFIX))
    return line[len(ce.RECOMMENDATION_PREFIX):].strip()


# --------------------------------------------------------------------------- #
# 1. pure, and runs with linear_ops and gh absent                              #
# --------------------------------------------------------------------------- #


def test_module_imports_only_the_two_pure_modules():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert {"console_escalation", "fix_context"} <= imported
    assert imported <= {"__future__", "console_escalation", "fix_context"}


def test_composes_with_linear_ops_gh_and_the_network_absent(tmp_path):
    """A fresh interpreter with `linear_ops` unimportable, no `gh` on PATH,
    and every socket and subprocess refused: the import and both texts still
    work, and nothing pulled `linear_ops` in."""
    probe = f"""
import socket, subprocess, sys
def refuse(*a, **k):
    raise AssertionError("network or subprocess touched")
socket.socket = refuse
socket.create_connection = refuse
subprocess.Popen = refuse
sys.modules["linear_ops"] = None
sys.modules["bureau_read"] = None
sys.path.insert(0, {str(ROOT / "scripts")!r})
import review_cap_question as rcq
kw = {FIXTURES["approve-red-checks"]!r}
assert rcq.compose(**kw) and rcq.pr_blocker(**kw)
assert "linear_ops" not in [m for m in sys.modules if sys.modules[m] is not None]
print("ok")
"""
    env = {"PATH": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-c", probe], env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


# --------------------------------------------------------------------------- #
# 2. the comment conforms, and the readers read it                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(ALL))
def test_comment_conforms_and_parses(name):
    text = rcq.compose(**ALL[name])
    assert ce.problems(text) == []
    esc = ce.parse(text)
    assert esc is not None
    assert esc.recommendation
    assert esc.why
    assert esc.choices == ()


@pytest.mark.parametrize("name", sorted(ALL))
def test_comment_opens_with_the_notice_and_ends_with_the_key(name):
    kw = ALL[name]
    got = lines(rcq.compose(**kw))
    assert got[0] == f"🚨 review-nudge-cap PR #{kw['pr_number']} @{HEAD}:"
    assert got[-1] == f"review-nudge-cap @{HEAD}"


@pytest.mark.parametrize("name", sorted(ALL))
def test_the_three_lines_are_console_escalation_render(name):
    text = rcq.compose(**ALL[name])
    assert ce.render(ce.parse(text)) in text


@pytest.mark.parametrize("name", sorted(ALL))
def test_hygiene_green_light_reads_the_declared_recommendation(name):
    import hygiene_green_light

    text = rcq.compose(**ALL[name])
    got = hygiene_green_light.recommendation("", note=text)
    assert got == declared_recommendation(text)
    esc = ce.parse(text)
    assert got == f"{esc.recommendation}{ce.SEPARATOR}{esc.why}"


def test_the_notice_and_key_match_the_sweep_s_own_spelling():
    """The sweep looks for the notice and counts the key by these words
    (DRE-5231). Where reconcile spells them as literals they must agree."""
    tree = ast.parse(RECONCILE.read_text(encoding="utf-8"))
    ours = {name: getattr(rcq, name) for name in
            ("REVIEW_NUDGE_CAP_KEY", "GATE_NUDGE_KEY", "REVIEW_NUDGE_KEY")}
    seen = set()
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ours
                and isinstance(node.value, ast.Constant)):
            seen.add(node.targets[0].id)
            assert node.value.value == ours[node.targets[0].id]
    assert seen == set(ours)


# --------------------------------------------------------------------------- #
# 3. no prefix restated                                                        #
# --------------------------------------------------------------------------- #


PREFIXES = (ce.FINDING_PREFIX, ce.QUESTION_PREFIX, ce.RECOMMENDATION_PREFIX)


def _string_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


@pytest.mark.parametrize("prefix", PREFIXES)
def test_no_prefix_in_the_module_source(prefix):
    assert prefix not in SCRIPT.read_text(encoding="utf-8")


@pytest.mark.parametrize("prefix", PREFIXES)
def test_no_prefix_in_this_file_s_literals(prefix):
    assert not [s for s in _string_literals(Path(__file__)) if prefix in s]


def test_the_module_renders_through_console_escalation():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "console_escalation.render(" in source
    assert "console_escalation.Escalation(" in source


# --------------------------------------------------------------------------- #
# 4. three cases, three questions                                              #
# --------------------------------------------------------------------------- #


def _esc(name: str) -> ce.Escalation:
    return ce.parse(rcq.compose(**ALL[name]))


def test_three_fixtures_three_questions_and_recommendations():
    escs = [_esc(name) for name in FIXTURES]
    assert len({e.question for e in escs}) == 3
    assert len({e.recommendation for e in escs}) == 3


def test_the_question_follows_the_evidence():
    assert "finding" in _esc("request-changes").question.lower()
    assert "red check" in _esc("approve-red-checks").question.lower()
    assert "read the pull request" in _esc("no-verdict").question.lower()
    # A Verifier FAIL stands the same way a REQUEST_CHANGES does.
    assert _esc("verifier-fail").question == _esc("request-changes").question


def test_the_gate_note_is_quoted_in_the_finding():
    assert GATE_NOTE in _esc("approve-red-checks").finding


@pytest.mark.parametrize("name", sorted(ALL))
def test_no_question_offers_a_decision_to_waive_a_check_or_merge(name):
    question = _esc(name).question.lower()
    assert "operator decision" not in question
    for text in (rcq.compose(**ALL[name]), rcq.pr_blocker(**ALL[name])):
        assert "waive" not in text.lower()
        assert "merge it anyway" not in text.lower()


@pytest.mark.parametrize("name", sorted(ALL))
def test_the_why_ends_by_naming_the_two_ways_to_a_new_head(name):
    why = _esc(name).why
    assert why.endswith(rcq.NEW_HEAD_WAYS)
    tail = rcq.NEW_HEAD_WAYS
    assert "push a fix" in tail
    assert "Operator decision" in tail
    assert "comment on the pull request" in tail
    assert "starts the fix agent once" in tail
    assert tail.index("push a fix") < tail.index("Operator decision")


@pytest.mark.parametrize("name", sorted(ALL))
def test_each_body_carries_the_way_back_and_names_dropping(name):
    for text in (rcq.compose(**ALL[name]), rcq.pr_blocker(**ALL[name])):
        flat = " ".join(text.split())
        assert ("comes back to In Review on its own once a new head lands "
                "on the pull request") in flat
        assert "the decision alone moves nothing" in flat
        assert "closing the pull request and canceling the card by hand" in flat
        assert "no comment on this card or the pull request" in flat


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_each_question_names_dropping_for_what_it_is(name):
    question = _esc(name).question
    assert "dropped" in question
    assert "closing the pull request and canceling the card by hand" in question


# --------------------------------------------------------------------------- #
# 5. the pull request blocker, read by the real fix loop                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(ALL))
def test_blocker_shape(name):
    kw = ALL[name]
    text = rcq.pr_blocker(**kw)
    got = lines(text)
    assert got[0] == f"🛑 review-nudge-cap PR #{kw['pr_number']} @{HEAD}:"
    assert got[0].startswith(fix_context.BLOCKER_PREFIX)
    assert got[-1] == f"review-nudge-cap @{HEAD}"
    assert fix_context.DECISION_EXAMPLE in text
    assert ce.problems(text) == []
    assert ce.parse(text) == ce.parse(rcq.compose(**kw))


def comment(login, body, created):
    """A GitHub issue comment as the REST API returns it."""
    return {"user": {"login": login,
                     "type": "Bot" if login.endswith("[bot]") else "User"},
            "body": body, "created_at": created}


def thread(blocker: str, *, with_verdict: bool) -> list:
    out = []
    if with_verdict:
        out.append(comment(QA_LOGIN, "🔎 QA Critic — VERDICT: REQUEST_CHANGES",
                           "2026-10-08T01:00:00Z"))
    out.append(comment(WORKER_LOGIN, blocker, "2026-10-08T02:00:00Z"))
    out.append(comment("sid-ceo", "**Operator decision** — fix the finding.",
                       "2026-10-08T03:00:00Z"))
    return out


@pytest.mark.parametrize("with_verdict", [False, True])
@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_the_fix_loop_reads_the_blocker_and_the_answer(name, with_verdict):
    blocker = rcq.pr_blocker(**FIXTURES[name])
    comments = thread(blocker, with_verdict=with_verdict)
    assert fix_context.prior_blockers(comments, WORKER_LOGIN) == [
        comments[-2]]
    assert fix_context.operator_decision(comments, WORKER_LOGIN) is comments[-1]
    assert fix_context.decision_trigger(comments, WORKER_LOGIN) == (
        fix_context.TRIGGER_PROCEED, fix_context.TRIGGER_STANDING)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_a_blocker_opening_with_the_card_notice_is_no_blocker(name):
    """The control: the same thread with the first line opening 🚨 holds no
    blocker, so the decision after it answers nothing."""
    blocker = rcq.pr_blocker(**FIXTURES[name])
    swapped = "🚨" + blocker[len(fix_context.BLOCKER_PREFIX):]
    comments = thread(swapped, with_verdict=False)
    assert fix_context.prior_blockers(comments, WORKER_LOGIN) == []
    assert fix_context.decision_trigger(comments, WORKER_LOGIN) == (
        fix_context.TRIGGER_SKIP, fix_context.SKIP_NO_BLOCKER)


def test_the_blocker_says_the_card_waits_in_green_light():
    flat = " ".join(rcq.pr_blocker(**FIXTURES["request-changes"]).split())
    assert "waits in Green Light" in flat


# --------------------------------------------------------------------------- #
# 6. the finding: which budget, and a cap of 0                                 #
# --------------------------------------------------------------------------- #


def test_cap_off_says_so_in_the_receipt_s_words():
    for name, rest in (("cap-off-gate", "the gate has not merged it."),
                       ("cap-off-review",
                        "no critic verdict is bound to this head.")):
        finding = _esc(name).finding
        assert "the cap is off" in finding
        assert ("REVIEW_NUDGE_CAP is 0, so the sweep re-triggers nothing, "
                f"and {rest}") in finding
        assert "stall" not in finding


def test_merge_gate_budget_says_the_gate_declined():
    finding = _esc("request-changes").finding
    assert "merge-gate re-trigger budget" in finding
    assert ("the sweep spent 3 re-triggers over 6.2h on the merge gate, and "
            "the gate declined to merge on those re-triggers.") in finding
    assert "no critic verdict bound to this head came back" not in finding


def test_review_budget_says_no_verdict_came_back():
    finding = _esc("no-verdict").finding
    assert "review re-trigger budget" in finding
    assert ("the sweep spent 3 re-triggers over 7.5h on the review, and no "
            "critic verdict bound to this head came back.") in finding
    assert "the gate declined" not in finding


def test_the_finding_names_the_head_and_what_stands_on_it():
    finding = _esc("approve-red-checks").finding
    assert HEAD[:7] in finding
    assert "critic APPROVE" in finding
    assert "verifier PASS" in finding
    assert "stall, not a slow round" in finding
    assert "stopped re-triggering" in finding


def test_one_re_trigger_is_singular():
    assert "1 re-trigger over 2.0h" in _esc("one-re-trigger").finding


def test_an_unknown_tag_is_refused():
    with pytest.raises(ValueError):
        rcq.compose(**args(tag="gate-nudge-cap"))


# --------------------------------------------------------------------------- #
# 7. no phrase a reader already counts                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(ALL))
def test_neither_text_carries_a_counted_phrase(name):
    for text in (rcq.compose(**ALL[name]), rcq.pr_blocker(**ALL[name])):
        for phrase in FORBIDDEN + VERDICT_MARKERS:
            assert phrase not in text
