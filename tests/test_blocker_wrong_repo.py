"""RED-first tests: the wrong-repo class resolves by the repo label (DRE-6446).

DRE-3242 was built at agent-bureau because its `repo:` label said so; the
agent found the file lives only in bureau-pipeline, said so in its blocker,
changed the label itself, and the card was still skipped as blocked for three
days because nothing read the blocker again. `scripts/blocker_wrong_repo.py`
is the action module the sweep's resolver (DRE-6508) imports by the name
`config/blocker-classes.json` gives it. It is driven here alone, with
`linear_ops` stubbed: the run through the real resolver is DRE-6509's.

WHAT THESE TESTS PIN.

  * Reading A — the LIVE labels (`get_issue(fresh=True)`, never
    `card["labels"]`) no longer carry the dispatched repo's label and carry
    exactly one other: the blocker is spent, `("relabeled", …)`, no write.
  * Reading B — the live labels carry the dispatched repo's label and the
    blocker names exactly one other rail slug: the label is swapped ADD FIRST,
    then the old one removed, and a half-done swap finishes on the next call.
  * Reading C — every other case is a person's call: `None`, no write.
  * A card that left Backlog raises `NotNow` naming the lane, no write.
  * A refused write propagates unchanged, and the order of the two writes
    means a card is never left with no `repo:` label.

Run: cd bureau-pipeline && python3 -m pytest tests/test_blocker_wrong_repo.py -v
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("LINEAR_API_KEY", "test-key")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import blocker_class  # noqa: E402
import blocker_wrong_repo  # noqa: E402
import linear_ops  # noqa: E402

MODULE = ROOT / "scripts" / "blocker_wrong_repo.py"
FIXTURE = ROOT / "tests" / "fixtures" / "blocker-reasons.json"

CARD = "DRE-3242"
SWEEP = "dreadnought-foundry/bureau-pipeline"
RUN_AB = "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/37542061691"
PARKED = (" — parked in Backlog until the blocker is resolved (a Todo return "
          "here would redispatch agents into the same wall). Run: ")
PROSE = ("The budget table script lives only in the other repository, so this "
         "run cannot change it.")
#: The words the resolver's receipt opens with for this class and action.
RECEIPT_WORDS = "class=wrong-repo action=relabeled"


def marker(reason: str, run: str | None = RUN_AB, *, cls: str | None = "wrong-repo") -> str:
    """A marker in the poster's grammar (DRE-6444)."""
    head = f"🛑 Agent blocked: class={cls} · " if cls else "🛑 Agent blocked: "
    if run is None:
        return f"{head}{reason} — parked in Backlog until the blocker is resolved."
    return f"{head}{reason}{PARKED}{run}"


def stamped(slug: str, prose: str = PROSE) -> str:
    return f"repo: {slug}\n{prose}"


def card(body: str, *, labels=("repo:agent-bureau",), title="the budget table retries",
         earlier=(), partial=False) -> dict:
    """The sweep's card dict: `comments` is the API's newest-first window."""
    bodies = [*earlier, body]
    comments = {"nodes": [{"body": b} for b in reversed(bodies)]}
    if partial:
        comments["pageInfo"] = {"hasNextPage": True}
    return {
        "identifier": CARD,
        "title": title,
        "labels": {"nodes": [{"name": name} for name in labels]},
        "description": "",
        "comments": comments,
    }


class Linear:
    """`linear_ops` as the module sees it: what the live read answers, and
    every call made, in order."""

    def __init__(self, labels=("repo:agent-bureau",), lane="Backlog", *,
                 refuse_add=False, refuse_remove=False, thread=None):
        self.labels = list(labels)
        self.lane = lane
        self.refuse_add = refuse_add
        self.refuse_remove = refuse_remove
        self.thread = thread
        self.calls: list = []
        self.add_error = linear_ops.LinearError("add refused")
        self.remove_error = linear_ops.LinearError("remove refused")

    def get_issue(self, identifier, *, fresh=False):
        self.calls.append(("get_issue", identifier, fresh))
        return {
            "identifier": identifier,
            "state": {"name": self.lane},
            "labels": {"nodes": [{"name": name} for name in self.labels]},
        }

    def add_label(self, identifier, name):
        self.calls.append(("add_label", identifier, name))
        if self.refuse_add:
            raise self.add_error

    def remove_label(self, identifier, name):
        self.calls.append(("remove_label", identifier, name))
        if self.refuse_remove:
            raise self.remove_error

    def comment_bodies(self, identifier, *, whole_thread=False):
        self.calls.append(("comment_bodies", identifier, whole_thread))
        return list(self.thread or [])

    @property
    def writes(self) -> list:
        return [c for c in self.calls if c[0] in ("add_label", "remove_label")]


@pytest.fixture
def linear(monkeypatch):
    holder: dict = {}

    def install(**kwargs) -> Linear:
        stub = Linear(**kwargs)
        for name in ("get_issue", "add_label", "remove_label", "comment_bodies"):
            monkeypatch.setattr(linear_ops, name, getattr(stub, name))
        holder["stub"] = stub
        return stub

    def no_network(*_a, **_k):
        raise AssertionError("an unstubbed Linear call was made")

    monkeypatch.setattr(linear_ops, "gql", no_network)
    for name in ("comment", "cmd_advance", "cmd_state"):
        if hasattr(linear_ops, name):
            monkeypatch.setattr(linear_ops, name, no_network)
    return install


def resolve(c: dict):
    body = blocker_class.newest_marker(
        [n["body"] for n in reversed(c["comments"]["nodes"])]).body
    return blocker_wrong_repo.resolve(c, blocker_class.marker_reason(body), repo=SWEEP)


def fixture_entry(run: int) -> dict:
    for entry in json.loads(FIXTURE.read_text(encoding="utf-8")):
        if entry["card"] == CARD and entry["run"] == run:
            return entry
    raise AssertionError(f"no {CARD} entry for run {run} in the fixture")


# --------------------------------------------------------------------------- #
# the shape                                                                    #
# --------------------------------------------------------------------------- #


def test_the_vocabulary_names_this_module_and_it_has_the_shared_shape():
    action = blocker_class.load()["classes"]["wrong-repo"]["action"]
    assert action == "blocker_wrong_repo" == MODULE.stem
    params = inspect.signature(blocker_wrong_repo.resolve).parameters
    assert list(params) == ["card", "reason", "repo"]
    assert params["repo"].kind is inspect.Parameter.KEYWORD_ONLY


def test_the_module_never_spells_the_resolvers_tag_and_posts_nothing():
    source = MODULE.read_text(encoding="utf-8")
    assert "agent-blocker-resolved" not in source
    for writer in ("linear_ops.comment(", "cmd_advance(", "cmd_state(", "post_comment"):
        assert writer not in source, writer


# --------------------------------------------------------------------------- #
# reading A — the blocker is spent                                             #
# --------------------------------------------------------------------------- #


def test_reading_a_a_person_changed_the_label_nothing_is_written(linear):
    stub = linear(labels=("repo:bureau-pipeline",))
    c = card(marker(stamped("bureau-pipeline")), labels=("repo:bureau-pipeline",))
    action, note = resolve(c)
    assert action == "relabeled"
    assert note == ("already relabeled by a person — label is now repo:bureau-pipeline, "
                    "changed after the run at agent-bureau")
    assert stub.writes == []


def test_reading_a_is_decided_on_the_live_read_not_the_board(linear):
    """The board still says agent-bureau; the live read does not."""
    stub = linear(labels=("repo:bureau-pipeline",))
    c = card(marker(stamped("bureau-pipeline")), labels=("repo:agent-bureau",))
    action, note = resolve(c)
    assert action == "relabeled"
    assert note.startswith("already relabeled by a person")
    assert stub.writes == []
    assert ("get_issue", CARD, True) in stub.calls


# --------------------------------------------------------------------------- #
# reading B — the sweep swaps the label                                        #
# --------------------------------------------------------------------------- #


def test_reading_b_swaps_the_label_add_first(linear):
    stub = linear(labels=("repo:agent-bureau",))
    reason = blocker_class.marker_reason(marker(stamped("bureau-pipeline")))
    assert reason.splitlines()[0] == "repo: bureau-pipeline"
    c = card(marker(stamped("bureau-pipeline")))
    assert blocker_wrong_repo.resolve(c, reason, repo=SWEEP) == (
        "relabeled", "repo:agent-bureau → repo:bureau-pipeline, named by the blocker")
    assert stub.writes == [
        ("add_label", CARD, "repo:bureau-pipeline"),
        ("remove_label", CARD, "repo:agent-bureau"),
    ]


def test_reading_b_the_dre_3242_legacy_note_names_one_other_rail_slug(linear):
    entry = fixture_entry(37542061691)
    assert "blocker-class:" not in entry["body"] and "class=" not in entry["body"]
    stub = linear(labels=("repo:agent-bureau",))
    action, _ = resolve(card(entry["body"]))
    assert action == "relabeled"
    assert stub.writes == [
        ("add_label", CARD, "repo:bureau-pipeline"),
        ("remove_label", CARD, "repo:agent-bureau"),
    ]


def test_a_run_url_under_another_owner_is_read_as_its_repo(linear):
    stub = linear(labels=("repo:atlas",))
    body = marker(stamped("bureau-pipeline"), "https://github.com/EveryBite/atlas/actions/runs/1")
    action, note = resolve(card(body, labels=("repo:atlas",)))
    assert (action, note) == ("relabeled", "repo:atlas → repo:bureau-pipeline, named by the blocker")
    assert stub.writes == [
        ("add_label", CARD, "repo:bureau-pipeline"),
        ("remove_label", CARD, "repo:atlas"),
    ]


def test_the_dispatched_repo_is_the_last_run_url_the_poster_appended(linear):
    """The agent's own text cites a portico run; the poster's is agent-bureau."""
    prose = ("The sibling built at https://github.com/dreadnought-foundry/portico/"
             "actions/runs/5 — Run: https://github.com/dreadnought-foundry/portico/"
             "actions/runs/5 — and this file is elsewhere.")
    stub = linear(labels=("repo:agent-bureau",))
    action, _ = resolve(card(marker(stamped("bureau-pipeline", prose))))
    assert action == "relabeled"
    assert stub.writes == [
        ("add_label", CARD, "repo:bureau-pipeline"),
        ("remove_label", CARD, "repo:agent-bureau"),
    ]


def test_the_stamp_line_is_read_off_the_reasons_first_line_only(linear):
    """A `repo:` line anywhere but first is prose: two slugs named, a person's call."""
    stub = linear(labels=("repo:agent-bureau",))
    reason = f"{PROSE}\nrepo: bureau-pipeline\nor maybe portico"
    assert resolve(card(marker(reason, cls=None))) is None
    assert stub.writes == []


# --------------------------------------------------------------------------- #
# reading C — a person's call                                                  #
# --------------------------------------------------------------------------- #


def test_c_the_sweep_already_relabeled_this_card_once(linear):
    receipt = f"✅ {RECEIPT_WORDS} — repo:portico → repo:agent-bureau, named by the blocker"
    stub = linear(labels=("repo:agent-bureau",))
    assert resolve(card(marker(stamped("bureau-pipeline")), earlier=[receipt])) is None
    assert stub.writes == []


def test_c_a_receipt_beyond_the_comment_window_is_still_read(linear):
    receipt = f"✅ {RECEIPT_WORDS} — repo:portico → repo:agent-bureau, named by the blocker"
    body = marker(stamped("bureau-pipeline"))
    stub = linear(labels=("repo:agent-bureau",), thread=[receipt, body])
    assert resolve(card(body, partial=True)) is None
    assert stub.writes == []
    assert ("comment_bodies", CARD, True) in stub.calls


def test_c_a_name_that_only_extends_the_dispatched_slug_names_nothing(linear):
    stub = linear(labels=("repo:agent-bureau-demo",))
    body = marker("The file is not in agent-bureau-demo at all.",
                  "https://github.com/dreadnought-foundry/agent-bureau-demo/actions/runs/9",
                  cls=None)
    assert resolve(card(body, labels=("repo:agent-bureau-demo",))) is None
    assert stub.writes == []


def test_c_the_checkout_path_is_not_a_mention(linear):
    stub = linear(labels=("repo:agent-bureau",))
    body = marker("The file is .bureau-pipeline/scripts/x.py and nowhere else.", cls=None)
    assert resolve(card(body)) is None
    assert stub.writes == []


def test_c_no_slug_named(linear):
    stub = linear(labels=("repo:agent-bureau",))
    assert resolve(card(marker("This card is pointed at the wrong repo."))) is None
    assert stub.writes == []


def test_c_two_slugs_named(linear):
    stub = linear(labels=("repo:agent-bureau",))
    body = marker("The files are split between bureau-pipeline and portico.", cls=None)
    assert resolve(card(body)) is None
    assert stub.writes == []


def test_c_the_named_slug_is_off_the_rail(linear):
    import validate_card  # noqa: PLC0415

    assert "not-a-repo" not in validate_card.VALID_SLUGS
    stub = linear(labels=("repo:agent-bureau",))
    assert resolve(card(marker(stamped("not-a-repo")))) is None
    assert stub.writes == []


def test_c_the_stamp_names_the_dispatched_repo_itself(linear):
    stub = linear(labels=("repo:agent-bureau",))
    assert resolve(card(marker(stamped("agent-bureau")))) is None
    assert stub.writes == []


def test_c_the_title_prefix_disagrees_with_the_named_slug(linear):
    stub = linear(labels=("repo:agent-bureau",))
    c = card(marker(stamped("bureau-pipeline")), title="agent-bureau: the budget table retries")
    assert resolve(c) is None
    assert stub.writes == []


@pytest.mark.parametrize("live", [("repo:agent-bureau",), ("repo:bureau-pipeline",)])
def test_c_a_legacy_marker_with_no_run_url(linear, live):
    stub = linear(labels=live)
    assert resolve(card(marker(stamped("bureau-pipeline"), None))) is None
    assert stub.writes == []


@pytest.mark.parametrize("live", [(), ("repo:portico", "repo:bureau-pipeline")])
def test_c_live_labels_with_no_dispatched_label_and_not_exactly_one_other(linear, live):
    stub = linear(labels=live)
    assert resolve(card(marker(stamped("bureau-pipeline")))) is None
    assert stub.writes == []


# --------------------------------------------------------------------------- #
# the lane                                                                     #
# --------------------------------------------------------------------------- #


def test_a_card_that_left_backlog_is_not_now_and_nothing_is_written(linear):
    stub = linear(labels=("repo:agent-bureau",), lane="Todo")
    with pytest.raises(blocker_class.NotNow) as raised:
        resolve(card(marker(stamped("bureau-pipeline"))))
    assert str(raised.value) == "the card left Backlog — it is in Todo"
    assert stub.writes == []


# --------------------------------------------------------------------------- #
# refused writes                                                               #
# --------------------------------------------------------------------------- #


def test_a_refused_add_propagates_and_nothing_is_removed(linear):
    stub = linear(labels=("repo:agent-bureau",), refuse_add=True)
    with pytest.raises(linear_ops.LinearError) as raised:
        resolve(card(marker(stamped("bureau-pipeline"))))
    assert raised.value is stub.add_error
    assert stub.writes == [("add_label", CARD, "repo:bureau-pipeline")]


def test_a_refused_remove_propagates_with_the_add_landed(linear):
    stub = linear(labels=("repo:agent-bureau",), refuse_remove=True)
    with pytest.raises(linear_ops.LinearError) as raised:
        resolve(card(marker(stamped("bureau-pipeline"))))
    assert raised.value is stub.remove_error
    assert stub.writes == [
        ("add_label", CARD, "repo:bureau-pipeline"),
        ("remove_label", CARD, "repo:agent-bureau"),
    ]


def test_a_half_done_swap_finishes_on_the_next_call(linear):
    stub = linear(labels=("repo:agent-bureau", "repo:bureau-pipeline"))
    action, note = resolve(card(marker(stamped("bureau-pipeline"))))
    assert (action, note) == (
        "relabeled", "repo:agent-bureau → repo:bureau-pipeline, named by the blocker")
    assert stub.writes == [
        ("add_label", CARD, "repo:bureau-pipeline"),
        ("remove_label", CARD, "repo:agent-bureau"),
    ]


def test_a_spent_quota_on_the_live_read_propagates(linear, monkeypatch):
    linear(labels=("repo:agent-bureau",))
    spent = linear_ops.LinearRateLimited("quota spent")

    def refuse(*_a, **_k):
        raise spent

    monkeypatch.setattr(linear_ops, "get_issue", refuse)
    with pytest.raises(linear_ops.LinearRateLimited) as raised:
        resolve(card(marker(stamped("bureau-pipeline"))))
    assert raised.value is spent
