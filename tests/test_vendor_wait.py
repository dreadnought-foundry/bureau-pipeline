"""RED-first: one reader says whether a build's API-error death was a wait
(DRE-7795).

Today every `api_death` a build reports goes to `dead_run.py decide
--is-error`: one of the card's three strikes is spent and a `model-error:`
marker switches the model. The week ending 2026-10-17 had 28 vendor-error
deaths, every one a 401 — 13 within ten minutes of a credential renewal.
Two of those deaths are not the card's fault and a fresh run fixes them: the
401 our own renewal causes, and a vendor's momentary 529/5xx.
`scripts/vendor_wait.py` is the one reader that names them. Nothing calls it
yet (DRE-7800 and DRE-7802 wire it), so no death changes with this card.

What this file pins:

  1. **The contract.** `KINDS == ("rotation", "vendor-transient")`, and the
     CLI prints exactly one of `rotation`, `vendor-transient`, `none`, exiting
     0 either way.
  2. **vendor-transient.** A 529 `overloaded_error` and each of 500, 502, 503,
     504 read `vendor-transient` — and not on a turn-cap death, nor on a
     capacity wall.
  3. **rotation.** Our renewal's `revoked` 401 reads `rotation`; an `expired`
     401 and a 401 that says neither read None, whatever their time — the
     wording decides, never the clock.
  4. **None for the rest.** A 403, a 429 `rate_limit_error`, an unreadable
     record, a run that did not die.
  5. **One reader of each fact.** `credential_rotation.in_execution`,
     `check_agent_result._turn_cap_evidence` and
     `model_fallback.capacity_refusal` are each monkeypatched in turn and the
     answer follows the patched reader, so none of them is re-implemented
     inside the module.

Run: cd bureau-pipeline && python3 -m pytest tests/test_vendor_wait.py -v
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_agent_result  # noqa: E402, RUF100 — the turn-cap reader
import credential_rotation  # noqa: E402, RUF100 — the renewal 401 reader
import model_fallback  # noqa: E402, RUF100 — the capacity-wall reader
import vendor_wait  # noqa: E402, RUF100

MODULE = SCRIPTS / "vendor_wait.py"

#: The reader's own four collaborators, the only pipeline modules it may use.
READERS = {"credential_rotation", "execution_result", "check_agent_result",
           "model_fallback"}


def _result(**fields) -> dict:
    """A result record as the action writes it: the one-turn, $0 death."""
    base = {"type": "result", "subtype": "success", "is_error": True,
            "num_turns": 1, "total_cost_usd": 0}
    base.update(fields)
    return base


def _overloaded(status=529) -> dict:
    return _result(
        api_error_status=status,
        result=(f'API Error: {status} {{"type":"error","error":'
                '{"type":"overloaded_error","message":"Overloaded"}}'),
    )


def _server_error(status: int) -> dict:
    return _result(
        api_error_status=status,
        result=(f'API Error: {status} {{"type":"error","error":'
                '{"type":"api_error","message":"Internal server error"}}'),
    )


REVOKED_401 = _result(
    api_error_status=401,
    result="API Error: 401 OAuth access token has been revoked",
)
EXPIRED_401 = _result(
    api_error_status=401,
    result="API Error: 401 OAuth access token has expired",
)
#: A 401 that says neither `revoked` nor `expired`, with no CLI 401 retry.
BARE_401 = _result(
    api_error_status=401,
    result=('API Error: 401 {"type":"error","error":'
            '{"type":"authentication_error","message":"Invalid bearer token"}}'),
)
FORBIDDEN_403 = _result(
    api_error_status=403,
    result=('API Error: 403 {"type":"error","error":'
            '{"type":"permission_error","message":"Request not allowed"}}'),
)
RATE_LIMITED_429 = _result(
    api_error_status=429,
    result=('API Error: 429 {"type":"error","error":{"type":"rate_limit_error",'
            '"message":"This request would exceed your account\'s rate limit."}}'),
)
#: A 500 on a run that hit the turn ceiling: real work, not a vendor blip.
TURN_CAP_500 = _result(
    subtype="error_max_turns",
    api_error_status=500,
    num_turns=48,
    total_cost_usd=3.10,
    result="API Error: 500 Internal server error",
)
#: A 503 whose body is the account's usage wall, not the service being busy.
CAPACITY_WALL_503 = _result(
    api_error_status=503,
    result="Claude AI usage limit reached · You've hit your limit · resets 8:30pm (UTC)",
)


def _write(tmp_path: Path, data, name="execution.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _cli(path: str, capsys) -> tuple[int, str]:
    rc = vendor_wait.main(["kind", path])
    return rc, capsys.readouterr().out.strip()


# --------------------------------------------------------------------------
# 1. the contract
# --------------------------------------------------------------------------

def test_kinds_are_exactly_the_two_contract_strings():
    assert vendor_wait.KINDS == ("rotation", "vendor-transient")


def test_the_cli_prints_the_kind_and_exits_zero(tmp_path, capsys):
    assert _cli(_write(tmp_path, [_overloaded()]), capsys) == (0, "vendor-transient")
    assert _cli(_write(tmp_path, [REVOKED_401]), capsys) == (0, "rotation")


# --------------------------------------------------------------------------
# 2. vendor-transient
# --------------------------------------------------------------------------

def test_a_529_overloaded_error_is_a_vendor_transient_wait():
    assert vendor_wait.kind([_overloaded()]) == "vendor-transient"
    # the result record alone, as the action also writes it
    assert vendor_wait.kind(_overloaded()) == "vendor-transient"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_each_5xx_is_a_vendor_transient_wait(status):
    assert vendor_wait.kind([_server_error(status)]) == "vendor-transient"


def test_a_status_written_as_a_string_still_reads():
    assert vendor_wait.kind([_server_error("502")]) == "vendor-transient"


def test_overloaded_error_with_no_status_is_a_vendor_transient_wait():
    record = _result(result='API Error: {"type":"overloaded_error"}')
    assert vendor_wait.kind([record]) == "vendor-transient"


def test_the_status_is_read_off_the_result_record_never_the_transcript():
    """An assistant turn that talks about a 529 is not the run dying on one."""
    turn = {"type": "assistant", "api_error_status": 529,
            "message": {"content": "the API said overloaded_error, 529"}}
    died = _result(api_error_status=400, result="API Error: 400 bad request")
    assert vendor_wait.kind([turn, died]) is None
    assert vendor_wait.kind([turn, _overloaded()]) == "vendor-transient"


def test_a_run_that_did_not_die_is_no_wait():
    assert vendor_wait.kind([_overloaded() | {"is_error": False}]) is None


# --------------------------------------------------------------------------
# 3. rotation — by why the token died, never by the clock
# --------------------------------------------------------------------------

def test_our_renewals_revoked_401_is_a_rotation_wait():
    assert vendor_wait.kind([REVOKED_401]) == "rotation"


def test_a_cli_401_retry_then_a_401_death_is_a_rotation_wait():
    retry = {"type": "system", "subtype": "api_retry", "error_status": 401}
    assert vendor_wait.kind([retry, BARE_401]) == "rotation"


def test_a_401_that_says_neither_revoked_nor_expired_is_no_wait_whatever_its_time():
    """The departure from the epic, pinned: no clock is read. A 401 inside a
    renewal window that carries neither shape stays a strike, and a `revoked`
    401 far outside one is still a rotation."""
    inside_window = BARE_401 | {"timestamp": "2026-10-15T07:56:52Z"}
    outside_window = REVOKED_401 | {"timestamp": "2026-10-15T03:00:00Z"}
    assert vendor_wait.kind([inside_window]) is None
    assert vendor_wait.kind([outside_window]) == "rotation"


def test_the_docstring_states_the_departure_from_the_epic():
    doc = ast.get_docstring(ast.parse(MODULE.read_text(encoding="utf-8")))
    assert "departs from the epic" in doc.lower()
    assert "not by the clock" in doc.lower()


# --------------------------------------------------------------------------
# 4. None for everything else — the death is a strike, exactly as today
# --------------------------------------------------------------------------

NONE_CASES = {
    "expired 401": [EXPIRED_401],
    "403": [FORBIDDEN_403],
    "429 rate_limit_error": [RATE_LIMITED_429],
    "500 on a turn-cap death": [TURN_CAP_500],
    "capacity wall": [CAPACITY_WALL_503],
    "a 401 that is not our rotation": [BARE_401],
}


@pytest.mark.parametrize("name", sorted(NONE_CASES))
def test_everything_else_is_no_wait(name):
    assert vendor_wait.kind(NONE_CASES[name]) is None


@pytest.mark.parametrize("name", sorted(NONE_CASES))
def test_the_cli_prints_none_for_everything_else(name, tmp_path, capsys):
    assert _cli(_write(tmp_path, NONE_CASES[name]), capsys) == (0, "none")


@pytest.mark.parametrize("data", [None, "", [], {}, [1, "x"], {"result": "API Error: 529"}])
def test_an_unreadable_record_is_no_wait(data):
    assert vendor_wait.kind(data) is None


def test_the_cli_prints_none_for_an_unreadable_or_missing_file(tmp_path, capsys):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert _cli(str(broken), capsys) == (0, "none")
    assert _cli(str(tmp_path / "absent.json"), capsys) == (0, "none")


def test_the_cli_refuses_a_malformed_call(capsys):
    assert vendor_wait.main([]) == 2
    assert vendor_wait.main(["check", "x"]) == 2


# --------------------------------------------------------------------------
# 5. one reader of each fact — the answer follows the patched reader
# --------------------------------------------------------------------------

def test_rotation_is_read_through_credential_rotation(monkeypatch):
    monkeypatch.setattr(credential_rotation, "in_execution", lambda data: False)
    assert vendor_wait.kind([REVOKED_401]) is None
    monkeypatch.setattr(credential_rotation, "in_execution", lambda data: True)
    assert vendor_wait.kind([FORBIDDEN_403]) == "rotation"


def test_the_turn_cap_veto_is_read_through_check_agent_result(monkeypatch):
    monkeypatch.setattr(check_agent_result, "_turn_cap_evidence", lambda record: True)
    assert vendor_wait.kind([_overloaded()]) is None
    monkeypatch.setattr(check_agent_result, "_turn_cap_evidence", lambda record: False)
    assert vendor_wait.kind([TURN_CAP_500]) == "vendor-transient"


def test_the_capacity_wall_is_read_through_model_fallback(monkeypatch):
    monkeypatch.setattr(model_fallback, "capacity_refusal",
                        lambda text, record=None: "hit your limit")
    assert vendor_wait.kind([_server_error(500)]) is None
    monkeypatch.setattr(model_fallback, "capacity_refusal",
                        lambda text, record=None: None)
    assert vendor_wait.kind([CAPACITY_WALL_503]) == "vendor-transient"
    monkeypatch.setattr(model_fallback, "capacity_refusal",
                        lambda text, record=None: "overloaded_error")
    assert vendor_wait.kind([_overloaded()]) == "vendor-transient"


# --------------------------------------------------------------------------
# the module's shape
# --------------------------------------------------------------------------

def _imports(tree: ast.AST) -> list[tuple[str, bool]]:
    """(module, imported at module level) for every import in `tree`."""
    top = {id(node) for node in tree.body}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name.split(".")[0], id(node) in top) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module.split(".")[0], id(node) in top))
    return found


def test_the_module_imports_stdlib_plus_its_four_readers_each_inside_a_function():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imports = _imports(tree)
    ours = {name for name, _ in imports if (SCRIPTS / f"{name}.py").exists()}
    assert ours == READERS
    assert not [name for name, top in imports if name in READERS and top]
    for name, _ in imports:
        if name not in READERS:
            assert name in sys.stdlib_module_names, name
