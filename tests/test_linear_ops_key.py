"""`linear_ops` finds the operator's key in Secrets Manager outside CI (DRE-4455).

`mid_epic.py`, `routing_verdict.py` and every other script on `linear_ops`
required `LINEAR_API_KEY` in the environment. On the operator's machine that
meant copying it out of a `.env` that goes stale on rotation and that an
assistant session is refused permission to read — so a card an assistant filed
needed the CEO to run a script by hand.

What these cases pin:

  * a key in the environment is used exactly as before, and AWS is never asked;
  * outside CI, with no key, it reads Secrets Manager
    `bureau/operator-tools/linear-api-key` — the operator-tools user, never the
    fleet's — once per process;
  * INSIDE GitHub Actions nothing changes: no key is the same `KeyError` it has
    always been, and AWS is never asked. There `LINEAR_API_KEY` is the fleet's,
    and borrowing the operator's would sign fleet work as the wrong user and
    spend the wrong budget (DRE-3168);
  * the request `gql` sends carries whichever key was resolved.

agent-bureau's `scripts/linear_key.py` resolves the same way (DRE-4454).
No network: `subprocess.run` and `urlopen` are stubbed.
"""

from __future__ import annotations

import ast
import io
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import linear_ops  # noqa: E402

OPERATOR_SECRET = "bureau/operator-tools/linear-api-key"
FLEET_SECRETS = ("bureau-console/linear-api-key", "bureau/relay/linear-api-key")


class _Run:
    def __init__(self, *, stdout="lin_api_operator\n", returncode=0, stderr=""):
        self.calls: list[dict] = []
        self._result = SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), **kwargs})
        return self._result


@pytest.fixture(autouse=True)
def _fresh_key_cache():
    linear_ops._operator_key.clear()
    yield
    linear_ops._operator_key.clear()


def test_a_key_in_the_environment_is_used_and_aws_is_never_asked():
    run = _Run()
    assert linear_ops.api_key(env={"LINEAR_API_KEY": "lin_api_env"}, run=run) == "lin_api_env"
    assert run.calls == []


def test_outside_ci_with_no_key_it_reads_the_operator_tools_secret():
    run = _Run()
    assert linear_ops.api_key(env={}, run=run) == "lin_api_operator"
    argv = run.calls[0]["argv"]
    assert argv[:3] == ["aws", "secretsmanager", "get-secret-value"]
    assert argv[argv.index("--secret-id") + 1] == OPERATOR_SECRET
    assert argv[argv.index("--region") + 1] == "us-west-2"
    assert run.calls[0]["env"]["AWS_PROFILE"] == "dreadnought"


def test_it_is_read_once_per_process_not_once_per_request():
    run = _Run()
    for _ in range(3):
        linear_ops.api_key(env={}, run=run)
    assert len(run.calls) == 1, "every Linear request would cost an aws process"


def test_inside_github_actions_no_key_fails_exactly_as_before():
    run = _Run()
    with pytest.raises(KeyError, match="LINEAR_API_KEY"):
        linear_ops.api_key(env={"GITHUB_ACTIONS": "true"}, run=run)
    assert run.calls == [], "a CI job reached for the operator's key"


def test_a_failed_read_names_the_secret_and_never_echoes_the_output():
    run = _Run(stdout="lin_api_half", returncode=255, stderr="AccessDeniedException")
    with pytest.raises(RuntimeError) as exc:
        linear_ops.api_key(env={}, run=run)
    assert OPERATOR_SECRET in str(exc.value)
    assert "AccessDeniedException" in str(exc.value)
    assert "lin_api_half" not in str(exc.value)
    assert linear_ops._operator_key == {}, "a failed read was cached"


def test_an_empty_secret_is_refused():
    with pytest.raises(RuntimeError):
        linear_ops.api_key(env={}, run=_Run(stdout=" \n"))


def test_gql_sends_the_key_it_resolved(monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(linear_ops, "_secrets_run", _Run(stdout="lin_api_operator"))
    sent = {}

    class _Resp(io.BytesIO):
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        sent["auth"] = req.get_header("Authorization")
        return _Resp(json.dumps({"data": {"ok": True}}).encode())

    monkeypatch.setattr(linear_ops.urllib.request, "urlopen", urlopen)
    linear_ops.gql("{ ok }")
    assert sent["auth"] == "lin_api_operator"


def test_linear_ops_never_names_a_fleet_secret():
    tree = ast.parse(open(linear_ops.__file__).read())
    strings = {n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not strings & set(FLEET_SECRETS)
