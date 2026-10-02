"""The What's New contract: the pull request line, the file, the switch (DRE-5508).

`scripts/whats_new.py` is the one module the gate, the critic, the collector
and the train import, so these tests pin the names and the behavior the
sibling cards build against.

The cutover is tested through its seams only — `path=` on `enforced_from`,
`cutover=` on `enforced_for`, or `whats_new.CUTOVER_FILE` pointed at a
temporary path. Nothing here asserts whether `config/whats-new-cutover.json`
exists in the checkout: DRE-5576 creates it, and a test that assumed its
absence would go red the day the rule switches on.
"""

from __future__ import annotations

import copy
import inspect
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import whats_new  # noqa: E402
from whats_new import Entry, WhatsNewError  # noqa: E402

SCRIPT = ROOT / "scripts" / "whats_new.py"
EXAMPLE = ROOT / "docs" / "whats-new.example.json"
STANDARD = ROOT / "standards" / "whats-new.md"


def _example() -> dict:
    return json.loads(EXAMPLE.read_text())


# --- the exported names ------------------------------------------------------


class TestTheNames:
    def test_kinds_and_audiences_are_the_contract(self):
        assert whats_new.KINDS == ("new", "improved", "fixed")
        assert whats_new.AUDIENCES == ("everyone", "moderators", "admins")

    def test_exempt_prefixes_are_the_three_machine_branches(self):
        assert whats_new.EXEMPT_PREFIXES == ("dependabot/", "repair/", "bot/")

    def test_the_cutover_file_lives_in_config_at_the_repo_root(self):
        assert whats_new.CUTOVER_FILE == ROOT / "config" / "whats-new-cutover.json"

    def test_internal_words_are_the_contract(self):
        assert whats_new.INTERNAL_WORDS == (
            "PR", "pull request", "merge", "merged", "commit", "refactor", "CI",
            "workflow", "endpoint", "schema", "migration", "card", "branch",
        )

    def test_the_error_is_a_value_error(self):
        assert issubclass(WhatsNewError, ValueError)


# --- parse_line --------------------------------------------------------------


class TestParseLine:
    def test_none_is_none(self):
        assert whats_new.parse_line("What's new: none\n\nCloses DRE-1.") is None

    def test_a_full_sentence_with_body_and_open(self):
        body = (
            "What's new: improved, everyone: Searching a document now finds "
            "words inside tables. Tables were skipped before. (open: /documents)\n"
        )
        assert whats_new.parse_line(body) == Entry(
            kind="improved",
            audience="everyone",
            title="Searching a document now finds words inside tables.",
            body="Tables were skipped before.",
            open="/documents",
        )

    def test_the_optional_parts_default(self):
        entry = whats_new.parse_line(
            "What's new: fixed, moderators: Approving a flagged post no "
            "longer hides the next one in the queue."
        )
        assert entry == Entry(
            kind="fixed",
            audience="moderators",
            title="Approving a flagged post no longer hides the next one in the queue.",
            body="",
            open=None,
        )

    def test_open_without_a_body(self):
        entry = whats_new.parse_line(
            "What's new: new, admins: You can export the member list. (open: /admin/members)"
        )
        assert entry.title == "You can export the member list."
        assert entry.body == ""
        assert entry.open == "/admin/members"

    def test_a_body_without_open(self):
        entry = whats_new.parse_line(
            "What's new: new, everyone: Posts can be pinned. Only the author can pin."
        )
        assert entry.body == "Only the author can pin."
        assert entry.open is None

    def test_the_line_need_not_be_the_first_line(self):
        body = "Adds table search.\n\nWhat's new: none\n"
        assert whats_new.parse_line(body) is None

    @pytest.mark.parametrize("label", [
        "What's new:", "What’s new:", "**What's new:**", "**What’s new:**",
    ])
    def test_the_label_variants(self, label):
        entry = whats_new.parse_line(f"{label} new, everyone: Posts can be pinned.")
        assert entry.kind == "new"
        assert whats_new.parse_line(f"{label} none") is None

    def test_a_line_inside_a_fenced_block_is_ignored(self):
        body = (
            "```\n"
            "What's new: none\n"
            "```\n"
            "What's new: fixed, everyone: Saving a draft no longer loses the title.\n"
        )
        entry = whats_new.parse_line(body)
        assert entry is not None and entry.kind == "fixed"

    def test_a_tilde_fence_hides_the_line_too(self):
        body = "~~~text\nWhat's new: none\n~~~\nno line here\n"
        with pytest.raises(WhatsNewError):
            whats_new.parse_line(body)

    def test_a_line_only_inside_a_fence_is_no_line(self):
        with pytest.raises(WhatsNewError, match="no .What's new:. line"):
            whats_new.parse_line("```md\nWhat's new: none\n```\n")

    def test_the_first_matching_line_wins(self):
        body = (
            "What's new: none\n"
            "What's new: new, everyone: Posts can be pinned.\n"
        )
        assert whats_new.parse_line(body) is None
        flipped = (
            "What's new: new, everyone: Posts can be pinned.\n"
            "What's new: none\n"
        )
        assert whats_new.parse_line(flipped).title == "Posts can be pinned."

    def test_a_label_mid_line_is_not_a_line(self):
        with pytest.raises(WhatsNewError):
            whats_new.parse_line("> quoted card says What's new: none\n")

    def test_no_line_raises(self):
        with pytest.raises(WhatsNewError, match="no .What's new:. line"):
            whats_new.parse_line("Adds table search.\n\nCloses DRE-1.")

    def test_an_empty_body_raises(self):
        with pytest.raises(WhatsNewError):
            whats_new.parse_line("")

    def test_an_unknown_kind_raises(self):
        with pytest.raises(WhatsNewError, match="changed"):
            whats_new.parse_line("What's new: changed, everyone: Posts can be pinned.")

    def test_an_unknown_audience_raises(self):
        with pytest.raises(WhatsNewError, match="staff"):
            whats_new.parse_line("What's new: new, staff: Posts can be pinned.")

    def test_a_missing_sentence_raises(self):
        with pytest.raises(WhatsNewError, match="sentence"):
            whats_new.parse_line("What's new: new, everyone:")

    def test_a_missing_sentence_with_only_open_raises(self):
        with pytest.raises(WhatsNewError, match="sentence"):
            whats_new.parse_line("What's new: new, everyone: (open: /documents)")

    def test_a_title_without_a_period_raises(self):
        with pytest.raises(WhatsNewError, match="period"):
            whats_new.parse_line("What's new: new, everyone: Posts can be pinned")

    def test_a_title_ending_in_a_question_mark_raises(self):
        with pytest.raises(WhatsNewError, match="period"):
            whats_new.parse_line("What's new: new, everyone: Can posts be pinned? Yes.")

    def test_an_open_path_without_a_slash_raises(self):
        with pytest.raises(WhatsNewError, match="open"):
            whats_new.parse_line(
                "What's new: new, everyone: Posts can be pinned. (open: documents)"
            )

    def test_the_kind_and_audience_must_be_in_order(self):
        with pytest.raises(WhatsNewError):
            whats_new.parse_line("What's new: everyone, new: Posts can be pinned.")

    def test_no_kind_and_audience_at_all_raises(self):
        with pytest.raises(WhatsNewError):
            whats_new.parse_line("What's new: Posts can be pinned.")

    def test_the_error_shows_the_two_accepted_forms(self):
        with pytest.raises(WhatsNewError) as caught:
            whats_new.parse_line("What's new: changed, everyone: Posts can be pinned.")
        message = str(caught.value)
        assert "What's new: none" in message
        assert "What's new: <kind>, <audience>: <sentence>" in message


class TestEntry:
    def test_as_item_omits_open_when_unset(self):
        entry = Entry(kind="new", audience="everyone", title="A.", body="", open=None)
        assert entry.as_item() == {
            "kind": "new", "audience": "everyone", "title": "A.", "body": "",
        }

    def test_as_item_carries_open_when_set(self):
        entry = Entry(kind="new", audience="everyone", title="A.", body="B.", open="/x")
        assert entry.as_item() == {
            "kind": "new", "audience": "everyone", "title": "A.", "body": "B.",
            "open": "/x",
        }

    def test_body_and_open_have_defaults(self):
        entry = Entry(kind="new", audience="everyone", title="A.")
        assert entry.body == "" and entry.open is None

    def test_a_parsed_entry_is_a_valid_item(self):
        entry = whats_new.parse_line(
            "What's new: improved, everyone: Searching a document now finds "
            "words inside tables. (open: /documents)"
        )
        document = _example()
        document["items"] = [entry.as_item()]
        assert whats_new.validate(document) == []


# --- required_for ------------------------------------------------------------


class TestRequiredFor:
    @pytest.mark.parametrize("head", [
        "dependabot/pip/pytest-9.1.2", "repair/main-red-123", "bot/sync-models",
    ])
    def test_machine_branches_owe_no_line(self, head):
        assert whats_new.required_for(head) is False

    @pytest.mark.parametrize("head", [
        "agent/DRE-5484-contract",
        "agent/DRE-5484-rescued-delivery",
        "main",
        "sid/fix-the-thing",
        "feature-table-search",
        "dependabot",  # no slash: not the prefix
    ])
    def test_every_other_branch_owes_a_line(self, head):
        assert whats_new.required_for(head) is True

    def test_it_takes_the_branch_alone(self):
        params = list(inspect.signature(whats_new.required_for).parameters)
        assert params == ["head_branch"]

    def test_it_reads_no_file(self, tmp_path, monkeypatch):
        broken = tmp_path / "whats-new-cutover.json"
        broken.write_text("not json")
        monkeypatch.setattr(whats_new, "CUTOVER_FILE", broken)

        def refuse(*_a, **_k):
            raise AssertionError("required_for must not read the cutover")

        monkeypatch.setattr(whats_new, "enforced_from", refuse)
        monkeypatch.setattr(Path, "read_text", refuse)
        monkeypatch.setattr("builtins.open", refuse)
        assert whats_new.required_for("agent/DRE-1-x") is True
        assert whats_new.required_for("bot/x") is False


# --- enforced_from -----------------------------------------------------------


def _cutover(tmp_path: Path, payload) -> Path:
    path = tmp_path / "whats-new-cutover.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


class TestEnforcedFrom:
    def test_an_absent_file_is_not_switched_on(self, tmp_path):
        assert whats_new.enforced_from(path=tmp_path / "absent.json") is None

    def test_a_z_instant_is_an_aware_utc_datetime(self, tmp_path):
        path = _cutover(tmp_path, {"enforced_from": "2026-10-03T00:00:00Z",
                                   "why": "Every product repo carries the line."})
        got = whats_new.enforced_from(path=path)
        assert got == datetime(2026, 10, 3, tzinfo=timezone.utc)
        assert got.utcoffset() == timedelta(0)

    def test_an_explicit_offset_is_the_same_instant(self, tmp_path):
        path = _cutover(tmp_path, {"enforced_from": "2026-10-02T17:00:00-07:00",
                                   "why": "x"})
        assert whats_new.enforced_from(path=path) == datetime(
            2026, 10, 3, tzinfo=timezone.utc)

    def test_a_string_path_works(self, tmp_path):
        path = _cutover(tmp_path, {"enforced_from": "2026-10-03T00:00:00+00:00",
                                   "why": "x"})
        assert whats_new.enforced_from(path=str(path)) is not None

    def test_the_default_reads_cutover_file_at_call_time(self, tmp_path, monkeypatch):
        path = _cutover(tmp_path, {"enforced_from": "2026-10-03T00:00:00Z", "why": "x"})
        monkeypatch.setattr(whats_new, "CUTOVER_FILE", path)
        assert whats_new.enforced_from() == datetime(2026, 10, 3, tzinfo=timezone.utc)
        monkeypatch.setattr(whats_new, "CUTOVER_FILE", tmp_path / "absent.json")
        assert whats_new.enforced_from() is None

    def test_an_extra_key_raises_naming_it(self, tmp_path):
        path = _cutover(tmp_path, {"enforced_from": "2026-10-03T00:00:00Z",
                                   "why": "x", "by": "sid"})
        with pytest.raises(WhatsNewError, match="by"):
            whats_new.enforced_from(path=path)

    def test_a_missing_why_raises_naming_it(self, tmp_path):
        path = _cutover(tmp_path, {"enforced_from": "2026-10-03T00:00:00Z"})
        with pytest.raises(WhatsNewError, match="why"):
            whats_new.enforced_from(path=path)

    def test_an_empty_why_raises(self, tmp_path):
        path = _cutover(tmp_path, {"enforced_from": "2026-10-03T00:00:00Z", "why": " "})
        with pytest.raises(WhatsNewError, match="why"):
            whats_new.enforced_from(path=path)

    def test_an_instant_with_no_offset_raises(self, tmp_path):
        path = _cutover(tmp_path, {"enforced_from": "2026-10-03T00:00:00", "why": "x"})
        with pytest.raises(WhatsNewError, match="offset"):
            whats_new.enforced_from(path=path)

    def test_an_unparseable_instant_raises(self, tmp_path):
        path = _cutover(tmp_path, {"enforced_from": "tomorrow", "why": "x"})
        with pytest.raises(WhatsNewError, match="enforced_from"):
            whats_new.enforced_from(path=path)

    def test_a_file_that_is_not_json_raises(self, tmp_path):
        path = _cutover(tmp_path, "{not json")
        with pytest.raises(WhatsNewError):
            whats_new.enforced_from(path=path)

    def test_a_file_that_is_not_an_object_raises(self, tmp_path):
        path = _cutover(tmp_path, ["2026-10-03T00:00:00Z"])
        with pytest.raises(WhatsNewError):
            whats_new.enforced_from(path=path)


# --- enforced_for ------------------------------------------------------------

CUTOVER = datetime(2026, 10, 3, tzinfo=timezone.utc)


class TestEnforcedFor:
    @pytest.mark.parametrize("created_at", [
        None, "2026-10-01T00:00:00Z", "2026-12-01T00:00:00Z", "yesterday",
    ])
    def test_off_when_not_switched_on_whatever_created_at_says(self, created_at):
        assert whats_new.enforced_for(created_at, cutover=None) is False

    def test_on_for_no_creation_time(self):
        assert whats_new.enforced_for(None, cutover=CUTOVER) is True

    def test_off_one_minute_before(self):
        assert whats_new.enforced_for("2026-10-02T23:59:00Z", cutover=CUTOVER) is False

    def test_on_at_the_cutover_itself(self):
        assert whats_new.enforced_for("2026-10-03T00:00:00Z", cutover=CUTOVER) is True

    def test_on_one_minute_after(self):
        assert whats_new.enforced_for("2026-10-03T00:01:00Z", cutover=CUTOVER) is True

    def test_an_offset_is_compared_as_an_instant(self):
        # 16:59 Pacific is 23:59 UTC: one minute before.
        assert whats_new.enforced_for(
            "2026-10-02T16:59:00-07:00", cutover=CUTOVER) is False

    def test_an_unreadable_time_raises(self):
        with pytest.raises(WhatsNewError, match="yesterday"):
            whats_new.enforced_for("yesterday", cutover=CUTOVER)

    def test_a_naive_time_raises(self):
        with pytest.raises(WhatsNewError):
            whats_new.enforced_for("2026-10-03T00:01:00", cutover=CUTOVER)

    def test_the_default_cutover_is_read_from_the_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(whats_new, "CUTOVER_FILE", tmp_path / "absent.json")
        assert whats_new.enforced_for(None) is False
        path = _cutover(tmp_path, {"enforced_from": "2026-10-03T00:00:00Z", "why": "x"})
        monkeypatch.setattr(whats_new, "CUTOVER_FILE", path)
        assert whats_new.enforced_for(None) is True
        assert whats_new.enforced_for("2026-10-02T23:59:00Z") is False

    def test_cutover_is_keyword_only(self):
        with pytest.raises(TypeError):
            whats_new.enforced_for(None, CUTOVER)  # type: ignore[misc]


# --- check_wording -----------------------------------------------------------


class TestCheckWording:
    def test_a_card_number(self):
        problems = whats_new.check_wording("Fixed the search from DRE-5484.")
        assert len(problems) == 1 and "DRE-5484" in problems[0]

    def test_a_pull_request_number(self):
        problems = whats_new.check_wording("Search is faster since #633.")
        assert len(problems) == 1 and "#633" in problems[0]

    def test_a_backtick(self):
        problems = whats_new.check_wording("The `search` box is faster.")
        assert len(problems) == 1 and "backtick" in problems[0]

    @pytest.mark.parametrize("word,title", [
        ("PR", "Search got faster in this PR."),
        ("pull request", "A Pull Request made search faster."),
        ("merge", "You can merge two documents."),
        ("merged", "Duplicate tags are merged."),
        ("commit", "Every commit is now faster."),
        ("refactor", "A refactor made search faster."),
        ("CI", "Search passes CI now."),
        ("workflow", "The approval Workflow is faster."),
        ("endpoint", "The search endpoint is faster."),
        ("schema", "The schema holds tags."),
        ("migration", "A migration moved your posts."),
        ("card", "This card adds search."),
        ("branch", "Search works on every branch."),
    ])
    def test_one_title_per_internal_word(self, word, title):
        problems = whats_new.check_wording(title)
        assert len(problems) == 1, problems
        assert word.lower() in problems[0].lower()

    def test_pr_and_ci_are_case_sensitive(self):
        assert whats_new.check_wording("Prices in pr and ci are shown.") == []

    def test_whole_words_only(self):
        assert whats_new.check_wording(
            "Scorecards, commitments and merger notes load faster."
        ) == []

    def test_several_problems_are_several_sentences(self):
        problems = whats_new.check_wording("DRE-1 merged in #2 with `x`.")
        assert len(problems) == 4

    def test_the_example_titles_are_clean(self):
        for item in _example()["items"]:
            assert whats_new.check_wording(item["title"]) == []


# --- validate ----------------------------------------------------------------


def _without(mapping: dict, key: str) -> dict:
    mapping = dict(mapping)
    mapping.pop(key)
    return mapping


class TestValidate:
    def test_the_example_is_valid(self):
        assert whats_new.validate(_example()) == []

    def test_the_example_is_the_documented_shape(self):
        document = _example()
        assert document["product"] == "portico"
        assert document["release"] == "portals-v1.2.3"
        assert len(document["items"]) == 2
        assert document["items"][0]["open"] == "/documents"
        assert "open" not in document["items"][1]

    def _one(self, document) -> str:
        problems = whats_new.validate(document)
        assert len(problems) == 1, problems
        return problems[0]

    def test_a_missing_product(self):
        assert "product" in self._one(_without(_example(), "product"))

    def test_a_shipped_with_no_offset(self):
        document = _example()
        document["shipped"] = "2026-10-01T14:05:00"
        assert "shipped" in self._one(document)

    def test_an_empty_items_list(self):
        document = _example()
        document["items"] = []
        assert "items" in self._one(document)

    def test_an_item_with_an_unknown_kind(self):
        document = _example()
        document["items"][0]["kind"] = "changed"
        assert "items[0].kind" in self._one(document)

    def test_an_item_without_audience(self):
        document = _example()
        document["items"][1] = _without(document["items"][1], "audience")
        assert "items[1].audience" in self._one(document)

    def test_a_title_with_a_card_number(self):
        document = _example()
        document["items"][0]["title"] = "Searching finds words inside tables (DRE-5484)."
        problem = self._one(document)
        assert "items[0].title" in problem and "DRE-5484" in problem

    def test_a_title_without_a_period(self):
        document = _example()
        document["items"][0]["title"] = "Searching finds words inside tables"
        assert "items[0].title" in self._one(document)

    def test_an_unknown_document_key(self):
        document = _example()
        document["version"] = "1"
        assert "version" in self._one(document)

    def test_an_unknown_item_key(self):
        document = _example()
        document["items"][1]["link"] = "/queue"
        assert "items[1].link" in self._one(document)

    @pytest.mark.parametrize("mutate,field", [
        (lambda d: d.update(product=""), "product"),
        (lambda d: d.update(release=""), "release"),
        (lambda d: d.update(release=3), "release"),
        (lambda d: d.update(shipped="last tuesday"), "shipped"),
        (lambda d: d.update(items="none"), "items"),
        (lambda d: d["items"].__setitem__(0, "a string"), "items[0]"),
        (lambda d: d["items"][0].update(audience="staff"), "items[0].audience"),
        (lambda d: d["items"][0].update(title=""), "items[0].title"),
        (lambda d: d["items"][0].update(body=None), "items[0].body"),
        (lambda d: d["items"][0].pop("body"), "items[0].body"),
        (lambda d: d["items"][0].update(open="documents"), "items[0].open"),
        (lambda d: d["items"][0].update(open=7), "items[0].open"),
    ])
    def test_each_field_rule(self, mutate, field):
        document = _example()
        mutate(document)
        assert field in self._one(document)

    def test_shipped_accepts_z(self):
        document = _example()
        document["shipped"] = "2026-10-01T21:05:00Z"
        assert whats_new.validate(document) == []

    def test_a_document_that_is_not_an_object(self):
        problems = whats_new.validate([_example()])
        assert len(problems) == 1

    def test_validate_does_not_mutate(self):
        document = _example()
        before = copy.deepcopy(document)
        whats_new.validate(document)
        assert document == before


# --- the command line --------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=ROOT,
    )


class TestCommandLine:
    def test_validate_the_example_exits_zero(self):
        done = _run("validate", "docs/whats-new.example.json")
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == (
            "whats-new: docs/whats-new.example.json is valid (2 items)"
        )

    def test_validate_a_copy_without_an_audience_exits_one(self, tmp_path):
        document = _example()
        del document["items"][0]["audience"]
        path = tmp_path / "whats-new.json"
        path.write_text(json.dumps(document))
        done = _run("validate", str(path))
        assert done.returncode == 1
        output = done.stdout + done.stderr
        assert "items[0].audience" in output
        assert "is valid" not in output

    def test_validate_a_file_that_is_not_json_exits_one(self, tmp_path):
        path = tmp_path / "whats-new.json"
        path.write_text("{")
        assert _run("validate", str(path)).returncode == 1

    def test_line_prints_the_entry_as_json(self, tmp_path):
        path = tmp_path / "body.md"
        path.write_text(
            "What's new: improved, everyone: Searching finds words inside "
            "tables. (open: /documents)\n\nCloses DRE-1.\n"
        )
        done = _run("line", str(path))
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout) == {
            "kind": "improved", "audience": "everyone",
            "title": "Searching finds words inside tables.", "body": "",
            "open": "/documents",
        }

    def test_line_prints_none(self, tmp_path):
        path = tmp_path / "body.md"
        path.write_text("**What's new:** none\n")
        done = _run("line", str(path))
        assert done.returncode == 0
        assert done.stdout.strip() == "none"

    def test_line_prints_the_error_and_exits_one(self, tmp_path):
        path = tmp_path / "body.md"
        path.write_text("Adds search.\n")
        done = _run("line", str(path))
        assert done.returncode == 1
        assert "What's new: none" in done.stdout + done.stderr

    def test_no_command_is_a_usage_error(self):
        assert _run().returncode not in (0, 1)


# --- the standard ------------------------------------------------------------


class TestTheStandard:
    @pytest.fixture(scope="class")
    def text(self) -> str:
        return STANDARD.read_text()

    def test_it_names_both_forms(self, text):
        assert "What's new: none" in text
        assert "What's new: <kind>, <audience>: <sentence>" in text
        assert "(open: " in text

    def test_it_names_every_kind_audience_and_exempt_prefix(self, text):
        for name in (*whats_new.KINDS, *whats_new.AUDIENCES, *whats_new.EXEMPT_PREFIXES):
            assert f"`{name}`" in text, name

    def test_it_names_the_adoption_and_rescue_branches(self, text):
        for name in (
            "agent/<card>-adopt-<candidate>",
            "agent/model-adoption-<candidate>",
            "agent/claude-code-pin-<version>",
            "agent/<card>-rescued-delivery",
            "scripts/push_rescue.py",
            "scripts/deliver_rescue.py",
        ):
            assert name in text, name

    def test_it_says_how_a_sent_back_pull_request_is_answered(self, text):
        assert "gh pr edit" in text
        assert "--body-file" in text
        assert "empty commit" in text

    def test_it_names_the_switch_and_the_publishing(self, text):
        assert "config/whats-new-cutover.json" in text
        assert "GitHub Release" in text
        assert "whats-new.json" in text
        assert "python3 .bureau-pipeline/scripts/whats_new.py validate whats-new.json" in text

    def test_its_file_shape_is_the_example_and_is_valid(self, text):
        blocks = re.findall(r"```json\n(.*?)```", text, re.S)
        assert blocks, "the standard shows the file shape in a ```json block"
        assert json.loads(blocks[0]) == _example()

    def test_its_sample_lines_parse(self, text):
        samples = [
            line for line in re.findall(r"^What's new: .*$", text, re.M)
            if "<" not in line
        ]
        assert samples, "the standard shows at least one concrete line"
        for line in samples:
            whats_new.parse_line(line)

    def test_it_is_short(self, text):
        assert len(text.splitlines()) <= 110
