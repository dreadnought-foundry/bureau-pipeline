"""RED-first: a change to the verify step that fails a replay case is not
merged (DRE-7974).

`.github/workflows/groom-replay-gate.yml` runs on every pull request. Its
`reach` job asks `groom_replay_gate.py reach` whether the change touches the
verify step; when it does and the gate set holds cards, `replay` calls this
repo's own `groomer.yml` over that set, and `judge` runs
`groom_replay_gate.py judge` over what the replay left behind. A case that
passes on `main` and fails on the change turns `judge` red, and the merge gate
merges only on a head whose every check run is green.

What this file holds shut:

  * the workflow's shape — the trigger with no `paths` filter, the two
    permissions and no job widening them, each job's command, condition,
    inputs, secrets and artifacts;
  * `groomer.yml`'s two concurrency groups: a replay queues in a group of its
    own, and every other run keeps `groomer` and `groomer-post` exactly;
  * the workflow's own `reach` and `judge` steps, run as written against a
    throwaway repository: a passing case that ends on Planning exits 1, and
    the same case ending on Cancel exits 0;
  * the ref the replay passes: `job_workflow_ref` on a pull request names
    `refs/pull/<n>/merge`, so `${{ github.ref }}` passes the client's
    `check-ref` and `${{ github.sha }}` would not;
  * on a `pull_request` run the door is never asked and no token is minted,
    so the problem search says `linear`; on `schedule` it says `door`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_replay_gate_wiring.py -q
"""
from __future__ import annotations

import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import bureau_read  # noqa: E402
import groom_lookups  # noqa: E402
import groom_replay  # noqa: E402
import groom_replay_cases as grc  # noqa: E402
import groom_replay_gate as gate  # noqa: E402
import groom_verify_agent as gva  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, door_env  # noqa: E402

GATE = "groom-replay-gate.yml"
GROOMER = "groomer.yml"
LOCAL_GROOMER = "./.github/workflows/groomer.yml"
SHORT_RUNS_ON = ("${{ fromJSON(vars.BUREAU_SHORT_RUNS_ON || vars.BUREAU_RUNS_ON "
                 "|| '[\"ubuntu-latest\"]') }}")

GROOM_GROUP = ("${{ inputs.replay != '' && format('groomer-replay-{0}', "
               "github.run_id) || 'groomer' }}")
POST_GROUP = ("${{ inputs.replay != '' && format('groomer-post-replay-{0}', "
              "github.run_id) || 'groomer-post' }}")
CONDITION = ("needs.reach.outputs.reached == 'true' && "
             "needs.reach.outputs.cases != '0'")
GATE_SET = "tests/fixtures/groom-replay-cases/gate-set.json"


# --------------------------------------------------------------------------
# reading the YAML
# --------------------------------------------------------------------------
def _load(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.is_file(), f"missing workflow {name}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    # YAML 1.1 reads the bare key `on` as the boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _jobs() -> dict:
    return _load(GATE)["jobs"]


def _step(job: dict, *, uses: str | None = None, run: str | None = None,
          artifact: str | None = None) -> dict:
    """The one step of `job` that uses `uses` (and downloads or uploads the
    artifact `artifact`), or whose `run` holds `run`."""
    found = []
    for step in job.get("steps") or []:
        if uses and str(step.get("uses") or "").startswith(uses):
            if artifact is None or (step.get("with") or {}).get("name") == artifact:
                found.append(step)
        elif run and run in str(step.get("run") or ""):
            found.append(step)
    assert len(found) == 1, f"{len(found)} steps match uses={uses} run={run} artifact={artifact}"
    return found[0]


def _argv(step: dict) -> list[str]:
    return shlex.split(" ".join(str(step["run"]).replace("\\\n", " ").split()))


def _flag(argv: list[str], flag: str) -> str:
    assert argv.count(flag) == 1, f"{flag} given {argv.count(flag)} times in {argv}"
    return argv[argv.index(flag) + 1]


# --------------------------------------------------------------------------
# GitHub's expression language — exactly the operators these workflows use
# --------------------------------------------------------------------------
_TOKEN = re.compile(r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<op>==|!=|&&|\|\||!|\(|\)|,)"
                    r"|(?P<num>\d+)|(?P<name>[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)*))")


def _tokens(text: str) -> list[tuple[str, str]]:
    text, out, pos = text.strip(), [], 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        assert match and match.end() > pos, f"cannot read {text[pos:]!r}"
        pos = match.end()
        out.append((match.lastgroup, match.group(match.lastgroup)))
    return out


def _truthy(value) -> bool:
    return value not in (False, None, "", 0)


def _text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _equal(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()     # GitHub compares strings ignoring case
    return a == b


def _format(fmt: str, *args) -> str:
    return re.sub(r"\{(\d+)\}", lambda m: _text(args[int(m.group(1))]), fmt)


#: The status functions as they read on a run nobody canceled.
_FUNCTIONS = {"format": _format, "cancelled": lambda: False,
              "always": lambda: True, "success": lambda: True}


class _Expression:
    """`||` yields the first truthy operand or the last, `&&` the first falsy
    or the last, and `==`/`!=` bind tighter than both. A context name the
    caller did not supply fails, so a misspelt name is never read as empty."""

    def __init__(self, text: str, context: dict, functions: dict | None = None):
        self.tokens, self.at, self.context = _tokens(text), 0, context
        self.functions = _FUNCTIONS if functions is None else functions

    def _peek(self) -> str | None:
        return self.tokens[self.at][1] if self.at < len(self.tokens) else None

    def _take(self, value: str | None = None) -> tuple[str, str]:
        assert self.at < len(self.tokens), "the expression ended early"
        token = self.tokens[self.at]
        assert value is None or token[1] == value, f"expected {value!r}, got {token[1]!r}"
        self.at += 1
        return token

    def value(self):
        out = self._or()
        assert self.at == len(self.tokens), f"left over: {self.tokens[self.at:]}"
        return out

    def _or(self):
        out = self._and()
        while self._peek() == "||":
            self._take()
            right = self._and()
            out = out if _truthy(out) else right
        return out

    def _and(self):
        out = self._compare()
        while self._peek() == "&&":
            self._take()
            right = self._compare()
            out = right if _truthy(out) else out
        return out

    def _compare(self):
        out = self._unary()
        while self._peek() in ("==", "!="):
            op = self._take()[1]
            same = _equal(out, self._unary())
            out = same if op == "==" else not same
        return out

    def _unary(self):
        if self._peek() == "!":
            self._take()
            return not _truthy(self._unary())
        return self._primary()

    def _primary(self):
        kind, value = self._take()
        if value == "(":
            out = self._or()
            self._take(")")
            return out
        if kind == "str":
            return value[1:-1].replace("''", "'")
        if kind == "num":
            return int(value)
        assert kind == "name", f"unexpected {value!r}"
        if value in ("true", "false"):
            return value == "true"
        if self._peek() == "(":
            self._take("(")
            args = []
            if self._peek() != ")":
                args.append(self._or())
                while self._peek() == ",":
                    self._take()
                    args.append(self._or())
            self._take(")")
            return self.functions[value](*args)
        assert value in self.context, f"no context value for {value}"
        return self.context[value]


def evaluate(text: str, context: dict):
    """An `if:` or a bare `${{ }}`: the wrapper is optional, as GitHub reads it."""
    inner = str(text).strip()
    if inner.startswith("${{") and inner.endswith("}}"):
        inner = inner[3:-2]
    return _Expression(inner, context).value()


_STATUS = {"success", "failure", "always", "cancelled"}


def job_runs(text: str, context: dict, *, needs_failed: bool,
             canceled: bool = False) -> bool:
    """Whether a job with this `if:` runs, as GitHub decides it: an `if:` that
    names no status function is read as `success() && (<if>)`, and `success()`
    is false once a job it needs has failed or the run is canceled."""
    inner = str(text).strip()
    if inner.startswith("${{") and inner.endswith("}}"):
        inner = inner[3:-2]
    if not {v for k, v in _tokens(inner) if k == "name"} & _STATUS:
        inner = f"success() && ({inner})"
    functions = {**_FUNCTIONS,
                 "success": lambda: not (needs_failed or canceled),
                 "failure": lambda: needs_failed,
                 "cancelled": lambda: canceled}
    return _truthy(_Expression(inner, context, functions).value())


def render(text: str, context: dict) -> str:
    """A string with `${{ }}` inside it, each rendered to text."""
    return re.sub(r"\$\{\{(.*?)\}\}",
                  lambda m: _text(_Expression(m.group(1), context).value()), str(text))


def test_the_evaluator_reads_the_operators_as_github_does():
    ctx = {"a": "", "b": "x", "n": 3}
    assert evaluate("a || 'dflt'", ctx) == "dflt"
    assert evaluate("b || 'dflt'", ctx) == "x"
    assert evaluate("a != '' && 'yes' || 'no'", ctx) == "no"
    assert evaluate("b != '' && 'yes' || 'no'", ctx) == "yes"
    assert evaluate("format('g-{0}', n)", ctx) == "g-3"
    assert evaluate("${{ !cancelled() && b == 'X' }}", ctx) is True
    with pytest.raises(AssertionError):
        evaluate("c == ''", ctx)


# --------------------------------------------------------------------------
# the trigger and the permissions
# --------------------------------------------------------------------------
def test_the_trigger_is_pull_request_opened_synchronize_reopened_with_no_paths():
    on = _on(_load(GATE))
    assert set(on) == {"pull_request"}, f"triggers: {sorted(on)}"
    trigger = on["pull_request"]
    assert sorted(trigger["types"]) == ["opened", "reopened", "synchronize"]
    # A paths filter creates no check run at all on the pull requests it
    # excludes (standards/engineering.md, CI rule 1): the job decides.
    assert set(trigger) == {"types"}, f"pull_request carries {sorted(trigger)}"


def test_the_permissions_are_exactly_contents_read_and_id_token_write():
    doc = _load(GATE)
    assert doc["permissions"] == {"contents": "read", "id-token": "write"}
    assert doc["permissions"] == _load("self-groomer.yml")["permissions"]
    for name, job in doc["jobs"].items():
        assert "permissions" not in job, f"job {name} carries its own permissions"


def test_a_new_push_cancels_its_own_stale_replay():
    assert _load(GATE)["concurrency"] == {
        "group": "groom-replay-gate-${{ github.event.pull_request.number }}",
        "cancel-in-progress": True}


def test_the_three_jobs_and_their_order():
    jobs = _jobs()
    assert list(jobs) == ["reach", "replay", "judge"]
    assert "needs" not in jobs["reach"]
    assert jobs["replay"]["needs"] == "reach"
    assert sorted(jobs["judge"]["needs"]) == ["reach", "replay"]


# --------------------------------------------------------------------------
# reach
# --------------------------------------------------------------------------
def test_reach_runs_the_reach_command_with_the_base_and_head_shas():
    job = _jobs()["reach"]
    # No `if`: the job runs on every pull request, so its check run exists.
    assert "if" not in job
    assert job["runs-on"] == SHORT_RUNS_ON
    checkout = _step(job, uses="actions/checkout@")
    # `git diff <base>...<head>` needs both commits and their merge base.
    assert (checkout.get("with") or {}).get("fetch-depth") == 0
    step = _step(job, run="groom_replay_gate.py reach")
    assert "${{" not in step["run"], "every expression goes through env"
    argv = _argv(step)
    assert argv[:4] == ["python3", "scripts/groom_replay_gate.py", "reach", "--base"]
    env = step["env"]
    base, head = _flag(argv, "--base"), _flag(argv, "--head")
    assert re.fullmatch(r"\$\w+", base) and re.fullmatch(r"\$\w+", head)
    assert env[base[1:]] == "${{ github.event.pull_request.base.sha }}"
    assert env[head[1:]] == "${{ github.event.pull_request.head.sha }}"
    assert "--gate-set" not in argv, "the gate set is the script's own default"
    assert job["outputs"] == {
        "reached": f"${{{{ steps.{step['id']}.outputs.reached }}}}",
        "cases": f"${{{{ steps.{step['id']}.outputs.cases }}}}"}


# --------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------
def test_replay_carries_the_condition_and_calls_the_local_groomer():
    job = _jobs()["replay"]
    assert job["if"] == CONDITION
    assert job["uses"] == LOCAL_GROOMER
    assert job["with"] == {
        "pipeline_ref": "${{ github.ref }}",
        "mode": "propose",
        "judgement": "off",
        "capacity": "20",
        "replay": GATE_SET,
        "intake_hold": "${{ vars.INTAKE_HOLD }}",
    }
    assert GATE_SET == grc.GATE_SET_PATH
    assert "card" not in job["with"], "a replay posts to no card"


def test_replay_passes_every_secret_the_stub_s_call_job_passes():
    call = _load("self-groomer.yml")["jobs"]["call"]
    job = _jobs()["replay"]
    assert job["secrets"] == call["secrets"] == "inherit"
    assert job["with"]["intake_hold"] == call["with"]["intake_hold"]


def test_replay_and_judge_run_only_when_reached_with_cases():
    jobs = _jobs()
    for reached, cases, runs in [("true", "3", True), ("true", "0", False),
                                 ("false", "3", False), ("", "", False)]:
        ctx = {"needs.reach.outputs.reached": reached,
               "needs.reach.outputs.cases": cases}
        assert _truthy(evaluate(jobs["replay"]["if"], ctx)) is runs
        assert _truthy(evaluate(jobs["judge"]["if"], ctx)) is runs


# --------------------------------------------------------------------------
# judge
# --------------------------------------------------------------------------
def test_judge_runs_on_the_same_condition_after_replay():
    job = _jobs()["judge"]
    assert CONDITION in job["if"]
    assert job["runs-on"] == SHORT_RUNS_ON


def test_judge_still_runs_after_a_failed_replay_and_replay_not_after_a_failed_reach():
    jobs = _jobs()
    ctx = {"needs.reach.outputs.reached": "true",
           "needs.reach.outputs.cases": "3"}
    # `judge` names a status function, so a replay that breaks partway still
    # gets judged and its cases read as not judged rather than going unsaid.
    assert job_runs(jobs["judge"]["if"], ctx, needs_failed=False) is True
    assert job_runs(jobs["judge"]["if"], ctx, needs_failed=True) is True
    # ...but never on a canceled run: a newer push replaced this one.
    assert job_runs(jobs["judge"]["if"], ctx, needs_failed=True, canceled=True) is False
    # `replay` names none, so GitHub's implicit `success()` skips it when
    # `reach` fails.
    assert job_runs(jobs["replay"]["if"], ctx, needs_failed=False) is True
    assert job_runs(jobs["replay"]["if"], ctx, needs_failed=True) is False


def test_judge_judges_the_downloaded_verified_proposal_with_its_targets():
    job = _jobs()["judge"]
    verified = _step(job, uses="actions/download-artifact@",
                     artifact="groom-proposal-verified")["with"]["path"]
    proposal = _step(job, uses="actions/download-artifact@",
                     artifact="groom-proposal")["with"]["path"]
    step = _step(job, run="groom_replay_gate.py judge")
    assert "${{" not in step["run"]
    assert not step.get("continue-on-error"), "judge exits as the job exits"
    argv = _argv(step)
    assert argv[:3] == ["python3", "scripts/groom_replay_gate.py", "judge"]
    assert _flag(argv, "--proposal") == f"{verified}/proposal-verified.json"
    assert _flag(argv, "--targets") == f"{proposal}/verify-targets.json"
    assert _flag(argv, "--out") == "result.json"
    assert "||" not in step["run"] and ";" not in step["run"]


def test_judge_keeps_its_result_as_groom_replay_gate_for_30_days():
    job = _jobs()["judge"]
    upload = _step(job, uses="actions/upload-artifact@", artifact="groom-replay-gate")
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "result.json"
    assert upload["with"]["retention-days"] == 30


def test_the_actions_are_pinned_where_groomer_yml_pins_them():
    pins = set()
    for job in _load(GROOMER)["jobs"].values():
        pins |= {s["uses"] for s in job.get("steps") or [] if s.get("uses")}
    for name, job in _jobs().items():
        for step in job.get("steps") or []:
            if step.get("uses"):
                assert step["uses"] in pins, f"{name}: {step['uses']}"


# --------------------------------------------------------------------------
# groomer.yml — a replay never queues with the morning's run
# --------------------------------------------------------------------------
@pytest.mark.parametrize("job,group,ordinary", [("groom", GROOM_GROUP, "groomer"),
                                                ("post", POST_GROUP, "groomer-post")])
def test_groomer_s_concurrency_groups(job, group, ordinary):
    concurrency = _load(GROOMER)["jobs"][job]["concurrency"]
    assert concurrency == {"group": group, "cancel-in-progress": False}
    ctx = {"inputs.replay": "", "github.run_id": "36426267250"}
    assert evaluate(group, ctx) == ordinary
    ctx["inputs.replay"] = GATE_SET
    replayed = evaluate(group, ctx)
    assert replayed == f"{ordinary}-replay-36426267250"
    assert replayed != ordinary


def test_two_replays_never_share_a_group_and_the_text_keeps_it_on_one_line():
    a = evaluate(GROOM_GROUP, {"inputs.replay": GATE_SET, "github.run_id": "1"})
    b = evaluate(GROOM_GROUP, {"inputs.replay": GATE_SET, "github.run_id": "2"})
    assert a != b
    text = (WORKFLOWS / GROOMER).read_text(encoding="utf-8")
    assert f"      group: {GROOM_GROUP}\n" in text
    assert f"      group: {POST_GROUP}\n" in text


# --------------------------------------------------------------------------
# the ref the client checks
# --------------------------------------------------------------------------
PR_CLAIMS = {"job_workflow_ref": ("dreadnought-foundry/bureau-pipeline/.github/"
                                  "workflows/groomer.yml@refs/pull/12/merge")}


def test_the_pull_request_ref_passes_check_ref():
    assert bureau_read.pipeline_ref_problem(PR_CLAIMS, "refs/pull/12/merge") is None


def test_the_merge_sha_would_have_failed_check_ref():
    problem = bureau_read.pipeline_ref_problem(PR_CLAIMS, "a" * 40)
    assert problem and "refs/pull/12/merge" in problem


# --------------------------------------------------------------------------
# the door is never asked on a pull request
# --------------------------------------------------------------------------
@pytest.fixture
def clean_door(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY",
                 "GITHUB_EVENT_NAME"):
        monkeypatch.delenv(name, raising=False)
    bureau_read.reset_for_tests()
    yield
    bureau_read.reset_for_tests()


class _Linear:
    """`linear_ops` as `cards` uses it: a key, the problem search answered
    from `found`, every other request answered empty."""

    def __init__(self, found=None):
        self.found = dict(found or {})
        self.searches = 0

    def api_key(self):
        return "lin-key"

    def gql(self, query, variables=None):
        variables = dict(variables or {})
        if "completedAt" in query:
            return {alias: None for alias in variables}
        if any(k.startswith("t") for k in variables):
            self.searches += 1
            return {alias: {"nodes": copy.deepcopy(self.found.get(term, []))}
                    for alias, term in variables.items()}
        return {alias: {"nodes": []} for alias in variables}


def _row():
    return {"card": "DRE-4049", "repository": "dreadnought-foundry/agent-bureau",
            "repo_slug": "agent-bureau", "title": "a stranded run holds the group",
            "body": "", "evidence": [], "list": "planning",
            "context": {"created_at": "2026-09-15T22:01:23.504Z", "age_days": 30},
            "excluded": None}


def _hit(ident):
    return {"identifier": ident, "title": f"{ident} title",
            "createdAt": "2026-10-06T02:49:26.858Z", "state": {"name": "Done"}}


def _point(monkeypatch, door, issuer, event):
    for key, value in door_env(door_url=door.url, issuer=issuer, mode="on").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)


def _answering(door):
    def answer(endpoint, query, headers):
        return 200, door.envelope([_hit("DRE-5942")])
    door.routes["/search"] = answer


def test_on_a_pull_request_the_door_is_refused_before_a_token_is_minted(
        monkeypatch, clean_door):
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer, "pull_request")
        _answering(door)
        with pytest.raises(bureau_read.ReadUnknown) as refused:
            bureau_read.search("stranded run", scope="fleet")
        assert refused.value.reason == "event-refused"
        assert issuer.requests == [], "a token was minted on a pull_request run"
        assert door.requests == []


def test_on_a_pull_request_the_problem_search_is_linear_s(monkeypatch, clean_door):
    row, lops = _row(), _Linear({"stranded run": [_hit("DRE-5942")]})
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer, "pull_request")
        _answering(door)
        groom_lookups.cards([row], lops=lops, now="2026-10-13T13:00:00Z")
        assert issuer.requests == [] and door.requests == []
    assert row["matched_work"]["searched"] is True
    assert row["matched_work"]["source"] == "linear"
    assert lops.searches == 1
    assert gate.searches([row]) == {"DRE-4049": "linear"}


def test_on_schedule_the_same_row_is_searched_through_the_door(monkeypatch, clean_door):
    row, lops = _row(), _Linear({"stranded run": [_hit("DRE-5942")]})
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer, "schedule")
        _answering(door)
        groom_lookups.cards([row], lops=lops, now="2026-10-13T13:00:00Z")
        assert issuer.requests and door.requests
    assert row["matched_work"]["source"] == "door"
    assert lops.searches == 0
    assert gate.searches([row]) == {"DRE-4049": "door"}


# --------------------------------------------------------------------------
# reach then judge, the way the workflow runs them
# --------------------------------------------------------------------------
def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=env).stdout.strip()


def _case(main_passes=True):
    return {"case": "c-planning", "card": "DRE-101", "proposal_id": "aaaa11112222",
            "standing_card": "DRE-4541", "list": "planning",
            "decision": "not-doing", "reason": "already-done",
            "reason_label": "Already done", "recorded_at": "2026-10-20T13:00:00Z",
            "source": "door", "expect": "off-planning", "title": "the DRE-101 card",
            "repo": "bureau-pipeline",
            "main": {"ended": "cancel", "at": "2026-10-19T15:00:00Z",
                     "passes": main_passes}}


@pytest.fixture(scope="module")
def checkout(tmp_path_factory):
    """A throwaway repository holding this repo's scripts, one case that
    passes on `main` and the gate set built from it; `base` commits it all,
    `head` changes one verify-step file."""
    root = tmp_path_factory.mktemp("gate") / "repo"
    shutil.copytree(ROOT / "scripts", root / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", root / "config")
    cases = [_case()]
    fixtures = root / Path(grc.CASES_PATH).parent
    fixtures.mkdir(parents=True)
    (root / grc.CASES_PATH).write_text(json.dumps(
        {"schema": grc.SCHEMA, "built_at": None, "cases": cases}, indent=2) + "\n",
        encoding="utf-8")
    (root / grc.GATE_SET_PATH).write_text(json.dumps(grc.gate_set(cases)),
                                          encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    with open(root / "scripts" / "groom_verify_agent.py", "a", encoding="utf-8") as fh:
        fh.write("\n# the change under review\n")
    _git(root, "commit", "-q", "-am", "change the verify step")
    return root, base, _git(root, "rev-parse", "HEAD")


def _run_step(step: dict, repo: Path, context: dict, extra_env=None):
    """A `run:` step as the runner runs it: its env rendered, bash -e -o
    pipefail, in the checkout."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    env.update({k: render(v, context) for k, v in (step.get("env") or {}).items()})
    env.update(extra_env or {})
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail",
                           "-c", step["run"]], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=120)


def _reach(repo, base, head, tmp_path):
    step = _step(_jobs()["reach"], run="groom_replay_gate.py reach")
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    done = _run_step(step, repo, {"github.event.pull_request.base.sha": base,
                                  "github.event.pull_request.head.sha": head},
                     {"GITHUB_OUTPUT": str(output)})
    assert done.returncode == 0, done.stderr
    written = dict(line.split("=", 1) for line in
                   output.read_text(encoding="utf-8").splitlines() if line)
    return written, done.stdout


def _verified(ended: str) -> dict:
    """A verified proposal where DRE-101 ends on `ended`."""
    row = {"identifier": "DRE-101", "title": "DRE-101", "position": 1,
           "repo": "bureau-pipeline", "reason": ""}
    outcomes = {"now": [], "not-now": [], "dead": []}
    if ended == groom_replay.PLANNING:
        row["verify"] = {"verdict": gva.STILL_NEEDED, "summary": "s",
                         "proof": [], "rested_on": []}
        outcomes["now"].append(row)
    else:
        row["verify"] = {"verdict": gva.DONE_ELSEWHERE, "summary": "s",
                         "proof": [], "rested_on": []}
        outcomes["dead"].append(row)
    doc = {"id": "replayed", "outcomes": outcomes, "sequence": [dict(row)]}
    assert groom_replay.ended(doc, "DRE-101") == ended
    return doc


def _judge(repo: Path, ended: str):
    """Lay the two artifacts where the job's downloads put them, then run the
    job's judge step as written."""
    job = _jobs()["judge"]
    verified = repo / _step(job, uses="actions/download-artifact@",
                            artifact="groom-proposal-verified")["with"]["path"]
    proposal = repo / _step(job, uses="actions/download-artifact@",
                            artifact="groom-proposal")["with"]["path"]
    for folder in (verified, proposal):
        folder.mkdir(parents=True, exist_ok=True)
    (verified / "proposal-verified.json").write_text(json.dumps(_verified(ended)),
                                                     encoding="utf-8")
    (proposal / "verify-targets.json").write_text(json.dumps([
        {"card": "DRE-101", "list": "planning",
         "matched_work": {"searched": True, "source": "linear", "why": None,
                          "terms": [], "cards": []}}]), encoding="utf-8")
    result = repo / "result.json"
    if result.exists():
        result.unlink()
    done = _run_step(_step(job, run="groom_replay_gate.py judge"), repo, {})
    return done, json.loads(result.read_text(encoding="utf-8"))


@pytest.mark.parametrize("ended,code", [(groom_replay.PLANNING, 1),
                                        (groom_replay.CANCEL, 0)])
def test_reach_then_judge_holds_a_regressed_case_and_passes_a_kept_one(
        checkout, tmp_path, ended, code):
    repo, base, head = checkout
    written, said = _reach(repo, base, head, tmp_path)
    assert written == {"reached": "true", "cases": "1"}
    assert said.startswith(f"{gate.TAG} reach true cards=1 — scripts/groom_verify_agent.py")
    ctx = {f"needs.reach.outputs.{k}": v for k, v in written.items()}
    jobs = _jobs()
    assert _truthy(evaluate(jobs["replay"]["if"], ctx))
    assert _truthy(evaluate(jobs["judge"]["if"], ctx))
    done, result = _judge(repo, ended)
    assert done.returncode == code, done.stdout + done.stderr
    line = next(x for x in done.stdout.splitlines()
                if x.startswith(f"{gate.TAG} c-planning "))
    assert f"head={ended} search=linear" in line
    assert result["regressed"] == (["c-planning"] if code else [])


def test_reach_on_a_change_that_misses_the_verify_step_skips_the_replay(
        checkout, tmp_path):
    repo, _, head = checkout
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "note.md").write_text("a note\n", encoding="utf-8")
    _git(repo, "add", "docs/note.md")
    _git(repo, "commit", "-q", "-m", "a doc")
    later = _git(repo, "rev-parse", "HEAD")
    try:
        written, _ = _reach(repo, head, later, tmp_path)
    finally:
        _git(repo, "reset", "-q", "--hard", head)
    assert written == {"reached": "false", "cases": "1"}
    ctx = {f"needs.reach.outputs.{k}": v for k, v in written.items()}
    jobs = _jobs()
    assert not _truthy(evaluate(jobs["replay"]["if"], ctx))
    assert not _truthy(evaluate(jobs["judge"]["if"], ctx))


# --------------------------------------------------------------------------
# the proof may read the gate's line
# --------------------------------------------------------------------------
def test_the_proof_may_read_the_gate_s_line():
    doc = json.loads((ROOT / "config" / "proof-reach.json").read_text(encoding="utf-8"))
    entries = [e for e in doc["log_lines"] if e["tag"] == gate.TAG]
    assert entries == [{
        "tag": "groom-replay-gate:", "printed_by": "scripts/groom_replay_gate.py",
        "constant": "TAG",
        "when": ("every run of groom-replay-gate.yml's reach and judge jobs, and of "
                 "groom-week.yml's week job, since this card")}]


# --------------------------------------------------------------------------
# failing closed (DRE-8741) — the run that let a broken verify step through
# --------------------------------------------------------------------------
def _groomer_jobs() -> dict:
    return _load(GROOMER)["jobs"]


def _named(job: dict, name: str) -> dict:
    found = [s for s in job.get("steps") or [] if s.get("name") == name]
    assert len(found) == 1, f"{len(found)} steps named {name!r}"
    return found[0]


SEED = "DRE-7239"


def _seed_cases(tmp_path) -> Path:
    """The seed passing on `main` beside a case that never ran there — the
    shape of the shipped file on the day of gate run 41370789518."""
    seed = dict(grc.SEED_CASES[0], main={"ended": "cancel", "at": "2026-11-01T10:14:53Z",
                                         "passes": True})
    other = dict(_case(main_passes=None), case="c-other", card="DRE-109")
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"schema": grc.SCHEMA, "built_at": None,
                                "cases": [seed, other]}, indent=2) + "\n",
                    encoding="utf-8")
    return path


def test_a_leg_that_crashed_on_startup_fails_the_gate_naming_the_reason(
        tmp_path, monkeypatch):
    """Gate run 41370789518, end to end: the gate set as the cases build it,
    the morning's `targets`, the verify leg's `prepare` and `Write the
    verdict` steps as written with the agent step's outcome `failure` and no
    execution file, the post job's `Apply the verdicts` as written, then
    `judge` — red, naming the case and `agent step failed`."""
    from test_groom_verify_agent import CardText
    monkeypatch.setenv("GH_TOKEN", "app-installation-token")
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / ".bureau-pipeline").symlink_to(ROOT)
    cases_path = _seed_cases(tmp_path)
    stored = grc.gate_set(grc._load_cases(cases_path)["cases"])
    (ws / "proposal.json").write_text(json.dumps(groom_replay.base(stored)),
                                      encoding="utf-8")
    assert gva.main(["targets", "--proposal", str(ws / "proposal.json"),
                     "--out", str(ws / "verify-targets.json"),
                     "--matrix-out", str(ws / "matrix.json")], lops=CardText()) == 0
    rows = json.loads((ws / "matrix.json").read_text(encoding="utf-8"))
    assert [r["card"] for r in rows] == [SEED]
    ctx = {"matrix.card": SEED, "matrix.repository": rows[0]["repository"],
           "steps.claude.outcome": "failure",
           "steps.claude.outputs.execution_file": "",
           "runner.temp": str(tmp_path / "runner-temp")}
    verify = _groomer_jobs()["verify"]
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    for name in ("Prepare the agent's input", "Write the verdict"):
        done = _run_step(_named(verify, name), ws, ctx, {"GITHUB_OUTPUT": str(output)})
        assert done.returncode == 0, done.stdout + done.stderr
    assert not Path(render(_named(verify, "Write the verdict")["env"]["EXECUTION_FILE"],
                           ctx)).exists()
    verdict = json.loads((ws / "verdict.json").read_text(encoding="utf-8"))
    assert (verdict["verdict"], verdict["reason"]) == (gva.UNVERIFIED, gva.STEP_FAILED)
    leg = ws / "verdicts" / f"groom-verdict-{SEED}"
    leg.mkdir(parents=True)
    (ws / "verdict.json").rename(leg / "verdict.json")
    done = _run_step(_named(_groomer_jobs()["post"], "Apply the verdicts"), ws, {})
    assert done.returncode == 0, done.stdout + done.stderr

    verified = json.loads((ws / "proposal-verified.json").read_text(encoding="utf-8"))
    assert groom_replay.ended(verified, SEED) == groom_replay.NOT_VERIFIED
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "groom_replay_gate.py"), "judge",
         "--proposal", str(ws / "proposal-verified.json"),
         "--cases", str(cases_path), "--out", str(tmp_path / "result.json")],
        capture_output=True, text=True, timeout=120)
    assert done.returncode != 0, done.stdout
    assert done.returncode == 1, done.stdout + done.stderr
    line = next(x for x in done.stdout.splitlines()
                if x.startswith(f"{gate.TAG} seed-DRE-7239 "))
    assert line.endswith("not judged — UNCHECKED: unverified (agent step failed)"), line
    assert " pass" not in line.split(" head=")[1]
    assert (f"{gate.TAG} failed closed — 1 case passes on main and was not checked: "
            f"seed-DRE-7239 ({SEED}, unverified (agent step failed))") in done.stdout
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["unchecked"] == ["seed-DRE-7239"] and result["checked"] == 0


@pytest.mark.parametrize("ended,code", [(groom_replay.PLANNING, 1),
                                        (groom_replay.CANCEL, 0)])
def test_a_real_answer_reads_as_before(checkout, ended, code):
    """Failing closed changes nothing for a leg that answered: off Planning
    passes and exits 0, on Planning is REGRESSED and exits 1."""
    repo, _, _ = checkout
    done, result = _judge(repo, ended)
    assert done.returncode == code, done.stdout + done.stderr
    assert "failed closed" not in done.stdout
    assert result["unchecked"] == [] and result["checked"] == 1


# --------------------------------------------------------------------------
# the verify agent runs from a git working directory (DRE-8741)
# --------------------------------------------------------------------------
GIT_STEP = "Make the workspace root a git repository"


def _verify_steps() -> list[dict]:
    return _groomer_jobs()["verify"]["steps"]


def test_the_workspace_root_is_made_a_repository_after_every_checkout_and_before_the_agent():
    steps = _verify_steps()
    names = [s.get("name") for s in steps]
    at = names.index(GIT_STEP)
    agent = next(i for i, s in enumerate(steps) if s.get("id") == "claude")
    checkouts = [i for i, s in enumerate(steps)
                 if str(s.get("uses") or "").startswith("actions/checkout@")]
    assert checkouts and max(checkouts) < at < agent
    step = steps[at]
    assert "uses" not in step
    # Never a checkout at the root: that would clean the downloaded proposal
    # away, and put a second copy of a repository beside `target/`.
    for s in steps:
        assert (s.get("with") or {}).get("path") not in (None, "", ".", "./") \
            or not str(s.get("uses") or "").startswith("actions/checkout@"), s
    assert step["if"] == steps[agent]["if"]
    assert step.get("continue-on-error") is True


def _git_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_", "GITHUB_"))}
    env.update({"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CEILING_DIRECTORIES": str(home.parent)})
    return env


def _action_startup(ws: Path, origin: str, env: dict) -> list[subprocess.CompletedProcess]:
    """The git calls claude-code-action v1.0.236 makes in its working
    directory on a `pull_request` event before the CLI starts
    (`configureGitAuth`, then `restoreConfigFromBase` — the
    `git diff … HEAD` that failed on gate run 41370789518 is its first), the
    origin URL rewritten as the action rewrites it."""
    def git(*args, check=True):
        done = subprocess.run(["git", *args], cwd=ws, env=env, capture_output=True,
                              text=True, timeout=120)
        assert not check or done.returncode == 0, (args, done.stdout, done.stderr)
        return done

    git("config", "user.name", "claude[bot]")
    git("remote", "set-url", "origin", origin)
    out = [git("diff", "--name-only", "-z", "--relative", "--ignore-submodules",
               "HEAD", "--"),
           git("ls-files", "-z")]
    shallow = git("rev-parse", "--is-shallow-repository").stdout.strip()
    out.append(git("fetch", "origin", "main",
                   *(["--depth=1"] if shallow == "true" else []),
                   "--no-recurse-submodules"))
    for path in (".claude", ".mcp.json", "CLAUDE.md"):
        git("checkout", "origin/main", "--", path, check=False)
    git("reset", "--", ".claude", ".mcp.json", "CLAUDE.md", check=False)
    out.append(shallow)
    return out


@pytest.fixture
def base_repo(tmp_path):
    """What the action's `origin` serves: a repository with a `main`."""
    env = _git_env(tmp_path / "home")
    (tmp_path / "home").mkdir()
    origin = tmp_path / "origin"
    origin.mkdir()
    for args in (["init", "-q", "-b", "main"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                  "--allow-empty", "-m", "one"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                  "--allow-empty", "-m", "two"]):
        subprocess.run(["git", *args], cwd=origin, env=env, check=True,
                       capture_output=True)
    return origin, env


def _workspace(tmp_path) -> Path:
    """The verify leg's workspace root as the job leaves it: the pipeline
    and the card's repo checked out under it, the agent's input at the top."""
    ws = tmp_path / "work" / "bureau-pipeline"
    for sub in (".bureau-pipeline", "target", "pipeline"):
        (ws / sub).mkdir(parents=True)
    (ws / "verify-input.md").write_text("the input\n", encoding="utf-8")
    (ws / "verify-targets.json").write_text("[]\n", encoding="utf-8")
    return ws


def test_without_the_step_the_action_s_first_git_call_fails_as_on_the_gate_run(
        tmp_path, base_repo):
    """The control: the layout as it was, and the error the run printed."""
    _, env = base_repo
    ws = _workspace(tmp_path)
    done = subprocess.run(["git", "diff", "--name-only", "-z", "--relative",
                           "--ignore-submodules", "HEAD", "--"], cwd=ws, env=env,
                          capture_output=True, text=True)
    assert done.returncode != 0
    assert "Not a git repository" in done.stderr


def test_after_the_step_the_action_s_startup_git_calls_all_succeed(tmp_path, base_repo):
    origin, env = base_repo
    ws = _workspace(tmp_path)
    step = _named(_groomer_jobs()["verify"], GIT_STEP)
    run_env = {**env, "GITHUB_SERVER_URL": "https://github.com",
               "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline"}
    done = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c",
                           step["run"]], cwd=ws, env=run_env, capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ws, env=env,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert Path(top).resolve() == ws.resolve()
    url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ws, env=env,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert url == "https://github.com/dreadnought-foundry/bureau-pipeline.git"

    diff, files, fetched, shallow = _action_startup(ws, f"file://{origin}", env)
    assert diff.stdout == "" and files.stdout == ""
    # Shallow, so the action fetches the base branch at depth 1 rather than
    # its whole history, once per leg.
    assert shallow == "true"
    # The agent's input and the two checkouts are where the prompt says.
    assert (ws / "verify-input.md").is_file()
    for sub in ("target", "pipeline", ".bureau-pipeline"):
        assert (ws / sub).is_dir()


def test_the_step_can_run_twice_on_one_workspace(tmp_path, base_repo):
    """A self-hosted runner may keep the workspace between jobs."""
    _, env = base_repo
    ws = _workspace(tmp_path)
    step = _named(_groomer_jobs()["verify"], GIT_STEP)
    run_env = {**env, "GITHUB_SERVER_URL": "https://github.com",
               "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline"}
    for _ in range(2):
        done = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c",
                               step["run"]], cwd=ws, env=run_env, capture_output=True,
                              text=True, timeout=60)
        assert done.returncode == 0, done.stdout + done.stderr


def test_the_agent_still_reads_target_and_pipeline_from_the_workspace_root():
    agent = next(s for s in _verify_steps() if s.get("id") == "claude")
    prompt = agent["with"]["prompt"]
    assert "verify-input.md at the workspace root" in prompt
    assert "checked out under target/" in prompt
    assert "under pipeline/ beside it" in prompt
    paths = {(s.get("with") or {}).get("path") for s in _verify_steps()
             if str(s.get("uses") or "").startswith("actions/checkout@")}
    assert paths == {".bureau-pipeline", "target", "pipeline"}


# --------------------------------------------------------------------------
# the header says what the code does (DRE-8741)
# --------------------------------------------------------------------------
def _premortem(question: str) -> str:
    text = (WORKFLOWS / GATE).read_text(encoding="utf-8")
    header = "\n".join(line[2:] if line.startswith("# ") else line.lstrip("#")
                       for line in text.splitlines() if line.startswith("#"))
    match = re.search(rf"^\s*{question} — (.*?)(?=^\s*Q\d — |^\S|\Z)", header,
                      re.S | re.M)
    assert match, f"no {question} paragraph"
    return " ".join(match.group(1).split())


def test_q5_says_the_gate_fails_closed():
    q5 = _premortem("Q5")
    assert "the leg's job is red" not in q5
    assert "never a pass" in q5
    for words in ("`unverified`", "fails the gate", "names the case",
                  "no case was checked", "`continue-on-error`"):
        assert words in q5, f"Q5 does not say {words!r}: {q5}"


def test_the_judge_job_comment_says_a_crashed_leg_fails_it():
    text = (WORKFLOWS / GATE).read_text(encoding="utf-8")
    block = text[text.index("  # The verdict."):text.index("  judge:")]
    assert "judged `not judged` here rather than left" not in block
    assert "fails" in block and "unchecked" in block.lower()
