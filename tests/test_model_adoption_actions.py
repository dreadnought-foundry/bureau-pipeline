"""RED-first tests for DRE-3903 — the adoption ACTIONS, and only the actions.

`scripts/model_adoption.py` (DRE-3895) decides: it sorts every catalog id the
pipeline has never configured into adopt / ignore / ask and prints one Decision
record per candidate. `scripts/model_adoption_actions.py` ACTS on that record,
four ways and no others — the four the adoption workflow (DRE-3898) calls:

  * `apply <candidate-id>` — re-runs the classifier for that id, refuses
    anything that is not `adopt`, and rewrites `config/models.yaml` as a TEXT
    edit, so every comment and every `reason:` block survives byte for byte;
  * `render <target>` — the PR, record-card and question texts;
  * `open-record-card` / `open-question-card` — the two Linear cards, in
    `scripts/repair_card.py`'s shape: a fake-able ops object, never a raise on
    a Linear failure, `$GITHUB_OUTPUT` lines as the result.

THE TRAPS THESE TESTS PIN.

  * **The Decision fixtures come from the real classifier**, run over the
    committed catalog snapshot plus the successor models a test adds — never
    hand-written records that could drift from what DRE-3895 prints.
  * **The config is edited as text, never re-dumped.** A YAML round-trip drops
    every comment in `config/models.yaml`, and the comments ARE the file's
    record of every spend decision ever made in it.
  * **Nothing is written on a refusal** — not on `ignore`, not on `ask`, not on
    a result `model_fallback.policy_errors` rejects.
  * **The question is for a non-technical reader**: no file path, no command,
    no verdict marker, and both prices plus the ladder in its first paragraph.
  * Zero network: every Linear call goes to a recorded fake.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_adoption_actions.py -q
"""

from __future__ import annotations

import copy
import difflib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dependabot_card  # noqa: E402
import model_adoption as ma  # noqa: E402
import model_catalog as mc  # noqa: E402
import model_fallback as mf  # noqa: E402
import routing_verdict  # noqa: E402
import sync_model_config  # noqa: E402
import validate_card  # noqa: E402

import model_adoption_actions as maa  # noqa: E402

MODULE = ROOT / "scripts" / "model_adoption_actions.py"
CONFIG_PATH = ROOT / "config" / "models.yaml"
PRICES_PATH = ROOT / "config" / "model-prices.yaml"
SNAPSHOT_PATH = ROOT / "models.json"

SONNET6 = "claude-sonnet-6"
SONNET5 = "claude-sonnet-5"
SONNET46 = "claude-sonnet-4-6"
OPUS6 = "claude-opus-6"
OPUS55 = "claude-opus-5-5"
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
#: every ladder today is dated by that snapshot, so the classifier ranks these
#: against the real ladders.
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
    the successors, against today's config and the given extra prices."""
    catalog = mc.snapshot_catalog(snapshot_with(*SUCCESSORS))
    prices = ma.load_prices()
    for key, (side_in, side_out) in extra_prices.items():
        prices[key.replace("_", "-")] = {"input": float(side_in),
                                         "output": float(side_out)}
    found = ma.classify_catalog(catalog, ma.load_config(), prices)
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
# apply — the config edit                                                      #
# --------------------------------------------------------------------------- #

class TestApply:
    """`apply` on today's config, with a Sonnet 6 the rule says to adopt."""

    def _setup(self, tmp_path, *, sonnet6_price=(2, 10), config_text=None):
        config = tmp_path / "models.yaml"
        config.write_text(config_text or CONFIG_PATH.read_text())
        prices = tmp_path / "model-prices.yaml"
        prices.write_text(prices_text(claude_sonnet_6=sonnet6_price))
        snap = write_json(tmp_path, "catalog.json", snapshot_with(*SUCCESSORS))
        return config, prices, snap

    def _apply(self, capsys, config, prices, snap, candidate=SONNET6, *extra):
        rc = maa.main(["apply", candidate, "--snapshot", snap,
                       "--config", str(config), "--prices", str(prices), *extra])
        return rc, capsys.readouterr()

    def test_every_former_sonnet_rung_reads_the_new_id(self, tmp_path, capsys,
                                                       adopt_sonnet6):
        config, prices, snap = self._setup(tmp_path)
        rc, _ = self._apply(capsys, config, prices, snap)
        assert rc == 0
        after = yaml.safe_load(config.read_text())
        before = yaml.safe_load(CONFIG_PATH.read_text())
        for rung in adopt_sonnet6["replaces"]:
            ids = [r["model"] for r in after["ladders"][rung["ladder"]]]
            was = [r["model"] for r in before["ladders"][rung["ladder"]]]
            assert rung["model"] not in ids
            assert ids == [SONNET6 if m == rung["model"] else m for m in was]
        on_a_ladder = {r["model"] for rungs in after["ladders"].values() for r in rungs}
        assert SONNET5 not in on_a_ladder and SONNET46 not in on_a_ladder

    def test_every_comment_and_reason_line_survives_byte_for_byte(self, tmp_path,
                                                                  capsys):
        config, prices, snap = self._setup(tmp_path)
        before = config.read_text().splitlines()
        assert self._apply(capsys, config, prices, snap)[0] == 0
        after = config.read_text().splitlines()
        removed = [l[1:] for l in difflib.unified_diff(before, after, lineterm="", n=0)
                   if l.startswith("-") and not l.startswith("---")]
        added = [l[1:] for l in difflib.unified_diff(before, after, lineterm="", n=0)
                 if l.startswith("+") and not l.startswith("+++")]
        # The ONLY lines that change are the rung ids and the separation rule
        # that names the moved rung; everything else is added, never rewritten.
        for line in removed:
            assert re.fullmatch(
                r"\s*-?\s*(model|built_on): claude-sonnet-(5|4-6)", line), line
        assert all(not l.lstrip().startswith("#") for l in removed)
        assert all("reason:" not in l for l in removed)
        assert sum(1 for l in added if re.fullmatch(r"\s*- model: claude-sonnet-6", l)) == 3
        # Every original comment line is still there, in order.
        comments = [l for l in before if l.lstrip().startswith("#")]
        assert comments == [l for l in after if l.lstrip().startswith("#")]

    def test_retired_gains_each_old_id_with_the_dated_reason(self, tmp_path, capsys):
        config, prices, snap = self._setup(tmp_path)
        assert self._apply(capsys, config, prices, snap)[0] == 0
        retired = yaml.safe_load(config.read_text())["retired"]
        by_id = {r["model"]: r["reason"] for r in retired}
        assert "claude-opus-4-8" in by_id, "the existing retired entry survives"
        for old in (SONNET5, SONNET46):
            assert by_id[old] == (
                f"replaced by {SONNET6} on {today()} — automated same-family "
                "adoption (DRE-3892)"
            )

    def test_the_result_passes_policy_and_the_separation_rule_moved(self, tmp_path,
                                                                    capsys,
                                                                    adopt_sonnet6):
        config, prices, snap = self._setup(tmp_path)
        assert self._apply(capsys, config, prices, snap)[0] == 0
        after = yaml.safe_load(config.read_text())
        assert mf.policy_errors(after, ma.load_prices(prices)) == []
        # Read off the committed config, never a literal (DRE-5138): a rule
        # naming a rung that left every ladder moves to the candidate, and a
        # rule naming a rung still on both ladders — Sonnet 5.5 since DRE-5116 —
        # still describes a real overlap and stays, or policy refuses the bare
        # overlap it leaves behind.
        on_a_ladder = {r["model"] for rungs in after["ladders"].values() for r in rungs}
        gone = {r["model"] for r in adopt_sonnet6["replaces"]} - on_a_ladder
        was = yaml.safe_load(CONFIG_PATH.read_text())["review_separation"]["rules"]
        assert any(r["built_on"] in gone for r in was), "a rule has to move"
        rules = after["review_separation"]["rules"]
        assert [r["built_on"] for r in rules] == [
            SONNET6 if r["built_on"] in gone else r["built_on"] for r in was]

    def test_sync_model_config_regenerates_both_mirrors(self, tmp_path, capsys):
        # A throwaway copy of the tree the generator writes into, so the
        # committed mirrors are never touched.
        tree = tmp_path / "tree"
        shutil.copytree(ROOT / "scripts", tree / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "config", tree / "config")
        shutil.copy(ROOT / "agents.yaml", tree / "agents.yaml")
        (tree / "config" / "model-prices.yaml").write_text(
            prices_text(claude_sonnet_6=(2, 10)))
        snap = write_json(tmp_path, "catalog.json", snapshot_with(*SUCCESSORS))
        rc, _ = self._apply(capsys, tree / "config" / "models.yaml",
                            tree / "config" / "model-prices.yaml", snap)
        assert rc == 0

        sync = [sys.executable, str(tree / "scripts" / "sync_model_config.py")]
        wrote = subprocess.run(sync, capture_output=True, text=True, cwd=tree)
        assert wrote.returncode == 0, wrote.stderr
        assert "wrote: scripts/model_fallback.py" in wrote.stdout
        # agents.yaml mirrors each agent's ladder TOP, so it is rewritten only
        # when the adoption moved a top — read off the two configs, never a
        # literal (DRE-5138: since DRE-5116 Sonnet 5 is no ladder's top).
        before = self._agent_tops(CONFIG_PATH)
        after = self._agent_tops(tree / "config" / "models.yaml")
        registry = {a["name"]: a["model"] for a in
                    yaml.safe_load((tree / "agents.yaml").read_text())["agents"]}
        mirrored = sorted(set(after) & set(registry))
        assert mirrored
        assert [registry[a] for a in mirrored] == [after[a] for a in mirrored]
        if before != after:
            assert "wrote: agents.yaml" in wrote.stdout
        else:
            assert "ok:    agents.yaml" in wrote.stdout
        check = subprocess.run(sync + ["--check"], capture_output=True, text=True,
                               cwd=tree)
        assert check.returncode == 0, check.stderr
        assert SONNET6 in (tree / "scripts" / "model_fallback.py").read_text()

    @staticmethod
    def _agent_tops(path) -> dict[str, str]:
        """`{agent: the top of its kind's ladder}` in a models.yaml, read
        through the generator's own normalizing loader."""
        cfg = sync_model_config.load_config(Path(path))
        return {agent: cfg["ladders"][cfg["kinds"].get(kind) or cfg["default_ladder"]][0]
                for agent, kind in cfg["agents"].items()}

    def test_check_prints_a_diff_and_writes_nothing(self, tmp_path, capsys):
        config, prices, snap = self._setup(tmp_path)
        before = config.read_text()
        rc, out = self._apply(capsys, config, prices, snap, SONNET6, "--check")
        assert rc == 0
        assert config.read_text() == before
        assert f"-    - model: {SONNET5}" in out.out
        assert f"+    - model: {SONNET6}" in out.out

    @pytest.mark.parametrize("candidate, sonnet6_price, rule", [
        ("claude-opus-4-7", (2, 10), "ignore"),
        (SONNET6, (3, 15), "ask"),
    ])
    def test_a_candidate_that_is_not_adopt_is_refused(self, tmp_path, capsys,
                                                      candidate, sonnet6_price, rule):
        config, prices, snap = self._setup(tmp_path, sonnet6_price=sonnet6_price)
        before = config.read_text()
        rc, out = self._apply(capsys, config, prices, snap, candidate)
        assert rc == 2
        assert config.read_text() == before
        assert rule in out.out

    def test_a_candidate_the_catalog_does_not_list_is_refused(self, tmp_path, capsys):
        config, prices, snap = self._setup(tmp_path)
        before = config.read_text()
        rc, _ = self._apply(capsys, config, prices, snap, "claude-sonnet-9")
        assert rc == 2
        assert config.read_text() == before

    def test_a_result_that_violates_policy_is_refused_and_nothing_written(
            self, tmp_path, capsys):
        # An effort level declared for the rung being replaced: after the edit
        # it names a model on no ladder, which `policy_errors` refuses.
        text = CONFIG_PATH.read_text().replace(
            "effort:\n  claude-opus-5-5: high\n",
            "effort:\n  claude-opus-5-5: high\n  claude-sonnet-5: high\n",
        )
        assert mf.policy_errors(yaml.safe_load(text)) == []
        config, prices, snap = self._setup(tmp_path, config_text=text)
        rc, out = self._apply(capsys, config, prices, snap)
        assert rc == 2
        assert config.read_text() == text
        assert "effort.claude-sonnet-5" in out.out

    def test_apply_text_is_a_text_edit_of_exactly_the_named_ladder(self,
                                                                  adopt_sonnet6):
        # Only the ladders the Decision names — never a `- model:` line under
        # `retired:` or `excluded:` that happens to share the id.
        decision = copy.deepcopy(adopt_sonnet6)
        decision["replaces"] = [r for r in decision["replaces"]
                                if r["ladder"] == "workhorse"]
        (old,) = [r["model"] for r in decision["replaces"]]
        before = yaml.safe_load(CONFIG_PATH.read_text())
        advisory = [r["model"] for r in before["ladders"]["advisory"]]
        assert old in advisory, "the workhorse rung is on the advisory ladder too"
        text = maa.apply_text(CONFIG_PATH.read_text(), decision, "2026-11-02")
        after = yaml.safe_load(text)
        # The advisory ladder is read off the committed config, never a literal
        # top (DRE-5138), and comes through untouched, rung for rung.
        assert [r["model"] for r in after["ladders"]["advisory"]] == advisory
        assert SONNET6 in [r["model"] for r in after["ladders"]["workhorse"]]
        assert old not in [r["model"] for r in after["ladders"]["workhorse"]]
        # The old id is still on the advisory ladder, so it is NOT retired.
        assert old not in [r["model"] for r in after["retired"]]


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
                                                       ask_new_family):
        body = render(capsys, "question-body",
                      write_json(tmp_path, "d.json", ask_new_family))
        first = body.strip().split("\n\n")[0]
        assert "$3.00" in first and "$15.00" in first
        # The ladder discovery lets a new model join, and its top rung is the
        # nearest thing we run there — both read off the committed config, never
        # a literal (DRE-5138): `claude-sonnet-5` stopped being the advisory top
        # at DRE-5116 and this line only passed by being a prefix of the new one.
        config = yaml.safe_load(CONFIG_PATH.read_text())
        ladder = config["discovery"]["on_new_model"]
        top = config["ladders"][ladder][0]["model"]
        price = ma.load_prices()[top]
        assert ladder in first
        assert f"The nearest model we run is {top}," in first
        assert f"${price['input']:.2f}" in first and f"${price['output']:.2f}" in first

    def test_no_declared_price_is_said_plainly(self, tmp_path, capsys, ask_no_price):
        body = render(capsys, "question-body",
                      write_json(tmp_path, "d.json", ask_no_price))
        first = body.strip().split("\n\n")[0]
        assert "no price" in first.lower()
        assert "$4.00" in first and "$20.00" in first


class TestPrAndRecordBodies:

    def test_pr_body_carries_the_trial_verbatim(self, tmp_path, capsys,
                                                adopt_sonnet6):
        body = render(capsys, "pr-body", write_json(tmp_path, "d.json", adopt_sonnet6),
                      "--trial", write_json(tmp_path, "t.json", TRIAL))
        assert TRIAL["run_url"] in body
        assert TRIAL["outcome"] in body
        assert TRIAL["summary"] in body

    def test_pr_body_carries_the_classification_evidence(self, tmp_path, capsys,
                                                         adopt_sonnet6):
        body = render(capsys, "pr-body", write_json(tmp_path, "d.json", adopt_sonnet6),
                      "--trial", write_json(tmp_path, "t.json", TRIAL))
        assert adopt_sonnet6["created_at"] in body
        for rung in adopt_sonnet6["replaces"]:
            assert rung["created_at"] in body
            assert rung["ladder"] in body
        assert "$2.00" in body and "$10.00" in body
        assert "$3.00" in body and "$15.00" in body
        assert "DRE-3892 rule 1" in body
        assert "critic and the merge gate are its review" in body

    def test_pr_body_never_carries_a_verdict_marker(self, tmp_path, capsys,
                                                    adopt_sonnet6):
        trial = dict(TRIAL, summary="VERDICT: APPROVE — QA Critic and QA Verifier")
        body = render(capsys, "pr-body", write_json(tmp_path, "d.json", adopt_sonnet6),
                      "--trial", write_json(tmp_path, "t.json", trial))
        for marker in VERDICT_MARKERS:
            assert marker not in body
        assert "APPROVE" in body, "the rest of the summary still arrives"

    def test_pr_body_without_a_trial_says_so(self, tmp_path, capsys, adopt_sonnet6):
        body = render(capsys, "pr-body", write_json(tmp_path, "d.json", adopt_sonnet6))
        assert "no trial result" in body.lower()

    def test_record_body_adds_the_branch_and_the_run_url(self, adopt_sonnet6):
        branch = maa.record_branch(CARD, SONNET6)
        body = maa.record_body(adopt_sonnet6, run_url=RUN_URL, branch=branch,
                               trial=TRIAL)
        assert branch in body and RUN_URL in body
        assert adopt_sonnet6["created_at"] in body
        assert TRIAL["summary"] in body
        for marker in VERDICT_MARKERS:
            assert marker not in body

    def test_record_body_renders_from_the_cli(self, tmp_path, capsys, adopt_sonnet6):
        body = render(capsys, "record-body",
                      write_json(tmp_path, "d.json", adopt_sonnet6),
                      "--trial", write_json(tmp_path, "t.json", TRIAL))
        assert adopt_sonnet6["created_at"] in body
        assert TRIAL["run_url"] in body


# --------------------------------------------------------------------------- #
# open-record-card / open-question-card — the Linear half, against a fake      #
# --------------------------------------------------------------------------- #

class TestOpenRecordCard:

    def test_files_the_record_in_progress_with_its_labels_and_verdict(
            self, adopt_sonnet6):
        ops = FakeOps()
        result = maa.open_record_card(adopt_sonnet6, run_url=RUN_URL, ops=ops)
        assert len(ops.created) == 1
        made = ops.created[0]
        assert made["lane"] == "In Progress"
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
        assert result["branch"] == f"agent/{CARD}-adopt-{SONNET6}"
        assert result["card_owed"] is False

    def test_the_record_passes_the_live_card_gate(self, adopt_sonnet6):
        ops = FakeOps()
        maa.open_record_card(adopt_sonnet6, run_url=RUN_URL, ops=ops)
        made = ops.created[0]
        assert validate_card.missing(made["description"], made["labels"]) == []
        assert validate_card.repo_title_mismatch(made["title"], made["labels"]) is None

    def test_the_record_ends_up_naming_its_branch_and_run(self, adopt_sonnet6):
        ops = FakeOps()
        maa.open_record_card(adopt_sonnet6, run_url=RUN_URL, ops=ops)
        final = ops.described[-1][1] if ops.described else ops.created[0]["description"]
        assert f"agent/{CARD}-adopt-{SONNET6}" in final
        assert RUN_URL in final

    def test_a_linear_failure_owes_the_card_and_falls_back(self, adopt_sonnet6):
        result = maa.open_record_card(adopt_sonnet6, run_url=RUN_URL,
                                      ops=FakeOps(fail_create=True))
        assert result["card"] == ""
        assert result["card_owed"] is True
        assert result["branch"] == f"agent/model-adoption-{SONNET6}"

    def test_a_failed_search_still_files_the_card(self, adopt_sonnet6):
        ops = FakeOps(fail_find=True)
        result = maa.open_record_card(adopt_sonnet6, run_url=RUN_URL, ops=ops)
        assert result["card"] == CARD and len(ops.created) == 1

    def test_a_stamp_failure_keeps_the_card(self, adopt_sonnet6):
        result = maa.open_record_card(adopt_sonnet6, run_url=RUN_URL,
                                      ops=FakeOps(fail_stamp=True))
        assert result["card"] == CARD
        assert result["branch"] == f"agent/{CARD}-adopt-{SONNET6}"

    def test_an_open_card_of_the_same_title_creates_nothing(self, adopt_sonnet6):
        ops = FakeOps(open_card="DRE-77")
        result = maa.open_record_card(adopt_sonnet6, run_url=RUN_URL, ops=ops)
        assert ops.created == [] and ops.stamped == []
        assert ("find_open", maa.record_title(adopt_sonnet6)) in ops.calls
        assert result["card"] == "DRE-77"
        assert result["branch"] == f"agent/DRE-77-adopt-{SONNET6}"

    def test_cli_prints_github_output_lines(self, tmp_path, capsys, adopt_sonnet6):
        rc = maa.main(["open-record-card", "--decision",
                       write_json(tmp_path, "d.json", adopt_sonnet6),
                       "--run-url", RUN_URL], ops=FakeOps())
        lines = capsys.readouterr().out.splitlines()
        assert rc == 0
        assert f"card={CARD}" in lines
        assert f"card_url=https://linear.app/x/issue/{CARD}" in lines
        assert f"branch=agent/{CARD}-adopt-{SONNET6}" in lines
        assert "card_owed=false" in lines

    def test_cli_on_a_linear_failure_exits_zero_and_owes_the_card(
            self, tmp_path, capsys, adopt_sonnet6):
        rc = maa.main(["open-record-card", "--decision",
                       write_json(tmp_path, "d.json", adopt_sonnet6),
                       "--run-url", RUN_URL], ops=FakeOps(fail_create=True))
        lines = capsys.readouterr().out.splitlines()
        assert rc == 0
        assert "card_owed=true" in lines
        assert f"branch=agent/model-adoption-{SONNET6}" in lines

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
        assert maa.RENDER_TARGETS == ("pr-title", "pr-body", "question-title",
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
