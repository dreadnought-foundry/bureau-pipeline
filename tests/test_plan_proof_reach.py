"""A PROOF criterion outside the declared reach, or one reading an unblocked
operator step, is a send-back at the first critic (DRE-6606).

Every one of these was readable off the plan before the CEO approved it:

* DRE-6543 asked for repository variables the read token answers 403 to;
* DRE-6431 row 6 asked for every repo in `config/repo-map.json`, two of which
  sit in organizations the read token cannot reach;
* DRE-6042 asked the record to quote the sweep's `proof-release:` line on a
  ready release, a line the sweep did not print until DRE-6516;
* DRE-6543 rows 3 and 4 read agent-bureau's `BUREAU_DOCKER_RUNS_ON` as set
  while setting it was the open operator card DRE-6478, with no `blockedBy`
  between the two.

The criteria below are quoted from those cards as they were written, read off
Linear on 2026-10-11. DRE-6042's is replayed against a declaration fixture with
no `proof-release:` entry in `log_lines` — the state the card was written in —
and against today's declaration, which keeps the line, so both readings are
asserted.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_proof_reach.py -v
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS))

import linear_ops  # noqa: E402
import plan_critic  # noqa: E402
import plan_proof_reach as ppr  # noqa: E402
import plan_seam_gate  # noqa: E402
import proof_reach  # noqa: E402
import proof_record  # noqa: E402
import routing_verdict  # noqa: E402

#: DRE-6543's fourth row, as written on the card.
DRE_6543_VARIABLES = (
    "Live: portico `BUREAU_RUNS_ON` and `BUREAU_CI_RUNS_ON` read back "
    "`[\"runs-on/runner=4cpu-linux-arm64\"]` and agent-bureau "
    "`BUREAU_DOCKER_RUNS_ON` reads back `[\"runs-on/runner=2cpu-linux-x64\"]` "
    "after the deploy — each quoted with its PT read time.")

#: DRE-6431 row 6, as written on the card.
DRE_6431_ROW_6 = (
    "Live: for each repo in `config/repo-map.json`, the pipeline ref its "
    "reconcile stub runs (`main` or `stable`) and the bureau-pipeline commit "
    "that ref resolved to at the time of reading, and whether that commit "
    "carries DRE-6427's merge; for a repo whose commit does not yet carry it, "
    "the record names the Backlog operator cards in that repo the pass stamped "
    "and says the channel's next move carries the sweep change to them, so the "
    "CEO can see where the change has and has not arrived. Every repo is "
    "recorded; the row is met on the recording.")

#: DRE-6042's `proof-release:` row, as written on the card.
DRE_6042_RELEASE = (
    "The record quotes the sweep's `proof-release:` line for this card and the "
    "surfaces `.github/bureau/release.json` declared at the merged commit, and "
    "the release reading it records is the gate's own, not this card's "
    "expectation.")

#: The closing row every proof card carries.
CLOSING = "The CEO closes this card after reading the record."

#: DRE-6478 as the card's replay writes it: the token backticked in the title.
DRE_6478 = {
    "identifier": "DRE-6478",
    "title": ("OPERATOR: agent-bureau: set `BUREAU_DOCKER_RUNS_ON` in GitHub "
              "to the record's value"),
    "body": "Set the variable.",
    "labels": ["no-code"],
    "state": "Todo",
}

#: DRE-6478 as it stands on the board, read off Linear on 2026-10-11: the
#: token bare in the title, the `[OPERATOR]` prefix, the stamped marks.
DRE_6478_AS_FILED = {
    "identifier": "DRE-6478",
    "title": ("[OPERATOR] agent-bureau: set BUREAU_DOCKER_RUNS_ON in GitHub to "
              "the routing record's value, RunsOn 2cpu-linux-x64"),
    "body": "`gh variable list` shows `BUREAU_DOCKER_RUNS_ON`.",
    "labels": ["operator-step", "no-code", "agent:ops", "repo:agent-bureau"],
    "state": "Todo",
}

PROOF_LABELS = ["repo:agent-bureau", "agent:ops", "no-code"]


def body(*criteria: str, preamble: str = "") -> str:
    lines = [preamble or "What the proof run observes.", "",
             "## Acceptance criteria", ""]
    lines += [f"- [ ] {c}" for c in criteria]
    lines += ["", "**Blocked by:** DRE-1"]
    return "\n".join(lines)


def proof(ident: str, *criteria: str, blocked_by=(), state="Todo",
          preamble: str = "", title: str = "") -> dict:
    return {
        "identifier": ident,
        "title": title or f"PROOF: the thing {ident} proves",
        "body": body(*criteria, preamble=preamble),
        "labels": list(PROOF_LABELS),
        "blocked_by": list(blocked_by),
        "state": state,
    }


def card(ident: str, title: str, *, labels=(), state="Todo", text="",
         blocked_by=()) -> dict:
    return {"identifier": ident, "title": title, "body": text,
            "labels": list(labels), "blocked_by": list(blocked_by),
            "state": state}


def split(records):
    """`children-json` and `children-detail` as the two reads hand them over."""
    cards = [{"identifier": r["identifier"], "body": r["body"],
              "labels": r["labels"], "state": r["state"]} for r in records]
    detail = [{"identifier": r["identifier"], "title": r["title"],
               "body": r["body"], "labels": r["labels"],
               "blocked_by": r["blocked_by"]} for r in records]
    return cards, detail


def read(records, operator_cards=(), **kw):
    kw.setdefault("caller_slug", "agent-bureau")
    cards, detail = split(records)
    return ppr.findings(cards, detail, operator_cards, **kw)


def what(rid: str) -> str:
    return next(r for r in proof_reach.load()["readings"]
                if r["id"] == rid)["what"]


def without_release_line() -> dict:
    doc = proof_reach.load()
    doc["log_lines"] = [e for e in doc["log_lines"] if e["tag"] != "proof-release:"]
    return doc


def operator_lines(lines):
    return [l for l in lines if "reads the result of operator step" in l]


class TheSentences(unittest.TestCase):
    """The two sentences, pinned as the card writes them."""

    def test_the_reach_sentence(self):
        self.assertEqual(
            ppr.FINDING_REACH,
            "{proof}: criterion “{criterion}” asks for a reading the proof run "
            "cannot make — {what} ({why}); write it as what the run can "
            "observe, or drop it.")

    def test_the_operator_sentence(self):
        self.assertEqual(
            ppr.FINDING_OPERATOR,
            "{proof}: criterion “{criterion}” reads the result of operator step "
            "{operator} (“{title}”) and is not blocked by it — add the "
            "blockedBy relation, or reword the criterion.")


class TheReplays(unittest.TestCase):

    def test_dre_6543_the_variable_row(self):
        lines = read([proof("DRE-6543", DRE_6543_VARIABLES, CLOSING)]).lines
        self.assertEqual(1, len(lines), lines)
        self.assertTrue(lines[0].startswith("DRE-6543: criterion “"), lines[0])
        self.assertIn(f"“{proof_record._cut(DRE_6543_VARIABLES, 70)}”", lines[0])
        self.assertIn(what("repo-variable"), lines[0])
        self.assertTrue(lines[0].endswith("write it as what the run can observe, "
                                          "or drop it."), lines[0])

    def test_dre_6431_row_6(self):
        lines = read([proof("DRE-6431", DRE_6431_ROW_6, CLOSING)],
                     caller_slug="bureau-pipeline").lines
        self.assertEqual(1, len(lines), lines)
        self.assertIn(f"“{proof_record._cut(DRE_6431_ROW_6, 70)}”", lines[0])
        self.assertIn(what("other-organization"), lines[0])
        self.assertIn("atlas", lines[0])
        self.assertIn("deltasolv", lines[0])

    def test_dre_6042_against_the_declaration_it_was_written_under(self):
        lines = read([proof("DRE-6042", DRE_6042_RELEASE, CLOSING)],
                     doc=without_release_line()).lines
        self.assertEqual(1, len(lines), lines)
        self.assertIn(f"“{proof_record._cut(DRE_6042_RELEASE, 70)}”", lines[0])
        self.assertIn("the `proof-release:` line, which nothing keeps", lines[0])

    def test_dre_6042_against_todays_declaration_is_within_reach(self):
        # Kept since DRE-6516: the check must say so.
        self.assertEqual([], read([proof("DRE-6042", DRE_6042_RELEASE, CLOSING)]).lines)

    def test_dre_6543_and_dre_6478_from_the_operator_cards_file(self):
        criterion = ("Live: agent-bureau `BUREAU_DOCKER_RUNS_ON` reads back "
                     "`[\"runs-on/runner=2cpu-linux-x64\"]`, quoted.")
        lines = operator_lines(read([proof("DRE-6543", criterion)], [DRE_6478]).lines)
        self.assertEqual(1, len(lines), lines)
        self.assertEqual(lines[0], ppr.FINDING_OPERATOR.format(
            proof="DRE-6543", criterion=proof_record._cut(criterion, 70),
            operator="DRE-6478", title=DRE_6478["title"]))

    def test_dre_6478_in_blocked_by_clears_it(self):
        criterion = "Live: agent-bureau `BUREAU_DOCKER_RUNS_ON` reads back the value."
        lines = read([proof("DRE-6543", criterion, blocked_by=["DRE-6478"])],
                     [DRE_6478]).lines
        self.assertEqual([], operator_lines(lines))

    def test_dre_6478_as_filed_names_the_token_bare(self):
        criterion = "Live: agent-bureau `BUREAU_DOCKER_RUNS_ON` reads back the value."
        lines = operator_lines(read([proof("DRE-6543", criterion)],
                                    [DRE_6478_AS_FILED]).lines)
        self.assertEqual(1, len(lines), lines)
        self.assertIn("DRE-6478", lines[0])


class WhatAnOperatorCardIs(unittest.TestCase):

    CRITERION = "Live: `BUREAU_DOCKER_RUNS_ON` reads back the record's value."

    def one(self, records, operator_cards=()):
        return operator_lines(read(records, operator_cards).lines)

    def test_a_sibling_found_by_label(self):
        for label in ("no-code", "agent:ops", "needs-human", "operator-step"):
            sib = card("DRE-7001", "agent-bureau: set `BUREAU_DOCKER_RUNS_ON`",
                       labels=[label])
            got = self.one([sib, proof("DRE-7009", self.CRITERION)])
            self.assertEqual(1, len(got), (label, got))
            self.assertIn("DRE-7001", got[0])
            self.assertEqual([], self.one([sib, proof("DRE-7009", self.CRITERION,
                                                      blocked_by=["DRE-7001"])]))

    def test_a_sibling_found_by_the_operator_title_convention_alone(self):
        convention = next(c for c in routing_verdict.title_conventions()
                          if c["verdict"] == "OPERATOR")
        title = convention["example"] + " — set `BUREAU_DOCKER_RUNS_ON`"
        self.assertEqual("OPERATOR", routing_verdict.title_verdict(title))
        sib = card("DRE-7002", title, labels=["agent:devops"])
        got = self.one([sib, proof("DRE-7009", self.CRITERION)])
        self.assertEqual(1, len(got), got)
        self.assertIn("DRE-7002", got[0])
        self.assertEqual([], self.one([sib, proof("DRE-7009", self.CRITERION,
                                                  blocked_by=["DRE-7002"])]))

    def test_a_build_card_is_not_one(self):
        sib = card("DRE-7003", "agent-bureau: read `BUREAU_DOCKER_RUNS_ON`",
                   labels=["agent:devops"])
        self.assertEqual([], self.one([sib, proof("DRE-7009", self.CRITERION)]))

    def test_a_plain_word_in_backticks_is_not_a_token(self):
        # DRE-6431 row 6 backticks `main` and `stable`; an operator title that
        # backticks the same word is not the step the criterion reads.
        criterion = ("Live: for each repo, the pipeline ref its stub runs "
                     "(`main` or `stable`) is quoted, with `v4` and `repo-map.json`.")
        for word in ("stable", "main", "v4", "repo-map.json"):
            sib = card("DRE-7005", f"[OPERATOR] agent-bureau: pin the stub to `{word}`",
                       labels=["operator-step", "no-code"])
            self.assertEqual([], self.one([sib, proof("DRE-7009", criterion)]), word)
            self.assertEqual([], self.one([proof("DRE-7009", criterion)], [sib]), word)
        # A variable-style name in the same title still is.
        sib = card("DRE-7005", "[OPERATOR] agent-bureau: pin `stable` and set "
                   "`BUREAU_DOCKER_RUNS_ON`", labels=["operator-step", "no-code"])
        got = self.one([sib, proof("DRE-7009", criterion + " " + self.CRITERION)])
        self.assertEqual(1, len(got), got)
        self.assertIn("DRE-7005", got[0])

    def test_naming_the_id_is_the_same_as_sharing_the_token(self):
        sib = card("DRE-7004", "agent-bureau: rotate the key", labels=["no-code"])
        criterion = "Live: the key DRE-7004 rotated answers the probe."
        got = self.one([sib, proof("DRE-7009", criterion)])
        self.assertEqual(1, len(got), got)
        self.assertIn("DRE-7004", got[0])
        self.assertEqual([], self.one([sib, proof("DRE-7009", criterion,
                                                  blocked_by=["DRE-7004"])]))
        # A longer id is a different card.
        self.assertEqual([], self.one([sib, proof("DRE-7009",
                                                  "Live: DRE-70041 answers.")]))

    def test_a_cross_epic_card_in_the_file_and_a_sibling_behave_the_same(self):
        as_file = self.one([proof("DRE-7009", self.CRITERION)], [DRE_6478])
        sib = dict(DRE_6478, blocked_by=[])
        as_sibling = self.one([sib, proof("DRE-7009", self.CRITERION)])
        self.assertEqual(as_file, as_sibling)
        self.assertEqual(1, len(as_file))


class AProofIsNeverItsOwnOperatorCard(unittest.TestCase):

    RELEASE = "The record quotes the sweep's `proof-release:` line for this card."

    def test_its_own_body_carries_the_token(self):
        own = proof("DRE-7009", self.RELEASE,
                    preamble="The sweep prints `proof-release:` when it reads.")
        self.assertEqual([], read([own]).lines)

    def test_another_open_proof_in_the_file(self):
        other = {"identifier": "DRE-7100", "title": "PROOF: `proof-release:` printed",
                 "body": "`proof-release:`", "labels": ["no-code", "agent:ops"],
                 "state": "Todo"}
        self.assertEqual([], read([proof("DRE-7009", self.RELEASE)], [other]).lines)

    def test_a_sibling_proof(self):
        other = card("DRE-7101", "PROOF: `proof-release:` printed",
                     labels=["no-code", "agent:ops"], text="`proof-release:`")
        self.assertEqual([], read([other, proof("DRE-7009", self.RELEASE)]).lines)

    def test_a_done_operator_card(self):
        done = dict(DRE_6478, state="Done")
        criterion = "Live: `BUREAU_DOCKER_RUNS_ON` reads back the record's value."
        self.assertEqual([], operator_lines(
            read([proof("DRE-7009", criterion)], [done]).lines))
        for state in ("Canceled", "Duplicate"):
            self.assertEqual([], operator_lines(read(
                [proof("DRE-7009", criterion)], [dict(DRE_6478, state=state)]).lines))

    def test_a_token_only_in_the_operator_cards_body(self):
        op = card("DRE-7102", "agent-bureau: set the runner variable",
                  labels=["no-code"], text="Set `BUREAU_DOCKER_RUNS_ON`.")
        criterion = "Live: `BUREAU_DOCKER_RUNS_ON` reads back the record's value."
        self.assertEqual([], operator_lines(read([op, proof("DRE-7009", criterion)]).lines))


class WithinReach(unittest.TestCase):

    def test_github_runs_linear_comments_and_a_declared_local_page(self):
        p = proof("DRE-7200",
                  "Read through `gh api repos/dreadnought-foundry/agent-bureau/"
                  "actions/runs/<id>`, the run's status is `completed`.",
                  "The Linear card's comments carry the `🔬 proof-observed` receipt.",
                  "Local screen: the `board` page shows the card in Hand-work.",
                  CLOSING)
        got = read([p], local={"pages": {"board": "/"}})
        self.assertEqual([], got.lines)
        self.assertEqual(3, got.criteria)
        self.assertEqual(1, got.proofs)

    def test_an_undeclared_local_page_is_out_of_reach(self):
        p = proof("DRE-7201", "Local screen: the `settings` page shows the toggle.")
        self.assertEqual(1, len(read([p], local={"pages": {"board": "/"}}).lines))

    def test_a_delivered_proof_is_not_read(self):
        for state in ("Done", "Canceled", "Duplicate"):
            p = proof("DRE-7202", DRE_6543_VARIABLES, state=state)
            got = read([p])
            self.assertEqual([], got.lines, state)
            self.assertEqual(0, got.proofs, state)

    def test_a_closing_row_is_skipped(self):
        row = "The CEO closes this card after reading the record, through `make db-read`."
        self.assertTrue(proof_record.is_closing_row(row))
        self.assertTrue(proof_reach.out_of_reach(row, caller_slug="agent-bureau"))
        got = read([proof("DRE-7203", row)])
        self.assertEqual([], got.lines)
        self.assertEqual(0, got.criteria)

    def test_only_the_acceptance_section_is_read(self):
        p = proof("DRE-7204", "The Linear card's comments carry the receipt.",
                  preamble="- [ ] Read `make db-read` (a note, not a criterion).")
        self.assertEqual([], read([p]).lines)

    def test_a_non_proof_card_is_not_read(self):
        c = card("DRE-7205", "agent-bureau: the variable",
                 labels=["agent:devops"], text=body(DRE_6543_VARIABLES))
        self.assertEqual([], read([c]).lines)


# --------------------------------------------------------------------------- #
# the CLI                                                                      #
# --------------------------------------------------------------------------- #

class _Cli(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def path(self, name: str) -> str:
        return os.path.join(self.tmp, name)

    def write(self, name: str, value) -> str:
        with open(self.path(name), "w", encoding="utf-8") as fh:
            fh.write(value if isinstance(value, str) else json.dumps(value))
        return self.path(name)

    def text(self, name: str) -> str:
        with open(self.path(name), encoding="utf-8") as fh:
            return fh.read()

    def run_findings(self, records, *extra, operator_cards=None):
        cards, detail = split(records)
        argv = ["findings", "--detail-file", self.write("detail.json", detail),
                "--caller", "agent-bureau",
                "--proof-local", self.path("no-such-proof-local.json")]
        if operator_cards is not None:
            argv += ["--operator-cards-file", self.write("ops.json", operator_cards)]
        argv += list(extra)
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(cards))), \
                mock.patch.object(sys, "stdout", out):
            code = ppr.main(argv)
        return code, out.getvalue()


class TheFindingsCommand(_Cli):

    def test_it_appends_to_the_seam_file_and_the_gate_lists_both(self):
        seam = ("DRE-3215: wait on observing DRE-3166 live — that is a child "
                "epic under this one, not a later step.")
        structural = self.write("structural.txt", seam + "\n")
        code, out = self.run_findings([proof("DRE-6543", DRE_6543_VARIABLES)],
                                      "--structural-file", structural)
        self.assertEqual(0, code)
        lines = [l for l in self.text("structural.txt").splitlines() if l]
        self.assertEqual(2, len(lines), lines)
        self.assertEqual(seam, lines[0])
        self.assertTrue(lines[1].startswith("DRE-6543: criterion"), lines[1])
        self.assertIn(lines[1], out)

        result = self.write("result.md", plan_critic.result_line(plan_critic.PASS) + "\n")
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(0, plan_seam_gate.main(
                ["gate", "--result-file", result, "--structural-file", structural]))
        rewritten = self.text("result.md")
        verdict, _ = plan_critic.read_result(rewritten)
        self.assertEqual(plan_critic.SEND_BACK, verdict)
        found = plan_critic.all_findings(rewritten)
        self.assertEqual(2, len(found), found)
        self.assertTrue(found[0].endswith(seam), found)
        self.assertTrue(found[1].startswith("DRE-6543: criterion"), found)
        self.assertIn(lines[1], rewritten)

    def test_an_empty_structural_file_stays_empty(self):
        structural = self.write("structural.txt", "")
        code, out = self.run_findings([proof("DRE-7300", "The Linear card's comments "
                                             "carry the receipt.", CLOSING)],
                                      "--structural-file", structural)
        self.assertEqual(0, code)
        self.assertEqual("", self.text("structural.txt"))
        self.assertIn("no criterion out of reach — 1 criteria read on 1 proof card(s)", out)

    def test_it_never_creates_a_structural_file_with_nothing_to_say(self):
        self.run_findings([proof("DRE-7301", "The receipt is on the card.")],
                          "--structural-file", self.path("absent.txt"))
        self.assertFalse(os.path.exists(self.path("absent.txt")))

    def test_it_appends_a_block_to_an_existing_note(self):
        note = self.write("note.md", "Mechanical findings\n")
        self.run_findings([proof("DRE-6543", DRE_6543_VARIABLES)], "--note-file", note)
        text = self.text("note.md")
        self.assertTrue(text.startswith("Mechanical findings\n"), text)
        self.assertIn(ppr.NOTE_HEADING, text)
        self.assertIn("- DRE-6543: criterion", text)

    def test_it_never_creates_a_note(self):
        self.run_findings([proof("DRE-6543", DRE_6543_VARIABLES)],
                          "--note-file", self.path("never.md"))
        self.assertFalse(os.path.exists(self.path("never.md")))

    def test_the_operator_cards_file_is_read(self):
        criterion = "Live: `BUREAU_DOCKER_RUNS_ON` reads back the record's value."
        code, out = self.run_findings([proof("DRE-6543", criterion)],
                                      operator_cards=[DRE_6478])
        self.assertEqual(0, code)
        self.assertIn("operator step DRE-6478", out)

    def test_an_unreadable_input_exits_zero(self):
        argv = ["findings", "--detail-file", self.path("missing.json"),
                "--operator-cards-file", self.write("ops.json", "not json"),
                "--caller", "no-such-repo"]
        with mock.patch.object(sys, "stdin", io.StringIO("nope")), \
                mock.patch.object(sys, "stdout", io.StringIO()), \
                mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(0, ppr.main(argv))

    def test_an_unknown_caller_is_said_and_the_operator_rule_still_runs(self):
        cards, detail = split([proof("DRE-6543", DRE_6543_VARIABLES)])
        argv = ["findings", "--detail-file", self.write("detail.json", detail),
                "--operator-cards-file", self.write("ops.json", [DRE_6478]),
                "--caller", "no-such-repo"]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(cards))), \
                mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err):
            self.assertEqual(0, ppr.main(argv))
        self.assertIn("no-such-repo", err.getvalue())
        self.assertIn("operator step DRE-6478", out.getvalue())

    def test_the_proof_local_file_is_read_from_the_checkout(self):
        local = self.write("proof-local.json", {"pages": {"board": "/"}})
        cards, detail = split([proof("DRE-7302", "Local screen: the `board` page "
                                     "shows the card.")])
        argv = ["findings", "--detail-file", self.write("detail.json", detail),
                "--caller", "agent-bureau", "--proof-local", local]
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(cards))), \
                mock.patch.object(sys, "stdout", out):
            ppr.main(argv)
        self.assertIn("no criterion out of reach", out.getvalue())


def _node(ident, title, labels, state="Todo", description="", children=0):
    return {"identifier": ident, "title": title, "description": description,
            "labels": {"nodes": [{"name": l} for l in labels]},
            "state": {"name": state},
            "children": {"nodes": [{"id": f"c{i}"} for i in range(children)]}}


class TheOperatorCardsCommand(_Cli):

    def answer(self, nodes):
        calls = []

        def gql(query, variables=None):
            calls.append((query, variables))
            return {"issues": {"nodes": copy.deepcopy(nodes),
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        return calls, gql

    def test_it_writes_the_operator_cards_and_drops_a_proof(self):
        calls, gql = self.answer([
            _node("DRE-6478", DRE_6478["title"], ["no-code"], description="body"),
            _node("DRE-7400", "[OPERATOR] portico: cut the key", ["operator-step"]),
            _node("DRE-7401", "PROOF: the key was cut", ["no-code", "agent:ops"]),
        ])
        out = self.path("ops.json")
        with mock.patch.object(linear_ops, "gql", gql), \
                mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(0, ppr.main(["operator-cards", "--out", out]))
        written = json.loads(self.text("ops.json"))
        self.assertEqual(["DRE-6478", "DRE-7400"], [r["identifier"] for r in written])
        self.assertEqual({"identifier", "title", "body", "labels", "state"},
                         set(written[0]))
        self.assertEqual(["no-code"], written[0]["labels"])
        self.assertEqual("body", written[0]["body"])
        self.assertEqual("Todo", written[0]["state"])
        self.assertTrue(calls)

    def test_it_asks_for_every_operator_label_and_open_cards_only(self):
        calls, gql = self.answer([])
        with mock.patch.object(linear_ops, "gql", gql), \
                mock.patch.object(sys, "stdout", io.StringIO()):
            ppr.main(["operator-cards", "--out", self.path("ops.json")])
        asked = set()
        for query, variables in calls:
            asked |= set((variables or {}).get("labels") or [])
        mapped = {l for l, v in routing_verdict.label_map().items() if v == "OPERATOR"}
        self.assertEqual(mapped | {"needs-human", "operator-step"}, asked)
        self.assertIn("completed", calls[0][0])
        self.assertIn("canceled", calls[0][0])

    def test_a_closed_card_and_an_epic_are_dropped(self):
        calls, gql = self.answer([
            _node("DRE-7402", "[OPERATOR] set it", ["operator-step"], state="Done"),
            _node("DRE-7403", "[EPIC] the parked epic", ["needs-human"], children=2),
            _node("DRE-7404", "[OPERATOR] set the other", ["operator-step"]),
        ])
        with mock.patch.object(linear_ops, "gql", gql), \
                mock.patch.object(sys, "stdout", io.StringIO()):
            ppr.main(["operator-cards", "--out", self.path("ops.json")])
        self.assertEqual(["DRE-7404"],
                         [r["identifier"] for r in json.loads(self.text("ops.json"))])

    def test_a_failed_read_writes_an_empty_list_and_exits_zero(self):
        def gql(query, variables=None):
            raise linear_ops.LinearError("boom")
        err = io.StringIO()
        with mock.patch.object(linear_ops, "gql", gql), \
                mock.patch.object(sys, "stdout", io.StringIO()), \
                mock.patch.object(sys, "stderr", err):
            self.assertEqual(0, ppr.main(["operator-cards", "--out",
                                          self.path("ops.json")]))
        self.assertEqual([], json.loads(self.text("ops.json")))
        self.assertIn("boom", err.getvalue())


# --------------------------------------------------------------------------- #
# the sandbox press sent to a person (DRE-7779)                                #
# --------------------------------------------------------------------------- #

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "sandbox-press-proof-card.json")
CARD_QUALITY = os.path.join(ROOT, "standards", "card-quality.md")
SANDBOX_LIST = "The sandbox presses, the one list (DRE-7772):"
_LIST_BULLET = re.compile(r"^- `([^`]+)`")


def fixture(*, labels=None) -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        rec = json.load(fh)
    if labels is not None:
        rec["labels"] = list(labels)
    return rec


def fixture_criteria() -> list[str]:
    """The fixture's seven criteria, numbered 1 to 7 as the card numbers them."""
    return ppr.criteria(fixture()["body"])


def sandbox_lines(lines):
    return [l for l in lines if "sends a press on the sandbox project to a person" in l]


def reach_lines(lines):
    return [l for l in lines if "asks for a reading the proof run cannot make" in l]


def sandbox_line(n: int) -> str:
    """The finding the fixture's criterion `n` gives."""
    return ppr.FINDING_SANDBOX_PRESS.format(
        proof=fixture()["identifier"],
        criterion=proof_record._cut(fixture_criteria()[n - 1], ppr.CUT))


def press_lines(lines):
    return [l for l in lines if "waits on the operator's press" in l]


def press_line(n: int) -> str:
    """The operator's-press finding the fixture's criterion `n` gives (DRE-7950)."""
    return ppr.FINDING_PERSON_PRESS.format(
        proof=fixture()["identifier"],
        criterion=proof_record._cut(fixture_criteria()[n - 1], ppr.CUT))


def listed_operations(text: str) -> list[str]:
    """The backticked operations of the bullets after the list's line."""
    lines = text.splitlines()
    start = lines.index(SANDBOX_LIST) + 1
    out = []
    for line in lines[start:]:
        found = _LIST_BULLET.match(line)
        if not found:
            break
        out.append(found.group(1))
    return out


def copy_differs(listed: list[str]) -> str:
    """Empty when `SANDBOX_PRESSES` copies the list, else the operation that differs."""
    ours = [op for op, _ in ppr.SANDBOX_PRESSES]
    for i in range(max(len(ours), len(listed))):
        mine = ours[i] if i < len(ours) else None
        theirs = listed[i] if i < len(listed) else None
        if mine != theirs:
            return (f"bullet {i + 1}: the standard lists {theirs!r} and "
                    f"`SANDBOX_PRESSES` carries {mine!r}")
    return ""


class TheSandboxPressSentence(unittest.TestCase):

    def test_the_sentence(self):
        self.assertEqual(
            ppr.FINDING_SANDBOX_PRESS,
            "{proof}: criterion “{criterion}” sends a press on the sandbox project "
            "to a person — write it as a `Sandbox press:` step the proof run makes "
            "as the console proof-reader, or drop it.")

    def test_the_names(self):
        self.assertEqual(("agent-bureau-demo", "Agent Bureau Sandbox",
                          "the sandbox project", "the sandbox"), ppr.SANDBOX_NAMES)


class TheListIsCopied(unittest.TestCase):

    def standard(self) -> str:
        with open(CARD_QUALITY, encoding="utf-8") as fh:
            return fh.read()

    def test_the_operations_are_the_standards_bullets_in_order(self):
        listed = listed_operations(self.standard())
        self.assertEqual(6, len(listed), listed)
        self.assertEqual("", copy_differs(listed))

    def test_a_changed_bullet_is_named(self):
        changed = self.standard().replace("- `markTaskDone` —", "- `finishTask` —", 1)
        said = copy_differs(listed_operations(changed))
        self.assertIn("finishTask", said)
        self.assertIn("markTaskDone", said)

    def test_each_pattern_compiles_and_names_its_own_fixture_press(self):
        crit = fixture_criteria()
        own = {"createTask": crit[1], "answerDeskPermission": crit[2],
               "markTaskDone": crit[3], "makeTaskCard": crit[4]}
        for op, pattern in ppr.SANDBOX_PRESSES:
            compiled = re.compile(pattern, re.IGNORECASE)
            for n, text in enumerate(crit, 1):
                hit = compiled.search(text) is not None
                if op == "createTask" and n == 2:
                    # Criterion 2 carries the operation itself, not the words.
                    continue
                self.assertEqual(own.get(op) == text, hit, (op, n, text))
        self.assertEqual(["createTask"], ppr.sandbox_presses(crit[1]))
        self.assertEqual(["answerDeskPermission"], ppr.sandbox_presses(crit[2]))
        self.assertEqual(["markTaskDone"], ppr.sandbox_presses(crit[3]))
        self.assertEqual(["makeTaskCard"], ppr.sandbox_presses(crit[4]))
        self.assertEqual([], ppr.sandbox_presses(crit[5]))
        self.assertEqual([], ppr.sandbox_presses(crit[6]))

    def test_the_two_presses_the_fixture_does_not_use(self):
        self.assertEqual(["snoozeTask"], ppr.sandbox_presses(
            "waiting for the operator's press: on the sandbox, snooze the fixture task"))
        self.assertEqual(["sendProjectAgentMessage"], ppr.sandbox_presses(
            "waiting for the operator's press: on the sandbox, send a Desk message"))
        self.assertEqual(["sendProjectAgentMessage"], ppr.sandbox_presses(
            "waiting for the operator's press: on the sandbox, open a conversation"))


class TheSandboxPressRule(_Cli):
    """The fixture card over the CLI, the run's caller `bureau-pipeline`."""

    def run_fixture(self, rec, caller="bureau-pipeline", *extra):
        cards, detail = split([rec])
        argv = ["findings", "--detail-file", self.write("detail.json", detail),
                "--caller", caller,
                "--proof-local", self.path("no-such-proof-local.json")] + list(extra)
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(cards))), \
                mock.patch.object(sys, "stdout", out):
            self.assertEqual(0, ppr.main(argv))
        return [l for l in out.getvalue().splitlines() if l]

    def test_the_fixture_has_seven_criteria(self):
        self.assertEqual(7, len(fixture_criteria()))

    def test_four_lines_for_criteria_2_to_5_and_no_reach_for_criterion_1(self):
        lines = self.run_fixture(fixture())
        self.assertEqual([sandbox_line(n) for n in (2, 3, 4, 5)], sandbox_lines(lines))
        self.assertEqual([], reach_lines(lines))
        self.assertIn("needs the CEO's press:", fixture_criteria()[4])
        # DRE-7950: criteria 6 and 7 press outside the list, and the
        # operator's press is refused for those too.
        self.assertEqual([press_line(n) for n in (6, 7)], press_lines(lines))
        self.assertEqual(6, len(lines), lines)

    def test_the_four_criteria_use_all_four_names(self):
        crit = fixture_criteria()
        used = {name for text in crit[1:5] for name in ppr.SANDBOX_NAMES
                if name.casefold() in text.casefold()}
        self.assertEqual(set(ppr.SANDBOX_NAMES), used)

    def test_relabeled_for_bureau_pipeline(self):
        lines = self.run_fixture(fixture(labels=["repo:bureau-pipeline", "agent:ops"]),
                                 "agent-bureau")
        self.assertEqual([], sandbox_lines(lines))
        found = reach_lines(lines)
        self.assertEqual(1, len(found), lines)
        self.assertIn(proof_record._cut(fixture_criteria()[0], ppr.CUT), found[0])
        self.assertIn(what("console-sandbox-press"), found[0])
        # DRE-7950: no proof-reader here, so every operator's-press row is
        # refused as one, and the CEO's row (5) is not.
        self.assertEqual([press_line(n) for n in (2, 3, 4, 6, 7)], press_lines(lines))
        self.assertEqual(6, len(lines), lines)

    def test_the_caller_falls_back_to_the_flag(self):
        for labels in (["agent:ops"],
                       ["repo:not-in-the-map", "agent:ops"],
                       ["repo:agent-bureau", "repo:portico", "agent:ops"]):
            rec = fixture(labels=labels)
            # The fallback holds no proof-reader: criterion 1 is refused, and
            # nothing is a person's sandbox press.
            lines = self.run_fixture(rec, "bureau-pipeline")
            self.assertEqual([], sandbox_lines(lines), labels)
            self.assertEqual(1, len(reach_lines(lines)), (labels, lines))
            self.assertEqual([press_line(n) for n in (2, 3, 4, 6, 7)],
                             press_lines(lines), labels)
            # The fallback holds one: the reverse.
            lines = self.run_fixture(rec, "agent-bureau")
            self.assertEqual(4, len(sandbox_lines(lines)), (labels, lines))
            self.assertEqual([], reach_lines(lines), labels)
            self.assertEqual([press_line(n) for n in (6, 7)], press_lines(lines), labels)

    def test_the_owner_name_caller_is_read_as_its_slug(self):
        rec = fixture(labels=["agent:ops"])
        lines = self.run_fixture(rec, "dreadnought-foundry/agent-bureau")
        self.assertEqual(4, len(sandbox_lines(lines)), lines)

    def test_it_is_appended_to_the_structural_file_and_is_a_send_back(self):
        structural = self.write("structural.txt", "")
        printed = self.run_fixture(fixture(), "bureau-pipeline",
                                   "--structural-file", structural)
        lines = [l for l in self.text("structural.txt").splitlines() if l]
        self.assertEqual([sandbox_line(n) for n in (2, 3, 4, 5)]
                         + [press_line(n) for n in (6, 7)], lines)
        self.assertEqual(lines, printed)

        result = self.write("result.md", plan_critic.result_line(plan_critic.PASS) + "\n")
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(0, plan_seam_gate.main(
                ["gate", "--result-file", result, "--structural-file", structural]))
        rewritten = self.text("result.md")
        verdict, _ = plan_critic.read_result(rewritten)
        self.assertEqual(plan_critic.SEND_BACK, verdict)
        found = plan_critic.all_findings(rewritten)
        self.assertEqual(6, len(found), found)
        for line in lines:
            self.assertIn(line, rewritten)


class TheSandboxPressRuleReadsTheDeclaration(unittest.TestCase):

    def test_held_for_is_read_off_the_reading(self):
        doc = proof_reach.load()
        for r in doc["readings"]:
            if r["id"] == "console-sandbox-press":
                r["held_for"] = ["bureau-pipeline"]
                r["queries_declared_by"] = {"bureau-pipeline": "x.py"}
        rec = fixture(labels=["repo:bureau-pipeline", "agent:ops"])
        lines = read([rec], doc=doc).lines
        self.assertEqual(4, len(sandbox_lines(lines)), lines)
        self.assertEqual([], sandbox_lines(read([fixture()], doc=doc).lines))

    def test_an_unknown_caller_with_the_label_still_reads_the_label(self):
        lines = read([fixture()], caller_slug=False).lines
        self.assertEqual(4, len(sandbox_lines(lines)), lines)

    def test_a_criterion_with_no_row_is_not_read(self):
        text = "on the sandbox, mark the fixture task done"
        self.assertEqual([], sandbox_lines(read([proof("DRE-7901", text)]).lines))


class ItRestatesNothing(unittest.TestCase):
    """The contract: the declaration only through `proof_reach`, operator cards
    only through the routing vocabulary and `is_proof`."""

    def source(self) -> str:
        with open(os.path.join(SCRIPTS, "plan_proof_reach.py"), encoding="utf-8") as fh:
            return fh.read()

    def test_no_cause_word_and_no_declaration_path(self):
        src = self.source()
        for word in proof_reach.causes():
            self.assertNotIn(word, src)
        self.assertNotIn("proof-reach.json", src)

    def test_no_label_literal_and_no_title_pattern(self):
        src = self.source()
        for label in ("\"no-code\"", "\"agent:ops\"", "\"needs-human\"",
                      "\"operator-step\"", "'no-code'", "'agent:ops'"):
            self.assertNotIn(label, src)
        self.assertNotIn("sign-off", src.lower())


if __name__ == "__main__":
    unittest.main()
