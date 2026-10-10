"""RED-first tests: a blocker reason has a class (DRE-6438).

A build agent's `/tmp/agent-blocker.txt` is posted as a `🛑 Agent blocked:`
comment, and until now the sweep held the card for a person whatever the reason
said. `config/blocker-classes.json` declares the four classes a reason can
belong to, and `scripts/blocker_class.py` is the one reader that names a
blocker's class, so the poster (DRE-6444) and the sweep (DRE-6448) act on the
class instead of the prose. This card writes nothing to Linear and changes no
behavior: it is the contract the siblings read.

WHAT THESE TESTS PIN.

  * `check` is green on the shipped file and names the class or the key for
    each of the five defects in the contract.
  * The FOUR real marker comments on the board, copied verbatim into
    `tests/fixtures/blocker-reasons.json`, classify as the board says they
    should, and `marker_reason(body)` reproduces each entry's `reason`.
  * The precedence: a valid first-line stamp wins, then the phrases, and two
    classes' phrases in one text is `question`.
  * `open_blocker` walks the thread the way `reconcile.has_unresolved_blocker`
    does, with the resolved tag and the machine prefixes handed in.
  * The CLI the poster calls (`classify`, `reason`) runs as a subprocess, and
    the two Linear readers (`classify-card`, `board`) run against stubs that
    record what was asked and that nothing was written.

Run: cd bureau-pipeline && python3 -m pytest tests/test_blocker_class.py -v
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("LINEAR_API_KEY", "test-key")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import blocker_class  # noqa: E402

CONFIG = ROOT / "config" / "blocker-classes.json"
FIXTURE = ROOT / "tests" / "fixtures" / "blocker-reasons.json"
SCRIPT = ROOT / "scripts" / "blocker_class.py"

CLASSES = ("nothing-to-change", "wrong-repo", "branch-without-pr", "question")
ACTIONS = {
    "nothing-to-change": "blocker_nothing_to_change",
    "wrong-repo": "blocker_wrong_repo",
    "branch-without-pr": "blocker_branch_pr",
    "question": "blocker_ask",
}
#: The real cards, in the fixture's order, and the class each must read as.
REAL = (("DRE-5195", 37873928192, "nothing-to-change"),
        ("DRE-3242", 34310166200, "wrong-repo"),
        ("DRE-3242", 37542061691, "wrong-repo"),
        ("DRE-6056", 37800258309, "branch-without-pr"))

#: Not the pipeline's tag — this module holds none; the caller hands one in.
TAG = "test-resolved-tag"
PREFIXES = ("🤖", "🛑", "🧹", "🪦", "🚨", "🏁")
TRAILER = (" — parked in Backlog until the blocker is resolved (a Todo return "
           "here would redispatch agents into the same wall). Run: "
           "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/42")


def _marker(reason: str, cls: str | None = None) -> str:
    head = f"class={cls} · " if cls else ""
    return f"🛑 Agent blocked: {head}{reason}{TRAILER}"


def _fixture() -> list:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# the vocabulary file                                                          #
# --------------------------------------------------------------------------- #


class TestVocabulary:
    def test_the_file_carries_the_contract_keys_and_exactly_four_classes(self):
        doc = json.loads(CONFIG.read_text(encoding="utf-8"))
        assert doc["version"] == 1
        assert doc["stamp"] == "blocker-class: "
        assert doc["marker"] == "🛑 Agent blocked:"
        assert doc["default"] == "question"
        assert tuple(doc["classes"]) == CLASSES
        for name, row in doc["classes"].items():
            assert row["action"] == ACTIONS[name], name
            assert row["means"].strip(), name
            assert all(p == p.lower() for p in row["phrases"]), name

    def test_load_reads_the_shipped_file(self):
        assert blocker_class.load() == json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_the_marker_stem_is_the_sweeps(self):
        import reconcile
        doc = blocker_class.load()
        assert doc["marker"].rstrip(":") == reconcile.BLOCKER_MARKER

    def test_check_is_green_on_the_shipped_file(self):
        done = subprocess.run([sys.executable, str(SCRIPT), "check"],
                              capture_output=True, text=True, cwd=ROOT)
        assert done.returncode == 0, done.stdout + done.stderr

    @pytest.mark.parametrize("defect, mutate, named", [
        ("a class has no means",
         lambda d: d["classes"]["wrong-repo"].update(means=""), "wrong-repo"),
        ("a non-default class has no phrase",
         lambda d: d["classes"]["branch-without-pr"].update(phrases=[]),
         "branch-without-pr"),
        ("a phrase appears under two classes",
         lambda d: d["classes"]["wrong-repo"]["phrases"].append(
             d["classes"]["nothing-to-change"]["phrases"][0]),
         "nothing-to-change"),
        ("default is not a class",
         lambda d: d.update(default="maybe"), "default"),
        ("an action is missing",
         lambda d: d["classes"]["question"].pop("action"), "question"),
        ("an action is not a module name",
         lambda d: d["classes"]["wrong-repo"].update(action="blocker-wrong-repo.py"),
         "wrong-repo"),
    ])
    def test_check_exits_1_naming_the_defect(self, tmp_path, capsys, defect, mutate, named):
        doc = json.loads(CONFIG.read_text(encoding="utf-8"))
        mutate(doc)
        path = tmp_path / "blocker-classes.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        assert blocker_class.main(["check", "--config", str(path)]) == 1, defect
        out = capsys.readouterr().out
        assert named in out, (defect, out)
        assert "[FAIL]" in out

    def test_the_phrase_under_two_classes_is_quoted(self, tmp_path):
        doc = json.loads(CONFIG.read_text(encoding="utf-8"))
        phrase = doc["classes"]["nothing-to-change"]["phrases"][0]
        doc["classes"]["wrong-repo"]["phrases"].append(phrase)
        found = blocker_class.problems(doc)
        assert any(phrase in p and "wrong-repo" in p for p in found), found


# --------------------------------------------------------------------------- #
# the real reasons                                                             #
# --------------------------------------------------------------------------- #


class TestRealReasons:
    def test_every_expected_class_is_a_class(self):
        unknown = [e["expected"] for e in _fixture() if e["expected"] not in CLASSES]
        assert not unknown, f"fixture names no class: {', '.join(map(repr, unknown))}"

    def test_a_fixture_naming_an_unknown_class_fails_with_the_word_quoted(self):
        bad = [{"card": None, "run": None, "body": "", "reason": "x",
                "expected": "out-of-scope"}]
        with pytest.raises(AssertionError, match="'out-of-scope'"):
            _assert_known(bad)

    def test_the_four_real_entries_are_the_four_markers_on_the_board(self):
        real = [e for e in _fixture() if e["card"] is not None]
        assert [(e["card"], e["run"], e["expected"]) for e in real] == list(REAL)
        for entry in real:
            assert entry["body"].startswith("🛑 Agent blocked:"), entry["card"]
            assert entry["body"].endswith(f"/actions/runs/{entry['run']}"), entry["card"]

    def test_the_wrong_repo_phrases_are_each_in_one_note(self):
        first, second = [e["reason"] for e in _fixture() if e["card"] == "DRE-3242"]
        assert "pointed at the wrong repo" in first
        assert "sent to the wrong repository" not in first
        assert "sent to the wrong repository" in second
        assert "pointed at the wrong repo" not in second

    def test_there_is_a_synthetic_entry_naming_no_class(self):
        synthetic = [e for e in _fixture() if e["card"] is None]
        assert synthetic
        assert all(e["expected"] == "question" for e in synthetic)

    @pytest.mark.parametrize("entry", _fixture(),
                             ids=lambda e: f"{e['card']}-{e['run']}")
    def test_classify_reads_the_expected_class(self, entry):
        assert blocker_class.classify(entry["reason"]) == entry["expected"]

    @pytest.mark.parametrize("entry", [e for e in _fixture() if e["card"]],
                             ids=lambda e: f"{e['card']}-{e['run']}")
    def test_marker_reason_reproduces_the_reason(self, entry):
        assert blocker_class.marker_reason(entry["body"]) == entry["reason"]

    @pytest.mark.parametrize("entry", _fixture(),
                             ids=lambda e: f"{e['card']}-{e['run']}")
    def test_a_legacy_marker_carries_no_class(self, entry):
        assert blocker_class.marker_class(entry["body"]) is None


def _assert_known(entries) -> None:
    unknown = [e["expected"] for e in entries if e["expected"] not in CLASSES]
    assert not unknown, f"fixture names no class: {', '.join(map(repr, unknown))}"


# --------------------------------------------------------------------------- #
# precedence                                                                   #
# --------------------------------------------------------------------------- #


class TestClassify:
    def test_two_classes_phrases_are_a_question(self):
        text = ("There is nothing for this card to change, and it was "
                "pointed at the wrong repo besides.")
        assert blocker_class.classify(text) == "question"

    def test_a_stamp_outranks_a_contradicting_phrase(self):
        text = "blocker-class: wrong-repo\nrepo: agent-bureau\nThere is nothing for this card to change."
        assert blocker_class.classify(text) == "wrong-repo"

    def test_a_stamp_naming_an_unknown_class_is_no_stamp(self):
        text = "blocker-class: out-of-scope\nThe card wants a decision."
        assert blocker_class.stamp_class(text) is None
        assert blocker_class.classify(text) == "question"

    def test_an_unknown_stamp_falls_through_to_the_phrases(self):
        text = "blocker-class: nope\nThis card was sent to the wrong repository."
        assert blocker_class.classify(text) == "wrong-repo"

    def test_a_stamp_counts_only_on_the_first_line(self):
        text = "Something first.\nblocker-class: wrong-repo"
        assert blocker_class.stamp_class(text) is None
        assert blocker_class.classify(text) == "question"

    def test_phrases_match_case_insensitively(self):
        assert blocker_class.classify("THERE IS NOTHING FOR THIS CARD TO CHANGE") == "nothing-to-change"

    @pytest.mark.parametrize("text", ["", None, "   \n", "\x00"])
    def test_classify_never_raises(self, text):
        assert blocker_class.classify(text) == "question"

    def test_each_class_stamps(self):
        for name in CLASSES:
            assert blocker_class.stamp_class(f"blocker-class: {name}\nwhy") == name

    def test_strip_stamp_removes_only_a_valid_stamp_line(self):
        note = "blocker-class: wrong-repo\nrepo: bureau-pipeline\nThe files live there.\n"
        assert blocker_class.strip_stamp(note) == "repo: bureau-pipeline\nThe files live there.\n"
        bogus = "blocker-class: maybe\nrepo: x\n"
        assert blocker_class.strip_stamp(bogus) == bogus
        plain = "No stamp here.\nrepo: x"
        assert blocker_class.strip_stamp(plain) == plain


# --------------------------------------------------------------------------- #
# the marker and the thread                                                    #
# --------------------------------------------------------------------------- #


class TestMarker:
    def test_marker_class_reads_the_class(self):
        assert blocker_class.marker_class(_marker("why", "wrong-repo")) == "wrong-repo"

    def test_marker_reason_of_a_classed_marker_drops_the_class(self):
        assert blocker_class.marker_reason(_marker("why", "question")) == "why"

    def test_marker_reason_keeps_a_three_line_reason_whole(self):
        reason = "repo: bureau-pipeline\nThe file lives there.\nNo PR was opened."
        body = _marker(reason, "wrong-repo")
        assert blocker_class.marker_reason(body) == reason

    def test_marker_reason_cuts_at_the_last_parked_clause(self):
        reason = "It says — parked in Backlog once before, and again."
        assert blocker_class.marker_reason(_marker(reason)) == reason

    def test_a_marker_class_is_read_without_the_phrases(self):
        body = _marker("There is nothing for this card to change.", "wrong-repo")
        found = blocker_class.open_blocker([body], machine_prefixes=PREFIXES,
                                           resolved_tag=TAG)
        assert found.cls == "wrong-repo"


class TestOpenBlocker:
    MARKER = _marker("This card was sent to the wrong repository.")

    def _open(self, bodies):
        return blocker_class.open_blocker(bodies, machine_prefixes=PREFIXES,
                                          resolved_tag=TAG)

    def test_a_lone_marker_is_open_with_its_class(self):
        found = self._open([self.MARKER])
        assert found == blocker_class.Blocker(
            "wrong-repo", "This card was sent to the wrong repository.", self.MARKER)

    def test_the_resolved_tag_closes_it(self):
        assert self._open([self.MARKER, f"🧹 resolved · {TAG} · moved to Todo"]) is None

    def test_a_persons_reply_closes_it(self):
        assert self._open([self.MARKER, "a person's reply"]) is None

    def test_a_machine_comment_without_the_tag_leaves_it_open(self):
        found = self._open([self.MARKER, "🧹 Auto-promoted Backlog → Todo"])
        assert found is not None and found.body == self.MARKER

    def test_the_newest_marker_is_the_one_returned(self):
        newer = _marker("why", "question")
        assert self._open([self.MARKER, newer]).body == newer

    def test_no_marker_is_no_blocker(self):
        assert self._open(["🧹 Auto-promoted", "⏳ 1/5 plan"]) is None

    def test_the_module_holds_no_tag(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert not re.search(r'(?m)^[A-Z][A-Z0-9_]*_TAG\s*=', source)
        assert ("agent-blocker" + "-resolved") not in source
        assert "_AGENT_COMMENT_PREFIXES" not in source

    def test_not_now_is_an_exception(self):
        assert issubclass(blocker_class.NotNow, Exception)


# --------------------------------------------------------------------------- #
# the module stays stdlib-only at import                                       #
# --------------------------------------------------------------------------- #


def test_the_module_imports_only_the_stdlib_at_top():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            top.add((node.module or "").split(".")[0])
    assert top <= set(sys.stdlib_module_names) | {"__future__"}, top


# --------------------------------------------------------------------------- #
# the CLI the poster calls                                                     #
# --------------------------------------------------------------------------- #


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=ROOT)


class TestPosterCli:
    def test_classify_prints_the_bare_class(self, tmp_path):
        note = tmp_path / "agent-blocker.txt"
        note.write_text("blocker-class: branch-without-pr\nThe work is pushed.\n",
                        encoding="utf-8")
        done = _run("classify", str(note))
        assert done.returncode == 0
        assert done.stdout == "branch-without-pr\n"

    def test_classify_reads_the_phrases_of_an_unstamped_note(self, tmp_path):
        note = tmp_path / "agent-blocker.txt"
        note.write_text("There is nothing for this card to change.", encoding="utf-8")
        assert _run("classify", str(note)).stdout == "nothing-to-change\n"

    def test_classify_of_a_missing_file_is_a_question(self, tmp_path):
        done = _run("classify", str(tmp_path / "absent.txt"))
        assert (done.returncode, done.stdout) == (0, "question\n")

    def test_classify_of_an_empty_file_is_a_question(self, tmp_path):
        note = tmp_path / "agent-blocker.txt"
        note.write_text("", encoding="utf-8")
        done = _run("classify", str(note))
        assert (done.returncode, done.stdout) == (0, "question\n")

    def test_reason_drops_the_stamp_and_keeps_the_repo_line(self, tmp_path):
        note = tmp_path / "agent-blocker.txt"
        note.write_text("blocker-class: wrong-repo\nrepo: bureau-pipeline\n"
                        "The files live there.\n", encoding="utf-8")
        done = _run("reason", str(note))
        assert done.returncode == 0
        assert done.stdout == "repo: bureau-pipeline\nThe files live there.\n"

    def test_reason_of_an_unstamped_note_is_the_note(self, tmp_path):
        note = tmp_path / "agent-blocker.txt"
        note.write_text("Just prose.\n", encoding="utf-8")
        assert _run("reason", str(note)).stdout == "Just prose.\n"


# --------------------------------------------------------------------------- #
# the two Linear readers                                                       #
# --------------------------------------------------------------------------- #


class _Writes:
    """Every Linear call the readers make, so a test can count mutations."""

    def __init__(self):
        self.calls = []

    def gql(self, query, variables=None, *a, **k):
        self.calls.append(query)
        raise AssertionError("no Linear request was stubbed for this test")

    @property
    def mutations(self):
        return [q for q in self.calls if "mutation" in q]


@pytest.fixture
def writes(monkeypatch):
    import linear_ops
    recorder = _Writes()
    monkeypatch.setattr(linear_ops, "gql", recorder.gql)
    for name in ("cmd_comment", "cmd_advance", "cmd_label", "cmd_unlabel"):
        if hasattr(linear_ops, name):
            monkeypatch.setattr(linear_ops, name,
                                lambda *a, **k: recorder.calls.append("mutation"))
    return recorder


class TestClassifyCard:
    def test_it_reads_the_whole_thread_and_names_an_old_marker(self, monkeypatch, capsys, writes):
        import linear_ops
        asked = []
        marker = _marker("I opened no pull request.", "branch-without-pr")
        thread = [marker] + [f"🧹 sweep note {n}" for n in range(60)]

        def bodies(identifier, *, whole_thread=False):
            asked.append((identifier, whole_thread))
            return thread if whole_thread else thread[-50:]

        monkeypatch.setattr(linear_ops, "comment_bodies", bodies)
        assert blocker_class.main(["classify-card", "DRE-1"]) == 0
        assert asked == [("DRE-1", True)]
        assert capsys.readouterr().out == "DRE-1 class=branch-without-pr\n"
        assert writes.mutations == []

    def test_a_card_with_no_marker_says_so(self, monkeypatch, capsys, writes):
        import linear_ops
        monkeypatch.setattr(linear_ops, "comment_bodies",
                            lambda identifier, *, whole_thread=False: ["hello"])
        assert blocker_class.main(["classify-card", "DRE-2"]) == 0
        assert capsys.readouterr().out == "DRE-2 no blocker\n"


def _card(identifier, repo, bodies):
    """A Backlog card in the sweep's read shape: `bodies` oldest→newest, the
    window newest-first as the API returns it."""
    return {"identifier": identifier,
            "labels": {"nodes": [{"name": f"repo:{repo}"}]},
            "comments": {"nodes": [{"body": b} for b in reversed(bodies)]}}


class TestBoard:
    def _board(self, monkeypatch, cards, open_ids):
        import reconcile
        asked = []

        def backlog(only=None, *, from_linear=False, stamped=False):
            asked.append(from_linear)
            return cards

        monkeypatch.setenv("REPO", "dreadnought-foundry/bureau-pipeline")
        monkeypatch.setenv("REPO_SLUG", "bureau-pipeline")
        monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
        monkeypatch.setattr(reconcile, "backlog_children", backlog)
        monkeypatch.setattr(reconcile, "has_unresolved_blocker",
                            lambda card: card["identifier"] in open_ids)
        return asked

    def test_it_prints_one_line_per_open_card_and_writes_nothing(self, monkeypatch, capsys, writes):
        long_reason = "This card was sent to the wrong repository. " * 4
        cards = [
            _card("DRE-10", "bureau-pipeline", [_marker(long_reason)]),
            _card("DRE-11", "bureau-pipeline",
                  [_marker("There is nothing for this card to change.", "question")]),
            _card("DRE-12", "bureau-pipeline", [_marker("I opened no pull request.")]),
            _card("DRE-13", "agent-bureau", [_marker("I opened no pull request.")]),
        ]
        asked = self._board(monkeypatch, cards, {"DRE-10", "DRE-11", "DRE-13"})
        assert blocker_class.main(["board"]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines == [
            f"DRE-10 class=wrong-repo · {long_reason.strip()[:80]}",
            "DRE-11 class=question · There is nothing for this card to change.",
        ]
        assert asked == [True]
        assert writes.mutations == []

    def test_an_empty_board_exits_0(self, monkeypatch, capsys, writes):
        self._board(monkeypatch, [], set())
        assert blocker_class.main(["board"]) == 0
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize("unset", ["REPO_SLUG", "REPO"])
    def test_it_needs_repo_and_repo_slug(self, unset):
        env = {k: v for k, v in os.environ.items() if k != unset}
        done = subprocess.run([sys.executable, str(SCRIPT), "board"],
                              capture_output=True, text=True, cwd=ROOT, env=env)
        assert done.returncode == 2
        assert done.stdout == ""
        assert "board needs REPO and REPO_SLUG" in done.stderr

    def test_reconcile_is_imported_inside_the_subcommand(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        top = {alias.name for node in tree.body if isinstance(node, ast.Import)
               for alias in node.names}
        assert "reconcile" not in top and "linear_ops" not in top


# --------------------------------------------------------------------------- #
# the page a person reads                                                      #
# --------------------------------------------------------------------------- #


class TestDocs:
    DOC = ROOT / "docs" / "blocker-classes.md"

    def test_the_page_names_every_class_and_its_action(self):
        text = self.DOC.read_text(encoding="utf-8")
        for name, action in ACTIONS.items():
            assert f"`{name}`" in text, name
            assert f"`{action}`" in text, action

    def test_the_page_carries_the_grammars_and_the_siblings(self):
        text = self.DOC.read_text(encoding="utf-8")
        assert "blocker-class: <class>" in text
        assert "repo: <slug>" in text
        assert "🛑 Agent blocked: class=<class> · <reason>" in text
        for card in ("DRE-6444", "DRE-6446", "DRE-6447", "DRE-6448", "DRE-6508"):
            assert card in text, card
        assert "draft" in text and "parity" in text

    def test_the_page_lists_the_transient_lines(self):
        text = self.DOC.read_text(encoding="utf-8").lower()
        for phrase in ("not on the checkout", "notnow", "could not be read live",
                       "left backlog", "not the full sweep"):
            assert phrase in text, phrase

    def test_the_config_readme_carries_the_bullet(self):
        text = (ROOT / "config" / "README.md").read_text(encoding="utf-8")
        assert "- **`blocker-classes.json`**" in text
