"""A comment or description body reaches `linear_ops.py` from a file, never as one argument (DRE-8758).

THE INCIDENT (2026-11-01, 4:45 PM PT). The Backend epic of the Loops
initiative re-planned after the Loops mockup merged. The planner posted its
plan, then `Mechanical findings — posted before the critic reads them` in
`plan.yml` died with exit code 126 and neither critic ran. The job log says
why, on the step script's line 64:

    /usr/bin/python3: Argument list too long

The step posted its note with `linear_ops.py comment "$EPIC" "$(cat "$NOTE")"`,
which hands the whole note to the kernel as ONE argument. Linux refuses any
single argument over 128 KiB (MAX_ARG_STRLEN, 131,072 bytes) with E2BIG, and
bash reports that as 126. The note was about 138 KiB, rebuilt from the
lines the step printed: 461 "designed but no card carries it" surface lines
from `plan_critic.py mechanical` (about 61 KiB) and one line per row of the
199-row Loops inventory from `plan_inventory.py` (DRE-8612, about 72 KiB).
DRE-3328 and DRE-3330 met the same wall with prompts and moved them to stdin.

THE FIX. `comment` and `set-description` take `--body-file=PATH`, or
`--body-file=-` for stdin; the positional body keeps working. The file's
trailing newlines are dropped, exactly as `"$(cat FILE)"` dropped them, so a
body posted either way is byte-identical and every reader that matches a
comment by its body still finds it.

The CLI tests run the real script in a subprocess with `urlopen` replaced by
a stub installed through a `sitecustomize` on `PYTHONPATH` (the shape of
`tests/test_linear_read_only.py`), so what Linear would have been sent is
read off the HTTP layer, and nothing reaches Linear.
"""

import json
import os
import subprocess  # nosec B404 - runs this repo's own scripts, fixed argv
import sys
import textwrap

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
PLAN = os.path.join(ROOT, ".github", "workflows", "plan.yml")
sys.path.insert(0, SCRIPTS)

import pipeline_act  # noqa: E402

MECHANICAL_STEP = "Mechanical findings — posted before the critic reads them"

#: Over 200 KiB, as the card asks — and over the platform's whole ARG_MAX
#: too, so the old one-argument form fails on a Mac as it does on the Linux
#: runner (macOS has no per-argument cap, only the total).
try:
    _ARG_MAX = os.sysconf("SC_ARG_MAX")
except (AttributeError, ValueError, OSError):
    _ARG_MAX = 0
NOTE_BYTES = max(200 * 1024 + 1, _ARG_MAX + 1)

SITECUSTOMIZE = textwrap.dedent('''
    import json, os, urllib.request

    CARD = {
        "id": "issue-1", "identifier": "DRE-1", "title": "A card",
        "description": "the card's description",
        "team": {"id": "team-1", "key": "DRE"},
        "state": {"id": "s-progress", "name": "In Progress", "type": "started"},
        "labels": {"nodes": []},
        "children": {"nodes": []},
        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
        "history": {"nodes": []},
    }

    class _Resp:
        headers = {}

        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(req, timeout=None):
        payload = json.loads(req.data)
        with open(os.environ["STUB_LOG"], "a") as f:
            f.write(json.dumps(payload) + "\\n")
        q = payload["query"]
        if q.lstrip().startswith("mutation"):
            if os.environ.get("STUB_REFUSE_WRITES") == "1":
                return _Resp(json.dumps({"errors": [{"message": "refused by the stub"}]}).encode())
            data = {"commentCreate": {"success": True},
                    "issueUpdate": {"success": True}}
        else:
            data = {"issue": CARD, "viewer": {"id": "u1"}}
        return _Resp(json.dumps({"data": data}).encode())

    urllib.request.urlopen = urlopen
''')


def _env(tmp_path, **extra):
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    (stub / "sitecustomize.py").write_text(SITECUSTOMIZE)
    log = tmp_path / "requests.jsonl"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(stub),
        "LINEAR_API_KEY": "lin_api_test",
        "LINEAR_IDENTITY": "fleet",
        "GITHUB_ACTIONS": "true",
        "STUB_LOG": str(log),
    }
    env.update(extra)
    return env, log


def _sent(log):
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]


def _mutations(sent):
    return [p for p in sent if p["query"].lstrip().startswith("mutation")]


def _run_cli(tmp_path, *argv, stdin=None, **extra):
    env, log = _env(tmp_path, **extra)
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, os.path.join(SCRIPTS, "linear_ops.py"), *argv],
        input=stdin, capture_output=True, text=True, env=env, timeout=120,
        check=False,
    )
    return proc, _sent(log)


def _comment_body(mutation):
    return mutation["variables"]["input"]["body"]


def _big_note() -> str:
    line = ("- LE59 | Drawer: the waiting note 'Waiting on …' | `stateNote() .k-note.quiet` "
            "| M10 Notice | DRE-8701 → deferred — built by the Loops page\n")
    out = []
    size = 0
    while size < NOTE_BYTES:
        out.append(line)
        size += len(line.encode())
    return "Mechanical findings\n" + "".join(out)


# --- The workflow's own command form ----------------------------------------

def _mechanical_post_command() -> str:
    """The shell that posts the mechanical note, read out of plan.yml: from
    the LAST `linear_ops.py comment "$EPIC"` in the step to the step's end."""
    with open(PLAN, encoding="utf-8") as fh:
        jobs = yaml.safe_load(fh)["jobs"]
    runs = [step.get("run") or ""
            for job in jobs.values() for step in job.get("steps") or []
            if step.get("name") == MECHANICAL_STEP]
    assert len(runs) == 1, f"one step named {MECHANICAL_STEP!r} in plan.yml"
    lines = runs[0].splitlines()
    starts = [i for i, ln in enumerate(lines)
              if 'linear_ops.py comment "$EPIC"' in ln]
    assert starts, "the step posts its note through linear_ops.py comment"
    return "\n".join(lines[starts[-1]:]) + "\n"


def test_a_note_over_200_kib_posts_through_the_mechanical_steps_command(tmp_path):
    """The acceptance test: the step's own command, run by `bash -e` as the
    runner runs it, posts a note larger than any one argument may be."""
    note = tmp_path / "plan-mechanical.md"
    text = _big_note()
    note.write_text(text, encoding="utf-8")
    assert note.stat().st_size > 200 * 1024
    work = tmp_path / "work"
    work.mkdir()
    os.symlink(ROOT, work / ".bureau-pipeline")
    env, log = _env(tmp_path, EPIC="DRE-1", NOTE=str(note))
    proc = subprocess.run(  # nosec B603 B607 - the workflow's own shell
        ["bash", "-e", "-c", _mechanical_post_command()],
        cwd=work, capture_output=True, text=True, env=env, timeout=120,
        check=False,
    )
    assert "Argument list too long" not in proc.stderr, proc.stderr[-2000:]
    assert proc.returncode == 0, proc.stderr[-2000:]
    posted = _mutations(_sent(log))
    assert len(posted) == 1, "the note is posted once"
    assert _comment_body(posted[0]) == text.rstrip("\n")


def test_a_failed_post_of_the_note_does_not_fail_the_step(tmp_path):
    """The critic reads `$NOTE` from disk, so a note Linear refuses costs the
    epic its copy of the list, never the critics' run."""
    note = tmp_path / "plan-mechanical.md"
    note.write_text("Mechanical findings\n- one\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    os.symlink(ROOT, work / ".bureau-pipeline")
    env, log = _env(tmp_path, EPIC="DRE-1", NOTE=str(note), STUB_REFUSE_WRITES="1")
    proc = subprocess.run(  # nosec B603 B607 - the workflow's own shell
        ["bash", "-e", "-c", _mechanical_post_command()],
        cwd=work, capture_output=True, text=True, env=env, timeout=120,
        check=False,
    )
    assert _mutations(_sent(log)), "the post was attempted"
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "::warning" in proc.stdout, "a refused post is named on the run"


# --- The CLI ----------------------------------------------------------------

def test_comment_reads_its_body_from_a_file(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("line one\n\nline two\n\n\n", encoding="utf-8")
    proc, sent = _run_cli(tmp_path, "comment", "DRE-1", f"--body-file={body}")
    assert proc.returncode == 0, proc.stderr
    posted = _mutations(sent)
    assert len(posted) == 1
    # Trailing newlines dropped, exactly as "$(cat FILE)" dropped them.
    assert _comment_body(posted[0]) == "line one\n\nline two"


def test_comment_reads_its_body_from_stdin(tmp_path):
    proc, sent = _run_cli(tmp_path, "comment", "DRE-1", "--body-file=-",
                          stdin="from stdin\n")
    assert proc.returncode == 0, proc.stderr
    assert _comment_body(_mutations(sent)[0]) == "from stdin"


def test_the_positional_body_still_works(tmp_path):
    proc, sent = _run_cli(tmp_path, "comment", "DRE-1", "a short body")
    assert proc.returncode == 0, proc.stderr
    assert _comment_body(_mutations(sent)[0]) == "a short body"


def test_a_body_file_still_composes_its_act(tmp_path):
    body = tmp_path / "receipt.md"
    body.write_text("the epic waits its turn\n", encoding="utf-8")
    proc, sent = _run_cli(tmp_path, "comment", "DRE-1", f"--body-file={body}",
                          "--act=epic-approval-queued")
    assert proc.returncode == 0, proc.stderr
    assert _comment_body(_mutations(sent)[0]) == pipeline_act.receipt(
        "epic-approval-queued", "the epic waits its turn")


def test_a_missing_body_file_posts_nothing(tmp_path):
    proc, sent = _run_cli(tmp_path, "comment", "DRE-1",
                          f"--body-file={tmp_path / 'absent.md'}")
    assert proc.returncode != 0
    assert not _mutations(sent), "no empty comment goes out"


def test_a_body_given_twice_is_refused(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("one\n", encoding="utf-8")
    proc, sent = _run_cli(tmp_path, "comment", "DRE-1", "positional",
                          f"--body-file={body}")
    assert proc.returncode != 0
    assert not _mutations(sent)


def test_set_description_reads_its_body_from_a_file(tmp_path):
    body = tmp_path / "desc.md"
    text = "## Acceptance criteria\n- [ ] " + "x" * (NOTE_BYTES) + "\n"
    body.write_text(text, encoding="utf-8")
    proc, sent = _run_cli(tmp_path, "set-description", "DRE-1", f"--body-file={body}")
    assert proc.returncode == 0, proc.stderr
    posted = _mutations(sent)
    assert len(posted) == 1
    assert posted[0]["variables"]["input"]["description"] == text.rstrip("\n")


def test_set_description_keeps_its_positional_body(tmp_path):
    proc, sent = _run_cli(tmp_path, "set-description", "DRE-1", "a description")
    assert proc.returncode == 0, proc.stderr
    assert _mutations(sent)[0]["variables"]["input"]["description"] == "a description"
