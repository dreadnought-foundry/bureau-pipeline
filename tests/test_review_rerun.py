"""The retry/re-review contract, in one place (DRE-3286).

Two workflow cards follow this one — `review death → retry` and `re-plan →
re-review` — and both of them need the same four answers: should the
post-approval review run again, at what ceiling, how many deaths are too many,
and how is that run asked for. A shared rule the pieces read is exactly what
`standards/card-quality.md` says to split out FIRST, so the siblings cite a
declared answer instead of each inventing one.

One section per acceptance criterion:

  1. `plan_run.fire` with no new arguments produces the payload it produces
     today, byte for byte — the two existing callers are unchanged. With
     `trigger_state`/`reason` both keys appear, which is how a caller asks for
     the ACTIVATE route.
  2. `retry_ceiling` — ×1.5, capped, with an unknown ceiling sized from
     `plan_critic.POST_REVIEW_TURNS_DEFAULT`.
  3. `deaths_since_last_round` — the tombstones a round has not answered yet,
     on the same credential `post_release` reads (pipeline-authored, alone in
     its comment), so a forged 🪦 line buys nothing.
  4. `ceiling_for_run` — a thread ending in a round sizes from the cards; a
     thread ending in a tombstone retries at the bigger ceiling.
  5. `after_death` — retry once, park on the second, and LEAVE any death that
     is not a turn cap to the medic, so the two never both act on one death.
  6. `card_set_diff` — order-insensitive over child identifiers.
  7. The CLI seams the workflows call, and the one that must never fail.
  8. `RERUN_REVIEW_ACT` is an exact string, and it is a whole comment body —
     no notice this pipeline already writes can be mistaken for it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_review_rerun.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import linear_ops  # noqa: E402
import plan_critic as pc  # noqa: E402
import plan_run  # noqa: E402
import review_rerun as rr  # noqa: E402

EPIC = "DRE-3164"

CARD = {
    "id": "uuid-3164",
    "identifier": EPIC,
    "title": "The post-approval review",
    "description": "A plan to review.",
    "labels": {"nodes": [{"name": "agent:planner"}, {"name": "repo:bureau-pipeline"}]},
    "children": {"nodes": [{"identifier": "DRE-3165"}]},
}


def _pipeline(*bodies):
    """A comment thread as `dump-comments --with-authors` writes it."""
    return [{"body": b, "authored_by_pipeline": True} for b in bodies]


def _round(n=1, result=pc.SEND_BACK, reason="no operator step"):
    return pc.marker(pc.STAGE_POST, n, result, reason)


def _death(run="34008698027", ceiling=80, subtype="error_max_turns", turns=81,
           stage=pc.STAGE_POST):
    return pc.death_marker(stage, run, 1, "posta", subtype, turns, ceiling)


class _FiredDispatch:
    """`plan_run.fire`'s gh call, with the payload it wrote kept."""

    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.sent = None

    def __call__(self, argv, **kwargs):
        self.sent = json.load(open(argv[argv.index("--input") + 1]))
        return subprocess.CompletedProcess(argv, self.returncode, "", self.stderr)


def _fire(card=CARD, repo="dreadnought-foundry/bureau-pipeline", **kwargs):
    gh = _FiredDispatch()
    with mock.patch.object(plan_run.subprocess, "run", gh):
        ok, err = plan_run.fire(card, repo, **kwargs)
    return ok, err, gh.sent


# --- 1. The dispatch payload -------------------------------------------------

class TheDispatchPayload(unittest.TestCase):
    """`reconcile.redispatch` and `plan_run.note` must not move at all."""

    #: What `fire` has always sent. Written out rather than computed, so a
    #: change to the builder shows up here as a diff instead of agreeing with
    #: itself.
    TODAY = {
        "card_id": "uuid-3164",
        "identifier": EPIC,
        "title": "The post-approval review",
        "description": "A plan to review.",
        "labels": ["agent:planner", "repo:bureau-pipeline"],
        "url": f"https://linear.app/dreadnoughtfoundry/issue/{EPIC}",
    }

    def test_an_unchanged_caller_sends_an_unchanged_payload(self):
        ok, err, sent = _fire()
        self.assertTrue(ok, err)
        self.assertEqual(sent["event_type"], "agent-plan")
        self.assertEqual(sent["client_payload"], self.TODAY)
        self.assertEqual(json.dumps(sent["client_payload"], sort_keys=True),
                         json.dumps(self.TODAY, sort_keys=True))

    def test_no_trigger_state_key_at_all_when_none_is_asked_for(self):
        """Not `trigger_state: null` — the plan route reads the key's VALUE
        and an explicit null is a key the payload never carried."""
        _ok, _err, sent = _fire()
        self.assertNotIn("trigger_state", sent["client_payload"])
        self.assertNotIn("reason", sent["client_payload"])

    def test_asking_for_the_activate_route_adds_both_keys(self):
        ok, err, sent = _fire(trigger_state=rr.TRIGGER_STATE_ACTIVATE,
                              reason=rr.REASON_REVIEW_RETRY)
        self.assertTrue(ok, err)
        payload = sent["client_payload"]
        self.assertEqual(payload["trigger_state"], "in progress")
        self.assertEqual(payload["reason"], "review-retry")
        # Everything else is untouched.
        self.assertEqual({k: v for k, v in payload.items()
                          if k not in ("trigger_state", "reason")}, self.TODAY)

    def test_the_activate_trigger_state_is_the_word_plan_yml_routes_on(self):
        """The route reads `client_payload.trigger_state == "in progress"`
        with children > 0 — so this constant is that word, lower-cased the way
        the relay sends it, or the dispatch takes the PLAN route instead."""
        with open(os.path.join(ROOT, ".github", "workflows", "plan.yml"),
                  encoding="utf-8") as f:
            plan_yml = f.read()
        self.assertIn(f'[ "$FROM" = "{rr.TRIGGER_STATE_ACTIVATE}" ]', plan_yml)

    def test_activate_payload_is_the_same_builder_the_dispatch_uses(self):
        built = rr.activate_payload(CARD)
        self.assertEqual(built["trigger_state"], rr.TRIGGER_STATE_ACTIVATE)
        _ok, _err, sent = _fire(trigger_state=rr.TRIGGER_STATE_ACTIVATE)
        self.assertEqual(built, sent["client_payload"])


# --- 2. The retry ceiling ----------------------------------------------------

class TheRetryCeiling(unittest.TestCase):

    def test_the_measured_numbers(self):
        self.assertEqual(rr.retry_ceiling(80), 120)
        self.assertEqual(rr.retry_ceiling(120), 180)
        self.assertEqual(rr.retry_ceiling(40), 60)

    def test_the_cap_holds(self):
        self.assertEqual(rr.retry_ceiling(150), rr.POST_REVIEW_RETRY_CAP)
        self.assertEqual(rr.retry_ceiling(10_000), rr.POST_REVIEW_RETRY_CAP)

    def test_an_unknown_ceiling_is_sized_from_the_default_not_from_zero(self):
        """Unknown is unknown (standards/console-honesty.md rule 2). A
        tombstone whose ceiling Linear could not report must not retry at 0."""
        # 150 = ceil(1.5 x POST_REVIEW_TURNS_DEFAULT), which DRE-2785 moved
        # 80 -> 90 with the web-tool grant and DRE-3498 moved 90 -> 100. The
        # property is the equality below; these three are it, written out by
        # value.
        self.assertEqual(rr.retry_ceiling(None), 150)
        self.assertEqual(rr.retry_ceiling("?"), 150)
        self.assertEqual(rr.retry_ceiling(""), 150)
        self.assertEqual(
            rr.retry_ceiling(None),
            rr.retry_ceiling(pc.POST_REVIEW_TURNS_DEFAULT),
            "an unknown ceiling is sized from POST_REVIEW_TURNS_DEFAULT",
        )

    def test_it_rounds_up_never_down(self):
        """`ceil`, so a ceiling never SHRINKS on the retry."""
        self.assertEqual(rr.retry_ceiling(41), 62)
        self.assertGreater(rr.retry_ceiling(41), 41)

    def test_the_multiplier_and_cap_are_the_declared_contract(self):
        self.assertEqual(rr.RETRY_MULTIPLIER, 1.5)
        self.assertEqual(rr.POST_REVIEW_RETRY_CAP, 180)
        self.assertEqual(rr.MAX_DEATHS, 2)


# --- 3. The deaths a round has not answered ---------------------------------

class DeathsSinceTheLastRound(unittest.TestCase):

    def test_a_tombstone_after_a_send_back_round_is_open(self):
        thread = _pipeline(pc.cycle_marker(EPIC), _round(), _death())
        rows = rr.deaths_since_last_round(thread, EPIC)
        self.assertEqual([r["run"] for r in rows], ["34008698027"])
        self.assertEqual(rows[0]["ceiling"], 80)

    def test_a_tombstone_older_than_a_round_is_history(self):
        """The re-run the tombstone asked for happened; that round is the
        record (the same reading `post_release` takes)."""
        thread = _pipeline(pc.cycle_marker(EPIC), _death(), _round(2, pc.PASS))
        self.assertEqual(rr.deaths_since_last_round(thread, EPIC), [])

    def test_two_open_tombstones_come_back_oldest_first(self):
        thread = _pipeline(pc.cycle_marker(EPIC), _round(),
                           _death(run="1", ceiling=80),
                           _death(run="2", ceiling=120))
        self.assertEqual([r["run"] for r in rr.deaths_since_last_round(thread, EPIC)],
                         ["1", "2"])

    def test_a_forged_tombstone_from_another_author_is_not_read(self):
        """Same credential as `post_release`: the pipeline wrote it, and the
        comment says nothing but the record. Anyone with comment access on the
        epic can post a 🪦 line."""
        thread = _pipeline(pc.cycle_marker(EPIC), _round()) + [
            {"body": _death(run="forged"), "authored_by_pipeline": False},
        ]
        self.assertEqual(rr.deaths_since_last_round(thread, EPIC), [])

    def test_a_tombstone_quoted_inside_prose_is_not_a_record(self):
        thread = _pipeline(pc.cycle_marker(EPIC), _round(),
                           "The review died. " + _death() + "\n\nWhat next?")
        self.assertEqual(rr.deaths_since_last_round(thread, EPIC), [])

    def test_a_previous_cycles_tombstone_belongs_to_a_plan_that_is_gone(self):
        thread = _pipeline(_death(run="old"), pc.cycle_marker(EPIC), _round())
        self.assertEqual(rr.deaths_since_last_round(thread, EPIC), [])

    def test_a_pre_stage_tombstone_is_not_this_reviews_death(self):
        thread = _pipeline(pc.cycle_marker(EPIC), _round(),
                           _death(run="pre-1", stage=pc.STAGE_PRE))
        self.assertEqual(rr.deaths_since_last_round(thread, EPIC), [])


# --- 4. The ceiling this run gets -------------------------------------------

class TheCeilingForARun(unittest.TestCase):

    def test_a_thread_ending_in_a_tombstone_retries_at_the_bigger_ceiling(self):
        thread = _pipeline(pc.cycle_marker(EPIC), _round(), _death(ceiling=80))
        turns, why = rr.ceiling_for_run(15, thread, EPIC)
        self.assertEqual(turns, 120)
        self.assertIn("retry after a death at 80: 120", why)

    def test_a_thread_ending_in_a_round_sizes_from_the_cards(self):
        thread = _pipeline(pc.cycle_marker(EPIC), _death(), _round(2))
        turns, why = rr.ceiling_for_run(15, thread, EPIC)
        self.assertEqual(turns, pc.post_review_turns(15))
        self.assertIn("sized from 15 cards", why)

    def test_a_thread_with_no_post_record_at_all_sizes_from_the_cards(self):
        turns, why = rr.ceiling_for_run(3, _pipeline(pc.cycle_marker(EPIC)), EPIC)
        self.assertEqual(turns, pc.post_review_turns(3))
        self.assertIn("sized from 3 cards", why)

    def test_the_sizing_is_never_re_derived(self):
        """`post_review_turns` is DRE-3241's answer and this module imports
        it — a second copy of base+per-card is how two files disagree."""
        for n in (0, 1, 5, 15, 40):
            with self.subTest(n=n):
                turns, _why = rr.ceiling_for_run(n, [], EPIC)
                self.assertEqual(turns, pc.post_review_turns(n))

    def test_a_second_death_retries_from_the_newest_tombstones_ceiling(self):
        thread = _pipeline(pc.cycle_marker(EPIC), _round(),
                           _death(run="1", ceiling=80),
                           _death(run="2", ceiling=120))
        turns, why = rr.ceiling_for_run(15, thread, EPIC)
        self.assertEqual(turns, 180)
        self.assertIn("120", why)


# --- 5. What happens after a death ------------------------------------------

class AfterADeath(unittest.TestCase):

    def test_a_first_turn_cap_death_is_retried(self):
        thread = _pipeline(pc.cycle_marker(EPIC), _round(), _death(run="1"))
        action, why = rr.after_death(thread, EPIC, "error_max_turns")
        self.assertEqual(action, "retry")
        self.assertTrue(why.strip())

    def test_a_second_turn_cap_death_parks_and_names_every_run(self):
        thread = _pipeline(pc.cycle_marker(EPIC), _round(),
                           _death(run="34008698027"), _death(run="34010101010"))
        action, why = rr.after_death(thread, EPIC, "error_max_turns")
        self.assertEqual(action, "park")
        self.assertIn("34008698027", why)
        self.assertIn("34010101010", why)

    def test_a_non_turn_death_is_left_to_the_medic(self):
        """The medic retries a non-turn death once already, and refuses a
        turn-cap one (`medic_retry.RULE_TURN_EXHAUSTION`). The two must never
        both act on one death."""
        thread = _pipeline(pc.cycle_marker(EPIC), _round(),
                           _death(subtype="error_during_execution"))
        action, why = rr.after_death(thread, EPIC, "error_during_execution")
        self.assertEqual(action, "leave")
        self.assertIn("medic", why.lower())

    def test_a_second_non_turn_death_is_still_the_medics(self):
        thread = _pipeline(pc.cycle_marker(EPIC), _round(),
                           _death(run="1", subtype="error_during_execution"),
                           _death(run="2", subtype="error_during_execution"))
        action, _why = rr.after_death(thread, EPIC, "error_during_execution")
        self.assertEqual(action, "leave")

    def test_an_unknown_subtype_is_left_alone(self):
        thread = _pipeline(pc.cycle_marker(EPIC), _round(), _death(subtype=None))
        self.assertEqual(rr.after_death(thread, EPIC, "")[0], "leave")

    def test_a_round_since_the_deaths_refunds_the_death_count(self):
        """A tombstone a round already answered is history — a later death is
        a FIRST death again, exactly as `post_release` reads the same thread."""
        thread = _pipeline(pc.cycle_marker(EPIC), _death(run="1"), _round(2),
                           _death(run="2"))
        action, _why = rr.after_death(thread, EPIC, "error_max_turns")
        self.assertEqual(action, "retry")

    def test_the_park_note_is_plain_english_and_names_the_runs(self):
        thread = _pipeline(pc.cycle_marker(EPIC), _round(),
                           _death(run="1"), _death(run="2"))
        note = rr.park_note(thread, EPIC)
        self.assertIn(EPIC, note)
        self.assertIn("1", note)
        self.assertIn("2", note)
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, note)

    def test_the_park_note_ends_on_the_ask_not_on_a_dangling_condition(self):
        """`plan_critic.REAPPROVE_HOW` is a sentence with its own clauses
        (DRE-3292), so the condition it is asked under goes BEFORE it — a
        trailing "once it is smaller" reads as a condition on the last clause
        of the quoted sentence rather than on the ask."""
        note = rr.park_note(_pipeline(pc.cycle_marker(EPIC), _death(run="1")),
                            EPIC)
        self.assertIn(pc.REAPPROVE_HOW, note)
        self.assertTrue(note.rstrip().endswith(pc.REAPPROVE_HOW + "."), note[-200:])
        self.assertLess(note.index("once it is smaller"),
                        note.index(pc.REAPPROVE_HOW))


# --- 6. Which cards changed --------------------------------------------------

class TheCardSetDiff(unittest.TestCase):

    def test_the_same_cards_in_a_different_order_are_unchanged(self):
        d = rr.card_set_diff(["DRE-1", "DRE-2"], ["DRE-2", "DRE-1"])
        self.assertFalse(d["changed"])
        self.assertEqual(d["added"], [])
        self.assertEqual(d["removed"], [])

    def test_an_added_card_is_reported(self):
        d = rr.card_set_diff(["DRE-1"], ["DRE-1", "DRE-2"])
        self.assertTrue(d["changed"])
        self.assertEqual(d["added"], ["DRE-2"])
        self.assertEqual(d["removed"], [])

    def test_a_removed_card_is_reported(self):
        d = rr.card_set_diff(["DRE-1", "DRE-2"], ["DRE-1"])
        self.assertTrue(d["changed"])
        self.assertEqual(d["added"], [])
        self.assertEqual(d["removed"], ["DRE-2"])

    def test_a_replaced_card_is_both(self):
        d = rr.card_set_diff(["DRE-1"], ["DRE-2"])
        self.assertEqual((d["added"], d["removed"]), (["DRE-2"], ["DRE-1"]))

    def test_empty_on_both_sides_is_unchanged(self):
        self.assertFalse(rr.card_set_diff([], [])["changed"])


# --- 7. The CLI the workflows call ------------------------------------------

class TheCli(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _file(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _run(self, *args, stdin=""):
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "review_rerun.py"), *args],
            input=stdin, capture_output=True, text=True,
        )

    def test_ceiling_writes_max_turns(self):
        thread = self._file("thread.json", json.dumps(
            _pipeline(pc.cycle_marker(EPIC), _round(), _death(ceiling=80))))
        gho = os.path.join(self.tmp, "gho")
        out = self._run("ceiling", "--epic", EPIC, "--children", "15",
                        "--thread-file", thread, "--github-output", gho)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("max_turns=120", open(gho).read())

    def test_ceiling_exits_zero_on_an_unreadable_thread_file(self):
        """A sizing that failed degrades to `post_review_turns`; it never
        wedges the review (`plan_critic post-turns`' rule)."""
        gho = os.path.join(self.tmp, "gho")
        out = self._run("ceiling", "--epic", EPIC, "--children", "15",
                        "--thread-file", os.path.join(self.tmp, "nope.json"),
                        "--github-output", gho)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(f"max_turns={pc.post_review_turns(15)}", open(gho).read())

    def test_ceiling_exits_zero_on_a_thread_file_that_is_not_json(self):
        thread = self._file("thread.json", "{not json at all")
        gho = os.path.join(self.tmp, "gho")
        out = self._run("ceiling", "--epic", EPIC, "--children", "",
                        "--thread-file", thread, "--github-output", gho)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(f"max_turns={pc.POST_REVIEW_TURNS_DEFAULT}", open(gho).read())

    def test_after_death_writes_the_action_and_the_runs(self):
        thread = self._file("thread.json", json.dumps(
            _pipeline(pc.cycle_marker(EPIC), _round(),
                      _death(run="1"), _death(run="2"))))
        gho = os.path.join(self.tmp, "gho")
        note = os.path.join(self.tmp, "note.md")
        out = self._run("after-death", "--epic", EPIC, "--thread-file", thread,
                        "--subtype", "error_max_turns", "--github-output", gho,
                        "--note-file", note)
        self.assertEqual(out.returncode, 0, out.stderr)
        written = open(gho).read()
        self.assertIn("action=park", written)
        self.assertIn("runs=1,2", written)
        # One line per output, always — no note can smuggle its own.
        self.assertTrue(all("=" in line for line in written.strip().splitlines()))
        body = open(note).read()
        self.assertIn("1", body)
        self.assertIn("2", body)

    def test_after_death_writes_retry_on_a_first_turn_death(self):
        thread = self._file("thread.json", json.dumps(
            _pipeline(pc.cycle_marker(EPIC), _round(), _death(run="1"))))
        gho = os.path.join(self.tmp, "gho")
        out = self._run("after-death", "--epic", EPIC, "--thread-file", thread,
                        "--subtype", "error_max_turns", "--github-output", gho)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("action=retry", open(gho).read())

    def test_after_death_writes_leave_for_a_death_that_is_the_medics(self):
        thread = self._file("thread.json", json.dumps(
            _pipeline(pc.cycle_marker(EPIC), _round(),
                      _death(subtype="error_during_execution"))))
        gho = os.path.join(self.tmp, "gho")
        out = self._run("after-death", "--epic", EPIC, "--thread-file", thread,
                        "--subtype", "error_during_execution",
                        "--github-output", gho)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("action=leave", open(gho).read())

    def test_card_set_reads_the_children_json_files(self):
        before = self._file("before.json", json.dumps([
            {"identifier": "DRE-1", "body": "", "labels": [], "parent": EPIC},
            {"identifier": "DRE-2", "body": "", "labels": [], "parent": EPIC},
        ]))
        after = self._file("after.json", json.dumps([
            {"identifier": "DRE-2", "body": "", "labels": [], "parent": EPIC},
            {"identifier": "DRE-3", "body": "", "labels": [], "parent": EPIC},
        ]))
        gho = os.path.join(self.tmp, "gho")
        out = self._run("card-set", "--before", before, "--after", after,
                        "--github-output", gho)
        self.assertEqual(out.returncode, 0, out.stderr)
        written = open(gho).read()
        self.assertIn("changed=true", written)
        self.assertIn("added=DRE-3", written)
        self.assertIn("removed=DRE-1", written)

    def test_card_set_reports_false_when_only_the_order_moved(self):
        before = self._file("before.json", json.dumps([
            {"identifier": "DRE-1"}, {"identifier": "DRE-2"}]))
        after = self._file("after.json", json.dumps([
            {"identifier": "DRE-2"}, {"identifier": "DRE-1"}]))
        gho = os.path.join(self.tmp, "gho")
        out = self._run("card-set", "--before", before, "--after", after,
                        "--github-output", gho)
        self.assertEqual(out.returncode, 0, out.stderr)
        written = open(gho).read()
        self.assertIn("changed=false", written)
        self.assertIn("added=\n", written)
        self.assertIn("removed=\n", written)


class TheDispatchSubcommand(unittest.TestCase):
    """DRE-2034: a receipt is never written on an unconfirmed dispatch."""

    def _dispatch(self, returncode=0, stderr=""):
        gh = _FiredDispatch(returncode, stderr)
        with mock.patch.object(linear_ops, "gql",
                               return_value={"issue": CARD}) as gql, \
                mock.patch.object(plan_run.subprocess, "run", gh):
            rc = rr.main(["dispatch", "--epic", EPIC, "--repo", "o/n",
                          "--reason", rr.REASON_RE_REVIEW])
        return rc, gh, gql

    def test_it_reads_the_card_with_plan_runs_own_query(self):
        rc, gh, gql = self._dispatch()
        self.assertEqual(rc, 0)
        self.assertEqual(gql.call_args[0][0], plan_run.CARD_QUERY)
        self.assertEqual(gql.call_args[0][1], {"id": EPIC})

    def test_it_asks_for_the_activate_route_with_the_reason(self):
        _rc, gh, _gql = self._dispatch()
        self.assertEqual(gh.sent["client_payload"]["trigger_state"], "in progress")
        self.assertEqual(gh.sent["client_payload"]["reason"], "re-review")

    def test_a_failed_dispatch_exits_non_zero(self):
        rc, _gh, _gql = self._dispatch(returncode=1, stderr="HTTP 403")
        self.assertNotEqual(rc, 0)

    def test_an_unreadable_card_exits_non_zero_rather_than_crashing(self):
        with mock.patch.object(linear_ops, "gql", side_effect=RuntimeError("boom")):
            rc = rr.main(["dispatch", "--epic", EPIC, "--repo", "o/n",
                          "--reason", rr.REASON_RE_REVIEW])
        self.assertNotEqual(rc, 0)


# --- 8. The act, and the strings the siblings share -------------------------

class TheSharedStrings(unittest.TestCase):
    """The relay (agent-bureau) and the console mirror these byte for byte."""

    def test_the_constants_are_the_declared_contract(self):
        self.assertEqual(rr.TRIGGER_STATE_ACTIVATE, "in progress")
        self.assertEqual(rr.REASON_REVIEW_RETRY, "review-retry")
        self.assertEqual(rr.REASON_RE_REVIEW, "re-review")
        self.assertEqual(rr.REASON_RERUN_ACT, "re-run")
        self.assertEqual(rr.RERUN_REVIEW_ACT, "▶️ re-run the review")

    def test_the_act_is_the_whole_comment_body_or_it_is_not_the_act(self):
        self.assertTrue(rr.is_rerun_act(rr.RERUN_REVIEW_ACT))
        self.assertTrue(rr.is_rerun_act(f"  {rr.RERUN_REVIEW_ACT}\n"))
        self.assertFalse(rr.is_rerun_act(
            f"I think we should {rr.RERUN_REVIEW_ACT} tomorrow."))
        self.assertFalse(rr.is_rerun_act(
            f"{rr.RERUN_REVIEW_ACT}\n\n...but not until Monday."))
        self.assertFalse(rr.is_rerun_act(""))
        self.assertFalse(rr.is_rerun_act(None))

    def test_no_notice_plan_critic_writes_is_the_act(self):
        """A notice that happened to BE the act line would re-run the review
        every time the pipeline posted it."""
        row = pc.parse_deaths([pc.death_marker(
            pc.STAGE_POST, "34008698027", 1, "posta", "error_max_turns", 41, 40)])[0]
        notices = [
            pc.death_note(EPIC, row),
            pc.cycle_start_note(EPIC),
            pc.death_marker(pc.STAGE_POST, "1", 1, "posta", "error_max_turns", 41, 40),
            pc.marker(pc.STAGE_POST, 1, pc.SEND_BACK, "no operator step"),
            pc.cycle_marker(EPIC),
            pc.promotion_refusal("DRE-3165", EPIC, "2026-09-06T00:00:00Z",
                                 _pipeline(pc.cycle_marker(EPIC))) or "",
            pc.promotion_refusal("DRE-3165", EPIC, "2026-09-06T00:00:00Z",
                                 _pipeline(pc.cycle_marker(EPIC),
                                           pc.death_marker(pc.STAGE_POST, "1", 1,
                                                           "posta",
                                                           "error_max_turns",
                                                           41, 40))) or "",
            pc.promotion_refusal("DRE-3165", EPIC, "2026-09-06T00:00:00Z",
                                 _pipeline(pc.cycle_marker(EPIC), _round())) or "",
        ]
        for notice in notices:
            with self.subTest(notice=notice[:60]):
                self.assertFalse(rr.is_rerun_act(notice))

    def test_the_module_reads_plan_critic_rather_than_re_deriving_it(self):
        with open(os.path.join(SCRIPTS, "review_rerun.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertIn("import plan_critic", source)
        for reused in ("parse_deaths", "current_cycle", "post_release",
                       "post_review_turns", "trusted_bodies"):
            self.assertIn(f"plan_critic.{reused}", source)


if __name__ == "__main__":
    unittest.main()
