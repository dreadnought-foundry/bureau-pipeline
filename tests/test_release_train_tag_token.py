"""The release tag is pushed with the worker App's token (DRE-5949).

On 2026-10-05 portico's train (run 37405864904) deployed both accounts and
read them back live on 4a083a97 at 20:26 PT, and then GitHub refused the tag:
`! [remote rejected] portico-portals-v1.0.112 (refusing to allow a GitHub App
to create or update workflow .github/workflows/ci.yml without workflows
permission)`. The `release` job's checkout of the caller stored the run's
`github.token` as its git credential, the surface script's `git push` of the
tag used it, and GitHub never grants that token the `workflows` scope — so any
tag whose range since the last tag changes a `.github/workflows/` file is
refused. No tag meant no release-state record and no What's new, and the board
read "stuck".

The fix this file pins, and nothing more:

* `BUREAU_APP_ID` and `BUREAU_APP_PRIVATE_KEY` are declared, both optional —
  a stub that passes nothing hands the train empty strings and keeps today's
  path exactly.
* The `release` job, and only it, mints the worker App's installation token
  as its first step, guarded by the id's presence off a job-level `env` that
  mirrors the id and never the key, and a failed mint is a warning and a fall
  back, never a stopped train (DRE-3263).
* The App token reaches the push only through the caller checkout's stored
  credential. Every `GH_TOKEN` stays `github.token`, so the decision record's
  creator stays `github-actions[bot]` and the train never re-triggers itself.

What it cannot show: GitHub's refusal of a `github.token` push over a range
that changes a workflow file. That is the vendor's behavior, read off the run
above, and the observation that the App's push is accepted is a reading after
merge (DRE-3075), owed by its own card.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "release-train.yml"
STANDARD = ROOT / "standards" / "release-train.md"

MINT = "actions/create-github-app-token"
CHECKOUT = "actions/checkout"
CALLER_TOKEN = "${{ steps.app.outputs.token || github.token }}"
WARNING_IF = "env.BUREAU_APP_ID != '' && steps.app.outcome != 'success'"


def _doc():
    return yaml.safe_load(WORKFLOW.read_text())


def _on(doc):
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _jobs():
    return _doc()["jobs"]


def _steps(job):
    return _jobs()[job]["steps"]


def _uses(step, action):
    return str(step.get("uses", "")).startswith(action + "@")


def _mints(job):
    return [i for i, s in enumerate(_steps(job)) if _uses(s, MINT)]


def _checkouts(job):
    return [i for i, s in enumerate(_steps(job)) if _uses(s, CHECKOUT)]


def _env_blocks(doc):
    """Every `env:` mapping in the file: workflow, job and step level."""
    blocks = []
    if isinstance(doc.get("env"), dict):
        blocks.append(("workflow", doc["env"]))
    for name, job in doc["jobs"].items():
        if isinstance(job.get("env"), dict):
            blocks.append((name, job["env"]))
        for step in job.get("steps", []):
            if isinstance(step.get("env"), dict):
                blocks.append((f"{name}/{step.get('name', step.get('uses'))}",
                               step["env"]))
    return blocks


# --------------------------------------------------------------------------
# The secrets: the pair is optional, the role is still the one required.
# --------------------------------------------------------------------------

def test_the_app_pair_is_declared_and_optional():
    secrets = _on(_doc())["workflow_call"]["secrets"]
    assert secrets["BUREAU_APP_ID"]["required"] is False
    assert secrets["BUREAU_APP_PRIVATE_KEY"]["required"] is False


def test_the_release_role_is_still_the_only_required_secret():
    secrets = _on(_doc())["workflow_call"]["secrets"]
    required = sorted(k for k, v in secrets.items() if v.get("required"))
    assert required == ["RELEASE_ROLE_ARN"], (
        "a stub that passes only RELEASE_ROLE_ARN must still start the train"
    )


# --------------------------------------------------------------------------
# The mint: one step, in the release job only, first, pinned, guarded.
# --------------------------------------------------------------------------

def test_the_release_job_mints_exactly_one_token():
    assert len(_mints("release")) == 1


def test_no_other_job_mints_a_token():
    for name in _jobs():
        if name != "release":
            assert _mints(name) == [], f"{name} must keep github.token"


def test_the_mint_is_the_release_jobs_first_step_before_its_checkouts():
    (mint,) = _mints("release")
    assert mint == 0, (
        "the token lives one hour and the job's bound is sixty minutes — "
        "minted first, no push the job makes outlives it"
    )
    assert len(_checkouts("release")) == 2
    assert all(mint < c for c in _checkouts("release"))


def test_the_mint_is_named_and_carries_the_id_the_checkout_reads():
    step = _steps("release")[_mints("release")[0]]
    assert step["name"] == "Mint the tag-push token"
    assert step["id"] == "app"


def test_the_mint_is_pinned_to_the_sha_every_other_workflow_carries():
    step = _steps("release")[_mints("release")[0]]
    elsewhere = set()
    for path in WORKFLOWS.glob("*.yml"):
        if path == WORKFLOW:
            continue
        elsewhere |= set(re.findall(
            rf"uses:\s*({re.escape(MINT)}@[0-9a-f]{{40}} # v[\w.]+)",
            path.read_text()))
    assert len(elsewhere) == 1, f"the repo's mint pins disagree: {elsewhere}"
    line = next(l for l in WORKFLOW.read_text().splitlines()
                if f"uses: {MINT}@" in l)
    assert line.split("uses:", 1)[1].strip() == next(iter(elsewhere))
    assert step["uses"] == next(iter(elsewhere)).split(" #")[0]


def test_the_pin_check_passes_on_the_train():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_action_pins.py"),
         str(WORKFLOW)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_mint_is_fed_by_the_two_secrets():
    step = _steps("release")[_mints("release")[0]]
    assert step["with"] == {
        "app-id": "${{ secrets.BUREAU_APP_ID }}",
        "private-key": "${{ secrets.BUREAU_APP_PRIVATE_KEY }}",
    }


def test_the_mint_is_skipped_when_no_id_was_passed_never_failed():
    step = _steps("release")[_mints("release")[0]]
    assert step["if"] == "env.BUREAU_APP_ID != ''"
    assert _jobs()["release"]["env"]["BUREAU_APP_ID"] == (
        "${{ secrets.BUREAU_APP_ID }}"
    ), "the secrets context is not readable in a step `if:` — the job mirrors it"


def test_a_failed_mint_never_stops_the_train():
    step = _steps("release")[_mints("release")[0]]
    assert step["continue-on-error"] is True


def test_a_passed_pair_that_did_not_mint_is_one_warning():
    steps = _steps("release")
    (mint,) = _mints("release")
    warns = [i for i, s in enumerate(steps)
             if "::warning::" in str(s.get("run", ""))]
    assert len(warns) == 1
    (warn,) = warns
    assert warn > mint
    step = steps[warn]
    # `outcome`, not `conclusion`: under continue-on-error a failed step
    # CONCLUDES success, and only its outcome says it failed.
    assert step["if"] == WARNING_IF
    assert step["run"].count("::warning::") == 1


def test_the_private_key_is_in_no_env_block_anywhere():
    blocks = _env_blocks(_doc())
    assert blocks, "the walk found no env blocks — it is reading nothing"
    for where, env in blocks:
        for key, value in env.items():
            assert "BUREAU_APP_PRIVATE_KEY" not in str(key), where
            assert "BUREAU_APP_PRIVATE_KEY" not in str(value), where
    # Belt and braces over the text: the key is named only where it is
    # declared and where the mint reads it.
    text = WORKFLOW.read_text()
    body = text.split("\non:", 1)[1]
    assert body.count("secrets.BUREAU_APP_PRIVATE_KEY") == 1


# --------------------------------------------------------------------------
# The credential: only the caller checkout of the release job stores it.
# --------------------------------------------------------------------------

def _caller_and_internal(job):
    caller, internal = [], []
    for i in _checkouts(job):
        step = _steps(job)[i]
        with_ = step.get("with") or {}
        if with_.get("repository") == "dreadnought-foundry/bureau-pipeline":
            internal.append(step)
        else:
            caller.append(step)
    return caller, internal


def test_the_release_jobs_caller_checkout_stores_the_app_token():
    (caller,), (internal,) = _caller_and_internal("release")
    assert caller["with"]["token"] == CALLER_TOKEN, (
        "the checkout's stored credential is the only one the surface "
        "script's `git push` can use"
    )
    assert caller["with"]["ref"] == "${{ matrix.sha }}"
    assert caller["with"]["fetch-depth"] == 0
    assert "token" not in internal["with"]


def test_no_checkout_in_plan_or_wait_carries_a_token():
    for job in ("plan", "wait"):
        assert _checkouts(job), job
        for i in _checkouts(job):
            assert "token" not in (_steps(job)[i].get("with") or {}), job


def test_the_app_token_reaches_nothing_but_the_caller_checkout():
    text = WORKFLOW.read_text()
    assert text.count("steps.app.outputs.token") == 1
    assert CALLER_TOKEN in text


def test_every_gh_token_is_the_runs_own_token():
    seen = 0
    for where, env in _env_blocks(_doc()):
        if "GH_TOKEN" in env:
            seen += 1
            assert env["GH_TOKEN"] == "${{ github.token }}", where
    assert seen >= 2, "the plan and the release step both carry GH_TOKEN"


def test_the_tagger_is_unchanged():
    step = next(s for s in _steps("release") if s.get("name") == "Name the tagger")
    assert step["run"] == (
        'git config user.name "github-actions[bot]"\n'
        'git config user.email '
        '"41898282+github-actions[bot]@users.noreply.github.com"\n'
    )


def test_the_surface_step_is_handed_no_app_credential():
    step = next(s for s in _steps("release") if s.get("name") == "Run the surface")
    env = yaml.dump(step["env"])
    assert "steps.app" not in env
    assert "BUREAU_APP" not in env


# --------------------------------------------------------------------------
# The documents: the header premortem and the standard name the pair.
# --------------------------------------------------------------------------

def test_the_header_premortem_q2_names_the_pair():
    header = WORKFLOW.read_text().split("\nname:", 1)[0]
    q2 = header.split("Q2 WHICH SECRETS STORE", 1)[1].split("Q3 ", 1)[0]
    assert "BUREAU_APP_ID" in q2
    assert "BUREAU_APP_PRIVATE_KEY" in q2


def _reference_stub():
    blocks = re.findall(r"```yaml\n(.*?)```", STANDARD.read_text(), re.S)
    for block in blocks:
        if "release-train.yml@stable" in block:
            return yaml.safe_load(block)
    raise AssertionError("standards/release-train.md carries no stub block")


def test_the_reference_stub_passes_the_pair():
    job = next(iter(_reference_stub()["jobs"].values()))
    assert job["secrets"]["BUREAU_APP_ID"] == "${{ secrets.BUREAU_APP_ID }}"
    assert job["secrets"]["BUREAU_APP_PRIVATE_KEY"] == (
        "${{ secrets.BUREAU_APP_PRIVATE_KEY }}"
    )
    assert "secrets: inherit" not in STANDARD.read_text()


def _secrets_paragraph():
    body = STANDARD.read_text()
    start = body.index("`RELEASE_ROLE_ARN` is the one required secret")
    return body[start:body.index("\n## ", start)]


def test_the_standard_names_the_pair_optional_and_what_it_buys():
    text = " ".join(_secrets_paragraph().split())
    assert "`BUREAU_APP_ID`" in text and "`BUREAU_APP_PRIVATE_KEY`" in text
    assert "optional" in text
    assert ".github/workflows/" in text
    assert "`github.token`" in text


def test_the_standard_says_an_app_pushed_tag_fires_push_and_create():
    text = " ".join(_secrets_paragraph().split())
    assert "`push`" in text and "`create`" in text
