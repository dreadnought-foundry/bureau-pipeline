"""RED-first tests for DRE-5121 — the Claude Code pin, raised by the adoption
workflow and only through a trial-gated pull request.

THE INCIDENT. On 2026-09-28 Claude Sonnet 5.5 passed `model-trial.yml` on the
pinned Claude Code 2.1.282 (run 36479502126: `claude-sonnet-5-5: passed —
completed, 3 turns, 7s`). 2.1.282 has no entry for the model in its registry,
so it ran it on its unknown-model defaults — a 200K context window and a 32K
output cap, where the model offers 1M and 128K. The adoption workflow as
planned (DRE-3898) would have put it on top of the advisory ladder, and every
reviewer would have kept a fifth of its context. DRE-5116 raised the pin to
2.1.284 (`claude-code-action` v1.0.236) by hand, the first release that lists
the model.

WHAT `scripts/claude_code_pin.py` ANSWERS, and what this file pins:

  * `supports <model>` — does the PINNED Claude Code list the model at full
    limits? Read out of that release's own model table, compared with the
    Models API's `max_input_tokens` / `max_tokens`. The fixtures under
    `tests/fixtures/claude_code_pin/` are the real tables, cut out of the
    published 2.1.282 and 2.1.284 linux-x64 binaries.
  * `latest-supporting <model>` — the oldest `claude-code-action` release
    newer than the pin whose Claude Code lists the model, read off the
    action's releases and the npm registry.
  * `render pr-title|pr-body` and `apply` — the pin-raise pull request: its
    texts, and the edit to every place DRE-3417's #511 raise touched.
  * `limits <model>` and `strength(...)` — the two halves `model-trial.yml`
    scores full strength with (tests/test_model_trial.py drives that script).

EVERY NETWORK READ IS FAKED. The module takes one `http(url, headers)` seam
and nothing here passes the real one. A read that fails is UNKNOWN — never a
yes — and that is asserted for every read the module makes.

Run: cd bureau-pipeline && python3 -m pytest tests/test_claude_code_pin.py -v
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "claude_code_pin"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import check_action_pins  # noqa: E402
import claude_code_pin as ccp  # noqa: E402

SONNET_55 = "claude-sonnet-5-5"

# What the Models API gives Sonnet 5.5 (the card: "the model offers 1M and
# 128K"), and what 2.1.282 ran it at instead (trial run 36479502126).
FULL = {"max_input_tokens": 1_000_000, "max_tokens": 128_000}
FALLBACK_RUN = {"contextWindow": 200_000, "maxOutputTokens": 32_000}
FULL_RUN = {"contextWindow": 1_000_000, "maxOutputTokens": 128_000}

# The real vendor pairs, read off each release's base-action/action.yml.
V234 = ("v1.0.234", "2.1.282", "9171db3e57d6a3140a37ddc2ba92788584e0ead6")
V235 = ("v1.0.235", "2.1.283", "b" * 40)
V236 = ("v1.0.236", "2.1.284", "8ce9314fa9a404564fa7e954cd84f25bcba2b829")
V237 = ("v1.0.237", "2.1.285", "c" * 40)


def _table(version: str) -> bytes:
    return (FIXTURES / f"model-table-{version}.txt").read_bytes().strip()


TABLE_282 = _table("2.1.282")
TABLE_284 = _table("2.1.284")


# --------------------------------------------------------------------------- #
# The fake registry                                                            #
# --------------------------------------------------------------------------- #

def _binary(table: bytes) -> bytes:
    """A stand-in for the published `claude` binary: the real model table,
    buried in bytes, beside a DECOY array of the same opening shape (the real
    binary carries five of those — its picker lists — and only the registry
    has `family:`)."""
    decoy = b'models:[{id:"claude-opus-5-5",name:"Opus 5.5",short_name:"Opus"}]'
    return b"\x7fELF" + b"\x00" * 512 + decoy + b"\x00" * 64 + table + b"\x00" * 512


def _tarball(table: bytes) -> bytes:
    """The npm platform package, packed the way npm packs it."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (("package/package.json", b'{"name": "x"}'),
                           ("package/claude", _binary(table))):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeHTTP:
    """The one network seam, answered from a route table. A URL with no route
    is a network failure, raised the way urllib raises one."""

    def __init__(self, routes: dict):
        self.routes = dict(routes)
        self.calls: list[str] = []

    def __call__(self, url, headers=None):
        self.calls.append(url)
        value = self.routes.get(url)
        if value is None:
            raise OSError(f"no route to {url}")
        if isinstance(value, Exception):
            raise value
        return value


def _registry(*, releases=(V234, V235, V236, V237), tables=None,
              limits=FULL, model=SONNET_55) -> dict:
    """Every URL the module reads, for a world where 2.1.282 and 2.1.283 do not
    list Sonnet 5.5 and 2.1.284 and 2.1.285 do."""
    tables = tables if tables is not None else {
        "2.1.282": TABLE_282, "2.1.283": TABLE_282,
        "2.1.284": TABLE_284, "2.1.285": TABLE_284,
    }
    routes: dict = {}
    # The action's releases, newest first as the API lists them — plus the
    # floating `v1` tag, which names no release and must be skipped.
    listing = [{"tag_name": tag, "draft": False, "prerelease": False}
               for tag, _, _ in sorted(releases, key=lambda r: ccp.version_key(r[0]),
                                       reverse=True)]
    listing.insert(1, {"tag_name": "v1", "draft": False, "prerelease": False})
    routes[ccp.ACTION_RELEASES_URL] = json.dumps(listing).encode()
    for tag, cli, sha in releases:
        routes[ccp.ACTION_INSTALLS_URL.format(tag=tag)] = (
            "runs:\n  steps:\n    - run: |\n"
            f'        CLAUDE_CODE_VERSION="{cli}"\n'
            '        curl -fsSL https://claude.ai/install.sh | bash -s -- "$CLAUDE_CODE_VERSION"\n'
        ).encode()
        routes[ccp.ACTION_COMMIT_URL.format(tag=tag)] = json.dumps({"sha": sha}).encode()
    # The npm registry: the package's published versions, and each platform
    # package's own tarball.
    routes[ccp.NPM_PACKAGE_URL] = json.dumps(
        {"versions": {v: {} for v in tables}}).encode()
    for version, table in tables.items():
        tarball = f"https://registry.npmjs.org/tarballs/claude-code-linux-x64-{version}.tgz"
        routes[ccp.NPM_BINARY_URL.format(version=version)] = json.dumps(
            {"version": version, "dist": {"tarball": tarball}}).encode()
        routes[tarball] = _tarball(table)
    if limits is not None:
        routes[ccp.MODELS_URL.format(model=model)] = json.dumps(
            {"id": model, "type": "model", **limits}).encode()
    return routes


def _tree(tmp_path: Path, *, cli: str, tag: str, sha: str) -> Path:
    """A minimal checkout: the installer and one workflow pinning the vendor."""
    installer = tmp_path / ".github" / "actions" / "install-claude-code" / "action.yml"
    installer.parent.mkdir(parents=True)
    installer.write_text(
        "name: Install Claude Code (asserted)\n"
        "inputs:\n  version:\n    required: false\n"
        f'    default: "{cli}"\n'
    )
    workflow = tmp_path / ".github" / "workflows" / "agent-task.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n  run:\n    steps:\n"
        f"      - uses: anthropics/claude-code-action@{sha} # {tag}\n"
    )
    return tmp_path


def _run(argv, http, capsys):
    rc = ccp.main(argv, http=http)
    out = capsys.readouterr()
    return rc, out.out, out.err


def _pairs(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


# --------------------------------------------------------------------------- #
# 1. The model table, read out of the binary                                   #
# --------------------------------------------------------------------------- #

def test_the_2_1_284_table_lists_sonnet_5_5_at_1m_and_128k():
    table = ccp.model_table(_binary(TABLE_284))
    assert table[SONNET_55] == {"context_window": 1_000_000, "max_output_tokens": 128_000}


def test_the_2_1_282_table_does_not_list_sonnet_5_5():
    table = ccp.model_table(_binary(TABLE_282))
    assert SONNET_55 not in table
    # Not vacuous: the table WAS read — it is the model that is missing.
    assert table["claude-sonnet-5"] == {"context_window": 1_000_000,
                                        "max_output_tokens": 64_000}
    assert len(table) == 20


def test_the_decoy_picker_list_is_not_read_as_the_registry():
    table = ccp.model_table(_binary(TABLE_284))
    assert set(table) >= {"claude-opus-5-5", "claude-haiku-4-5"}
    assert len(table) == 21


def test_a_binary_with_no_registry_is_unreadable_not_empty():
    # "No table" must never read as "a table that lists nothing" — the first is
    # UNKNOWN, the second a definite no.
    assert ccp.model_table(b"\x7fELF" + b"\x00" * 2048) is None


@pytest.mark.parametrize("literal,value", [
    ("1e6", 1_000_000), ("2e5", 200_000), ("128000", 128_000), ("128e3", 128_000),
])
def test_js_number_literals_read_as_integers(literal, value):
    entry = ('models:[{id:"claude-x-1",family:"x",context:{window:%s},'
             'max_output_tokens:{default:%s,upper:%s}}]' % (literal, literal, literal))
    assert ccp.model_table(entry.encode())["claude-x-1"] == {
        "context_window": value, "max_output_tokens": value}


# --------------------------------------------------------------------------- #
# 2. supports                                                                  #
# --------------------------------------------------------------------------- #

def test_supports_answers_no_on_2_1_282(tmp_path, capsys):
    root = _tree(tmp_path, cli="2.1.282", tag=V234[0], sha=V234[2])
    rc, out, _ = _run(["supports", SONNET_55, "--root", str(root)],
                      FakeHTTP(_registry()), capsys)
    assert rc == 0
    got = _pairs(out)
    assert got["answer"] == "no"
    assert got["version"] == "2.1.282"
    assert SONNET_55 in got["why"]


def test_supports_answers_yes_on_2_1_284(tmp_path, capsys):
    root = _tree(tmp_path, cli="2.1.284", tag=V236[0], sha=V236[2])
    rc, out, _ = _run(["supports", SONNET_55, "--root", str(root)],
                      FakeHTTP(_registry()), capsys)
    assert rc == 0
    got = _pairs(out)
    assert got["answer"] == "yes"
    assert got["version"] == "2.1.284"


def test_supports_reads_the_live_installer_by_default(capsys):
    # The repo's own pin, read where the fleet reads it.
    rc, out, _ = _run(["supports", SONNET_55], FakeHTTP(_registry()), capsys)
    assert rc == 0
    pinned = yaml.safe_load(
        (ROOT / ".github/actions/install-claude-code/action.yml").read_text()
    )["inputs"]["version"]["default"]
    assert _pairs(out)["version"] == pinned


def test_supports_takes_the_limits_from_a_file_without_a_models_api_read(tmp_path, capsys):
    root = _tree(tmp_path, cli="2.1.284", tag=V236[0], sha=V236[2])
    limits = tmp_path / "limits.json"
    limits.write_text(json.dumps({"model": SONNET_55, **FULL}))
    http = FakeHTTP(_registry(limits=None))
    rc, out, _ = _run(["supports", SONNET_55, "--root", str(root),
                       "--limits", str(limits)], http, capsys)
    assert _pairs(out)["answer"] == "yes"
    assert not any("api.anthropic.com" in url for url in http.calls)


def test_listed_below_the_models_limits_is_no(tmp_path, capsys):
    # The model is in the table but at less than the API offers — a table that
    # knows the model without its full window is exactly what a raise fixes.
    root = _tree(tmp_path, cli="2.1.284", tag=V236[0], sha=V236[2])
    bigger = {"max_input_tokens": 2_000_000, "max_tokens": 128_000}
    rc, out, _ = _run(["supports", SONNET_55, "--root", str(root)],
                      FakeHTTP(_registry(limits=bigger)), capsys)
    got = _pairs(out)
    assert got["answer"] == "no"
    assert "2,000,000" in got["why"] and "1,000,000" in got["why"]


@pytest.mark.parametrize("broken", ["package", "binary", "tarball"])
def test_a_failed_table_read_is_unknown_never_yes(tmp_path, capsys, broken):
    root = _tree(tmp_path, cli="2.1.284", tag=V236[0], sha=V236[2])
    routes = _registry()
    if broken == "package":
        routes[ccp.NPM_PACKAGE_URL] = OSError("registry down")
    elif broken == "binary":
        routes[ccp.NPM_BINARY_URL.format(version="2.1.284")] = b"<html>502</html>"
    else:
        tarball = "https://registry.npmjs.org/tarballs/claude-code-linux-x64-2.1.284.tgz"
        routes[tarball] = b"not a tarball"
    rc, out, _ = _run(["supports", SONNET_55, "--root", str(root)],
                      FakeHTTP(routes), capsys)
    assert rc == 0
    assert _pairs(out)["answer"] == "unknown"


def test_a_failed_models_api_read_is_unknown_when_the_table_lists_it(tmp_path, capsys):
    root = _tree(tmp_path, cli="2.1.284", tag=V236[0], sha=V236[2])
    rc, out, _ = _run(["supports", SONNET_55, "--root", str(root)],
                      FakeHTTP(_registry(limits=None)), capsys)
    assert _pairs(out)["answer"] == "unknown"


def test_absent_from_a_readable_table_is_no_even_without_the_api(tmp_path, capsys):
    # A table that does not list the model cannot run it at full strength,
    # whatever the API would have said.
    root = _tree(tmp_path, cli="2.1.282", tag=V234[0], sha=V234[2])
    rc, out, _ = _run(["supports", SONNET_55, "--root", str(root)],
                      FakeHTTP(_registry(limits=None)), capsys)
    assert _pairs(out)["answer"] == "no"


def test_a_table_with_no_registry_at_all_is_unknown(tmp_path, capsys):
    root = _tree(tmp_path, cli="2.1.284", tag=V236[0], sha=V236[2])
    routes = _registry()
    tarball = "https://registry.npmjs.org/tarballs/claude-code-linux-x64-2.1.284.tgz"
    routes[tarball] = _tarball(b"no registry in this build")
    rc, out, _ = _run(["supports", SONNET_55, "--root", str(root)],
                      FakeHTTP(routes), capsys)
    assert _pairs(out)["answer"] == "unknown"


# --------------------------------------------------------------------------- #
# 3. latest-supporting                                                         #
# --------------------------------------------------------------------------- #

def _latest(tmp_path, capsys, routes, pin=V234):
    root = _tree(tmp_path, cli=pin[1], tag=pin[0], sha=pin[2])
    http = FakeHTTP(routes)
    rc, out, err = _run(["latest-supporting", SONNET_55, "--root", str(root)], http, capsys)
    assert rc == 0, err
    return json.loads(out), http


def test_latest_supporting_finds_2_1_284_and_v1_0_236(tmp_path, capsys):
    pin, _ = _latest(tmp_path, capsys, _registry())
    assert pin["answer"] == "found"
    assert pin["model"] == SONNET_55
    assert pin["to"] == {"claude_code": "2.1.284", "action": "v1.0.236",
                         "sha": "8ce9314fa9a404564fa7e954cd84f25bcba2b829"}
    assert pin["from"] == {"claude_code": "2.1.282", "action": "v1.0.234",
                           "sha": "9171db3e57d6a3140a37ddc2ba92788584e0ead6"}
    assert pin["limits"] == FULL
    assert pin["listed"] == {"context_window": 1_000_000, "max_output_tokens": 128_000}


def test_latest_supporting_takes_the_oldest_not_the_newest(tmp_path, capsys):
    # 2.1.285 lists the model too; the smallest step that fixes it is 2.1.284.
    _, http = _latest(tmp_path, capsys, _registry())
    assert not any("2.1.285" in url for url in http.calls if "tarballs" in url)


def test_latest_supporting_never_looks_below_the_pin(tmp_path, capsys):
    older = ("v1.0.233", "2.1.281", "d" * 40)
    routes = _registry(releases=(older, V234, V235, V236, V237))
    _, http = _latest(tmp_path, capsys, routes)
    assert ccp.ACTION_INSTALLS_URL.format(tag="v1.0.233") not in http.calls


def test_a_failed_read_on_an_earlier_release_is_unknown_never_found(tmp_path, capsys):
    # 2.1.283 cannot be read, so nothing can say 2.1.284 is the OLDEST fix.
    routes = _registry()
    routes["https://registry.npmjs.org/tarballs/claude-code-linux-x64-2.1.283.tgz"] = \
        OSError("reset by peer")
    pin, _ = _latest(tmp_path, capsys, routes)
    assert pin["answer"] == "unknown"
    assert "to" not in pin


@pytest.mark.parametrize("url", [
    "releases", "installs", "commit", "package", "models",
])
def test_every_read_that_fails_is_unknown(tmp_path, capsys, url):
    routes = _registry()
    key = {
        "releases": ccp.ACTION_RELEASES_URL,
        "installs": ccp.ACTION_INSTALLS_URL.format(tag="v1.0.235"),
        "commit": ccp.ACTION_COMMIT_URL.format(tag="v1.0.236"),
        "package": ccp.NPM_PACKAGE_URL,
        "models": ccp.MODELS_URL.format(model=SONNET_55),
    }[url]
    routes[key] = OSError("down")
    pin, _ = _latest(tmp_path, capsys, routes)
    assert pin["answer"] == "unknown", pin
    assert pin["why"]


def test_an_http_error_carries_the_servers_reason(monkeypatch):
    """A refused read names why, not just the status (DRE-2923's audit)."""
    import urllib.error
    import urllib.request

    def refuse(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", {},
            io.BytesIO(b'{"message": "API rate limit exceeded"}'))

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(RuntimeError, match="403.*API rate limit exceeded"):
        ccp._http_get(ccp.ACTION_RELEASES_URL)


def test_no_release_lists_it_is_none(tmp_path, capsys):
    routes = _registry(tables={"2.1.282": TABLE_282, "2.1.283": TABLE_282,
                               "2.1.284": TABLE_282, "2.1.285": TABLE_282})
    pin, _ = _latest(tmp_path, capsys, routes)
    assert pin["answer"] == "none"
    assert "to" not in pin


def test_a_pin_that_already_supports_it_needs_no_raise(tmp_path, capsys):
    pin, _ = _latest(tmp_path, capsys, _registry(), pin=V236)
    assert pin["answer"] == "none"
    assert "already" in pin["why"]


# --------------------------------------------------------------------------- #
# 4. limits and strength — the two halves the trial scores with               #
# --------------------------------------------------------------------------- #

def test_limits_prints_the_models_api_numbers(capsys):
    rc, out, _ = _run(["limits", SONNET_55], FakeHTTP(_registry()), capsys)
    assert rc == 0
    assert json.loads(out) == {"model": SONNET_55, **FULL}


@pytest.mark.parametrize("body", [
    OSError("down"), b"not json", json.dumps({"id": SONNET_55}).encode(),
    json.dumps({"id": SONNET_55, "max_input_tokens": "lots", "max_tokens": 1}).encode(),
])
def test_limits_that_cannot_be_read_are_unknown(capsys, body):
    routes = _registry()
    routes[ccp.MODELS_URL.format(model=SONNET_55)] = body
    rc, out, _ = _run(["limits", SONNET_55], FakeHTTP(routes), capsys)
    assert rc == 0
    got = json.loads(out)
    assert got["model"] == SONNET_55
    assert "unknown" in got and "max_tokens" not in got


def test_strength_reads_the_run_below_the_model():
    record = {"modelUsage": {SONNET_55: dict(FALLBACK_RUN, outputTokens=40)}}
    got = ccp.strength(record, SONNET_55, FULL)
    assert got["status"] == ccp.BELOW
    for number in ("200,000", "1,000,000", "32,000", "128,000"):
        assert number in got["why"]


def test_strength_at_full_limits_is_full():
    record = {"modelUsage": {SONNET_55: dict(FULL_RUN, outputTokens=40)}}
    assert ccp.strength(record, SONNET_55, FULL)["status"] == ccp.FULL


@pytest.mark.parametrize("ran", [
    {"contextWindow": 200_000, "maxOutputTokens": 128_000},
    {"contextWindow": 1_000_000, "maxOutputTokens": 32_000},
])
def test_below_on_either_limit_is_below(ran):
    got = ccp.strength({"modelUsage": {SONNET_55: ran}}, SONNET_55, FULL)
    assert got["status"] == ccp.BELOW


@pytest.mark.parametrize("record,limits", [
    ({"modelUsage": {SONNET_55: FULL_RUN}}, None),
    ({"modelUsage": {}}, FULL),
    ({}, FULL),
    ({"modelUsage": {SONNET_55: {"contextWindow": 1_000_000}}}, FULL),
    # Billed only to another model: nothing says what the candidate ran at.
    ({"modelUsage": {"claude-haiku-4-5": FULL_RUN}}, FULL),
])
def test_strength_without_both_halves_is_unknown(record, limits):
    assert ccp.strength(record, SONNET_55, limits)["status"] == ccp.UNKNOWN


def test_strength_uses_the_one_modelusage_seam():
    # tests/test_planning_classify.py holds every modelUsage reader in scripts/
    # to `answered_model`; side work billed to Haiku must not be read as the
    # candidate's limits.
    record = {"modelUsage": {"claude-haiku-4-5": {"contextWindow": 200_000,
                                                  "maxOutputTokens": 8_000,
                                                  "outputTokens": 900},
                             SONNET_55: dict(FULL_RUN, outputTokens=10)}}
    assert ccp.strength(record, SONNET_55, FULL)["status"] == ccp.FULL
    assert "answered_model" in (ROOT / "scripts" / "claude_code_pin.py").read_text()


# --------------------------------------------------------------------------- #
# 5. render — the pin-raise pull request's texts                              #
# --------------------------------------------------------------------------- #

FOUND = {
    "model": SONNET_55, "answer": "found", "why": "",
    "from": {"claude_code": "2.1.282", "action": "v1.0.234", "sha": V234[2]},
    "to": {"claude_code": "2.1.284", "action": "v1.0.236", "sha": V236[2]},
    "limits": FULL, "listed": {"context_window": 1_000_000, "max_output_tokens": 128_000},
}
TRIAL = {"model": SONNET_55, "outcome": "degraded",
         "run_url": "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36479502126",
         "summary": "claude-sonnet-5-5: degraded — ran below full strength"}


def _files(tmp_path, pin=FOUND, trial=TRIAL):
    pin_file = tmp_path / "pin.json"
    pin_file.write_text(json.dumps(pin))
    trial_file = tmp_path / "trial.json"
    trial_file.write_text(json.dumps(trial))
    return str(pin_file), str(trial_file)


def test_the_title_is_deterministic_and_names_the_raise(tmp_path, capsys):
    pin_file, _ = _files(tmp_path)
    titles = set()
    for _ in range(2):
        rc, out, _ = _run(["render", "pr-title", "--pin", pin_file], FakeHTTP({}), capsys)
        assert rc == 0
        titles.add(out.strip())
    assert len(titles) == 1
    (title,) = titles
    assert title.startswith(ccp.PR_TITLE_PREFIX)
    assert "2.1.284" in title and "v1.0.236" in title


def test_one_raise_in_flight_shares_one_title_across_models():
    # Two candidates waiting on the same release open ONE pull request.
    other = dict(FOUND, model="claude-haiku-5-5")
    assert ccp.pr_title(FOUND) == ccp.pr_title(other)


def test_the_body_names_the_model_both_versions_and_the_trial_run(tmp_path, capsys):
    pin_file, trial_file = _files(tmp_path)
    rc, out, _ = _run(["render", "pr-body", "--pin", pin_file, "--trial", trial_file],
                      FakeHTTP({}), capsys)
    assert rc == 0
    for needle in (SONNET_55, "2.1.282", "2.1.284", "v1.0.234", "v1.0.236",
                   TRIAL["run_url"], V236[2]):
        assert needle in out, needle


def test_the_body_says_the_hold_stays_and_the_adoption_follows():
    body = ccp.pr_body(FOUND, TRIAL)
    assert "Dependabot" in body
    assert "after this merges" in body


def test_the_body_carries_no_verdict_marker():
    trial = dict(TRIAL, summary="VERDICT: APPROVE — QA Critic says so")
    body = ccp.pr_body(FOUND, trial)
    assert "VERDICT:" not in body and "QA Critic" not in body


@pytest.mark.parametrize("answer", ["none", "unknown"])
def test_render_refuses_a_record_that_found_nothing(tmp_path, capsys, answer):
    pin_file, _ = _files(tmp_path, pin={"model": SONNET_55, "answer": answer, "why": "x"})
    rc, out, err = _run(["render", "pr-title", "--pin", pin_file], FakeHTTP({}), capsys)
    assert rc == 2
    assert out == ""


# --------------------------------------------------------------------------- #
# 6. apply — every place DRE-3417's #511 raise touched, on a copy of the repo  #
# --------------------------------------------------------------------------- #

NEW = {"claude_code": "2.1.290", "action": "v1.0.240",
       "sha": "0123456789abcdef0123456789abcdef01234567"}


def _live_pin() -> dict:
    return ccp.pinned(ROOT)


def _copy_repo(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(
        ".git", ".bureau-pipeline", "__pycache__", ".pytest_cache", "node_modules"))
    return dest


def test_pinned_reads_the_installer_and_the_one_vendor_sha():
    live = _live_pin()
    installer = yaml.safe_load(
        (ROOT / ".github/actions/install-claude-code/action.yml").read_text())
    assert live["claude_code"] == installer["inputs"]["version"]["default"]
    shas = {ref.ref for path in check_action_pins.iter_files(check_action_pins.DEFAULT_PATHS)
            for ref in check_action_pins.references(path)
            if ref.action == ccp.VENDOR_ACTION}
    assert {live["sha"]} == shas
    assert live["action"].startswith("v1.0.")


def test_apply_raises_every_place_and_the_repos_own_guards_pass(tmp_path):
    repo = _copy_repo(tmp_path)
    # A candidate the config does not know yet — the case the raise exists for.
    pin = {"model": "claude-sonnet-6", "answer": "found", "why": "",
           "from": _live_pin(), "to": NEW, "limits": FULL,
           "listed": {"context_window": 1_000_000, "max_output_tokens": 128_000}}
    changed = ccp.apply(pin, root=repo, today="2026-10-01")

    rel = {str(Path(p).relative_to(repo)) for p in changed}
    assert ".github/actions/install-claude-code/action.yml" in rel
    assert "tests/test_check_action_pins.py" in rel
    assert "tests/test_model_cli_support.py" in rel
    assert any(p.startswith(".github/workflows/") for p in rel)
    assert not any(p.startswith("scripts/") for p in rel), (
        "a pin raise edits ops and tests only — never code (the TDD gate)"
    )

    # The pair, moved together.
    assert ccp.pinned(repo) == NEW
    for path in check_action_pins.iter_files([repo / ".github" / "workflows",
                                              repo / ".github" / "actions"]):
        for ref in check_action_pins.references(path):
            if ref.action == ccp.VENDOR_ACTION:
                assert ref.ref == NEW["sha"]
                assert check_action_pins.VERSION_COMMENT_RE.match(ref.comment).group(1) \
                    == NEW["action"]

    # History is kept: the old VENDOR_INSTALLS rows survive beside the new one.
    pins_test = (repo / "tests" / "test_check_action_pins.py").read_text()
    assert f'"{NEW["sha"]}": "{NEW["claude_code"]}",  # {NEW["action"]}' in pins_test
    assert f'"{pin["from"]["sha"]}": "{pin["from"]["claude_code"]}"' in pins_test
    support = (repo / "tests" / "test_model_cli_support.py").read_text()
    ahead = support[support.index("AHEAD_OF_ADOPTION = {"):]
    assert f'"claude-sonnet-6": "{NEW["claude_code"]}"' in ahead[:ahead.index("}")]

    # And the repo's own guards agree, run against the raised copy.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_check_action_pins.py", "tests/test_model_cli_support.py",
         "tests/test_model_trial_workflow.py", "tests/test_groomer_wiring.py",
         "tests/test_agent_task_rate_limit_retry.py", "tests/test_claude_install_asserted.py"],
        cwd=repo, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    check = subprocess.run([sys.executable, "scripts/check_action_pins.py"],
                           cwd=repo, capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr


def test_apply_puts_a_known_models_row_in_the_minimum_table(tmp_path):
    repo = _copy_repo(tmp_path)
    pin = dict(FOUND, **{"model": "claude-fable-5-1", "from": _live_pin(), "to": NEW})
    ccp.apply(pin, root=repo, today="2026-10-01")
    support = (repo / "tests" / "test_model_cli_support.py").read_text()
    minimum = support[support.index("MINIMUM_CLAUDE_CODE = {"):]
    assert f'"claude-fable-5-1": "{NEW["claude_code"]}"' in minimum[:minimum.index("}")]
    assert support.count('"claude-fable-5-1": "') == 1


def test_apply_updates_an_existing_minimum_row_rather_than_adding_a_second(tmp_path):
    repo = _copy_repo(tmp_path)
    pin = dict(FOUND, **{"from": _live_pin(), "to": NEW})
    ccp.apply(pin, root=repo, today="2026-10-01")
    support = (repo / "tests" / "test_model_cli_support.py").read_text()
    assert support.count(f'"{SONNET_55}":') == 1
    assert f'"{SONNET_55}": "{NEW["claude_code"]}"' in support


def test_apply_records_the_raise_in_the_installers_history(tmp_path):
    repo = _copy_repo(tmp_path)
    pin = dict(FOUND, **{"from": _live_pin(), "to": NEW})
    ccp.apply(pin, root=repo, today="2026-10-01")
    text = (repo / ".github/actions/install-claude-code/action.yml").read_text()
    assert f"{NEW['claude_code']} / {NEW['action']} on 2026-10-01" in text
    assert "DRE-5121" in text
    # The earlier history is untouched.
    assert "Then 2.1.284 / v1.0.236 on 2026-09-28 (DRE-5116)" in text


def test_apply_refuses_a_record_the_tree_has_moved_past(tmp_path):
    repo = _copy_repo(tmp_path)
    stale = dict(FOUND, **{"from": {"claude_code": "2.1.263", "action": "v1.0.217",
                                   "sha": "9c5ddab2e6d17b83ea679153b31f1d5f023cf636"},
                          "to": NEW})
    with pytest.raises(ccp.Refused):
        ccp.apply(stale, root=repo, today="2026-10-01")
    assert ccp.pinned(repo) == _live_pin(), "a refusal writes nothing"


def test_apply_refuses_a_move_that_is_not_up(tmp_path):
    repo = _copy_repo(tmp_path)
    down = dict(FOUND, **{"from": _live_pin(),
                          "to": {"claude_code": "2.1.200", "action": "v1.0.200",
                                 "sha": "e" * 40}})
    with pytest.raises(ccp.Refused):
        ccp.apply(down, root=repo, today="2026-10-01")


def test_apply_cli_reads_the_pin_record(tmp_path, capsys):
    repo = _copy_repo(tmp_path)
    pin_file = tmp_path / "pin.json"
    pin_file.write_text(json.dumps(dict(FOUND, **{"from": _live_pin(), "to": NEW})))
    rc, out, err = _run(["apply", "--pin", str(pin_file), "--root", str(repo),
                         "--date", "2026-10-01"], FakeHTTP({}), capsys)
    assert rc == 0, err
    assert "tests/test_model_cli_support.py" in out
    assert ccp.pinned(repo) == NEW


# --------------------------------------------------------------------------- #
# 7. The Dependabot hold stays: this PR is the only way the pin moves          #
# --------------------------------------------------------------------------- #

def test_dependabot_holds_claude_code_action_for_every_update_type():
    """DRE-4336's hold, standing: the pin moves only through the trial-gated
    pin-raise PR above, never through a bare Dependabot bump (DRE-3416's
    2026-09-08 outage was a vendor release nobody trialled)."""
    doc = yaml.safe_load(DEPENDABOT.read_text())
    actions = next(u for u in doc["updates"] if u["package-ecosystem"] == "github-actions")
    rules = [r for r in actions.get("ignore") or []
             if r.get("dependency-name") == ccp.VENDOR_ACTION]
    assert len(rules) == 1, rules
    (rule,) = rules
    assert "update-types" not in rule and "versions" not in rule, (
        "the hold must refuse every update of the action — the bumps it refuses "
        "are patches"
    )


def test_the_hold_names_the_one_sanctioned_path():
    text = DEPENDABOT.read_text()
    assert "DRE-5121" in text
    assert "claude_code_pin.py" in text


# --------------------------------------------------------------------------- #
# 8. The CLI                                                                   #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("argv", [
    [], ["frobnicate"], ["supports"], ["render", "pr-title"],
    ["render", "question-title", "--pin", "x"], ["apply"],
])
def test_a_malformed_call_exits_2(capsys, argv):
    rc, _, _ = _run(argv, FakeHTTP({}), capsys)
    assert rc == 2
