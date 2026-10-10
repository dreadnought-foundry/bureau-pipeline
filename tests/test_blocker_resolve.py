"""RED-first tests: the blocker resolver (DRE-6508).

The sweep reads an open `🛑 Agent blocked:` marker's class through
`blocker_class.open_blocker` (DRE-6438) and, once DRE-6448 lands, hands it to
`scripts/blocker_resolve.py`. The resolver reads the class's `action` off
`config/blocker-classes.json`, calls that module's `resolve`, and posts the
one receipt that resolves the marker for every later sweep. It carries no
action of its own: every action module here is a stub, so this file is green
before any of the four sibling modules is on the checkout.

WHAT THESE TESTS PIN, with `linear_ops.cmd_comment` stubbed:

  * A pair from the class's module → that pair returned, and exactly one
    receipt, posted AFTER the module's own write.
  * `question` → the ask, called with the marker's reason as it stands.
  * `None` from a mechanical module → the ask, called with the reason and one
    `(class=<class>: …)` line, and the receipt names the marker's class.
  * `NotNow` → the `not resolved this pass` line, `None`, nothing written.
  * A module absent from the checkout → the `is not on this checkout` line,
    `None`, nothing written; a broken import of ANOTHER module propagates.
  * Every other exception propagates unchanged, with no receipt.
  * The ask is imported statically and called in `resolve_blocker`'s own body,
    which is where `scripts/lane_callers.py` names the caller, and the module
    holds exactly one `cmd_comment` call.

Run: cd bureau-pipeline && python3 -m pytest tests/test_blocker_resolve.py -v
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("LINEAR_API_KEY", "test-key")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import blocker_class  # noqa: E402
import blocker_resolve  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402

SCRIPT = ROOT / "scripts" / "blocker_resolve.py"
REPO = "dreadnought-foundry/bureau-pipeline"
CARD = {"identifier": "DRE-1", "title": "a card", "labels": {"nodes": []},
        "description": "", "comments": {"nodes": []}}
REASON = "Every criterion already holds on main."
ACT = "agent-blocker-resolved"
TAIL = "the sweep could not act on this mechanically"


def _blocker(cls: str, reason: str = REASON) -> blocker_class.Blocker:
    return blocker_class.Blocker(cls, reason, f"🛑 Agent blocked: class={cls} · {reason}")


class Recorder:
    """Every write in the order it happened: the stub modules' own writes,
    the ask's calls, and the resolver's comments."""

    def __init__(self):
        self.log: list = []

    def comments(self) -> list:
        return [entry[2] for entry in self.log if entry[0] == "comment"]

    def module(self, result=None, raises=None):
        def resolve(card, reason, *, repo):
            if raises is not None:
                raise raises
            self.log.append(("write", card["identifier"], reason, repo))
            return result
        return SimpleNamespace(resolve=resolve)

    def ask(self, result=("asked", "asked in Green Light")):
        def resolve(card, reason, *, repo):
            self.log.append(("ask", card["identifier"], reason, repo))
            return result
        return SimpleNamespace(resolve=resolve)


@pytest.fixture
def rec(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(
        linear_ops, "cmd_comment",
        lambda identifier, body, *flags: recorder.log.append(("comment", identifier, body)))
    monkeypatch.setattr(blocker_resolve, "blocker_ask", recorder.ask())
    return recorder


def _stub(monkeypatch, name, module):
    monkeypatch.setitem(sys.modules, name, module)


# --------------------------------------------------------------------------- #
# a module that acted                                                          #
# --------------------------------------------------------------------------- #


def test_a_pair_is_returned_and_one_receipt_posted_after_the_write(rec, monkeypatch):
    _stub(monkeypatch, "blocker_nothing_to_change",
          rec.module(("canceled", "every criterion attested")))
    got = blocker_resolve.resolve_blocker(CARD, _blocker("nothing-to-change"), repo=REPO)
    assert got == ("canceled", "every criterion attested")
    assert [entry[0] for entry in rec.log] == ["write", "comment"]
    assert rec.log[0] == ("write", "DRE-1", REASON, REPO)
    (body,) = rec.comments()
    assert body.startswith("🧹 agent-blocker-resolved: class=nothing-to-change action=canceled —")
    assert rec.log[-1][1] == "DRE-1"


def test_the_receipt_is_composed_by_the_one_writer(rec, monkeypatch):
    _stub(monkeypatch, "blocker_nothing_to_change",
          rec.module(("canceled", "every criterion attested")))
    blocker_resolve.resolve_blocker(CARD, _blocker("nothing-to-change"), repo=REPO)
    (body,) = rec.comments()
    assert body == pipeline_act.receipt(
        ACT, "🧹 agent-blocker-resolved: class=nothing-to-change action=canceled"
             " — every criterion attested")
    fields = pipeline_act.read_trailer(body)
    assert fields["act"] == ACT and fields["tag"] == ACT


def test_the_class_module_is_the_one_the_config_names(rec, monkeypatch):
    for cls, spec in blocker_class.load()["classes"].items():
        if cls == "question":
            continue
        _stub(monkeypatch, spec["action"], rec.module(("relabeled", cls)))
        assert blocker_resolve.resolve_blocker(CARD, _blocker(cls), repo=REPO) == ("relabeled", cls)
    assert len(rec.comments()) == 3


# --------------------------------------------------------------------------- #
# the ask                                                                      #
# --------------------------------------------------------------------------- #


def test_a_question_goes_to_the_ask_with_the_reason_as_it_stands(rec):
    got = blocker_resolve.resolve_blocker(CARD, _blocker("question"), repo=REPO)
    assert got == ("asked", "asked in Green Light")
    assert rec.log[0] == ("ask", "DRE-1", REASON, REPO)
    (body,) = rec.comments()
    assert body.startswith("🧹 agent-blocker-resolved: class=question action=asked —")


def test_a_person_s_call_goes_to_the_ask_with_the_class_line(rec, monkeypatch):
    _stub(monkeypatch, "blocker_wrong_repo", rec.module(None))
    got = blocker_resolve.resolve_blocker(CARD, _blocker("wrong-repo"), repo=REPO)
    assert got == ("asked", "asked in Green Light")
    assert [entry[0] for entry in rec.log] == ["write", "ask", "comment"]
    assert rec.log[1][2] == f"{REASON}\n(class=wrong-repo: {TAIL})"
    (body,) = rec.comments()
    assert body.startswith("🧹 agent-blocker-resolved: class=wrong-repo action=asked —")


# --------------------------------------------------------------------------- #
# not this pass                                                                #
# --------------------------------------------------------------------------- #


def test_not_now_prints_the_line_and_writes_nothing(rec, monkeypatch, capsys):
    _stub(monkeypatch, "blocker_nothing_to_change",
          rec.module(raises=blocker_class.NotNow("x")))
    assert blocker_resolve.resolve_blocker(CARD, _blocker("nothing-to-change"), repo=REPO) is None
    assert rec.log == []
    assert capsys.readouterr().out.splitlines() == [
        "promotion: DRE-1 agent-blocker class=nothing-to-change — not resolved this pass: x"]


def test_not_now_from_the_ask_writes_nothing(rec, monkeypatch, capsys):
    def resolve(card, reason, *, repo):
        raise blocker_class.NotNow("Green Light would not answer")
    monkeypatch.setattr(blocker_resolve, "blocker_ask", SimpleNamespace(resolve=resolve))
    assert blocker_resolve.resolve_blocker(CARD, _blocker("question"), repo=REPO) is None
    assert rec.log == []
    assert "class=question — not resolved this pass: Green Light would not answer" \
        in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# a module that is not on the checkout                                         #
# --------------------------------------------------------------------------- #


def _absent(monkeypatch, missing: str):
    real = blocker_resolve.importlib.import_module

    def import_module(name, *args):
        if name == "blocker_branch_pr":
            raise ModuleNotFoundError(f"No module named {missing!r}", name=missing)
        return real(name, *args)
    monkeypatch.setattr(blocker_resolve.importlib, "import_module", import_module)


def test_an_absent_class_module_is_named_and_skipped(rec, monkeypatch, capsys):
    _absent(monkeypatch, "blocker_branch_pr")
    assert blocker_resolve.resolve_blocker(CARD, _blocker("branch-without-pr"), repo=REPO) is None
    assert rec.log == []
    assert capsys.readouterr().out.splitlines() == [
        "promotion: DRE-1 agent-blocker class=branch-without-pr — action module "
        "blocker_branch_pr is not on this checkout — skipping"]


def test_an_absent_ask_is_named_and_skipped(rec, monkeypatch, capsys):
    monkeypatch.setattr(blocker_resolve, "blocker_ask", None)
    assert blocker_resolve.resolve_blocker(CARD, _blocker("question"), repo=REPO) is None
    assert rec.log == []
    assert capsys.readouterr().out.splitlines() == [
        "promotion: DRE-1 agent-blocker class=question — action module "
        "blocker_ask is not on this checkout — skipping"]


def test_an_absent_ask_after_a_person_s_call_is_named_and_skipped(rec, monkeypatch, capsys):
    _stub(monkeypatch, "blocker_wrong_repo", rec.module(None))
    monkeypatch.setattr(blocker_resolve, "blocker_ask", None)
    assert blocker_resolve.resolve_blocker(CARD, _blocker("wrong-repo"), repo=REPO) is None
    assert rec.comments() == []
    assert capsys.readouterr().out.splitlines() == [
        "promotion: DRE-1 agent-blocker class=wrong-repo — action module "
        "blocker_ask is not on this checkout — skipping"]


def test_a_broken_import_of_another_module_propagates(rec, monkeypatch, capsys):
    _absent(monkeypatch, "requests_oauthlib")
    with pytest.raises(ModuleNotFoundError) as raised:
        blocker_resolve.resolve_blocker(CARD, _blocker("branch-without-pr"), repo=REPO)
    assert raised.value.name == "requests_oauthlib"
    assert rec.log == []
    assert "is not on this checkout" not in capsys.readouterr().out


def test_a_real_module_whose_own_import_is_broken_propagates(rec, monkeypatch, tmp_path):
    """Not a stubbed `import_module`: a module file on the path whose first
    line imports something that is not there."""
    (tmp_path / "blocker_branch_pr.py").write_text(
        "import dre6508_not_a_module_anywhere\n", encoding="utf-8")
    monkeypatch.delitem(sys.modules, "blocker_branch_pr", raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ModuleNotFoundError) as raised:
        blocker_resolve.resolve_blocker(CARD, _blocker("branch-without-pr"), repo=REPO)
    assert raised.value.name == "dre6508_not_a_module_anywhere"
    monkeypatch.delitem(sys.modules, "blocker_branch_pr", raising=False)
    assert rec.log == []


# --------------------------------------------------------------------------- #
# every other failure is the call site's                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("error", [
    RuntimeError("boom"),
    linear_ops.LinearError("refused"),
    linear_ops.LinearRateLimited("spent"),
])
def test_any_other_failure_propagates_unchanged(rec, monkeypatch, error):
    _stub(monkeypatch, "blocker_nothing_to_change", rec.module(raises=error))
    with pytest.raises(type(error)) as raised:
        blocker_resolve.resolve_blocker(CARD, _blocker("nothing-to-change"), repo=REPO)
    assert raised.value is error
    assert rec.comments() == []


def test_a_failure_in_the_ask_propagates_with_no_receipt(rec, monkeypatch):
    error = linear_ops.LinearError("refused")

    def resolve(card, reason, *, repo):
        raise error
    monkeypatch.setattr(blocker_resolve, "blocker_ask", SimpleNamespace(resolve=resolve))
    with pytest.raises(linear_ops.LinearError) as raised:
        blocker_resolve.resolve_blocker(CARD, _blocker("question"), repo=REPO)
    assert raised.value is error
    assert rec.comments() == []


# --------------------------------------------------------------------------- #
# the shape lane_callers and check_act_receipts read                           #
# --------------------------------------------------------------------------- #


def _tree() -> ast.Module:
    return ast.parse(SCRIPT.read_text(encoding="utf-8"))


def _innermost(tree: ast.Module) -> dict:
    """`id(call node) -> the innermost enclosing def's name`."""
    out: dict = {}

    def walk(node, owner):
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else owner
            if isinstance(child, ast.Lambda):
                inner = "<lambda>"
            if isinstance(child, ast.Call):
                out[id(child)] = owner
            walk(child, inner)
    walk(tree, "<module>")
    return out


def _is_ask_call(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "resolve" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "blocker_ask")


def test_the_ask_is_called_in_resolve_blocker_s_own_body():
    tree = _tree()
    owners = _innermost(tree)
    calls = [node for node in ast.walk(tree) if _is_ask_call(node)]
    assert calls, "blocker_ask.resolve( is never called"
    assert {owners[id(node)] for node in calls} == {"resolve_blocker"}


def test_the_ask_is_imported_statically_and_never_through_importlib():
    tree = _tree()
    imported = [node for node in tree.body if isinstance(node, ast.Try)
                for stmt in node.body if isinstance(stmt, ast.Import)
                for alias in stmt.names if alias.name == "blocker_ask"]
    assert imported, "blocker_ask is not imported by a top-level try: import"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for argument in node.args:
                assert not (isinstance(argument, ast.Constant)
                            and argument.value == "blocker_ask"), (
                    "blocker_ask is named as a string in a call — an import "
                    "through importlib that lane_callers cannot follow")


def test_the_ask_import_guard_re_raises_another_missing_module():
    tree = _tree()
    (guard,) = [node for node in tree.body if isinstance(node, ast.Try)
                and any(isinstance(s, ast.Import) and s.names[0].name == "blocker_ask"
                        for s in node.body)]
    (handler,) = guard.handlers
    assert isinstance(handler.type, ast.Name) and handler.type.id == "ModuleNotFoundError"
    source = ast.unparse(handler)
    assert "e.name" in source and "'blocker_ask'" in source and "raise" in source


def test_the_module_holds_exactly_one_cmd_comment_call():
    calls = [node for node in ast.walk(_tree()) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "cmd_comment"]
    assert len(calls) == 1


def test_the_one_comment_composes_its_body_inline():
    (call,) = [node for node in ast.walk(_tree()) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and node.func.attr == "cmd_comment"]
    body = call.args[1]
    assert isinstance(body, ast.Call) and ast.unparse(body.func) == "pipeline_act.receipt"
    assert isinstance(body.args[0], ast.Constant) and body.args[0].value == ACT


def test_the_receipt_s_first_words_appear_once():
    assert SCRIPT.read_text(encoding="utf-8").count("🧹 agent-blocker-resolved: class=") == 1


def test_the_tag_is_written_in_one_file_of_scripts_and_workflows():
    hits = []
    for base in (ROOT / "scripts", ROOT / ".github"):
        for path in base.rglob("*"):
            if path.suffix in (".py", ".yml", ".sh") and path.is_file():
                if ACT in path.read_text(encoding="utf-8", errors="replace"):
                    hits.append(path.relative_to(ROOT).as_posix())
    assert hits == ["scripts/blocker_resolve.py"]


# --------------------------------------------------------------------------- #
# the registry row and its doc                                                 #
# --------------------------------------------------------------------------- #


def test_the_act_row_ships_with_the_module():
    row = pipeline_act.record(ACT)
    assert row["tag"] == ACT
    assert row["adopted"] is True
    assert (row["kind"], row["state"]) == ("recovery", "unchanged")
    proof = pipeline_act.record("proof-run-dispatched")
    assert row["subscriber"] == proof["subscriber"] == "reconcile.yml"
    assert row["next_actor"] == "reconcile.py"
    assert row["discharges"] is None
    assert row["cadence_s"] is None
    assert "scripts/blocker_resolve.py" in row["cadence_why"]
    assert row["emits"] == {"file": "scripts/blocker_resolve.py",
                            "anchor": "🧹 agent-blocker-resolved: class="}
    assert "open_blocker" in row["means"] + row["why"]
    assert pipeline_act.problems() == []


def test_the_row_sits_immediately_before_the_fleet_reviewer_outage_row():
    names = pipeline_act.acts()
    assert names.index(ACT) + 1 == names.index("reviewer-outage-fleet-wide")


def test_the_doc_carries_the_row():
    text = (ROOT / "docs" / "pipeline-acts.md").read_text(encoding="utf-8")
    for expected in (ACT, "`🧹 agent-blocker-resolved: class=<class> action=<action> — <note>`",
                     "scripts/blocker_resolve.py", "DRE-6440",
                     "`canceled`", "`replanned`", "`relabeled`", "`pr-opened`",
                     "`pr-found`", "`asked`"):
        assert expected in text, expected
