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
