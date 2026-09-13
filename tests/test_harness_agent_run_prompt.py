"""RED-first tests: the harness's build agent gets its prompt on STDIN (DRE-3330).

`scripts/harness/agent_run.py` drives the SHIPPED `agent-task.yml` prompt
through the Claude Code CLI. It put that prompt in the argv list — the exact
shape DRE-3328 had just removed from `scripts/planning_classify.py`, still
standing one seam over.

That shape fails on a SIZE threshold, so it is invisible until the day the input
is big, and then it presents as

    [Errno 7] Argument list too long: 'npx'

which reads like a broken runner rather than a prompt that outgrew the argument
vector. The groomer hit it live on 2026-09-07 (run 34183475867): 260 Intake
cards, no judgement at all. Nothing about the harness's prompt is smaller by
nature — more context, a longer brief, a bigger seeded card and it walks into
the same wall.

The tests below copy DRE-3328's shape deliberately, because a small fixture
proves nothing here: it passes identically on the broken and the fixed code.
Every test drives a prompt PAST the kernel's limit, and one of them pins that
the OS really does refuse an argument that size on the machine running this
suite, so the others cannot pass vacuously.

Run: cd bureau-pipeline && python3 -m pytest tests/test_harness_agent_run_prompt.py -v
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from harness import agent_run  # noqa: E402

AGENT_TASK_YML = str(ROOT / agent_run.AGENT_TASK_WORKFLOW)
TOKEN = "x-access-token-fixture"  # nosec B105 — a fixture, never a credential

# Comfortably past both limits the kernels enforce: macOS's ~1 MiB total argv
# and Linux's 128 KiB cap on any SINGLE argument. The point of the number is
# that it is unambiguously over, not that it matches any real prompt.
_OVERSIZE_PROMPT_BYTES = 2 * 1024 * 1024

# A stand-in for the Claude Code CLI that is a REAL process: it reads its prompt
# off stdin and writes back what it received and what its own argv held. Mocking
# `subprocess.run` here would mock away the very thing under test — the exec.
# The report goes to a FILE because run_agent keeps only the last 2000 chars of
# stdout, and the whole point is to account for every byte.
_STUB_CLI = '''\
import hashlib, json, os, sys

prompt = sys.stdin.read()
with open(os.environ["HARNESS_STDIN_REPORT"], "w", encoding="utf-8") as f:
    json.dump({
        "stdin_bytes": len(prompt.encode("utf-8")),
        "stdin_sha": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "argv": sys.argv[1:],
    }, f)
print("stub cli done")
'''


def _oversize_card(size: int = _OVERSIZE_PROMPT_BYTES) -> agent_run.SeededCard:
    """A seeded card whose description alone pushes the assembled prompt past
    the argv limit — the growth vector the card names (more context, a larger
    census, a longer brief)."""
    line = "DRE-3330 the harness drives the shipped prompt through the CLI\n"
    body = (line * (size // len(line) + 1))[:size]
    return agent_run.SeededCard(
        identifier="DRE-3330",
        title="the prompt must not transit argv",
        description=body,
        url="https://linear.app/dreadnoughtfoundry/issue/DRE-3330",
    )


def _real_exec_runner(calls: list):
    """A runner that stubs the sandbox CLONE and EXECS everything else for real.

    The clone needs a network and a credential and is not what this file
    measures; the exec is exactly what it measures, so it is never faked.
    """

    def runner(argv, **kwargs):
        calls.append({"argv": list(argv), **kwargs})
        if argv[0] == "git":
            if argv[1] == "clone":
                os.makedirs(argv[-1], exist_ok=True)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return subprocess.run(argv, **kwargs)  # nosec B603 — argv list, shell=False

    return runner


def _recording_runner(calls: list):
    """A runner that records every call and execs nothing — for the cheap
    assertions about bounds, timeout and environment."""

    def runner(argv, **kwargs):
        calls.append({"argv": list(argv), **kwargs})
        if argv[0] == "git" and argv[1] == "clone":
            os.makedirs(argv[-1], exist_ok=True)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return runner


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """A stub CLI on `HARNESS_AGENT_CLI`, a pinned model so no probe runs, and
    a report path for the child to write to."""
    stub = tmp_path / "stub_cli.py"
    stub.write_text(_STUB_CLI, encoding="utf-8")
    report = tmp_path / "report.json"
    monkeypatch.setenv("HARNESS_AGENT_CLI", f"{sys.executable} {stub}")
    monkeypatch.setenv("HARNESS_AGENT_MODEL", "claude-opus-5")
    monkeypatch.setenv("HARNESS_STDIN_REPORT", str(report))
    return SimpleNamespace(report=report, workdir=str(tmp_path / "clone"))


class TestThePromptDoesNotTransitArgv:
    def test_the_os_really_refuses_a_prompt_this_size_in_argv(self):
        """The threshold is REAL on the machine running this suite, not assumed.

        Without this the tests below could pass on a box with no limit and the
        regression would sail through. This is the production failure in
        miniature — same errno, same message."""
        oversize = _oversize_card().description
        with pytest.raises(OSError) as caught:
            subprocess.run(  # nosec B603 — argv list, shell=False, never execs
                [sys.executable, "-c", "pass", oversize],
                capture_output=True,
            )
        assert caught.value.errno == errno.E2BIG
        assert "Argument list too long" in str(caught.value)

    def test_a_census_sized_prompt_reaches_a_real_cli_whole_on_stdin(self, sandbox):
        """The bug, closed end to end: a prompt the OS would refuse in argv is
        handed to a REAL child process, and every byte of it arrives."""
        card = _oversize_card()
        calls: list = []

        result = agent_run.run_agent(
            card,
            repo="dreadnought-foundry/bureau-harness",
            token=TOKEN,
            workdir=sandbox.workdir,
            pipeline_root=str(ROOT),
            runner=_real_exec_runner(calls),
            log=lambda *a, **k: None,
        )

        assert result.returncode == 0, "the child process started and exited clean"
        report = json.loads(sandbox.report.read_text(encoding="utf-8"))
        expected = agent_run.build_prompt(card, workflow_path=AGENT_TASK_YML)
        assert report["stdin_bytes"] == len(expected.encode("utf-8")), (
            "the whole prompt reached the process — not a truncated pipe"
        )
        assert report["stdin_sha"] == hashlib.sha256(
            expected.encode("utf-8")).hexdigest()
        assert not any(len(word) > 256 for word in report["argv"]), (
            "the child's OWN argv is the bounds and nothing else"
        )

    def test_the_argv_carries_the_bounds_and_not_the_prompt(self, sandbox):
        """`-p` is a boolean flag — print mode — and the CLI reads the prompt
        from stdin behind it. Everything the harness mirrors from
        agent-task.yml's `claude_args` stays in the argv."""
        card = _oversize_card()
        calls: list = []

        agent_run.run_agent(
            card,
            repo="dreadnought-foundry/bureau-harness",
            token=TOKEN,
            workdir=sandbox.workdir,
            pipeline_root=str(ROOT),
            runner=_recording_runner(calls),
            log=lambda *a, **k: None,
        )

        agent_call = calls[-1]
        argv = agent_call["argv"]
        assert "-p" in argv
        assert argv[argv.index("--max-turns") + 1] == str(agent_run.MAX_TURNS)
        assert argv[argv.index("--model") + 1] == "claude-opus-5"
        assert argv[argv.index("--allowedTools") + 1] == agent_run.ALLOWED_TOOLS
        # Nothing card-shaped can be in there: the argv is a fixed handful of
        # flags whose size does not move with the prompt.
        assert max(len(word) for word in argv) < 256
        assert card.description not in argv

    def test_the_prompt_is_the_childs_stdin(self, sandbox):
        """The prompt is not dropped on the way out of argv — it is the
        subprocess's `input`, which is what a pipe is at this seam."""
        card = _oversize_card()
        calls: list = []

        agent_run.run_agent(
            card,
            repo="dreadnought-foundry/bureau-harness",
            token=TOKEN,
            workdir=sandbox.workdir,
            pipeline_root=str(ROOT),
            runner=_recording_runner(calls),
            log=lambda *a, **k: None,
        )

        agent_call = calls[-1]
        assert agent_call["input"] == agent_run.build_prompt(
            card, workflow_path=AGENT_TASK_YML
        )
        assert "shell" not in agent_call, "no shell — the argv list is the call"

    def test_the_call_still_carries_its_wall_clock_cwd_and_credential(self, sandbox):
        """Moving the prompt must not quietly drop the timeout that turns a hung
        agent into a named scenario failure, the sandbox clone it runs in, or
        the token it pushes with."""
        calls: list = []

        agent_run.run_agent(
            _oversize_card(),
            repo="dreadnought-foundry/bureau-harness",
            token=TOKEN,
            workdir=sandbox.workdir,
            pipeline_root=str(ROOT),
            runner=_recording_runner(calls),
            log=lambda *a, **k: None,
        )

        agent_call = calls[-1]
        assert agent_call["timeout"] == agent_run.AGENT_TIMEOUT_SECONDS
        assert agent_call["cwd"] == sandbox.workdir
        assert agent_call["capture_output"] is True
        assert agent_call["text"] is True
        assert agent_call["env"]["GH_TOKEN"] == TOKEN
        assert agent_call["env"]["GITHUB_TOKEN"] == TOKEN
