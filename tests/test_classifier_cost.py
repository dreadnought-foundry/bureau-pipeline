"""RED-first: the card sorter's receipt says what its call cost, and a tally adds them up (DRE-7196).

The planning classifier deliberately runs on the planner's models, and nothing recorded what that costs: the
receipt named the model only, and the `total_cost_usd` the Claude Code CLI reports on every result envelope was
parsed and thrown away. A trial of a cheaper sorter model is owed only above $10 a day, so the number has to be
readable off the receipts rather than estimated.

  A. The classifier's `receipt` output ends ` · cost $<d.dddd>` with the envelope's `total_cost_usd`; a call that
     stepped down the ladder sums the attempts that returned an envelope; a call with no readable cost ends
     ` · cost unknown`, never `$0.0000`.
  B. On the raw API path the cost is computed from the response's usage tokens and `config/model-prices.yaml`.
  C. The clause is opt-in: the groomer's and the groom judgement's receipts are byte-identical to before.
  D. `model_fallback.last_attempt_model` reads the same model id with the clause as without it.
  E. `scripts/classifier_cost.py` counts the classifier receipts inside a window, sums the costed ones, counts the
     uncosted ones separately, and prints one `classifier-cost:` line.

No model id here is a literal: a priced id is read off the committed prices file and an attributable one off
`model_fallback.KNOWN_MODELS`, and the ladder a fall walks is stood in for (DRE-6866).

Run: cd bureau-pipeline && python3 -m pytest tests/test_classifier_cost.py -v
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import classifier_cost  # noqa: E402
import groom_judgement  # noqa: E402
import groomer  # noqa: E402
import model_fallback  # noqa: E402
import planning_classify  # noqa: E402

PRICES = ROOT / "config" / "model-prices.yaml"
TOP, BELOW = "top-rung-under-test", "rung-below-under-test"
HEARTBEAT_TAIL = " — planning classifier read the card."


def _card() -> dict:
    return {"identifier": "DRE-7196", "title": "the sorter's receipt carries its cost",
            "description": "Change one file.", "labels": ["repo:bureau-pipeline"], "has_children": False}


def _one_off() -> str:
    return json.dumps({"shape": "one-off", "why": "one file, one pull request, and no decision in it",
                       "tells": [1, 2], "decision": False})


def _subscription_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")


def _api_key_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _success(model: str, **extra) -> str:
    doc = {"type": "result", "subtype": "success", "is_error": False, "result": _one_off(),
           "modelUsage": {model: {"inputTokens": 10, "outputTokens": 5}}}
    doc.update(extra)
    return json.dumps(doc)


def _no_answer(**extra) -> str:
    """The envelope a rung that gave no answer within its one turn writes (DRE-5979) — it falls."""
    doc = {"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 2, "result": None}
    doc.update(extra)
    return json.dumps(doc)


def _cli_by_model(monkeypatch, stdout_for: dict):
    """The Claude Code CLI, answering each `--model` with its own stdout."""
    seen: list[str] = []

    def run(argv, **kwargs):
        model = argv[argv.index("--model") + 1]
        seen.append(model)
        return _Done(returncode=0, stdout=stdout_for[model])

    monkeypatch.setattr(planning_classify.subprocess, "run", run)
    return seen


def _ladder_falls_to_below(monkeypatch):
    monkeypatch.setattr(planning_classify.model_fallback, "fallback_for",
                        lambda role, model, repo=None: BELOW if model == TOP else None)


def _receipt(decision) -> str:
    return dict(planning_classify._model_pairs(decision))["receipt"]


# --------------------------------------------------------------------------- #
# A. the classifier's receipt carries the cost                                 #
# --------------------------------------------------------------------------- #

def test_the_cli_envelopes_cost_ends_the_receipt(monkeypatch):
    _subscription_env(monkeypatch)
    _cli_by_model(monkeypatch, {TOP: _success(TOP, total_cost_usd=0.0123456)})
    decision = planning_classify.classify(_card(), model=TOP)
    assert decision.answered and decision.shape == "one-off"
    receipt = _receipt(decision)
    assert receipt == f"{TOP} (asked) / {TOP} (answered) · cost $0.0123"


def test_the_cli_transport_keeps_the_envelopes_cost_on_its_answer(monkeypatch):
    _subscription_env(monkeypatch)
    _cli_by_model(monkeypatch, {TOP: _success(TOP, total_cost_usd=0.5)})
    assert planning_classify._call_claude_code(TOP, "the prompt").cost_usd == 0.5


def test_a_call_that_stepped_down_the_ladder_sums_its_attempts(monkeypatch):
    _subscription_env(monkeypatch)
    _ladder_falls_to_below(monkeypatch)
    seen = _cli_by_model(monkeypatch, {
        TOP: _no_answer(total_cost_usd=0.0101),
        BELOW: _success(BELOW, total_cost_usd=0.0202),
    })
    decision = planning_classify.classify(_card(), model=TOP)
    assert seen == [TOP, BELOW]
    receipt = _receipt(decision)
    assert receipt.startswith("DEGRADED "), receipt
    assert receipt.endswith(" · cost $0.0303"), receipt


def test_an_attempt_that_returned_no_envelope_adds_nothing_to_the_sum(monkeypatch):
    """A refusal that never produced an envelope (a 429 on the raw path) billed nothing we can read."""
    def call(model, prompt):
        if model == TOP:
            raise planning_classify.TransportError("HTTP 429: overloaded", "HTTP 429")
        return planning_classify.Answer(text=_one_off(), model=model, cost_usd=0.04)

    monkeypatch.setattr(planning_classify.model_fallback, "capacity_refusal",
                        lambda message, record=None: "overloaded" if "429" in message else None)
    _ladder_falls_to_below(monkeypatch)
    decision = planning_classify.classify(_card(), call=call, model=TOP)
    assert decision.model == BELOW
    assert _receipt(decision).endswith(" · cost $0.0400")


def test_an_envelope_with_no_readable_cost_makes_the_whole_sum_unknown(monkeypatch):
    """Half a sum printed as the total would understate the day."""
    _subscription_env(monkeypatch)
    _ladder_falls_to_below(monkeypatch)
    _cli_by_model(monkeypatch, {
        TOP: _no_answer(),
        BELOW: _success(BELOW, total_cost_usd=0.0202),
    })
    receipt = _receipt(planning_classify.classify(_card(), model=TOP))
    assert receipt.endswith(" · cost unknown"), receipt
    assert "$" not in receipt


def test_a_call_with_no_readable_cost_says_so_rather_than_zero(monkeypatch):
    _subscription_env(monkeypatch)
    _cli_by_model(monkeypatch, {TOP: _success(TOP)})
    receipt = _receipt(planning_classify.classify(_card(), model=TOP))
    assert receipt == f"{TOP} (asked) / {TOP} (answered) · cost unknown"


@pytest.mark.parametrize("unreadable", ["0.01", None, True, "n/a", -1])
def test_a_cost_the_envelope_wrote_in_a_shape_we_do_not_read_is_unknown(monkeypatch, unreadable):
    _subscription_env(monkeypatch)
    _cli_by_model(monkeypatch, {TOP: _success(TOP, total_cost_usd=unreadable)})
    assert _receipt(planning_classify.classify(_card(), model=TOP)).endswith(" · cost unknown")


def test_a_plain_string_call_seam_is_uncosted():
    decision = planning_classify.classify(_card(), call=lambda model, prompt: _one_off(), model=TOP)
    assert _receipt(decision).endswith(" · cost unknown")


def test_the_clause_comes_after_the_reason_a_rung_was_left(monkeypatch):
    _subscription_env(monkeypatch)
    _ladder_falls_to_below(monkeypatch)
    _cli_by_model(monkeypatch, {TOP: _no_answer(total_cost_usd=0.001),
                                BELOW: _success(BELOW, total_cost_usd=0.002)})
    receipt = _receipt(planning_classify.classify(_card(), model=TOP))
    assert receipt.endswith(f" — {TOP} {planning_classify.NO_ANSWER} · cost $0.0030"), receipt


def test_the_written_step_output_carries_the_clause(monkeypatch, tmp_path):
    out = tmp_path / "github-output"
    monkeypatch.setattr(planning_classify, "run", lambda lops, ident: planning_classify.Decision(
        shape="one-off", why="w", answered=True, model=TOP, asked=TOP, cost_usd=1.23456))
    assert planning_classify._cmd_classify("DRE-7196", str(out), None) == 0
    written = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").strip().splitlines())
    assert written["receipt"] == f"{TOP} (asked) / {TOP} (answered) · cost $1.2346"


LINEAR_OPS_STUB = """import json, os, sys
with open(os.environ["CAPTURE"], "w", encoding="utf-8") as fh:
    json.dump(sys.argv[1:], fh)
"""


def _heartbeat_step() -> tuple[dict, str]:
    """plan.yml's classifier heartbeat, and the id of the step whose receipt it posts."""
    steps = yaml.safe_load((ROOT / ".github" / "workflows" / "plan.yml").read_text(encoding="utf-8"))["jobs"]["plan"]["steps"]
    heartbeat = next(step for step in steps if HEARTBEAT_TAIL.strip(" —.") in json.dumps(step, ensure_ascii=False))
    classify_id = next(step["id"] for step in steps if "planning_classify.py" in (step.get("run") or ""))
    return heartbeat, classify_id


def _post_heartbeat(receipt: str, tmp_path) -> list[str]:
    """Run the heartbeat step as the runner does — expressions substituted into the
    script text first, then `bash -e <file>` — and return what linear_ops.py was handed."""
    step, classify_id = _heartbeat_step()
    known = {f"steps.{classify_id}.outputs.receipt": receipt,
             "github.event.client_payload.identifier": "DRE-7196"}

    def expand(text: str) -> str:
        def one(match):
            expr = match.group(1).strip()
            if expr.startswith("secrets."):
                return "test-key"
            assert expr in known, f"an expression this test does not expand: {expr}"
            return known[expr]
        return re.sub(r"\$\{\{(.*?)\}\}", one, text)

    scripts = tmp_path / ".bureau-pipeline" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "linear_ops.py").write_text(LINEAR_OPS_STUB, encoding="utf-8")
    script = tmp_path / "step.sh"
    script.write_text(expand(step["run"]), encoding="utf-8")
    capture = tmp_path / "argv.json"
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "CAPTURE": str(capture)}
    env.update({name: expand(str(value)) for name, value in (step.get("env") or {}).items()})
    proc = subprocess.run(["bash", "-e", str(script)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(capture.read_text(encoding="utf-8"))


@pytest.mark.parametrize("cost", ["0.0123", "1.2346"])
def test_the_posted_heartbeat_keeps_the_cost_the_tally_reads(tmp_path, cost):
    """The seam workflow → comment → tally: a `$0`/`$1` the shell expands would
    post a mangled figure, and every receipt would count as uncosted."""
    receipt = f"{TOP} (asked) / {TOP} (answered) · cost ${cost}"
    argv = _post_heartbeat(receipt, tmp_path)
    assert argv[:2] == ["comment", "DRE-7196"]
    body = argv[2]
    assert body == f"🧠 model-attempt: {receipt}{HEARTBEAT_TAIL}"
    assert classifier_cost.is_receipt(body)
    assert classifier_cost.receipt_cost(body) == pytest.approx(float(cost))


def test_the_heartbeat_reads_the_receipt_from_its_env_not_its_script():
    """An output spliced into `run:` is script text — the receipt reaches bash as data."""
    step, classify_id = _heartbeat_step()
    assert f"steps.{classify_id}.outputs.receipt" not in step["run"]
    assert step["env"]["RECEIPT"] == f"${{{{ steps.{classify_id}.outputs.receipt }}}}"


# --------------------------------------------------------------------------- #
# B. the raw API path prices its usage                                          #
# --------------------------------------------------------------------------- #

def _priced_model() -> tuple[str, dict]:
    prices = yaml.safe_load(PRICES.read_text(encoding="utf-8"))["prices"]
    model_id = sorted(prices)[0]
    return model_id, prices[model_id]


def _api_answer(monkeypatch, body: dict):
    import urllib.request

    class _Resp:
        def read(self):
            return json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, *a, **k: _Resp())


def test_the_api_path_prices_its_usage_off_the_declared_prices(monkeypatch):
    _api_key_env(monkeypatch)
    model_id, price = _priced_model()
    _api_answer(monkeypatch, {"model": model_id, "content": [{"type": "text", "text": _one_off()}],
                              "usage": {"input_tokens": 12000, "output_tokens": 800}})
    answer = planning_classify._call_api(model_id, "the prompt")
    expected = (12000 * float(price["input"]) + 800 * float(price["output"])) / 1_000_000
    assert answer.cost_usd == pytest.approx(expected)
    receipt = planning_classify.model_receipt(model_id, model_id, cost=answer.cost_usd)
    assert receipt.endswith(f" · cost ${expected:.4f}")


def test_an_api_answer_from_an_undeclared_model_is_uncosted(monkeypatch):
    """Price is never guessed: no declared price, no cost."""
    _api_key_env(monkeypatch)
    _api_answer(monkeypatch, {"model": "a-model-no-file-prices", "content": [{"type": "text", "text": "x"}],
                              "usage": {"input_tokens": 12000, "output_tokens": 800}})
    assert planning_classify._call_api("a-model-no-file-prices", "the prompt").cost_usd is None


def test_an_api_answer_with_no_usage_is_uncosted(monkeypatch):
    _api_key_env(monkeypatch)
    model_id, _ = _priced_model()
    _api_answer(monkeypatch, {"model": model_id, "content": [{"type": "text", "text": "x"}]})
    assert planning_classify._call_api(model_id, "the prompt").cost_usd is None


def test_an_api_answer_with_cache_tokens_is_uncosted(monkeypatch):
    """The prices file declares no cache price, so a call that read or wrote the cache cannot be priced."""
    _api_key_env(monkeypatch)
    model_id, _ = _priced_model()
    _api_answer(monkeypatch, {"model": model_id, "content": [{"type": "text", "text": "x"}],
                              "usage": {"input_tokens": 10, "output_tokens": 5,
                                        "cache_read_input_tokens": 4000}})
    assert planning_classify._call_api(model_id, "the prompt").cost_usd is None


# --------------------------------------------------------------------------- #
# C. the clause is opt-in                                                      #
# --------------------------------------------------------------------------- #

def test_model_receipt_without_the_keyword_is_unchanged():
    assert planning_classify.model_receipt(TOP, TOP) == f"{TOP} (asked) / {TOP} (answered)"
    assert planning_classify.model_receipt(TOP, BELOW, because="overloaded") == (
        f"DEGRADED {TOP} (asked) / {BELOW} (answered) — {TOP} out of capacity (overloaded)")


def test_the_groom_judgements_receipt_is_byte_identical():
    judgement = groom_judgement.Judgement(calls=1, asked=TOP, answered=BELOW)
    assert judgement.receipt == f"DEGRADED {TOP} (asked) / {BELOW} (answered)"
    assert groom_judgement.Judgement(calls=1, asked=TOP, answered=TOP).receipt == (
        f"{TOP} (asked) / {TOP} (answered)")


def test_the_groomers_receipt_is_byte_identical():
    proposal = {"outcomes": {"now": [], "not-now": [], "dead": []}, "sequence": []}
    judgement = groom_judgement.Judgement(calls=1, asked=TOP, answered=TOP)
    block = groomer._annotate(proposal, judgement, None)
    assert block["receipt"] == f"{TOP} (asked) / {TOP} (answered)"


# --------------------------------------------------------------------------- #
# D. the readers of `model-attempt:` lines read the same model                 #
# --------------------------------------------------------------------------- #

def test_last_attempt_model_reads_the_same_id_with_and_without_the_clause():
    model = sorted(model_fallback.KNOWN_MODELS)[0]
    bare = planning_classify.model_receipt(model, model)
    for cost in (0.0123, None):
        costed = planning_classify.model_receipt(model, model, cost=cost)
        assert costed != bare
        with_clause = model_fallback.last_attempt_model([f"🧠 model-attempt: {costed}{HEARTBEAT_TAIL}"])
        without = model_fallback.last_attempt_model([f"🧠 model-attempt: {bare}{HEARTBEAT_TAIL}"])
        assert with_clause == without == model


# --------------------------------------------------------------------------- #
# E. the tally                                                                 #
# --------------------------------------------------------------------------- #

SINCE, UNTIL = "2026-10-12T00:00:00Z", "2026-10-13T00:00:00Z"


def _heartbeat(receipt: str) -> str:
    return f"🧠 model-attempt: {receipt}{HEARTBEAT_TAIL}"


def _fixture_comments() -> list[dict]:
    costed = planning_classify.model_receipt(TOP, TOP, cost=0.0125)
    fell = planning_classify.model_receipt(TOP, BELOW, because=planning_classify.NO_ANSWER, cost=0.5)
    return [
        {"createdAt": "2026-10-12T01:00:00.000Z", "body": _heartbeat(costed)},
        {"createdAt": "2026-10-12T09:30:00.000Z", "body": _heartbeat(fell)},
        # Uncosted: the classifier could not read a cost, and one written before the clause existed.
        {"createdAt": "2026-10-12T10:00:00.000Z",
         "body": _heartbeat(planning_classify.model_receipt(TOP, TOP, cost=None))},
        {"createdAt": "2026-10-12T11:00:00.000Z", "body": _heartbeat(planning_classify.model_receipt(TOP, TOP))},
        # Outside the window, at both ends — the end is exclusive, so a day never counts twice.
        {"createdAt": "2026-10-11T23:59:59.000Z", "body": _heartbeat(costed)},
        {"createdAt": "2026-10-13T00:00:00.000Z", "body": _heartbeat(costed)},
        # Not classifier receipts: another model-attempt, and a comment that quotes one.
        {"createdAt": "2026-10-12T12:00:00.000Z",
         "body": f"🧠 model-attempt: {TOP} — engineer agent starting · cost $9.0000"},
        {"createdAt": "2026-10-12T12:30:00.000Z",
         "body": f"Seen above: {_heartbeat(costed)}"},
    ]


def _stub_linear(monkeypatch, nodes: list[dict]):
    calls: list[dict] = []

    def gql_paged(query, variables=None, *, connection="issues"):
        calls.append({"query": query, "variables": dict(variables or {}), "connection": connection})
        return list(nodes)

    monkeypatch.setattr(classifier_cost.linear_ops, "gql_paged", gql_paged)
    return calls


def test_the_tag_is_the_module_constant():
    assert classifier_cost.TAG == "classifier-cost:"


def test_the_tally_counts_only_classifier_receipts_inside_the_window(monkeypatch, capsys):
    _stub_linear(monkeypatch, _fixture_comments())
    assert classifier_cost.main(["--since", SINCE, "--until", UNTIL]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines == [f"classifier-cost: receipts=4 uncosted=2 total_usd=0.51 since={SINCE} until={UNTIL}"]


def test_the_tally_line_opens_with_the_tag(monkeypatch, capsys):
    _stub_linear(monkeypatch, [])
    assert classifier_cost.main(["--since", SINCE, "--until", UNTIL]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == [f"{classifier_cost.TAG} receipts=0 uncosted=0 total_usd=0.00 since={SINCE} until={UNTIL}"]


def test_the_tally_reads_linear_once_filtered_by_window_and_body(monkeypatch, capsys):
    calls = _stub_linear(monkeypatch, [])
    classifier_cost.main(["--since", SINCE, "--until", UNTIL])
    assert len(calls) == 1
    call = calls[0]
    assert call["connection"] == "comments"
    assert "$after" in call["query"] and "pageInfo" in call["query"]
    assert "createdAt" in call["query"]
    assert "🧠 model-attempt:" in call["query"]
    assert "planning classifier read the card" in call["query"]
    assert set(call["variables"].values()) >= {SINCE, UNTIL}


def test_a_window_that_does_not_parse_is_refused(monkeypatch, capsys):
    calls = _stub_linear(monkeypatch, [])
    with pytest.raises(SystemExit) as raised:
        classifier_cost.main(["--since", "yesterday", "--until", UNTIL])
    assert raised.value.code != 0
    assert calls == []


def test_a_failed_linear_read_prints_no_tally(monkeypatch, capsys):
    """A zero tally on a read that failed would say the sorter cost nothing."""
    def gql_paged(query, variables=None, *, connection="issues"):
        raise classifier_cost.linear_ops.LinearError("rate limited")

    monkeypatch.setattr(classifier_cost.linear_ops, "gql_paged", gql_paged)
    assert classifier_cost.main(["--since", SINCE, "--until", UNTIL]) != 0
    captured = capsys.readouterr()
    assert classifier_cost.TAG not in captured.out
    assert "rate limited" in captured.err
