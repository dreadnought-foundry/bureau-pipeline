"""The agent-log scrub (DRE-4268) — the one piece where a mistake leaks a credential.

An agent's working log leaves the runner for six months of storage (epic
DRE-4267). Before it does, `scripts/scrub_agent_log.py` removes every secret
the job was given — by VALUE, in each form a tool can print it — and then
anything SHAPED like a token or key. These tests are adversarial on purpose:
each one plants a credential the way a real run would expose it and asserts it
is gone from what would be uploaded.

Three properties matter as much as the removals:

  * the COUNTER-TEST — a log with no secrets comes out byte-identical, so the
    scrub is not passing by mangling everything;
  * REFUSAL — a file that cannot be decoded, or is over the cap, exits non-zero
    and writes NOTHING. Silence is never success: an unscanned file is never
    uploaded;
  * the summary line names counts and secret NAMES only — never a fragment of a
    value.

Every planted credential below is fabricated for this file.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "scrub_agent_log.py"
sys.path.insert(0, str(SCRIPTS))

import scrub_agent_log  # noqa: E402
import secret_shapes  # noqa: E402

# Two fabricated credentials. Neither matches a shape pattern, so pass 1 (by
# value) is what must catch them. Every planted value in this file is ASSEMBLED
# AT RUN TIME from fragments: GitHub's push protection scans source for
# contiguous token-shaped literals and refused the first version of this file —
# correctly, by its lights — over a made-up value that looked like a legacy
# installation token.
APP_TOKEN = "-".join(["inst", "4f9c2e7a1b8d4c6e", "9f0a3b5d7c1e2f4a", "6b8c0d9e"])
LINEAR_KEY = "-".join(["lk", "7Hq2mZx9Wd4Rt6Yp", "1Ns8Bv3Cf5Gj0KaE"])
SECRETS = {"BUREAU_APP_TOKEN": APP_TOKEN, "LINEAR_API_KEY": LINEAR_KEY}


def run(tmp_path: Path, files: dict[str, bytes], secrets: dict[str, str] | None = None,
        extra: list[str] | None = None):
    """Run the script the way the workflow step will: files as arguments, the
    secret values on STDIN (arguments reach the process table), one out dir."""
    src = tmp_path / "in"
    out = tmp_path / "out"
    src.mkdir(exist_ok=True)
    paths = []
    for name, content in files.items():
        (src / name).write_bytes(content)
        paths.append(str(src / name))
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(out), *(extra or []), *paths],
        input=json.dumps(SECRETS if secrets is None else secrets),
        capture_output=True, text=True, check=False)
    return done, out


def transcript(*texts: str) -> bytes:
    """The shape of `claude-execution-output.json`: a JSON list of turns. Built
    with json.dumps so a planted value is escaped exactly as the real file
    escapes it."""
    return json.dumps(
        [{"type": "assistant", "message": {"content": [{"type": "text", "text": t}]}}
         for t in texts], indent=1).encode()


# --- pass 1: by value ----------------------------------------------------------


def test_a_planted_secret_value_is_absent_from_the_output(tmp_path):
    done, out = run(tmp_path, {"log.json": transcript(f"export TOKEN={APP_TOKEN}", "ok")})
    assert done.returncode == 0, done.stderr
    scrubbed = (out / "log.json").read_text()
    assert APP_TOKEN not in scrubbed
    assert "«redacted:BUREAU_APP_TOKEN»" in scrubbed
    json.loads(scrubbed)  # still the JSON it was


def test_the_value_inside_a_git_remote_url_is_removed(tmp_path):
    line = f"remote: https://x-access-token:{APP_TOKEN}@github.com/o/r.git"
    done, out = run(tmp_path, {"step.log": line.encode()})
    assert done.returncode == 0, done.stderr
    assert APP_TOKEN not in (out / "step.log").read_text()


def test_the_base64_of_the_value_is_removed(tmp_path):
    encoded = base64.b64encode(APP_TOKEN.encode()).decode()
    done, out = run(tmp_path, {"step.log": f"Authorization: Basic {encoded}".encode()})
    assert done.returncode == 0, done.stderr
    assert encoded not in (out / "step.log").read_text()


@pytest.mark.parametrize("prefix", ["x-access-token:", "u:", "abc"])
def test_the_value_inside_a_longer_base64_payload_is_removed(tmp_path, prefix):
    """The real exposure: `git` sends base64("x-access-token:" + TOKEN), which
    contains NO substring equal to base64(TOKEN) unless the prefix length is a
    multiple of three. The three alignments are what catch it."""
    encoded = base64.b64encode(f"{prefix}{APP_TOKEN}".encode()).decode()
    done, out = run(tmp_path, {"step.log": f"AUTHORIZATION: basic {encoded}".encode()})
    assert done.returncode == 0, done.stderr
    scrubbed = (out / "step.log").read_text()
    assert "«redacted:BUREAU_APP_TOKEN»" in scrubbed
    # The characters of the payload that encode the token's own bytes: from the
    # first character touching the token to the end. No 8-character window of
    # that region may survive — 8 base64 characters are 6 bytes of the token.
    first = (len(prefix) * 8) // 6
    region = encoded.rstrip("=")[first:]
    survivors = [region[i:i + 8] for i in range(len(region) - 7)
                 if region[i:i + 8] in scrubbed]
    assert survivors == []


def test_the_url_encoded_value_is_removed(tmp_path):
    secret = "p@ss/w0rd+with&chars=9Zq"
    quoted = urllib.parse.quote(secret, safe="")
    done, out = run(tmp_path, {"step.log": f"GET /cb?key={quoted}".encode()},
                    secrets={"CALLBACK_KEY": secret})
    assert done.returncode == 0, done.stderr
    assert quoted not in (out / "step.log").read_text()


def test_a_multi_line_secret_is_removed_in_its_json_escaped_form(tmp_path):
    """A PEM-less multi-line secret inside the transcript is `\\n`-escaped there,
    so the raw value never matches."""
    secret = "line-one-7Hq2mZx9\nline-two-Wd4Rt6Yp\nline-three-1Ns8Bv3C"
    done, out = run(tmp_path, {"log.json": transcript(f"key is {secret} end")},
                    secrets={"MULTILINE": secret})
    assert done.returncode == 0, done.stderr
    scrubbed = (out / "log.json").read_text()
    assert "line-two-Wd4Rt6Yp" not in scrubbed


# --- pass 2: by shape ----------------------------------------------------------


_FILLER = "A1b2C3d4E5f6G7h8I9j0" * 4


@pytest.mark.parametrize("planted", [
    "".join(["sk", "-ant-", "oat01-", _FILLER[:48]]),
    "".join(["sk", "-ant-", "api03-", _FILLER[:48]]),
    "".join(["gh", "p_", _FILLER[:36]]),
    "".join(["gh", "s_", _FILLER[:36]]),
    "".join(["github", "_pat_", "11ABCDEFG0", _FILLER[:60]]),
    "".join(["AK", "IA", "IOSFODNN7", "EXAMPLE"]),
    "".join(["lin", "_api_", _FILLER[:40]]),
    "".join(["xo", "xb-", "1234567890-", _FILLER[:24]]),
    ".".join(["ey" + "J" + _FILLER[:20], "ey" + "J" + _FILLER[:30], _FILLER[:43]]),
], ids=["anthropic-oauth", "anthropic-api", "github-classic", "github-server",
        "github-fine-grained", "aws-key-id", "linear", "slack", "jwt"])
def test_a_token_shaped_string_nobody_supplied_is_removed(tmp_path, planted):
    done, out = run(tmp_path, {"step.log": f"found {planted} in env".encode()}, secrets={})
    assert done.returncode == 0, done.stderr
    scrubbed = (out / "step.log").read_text()
    assert planted not in scrubbed
    assert "«redacted-shape:" in scrubbed


_SHAPED = {
    "github-token": "".join(["gh", "p_", _FILLER[:36]]),
    "anthropic-key": "".join(["sk", "-ant-", "oat01-", _FILLER[:48]]),
    "aws-access-key-id": "".join(["AK", "IA", "IOSFODNN7", "EXAMPLE"]),
    "linear-key": "".join(["lin", "_api_", _FILLER[:40]]),
    "slack-token": "".join(["xo", "xb-", "1234567890-", _FILLER[:24]]),
    "jwt": ".".join(["ey" + "J" + _FILLER[:20], "ey" + "J" + _FILLER[:30], _FILLER[:43]]),
}


@pytest.mark.parametrize("shape", sorted(_SHAPED))
@pytest.mark.parametrize("depth", [1, 2], ids=["json-escaped", "escaped-twice"])
def test_a_token_at_the_start_of_a_line_inside_the_transcript_is_removed(tmp_path, shape, depth):
    """The position a tool actually prints a credential — alone on its own line —
    and inside the transcript that line break is the two characters `\\n`. `n` is
    a word character, so a pattern opening with `\\b` does not match there: the
    same token was caught raw and MISSED in the transcript until `_LEAD`.
    Escaped twice is the same token printed from inside a JSON file."""
    planted = _SHAPED[shape]
    text = f"export TOKEN=\n{planted}\tnext\r\n{planted}"
    for _ in range(depth - 1):
        text = json.dumps(text)
    done, out = run(tmp_path, {"log.json": transcript(text)}, secrets={})
    assert done.returncode == 0, done.stderr
    scrubbed = (out / "log.json").read_text()
    assert planted not in scrubbed
    assert scrubbed.count(f"«redacted-shape:{shape}»") == 2
    json.loads(scrubbed)


def test_every_shape_has_a_line_start_case():
    """A shape added without one is a shape nobody checked at the position that
    matters. The PEM pattern is line-oriented and has its own tests below."""
    assert sorted(_SHAPED) == sorted(set(secret_shapes.SHAPES) - {"pem-private-key"})


def test_a_key_printed_from_inside_a_json_file_is_removed(tmp_path):
    """`cat app-key.json`: the key's newlines are escaped once by the file and
    again by the transcript, so they reach the scrub as `\\\\n`."""
    inner = json.dumps({"private_key": _pem(_KEY_LINE, _KEY_LINE, "Aw=="), "app_id": 3350400})
    done, out = run(tmp_path, {"log.json": transcript(f"$ cat app-key.json\n{inner}\ndone")},
                    secrets={})
    assert done.returncode == 0, done.stderr
    scrubbed = (out / "log.json").read_text()
    assert _KEY_LINE not in scrubbed and "Aw==" not in scrubbed
    text = json.loads(scrubbed)[0]["message"]["content"][0]["text"]
    assert text.endswith("\ndone") and "3350400" in text


def test_a_multi_line_secret_printed_from_inside_a_json_file_is_removed(tmp_path):
    secret = "line-one-7Hq2mZx9\nline-two-Wd4Rt6Yp\nline-three-1Ns8Bv3C"
    inner = json.dumps({"MULTILINE": secret})
    done, out = run(tmp_path, {"log.json": transcript(f"$ cat env.json\n{inner}")},
                    secrets={"MULTILINE": secret})
    assert done.returncode == 0, done.stderr
    assert "line-two-Wd4Rt6Yp" not in (out / "log.json").read_text()


def test_a_pem_block_is_removed_raw_and_json_escaped(tmp_path):
    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7fabricated"
    dashes, kind = "-" * 5, " ".join(["RSA", "PRIVATE", "KEY"])
    pem = f"{dashes}BEGIN {kind}{dashes}\n{body}\n{body}\n{dashes}END {kind}{dashes}"
    done, out = run(tmp_path, {"step.log": f"cat key:\n{pem}\n".encode(),
                               "log.json": transcript(f"the key: {pem}")}, secrets={})
    assert done.returncode == 0, done.stderr
    for name in ("step.log", "log.json"):
        scrubbed = (out / name).read_text()
        assert body not in scrubbed, name
        assert "PRIVATE KEY" not in scrubbed, name
    json.loads((out / "log.json").read_text())


def test_the_shapes_are_read_from_the_one_shared_declaration():
    """Not a second list (the card's rule): the scrub uses `secret_shapes.SHAPES`
    itself, and `deliver_rescue.redact` — the only token pattern the pipeline had
    — now reads the same module."""
    import deliver_rescue

    assert scrub_agent_log.SHAPES is secret_shapes.SHAPES
    assert deliver_rescue._TOKEN_RE is secret_shapes.SHAPES["github-token"]


# --- the counter-test ------------------------------------------------------------


def test_a_file_with_no_secrets_is_byte_identical(tmp_path):
    clean = transcript(
        "Ran `pytest -q`: 412 passed. The sha is 9f8e7d6c5b4a39281706f5e4d3c2b1a0ffeeddcc.",
        "uuid 2fac5912-62ad-47a1-886b-a9433d255b45, arn:aws:s3:::bucket/record/v1/x.json.gz",
        "Unicode survives: «quoted», café, 日本語 — and a long base64-looking id "
        "QWxsIHdvcmsgYW5kIG5vIHBsYXkgbWFrZXMgSmFjayBhIGR1bGwgYm95Lg==")
    done, out = run(tmp_path, {"log.json": clean})
    assert done.returncode == 0, done.stderr
    assert (out / "log.json").read_bytes() == clean
    summary = json.loads(done.stdout.strip().splitlines()[-1])
    assert summary["by_value"] == {} and summary["by_shape"] == {}


# --- refusal ----------------------------------------------------------------------


def test_undecodable_bytes_exit_non_zero_and_write_nothing(tmp_path):
    done, out = run(tmp_path, {"good.log": b"fine", "bad.log": b"\xff\xfe\x00broken\x80"})
    assert done.returncode != 0
    assert not out.exists() or not any(out.iterdir()), "a refused run wrote output"
    assert "bad.log" in done.stderr


def test_an_over_cap_file_exits_non_zero_and_writes_nothing(tmp_path):
    done, out = run(tmp_path, {"big.log": b"a" * 2048}, extra=["--max-bytes", "1024"])
    assert done.returncode != 0
    assert not out.exists() or not any(out.iterdir())
    assert "1024" in done.stderr


def test_secrets_that_are_not_a_json_object_of_strings_are_refused(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.log").write_text("x")
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(tmp_path / "out"), str(src / "a.log")],
        input="not json", capture_output=True, text=True, check=False)
    assert done.returncode != 0
    assert not (tmp_path / "out").exists()


def test_a_file_that_stats_but_cannot_be_read_is_a_clean_refusal(tmp_path):
    """The real case on a self-hosted runner is a log a container wrote as another
    uid; a directory with a log's name reaches the same branch without needing
    root to build. A refusal by name and reason — exit 2, never a traceback that
    prints the full path (DRE-4268 review, blocking 3)."""
    src = tmp_path / "in"
    (src / "dir.log").mkdir(parents=True)
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(tmp_path / "out"), str(src / "dir.log")],
        input="{}", capture_output=True, text=True, check=False)
    assert done.returncode == scrub_agent_log.REFUSED
    assert "Traceback" not in done.stderr
    assert "dir.log: cannot be read" in done.stderr
    assert str(src) not in done.stderr
    assert not (tmp_path / "out").exists()


def test_two_inputs_with_one_basename_are_refused_not_overwritten(tmp_path):
    """`--out-dir` is keyed on the basename alone, so without the guard the second
    `claude-execution-output.json` silently overwrites the first and one of two
    records is lost with exit 0 (DRE-4268 review, blocking 2)."""
    for sub in ("a", "b"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "log.json").write_text(f"from {sub}")
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(tmp_path / "out"),
         str(tmp_path / "a" / "log.json"), str(tmp_path / "b" / "log.json")],
        input="{}", capture_output=True, text=True, check=False)
    assert done.returncode == scrub_agent_log.REFUSED
    assert "share a basename" in done.stderr
    assert not (tmp_path / "out").exists()


def test_a_secret_name_that_is_not_an_identifier_is_refused(tmp_path):
    """The name is the one piece of caller text that reaches the output, inside
    the marker; a quote or newline in it would break a scrubbed transcript's JSON."""
    done, out = run(tmp_path, {"a.log": b"x"}, secrets={'BAD"NAME\n': "a-long-enough-value"})
    assert done.returncode == scrub_agent_log.REFUSED
    assert not out.exists()


# --- the last check: verify() ------------------------------------------------------
#
# Defensive code nothing can reach today — every marker opens with `«`, so a
# replacement cannot splice a new match — which is exactly why it is tested
# directly: its regression would otherwise be invisible until a credential sat
# in storage (DRE-4268 review, blocking 1).


def test_verify_refuses_a_surviving_value_in_any_form():
    plan = [("BUREAU_APP_TOKEN", scrub_agent_log.forms(APP_TOKEN))]
    for leftover in (APP_TOKEN, base64.b64encode(f"x:{APP_TOKEN}".encode()).decode()):
        with pytest.raises(scrub_agent_log.Refused) as refused:
            scrub_agent_log.verify("log.json", f"leftover {leftover} here", plan)
        assert "BUREAU_APP_TOKEN" in str(refused.value)
        assert APP_TOKEN[:8] not in str(refused.value)
    scrub_agent_log.verify("log.json", "nothing to see", plan)  # and passes clean text


def test_verify_refuses_a_surviving_shape():
    planted = "".join(["gh", "p_", _FILLER[:36]])
    with pytest.raises(scrub_agent_log.Refused) as refused:
        scrub_agent_log.verify("step.log", f"token {planted}", [])
    assert "github-token" in str(refused.value)
    assert planted[:10] not in str(refused.value)


def test_a_scrub_that_missed_is_refused_and_nothing_is_written(tmp_path, monkeypatch):
    """The contract DRE-4269's upload step depends on — no successful scrub, no
    upload — pinned end to end: with the scrub reduced to a pass-through, `main`
    must still exit REFUSED and leave the out dir unwritten."""
    import io

    src = tmp_path / "log.json"
    src.write_bytes(transcript(f"export TOKEN={APP_TOKEN}"))
    out = tmp_path / "out"
    monkeypatch.setattr(scrub_agent_log, "scrub", lambda text, *args, **kwargs: text)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SECRETS)))
    assert scrub_agent_log.main(["--out-dir", str(out), str(src)]) == scrub_agent_log.REFUSED
    assert not out.exists()


# --- forms found in review -----------------------------------------------------------


def test_a_lower_case_percent_encoded_value_is_removed(tmp_path):
    """`quote` writes `%2F`; plenty of tools write `%2f`."""
    secret = "p@ss/w0rd+with&chars=9Zq"
    lowered = scrub_agent_log._lower_percent_escapes(urllib.parse.quote(secret, safe=""))
    assert "%2f" in lowered and "9Zq" in lowered  # escapes lowered, the value's own case kept
    done, out = run(tmp_path, {"step.log": f"GET /cb?key={lowered}".encode()},
                    secrets={"CALLBACK_KEY": secret})
    assert done.returncode == 0, done.stderr
    assert lowered not in (out / "step.log").read_text()


def test_a_pem_key_whose_end_line_was_clipped_is_still_removed(tmp_path):
    """A tool's output is clipped long before a log is, and the END line goes
    first. The key body must not survive for want of its framing — and the rest
    of the log must."""
    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7fabricated"
    dashes, kind = "-" * 5, " ".join(["RSA", "PRIVATE", "KEY"])
    clipped = f"{dashes}BEGIN {kind}{dashes}\n{body}\n{body}"
    done, out = run(tmp_path, {"step.log": f"cat key:\n{clipped}\n[output truncated] then more log".encode(),
                               "log.json": transcript(f"the key: {clipped}", "a later turn")},
                    secrets={})
    assert done.returncode == 0, done.stderr
    for name in ("step.log", "log.json"):
        assert body not in (out / name).read_text(), name
    assert "then more log" in (out / "step.log").read_text()
    assert "a later turn" in (out / "log.json").read_text()
    json.loads((out / "log.json").read_text())


_KEY_LINE = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7fabricated"
_PROSE = ["truncated output follows", "Ran pytest and 412 tests passed",
          "the deploy finished and the build id was 99182", "uploading artifacts now",
          "run complete"]


def _pem(*lines: str, end: bool = True) -> str:
    dashes, kind = "-" * 5, " ".join(["RSA", "PRIVATE", "KEY"])
    body = [f"{dashes}BEGIN {kind}{dashes}", *lines]
    return "\n".join(body + ([f"{dashes}END {kind}{dashes}"] if end else []))


def test_a_clipped_key_does_not_take_the_prose_after_it(tmp_path):
    """The reproduction from the second review, verbatim in shape: prose holding
    NO character outside letters, digits and spaces. A body class that admitted
    whitespace ate all five lines and the run reported one clean redaction. The
    earlier fixture only passed because its first trailing character was `[`."""
    assert all(ch.isalnum() or ch == " " for line in _PROSE for ch in line)
    clipped = _pem(_KEY_LINE, end=False)
    raw = "step 4 dumping the deploy key\n" + clipped + "\n" + "\n".join(_PROSE) + "\n"
    done, out = run(tmp_path, {"step.log": raw.encode(),
                               "log.json": transcript("the key: " + clipped + "\n" + "\n".join(_PROSE),
                                                      "a later turn")}, secrets={})
    assert done.returncode == 0, done.stderr
    step, log = (out / "step.log").read_text(), (out / "log.json").read_text()
    assert _KEY_LINE not in step and _KEY_LINE not in log
    assert step == ("step 4 dumping the deploy key\n«redacted-shape:pem-private-key»\n"
                    + "\n".join(_PROSE) + "\n")
    turns = json.loads(log)
    for line in _PROSE:
        assert line in turns[0]["message"]["content"][0]["text"]
    assert turns[1]["message"]["content"][0]["text"] == "a later turn"


def test_an_unterminated_key_does_not_swallow_the_log_up_to_a_later_keys_end(tmp_path):
    """`BEGIN.*?END` under DOTALL runs from the FIRST key's BEGIN to the SECOND
    key's END and takes the whole log between them with it."""
    raw = "\n".join([_pem(_KEY_LINE, end=False), *_PROSE, _pem(_KEY_LINE, _KEY_LINE, "Aw=="), "after"])
    done, out = run(tmp_path, {"step.log": raw.encode()}, secrets={})
    assert done.returncode == 0, done.stderr
    scrubbed = (out / "step.log").read_text()
    assert scrubbed == "\n".join(["«redacted-shape:pem-private-key»", *_PROSE,
                                  "«redacted-shape:pem-private-key»", "after"])


def test_an_encrypted_key_loses_its_headers_and_body_too(tmp_path):
    """RFC 1421 framing: header lines and a blank line sit between BEGIN and the
    body. A line-by-line pattern that did not know them would redact the BEGIN
    line and leave the whole body behind it."""
    raw = _pem("Proc-Type: 4,ENCRYPTED", "DEK-Info: AES-256-CBC,0123456789ABCDEF0123456789ABCDEF",
               "", _KEY_LINE, _KEY_LINE, "Aw==") + "\nnext line\n"
    done, out = run(tmp_path, {"step.log": raw.encode(), "log.json": transcript(raw)}, secrets={})
    assert done.returncode == 0, done.stderr
    assert (out / "step.log").read_text() == "«redacted-shape:pem-private-key»\nnext line\n"
    assert _KEY_LINE not in (out / "log.json").read_text()
    assert "DEK-Info" not in (out / "log.json").read_text()


def test_a_key_line_with_text_appended_still_loses_its_key_material(tmp_path):
    raw = _pem(_KEY_LINE, _KEY_LINE + " ...[clipped]", end=False)
    done, out = run(tmp_path, {"step.log": raw.encode()}, secrets={})
    assert done.returncode == 0, done.stderr
    assert (out / "step.log").read_text() == "«redacted-shape:pem-private-key» ...[clipped]"


# --- absent is not none -----------------------------------------------------------


def _run_with_stdin(tmp_path, stdin: str):
    src = tmp_path / "in"
    src.mkdir()
    password = "-".join(["Hunter2", "correct", "horse", "battery"])
    (src / "step.log").write_text(f"db url postgresql://bureau:{password}@db.internal:5432/bureau")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(tmp_path / "out"), str(src / "step.log")],
        input=stdin, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("stdin", ["", "\n", "   "], ids=["empty", "newline", "spaces"])
def test_empty_stdin_is_refused_not_read_as_no_secrets(tmp_path, stdin):
    """What an unset `$SECRETS_JSON` in the calling step looks like. Read as `{}`
    it skips pass 1, exits 0, and a prefix-less credential — this database
    password matches no shape — goes to storage untouched (second review)."""
    done = _run_with_stdin(tmp_path, stdin)
    assert done.returncode == scrub_agent_log.REFUSED
    assert "stdin was empty" in done.stderr
    assert not (tmp_path / "out").exists()


def test_an_explicit_empty_object_still_means_no_secrets(tmp_path):
    """The distinction pinned from the other side: `{}` is a statement, and runs."""
    done = _run_with_stdin(tmp_path, "{}")
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "out" / "step.log").exists()


# --- the minimum-length rule ------------------------------------------------------


def test_a_short_or_wordlike_secret_does_not_blank_the_log(tmp_path):
    """A by-value replacement needs MIN_VALUE_LENGTH characters. A repo variable
    set to `true`, or a secret that is the word `main`, would otherwise redact
    every occurrence of an ordinary word and make the log unreadable. Such a
    value is SKIPPED and named in the summary — by NAME, which is not secret."""
    text = "on branch main: it is true that main is the main branch"
    done, out = run(tmp_path, {"step.log": text.encode()},
                    secrets={"DEFAULT_BRANCH": "main", "FLAG": "true"})
    assert done.returncode == 0, done.stderr
    assert (out / "step.log").read_text() == text
    summary = json.loads(done.stdout.strip().splitlines()[-1])
    assert sorted(summary["skipped_short"]) == ["DEFAULT_BRANCH", "FLAG"]
    assert scrub_agent_log.MIN_VALUE_LENGTH == 8


# --- the summary -------------------------------------------------------------------


def test_the_summary_counts_by_name_and_holds_no_fragment_of_a_secret(tmp_path):
    body = f"a {APP_TOKEN} b {APP_TOKEN} c {LINEAR_KEY} d ghp_{'Z9' * 18}"
    done, _ = run(tmp_path, {"step.log": body.encode()})
    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout.strip().splitlines()[-1])
    assert summary["by_value"] == {"BUREAU_APP_TOKEN": 2, "LINEAR_API_KEY": 1}
    assert summary["by_shape"] == {"github-token": 1}
    everything_printed = done.stdout + done.stderr
    for value in (APP_TOKEN, LINEAR_KEY):
        for start in range(0, len(value) - 5):
            assert value[start:start + 6] not in everything_printed


def test_the_secret_values_are_never_accepted_as_arguments():
    """Arguments reach the process table. The parser has no option that takes a
    value — only stdin does."""
    options = {a.dest for a in scrub_agent_log.build_parser()._actions}
    assert options == {"help", "out_dir", "max_bytes", "files"}
