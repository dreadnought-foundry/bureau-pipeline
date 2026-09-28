"""A merge GitHub refuses for a missing code-owner review is a HOLD, said
once to the person named — not a "real failure" every pass (DRE-4341).

THE INCIDENT (2026-09-19, Portico #616, DRE-4177 — the portal favicons). The
pull request reworded one sentence under `docs/design/system/`, which
Portico's ruleset "Sid reviews atoms and the design system" (ruleset
23194028, `require_code_owner_review: true`) holds for the review of the
owner `.github/CODEOWNERS` names, `@smeed652`. CI was green and the critic's
APPROVE was bound to the head, so `merge_gate.py` decided `merge`; GitHub
answered

    Pull request … is not mergeable: the base branch policy prohibits the merge.

and the step printed "merge failed with the head still at <sha> — real
failure" and exited 1. Five times, over about nine hours (runs 35465747760,
35465755335, 35472731884, 35479704383, 35485207923). Nothing was posted on
the pull request or the card; the medic then filed a wrong diagnosis into
the CEO's queue. It merged within two minutes of the review.

These tests drive:

  * the pure reading (`code_owner_hold.read_owners`) of the three records the
    gate now gathers — the base branch's rules, the pull request's reviews
    and the base branch's CODEOWNERS — against the pull request's files;
  * `merge_gate.decide`'s condition O over that reading: `hold` when the
    code-owner requirement is unmet, the old path when it is met, and the
    old LOUD path when any of the reads failed;
  * the SHIPPED `Evaluate and merge` step, run by bash against a stub `gh`
    and a stand-in Linear, for the hold, the park, the release and the
    other refusals that must stay loud.

Run: cd bureau-pipeline && python3 -m pytest tests/test_merge_gate_code_owner_hold.py -v
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess  # nosec B404 — fixed argv, our own workflow body
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"
SELF_STUB = ROOT / ".github" / "workflows" / "self-merge-gate.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import code_owner_hold as coh  # noqa: E402
import merge_gate  # noqa: E402

REPO = "dreadnought-foundry/portico"
PR = 616
CARD = "DRE-4177"
BRANCH = "agent/DRE-4177-portal-favicons"
HEAD = "4f1d2c3b" + "a" * 32
QA = "agent-bureau-qa-bot[bot]"
AUTHOR = "agent-bureau-bot[bot]"
OWNER = "smeed652"

CODEOWNERS = """\
# Sid reviews atoms and the design system (DRE-3760).
/client/app/src/components/atoms/ @smeed652
/docs/design/system/ @smeed652
"""

#: GitHub's `GET rules/branches/{branch}` answer for Portico's `main`, in the
#: shape the endpoint returns: one entry per rule in force, each naming the
#: ruleset it came from.
CODE_OWNER_RULE = {
    "type": "pull_request",
    "ruleset_source_type": "Repository",
    "ruleset_source": REPO,
    "ruleset_id": 23194028,
    "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": False,
        "require_code_owner_review": True,
        "require_last_push_approval": False,
        "required_review_thread_resolution": False,
    },
}
REQUIRED_CHECKS_RULE = {
    "type": "required_status_checks",
    "ruleset_id": 1,
    "parameters": {
        "strict_required_status_checks_policy": False,
        "required_status_checks": [{"context": "scripts unit tests"}],
    },
}
FILES = ["docs/design/system/voice.md", "client/app/public/favicon.svg"]
POLICY_REFUSAL = (
    f"GraphQL: Pull request {REPO}#{PR} is not mergeable: the base branch "
    "policy prohibits the merge. (mergePullRequest)"
)
HEX_RUN = re.compile(r"\b[0-9a-f]{7,40}\b")


def review(login: str, state: str, rid: int = 1) -> dict:
    return {"id": rid, "user": {"login": login}, "state": state,
            "commit_id": HEAD}


def record(rules=None, reviews=(), codeowners=CODEOWNERS, files=FILES) -> dict:
    """The owners record `code_owner_hold.py gather` writes."""
    return {
        "rules": [CODE_OWNER_RULE] if rules is None else rules,
        "reviews": list(reviews) if reviews is not None else None,
        "codeowners": (
            None if codeowners is None
            else {"path": ".github/CODEOWNERS", "text": codeowners}
        ),
        "files": list(files) if files is not None else None,
    }


# --------------------------------------------------------------------------
# CODEOWNERS, as GitHub reads it
# --------------------------------------------------------------------------
class CodeownersMatchingTest(unittest.TestCase):
    def owner(self, text, path):
        hit = coh.owners_of(path, coh.parse_codeowners(text))
        return hit[1] if hit else None

    def test_an_anchored_folder_owns_everything_under_it(self):
        self.assertEqual(self.owner(CODEOWNERS, "docs/design/system/voice.md"), ("@smeed652",))
        self.assertEqual(self.owner(CODEOWNERS, "docs/design/system/a/b.md"), ("@smeed652",))
        self.assertEqual(
            self.owner(CODEOWNERS, "client/app/src/components/atoms/Button.tsx"),
            ("@smeed652",))

    def test_a_path_outside_every_rule_has_no_owner(self):
        self.assertIsNone(self.owner(CODEOWNERS, "client/app/public/favicon.svg"))
        self.assertIsNone(self.owner(CODEOWNERS, "docs/design/systemic.md"))
        self.assertIsNone(self.owner(CODEOWNERS, "x/docs/design/system/voice.md"))

    def test_the_last_matching_line_wins(self):
        text = "* @everyone\n/docs/ @docs-owner\n"
        self.assertEqual(self.owner(text, "docs/a.md"), ("@docs-owner",))
        self.assertEqual(self.owner(text, "src/a.py"), ("@everyone",))

    def test_a_later_line_with_no_owner_clears_ownership(self):
        text = "/docs/ @docs-owner\n/docs/free/\n"
        self.assertEqual(self.owner(text, "docs/free/a.md"), ())

    def test_an_unanchored_name_matches_at_any_depth(self):
        self.assertEqual(self.owner("*.css @styler\n", "a/b/c.css"), ("@styler",))
        self.assertEqual(self.owner("apps/ @octo\n", "x/apps/y.py"), ("@octo",))

    def test_a_single_star_does_not_descend(self):
        # GitHub's own example: `docs/*` owns docs/getting-started.md but not
        # docs/build-app/troubleshooting.md.
        text = "docs/* docs@example.com\n"
        self.assertEqual(self.owner(text, "docs/getting-started.md"), ("docs@example.com",))
        self.assertIsNone(self.owner(text, "docs/build-app/troubleshooting.md"))

    def test_a_double_star_does(self):
        self.assertEqual(self.owner("/docs/** @d\n", "docs/a/b/c.md"), ("@d",))

    def test_comments_and_blank_lines_are_ignored(self):
        self.assertEqual(coh.parse_codeowners("# x\n\n   \n# y @z\n"), [])


# --------------------------------------------------------------------------
# the reading the gate decides on
# --------------------------------------------------------------------------
class ReadOwnersTest(unittest.TestCase):
    def read(self, rec, author=AUTHOR):
        return coh.read_owners(rec, author)

    def test_the_incident_is_unmet_and_names_the_owner_and_folder(self):
        r = self.read(record())
        self.assertEqual(r.state, coh.UNMET)
        self.assertEqual(r.groups, ((("@smeed652",), ("docs/design/system/",)),))

    def test_the_owners_approval_meets_it(self):
        self.assertEqual(self.read(record(reviews=[review(OWNER, "APPROVED")])).state, coh.MET)

    def test_login_case_does_not_matter(self):
        self.assertEqual(self.read(record(reviews=[review("SMeed652", "APPROVED")])).state, coh.MET)

    def test_the_latest_review_per_person_counts(self):
        rec = record(reviews=[review(OWNER, "APPROVED", 1),
                              review(OWNER, "CHANGES_REQUESTED", 2)])
        self.assertEqual(self.read(rec).state, coh.UNMET)
        rec = record(reviews=[review(OWNER, "APPROVED", 1), review(OWNER, "COMMENTED", 2)])
        self.assertEqual(self.read(rec).state, coh.MET, "a comment does not withdraw an approval")
        rec = record(reviews=[review(OWNER, "APPROVED", 1), review(OWNER, "DISMISSED", 2)])
        self.assertEqual(self.read(rec).state, coh.UNMET)

    def test_someone_elses_approval_does_not_meet_it(self):
        rec = record(reviews=[review("agent-bureau-qa-bot[bot]", "APPROVED")])
        self.assertEqual(self.read(rec).state, coh.UNMET)

    def test_the_authors_own_approval_does_not_count(self):
        rec = record(reviews=[review(OWNER, "APPROVED")])
        self.assertEqual(self.read(rec, author=OWNER).state, coh.UNMET)

    def test_a_pull_request_touching_no_owned_path_is_met(self):
        self.assertEqual(self.read(record(files=["client/app/public/favicon.svg"])).state, coh.MET)

    def test_no_code_owner_rule_is_not_required(self):
        rec = record(rules=[REQUIRED_CHECKS_RULE])
        self.assertEqual(self.read(rec).state, coh.NOT_REQUIRED)
        off = json.loads(json.dumps(CODE_OWNER_RULE))
        off["parameters"]["require_code_owner_review"] = False
        self.assertEqual(self.read(record(rules=[off])).state, coh.NOT_REQUIRED)

    def test_every_unreadable_record_is_unknown_never_a_hold(self):
        for name, rec in {
            "rules": {**record(), "rules": None},
            "reviews": record(reviews=None),
            "codeowners": record(codeowners=None),
            "files": record(files=None),
            "not a record": "garbage",
            "rules not a list": {**record(), "rules": {"message": "Not Found"}},
        }.items():
            with self.subTest(unreadable=name):
                r = self.read(rec)
                self.assertEqual(r.state, coh.UNKNOWN, r)
                self.assertTrue(r.detail)

    def test_no_codeowners_file_is_unknown(self):
        rec = record()
        rec["codeowners"] = {"path": None, "text": ""}
        self.assertEqual(self.read(rec).state, coh.UNKNOWN)

    def test_a_team_owner_with_an_approval_on_the_pr_cannot_be_proved(self):
        rec = record(codeowners="/docs/ @dreadnought-foundry/design\n",
                     reviews=[review("someone", "APPROVED")])
        self.assertEqual(self.read(rec).state, coh.UNKNOWN)

    def test_a_team_owner_with_no_approval_at_all_is_unmet(self):
        rec = record(codeowners="/docs/ @dreadnought-foundry/design\n")
        r = self.read(rec)
        self.assertEqual(r.state, coh.UNMET)
        self.assertEqual(r.groups[0][0], ("@dreadnought-foundry/design",))


# --------------------------------------------------------------------------
# the words — for a non-technical reader
# --------------------------------------------------------------------------
class TheSentenceTest(unittest.TestCase):
    def setUp(self):
        self.groups = coh.read_owners(record(), AUTHOR).groups
        self.sentence = coh.hold_sentence(PR, self.groups)

    def test_it_names_who_which_folder_and_what_to_press(self):
        self.assertIn("@smeed652", self.sentence)
        self.assertIn("docs/design/system/", self.sentence)
        self.assertIn("approve", self.sentence.lower())
        self.assertIn(f"#{PR}", self.sentence)

    def test_it_carries_no_sha_no_diff_and_no_verdict_text(self):
        self.assertIsNone(HEX_RUN.search(self.sentence), self.sentence)
        self.assertNotIn("diff", self.sentence.lower())
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, self.sentence)
        self.assertNotIn(";", self.sentence)
        self.assertNotIn("!", self.sentence)

    def test_the_pr_note_and_the_card_comment_carry_the_same_sentence(self):
        marker, note = coh.pr_note(HEAD, self.sentence)
        self.assertTrue(merge_gate.opens_with_marker(note, marker))
        self.assertIn(HEAD, marker, "the note's marker is per head sha")
        self.assertIn(self.sentence, note)
        card = coh.card_comment(PR, self.sentence)
        self.assertIn(self.sentence, card)
        self.assertEqual(coh.latest_gate_marker([{"body": card, "authored_by_pipeline": True}], PR), "hold")

    def test_the_visible_part_of_the_note_has_no_sha(self):
        _, note = coh.pr_note(HEAD, self.sentence)
        visible = re.sub(r"<!--.*?-->", "", note, flags=re.S)
        self.assertIsNone(HEX_RUN.search(visible), visible)

    def test_two_owners_are_both_named(self):
        text = "/docs/ @a\n/src/ @b @c\n"
        groups = coh.read_owners(
            record(codeowners=text, files=["docs/x.md", "src/y.py"]), AUTHOR).groups
        s = coh.hold_sentence(PR, groups)
        for who in ("@a", "@b", "@c", "docs/", "src/"):
            self.assertIn(who, s)


# --------------------------------------------------------------------------
# condition O in the decision
# --------------------------------------------------------------------------
def green_checks():
    return [{"name": "scripts unit tests", "status": "completed",
             "conclusion": "success", "check_suite": {"id": 1}}]


def approve_comments():
    return [{"id": 1, "user": {"login": QA},
             "body": f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}"}]


class DecideTest(unittest.TestCase):
    def decide(self, owners, checks=None):
        return merge_gate.decide(
            HEAD, QA, green_checks() if checks is None else checks,
            approve_comments(), owners=owners)

    def test_unmet_is_a_hold_not_a_merge(self):
        d = self.decide(coh.read_owners(record(), AUTHOR))
        self.assertEqual(d.action, "hold")
        self.assertIn("@smeed652", d.reason)
        self.assertEqual(d.code_owner_review, coh.UNMET)
        self.assertTrue(d.owner_hold)

    def test_met_merges(self):
        d = self.decide(coh.read_owners(record(reviews=[review(OWNER, "APPROVED")]), AUTHOR))
        self.assertEqual(d.action, "merge")
        self.assertEqual(d.code_owner_review, coh.MET)

    def test_unknown_merges_as_today_and_says_so(self):
        d = self.decide(coh.read_owners(record(reviews=None), AUTHOR))
        self.assertEqual(d.action, "merge")
        self.assertEqual(d.code_owner_review, coh.UNKNOWN)
        self.assertTrue(any("UNKNOWN" in n for n in d.notes), d.notes)

    def test_no_record_is_the_old_behaviour(self):
        d = self.decide(None)
        self.assertEqual(d.action, "merge")
        self.assertIsNone(d.code_owner_review)

    def test_red_ci_still_decides_first(self):
        red = [{"name": "t", "status": "completed", "conclusion": "failure",
                "check_suite": {"id": 1}}]
        self.assertNotEqual(self.decide(coh.read_owners(record(), AUTHOR), red).action, "hold")


class CliTest(unittest.TestCase):
    def run_cli(self, owners_payload):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "cr.json").write_text(json.dumps({"check_runs": green_checks()}))
            (td / "c.json").write_text(json.dumps([approve_comments()]))
            (td / "wr.json").write_text(json.dumps({"workflow_runs": []}))
            (td / "cmp.json").write_text("{}")
            args = [sys.executable, str(ROOT / "scripts" / "merge_gate.py"),
                    "--head-sha", HEAD, "--qa-login", QA,
                    "--check-runs-file", str(td / "cr.json"),
                    "--comments-file", str(td / "c.json"),
                    "--workflow-runs-file", str(td / "wr.json"),
                    "--compare-file", str(td / "cmp.json"),
                    "--pr-author", AUTHOR]
            if owners_payload is not None:
                (td / "o.json").write_text(owners_payload)
                args += ["--owners-file", str(td / "o.json")]
            return subprocess.run(args, capture_output=True, text=True)  # nosec B603

    def test_the_hold_is_printed_with_its_groups(self):
        proc = self.run_cli(json.dumps(record()))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("decision=hold", proc.stdout)
        self.assertIn("code_owner_review=unmet", proc.stdout)
        line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("owner_hold="))
        groups = json.loads(line.split("=", 1)[1])
        self.assertEqual(groups, [{"owners": ["@smeed652"], "folders": ["docs/design/system/"]}])

    def test_an_unreadable_owners_file_is_unknown_and_merges(self):
        proc = self.run_cli("{not json")
        self.assertIn("decision=merge", proc.stdout)
        self.assertIn("code_owner_review=unknown", proc.stdout)


# --------------------------------------------------------------------------
# the gatherer — one seam to the network, never raises
# --------------------------------------------------------------------------
class GatherTest(unittest.TestCase):
    COMPARE = {"files": [{"filename": f} for f in FILES]}

    def fake(self, answers):
        calls = []

        def _gh(args):
            calls.append(args)
            path = next(a for a in args if "/" in a and not a.startswith("-"))
            for key, value in answers.items():
                if key in path:
                    return value
            return None, "HTTP 404: Not Found"
        return _gh, calls

    def encoded(self, text):
        return json.dumps({"encoding": "base64",
                           "content": base64.b64encode(text.encode()).decode()}), None

    def test_the_incident_record(self):
        fake, calls = self.fake({
            "/rules/branches/": (json.dumps([[CODE_OWNER_RULE]]), None),
            "/reviews": (json.dumps([[]]), None),
            "contents/.github/CODEOWNERS": self.encoded(CODEOWNERS),
        })
        with mock.patch.object(coh, "_gh", fake):
            rec = coh.gather_owners(REPO, PR, "main", self.COMPARE)
        self.assertEqual(coh.read_owners(rec, AUTHOR).state, coh.UNMET)
        self.assertTrue(any("ref=main" in " ".join(c) for c in calls), calls)

    def test_no_pull_request_rule_costs_one_call(self):
        fake, calls = self.fake({"/rules/branches/": (json.dumps([[]]), None)})
        with mock.patch.object(coh, "_gh", fake):
            rec = coh.gather_owners(REPO, PR, "main", self.COMPARE)
        self.assertEqual(len(calls), 1)
        self.assertEqual(coh.read_owners(rec, AUTHOR).state, coh.NOT_REQUIRED)

    def test_a_failed_read_is_recorded_unknown(self):
        for broken in ("/rules/branches/", "/reviews", "contents/"):
            answers = {
                "/rules/branches/": (json.dumps([[CODE_OWNER_RULE]]), None),
                "/reviews": (json.dumps([[]]), None),
                "contents/.github/CODEOWNERS": self.encoded(CODEOWNERS),
            }
            answers = {k: ((None, "HTTP 502: Bad Gateway") if broken in k else v)
                       for k, v in answers.items()}
            fake, _ = self.fake(answers)
            with self.subTest(broken=broken), mock.patch.object(coh, "_gh", fake):
                rec = coh.gather_owners(REPO, PR, "main", self.COMPARE)
                self.assertEqual(coh.read_owners(rec, AUTHOR).state, coh.UNKNOWN)

    def test_a_blipped_or_truncated_compare_is_unknown(self):
        fake, _ = self.fake({
            "/rules/branches/": (json.dumps([[CODE_OWNER_RULE]]), None),
            "/reviews": (json.dumps([[]]), None),
            "contents/.github/CODEOWNERS": self.encoded(CODEOWNERS),
        })
        for compare in ({}, {"files": [{"filename": f"f{i}"} for i in range(300)]}):
            with mock.patch.object(coh, "_gh", fake):
                rec = coh.gather_owners(REPO, PR, "main", compare)
            self.assertEqual(coh.read_owners(rec, AUTHOR).state, coh.UNKNOWN)


# --------------------------------------------------------------------------
# does the calling repo's stub wake the gate on a review?
# --------------------------------------------------------------------------
class StubDeclaresReviewTest(unittest.TestCase):
    def test_a_mapping_trigger(self):
        self.assertTrue(coh.stub_wakes_on_review(
            "on:\n  issue_comment:\n    types: [created]\n"
            "  pull_request_review:\n    types: [submitted]\njobs: {}\n"))

    def test_a_list_trigger(self):
        self.assertTrue(coh.stub_wakes_on_review("on: [workflow_run, pull_request_review]\n"))
        self.assertTrue(coh.stub_wakes_on_review("on:\n  - issue_comment\n  - pull_request_review\n"))

    def test_absent_commented_or_a_lookalike_is_not(self):
        self.assertFalse(coh.stub_wakes_on_review("on:\n  issue_comment:\n    types: [created]\n"))
        self.assertFalse(coh.stub_wakes_on_review(
            "on:\n  # pull_request_review:\n  issue_comment:\n"))
        self.assertFalse(coh.stub_wakes_on_review(
            "on:\n  pull_request_review_comment:\n    types: [created]\n"))
        self.assertFalse(coh.stub_wakes_on_review(""))

    def test_the_reusable_itself_is_not_a_stub_that_wakes_on_a_review(self):
        # merge-gate.yml NAMES the event in its job `if:` — only the `on:`
        # block counts, which is why bureau-pipeline reads self-merge-gate.yml.
        self.assertFalse(coh.stub_wakes_on_review(WORKFLOW.read_text()))

    def test_the_stub_is_resolved_per_repo(self):
        self.assertEqual(coh.stub_path("dreadnought-foundry/bureau-pipeline"),
                         ".github/workflows/self-merge-gate.yml")
        self.assertEqual(coh.stub_path(REPO), ".github/workflows/merge-gate.yml")


class SelfStubTest(unittest.TestCase):
    """bureau-pipeline's own pull requests get the prompt wake as soon as this
    merges (card item 4)."""

    def test_self_merge_gate_declares_submitted_reviews(self):
        doc = yaml.safe_load(SELF_STUB.read_text())
        on = doc.get("on", doc.get(True))
        self.assertIn("pull_request_review", on)
        self.assertEqual(on["pull_request_review"], {"types": ["submitted"]})
        self.assertTrue(coh.stub_wakes_on_review(SELF_STUB.read_text()))


# --------------------------------------------------------------------------
# the SHIPPED step, executed
# --------------------------------------------------------------------------
GH_STUB = r'''#!/usr/bin/env python3
import base64, json, os, sys

args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
fx = json.load(open(os.environ["FIXTURE"]))


def emit(value):
    print(value if isinstance(value, str) else json.dumps(value))
    raise SystemExit(0)


def fail(msg):
    sys.stderr.write(msg + "\n")
    raise SystemExit(1)


def opt(name):
    return args[args.index(name) + 1] if name in args else None


def comments():
    return json.load(open(os.environ["COMMENTS"]))


def write_comments(rows):
    with open(os.environ["COMMENTS"], "w") as fh:
        json.dump(rows, fh)


if args[:2] == ["pr", "view"]:
    field = (opt("--jq") or "").lstrip(".")
    value = fx["pr"][field]
    emit("true" if value is True else "false" if value is False else str(value))
if args[:2] == ["run", "list"]:
    emit([])
if args[:2] == ["pr", "merge"]:
    if fx.get("merge_error"):
        fail(fx["merge_error"])
    emit("merged")
if args[0] == "api":
    method = opt("--method") or "GET"
    path = [a for a in args[1:] if "/" in a and not a.startswith("-")][0]
    slurp = "--slurp" in args
    if "/rules/branches/" in path:
        if fx.get("rules") is None:
            fail("HTTP 502: Bad Gateway")
        emit([fx["rules"]] if slurp else fx["rules"])
    if path.endswith("/reviews") or "/reviews?" in path:
        if fx.get("reviews") is None:
            fail("HTTP 502: Bad Gateway")
        emit([fx["reviews"]] if slurp else fx["reviews"])
    if "/contents/" in path:
        name = path.split("/contents/", 1)[1].split("?", 1)[0]
        if fx.get("codeowners") is None:
            fail("HTTP 502: Bad Gateway")
        if name != ".github/CODEOWNERS":
            fail("gh: Not Found (HTTP 404)")
        emit({"encoding": "base64",
              "content": base64.b64encode(fx["codeowners"].encode()).decode()})
    if "pulls?state=open" in path:
        emit(json.dumps({"number": int(os.environ["PR"]),
                         "head_sha": fx["pr"]["headRefOid"]}))
    if method == "POST":
        rows = comments()
        row = {"id": 900 + len(rows), "user": {"login": os.environ["QA_LOGIN"]},
               "body": json.loads(sys.stdin.read())["body"]}
        rows.append(row)
        write_comments(rows)
        emit(row)
    if method == "DELETE":
        cid = int(path.rsplit("/", 1)[1])
        write_comments([c for c in comments() if c["id"] != cid])
        emit({})
    if "check-runs" in path:
        emit({"check_runs": fx["check_runs"]})
    if "/compare/" in path:
        emit(fx["compare"])
    if "actions/runs" in path:
        emit({"workflow_runs": []})
    if "/commits" in path:
        emit([{"sha": fx["pr"]["headRefOid"]}])
    if "/comments" in path:
        rows = comments()
        if "page=" in path.replace("per_page", "") and "page=1" not in path.replace("per_page", ""):
            emit([])
        emit([rows] if slurp else rows)
    if "/pulls/" in path:
        emit(fx["author"] if opt("--jq") else {})
    emit({})
sys.stderr.write("unexpected gh call: %r\n" % (args,))
raise SystemExit(2)
'''

#: The stand-in Linear: the functions code_owner_hold uses, plus the two CLI
#: commands the step runs, all over one JSON file — and every call appended
#: to the gh log, so the ORDER of Linear moves and GitHub calls is visible.
FAKE_LINEAR = r'''
import json, os, sys


class LinearError(RuntimeError):
    pass


def _load():
    return json.load(open(os.environ["LINEAR_FAKE"]))


def _save(d):
    json.dump(d, open(os.environ["LINEAR_FAKE"], "w"))


def _log(*call):
    with open(os.environ["GH_LOG"], "a") as fh:
        fh.write(json.dumps(["linear", *call]) + "\n")


def get_issue(identifier):
    card = _load()["cards"][identifier]
    return {"identifier": identifier, "title": "t", "state": {"name": card["state"]}}


def comment_records(identifier, whole_thread=False):
    return list(_load()["cards"][identifier]["comments"])


def cmd_comment(identifier, body, *flags):
    _log("comment", identifier, body)
    d = _load()
    d["cards"][identifier]["comments"].append({"body": body, "authored_by_pipeline": True})
    _save(d)


def cmd_advance(identifier, to_state, from_states_csv, *flags):
    d = _load()
    card = d["cards"][identifier]
    if card["state"].lower() not in [s.strip().lower() for s in from_states_csv.split(",")]:
        print(f"{identifier} is in {card['state']!r}, not in {from_states_csv!r} — not advancing")
        return
    _log("advance", identifier, card["state"], to_state)
    card["state"] = to_state
    _save(d)
    print(f"{identifier} → {to_state}")


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    if cmd == "advance":
        cmd_advance(*rest)
    elif cmd == "comment":
        cmd_comment(*rest)
    else:
        _log("UNEXPECTED", cmd, *rest)
        raise SystemExit(3)
'''

STUB_WITH_REVIEW = """\
name: Merge Gate
on:
  workflow_run:
    workflows: [CI]
    types: [completed]
  issue_comment:
    types: [created]
  pull_request_review:
    types: [submitted]
jobs:
  call:
    uses: dreadnought-foundry/bureau-pipeline/.github/workflows/merge-gate.yml@stable
"""
STUB_WITHOUT_REVIEW = STUB_WITH_REVIEW.replace(
    "  pull_request_review:\n    types: [submitted]\n", "")


def evaluate_body() -> str:
    doc = yaml.safe_load(WORKFLOW.read_text())
    step = next(s for s in doc["jobs"]["evaluate"]["steps"]
                if s.get("name") == "Evaluate and merge")
    return re.sub(r"\$\{\{[^}]*\}\}", "", step["run"])


class Shipped:
    def __init__(self, proc, calls, comments, linear):
        self.proc, self.calls, self.comments, self.linear = proc, calls, comments, linear

    @property
    def out(self):
        return f"{self.proc.stdout}\n{self.proc.stderr}"

    @property
    def merges(self):
        return [c for c in self.calls if c[:2] == ["pr", "merge"]]

    @property
    def notes(self):
        return [c["body"] for c in self.comments if "code-owner hold" in c["body"]]

    @property
    def linear_calls(self):
        return [c[1:] for c in self.calls if c[:1] == ["linear"]]

    @property
    def moves(self):
        return [c for c in self.linear_calls if c[0] == "advance"]

    @property
    def card_comments(self):
        return [c[2] for c in self.linear_calls if c[0] == "comment"]


def run_shipped(*, reviews=(), rules=None, codeowners=CODEOWNERS, stub=STUB_WITH_REVIEW,
                card_state="In Review", card_comments=(), merge_error="",
                state_dir=None, check_runs=None, rules_unreadable=False) -> Shipped:
    """Execute merge-gate.yml's `Evaluate and merge` body for real. Pass the
    same `state_dir` twice to run a second wake over the first one's
    comments and card."""
    fixture = {
        "pr": {"headRefName": BRANCH, "state": "OPEN", "mergeStateStatus": "BLOCKED",
               "isDraft": False, "headRefOid": HEAD, "baseRefName": "main",
               "url": f"https://github.com/{REPO}/pull/{PR}"},
        "check_runs": green_checks() if check_runs is None else check_runs,
        "compare": {"status": "ahead", "files": [{"filename": f} for f in FILES]},
        "author": AUTHOR,
        "rules": (None if rules_unreadable
                  else [CODE_OWNER_RULE] if rules is None else rules),
        "reviews": None if reviews is None else list(reviews),
        "codeowners": codeowners,
        "merge_error": merge_error,
    }
    own = state_dir is None
    td = Path(tempfile.mkdtemp()) if own else Path(state_dir)
    try:
        if not (td / "comments.json").exists():
            (td / "bin").mkdir()
            stub_gh = td / "bin" / "gh"
            stub_gh.write_text(GH_STUB)
            stub_gh.chmod(0o755)  # nosec B103 — a test stub on PATH
            # .bureau-pipeline/scripts: every real script, except Linear —
            # and the entry point that imports it is a COPY, so Python puts
            # this directory (not the real one) first on its path.
            scripts = td / ".bureau-pipeline" / "scripts"
            scripts.mkdir(parents=True)
            for src in (ROOT / "scripts").iterdir():
                if src.name in ("linear_ops.py", "code_owner_hold.py"):
                    continue
                os.symlink(src, scripts / src.name)
            shutil.copy(ROOT / "scripts" / "code_owner_hold.py", scripts)
            (scripts / "linear_ops.py").write_text(FAKE_LINEAR)
            (td / ".github" / "workflows").mkdir(parents=True)
            (td / ".github" / "workflows" / "merge-gate.yml").write_text(stub)
            (td / "comments.json").write_text(json.dumps(approve_comments()))
            (td / "linear.json").write_text(json.dumps({"cards": {CARD: {
                "state": card_state, "comments": list(card_comments)}}}))
        (td / "fixture.json").write_text(json.dumps(fixture))
        (td / "gh.log").write_text("")
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own body
            ["bash", "-e", "-c", evaluate_body()], cwd=td, capture_output=True, text=True,
            env={
                **{k: v for k, v in os.environ.items() if k != "LINEAR_API_KEY"},
                "PATH": f"{td / 'bin'}{os.pathsep}{os.environ['PATH']}",
                "PR": str(PR), "GH_TOKEN": "qa-token", "REPO_FULL": REPO,
                "WORKFLOW_TOKEN": "workflow-token", "QA_LOGIN": QA,
                "LINEAR_API_KEY": "fake", "LINEAR_FAKE": str(td / "linear.json"),
                "FIXTURE": str(td / "fixture.json"),
                "COMMENTS": str(td / "comments.json"), "GH_LOG": str(td / "gh.log"),
            })
        calls = [json.loads(ln) for ln in (td / "gh.log").read_text().splitlines() if ln]
        comments = json.loads((td / "comments.json").read_text())
        linear = json.loads((td / "linear.json").read_text())
    finally:
        if own:
            shutil.rmtree(td, ignore_errors=True)
    return Shipped(proc, calls, comments, linear)


class TheIncidentIsAHoldTest(unittest.TestCase):
    """CI green, APPROVE bound to the head, a base-branch rule requiring a
    code-owner review, and no review from the owner."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.first = run_shipped(state_dir=cls.dir)
        cls.second = run_shipped(state_dir=cls.dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_the_step_exits_clean(self):
        self.assertEqual(self.first.proc.returncode, 0, self.first.out)
        self.assertIn("decision=hold", self.first.proc.stdout)
        self.assertNotIn("real failure", self.first.out)

    def test_no_merge_is_attempted(self):
        self.assertEqual(self.first.merges, [], self.first.out)

    def test_exactly_one_note_names_the_reviewer_and_the_folder(self):
        self.assertEqual(len(self.first.notes), 1, self.first.comments)
        note = self.first.notes[0]
        self.assertIn("@smeed652", note)
        self.assertIn("docs/design/system/", note)
        self.assertIn("approve", note.lower())
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, note)

    def test_the_card_is_parked_in_green_light_once_with_the_same_sentence(self):
        self.assertEqual(self.first.moves, [["advance", CARD, "In Review", "Green Light"]])
        self.assertEqual(len(self.first.card_comments), 1)
        sentence = coh.hold_sentence(PR, coh.read_owners(record(), AUTHOR).groups)
        self.assertIn(sentence, self.first.card_comments[0])
        self.assertIn(sentence, self.first.notes[0])
        self.assertEqual(
            coh.latest_gate_marker(self.first.linear["cards"][CARD]["comments"], PR), "hold")
        self.assertEqual(self.first.linear["cards"][CARD]["state"], "Green Light")

    def test_the_comment_is_posted_before_the_move(self):
        kinds = [c[0] for c in self.first.linear_calls]
        self.assertEqual(kinds, ["comment", "advance"])

    def test_a_second_wake_posts_nothing_and_moves_nothing(self):
        self.assertEqual(self.second.proc.returncode, 0, self.second.out)
        self.assertEqual(self.second.merges, [])
        self.assertEqual(len(self.second.notes), 1, "a second note was posted")
        self.assertEqual(self.second.linear_calls, [], "a second Linear write was made")

    def test_no_pipeline_failure_card_is_filed(self):
        # The step exits 0 (the medic files on a failed run), and the only
        # Linear writes are the park's own comment and move.
        for run in (self.first, self.second):
            self.assertEqual(run.proc.returncode, 0)
            for call in run.linear_calls:
                self.assertIn(call[0], ("comment", "advance"), call)
                self.assertNotIn("Pipeline failure", json.dumps(call))


class TheStubCannotWakeTheGateTest(unittest.TestCase):
    """Rollout safety (card item 5): a repo whose stub does not declare the
    review trigger keeps its card in In Review — the sweep's nudge stays its
    wake — but still gets the note and a clean exit."""

    @classmethod
    def setUpClass(cls):
        cls.shipped = run_shipped(stub=STUB_WITHOUT_REVIEW)

    def test_the_note_is_still_posted_and_the_step_exits_clean(self):
        self.assertEqual(self.shipped.proc.returncode, 0, self.shipped.out)
        self.assertEqual(len(self.shipped.notes), 1)
        self.assertEqual(self.shipped.merges, [])

    def test_the_card_stays_in_review(self):
        self.assertEqual(self.shipped.moves, [])
        self.assertEqual(self.shipped.card_comments, [])
        self.assertEqual(self.shipped.linear["cards"][CARD]["state"], "In Review")

    def test_the_log_says_why_the_park_was_skipped(self):
        self.assertRegex(self.shipped.proc.stdout,
                         r"park skipped.*cannot wake the gate on a review")


class TheReviewLandsTest(unittest.TestCase):
    def test_a_card_the_gate_parked_is_released_then_merged(self):
        parked = coh.card_comment(PR, "Waiting on a review.")
        run = run_shipped(reviews=[review(OWNER, "APPROVED")], card_state="Green Light",
                          card_comments=[{"body": parked, "authored_by_pipeline": True}])
        self.assertEqual(run.proc.returncode, 0, run.out)
        self.assertEqual(run.moves, [["advance", CARD, "Green Light", "In Review"]])
        self.assertEqual(len(run.merges), 1, run.out)
        order = [c[:2] for c in run.calls]
        self.assertLess(order.index(["linear", "advance"]), order.index(["pr", "merge"]),
                        "the card must be released BEFORE the merge path runs")
        self.assertEqual(coh.latest_gate_marker(run.linear["cards"][CARD]["comments"], PR),
                         "release")

    def test_a_card_in_green_light_without_the_gates_marker_is_not_moved(self):
        run = run_shipped(reviews=[review(OWNER, "APPROVED")], card_state="Green Light",
                          card_comments=[{"body": "Sid: holding this for the launch.",
                                          "authored_by_pipeline": False}])
        self.assertEqual(run.moves, [])
        self.assertEqual(run.linear["cards"][CARD]["state"], "Green Light")
        self.assertEqual(len(run.merges), 1, run.out)

    def test_a_marker_someone_else_wrote_is_not_the_gates(self):
        forged = coh.card_comment(PR, "Waiting on a review.")
        run = run_shipped(reviews=[review(OWNER, "APPROVED")], card_state="Green Light",
                          card_comments=[{"body": forged, "authored_by_pipeline": False}])
        self.assertEqual(run.moves, [])


class OtherRefusalsStayLoudTest(unittest.TestCase):
    def test_a_policy_refusal_that_is_not_a_code_owner_review_fails_and_names_what(self):
        rules = [REQUIRED_CHECKS_RULE, {**REQUIRED_CHECKS_RULE, "parameters": {
            "required_status_checks": [{"context": "build"}]}}]
        run = run_shipped(rules=rules, merge_error=POLICY_REFUSAL)
        self.assertEqual(run.proc.returncode, 1, run.out)
        self.assertEqual(len(run.merges), 1)
        self.assertIn("real failure", run.out)
        self.assertRegex(run.out, r"required check 'build': NOT satisfied")
        self.assertRegex(run.out, r"required check 'scripts unit tests': satisfied")
        self.assertIn("code-owner review: not required", run.out)
        self.assertEqual(run.notes, [])
        self.assertEqual(run.linear_calls, [])

    def test_an_unreadable_read_falls_back_to_the_loud_failure(self):
        for name, kwargs in {
            "rules": {"rules_unreadable": True},
            "reviews": {"reviews": None},
            "codeowners": {"codeowners": None},
        }.items():
            with self.subTest(unreadable=name):
                run = run_shipped(merge_error=POLICY_REFUSAL, **kwargs)
                self.assertEqual(run.proc.returncode, 1, run.out)
                self.assertEqual(len(run.merges), 1, "an unknown must fall back to the merge")
                self.assertIn("real failure", run.out)
                self.assertIn("code_owner_review=unknown", run.proc.stdout)
                self.assertEqual(run.notes, [], "never a silent hold")
                self.assertEqual(run.linear_calls, [], "never a Green Light park")
                self.assertIn("UNKNOWN", run.out)


if __name__ == "__main__":
    unittest.main()
