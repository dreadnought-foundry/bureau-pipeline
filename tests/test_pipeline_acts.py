"""RED-first tests: one registry declares every autonomous act, and one module
writes its receipt (DRE-2825).

Today every autonomous act announces itself in prose and every reader scrapes
that prose independently: ten tag constants live scattered through
`reconcile.py`, the console pattern-matches separate string markers, and a
sweep decides "was that the bot" from an emoji prefix. The vocabulary is
remembered in six places instead of declared in one.

`config/pipeline-acts.json` is the one declaration and `scripts/pipeline_act.py`
is the one reader and writer, built to the shape of
`config/routing-verdicts.json` / `scripts/routing_verdict.py` rather than to a
second one invented here.

THE TRAILER IS ADDITIVE, AND THAT IS THE PART TO GET RIGHT. The tags in
`reconcile.py` are NOT prose: they are idempotency keys and per-sha budget
counters (`_worker_receipt_count` enforces `CRASHED_REVIEW_RETRY_CAP` and
`DEPENDABOT_RECEIPT_CAP` by counting receipts that contain the tag). Change the
grammar and every in-flight PR's existing receipts become invisible — budgets
re-arm from zero and suppressed comments re-post. So the registry ADOPTS those
tags as data, `receipt()` reproduces the existing body BYTE-IDENTICALLY, and
nothing here deletes a constant.

WHAT THIS PINS, one section per acceptance criterion:

  1. Every act the pipeline takes has a row — the ten `reconcile.py` tags (the
     card says nine; `STALE_VERDICT_TAG` is the tenth), the fix loop's attempt
     and blocker notices, the restart receipt, the crashed-review redispatch,
     the conflict sweep and the medic.
  2. `receipt()` reproduces the live body byte-identically and only APPENDS a
     machine-readable trailer.
  3. A tag in the registry that no code emits fails; a tag emitted that the
     registry does not declare fails. Both directions, mechanically.
  4. `python3 scripts/pipeline_act.py check` self-checks the file, the way
     `routing_verdict.py check` does.
  5. The trailer can never carry ANOTHER act's tag — that is the exact
     mechanism by which an additive trailer would stop being additive.

Run: cd bureau-pipeline && python3 -m pytest tests/test_pipeline_acts.py -v
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import pipeline_act  # noqa: E402

CONFIG = ROOT / "config" / "pipeline-acts.json"
RECONCILE = ROOT / "scripts" / "reconcile.py"

# The tag constants defined in reconcile.py, read the way the reverse check
# reads them: module-level `NAME_TAG = "literal"` only. The aliases
# (`DEAD_TAG = dead_run.DEAD_TAG`) are not literals and belong to their own
# module, so they are deliberately outside this scope.
_TAG_CONSTANT = re.compile(r'(?m)^([A-Z][A-Z0-9_]*_TAG)\s*=\s*"([^"]+)"')


def _doc() -> dict:
    return copy.deepcopy(pipeline_act.load())


# --------------------------------------------------------------------------- #
# 1. every act has a row                                                       #
# --------------------------------------------------------------------------- #


class TestEveryActHasARow:
    def test_the_registry_file_exists_and_parses(self):
        assert CONFIG.exists(), "config/pipeline-acts.json is the declaration"
        json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_load_is_the_one_reader(self):
        doc = pipeline_act.load()
        assert doc["acts"], "the registry declares at least one act"

    def test_acts_are_returned_in_file_order(self):
        names = pipeline_act.acts()
        declared = [a["name"] for a in pipeline_act.load()["acts"]]
        assert list(names) == declared

    def test_every_reconcile_tag_constant_has_a_row(self):
        """The ten constants scattered through reconcile.py, declared once."""
        emitted = {tag for _, tag in _TAG_CONSTANT.findall(RECONCILE.read_text("utf-8"))}
        assert len(emitted) == 10, (
            "reconcile.py should still define ten tag constants; if that "
            "changed, the registry changes with it"
        )
        declared = {pipeline_act.tag(name) for name in pipeline_act.acts()}
        assert emitted <= declared, f"undeclared: {sorted(emitted - declared)}"

    @pytest.mark.parametrize(
        "tag",
        [
            "unresolvable-blocker-reference",
            "stranded-watchdog",
            "intake-aged",
            "no-checks-watchdog",
            "unlanded-work-watchdog",
            "fix-restart-on-operator-decision",
            "dependabot-review-dispatch",
            "crashed-review-redispatch",
            "reviewer-down",
            "stale-verdict-watchdog",
        ],
    )
    def test_each_existing_idempotency_key_is_adopted_not_replaced(self, tag):
        names = [n for n in pipeline_act.acts() if pipeline_act.tag(n) == tag]
        assert len(names) == 1, f"exactly one act carries {tag!r}"
        assert pipeline_act.record(names[0])["adopted"] is True, (
            f"{tag!r} is a live idempotency key and a per-sha budget counter — "
            "the registry adopts it as data, it does not replace it"
        )

    @pytest.mark.parametrize(
        "emitter,anchor",
        [
            (".github/workflows/agent-fix.yml", "Conflict-resolution agent dispatched (round"),
            (".github/workflows/agent-fix.yml", "pushed — CI and critic review re-running."),
            (".github/workflows/agent-fix.yml", "blocked: $(cat /tmp/fix-blocker.txt)"),
            (".github/workflows/medic.yml", 'linear_ops.py comment DRE-N "<report>"'),
        ],
    )
    def test_the_acts_outside_reconcile_have_rows_too(self, emitter, anchor):
        """The fix loop's attempt and blocker notices, the conflict sweep and
        the medic. None of them carries an idempotency key today, so each
        declares a NEW tag and pins the live wording it will be attached to."""
        hits = [
            n for n in pipeline_act.acts()
            if pipeline_act.record(n)["emits"]["file"] == emitter
            and anchor in pipeline_act.record(n)["emits"]["anchor"]
        ]
        assert len(hits) == 1, f"one act owns {anchor!r} in {emitter}"

    def test_every_act_declares_the_six_fields_the_card_names(self):
        for name in pipeline_act.acts():
            entry = pipeline_act.record(name)
            assert entry["tag"].strip()
            assert entry["kind"] in pipeline_act.kinds()
            assert entry["state"] in pipeline_act.states()
            assert entry["next_actor"].strip()
            assert "discharges" in entry, "absent is null, never missing"
            assert entry["subscriber"].strip()


# --------------------------------------------------------------------------- #
# 2. the trailer is ADDITIVE — the body survives byte-identical                #
# --------------------------------------------------------------------------- #


class TestTheTrailerIsAdditive:
    def test_receipt_reproduces_the_live_body_byte_identically(self):
        """The console tests pin some of this wording verbatim, and every
        reconcile tag is an idempotency key. A receipt that rewrote one word
        of the body would make every in-flight PR's existing receipts
        invisible: budgets re-arm from zero and suppressed comments re-post."""
        for name in pipeline_act.acts():
            body = pipeline_act.record(name)["emits"]["anchor"]
            out = pipeline_act.receipt(name, body)
            assert out.startswith(body), f"{name}: the body was rewritten"
            assert out[: len(body)] == body

    def test_receipt_only_appends(self):
        """Adversarial on purpose: leading and trailing whitespace, an inner
        double space, and no trailing newline. A `.strip()` anywhere in the
        writer changes the body, and a changed body is an idempotency key that
        no longer covers the receipt it was counted on."""
        body = "  \U0001f6a8 leading space, inner  gap, no trailing newline  "
        for name in pipeline_act.acts():
            out = pipeline_act.receipt(name, body)
            trailer = pipeline_act.trailer(name)
            assert out == f"{body}\n\n{trailer}"
            assert len(out) == len(body) + 2 + len(trailer)

    def test_the_pinned_wording_is_still_the_live_wording(self):
        """The pin tracks the source. Reword a receipt in reconcile.py or
        agent-fix.yml without updating the registry and this goes red — which
        is the only thing that stops the registry drifting into a description
        of what the pipeline used to say."""
        for name in pipeline_act.acts():
            emits = pipeline_act.record(name)["emits"]
            text = (ROOT / emits["file"]).read_text(encoding="utf-8")
            assert text.count(emits["anchor"]) == 1, (
                f"{name}: {emits['anchor']!r} must appear exactly once in "
                f"{emits['file']} — an ambiguous anchor pins nothing"
            )

    def test_the_trailer_is_machine_readable(self):
        for name in pipeline_act.acts():
            fields = pipeline_act.read_trailer(pipeline_act.trailer(name))
            entry = pipeline_act.record(name)
            assert fields["act"] == name
            assert fields["tag"] == entry["tag"]
            assert fields["kind"] == entry["kind"]
            assert fields["state"] == entry["state"]
            assert fields["next"] == entry["next_actor"]
            assert fields["subscriber"] == entry["subscriber"]

    def test_read_trailer_ignores_a_body_that_carries_none(self):
        assert pipeline_act.read_trailer("🚨 stranded-watchdog: nothing here") is None

    def test_receipt_refuses_an_unknown_act(self):
        with pytest.raises(pipeline_act.UnknownAct):
            pipeline_act.receipt("no-such-act", "body")

    def test_receipt_refuses_an_empty_body(self):
        with pytest.raises(pipeline_act.ActError):
            pipeline_act.receipt(pipeline_act.acts()[0], "   ")


# --------------------------------------------------------------------------- #
# 3. both directions of the tag binding                                        #
# --------------------------------------------------------------------------- #


class TestTagsBindBothWays:
    def test_a_declared_tag_no_code_emits_fails(self):
        doc = _doc()
        adopted = next(a for a in doc["acts"] if a["adopted"])
        adopted["tag"] = "a-tag-nothing-has-ever-emitted"
        assert any(
            "a-tag-nothing-has-ever-emitted" in p for p in pipeline_act.problems(doc)
        )

    def test_an_emitted_tag_the_registry_does_not_declare_fails(self):
        doc = _doc()
        doc["acts"] = [a for a in doc["acts"] if a["tag"] != "reviewer-down"]
        assert any(
            "reviewer-down" in p for p in pipeline_act.problems(doc)
        ), "a live tag with no row must fail, or the registry is not the registry"

    def test_a_tag_declared_as_not_yet_emitted_must_really_be_absent(self):
        """The other half of the same honesty. An act whose emission is a later
        card declares `adopted: false`, and the check proves the tag is
        genuinely nowhere — so the registry cannot quietly claim an emission it
        does not have, and the emission card flips the flag."""
        doc = _doc()
        pending = next(a for a in doc["acts"] if not a["adopted"])
        pending["tag"] = "stranded-watchdog"
        assert any("stranded-watchdog" in p for p in pipeline_act.problems(doc))

    def test_the_declared_anchor_must_be_in_the_declared_file(self):
        doc = _doc()
        doc["acts"][0]["emits"]["anchor"] = "wording no file has ever carried"
        assert any("anchor" in p for p in pipeline_act.problems(doc))

    def test_a_missing_emitter_file_fails(self):
        doc = _doc()
        doc["acts"][0]["emits"]["file"] = "scripts/does_not_exist.py"
        assert any("does_not_exist" in p for p in pipeline_act.problems(doc))


# --------------------------------------------------------------------------- #
# 4. the file checks itself                                                    #
# --------------------------------------------------------------------------- #


class TestTheFileChecksItself:
    def test_the_shipped_file_is_clean(self):
        assert pipeline_act.problems() == []

    def test_check_command_exits_zero(self):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "pipeline_act.py"), "check"],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        )
        assert r.returncode == 0, r.stdout + r.stderr

    def test_check_is_the_default_command(self):
        assert pipeline_act.main([]) == 0

    def test_an_unknown_kind_fails(self):
        doc = _doc()
        doc["acts"][0]["kind"] = "vibes"
        assert any("vibes" in p for p in pipeline_act.problems(doc))

    def test_an_unknown_state_fails(self):
        doc = _doc()
        doc["acts"][0]["state"] = "somewhere"
        assert any("somewhere" in p for p in pipeline_act.problems(doc))

    def test_a_next_actor_outside_the_lane_contract_fails(self):
        """Bound to `config/lane-contract.json`'s writer glossary, the way
        routing verdicts bind their actor: an act that hands the work to
        somebody the contract has never heard of is a dead end."""
        doc = _doc()
        doc["acts"][0]["next_actor"] = "the night shift"
        assert any("the night shift" in p for p in pipeline_act.problems(doc))
        assert "the night shift" not in lane_contract.writers()

    def test_discharging_an_undeclared_act_fails(self):
        doc = _doc()
        doc["acts"][0]["discharges"] = "an-obligation-nobody-declared"
        assert any(
            "an-obligation-nobody-declared" in p for p in pipeline_act.problems(doc)
        )

    def test_an_empty_subscriber_fails(self):
        doc = _doc()
        doc["acts"][0]["subscriber"] = ""
        assert any("subscriber" in p for p in pipeline_act.problems(doc))

    def test_two_acts_sharing_a_tag_fails(self):
        doc = _doc()
        doc["acts"][1]["tag"] = doc["acts"][0]["tag"]
        assert any("twice" in p or "two acts" in p for p in pipeline_act.problems(doc))

    def test_a_tag_that_is_a_substring_of_another_tag_fails(self):
        """`tag in body` is how every one of these is counted. A tag contained
        in another tag makes one act's receipt count as the other's, which is
        the budget-counter failure with the grammar left untouched."""
        doc = _doc()
        doc["acts"][0]["tag"] = "reviewer-down-again"
        assert any("reviewer-down" in p for p in pipeline_act.problems(doc))


# --------------------------------------------------------------------------- #
# 5. the trailer can never carry another act's tag                             #
# --------------------------------------------------------------------------- #


class TestTheTrailerCarriesNoForeignTag:
    def test_no_trailer_contains_another_acts_tag(self):
        tags = {n: pipeline_act.tag(n) for n in pipeline_act.acts()}
        for name in pipeline_act.acts():
            body = pipeline_act.trailer(name)
            for other, tag in tags.items():
                if other == name:
                    continue
                assert tag not in body, (
                    f"{name}'s trailer carries {other}'s live key {tag!r} — "
                    "an idempotency check keyed on that tag would read this "
                    "receipt as the other act's, and suppress it forever"
                )

    def test_the_check_catches_a_name_that_smuggles_a_foreign_tag(self):
        doc = _doc()
        doc["acts"][0]["name"] = "answers-the-stranded-watchdog"
        assert any("stranded-watchdog" in p for p in pipeline_act.problems(doc))

    def test_the_check_catches_a_discharge_that_smuggles_a_foreign_tag(self):
        """`discharges` names an ACT, never a tag — for exactly this reason."""
        doc = _doc()
        for act in doc["acts"]:
            if act["name"] == "reviewer-unavailable":
                act["name"] = "reviewer-down-report"
            if act.get("discharges") == "reviewer-unavailable":
                act["discharges"] = "reviewer-down-report"
        assert any("reviewer-down" in p for p in pipeline_act.problems(doc))

    def test_nothing_changes_behaviour_yet(self):
        """This card lands the vocabulary and the writer and nothing else.
        No call site emits a trailer, so no live receipt changes shape — which
        is what makes the foundation card safe to go first.

        The card that wires emission DELETES this test. That is the point of
        it: the change of behaviour becomes a deliberate act with a diff on it,
        rather than something that leaks in while nobody is looking.
        """
        for path in sorted((ROOT / "scripts").glob("*.py")):
            if path.name == "pipeline_act.py":
                continue
            assert "pipeline_act" not in path.read_text(encoding="utf-8"), (
                f"{path.name} imports the writer — emission is its own card"
            )

    def test_no_existing_constant_was_deleted(self):
        text = RECONCILE.read_text(encoding="utf-8")
        for constant in (
            "BAD_REF_TAG", "WATCHDOG_TAG", "INTAKE_AGED_TAG", "NO_CHECKS_TAG",
            "UNLANDED_TAG", "DECISION_RESTART_TAG", "DEPENDABOT_DISPATCH_TAG",
            "CRASHED_REVIEW_DISPATCH_TAG", "REVIEWER_DOWN_TAG",
            "STALE_VERDICT_TAG", "_AGENT_COMMENT_PREFIXES",
        ):
            assert f"{constant} = " in text or f"{constant}=" in text, (
                f"{constant} was deleted — this card deletes nothing. "
                "_AGENT_COMMENT_PREFIXES in particular stays until every "
                "commenter emits a trailer; its removal is a later card."
            )
