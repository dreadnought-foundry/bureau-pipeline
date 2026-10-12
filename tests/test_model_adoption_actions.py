"""RED-first tests for DRE-3903 — the adoption ACTIONS, and only the actions.

`scripts/model_adoption.py` (DRE-3895) decides: it sorts every catalog id the
pipeline has never configured into adopt / ignore / ask and prints one Decision
record per candidate. `scripts/model_adoption_actions.py` ACTS on that record,
four ways and no others — the four the adoption workflow (DRE-3898) calls:

  * `apply <candidate-id>` — reads the model home again, writes nothing when
    it changed while the trial ran, re-runs the classifier for that id against
    it, refuses anything that is not `adopt`, and writes the home with ONE
    `PATCH` under its own name, `daily-adoption` (DRE-6943). It edits no file;
  * `render <target>` — the record-card and question texts, and the title the
    in-flight check reads;
  * `open-record-card` / `open-question-card` — the two Linear cards, in
    `scripts/repair_card.py`'s shape: a fake-able ops object, never a raise on
    a Linear failure, `$GITHUB_OUTPUT` lines as the result.

THE TRAPS THESE TESTS PIN.

  * **The Decision fixtures come from the real classifier**, run over the
    committed catalog snapshot plus the successor models a test adds — never
    hand-written records that could drift from what DRE-3895 prints.
  * **The home is read again right before the write**, and a `written_at`
    other than the one `classify` recorded writes nothing: the console's lock
    does not cover this writer, so that comparison is what keeps a save made
    during the trial from being overwritten.
  * **Nothing is written on a refusal** — not on `ignore`, not on `ask`, not on
    a document `model_home.home_errors` rejects — and no file is ever edited.
  * **The question is for a non-technical reader**: no file path, no command,
    no verdict marker, and both prices plus the ladder in its first paragraph.
  * Zero network: every Linear call goes to a recorded fake, and the one
    HTTP write to the home to a recorded stub.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_adoption_actions.py -q
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import dependabot_card  # noqa: E402
import model_adoption as ma  # noqa: E402
import model_catalog as mc  # noqa: E402
import model_fallback as mf  # noqa: E402
import model_home as mh  # noqa: E402
import routing_verdict  # noqa: E402
import validate_card  # noqa: E402
import model_fixture  # noqa: E402

import model_adoption_actions as maa  # noqa: E402

MODULE = ROOT / "scripts" / "model_adoption_actions.py"
CONFIG_PATH = ROOT / "config" / "models.yaml"
PRICES_PATH = ROOT / "config" / "model-prices.yaml"
SNAPSHOT_PATH = ROOT / "models.json"

#: The frozen model config (DRE-6864) and its home document (DRE-6943). The
#: Decisions below are classified against the home's groups, never the
#: committed file: a console save that put Sonnet 4.6 at the Planners' top
#: turned every adoption here into a refusal. The ids it holds are read off
#: its ladders, never spelled.
FROZEN = model_fixture.fixture_config()
SONNET5 = model_fixture.ladder(FROZEN, "workhorse")[-1]   # the Builders' last rung
SONNET46 = model_fixture.ladder(FROZEN, "judgement")[-1]  # the Planners' last rung
OPUS55 = model_fixture.ladder(FROZEN, "workhorse")[0]     # the Builders' top

SONNET6 = "claude-sonnet-6"
OPUS6 = "claude-opus-6"
CORVID = "claude-corvid-1"
CARD = "DRE-9001"
RUN_URL = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/123"
TRIAL = {
    "model": SONNET6,
    "outcome": "passed",
    "run_url": "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/456",
    "summary": "trial card DRE-1 built green in 41 turns; tests 12/12",
}
VERDICT_MARKERS = ("VERDICT:", "QA Critic", "QA Verifier")


# --------------------------------------------------------------------------- #
# Fixtures — the catalog, the prices, and the Decisions the REAL rule prints   #
# --------------------------------------------------------------------------- #

#: The successors a test adds to the committed catalog snapshot. Every rung on
#: every frozen ladder is dated by that snapshot, so the classifier ranks these
#: against the frozen ladders.
SUCCESSORS = [
    {"id": SONNET6, "display_name": "Claude Sonnet 6",
     "created_at": "2026-11-01T00:00:00Z", "in_catalog": True},
    {"id": OPUS6, "display_name": "Claude Opus 6",
     "created_at": "2026-11-05T00:00:00Z", "in_catalog": True},
    {"id": CORVID, "display_name": "Claude Corvid 1",
     "created_at": "2026-11-10T00:00:00Z", "in_catalog": True},
]


def snapshot_with(*entries) -> dict:
    snap = json.loads(SNAPSHOT_PATH.read_text())
    snap["models"] = list(entries) + list(snap["models"])
    return snap


def prices_text(**extra) -> str:
    """Today's config/model-prices.yaml with extra entries appended —
    `claude_sonnet_6=(2, 10)`. The committed file is never edited."""
    lines = [PRICES_PATH.read_text().rstrip("\n")]
    for key, (side_in, side_out) in extra.items():
        lines += [
            f"  {key.replace('_', '-')}:",
            f"    input: {side_in:.2f}",
            f"    output: {side_out:.2f}",
            '    source: "https://www.anthropic.com/pricing"',
            "    declared: 2026-11-01",
        ]
    return "\n".join(lines) + "\n"


def decisions(**extra_prices) -> dict:
    """`{candidate: Decision}` from the real classifier over the snapshot plus
    the successors, against the frozen home's groups and the given extra
    prices."""
    catalog = mc.snapshot_catalog(snapshot_with(*SUCCESSORS))
    prices = ma.load_prices()
    for key, (side_in, side_out) in extra_prices.items():
        prices[key.replace("_", "-")] = {"input": float(side_in),
                                         "output": float(side_out)}
    found = ma.classify_catalog(catalog, ma.home_config(model_fixture.fixture_home()),
                                prices)
    return {d["candidate"]: json.loads(json.dumps(d)) for d in found}


@pytest.fixture(scope="module")
def adopt_sonnet6():
    d = decisions(claude_sonnet_6=(2, 10))[SONNET6]
    assert d["rule"] == ma.RULE_ADOPT, d
    return d


@pytest.fixture(scope="module")
def ask_priced_above():
    d = decisions(claude_opus_6=(6, 30))[OPUS6]
    assert d["rule"] == ma.RULE_ASK and d["reason"].startswith(ma.ASK_PRICED_ABOVE), d
    return d


@pytest.fixture(scope="module")
def ask_no_price():
    d = decisions()[OPUS6]
    assert d["rule"] == ma.RULE_ASK and d["reason"].startswith(ma.ASK_NO_PRICE), d
    return d


@pytest.fixture(scope="module")
def ask_new_family():
    d = decisions(claude_corvid_1=(3, 15))[CORVID]
    assert d["rule"] == ma.RULE_ASK and d["reason"].startswith(ma.ASK_NEW_FAMILY), d
    return d


@pytest.fixture(scope="module")
def ignore_old():
    d = decisions()["claude-opus-4-7"]
    assert d["rule"] == ma.RULE_IGNORE, d
    return d


def write_json(tmp_path, name, data) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class FakeOps:
    """The Linear seam, recorded. Every method mirrors the real signature."""

    def __init__(self, *, open_card=None, fail_create=False, fail_find=False,
                 fail_stamp=False):
        self.open_card = open_card
        self.fail_create = fail_create
        self.fail_find = fail_find
        self.fail_stamp = fail_stamp
        self.calls: list[tuple] = []
        self.created: list[dict] = []
        self.stamped: list[tuple] = []
        self.described: list[tuple] = []

    def find_open(self, title):
        self.calls.append(("find_open", title))
        if self.fail_find:
            raise RuntimeError("Linear says 500")
        return self.open_card

    def create_card(self, title, description, *, repo_slug, labels=(),
                    lane="Planning"):
        self.calls.append(("create_card", title))
        if self.fail_create:
            raise RuntimeError("Linear says 429: rate limited")
        self.created.append({"title": title, "description": description,
                             "repo_slug": repo_slug, "labels": list(labels),
                             "lane": lane})
        return {"identifier": CARD, "url": f"https://linear.app/x/issue/{CARD}"}

    def stamp_card(self, identifier, name, why):
        self.calls.append(("stamp_card", identifier))
        if self.fail_stamp:
            raise RuntimeError("Linear says 500")
        self.stamped.append((identifier, name, why))
        return 0

    def set_description(self, identifier, body):
        self.calls.append(("set_description", identifier))
        self.described.append((identifier, body))


# --------------------------------------------------------------------------- #
# apply — the home, read again and written under the adoption's own name       #
# --------------------------------------------------------------------------- #

OPUS56 = "claude-opus-5-6"
DEMO = "agent-bureau-demo"
WRITTEN_AT = model_fixture.fixture_home()["written_at"]
WRITE_TOKEN = "write-token-from-the-mint"


class PatchStub:
    """The one HTTP write `apply` makes, recorded: `(url, headers, body)` per
    call, answered 204 as GitHub answers a variable update."""

    def __init__(self, status=204):
        self.status = status
        self.calls: list[tuple] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, dict(headers), json.loads(body)))
        return self.status, ""

    def document(self) -> dict:
        (call,) = self.calls
        return json.loads(call[2]["value"])


def opus_home(*, demo_names_opus=True) -> dict:
    """The frozen home, with project agent-bureau-demo trying Opus 5.5 first
    on the Builders when asked."""
    doc = model_fixture.fixture_home()
    if demo_names_opus:
        doc = model_fixture.with_project(doc, DEMO, "workhorse", OPUS55)
    return doc


class TestApply:
    """`apply` against a home whose every group runs Opus 5.5, with an Opus
    5.6 the rule says to adopt — cheaper than Opus 5.5 on both sides."""

    @pytest.fixture
    def stub(self, monkeypatch):
        stub = PatchStub()
        monkeypatch.setattr(maa, "_http_patch", stub)
        monkeypatch.setenv(maa.WRITE_TOKEN_ENV, WRITE_TOKEN)
        return stub

    def _setup(self, tmp_path, monkeypatch, doc=None, *, opus56_price=(3, 15)):
        home = tmp_path / "home.json"
        home.write_text(json.dumps(doc if doc is not None else opus_home()))
        monkeypatch.setenv(mh.FILE_ENV, str(home))
        prices = tmp_path / "model-prices.yaml"
        prices.write_text(prices_text(claude_opus_5_6=opus56_price))
        snap = write_json(tmp_path, "catalog.json", snapshot_with(
            {"id": OPUS56, "display_name": "Claude Opus 5.6",
             "created_at": "2026-10-10T00:00:00Z", "in_catalog": True}))
        return home, prices, snap

    def _apply(self, capsys, tmp_path, prices, snap, *extra,
               expect=WRITTEN_AT, candidate=OPUS56):
        args = ["apply", candidate, "--snapshot", snap, "--prices", str(prices),
                "--result", str(tmp_path / "applied.json")]
        if expect is not None:
            args += ["--expect-written-at", expect]
        rc = maa.main([*args, *extra])
        return rc, capsys.readouterr()

    def test_the_module_declares_that_every_command_reads_the_home(self):
        assert maa.READS_MODEL_HOME == ("*",)

    def test_one_patch_puts_the_successor_where_the_old_id_stood(
            self, tmp_path, monkeypatch, capsys, stub):
        _, prices, snap = self._setup(tmp_path, monkeypatch)
        before = opus_home()
        rc, out = self._apply(capsys, tmp_path, prices, snap)
        assert rc == 0, out.out + out.err
        assert len(stub.calls) == 1, "one PATCH, no retry"
        url, headers, body = stub.calls[0]
        assert url == mh.API_URL
        assert headers["Authorization"] == f"Bearer {WRITE_TOKEN}"
        assert set(body) == {"value"}
        after = stub.document()
        # Every group that ran Opus 5.5 runs Opus 5.6 in its place, in order.
        moved = [key for key in before["groups"]
                 if OPUS55 in before["groups"][key]["models"]]
        assert {"workhorse", "advisory", "judgement"} == set(moved)
        for key, group in before["groups"].items():
            assert after["groups"][key]["models"] == [
                OPUS56 if m == OPUS55 else m for m in group["models"]]
            assert after["groups"][key]["label"] == group["label"]
        # The project that named it names the successor.
        assert after["projects"][DEMO] == {"workhorse": OPUS56}
        # Under the adoption's own name, at a fresh time; nothing else moves.
        assert after["written_by"] == "daily-adoption"
        assert after["written_at"] != WRITTEN_AT
        assert datetime.strptime(after["written_at"], "%Y-%m-%dT%H:%M:%SZ")
        for key in ("version", "agents", "added_agents"):
            assert after[key] == before[key]
        assert mh.home_errors(after, mf.declared_prices(prices), mh.code_side(),
                              mf.declared_repo_slugs()) == []
        result = json.loads((tmp_path / "applied.json").read_text())
        assert result["outcome"] == "written"
        assert result["written_at"] == after["written_at"]

    def test_a_project_choice_on_another_model_is_left_alone(
            self, tmp_path, monkeypatch, capsys, stub):
        doc = opus_home(demo_names_opus=False)
        assert doc["projects"][DEMO] != {"workhorse": OPUS55}
        _, prices, snap = self._setup(tmp_path, monkeypatch, doc)
        rc, out = self._apply(capsys, tmp_path, prices, snap)
        assert rc == 0, out.out + out.err
        assert stub.document()["projects"] == doc["projects"]

    def test_the_old_id_left_on_nothing_is_said_not_retired(
            self, tmp_path, monkeypatch, capsys, stub):
        _, prices, snap = self._setup(tmp_path, monkeypatch)
        rc, out = self._apply(capsys, tmp_path, prices, snap)
        assert rc == 0
        sentence = maa.not_retired_sentence(OPUS55)
        assert sentence == (
            f"{OPUS55} is on no group and no project of the home and is not "
            "retired; retiring it is a code change a person makes once part 3 "
            "has taken the choices out of config/models.yaml")
        assert sentence in out.out
        result = json.loads((tmp_path / "applied.json").read_text())
        assert sentence in result["sentences"]

    def test_every_project_choice_naming_a_replaced_id_moves(
            self, tmp_path, monkeypatch, capsys, stub):
        # "In every project choice that names it": the Builders' choice and
        # the Reviewers' both name Opus 5.5 and both move; a choice of a
        # model the adoption replaces nowhere stays.
        doc = model_fixture.with_project(opus_home(), DEMO, "advisory", OPUS55)
        doc = model_fixture.with_project(doc, "portico", "workhorse", "claude-opus-5")
        _, prices, snap = self._setup(tmp_path, monkeypatch, doc)
        rc, out = self._apply(capsys, tmp_path, prices, snap)
        assert rc == 0, out.out + out.err
        after = stub.document()
        assert after["projects"][DEMO] == {"workhorse": OPUS56, "advisory": OPUS56}
        assert after["projects"]["portico"] == {"workhorse": "claude-opus-5"}

    def test_only_an_old_id_left_on_nothing_is_said(
            self, tmp_path, monkeypatch, capsys, stub):
        # The Reviewers run Opus 5 as their newest Opus, so the adoption takes
        # Opus 5 there — and Opus 5 stays on the Builders, below Opus 5.6.
        doc = opus_home()
        doc["groups"]["advisory"]["models"] = ["claude-sonnet-5-5", "claude-sonnet-5",
                                               "claude-opus-5"]
        _, prices, snap = self._setup(tmp_path, monkeypatch, doc)
        rc, out = self._apply(capsys, tmp_path, prices, snap)
        assert rc == 0, out.out + out.err
        after = stub.document()
        assert after["groups"]["advisory"]["models"] == [
            "claude-sonnet-5-5", "claude-sonnet-5", OPUS56]
        assert "claude-opus-5" in after["groups"]["workhorse"]["models"]
        result = json.loads((tmp_path / "applied.json").read_text())
        assert result["sentences"] == [maa.not_retired_sentence(OPUS55)]

    def test_a_home_changed_during_the_trial_is_not_written(
            self, tmp_path, monkeypatch, capsys, stub):
        doc = opus_home()
        doc["written_at"] = "2026-10-12T09:30:00Z"   # a console save mid-trial
        doc["written_by"] = "console"
        _, prices, snap = self._setup(tmp_path, monkeypatch, doc)
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        rc, out = self._apply(capsys, tmp_path, prices, snap)
        assert rc == 0, "nothing written is a green run"
        assert stub.calls == [], "no PATCH"
        sentence = ("the home changed at 2026-10-12T09:30:00Z while the trial ran; "
                    "nothing written; tomorrow's run sorts again")
        assert maa.home_changed_sentence("2026-10-12T09:30:00Z") == sentence
        assert sentence in out.out
        assert sentence in summary.read_text()
        result = json.loads((tmp_path / "applied.json").read_text())
        assert result["outcome"] == "home-changed"
        assert result["sentences"] == [sentence]

    def test_a_home_that_cannot_be_read_again_is_not_written(
            self, tmp_path, monkeypatch, capsys, stub):
        _, prices, snap = self._setup(tmp_path, monkeypatch)
        monkeypatch.delenv(mh.FILE_ENV)
        rc, out = self._apply(capsys, tmp_path, prices, snap)
        assert rc == 0
        assert stub.calls == []
        result = json.loads((tmp_path / "applied.json").read_text())
        assert result["outcome"] == "unread"
        assert "nothing written; tomorrow's run sorts again" in result["sentences"][0]
        assert "model-home: not set" in out.err

    def test_no_write_token_writes_nothing_and_fails(
            self, tmp_path, monkeypatch, capsys, stub):
        _, prices, snap = self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv(maa.WRITE_TOKEN_ENV, "")
        rc, out = self._apply(capsys, tmp_path, prices, snap)
        assert rc == 2
        assert stub.calls == []
        assert maa.WRITE_TOKEN_ENV in out.out

    def test_a_refused_patch_fails_the_run(self, tmp_path, monkeypatch, capsys):
        stub = PatchStub(status=403)
        monkeypatch.setattr(maa, "_http_patch", stub)
        monkeypatch.setenv(maa.WRITE_TOKEN_ENV, WRITE_TOKEN)
        _, prices, snap = self._setup(tmp_path, monkeypatch)
        rc, out = self._apply(capsys, tmp_path, prices, snap)
        assert rc == 2
        assert len(stub.calls) == 1
        assert "403" in out.out

    def test_without_the_expected_written_at_nothing_is_written(
            self, tmp_path, monkeypatch, capsys, stub):
        _, prices, snap = self._setup(tmp_path, monkeypatch)
        rc, _ = self._apply(capsys, tmp_path, prices, snap, expect=None)
        assert rc == 2
        assert stub.calls == []

    def test_check_judges_the_document_and_writes_nothing(
            self, tmp_path, monkeypatch, capsys, stub):
        _, prices, snap = self._setup(tmp_path, monkeypatch)
        rc, out = self._apply(capsys, tmp_path, prices, snap, "--check")
        assert rc == 0, out.out + out.err
        assert stub.calls == []
        assert f'-        "{OPUS55}"' in out.out
        assert f'+        "{OPUS56}"' in out.out
        assert '+  "written_by": "daily-adoption"' in out.out

    @pytest.mark.parametrize("rule", ["no declared price", "excluded"])
    def test_check_refuses_what_the_home_rules_refuse(
            self, tmp_path, monkeypatch, capsys, stub, rule):
        # The rule classified Opus 5.6 `adopt`; the home's own rules, judged
        # on the document `apply` would write, still say no — and `apply`
        # repeats their plain sentence and writes nothing.
        _, prices, snap = self._setup(tmp_path, monkeypatch)
        if rule == "excluded":
            side = dict(mh.code_side(), excluded=[*mh.code_side()["excluded"], OPUS56])
            monkeypatch.setattr(mh, "code_side", lambda: side)
            want = f"{OPUS56} is excluded, so it cannot be chosen."
        else:
            declared = dict(mf.declared_prices(prices))
            declared.pop(OPUS56)
            monkeypatch.setattr(mf, "declared_prices", lambda path=None: declared)
            want = mf._unpriced(OPUS56)
        for extra in (("--check",), ()):
            rc, out = self._apply(capsys, tmp_path, prices, snap, *extra)
            assert rc == 2, extra
            assert want in out.out
            assert stub.calls == []

    @pytest.mark.parametrize("candidate, opus56_price, rule", [
        ("claude-opus-4-7", (3, 15), "ignore"),
        (OPUS56, (6, 30), "ask"),
    ])
    def test_a_candidate_that_is_not_adopt_is_refused(
            self, tmp_path, monkeypatch, capsys, stub, candidate, opus56_price, rule):
        _, prices, snap = self._setup(tmp_path, monkeypatch, opus56_price=opus56_price)
        rc, out = self._apply(capsys, tmp_path, prices, snap, candidate=candidate)
        assert rc == 2
        assert rule in out.out
        assert stub.calls == []

    def test_a_candidate_the_catalog_does_not_list_is_refused(
            self, tmp_path, monkeypatch, capsys, stub):
        _, prices, snap = self._setup(tmp_path, monkeypatch)
        rc, _ = self._apply(capsys, tmp_path, prices, snap, candidate="claude-opus-9")
        assert rc == 2
        assert stub.calls == []

    def test_the_config_option_is_gone(self, tmp_path, monkeypatch, capsys, stub):
        _, prices, snap = self._setup(tmp_path, monkeypatch)
        rc, _ = self._apply(capsys, tmp_path, prices, snap,
                            "--config", str(CONFIG_PATH))
        assert rc == 2
        assert stub.calls == []


class TestApplyEditsNoFile:
    """In a tree whose `config/models.yaml` still names Opus 5.5 on its
    ladders, as the committed file will until part 3, an adoption that takes
    Opus 5.5 off every group and project of the home leaves that file — and
    every other file of the tree — byte for byte as it was."""

    def test_the_tree_is_untouched_and_still_validates(self, tmp_path, monkeypatch,
                                                       capsys):
        tree = model_fixture.fixture_tree(tmp_path / "tree")
        actions = model_fixture.fixture_module(tree, "model_adoption_actions")
        config = tree / "config" / "models.yaml"
        text = config.read_text()
        cfg = yaml.safe_load(text)
        assert OPUS55 in {m for kind in ("workhorse", "advisory", "judgement")
                          for m in model_fixture.ladder(cfg, kind)}
        retired_before = model_catalog_ids(cfg.get("retired"))
        assert OPUS55 not in retired_before

        doc = model_fixture.fixture_home()
        home = tmp_path / "home.json"
        home.write_text(json.dumps(doc))
        monkeypatch.setenv(mh.FILE_ENV, str(home))
        monkeypatch.setenv(actions.WRITE_TOKEN_ENV, WRITE_TOKEN)
        stub = PatchStub()
        monkeypatch.setattr(actions, "_http_patch", stub)
        prices = tmp_path / "model-prices.yaml"
        frozen = (model_fixture.FIXTURE_DIR / "model-prices.yaml").read_text()
        prices.write_text(frozen.rstrip("\n") + (
            f"\n  {OPUS56}:\n    input: 3.00\n    output: 15.00\n"
            '    source: "https://www.anthropic.com/pricing"\n'
            "    declared: 2026-10-10\n"))
        snap = write_json(tmp_path, "catalog.json", snapshot_with(
            {"id": OPUS56, "display_name": "Claude Opus 5.6",
             "created_at": "2026-10-10T00:00:00Z", "in_catalog": True}))

        before = _tree_bytes(tree)
        rc = actions.main(["apply", OPUS56, "--snapshot", snap, "--prices",
                           str(prices), "--expect-written-at", doc["written_at"],
                           "--result", str(tmp_path / "applied.json")])
        out = capsys.readouterr()
        assert rc == 0, out.out + out.err

        # The home moved: Opus 5.5 is on no group and no project of it.
        after = stub.document()
        assert all(OPUS55 not in g["models"] for g in after["groups"].values())
        assert all(OPUS55 not in p.values() for p in after["projects"].values())
        # The tree did not: not a byte, no `retired:` entry, still valid.
        assert _tree_bytes(tree) == before
        assert config.read_text() == text
        cfg_after = yaml.safe_load(config.read_text())
        assert model_catalog_ids(cfg_after.get("retired")) == retired_before
        tree_mf = actions.mf
        assert tree_mf.policy_errors(cfg_after, tree_mf.declared_prices(),
                                     repo_slugs=tree_mf.declared_repo_slugs()) == []
        # And the record card says the retirement is a person's, later.
        sentence = actions.not_retired_sentence(OPUS55)
        applied = json.loads((tmp_path / "applied.json").read_text())
        decision = next(d for d in actions.classify_catalog(
            mc.snapshot_catalog(json.loads(Path(snap).read_text())),
            ma.home_config(doc), ma.load_prices(prices)) if d["candidate"] == OPUS56)
        body = actions.record_body(decision, run_url=RUN_URL, applied=applied)
        assert sentence in body


def model_catalog_ids(entries) -> list[str]:
    return [e["model"] if isinstance(e, dict) else e for e in entries or []]


def _tree_bytes(tree: Path) -> dict[str, bytes]:
    return {path.relative_to(tree).as_posix(): path.read_bytes()
            for path in sorted(tree.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts}


# --------------------------------------------------------------------------- #
# render — titles and bodies                                                   #
# --------------------------------------------------------------------------- #

_PATH = re.compile(r"[\w.-]+/[\w./-]+|\.(ya?ml|py|json|md)\b")
_COMMAND = re.compile(r"python|`|--|\$ |scripts|\bgh\b|\bmake\b|\bgit\b")


def render(capsys, target, decision_path, *extra):
    rc = maa.main(["render", target, "--decision", decision_path, *extra])
    out = capsys.readouterr().out
    assert rc == 0, out
    return out


class TestTitles:

    def test_record_title_names_the_workhorse_rung(self, tmp_path, capsys,
                                                   adopt_sonnet6):
        path = write_json(tmp_path, "d.json", adopt_sonnet6)
        assert render(capsys, "record-title", path).strip() == (
            f"Model adoption: {SONNET5} → {SONNET6}")

    def test_record_title_falls_back_to_the_first_entry(self, adopt_sonnet6):
        decision = copy.deepcopy(adopt_sonnet6)
        decision["replaces"] = [r for r in decision["replaces"]
                                if r["ladder"] == "judgement"]
        assert maa.record_title(decision) == f"Model adoption: {SONNET46} → {SONNET6}"

    def test_pr_title_names_every_ladder(self, tmp_path, capsys, adopt_sonnet6):
        path = write_json(tmp_path, "d.json", adopt_sonnet6)
        assert render(capsys, "pr-title", path).strip() == (
            f"Adopt {SONNET6} on the workhorse, advisory, judgement ladder "
            f"(replaces {SONNET5})")

    def test_titles_are_deterministic(self, adopt_sonnet6, ask_priced_above):
        assert maa.record_title(adopt_sonnet6) == maa.record_title(
            copy.deepcopy(adopt_sonnet6))
        assert maa.question_title(ask_priced_above) == maa.question_title(
            copy.deepcopy(ask_priced_above))

    def test_question_titles_follow_the_reason_class(
            self, tmp_path, capsys, ask_priced_above, ask_no_price, ask_new_family):
        cases = [
            (ask_new_family, f"Spending decision: {CORVID} — new model family"),
            (ask_priced_above, f"Spending decision: {OPUS6} — priced above {OPUS55}"),
            (ask_no_price, f"Spending decision: {OPUS6} — no declared price"),
        ]
        for decision, want in cases:
            path = write_json(tmp_path, "d.json", decision)
            assert render(capsys, "question-title", path).strip() == want


class TestQuestionBody:

    @pytest.fixture(params=["priced", "no_price", "new_family"])
    def ask(self, request, ask_priced_above, ask_no_price, ask_new_family):
        return {"priced": ask_priced_above, "no_price": ask_no_price,
                "new_family": ask_new_family}[request.param]

    def test_no_path_no_command_no_verdict_marker(self, tmp_path, capsys, ask):
        body = render(capsys, "question-body", write_json(tmp_path, "d.json", ask))
        assert not _PATH.search(body), _PATH.search(body)
        assert not _COMMAND.search(body), _COMMAND.search(body)
        for marker in VERDICT_MARKERS:
            assert marker not in body

    def test_the_first_paragraph_is_the_whole_question(self, tmp_path, capsys,
                                                       ask_priced_above):
        body = render(capsys, "question-body",
                      write_json(tmp_path, "d.json", ask_priced_above))
        first = body.strip().split("\n\n")[0]
        assert "$6.00" in first and "$30.00" in first, "the candidate's price"
        assert "$4.00" in first and "$20.00" in first, "the rung's price"
        assert OPUS55 in first and "Claude Opus 6" in first
        assert "workhorse" in first
        assert "nothing is adopted until you answer" in first.lower()
        assert first.rstrip().endswith("?") or "?" in first

    def test_a_new_family_names_the_ladder_it_may_join(self, tmp_path, capsys,
                                                       monkeypatch, ask_new_family):
        # The ladder discovery lets a new model join is a code-side fact; the
        # model that leads it is the HOME's (DRE-6943) — here a home whose
        # Reviewers lead with Opus 5.5, which no file or default says.
        ladder = mf.CONFIG["discovery"]["on_new_model"]
        doc = model_fixture.lead_with(model_fixture.fixture_home(), ladder, OPUS55)
        home = tmp_path / "home.json"
        home.write_text(json.dumps(doc))
        monkeypatch.setenv(mh.FILE_ENV, str(home))
        body = render(capsys, "question-body",
                      write_json(tmp_path, "d.json", ask_new_family))
        first = body.strip().split("\n\n")[0]
        assert "$3.00" in first and "$15.00" in first
        price = ma.load_prices()[OPUS55]
        assert ladder in first
        assert f"The nearest model we run is {OPUS55}," in first
        assert f"${price['input']:.2f}" in first and f"${price['output']:.2f}" in first

    def test_a_new_family_with_no_home_says_it_could_not_read_the_ladder(
            self, ask_new_family):
        # The suite's default: no home named, so no ladder top is claimed.
        first = maa.question_body(ask_new_family).split("\n\n")[0]
        assert "The nearest model we run" not in first
        assert "We run nothing close to it today." not in first
        assert "could not read" in first

    def test_every_question_says_where_the_ceo_can_set_the_model(
            self, tmp_path, capsys, ask):
        body = render(capsys, "question-body", write_json(tmp_path, "d.json", ask))
        assert ("You can set it yourself on the Settings › Agents & models page, "
                "where it is already offered the day it is priced.") in body
        assert "reviewed change" not in body, "no pull request carries the answer now"

    def test_no_declared_price_is_said_plainly(self, tmp_path, capsys, ask_no_price):
        body = render(capsys, "question-body",
                      write_json(tmp_path, "d.json", ask_no_price))
        first = body.strip().split("\n\n")[0]
        assert "no price" in first.lower()
        assert "$4.00" in first and "$20.00" in first


class TestRecordBody:

    WRITTEN = {"outcome": "written", "written_at": "2026-11-02T07:40:00Z",
               "sentences": []}

    def test_the_record_names_the_home_as_where_the_ladder_changed(
            self, adopt_sonnet6):
        body = maa.record_body(adopt_sonnet6, run_url=RUN_URL, trial=TRIAL,
                               applied=self.WRITTEN)
        first = body.split("\n\n")[1]
        # What moved where, in the words it uses today ...
        assert maa._summary(adopt_sonnet6) in first
        # ... and the home as the place the ladder changed, under its own name.
        assert "in the home" in first
        assert "daily-adoption" in first
        assert "2026-11-02T07:40:00Z" in first
        assert "Settings › Agents & models" in first
        assert "pull request" not in body
        assert RUN_URL in body
        assert adopt_sonnet6["created_at"] in body
        assert TRIAL["summary"] in body
        for marker in VERDICT_MARKERS:
            assert marker not in body

    def test_the_record_carries_every_sentence_apply_said(self, adopt_sonnet6):
        said = maa.not_retired_sentence(SONNET5)
        body = maa.record_body(adopt_sonnet6, run_url=RUN_URL,
                               applied=dict(self.WRITTEN, sentences=[said]))
        assert said in body

    def test_a_home_changed_mid_trial_is_recorded_as_nothing_written(
            self, adopt_sonnet6):
        said = maa.home_changed_sentence("2026-11-02T07:00:00Z")
        body = maa.record_body(adopt_sonnet6, run_url=RUN_URL, applied={
            "outcome": "home-changed", "written_at": "2026-11-02T07:00:00Z",
            "sentences": [said]})
        assert said in body
        assert "daily-adoption" not in body.split("\n\n")[1]

    def test_a_trial_summary_never_carries_a_verdict_marker(self, adopt_sonnet6):
        trial = dict(TRIAL, summary="VERDICT: APPROVE — QA Critic and QA Verifier")
        body = maa.record_body(adopt_sonnet6, run_url=RUN_URL, trial=trial,
                               applied=self.WRITTEN)
        for marker in VERDICT_MARKERS:
            assert marker not in body
        assert "APPROVE" in body, "the rest of the summary still arrives"

    def test_record_body_renders_from_the_cli(self, tmp_path, capsys, adopt_sonnet6):
        body = render(capsys, "record-body",
                      write_json(tmp_path, "d.json", adopt_sonnet6),
                      "--trial", write_json(tmp_path, "t.json", TRIAL),
                      "--applied", write_json(tmp_path, "a.json", self.WRITTEN))
        assert adopt_sonnet6["created_at"] in body
        assert TRIAL["run_url"] in body
        assert "in the home" in body


# --------------------------------------------------------------------------- #
# open-record-card / open-question-card — the Linear half, against a fake      #
# --------------------------------------------------------------------------- #

class TestOpenRecordCard:

    APPLIED = {"outcome": "written", "written_at": "2026-11-02T07:40:00Z",
               "sentences": [maa.not_retired_sentence(SONNET5)]}

    def test_files_the_record_in_done_with_its_labels_and_verdict(
            self, adopt_sonnet6):
        # Done, not In Progress: no pull request will ever close it — the
        # write it records is already made (DRE-6943).
        ops = FakeOps()
        result = maa.open_record_card(adopt_sonnet6, run_url=RUN_URL, ops=ops,
                                      trial=TRIAL, applied=self.APPLIED)
        assert len(ops.created) == 1
        made = ops.created[0]
        assert made["lane"] == "Done"
        assert made["repo_slug"] == "bureau-pipeline"
        assert made["title"] == maa.record_title(adopt_sonnet6)
        for want in ("repo:bureau-pipeline", "agent:devops", "initiative:bureau",
                     "automation"):
            assert want in made["labels"]
        # DRE-6228: automation filed this card; `hand-built` is the CEO's mark.
        assert dependabot_card.LABEL in maa.RECORD_LABELS
        assert routing_verdict.HAND_BUILT_LABEL not in maa.RECORD_LABELS
        assert routing_verdict.HAND_BUILT_LABEL not in made["labels"]
        assert ops.stamped and ops.stamped[0][:2] == (CARD, "WORKBENCH")
        assert "WORKBENCH" in routing_verdict.verdicts()
        assert RUN_URL in ops.stamped[0][2]
        assert result["card"] == CARD
        assert "branch" not in result, "an adoption opens no branch"
        assert result["card_owed"] is False
        assert made["description"] == maa.record_body(
            adopt_sonnet6, run_url=RUN_URL, trial=TRIAL, applied=self.APPLIED)
        assert maa.not_retired_sentence(SONNET5) in made["description"]
        assert ops.described == [], "the body is whole at creation"

    def test_the_record_passes_the_live_card_gate(self, adopt_sonnet6):
        ops = FakeOps()
        maa.open_record_card(adopt_sonnet6, run_url=RUN_URL, ops=ops)
        made = ops.created[0]
        assert validate_card.missing(made["description"], made["labels"]) == []
        assert validate_card.repo_title_mismatch(made["title"], made["labels"]) is None

    def test_a_linear_failure_owes_the_card(self, adopt_sonnet6):
        result = maa.open_record_card(adopt_sonnet6, run_url=RUN_URL,
                                      ops=FakeOps(fail_create=True))
        assert result["card"] == ""
        assert result["card_owed"] is True

    def test_a_failed_search_still_files_the_card(self, adopt_sonnet6):
        ops = FakeOps(fail_find=True)
        result = maa.open_record_card(adopt_sonnet6, run_url=RUN_URL, ops=ops)
        assert result["card"] == CARD and len(ops.created) == 1

    def test_a_stamp_failure_keeps_the_card(self, adopt_sonnet6):
        result = maa.open_record_card(adopt_sonnet6, run_url=RUN_URL,
                                      ops=FakeOps(fail_stamp=True))
        assert result["card"] == CARD

    def test_an_open_card_of_the_same_title_creates_nothing(self, adopt_sonnet6):
        ops = FakeOps(open_card="DRE-77")
        result = maa.open_record_card(adopt_sonnet6, run_url=RUN_URL, ops=ops)
        assert ops.created == [] and ops.stamped == []
        assert ("find_open", maa.record_title(adopt_sonnet6)) in ops.calls
        assert result["card"] == "DRE-77"

    def test_cli_prints_github_output_lines(self, tmp_path, capsys, adopt_sonnet6):
        ops = FakeOps()
        rc = maa.main(["open-record-card", "--decision",
                       write_json(tmp_path, "d.json", adopt_sonnet6),
                       "--run-url", RUN_URL,
                       "--trial", write_json(tmp_path, "t.json", TRIAL),
                       "--applied", write_json(tmp_path, "a.json", self.APPLIED)],
                      ops=ops)
        lines = capsys.readouterr().out.splitlines()
        assert rc == 0
        assert f"card={CARD}" in lines
        assert f"card_url=https://linear.app/x/issue/{CARD}" in lines
        assert not [line for line in lines if line.startswith("branch=")]
        assert "card_owed=false" in lines
        made = ops.created[0]["description"]
        assert TRIAL["summary"] in made
        assert maa.not_retired_sentence(SONNET5) in made

    def test_cli_on_a_linear_failure_exits_zero_and_owes_the_card(
            self, tmp_path, capsys, adopt_sonnet6):
        rc = maa.main(["open-record-card", "--decision",
                       write_json(tmp_path, "d.json", adopt_sonnet6),
                       "--run-url", RUN_URL], ops=FakeOps(fail_create=True))
        lines = capsys.readouterr().out.splitlines()
        assert rc == 0
        assert "card_owed=true" in lines

    @pytest.mark.parametrize("which", ["ask_priced_above", "ignore_old"])
    def test_a_non_adopt_decision_exits_2_without_touching_ops(
            self, tmp_path, capsys, request, which):
        ops = FakeOps()
        decision = request.getfixturevalue(which)
        rc = maa.main(["open-record-card", "--decision",
                       write_json(tmp_path, "d.json", decision),
                       "--run-url", RUN_URL], ops=ops)
        assert rc == 2
        assert ops.calls == []


class TestOpenQuestionCard:

    def test_files_the_question_with_its_labels_in_planning(self, ask_priced_above):
        ops = FakeOps()
        result = maa.open_question_card(ask_priced_above, ops=ops)
        assert len(ops.created) == 1
        made = ops.created[0]
        assert made["title"] == maa.question_title(ask_priced_above)
        assert made["lane"] == "Planning"
        assert made["repo_slug"] == "bureau-pipeline"
        for want in ("needs-human", "no-code", "agent:devops"):
            assert want in made["labels"]
        assert made["description"] == maa.question_body(ask_priced_above)
        assert ops.stamped == [], "the question is classified by the planner"
        assert result["card"] == CARD and result["card_owed"] is False

    def test_the_question_passes_the_live_card_gate(self, ask_new_family):
        ops = FakeOps()
        maa.open_question_card(ask_new_family, ops=ops)
        made = ops.created[0]
        labels = ["repo:bureau-pipeline", *made["labels"]]
        assert validate_card.missing(made["description"], labels) == []

    def test_an_open_question_of_the_same_title_creates_nothing(self, ask_no_price):
        ops = FakeOps(open_card="DRE-78")
        result = maa.open_question_card(ask_no_price, ops=ops)
        assert ops.created == []
        assert ("find_open", maa.question_title(ask_no_price)) in ops.calls
        assert result["card"] == "DRE-78"

    def test_a_linear_failure_owes_the_card(self, tmp_path, capsys, ask_priced_above):
        rc = maa.main(["open-question-card", "--decision",
                       write_json(tmp_path, "d.json", ask_priced_above)],
                      ops=FakeOps(fail_create=True))
        lines = capsys.readouterr().out.splitlines()
        assert rc == 0
        assert "card_owed=true" in lines
        assert "card=" in lines

    def test_cli_prints_the_card(self, tmp_path, capsys, ask_priced_above):
        rc = maa.main(["open-question-card", "--decision",
                       write_json(tmp_path, "d.json", ask_priced_above)],
                      ops=FakeOps())
        lines = capsys.readouterr().out.splitlines()
        assert rc == 0
        assert f"card={CARD}" in lines
        assert "card_owed=false" in lines

    @pytest.mark.parametrize("which", ["adopt_sonnet6", "ignore_old"])
    def test_a_non_ask_decision_exits_2_without_touching_ops(
            self, tmp_path, capsys, request, which):
        ops = FakeOps()
        decision = request.getfixturevalue(which)
        rc = maa.main(["open-question-card", "--decision",
                       write_json(tmp_path, "d.json", decision)], ops=ops)
        assert rc == 2
        assert ops.calls == []


# --------------------------------------------------------------------------- #
# The CLI contract DRE-3898's workflow calls                                   #
# --------------------------------------------------------------------------- #

class TestCliContract:

    def test_the_four_commands_and_six_render_targets(self):
        assert maa.COMMANDS == ("apply", "render", "open-record-card",
                                "open-question-card")
        # `pr-body` went with the adoption's pull request (DRE-6943); the
        # title stays, because the in-flight check still reads it.
        assert maa.RENDER_TARGETS == ("pr-title", "question-title",
                                      "question-body", "record-title",
                                      "record-body")

    def test_an_unknown_command_or_target_exits_2(self, tmp_path, capsys,
                                                  adopt_sonnet6):
        assert maa.main(["classify"]) == 2
        assert maa.main([]) == 2
        assert maa.main(["render", "tweet", "--decision",
                         write_json(tmp_path, "d.json", adopt_sonnet6)]) == 2
        capsys.readouterr()

    def test_runs_as_a_script(self, tmp_path, adopt_sonnet6):
        out = subprocess.run(
            [sys.executable, str(MODULE), "render", "record-title", "--decision",
             write_json(tmp_path, "d.json", adopt_sonnet6)],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == f"Model adoption: {SONNET5} → {SONNET6}"

    def test_the_rule_module_is_imported_not_restated(self):
        # One classifier and one price reader: this module imports both.
        assert maa.classify_catalog is ma.classify_catalog
        assert maa.load_prices is ma.load_prices
