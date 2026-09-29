#!/usr/bin/env python3
"""The Claude Code pin — does it run a model at full strength, and what raises
it when it does not (DRE-5121).

THE INCIDENT. On 2026-09-28 Claude Sonnet 5.5 passed `model-trial.yml` on the
pinned Claude Code 2.1.282 (run 36479502126). 2.1.282 has no entry for the
model in its own model registry, so it ran it on its unknown-model defaults —
a 200K context window and a 32K output cap, where the Models API lists 1M and
128K. The adoption workflow (DRE-3898) as planned would have put it on top of
the advisory ladder, and every reviewer would have kept a fifth of its context
with nothing failing to say so. DRE-5116 raised the pin to 2.1.284
(`claude-code-action` v1.0.236), the first release that lists the model, by
hand. This module is that raise made automatic, and the CEO's ask of
2026-09-28 13:53 PT is the reason: "It should just automatically go up to use
the new ones."

THE CLI, as the model adoption workflow (DRE-3898) is to call it. That
workflow is not built yet, so nothing calls it today and the pin moves by hand:

    supports <model-id> [--root DIR] [--limits FILE]
        `answer=yes|no|unknown`, `version=<pinned Claude Code>`, `why=…` as
        `$GITHUB_OUTPUT` lines. Yes only when the pinned release's model table
        lists the model at the Models API's `max_input_tokens` and
        `max_tokens` or above.
    latest-supporting <model-id> [--root DIR] [--limits FILE]
        One JSON pin record on stdout. `answer` is `found` — with `from` and
        `to`, each `{claude_code, action, sha}` — when a `claude-code-action`
        release newer than the pin installs a Claude Code that lists the model
        at full limits; the OLDEST such release, the smallest step that fixes
        it. `none` when no release does (or the pin already does), `unknown`
        when any read failed.
    limits <model-id>
        The Models API's numbers for the id, as JSON — or `{"unknown": why}`.
        `model-trial.yml` writes this to `$RUNNER_TEMP/model-limits.json` and
        scores full strength against it with `strength()`.
    render pr-title|pr-body --pin FILE [--trial FILE]
        The pin-raise pull request's texts. The title names only the target
        release, so a rerun renders the same title and finds the open PR, and
        two models waiting on one release share one PR. `PR_TITLE_PREFIX` is
        what "one pin raise in flight" dedupes on.
    apply --pin FILE [--root DIR] [--date YYYY-MM-DD]
        The raise itself, as text edits to every place DRE-3417's #511 raise
        touched: the installer default (and a line in its history), every
        `claude-code-action` `uses:` sha with its version comment, the tests
        that pin that sha by value, a `VENDOR_INSTALLS` row for the new pair,
        and the model's floor in `tests/test_model_cli_support.py`
        (`AHEAD_OF_ADOPTION` for a candidate no ladder names yet,
        `MINIMUM_CLAUDE_CODE` for one the config knows). `.github/` and `tests/`
        only, so the pin-raise PR is an ops change the TDD gate exempts.

WHERE "FULL LIMITS" IS READ. A Claude Code release carries its model registry
in its own binary, as a minified array: `models:[{id:"claude-sonnet-5-5",
family:"sonnet",…,context:{window:1e6,…},max_output_tokens:{default:128000,
upper:128000},…},…]`. `model_table` cuts that array out of the linux-x64
binary the npm registry publishes (`@anthropic-ai/claude-code-linux-x64`, the
platform the fleet runs on) and reads each entry's window and DEFAULT output
cap — the default, because that is the cap a run gets and the one the trial
sees in `modelUsage.<model>.maxOutputTokens`. `tests/fixtures/claude_code_pin/`
holds the real arrays out of 2.1.282 and 2.1.284.

A FAILED READ IS UNKNOWN, NEVER A YES. Every network read goes through one
`http(url, headers) -> bytes` seam the tests replace, and every failure of it —
network, HTTP, a body that will not parse, a binary with no registry in it —
becomes `unknown` with the reason named. The one definite `no` that needs no
Models API is a readable table that does not list the model at all: a Claude
Code that does not know a model cannot run it at full strength.

THE DEPENDABOT HOLD STAYS. `.github/dependabot.yml` refuses every update of
`claude-code-action`, and this is the one sanctioned path by which automation
moves the pin: a trial-gated pull request, never a bare bump (DRE-3416's
2026-09-08 outage was an untrialled vendor release).
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tarfile
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_action_pins  # noqa: E402
import github_output  # noqa: E402
from model_adoption_actions import _quiet, load_trial  # noqa: E402
from model_adoption_actions import _trial as _trial_section  # noqa: E402
from model_catalog import CATALOG_URL  # noqa: E402
from model_fallback import auth_headers  # noqa: E402
from planning_classify import answered_model  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

#: The CLI, exactly as the adoption workflow (DRE-3898, not built yet) is to call it.
COMMANDS = ("supports", "latest-supporting", "limits", "render", "apply")
RENDER_TARGETS = ("pr-title", "pr-body")

#: What "one pin raise in flight" dedupes on: every pin-raise title opens with it.
PR_TITLE_PREFIX = "Raise the Claude Code pin"

#: `supports` answers; `strength` statuses share UNKNOWN.
YES, NO, UNKNOWN = "yes", "no", "unknown"
#: `latest-supporting` answers, beside UNKNOWN.
FOUND, NONE = "found", "none"
#: `strength` statuses.
FULL, BELOW = "full", "below"

VENDOR_ACTION = "anthropics/claude-code-action"
INSTALLER = Path(".github") / "actions" / "install-claude-code" / "action.yml"
PINS_TEST = Path("tests") / "test_check_action_pins.py"
SUPPORT_TEST = Path("tests") / "test_model_cli_support.py"

NPM_PACKAGE_URL = "https://registry.npmjs.org/@anthropic-ai/claude-code"
NPM_BINARY_URL = "https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/{version}"
BINARY_MEMBER = "package/claude"
ACTION_RELEASES_URL = (
    "https://api.github.com/repos/anthropics/claude-code-action/releases?per_page=100")
ACTION_INSTALLS_URL = (
    "https://raw.githubusercontent.com/anthropics/claude-code-action/{tag}/base-action/action.yml")
ACTION_COMMIT_URL = "https://api.github.com/repos/anthropics/claude-code-action/commits/{tag}"
MODELS_URL = CATALOG_URL + "/{model}"

#: One page of releases. A page that is full and still newer than the pin
#: leaves releases unread, and the oldest fix could be among them.
_RELEASES_PAGE = 100

_EXACT = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_TAG = re.compile(r"^v\d+\.\d+\.\d+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The registry array, and only it: the binary carries other `models:[{id:…`
# arrays (the picker's lists), and only the registry's entries open with
# `family:`.
_REGISTRY = re.compile(rb'models:\[\{id:"claude-[a-z0-9.-]+",family:"')
_ENTRY = re.compile(rb'\{id:"(claude-[a-z0-9.-]+)",family:"')
_WINDOW = re.compile(rb"context:\{window:([0-9][0-9.e+]*)")
_OUTPUT = re.compile(rb"max_output_tokens:\{default:([0-9][0-9.e+]*)")
#: How far past the array's opening bracket its close is looked for. The real
#: array is ~16KB; a missing close is an unreadable table, not a scan of 240MB.
_ARRAY_SCAN = 1 << 20

_INSTALLS = re.compile(r'CLAUDE_CODE_VERSION="?(\d+\.\d+\.\d+)"?')


class ReadFailed(Exception):
    """A read this module could not complete. Always becomes UNKNOWN."""


class Refused(ValueError):
    """A call or a pin record this module will not act on. Exit 2."""


def version_key(text: str) -> tuple[int, ...]:
    """`"2.1.284"` / `"v1.0.236"` → `(2, 1, 284)`. Numbers, never strings:
    `"2.1.1000" < "2.1.284"` is True lexically and wrong."""
    match = _EXACT.match(str(text or "").strip())
    if not match:
        raise ValueError(f"{text!r} is not an exact version")
    return tuple(int(part) for part in match.groups())


def _grouped(n: int) -> str:
    return f"{n:,}"


# --------------------------------------------------------------------------- #
# The network seam                                                             #
# --------------------------------------------------------------------------- #

def _http_get(url: str, headers: Mapping | None = None) -> bytes:
    """The real read: one HTTPS GET, stdlib only. Raises on any failure; an
    HTTP error carries the server's body, the only place its reason survives."""
    import urllib.error
    import urllib.request

    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https URL: {url}")
    request = urllib.request.Request(
        url, headers={"user-agent": "bureau-pipeline-claude-code-pin", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=300) as resp:  # nosec B310 - https only
            return resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - defensive
            body = "<body unreadable>"
        raise RuntimeError(f"HTTP {exc.code}: {body[:500]!r}") from exc


def _github_headers() -> dict:
    headers = {"accept": "application/vnd.github+json"}
    token = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        headers["authorization"] = f"Bearer {token}"
    return headers


def _read(http, url: str, headers: Mapping | None = None) -> bytes:
    try:
        body = http(url, dict(headers or {}))
    except Exception as exc:  # the seam's every failure is a failed read
        raise ReadFailed(f"could not read {url} ({exc})") from exc
    if not isinstance(body, (bytes, bytearray)):
        raise ReadFailed(f"{url} returned no body")
    return bytes(body)


def _read_json(http, url: str, headers: Mapping | None = None):
    body = _read(http, url, headers)
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise ReadFailed(f"{url} did not return JSON ({exc})") from exc


# --------------------------------------------------------------------------- #
# The model table                                                              #
# --------------------------------------------------------------------------- #

def _array_end(blob: bytes, start: int) -> int | None:
    """The index just past the `]` closing the array opening at `start`,
    skipping brackets inside string literals. None when it does not close."""
    depth, quote, i = 0, None, start
    stop = min(len(blob), start + _ARRAY_SCAN)
    while i < stop:
        c = blob[i]
        if quote is not None:
            if c == 0x5C:  # backslash
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in (0x22, 0x27, 0x60):  # " ' `
            quote = c
        elif c in (0x5B, 0x7B):  # [ {
            depth += 1
        elif c in (0x5D, 0x7D):  # ] }
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _number(raw: bytes | None) -> int | None:
    """A JS numeric literal (`1e6`, `128000`) as a positive integer, or None."""
    if raw is None:
        return None
    try:
        value = float(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None
    if value != value or value <= 0 or value != int(value):
        return None
    return int(value)


def model_table(blob: bytes) -> dict[str, dict] | None:
    """The model registry out of a Claude Code binary:
    `{model_id: {"context_window": int|None, "max_output_tokens": int|None}}`.

    None when the binary carries no registry we can read — which is UNKNOWN,
    never "a table that lists nothing". An entry whose numbers do not parse
    keeps the id with None, which `_judge` also reads as UNKNOWN.
    """
    match = _REGISTRY.search(blob)
    if not match:
        return None
    start = match.start() + len(b"models:")
    end = _array_end(blob, start)
    if end is None:
        return None
    array = blob[start:end]
    entries = list(_ENTRY.finditer(array))
    table: dict[str, dict] = {}
    for i, entry in enumerate(entries):
        stop = entries[i + 1].start() if i + 1 < len(entries) else len(array)
        body = array[entry.start():stop]
        window = _WINDOW.search(body)
        output = _OUTPUT.search(body)
        table.setdefault(entry.group(1).decode("ascii"), {
            "context_window": _number(window.group(1) if window else None),
            "max_output_tokens": _number(output.group(1) if output else None),
        })
    return table or None


class _Reader:
    """Every read one command makes, cached, through the one seam."""

    def __init__(self, http, limits: dict | None = None, limits_why: str = ""):
        self.http = http
        self._published: set | None = None
        self._tables: dict[str, dict] = {}
        self._limits = limits
        self._limits_why = limits_why
        self._limits_read = limits is not None or bool(limits_why)

    def published(self) -> set:
        if self._published is None:
            meta = _read_json(self.http, NPM_PACKAGE_URL)
            versions = meta.get("versions") if isinstance(meta, Mapping) else None
            if not isinstance(versions, Mapping) or not versions:
                raise ReadFailed(f"{NPM_PACKAGE_URL} lists no versions")
            self._published = set(versions)
        return self._published

    def table(self, version: str) -> dict:
        if version in self._tables:
            return self._tables[version]
        if version not in self.published():
            raise ReadFailed(f"Claude Code {version} is not a published npm release")
        url = NPM_BINARY_URL.format(version=version)
        meta = _read_json(self.http, url)
        tarball = ((meta.get("dist") or {}).get("tarball")
                   if isinstance(meta, Mapping) else None)
        if not isinstance(tarball, str) or not tarball.startswith("https://"):
            raise ReadFailed(f"{url} names no tarball")
        data = _read(self.http, tarball)
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                member = tar.extractfile(BINARY_MEMBER)
                blob = member.read() if member is not None else b""
        except (tarfile.TarError, KeyError, OSError, EOFError, ValueError) as exc:
            raise ReadFailed(f"could not unpack Claude Code {version} ({exc})") from exc
        table = model_table(blob)
        if table is None:
            raise ReadFailed(f"no model registry could be read out of Claude Code {version}")
        self._tables[version] = table
        return table

    def limits(self, model: str) -> dict:
        if not self._limits_read:
            self._limits_read = True
            try:
                self._limits = model_limits(model, http=self.http)
            except ReadFailed as exc:
                self._limits_why = str(exc)
        if self._limits is None:
            raise ReadFailed(self._limits_why or f"no Models API limits for {model}")
        return self._limits


def _judge(table: dict, model: str, version: str, reader: _Reader) -> tuple[str, str]:
    """(YES|NO|UNKNOWN, why) for one release's table."""
    entry = table.get(model)
    if entry is None:
        return NO, (f"Claude Code {version} does not list {model}, so it runs it on "
                    "its unknown-model defaults")
    window, output = entry.get("context_window"), entry.get("max_output_tokens")
    if window is None or output is None:
        return UNKNOWN, (f"Claude Code {version} lists {model} but its context window "
                         "or output cap could not be read")
    try:
        limits = reader.limits(model)
    except ReadFailed as exc:
        return UNKNOWN, (f"Claude Code {version} lists {model}, but its limits could "
                         f"not be compared: {exc}")
    want_in, want_out = limits["max_input_tokens"], limits["max_tokens"]
    listed = (f"Claude Code {version} lists {model} at a {_grouped(window)}-token "
              f"context window and a {_grouped(output)}-token output cap")
    if window >= want_in and output >= want_out:
        return YES, (f"{listed}; the Models API gives {_grouped(want_in)} and "
                     f"{_grouped(want_out)}")
    return NO, (f"{listed}, below the Models API's {_grouped(want_in)} and "
                f"{_grouped(want_out)}")


# --------------------------------------------------------------------------- #
# The Models API, and full strength                                            #
# --------------------------------------------------------------------------- #

def _positive_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def model_limits(model: str, *, http=None) -> dict:
    """`{"max_input_tokens", "max_tokens"}` for one id, off the Models API.

    Through the one auth seam the catalog uses (`model_fallback.auth_headers`,
    an API key or the subscription OAuth token) — a GET on `/v1/models`, never
    the Messages API (DRE-3074). With no credential the request goes out bare
    and fails, which is a failed read like any other. Raises `ReadFailed`.
    """
    url = MODELS_URL.format(model=model)
    payload = _read_json(http or _http_get, url, auth_headers() or {})
    if not isinstance(payload, Mapping):
        raise ReadFailed(f"{url} returned no model")
    if payload.get("id") not in (None, model):
        raise ReadFailed(f"{url} described {payload.get('id')!r}, not {model}")
    max_in = _positive_int(payload.get("max_input_tokens"))
    max_out = _positive_int(payload.get("max_tokens"))
    if max_in is None or max_out is None:
        raise ReadFailed(f"{url} gives no max_input_tokens / max_tokens for {model}")
    return {"max_input_tokens": max_in, "max_tokens": max_out}


def load_limits(path, model: str) -> tuple[dict | None, str]:
    """(limits, why) out of the file `limits` wrote. None with the reason when
    the file is missing, unreadable, says unknown, or is for another model."""
    if not path:
        return None, "no limits file"
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        return None, f"the Models API limits could not be read ({exc})"
    if not isinstance(data, Mapping):
        return None, "the limits file holds no limits"
    if data.get("model") != model:
        return None, f"the limits file is for {data.get('model')!r}, not {model}"
    if data.get("unknown"):
        return None, str(data["unknown"])
    max_in = _positive_int(data.get("max_input_tokens"))
    max_out = _positive_int(data.get("max_tokens"))
    if max_in is None or max_out is None:
        return None, "the limits file gives no max_input_tokens / max_tokens"
    return {"max_input_tokens": max_in, "max_tokens": max_out}, ""


def strength(record, model: str, limits: dict | None, limits_why: str = "") -> dict:
    """Did the run give the model its full limits? Read out of the run itself.

    `record` is the execution result record; Claude Code writes the limits it
    ran each model at into `modelUsage.<model>` as `contextWindow` and
    `maxOutputTokens`. The entry is chosen through `answered_model`, the one
    seam every `modelUsage` reader here goes through, so side work billed to
    another model is never read as the candidate's.

    `{"status": FULL|BELOW|UNKNOWN, "why": one line}`. BELOW when either
    number is under the Models API's; UNKNOWN when either half is missing.
    """
    def unknown(reason: str) -> dict:
        return {"status": UNKNOWN,
                "why": f"could not confirm full strength: {' '.join(reason.split())}"}

    if not limits:
        return unknown(limits_why or f"no Models API limits for {model}")
    if answered_model(record if isinstance(record, dict) else {}, model) != model:
        return unknown(f"the run recorded no usage for {model}")
    usage = record["modelUsage"][model]
    usage = usage if isinstance(usage, Mapping) else {}
    ran_in = _positive_int(usage.get("contextWindow"))
    ran_out = _positive_int(usage.get("maxOutputTokens"))
    if ran_in is None or ran_out is None:
        return unknown(f"the run recorded no context window or output cap for {model}")
    want_in, want_out = limits["max_input_tokens"], limits["max_tokens"]
    detail = (f"context window {_grouped(ran_in)} of {_grouped(want_in)} and output "
              f"cap {_grouped(ran_out)} of {_grouped(want_out)}")
    if ran_in < want_in or ran_out < want_out:
        return {"status": BELOW, "why": f"ran below full strength: {detail}"}
    return {"status": FULL, "why": f"full strength: {detail}"}


# --------------------------------------------------------------------------- #
# The pin, as the tree holds it                                                #
# --------------------------------------------------------------------------- #

def pinned(root=ROOT) -> dict:
    """`{"claude_code", "action", "sha"}` — the installer's default and the one
    `claude-code-action` sha every workflow pins, with its version comment.
    Raises `Refused` when the tree does not hold exactly one pair."""
    import yaml

    root = Path(root)
    try:
        action = yaml.safe_load((root / INSTALLER).read_text())
        version = str(action["inputs"]["version"]["default"]).strip()
        version_key(version)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise Refused(f"cannot read the pinned Claude Code out of {INSTALLER} ({exc})") from exc
    pairs = set()
    paths = check_action_pins.iter_files(
        [root / ".github" / "workflows", root / ".github" / "actions"])
    for path in paths:
        for ref in check_action_pins.references(path):
            if ref.action != VENDOR_ACTION:
                continue
            tag = check_action_pins.VERSION_COMMENT_RE.match(ref.comment or "")
            pairs.add((ref.ref, tag.group(1) if tag else ""))
    if len(pairs) != 1:
        raise Refused(f"the tree pins {VENDOR_ACTION} as {sorted(pairs)}, not one sha "
                      "with one version comment")
    ((sha, tag),) = pairs
    if not _SHA.match(sha) or not _TAG.match(tag):
        raise Refused(f"{VENDOR_ACTION}@{sha} # {tag} is not a sha pin with a release")
    return {"claude_code": version, "action": tag, "sha": sha}


# --------------------------------------------------------------------------- #
# supports / latest-supporting                                                 #
# --------------------------------------------------------------------------- #

def supports(model: str, *, root=ROOT, http=None, limits: dict | None = None,
             limits_why: str = "") -> dict:
    """`{"model", "answer", "version", "why"}` for the pinned Claude Code."""
    version = pinned(root)["claude_code"]
    reader = _Reader(http or _http_get, limits, limits_why)
    try:
        answer, why = _judge(reader.table(version), model, version, reader)
    except ReadFailed as exc:
        answer, why = UNKNOWN, str(exc)
    return {"model": model, "answer": answer, "version": version, "why": why}


def _releases_after(reader: _Reader, tag: str) -> list[str]:
    """The action's release tags newer than `tag`, oldest first."""
    listing = _read_json(reader.http, ACTION_RELEASES_URL, _github_headers())
    if not isinstance(listing, list):
        raise ReadFailed(f"{ACTION_RELEASES_URL} returned no release list")
    tags = []
    for release in listing:
        if not isinstance(release, Mapping) or release.get("draft") or release.get("prerelease"):
            continue
        name = release.get("tag_name")
        if isinstance(name, str) and _TAG.match(name):
            tags.append(name)
    if not tags:
        raise ReadFailed(f"{ACTION_RELEASES_URL} lists no releases")
    floor = version_key(tag)
    if len(listing) >= _RELEASES_PAGE and min(map(version_key, tags)) > floor:
        raise ReadFailed(f"more than {_RELEASES_PAGE} {VENDOR_ACTION} releases since "
                         f"{tag}; the oldest fix may be on a page not read")
    return sorted((t for t in tags if version_key(t) > floor), key=version_key)


def _installs(reader: _Reader, tag: str) -> str:
    url = ACTION_INSTALLS_URL.format(tag=tag)
    match = _INSTALLS.search(_read(reader.http, url).decode("utf-8", "replace"))
    if not match:
        raise ReadFailed(f"{url} names no CLAUDE_CODE_VERSION")
    return match.group(1)


def _commit(reader: _Reader, tag: str) -> str:
    url = ACTION_COMMIT_URL.format(tag=tag)
    payload = _read_json(reader.http, url, _github_headers())
    sha = payload.get("sha") if isinstance(payload, Mapping) else None
    if not isinstance(sha, str) or not _SHA.match(sha):
        raise ReadFailed(f"{url} names no commit sha")
    return sha


def latest_supporting(model: str, *, root=ROOT, http=None, limits: dict | None = None,
                      limits_why: str = "") -> dict:
    """The pin record `render` and `apply` read. See the module docstring."""
    current = pinned(root)
    record: dict = {"model": model, "from": current}
    reader = _Reader(http or _http_get, limits, limits_why)
    try:
        answer, why = _judge(reader.table(current["claude_code"]), model,
                             current["claude_code"], reader)
        if answer == YES:
            return dict(record, answer=NONE,
                        why=f"already supported, no raise needed: {why}")
        if answer == UNKNOWN:
            return dict(record, answer=UNKNOWN, why=why)
        for tag in _releases_after(reader, current["action"]):
            version = _installs(reader, tag)
            if version_key(version) <= version_key(current["claude_code"]):
                continue
            answer, why = _judge(reader.table(version), model, version, reader)
            if answer == UNKNOWN:
                return dict(record, answer=UNKNOWN, why=why)
            if answer == YES:
                return dict(record, answer=FOUND, why=why,
                            to={"claude_code": version, "action": tag,
                                "sha": _commit(reader, tag)},
                            limits=reader.limits(model),
                            listed=reader.table(version)[model])
    except ReadFailed as exc:
        return dict(record, answer=UNKNOWN, why=str(exc))
    return dict(record, answer=NONE,
                why=(f"no {VENDOR_ACTION} release after {current['action']} installs a "
                     f"Claude Code that lists {model} at full limits"))


# --------------------------------------------------------------------------- #
# render                                                                       #
# --------------------------------------------------------------------------- #

def _require_found(pin) -> None:
    if not isinstance(pin, Mapping) or pin.get("answer") != FOUND:
        answer = pin.get("answer") if isinstance(pin, Mapping) else None
        raise Refused(f"the pin record's answer is {answer!r}, not {FOUND!r} — there is "
                      "no raise to render or apply")
    for side in ("from", "to"):
        half = pin.get(side)
        if (not isinstance(half, Mapping)
                or not _EXACT.match(str(half.get("claude_code") or ""))
                or not _TAG.match(str(half.get("action") or ""))
                or not _SHA.match(str(half.get("sha") or ""))):
            raise Refused(f"the pin record's `{side}` is not a Claude Code version, a "
                          "release tag and a sha")
    if not _MODEL_ID.match(str(pin.get("model") or "")):
        raise Refused("the pin record names no model")


def load_pin(path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read the pin record {path} ({exc})") from exc
    if not isinstance(data, Mapping):
        raise Refused(f"{path} is not a pin record")
    return dict(data)


def pr_title(pin) -> str:
    """Deterministic, and it names only the target: a rerun renders the same
    title and finds the open PR instead of opening a second."""
    _require_found(pin)
    to = pin["to"]
    return f"{PR_TITLE_PREFIX} to {to['claude_code']} (claude-code-action {to['action']})"


def pr_body(pin, trial=None) -> str:
    _require_found(pin)
    model, old, new = pin["model"], pin["from"], pin["to"]
    limits = pin.get("limits") if isinstance(pin.get("limits"), Mapping) else {}
    listed = pin.get("listed") if isinstance(pin.get("listed"), Mapping) else {}

    def num(mapping, key) -> str:
        value = _positive_int(mapping.get(key))
        return _grouped(value) if value else "unknown"

    first = (
        f"`{model}` needs a newer Claude Code before the fleet can adopt it. The "
        f"pinned Claude Code {old['claude_code']} (`claude-code-action` "
        f"{old['action']}) does not run it at full strength, and {new['claude_code']} "
        f"(`claude-code-action` {new['action']}) is the oldest release that does. "
        "This pull request raises the pin and nothing else. The model's adoption "
        "follows on the first model adoption run after this merges."
    )
    raise_table = "\n".join([
        "## The raise",
        "",
        "| | Claude Code | `claude-code-action` | sha |",
        "|---|---|---|---|",
        f"| Pinned today | {old['claude_code']} | {old['action']} | `{old['sha']}` |",
        f"| This pull request | {new['claude_code']} | {new['action']} | `{new['sha']}` |",
    ])
    why = "\n".join([
        "## Why",
        "",
        f"- The Models API gives `{model}` a {num(limits, 'max_input_tokens')}-token "
        f"input window and {num(limits, 'max_tokens')} output tokens.",
        f"- Claude Code {new['claude_code']}'s own model table lists it at a "
        f"{num(listed, 'context_window')}-token context window and a "
        f"{num(listed, 'max_output_tokens')}-token output cap.",
        f"- Claude Code {old['claude_code']} does not, so a run on today's pin would "
        "give the model less than it offers with nothing failing to say so "
        "(Sonnet 5.5 on 2026-09-28: a 200K window where it has 1M).",
    ])
    changes = "\n".join([
        "## What this pull request changes",
        "",
        "The same places DRE-3417's raise (#511) touched, edited by "
        "`scripts/claude_code_pin.py apply`:",
        "",
        f"- The shared installer's default Claude Code, {old['claude_code']} → "
        f"{new['claude_code']}, with a line in its history.",
        f"- Every `{VENDOR_ACTION}` `uses:` pin: the sha and its version comment, "
        f"{old['action']} → {new['action']}.",
        "- `VENDOR_INSTALLS` in `tests/test_check_action_pins.py`: a row for the new "
        "sha. The old rows stay.",
        f"- `tests/test_model_cli_support.py`: `{model}` needs {new['claude_code']}, "
        "so no ladder can carry it on an older pin.",
        "- The tests that pin the vendor sha by value.",
    ])
    gate = "\n".join([
        "## The gate",
        "",
        "Whoever opens this pull request owes it a `model-trial.yml` run on this "
        "pin against the current top rung of every ladder, reported in the trial "
        "result above, so a release that breaks a model the fleet runs today fails "
        "loudly before it reaches any agent. With no passed trial above, this raise "
        "is untrialled and must not merge. The critic and the merge gate are the "
        "rest of the review.",
        "",
        "This is the one sanctioned path for raising the Claude Code pin by "
        "automation (DRE-5121). Dependabot holds `claude-code-action` for every "
        "update type, so a vendor release never arrives as a bare bump: it arrives "
        "here, trialled (DRE-3416's 2026-09-08 outage was an untrialled release).",
    ])
    body = "\n\n".join([first, raise_table, why, _trial_section(trial), changes, gate])
    return _quiet(body) + "\n"


def render(target: str, pin, trial=None) -> str:
    if target == "pr-title":
        return pr_title(pin)
    if target == "pr-body":
        return pr_body(pin, trial)
    raise Refused(f"unknown render target {target!r} — one of {list(RENDER_TARGETS)}")


# --------------------------------------------------------------------------- #
# apply — the raise, as text edits                                             #
# --------------------------------------------------------------------------- #

def _one_block(lines: list[str], opener: str, what: str) -> tuple[int, int]:
    """(first, closing) line indexes of a `NAME = {` literal ending in a line
    that is only `}`."""
    starts = [i for i, line in enumerate(lines) if line.strip() == opener]
    if len(starts) != 1:
        raise Refused(f"{what}: expected one `{opener}` block, found {len(starts)}")
    for j in range(starts[0] + 1, len(lines)):
        if lines[j].strip() == "}":
            return starts[0], j
    raise Refused(f"{what}: the `{opener}` block never closes")


def _installer_text(text: str, pin: dict, today: str) -> str:
    old, new = pin["from"], pin["to"]
    lines = text.splitlines(keepends=True)
    default = re.compile(r'^(\s*default:\s*)"' + re.escape(old["claude_code"]) + r'"(\s*)$')
    hits = [i for i, line in enumerate(lines) if default.match(line)]
    if len(hits) != 1:
        raise Refused(f"{INSTALLER}: expected one `default: \"{old['claude_code']}\"`, "
                      f"found {len(hits)}")
    lines[hits[0]] = default.sub(
        lambda m: f'{m.group(1)}"{new["claude_code"]}"{m.group(2)}', lines[hits[0]])

    # The paragraph that names the pair as it stands today moves with it; the
    # history below it does not.
    head = re.compile(r"^\s*#\s*" + re.escape(old["claude_code"]) + r" — EXACTLY")
    for i, line in enumerate(lines):
        if not head.match(line):
            continue
        for j in range(i, len(lines)):
            if j > i and lines[j].strip() == "#":
                break
            lines[j] = (lines[j].replace(old["claude_code"], new["claude_code"])
                        .replace(old["sha"][:7], new["sha"][:7])
                        .replace(old["action"], new["action"]))
        break

    for i, line in enumerate(lines):
        if line.lstrip().startswith("# MOVE THIS WITH THE VENDOR PIN."):
            indent = line[: len(line) - len(line.lstrip())]
            note = [
                f"Then {new['claude_code']} / {new['action']} on {today} (DRE-5121), "
                "raised by",
                f"`claude_code_pin.py apply` for {pin['model']}: Claude Code {old['claude_code']} "
                "does not run it",
                f"at full strength, and {new['claude_code']} is the oldest release that does.",
            ]
            lines[i:i] = [f"{indent}# {n}\n" for n in note] + [f"{indent}#\n"]
            break
    return "".join(lines)


def _vendor_line(line: str, pin: dict) -> str:
    old, new = pin["from"], pin["to"]
    line = line.replace(old["sha"], new["sha"])
    return re.sub(r"(#\s*)" + re.escape(old["action"]) + r"(?![\d.])",
                  lambda m: m.group(1) + new["action"], line, count=1)


def _pins_test_text(text: str, pin: dict) -> str:
    old, new = pin["from"], pin["to"]
    lines = text.splitlines(keepends=True)
    first, close = _one_block(lines, "VENDOR_INSTALLS = {", str(PINS_TEST))
    row = re.compile(r'^\s*"([0-9a-f]{40})":\s*"[\d.]+",')
    for i, line in enumerate(lines):
        if old["sha"] in line and not (first < i < close and row.match(line)):
            lines[i] = _vendor_line(line, pin)
    if not any(new["sha"] in lines[i] for i in range(first + 1, close)):
        indent = "        "
        for i in range(close - 1, first, -1):
            if row.match(lines[i]):
                indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
                break
        lines.insert(close,
                     f'{indent}"{new["sha"]}": "{new["claude_code"]}",  # {new["action"]}\n')
    return "".join(lines)


def _known_models(root: Path) -> set[str]:
    """Every id the root's `config/models.yaml` knows — the ladders, `retired`
    and `excluded`, the same union `model_fallback.KNOWN_MODELS` is. Empty when
    the file cannot be read, which files the row ahead of adoption."""
    import yaml

    try:
        config = yaml.safe_load((root / "config" / "models.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return set()
    known: set[str] = set()
    for rungs in (config.get("ladders") or {}).values():
        for rung in rungs or []:
            known.add(str(rung.get("model") if isinstance(rung, Mapping) else rung))
    for key in ("retired", "excluded"):
        known |= {str(model) for model in (config.get(key) or [])}
    return known


def _support_test_text(text: str, pin: dict, today: str, known: set[str]) -> str:
    """The model's floor in `tests/test_model_cli_support.py`: its
    MINIMUM_CLAUDE_CODE row when the config knows it, else its AHEAD_OF_ADOPTION
    row — the adoption that follows this raise is what puts it on a ladder."""
    old, new, model = pin["from"], pin["to"], pin["model"]
    lines = text.splitlines(keepends=True)
    blocks = [_one_block(lines, f"{name} = {{", str(SUPPORT_TEST))
              for name in ("MINIMUM_CLAUDE_CODE", "AHEAD_OF_ADOPTION")]
    row = re.compile(r'^(\s*)"' + re.escape(model) + r'":\s*"[\d.]+",(.*)$', re.S)
    for first, close in blocks:
        for i in range(first + 1, close):
            match = row.match(lines[i])
            if match:
                lines[i] = f'{match.group(1)}"{model}": "{new["claude_code"]}",{match.group(2)}'
                return "".join(lines)
    _, close = blocks[0] if model in known else blocks[1]
    indent = "    "
    lines[close:close] = [
        f"{indent}# Raised by claude_code_pin.py apply on {today} (DRE-5121): Claude\n",
        f"{indent}# Code {old['claude_code']} does not run it at full strength.\n",
        f'{indent}"{model}": "{new["claude_code"]}",\n',
    ]
    return "".join(lines)


def apply(pin, *, root=ROOT, today: str) -> list[Path]:
    """Raise the pin in the tree at `root`. Returns the files changed.

    Every edit is computed before anything is written, and the result is
    re-read afterwards: a tree that does not then pin exactly `to` is put back
    and refused, so a half-raised pin is never left behind.
    """
    _require_found(pin)
    if not _DATE.match(str(today or "")):
        raise Refused(f"{today!r} is not a YYYY-MM-DD date")
    root = Path(root)
    old, new = dict(pin["from"]), dict(pin["to"])
    current = pinned(root)
    if current != old:
        raise Refused(f"the tree pins {current}, not the record's `from` {old} — the "
                      "record is stale; run latest-supporting again")
    if (version_key(new["claude_code"]) <= version_key(old["claude_code"])
            or version_key(new["action"]) <= version_key(old["action"])):
        raise Refused(f"{old} → {new} is not a raise")

    edits: dict[Path, str] = {}
    installer = root / INSTALLER
    edits[installer] = _installer_text(installer.read_text(), pin, today)
    for path in check_action_pins.iter_files(
            [root / ".github" / "workflows", root / ".github" / "actions"]):
        text = edits.get(path, path.read_text())
        if old["sha"] in text:
            edits[path] = "".join(
                _vendor_line(line, pin) if old["sha"] in line else line
                for line in text.splitlines(keepends=True))
    pins_test = root / PINS_TEST
    edits[pins_test] = _pins_test_text(pins_test.read_text(), pin)
    support_test = root / SUPPORT_TEST
    edits[support_test] = _support_test_text(support_test.read_text(), pin, today,
                                             _known_models(root))
    for path in sorted((root / "tests").glob("*.py")):
        if path in edits:
            continue
        text = path.read_text()
        if old["sha"] in text:
            edits[path] = "".join(
                _vendor_line(line, pin) if old["sha"] in line else line
                for line in text.splitlines(keepends=True))

    originals = {path: path.read_text() for path in edits}
    changed = [path for path, text in edits.items() if text != originals[path]]
    for path in changed:
        path.write_text(edits[path])
    try:
        after = pinned(root)
    except Refused:
        after = None
    if after != new:
        for path in changed:
            path.write_text(originals[path])
        raise Refused(f"the raise did not leave the tree pinned at {new} (read {after}); "
                      "nothing was changed")
    return sorted(changed)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _options(argv, allowed) -> tuple[list[str], dict]:
    positional, opts = [], {}
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg in allowed:
            if not rest:
                raise Refused(f"{arg} needs a value")
            opts[arg] = rest.pop(0)
        elif arg.startswith("--"):
            raise Refused(f"unknown option {arg!r}")
        else:
            positional.append(arg)
    return positional, opts


def _model_arg(positional, cmd) -> str:
    if len(positional) != 1 or not _MODEL_ID.match(positional[0]):
        raise Refused(f"{cmd} needs one model id")
    return positional[0]


def _limits_opt(opts, model) -> tuple[dict | None, str]:
    if "--limits" not in opts:
        return None, ""
    limits, why = load_limits(opts["--limits"], model)
    return limits, why or ("" if limits else "the limits file holds no limits")


def main(argv: list[str], http=None) -> int:
    """Exit 0 when a question was answered (yes, no or unknown alike) or a
    text rendered or a raise applied; exit 2 on a malformed call or a refusal."""
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: claude_code_pin.py {{{'|'.join(COMMANDS)}}} …", file=sys.stderr)
        return 2
    cmd, *rest = argv
    http = http or _http_get
    try:
        if cmd in ("supports", "latest-supporting"):
            positional, opts = _options(rest, ("--root", "--limits"))
            model = _model_arg(positional, cmd)
            limits, why = _limits_opt(opts, model)
            root = opts.get("--root", ROOT)
            if cmd == "supports":
                got = supports(model, root=root, http=http, limits=limits, limits_why=why)
                sys.stdout.write(github_output.render(
                    [("answer", got["answer"]), ("version", got["version"]),
                     ("why", got["why"])]))
            else:
                got = latest_supporting(model, root=root, http=http, limits=limits,
                                        limits_why=why)
                print(json.dumps(got, indent=2))
            print(f"{cmd} {model}: {got['answer']} — {got['why']}", file=sys.stderr)
            return 0
        if cmd == "limits":
            positional, _ = _options(rest, ())
            model = _model_arg(positional, cmd)
            try:
                got = {"model": model, **model_limits(model, http=http)}
            except ReadFailed as exc:
                got = {"model": model, "unknown": str(exc)}
            print(json.dumps(got))
            return 0
        if cmd == "render":
            positional, opts = _options(rest, ("--pin", "--trial"))
            if len(positional) != 1 or positional[0] not in RENDER_TARGETS:
                raise Refused(f"render needs one target of {list(RENDER_TARGETS)}")
            if "--pin" not in opts:
                raise Refused("render needs --pin <pin.json>")
            text = render(positional[0], load_pin(opts["--pin"]),
                          load_trial(opts.get("--trial")))
            print(text.rstrip("\n"))
            return 0
        # apply
        positional, opts = _options(rest, ("--pin", "--root", "--date"))
        if positional or "--pin" not in opts:
            raise Refused("apply takes --pin <pin.json> [--root DIR] [--date YYYY-MM-DD]")
        from datetime import datetime, timezone

        today = opts.get("--date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        root = Path(opts.get("--root", ROOT))
        changed = apply(load_pin(opts["--pin"]), root=root, today=today)
        for path in changed:
            print(path.relative_to(root))
        return 0
    except (OSError, ValueError) as exc:
        print(f"::error::{cmd} refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
