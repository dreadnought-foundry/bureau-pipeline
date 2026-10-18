#!/usr/bin/env python3
"""The notice door's client: a pipeline writer raises a console alert, not a card.

Nine writers in this repository — the medic's diagnosis, red-main repair's two
escalations, the nightly watch, the model trial, the switch watch, the groom
stall, the fleet reviewer outage and the standards sync — each used to file a
Linear card to say something went wrong. Each is repointed, by its own card,
at one console system alert per notice key, raised and resolved through this
module. It is the one client for that door, so none of them invents its own
HTTP call. Stdlib only, shaped like `bureau_read.py`: it runs inside every
reusable workflow.

## The door

`POST $BUREAU_READ_URL/api/v1/pipeline/notices` — the console base URL
`bureau_read.py` already reads — with a JSON body and
`Authorization: Bearer <GitHub Actions OIDC JWT>`, minted fresh for each call
for the audience `$BUREAU_NOTICE_AUDIENCE` (default `DEFAULT_AUDIENCE`, which
the console's `PIPELINE_NOTICE_AUDIENCE` defaults to as well). The door
answers 200 with `{"kind": "pipeline_notice:<key>", "outcome": <outcome>}`,
the outcome one of `OUTCOMES`; 400, 401, 403 and 503 are its refusals.

## It never goes silent

When the door cannot be reached, refuses, answers anything but a well-formed
200, or the job has no OIDC token or no `BUREAU_READ_URL`, a raise files the
card its writer filed before — the `Fallback` it was handed: the exact title
looked up with `linear_ops.find_open`, commented on when it is already open
and created with `linear_ops.create_card` when it is not, the body carrying
one added line that says the console door was not reached and why. A search
that fails files NOTHING: a card minted on a search that never answered is
the duplicate the search exists to prevent (medic.yml's rule). So until the
door is live, or whenever it is down, a writer behaves exactly as it did. An
open card already at Linear's comment limit takes nothing
(`linear_ops.COMMENT_CAP_CONDITION`), so that raise is `lost comment-capped`,
never reported as filed — and no second card is minted beside it, because
every later search would find the full one again.

A raise handed no fallback that the door could not take is `lost`: nothing is
filed and the CLI exits non-zero. Only the smoke workflow raises that way. A
resolve the door refuses is logged as `resolve-refused` and nothing else — a
resolve never falls back to a card, because there is nothing to report.

## It cuts long text to fit

The door answers an over-long text with a 400, and without this rule every
long diagnosis, escalation or run list would be filed as the fallback card,
quietly undoing the repoint. So before it sends, the client cuts `title` and
`recovery_title` to `TITLE_LIMIT` characters (the first 199 and `…`) and
`body` and `recovery_body` to `BODY_LIMIT` (the first 7900, a blank line and
`CUT_NOTE`), writing each full text it cut to stderr first so the run's log
keeps it. A fallback card carries the full text uncut. No writer measures its
own text.

## The one line

Exactly one line per call on stdout, under `TAG`:

    pipeline-notice: <outcome> kind=pipeline_notice:<key>
    pipeline-notice: fallback-card <reason> kind=pipeline_notice:<key> card=<DRE-n>
    pipeline-notice: lost <reason> kind=pipeline_notice:<key>
    pipeline-notice: resolve-refused <reason> kind=pipeline_notice:<key>

`config/proof-reach.json` declares it, so a proof run may read it. Every other
word this module says — a refusal's detail, a cut text — goes to stderr, and
so does whatever `linear_ops` prints while filing a fallback card.

## Usage

    python3 scripts/pipeline_notice.py raise --notice <key> --severity <WARN|CRITICAL> \\
        --title <t> --body-file <f> [--action-url <u> --action-label <l>] \\
        [--fallback-title <t> --fallback-lane <lane> [--fallback-label <x> ...] \\
         [--fallback-repo <slug>]]
    python3 scripts/pipeline_notice.py resolve --notice <key> \\
        [--recovery-title <t> --recovery-body-file <f>]

`--fallback-repo` names the card's `repo:<slug>` when its labels do not; with
neither, the slug is this run's repository (`GITHUB_REPOSITORY`, lowercased,
without its owner).
"""
from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import os
import re
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Mapping

TAG = "pipeline-notice:"
PATH = "/api/v1/pipeline/notices"
#: The one audience both sides default to: this client asks GitHub for it, and
#: the console's `PIPELINE_NOTICE_AUDIENCE` defaults to it.
DEFAULT_AUDIENCE = "https://app.agent-bureau.com/pipeline-notice"
URL_ENV = "BUREAU_READ_URL"
AUDIENCE_ENV = "BUREAU_NOTICE_AUDIENCE"

KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
SEVERITIES = ("WARN", "CRITICAL")
#: What a 200 may say. Anything else is a malformed answer.
OUTCOMES = frozenset({"raised", "repeat", "escalated", "resolved", "not-open"})

#: The contract's text limits, and the cut that keeps a text inside them.
TITLE_LIMIT = 200
BODY_LIMIT = 8000
BODY_KEPT = 7900
CUT_NOTE = "… [cut to fit the console; the full text is in this run's log]"

#: One attempt: connect within CONNECT_TIMEOUT, the whole exchange within
#: TOTAL_TIMEOUT. Module constants so the tests can shrink them.
CONNECT_TIMEOUT = 5.0
TOTAL_TIMEOUT = 15.0

#: The lane `linear_ops.create_card` defaults to, for a fallback naming none.
DEFAULT_LANE = "Planning"
#: The lanes a fallback card may be filed in: the two the repointed writers
#: filed theirs in — Planning (`linear_ops.py create`'s default) and Triage
#: (red-main repair, the groom stall, the reviewer outage). A notice card is
#: never ready work, so any other lane is refused before a request is made.
FALLBACK_LANES = (DEFAULT_LANE, "Triage")
#: The flag the CLI's fallback labels arrive under. `label_writers.py` reads
#: this constant to find the labels a workflow hands this command line, so a
#: writer that moves its card here does not move its labels out of sight.
LABEL_FLAG = "--fallback-label"


@dataclass(frozen=True)
class Fallback:
    """The card a writer filed before the door existed: filed again, exactly,
    when the door cannot take the notice."""

    title: str
    lane: str = DEFAULT_LANE
    labels: tuple = ()
    repo: str | None = None
    #: The stable start of a title that carries counts (the fleet outage's
    #: `Reviewer down since …`): when set, the open card is found by this
    #: prefix rather than the exact title, so a title whose numbers moved
    #: between raises comments on the card it filed instead of filing another.
    dedupe_prefix: str | None = None


def destinations() -> tuple:
    """The lanes a fallback card can land in, published for
    `ready_lane_writers.py`, which cannot read a lane the caller hands in."""
    return FALLBACK_LANES


class DoorMissed(Exception):
    """The door did not take the call. `reason` is the one word the log line
    carries (`no-url`, `bad-url`, `no-token`, `timeout`, `unreachable`,
    `http-<status>`, `malformed`); the detail goes to stderr."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _say(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _line(text: str) -> None:
    print(f"{TAG} {text}", flush=True)


def _kind(notice: str) -> str:
    return f"pipeline_notice:{notice}"


def _check_key(notice: str) -> None:
    if not isinstance(notice, str) or not KEY_PATTERN.match(notice):
        raise ValueError(f"notice key {notice!r} does not match {KEY_PATTERN.pattern}")


# ── cutting to fit ──────────────────────────────────────────────────────────


def cut_title(text: str) -> str:
    """A title or recovery title, at most TITLE_LIMIT characters."""
    if len(text) <= TITLE_LIMIT:
        return text
    return text[:TITLE_LIMIT - 1] + "…"


def cut_body(text: str) -> str:
    """A body or recovery body, at most BODY_LIMIT characters."""
    if len(text) <= BODY_LIMIT:
        return text
    return text[:BODY_KEPT] + "\n\n" + CUT_NOTE


def _fit(notice: str, field: str, text: str | None, cut) -> str | None:
    """`text` cut to fit, with the full text written to stderr first when the
    cut changed it, so the run's log keeps what the console does not."""
    if text is None:
        return None
    fitted = cut(text)
    if fitted != text:
        _say(f"notice-door: {field} for kind={_kind(notice)} is {len(text)} characters "
             f"and is sent cut to fit the console; the full text follows")
        _say(f"----- BEGIN full {field} -----")
        _say(text)
        _say(f"----- END full {field} -----")
    return fitted


# ── the OIDC token ──────────────────────────────────────────────────────────


def _mint() -> str:
    """A fresh token for one request: the console claims each `jti` once."""
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not request_token:
        raise DoorMissed("no-token", "no ACTIONS_ID_TOKEN_REQUEST_URL — the job does "
                         "not grant id-token: write")
    audience = os.environ.get(AUDIENCE_ENV) or DEFAULT_AUDIENCE
    joined = "&" if "?" in url else "?"
    req = urllib.request.Request(
        f"{url}{joined}audience={urllib.parse.quote(audience, safe='')}",
        headers={"Authorization": f"Bearer {request_token}",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TOTAL_TIMEOUT) as resp:  # nosec B310
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            said = e.read().decode("utf-8", "replace").strip()[:300]
        except Exception:  # noqa: BLE001 — the status alone still names it
            said = ""
        raise DoorMissed("no-token", f"the runner's OIDC endpoint answered {e.code}"
                         + (f": {said}" if said else "")) from e
    except Exception as e:  # noqa: BLE001 — any failure to mint is a miss
        raise DoorMissed("no-token", f"minting failed: {e}") from e
    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value:
        raise DoorMissed("no-token", "the OIDC endpoint returned no token value")
    return value


# ── the request ─────────────────────────────────────────────────────────────


def _base() -> urllib.parse.SplitResult:
    raw = (os.environ.get(URL_ENV) or "").strip()
    if not raw:
        raise DoorMissed("no-url", f"{URL_ENV} is not set")
    parts = urllib.parse.urlsplit(raw)
    loopback = parts.hostname in ("127.0.0.1", "localhost", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
        # The token is a bearer credential: never sent in clear text, except
        # to a door on this machine.
        raise DoorMissed("bad-url", f"{URL_ENV} must be https (got "
                         f"{parts.scheme}://{parts.hostname})")
    return parts


def _http_post(base: urllib.parse.SplitResult, payload: dict,
               headers: dict) -> tuple[int, bytes]:
    """One POST, one attempt, bounded as `bureau_read._http_get` is: the
    TOTAL bound is a join, because a socket timeout bounds each recv and a
    door dripping a byte a second would never trip it."""
    target = base.path.rstrip("/") + PATH
    if base.scheme == "https":
        conn = http.client.HTTPSConnection(
            base.hostname, base.port or 443, timeout=CONNECT_TIMEOUT,
            context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(base.hostname, base.port or 80,
                                          timeout=CONNECT_TIMEOUT)
    data = json.dumps(payload).encode()
    outcome: dict = {}

    def exchange() -> None:
        try:
            conn.connect()
            conn.sock.settimeout(TOTAL_TIMEOUT)
            conn.request("POST", target, body=data, headers=headers)
            resp = conn.getresponse()
            outcome["answer"] = (resp.status, resp.read())
        except BaseException as e:  # noqa: BLE001 — handed to the caller below
            outcome["error"] = e

    worker = threading.Thread(target=exchange, daemon=True, name="pipeline-notice")
    worker.start()
    worker.join(TOTAL_TIMEOUT)
    if worker.is_alive():
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — best effort
            pass
        raise TimeoutError(f"no complete answer within {TOTAL_TIMEOUT:g}s")
    conn.close()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["answer"]


def _send(payload: dict) -> str:
    """The door's outcome for `payload`, or DoorMissed."""
    base = _base()
    token = _mint()
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json",
               "Accept": "application/json"}
    try:
        status, raw = _http_post(base, payload, headers)
    except TimeoutError as e:
        raise DoorMissed("timeout", str(e)) from e
    except Exception as e:  # noqa: BLE001 — every other transport failure
        raise DoorMissed("unreachable", f"{type(e).__name__}: {e}") from e
    if status != 200:
        detail = raw.decode("utf-8", "replace").strip()[:300]
        raise DoorMissed(f"http-{status}", detail or "no body")
    try:
        answer = json.loads(raw.decode())
    except ValueError as e:
        raise DoorMissed("malformed", "the 200 was not JSON") from e
    outcome = answer.get("outcome") if isinstance(answer, dict) else None
    if outcome not in OUTCOMES:
        raise DoorMissed("malformed", f"the 200 carried outcome {outcome!r}")
    return outcome


# ── the fallback card ───────────────────────────────────────────────────────


def _as_fallback(value) -> Fallback | None:
    if value is None or isinstance(value, Fallback):
        return value
    if isinstance(value, Mapping):
        fields = dict(value)
        fields["labels"] = tuple(fields.get("labels") or ())
        return Fallback(**fields)
    raise TypeError(f"fallback must be a Fallback or a mapping, not {type(value).__name__}")


def _repo_slug(fallback: Fallback) -> str:
    """The card's `repo:<slug>`: named, or read off its labels, or this run's."""
    if fallback.repo:
        return fallback.repo
    for label in fallback.labels:
        if label.startswith("repo:"):
            return label[len("repo:"):]
    repository = os.environ.get("GITHUB_REPOSITORY") or ""
    return repository.rsplit("/", 1)[-1].lower()


def _description(body: str, missed: DoorMissed) -> str:
    return (f"{body.rstrip()}\n\n_The console door was not reached ({missed.reason}"
            f"{': ' + missed.detail if missed.detail else ''}), so this notice was "
            "filed as a card, as it was before the console carried notices._")


def _file_card(notice: str, fallback: Fallback, body: str, missed: DoorMissed) -> str:
    """`fallback-card` with the card it went to, or `lost` — one line either way."""
    # linear_ops narrates its writes on stdout; the one line on stdout is the
    # client's, so that narration goes to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        card, failed = _file_card_quietly(fallback, _description(body, missed))
    if failed:
        return _lost(notice, failed)
    _line(f"fallback-card {missed.reason} kind={_kind(notice)} card={card}")
    return "fallback-card"


def _file_card_quietly(fallback: Fallback, description: str) -> tuple[str | None, str | None]:
    """(the card the notice went to, None), or (None, the word for why not)."""
    import linear_ops  # noqa: PLC0415 — Linear only on the path that needs it

    searched = fallback.dedupe_prefix or fallback.title
    try:
        if fallback.dedupe_prefix:
            found = linear_ops.find_open_prefix(fallback.dedupe_prefix)
            existing = found["identifier"] if found else None
        else:
            existing = linear_ops.find_open(fallback.title)
    except Exception as e:  # noqa: BLE001 — an unanswered search files nothing
        _say(f"notice-door: the duplicate search for {searched!r} failed "
             f"({e}); filing nothing")
        return None, "search-failed"
    try:
        if existing:
            capped = linear_ops.cmd_comment(existing, description)
            if capped:
                _say(f"notice-door: {existing} cannot take the notice ({capped}); "
                     "filing nothing")
                return None, "comment-capped"
            return existing, None
        issue = linear_ops.create_card(
            fallback.title, description, repo_slug=_repo_slug(fallback),
            labels=list(fallback.labels), lane=fallback.lane)
        return issue["identifier"], None
    except Exception as e:  # noqa: BLE001 — named by the caller, never raised past
        _say(f"notice-door: filing the fallback card failed ({e})")
        return None, "comment-failed" if existing else "create-failed"


def _lost(notice: str, reason: str) -> str:
    _line(f"lost {reason} kind={_kind(notice)}")
    return "lost"


# ── the two calls ───────────────────────────────────────────────────────────


def raise_notice(notice: str, *, severity: str, title: str, body: str,
                 action_url: str | None = None, action_label: str | None = None,
                 fallback: Fallback | Mapping | None = None) -> str:
    """Raise `notice`. Returns the door's outcome, or `fallback-card` when the
    writer's old card was filed instead, or `lost` when nothing could be."""
    _check_key(notice)
    if severity not in SEVERITIES:
        raise ValueError(f"severity {severity!r} is not one of {'/'.join(SEVERITIES)}")
    fallback = _as_fallback(fallback)
    if fallback is not None and fallback.lane not in FALLBACK_LANES:
        raise ValueError(f"fallback lane {fallback.lane!r} is not one of "
                         f"{'/'.join(FALLBACK_LANES)}")
    payload = {
        "action": "raise",
        "notice": notice,
        "severity": severity,
        "title": _fit(notice, "title", title, cut_title),
        "body": _fit(notice, "body", body, cut_body),
        "action_url": action_url,
        "action_label": action_label,
    }
    try:
        outcome = _send(payload)
    except DoorMissed as missed:
        _say(f"notice-door: the raise of kind={_kind(notice)} was not taken — "
             f"{missed.reason}: {missed.detail}")
        if fallback is None:
            return _lost(notice, missed.reason)
        return _file_card(notice, fallback, body, missed)
    _line(f"{outcome} kind={_kind(notice)}")
    return outcome


def resolve_notice(notice: str, *, recovery_title: str | None = None,
                   recovery_body: str | None = None) -> str:
    """Resolve `notice`, with the all-clear copy when given. Returns the
    door's outcome, or `resolve-refused` — logged, and nothing else."""
    _check_key(notice)
    if recovery_body is not None and recovery_title is None:
        raise ValueError("a recovery body needs a recovery title")
    payload = {"action": "resolve", "notice": notice}
    if recovery_title is not None:
        payload["recovery_title"] = _fit(notice, "recovery_title", recovery_title, cut_title)
    if recovery_body is not None:
        payload["recovery_body"] = _fit(notice, "recovery_body", recovery_body, cut_body)
    try:
        outcome = _send(payload)
    except DoorMissed as missed:
        _say(f"notice-door: the resolve of kind={_kind(notice)} was not taken — "
             f"{missed.reason}: {missed.detail}")
        _line(f"resolve-refused {missed.reason} kind={_kind(notice)}")
        return "resolve-refused"
    _line(f"{outcome} kind={_kind(notice)}")
    return outcome


# ── the CLI ─────────────────────────────────────────────────────────────────


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _flag_values(flags, name: str) -> list:
    """Every value given as `<name> <value>` in `flags`, in order."""
    return [flags[i + 1] for i in range(len(flags) - 1) if flags[i] == name]


def _raise_parser() -> argparse.ArgumentParser:
    up = argparse.ArgumentParser(prog="pipeline_notice.py raise",
                                 description="raise a console alert, or file the old card")
    up.add_argument("--notice", required=True)
    up.add_argument("--severity", required=True, choices=SEVERITIES)
    up.add_argument("--title", required=True)
    up.add_argument("--body-file", required=True)
    up.add_argument("--action-url")
    up.add_argument("--action-label")
    up.add_argument("--fallback-title")
    up.add_argument("--fallback-lane", default=DEFAULT_LANE)
    up.add_argument(LABEL_FLAG, action="append", default=[])
    up.add_argument("--fallback-repo")
    return up


def _resolve_parser() -> argparse.ArgumentParser:
    down = argparse.ArgumentParser(prog="pipeline_notice.py resolve",
                                   description="resolve a console alert")
    down.add_argument("--notice", required=True)
    down.add_argument("--recovery-title")
    down.add_argument("--recovery-body-file")
    return down


def cmd_raise(*flags: str) -> int:
    args = _raise_parser().parse_args(flags)
    fallback = None
    if args.fallback_title:
        # The labels are read off the flags themselves rather than argparse's
        # namespace, so `label_writers.py` can follow each one from the
        # workflow that names it to the card (DRE-6362).
        labels = _flag_values(flags, LABEL_FLAG)
        fallback = Fallback(title=args.fallback_title, lane=args.fallback_lane,
                            labels=tuple(labels), repo=args.fallback_repo)
    try:
        outcome = raise_notice(
            args.notice, severity=args.severity, title=args.title,
            body=_read(args.body_file), action_url=args.action_url,
            action_label=args.action_label, fallback=fallback)
    except (ValueError, OSError) as e:
        _say(f"notice-door: refused before any request — {e}")
        return 2
    return 1 if outcome == "lost" else 0


def cmd_resolve(*flags: str) -> int:
    args = _resolve_parser().parse_args(flags)
    try:
        recovery_body = (_read(args.recovery_body_file)
                         if args.recovery_body_file else None)
        resolve_notice(args.notice, recovery_title=args.recovery_title,
                       recovery_body=recovery_body)
    except (ValueError, OSError) as e:
        _say(f"notice-door: refused before any request — {e}")
        return 2
    return 0


#: The command line, by subcommand. A dict of handlers, the shape
#: `label_writers.py` reads a command line in.
COMMANDS = {"raise": cmd_raise, "resolve": cmd_resolve}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] not in COMMANDS:
        _say("usage: pipeline_notice.py raise|resolve --notice <key> … (see the "
             "module docstring)")
        return 2
    return COMMANDS[argv[0]](*argv[1:])


if __name__ == "__main__":
    sys.exit(main())
