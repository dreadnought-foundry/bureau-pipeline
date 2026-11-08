"""RED-first tests: the operator agent's public proof record (DRE-8745, epic DRE-8738).

The epic's PROOF, DRE-8837, commits `docs/operator-agent-proof-*.md` to this
PUBLIC repository (DRE-3918). The record quotes each `do` card's decision
line, and that line's `step:` and `reason` are the model's words, written from
real card text. So every such record is held here to the limits the fixtures
are held to:

  * every place `operator_decide.LOG_TAG`, a space and `DRE-<n>` appear
    together is a per-card log line, in one of the three forms
    `operator_decide` prints — `→ <outcome> — <reason> · step: <step>`,
    `unread — <why>` or `left alone — <why>` — whole on the line it opened on;
  * no email address but `person@example.com`;
  * no URL outside `https://github.com/dreadnought-foundry/` and
    `https://linear.app/dreadnoughtfoundry/`;
  * no run of 12 or more digits outside such a URL. A run's id is 11 digits
    on 2026-11-02 and sits inside its run URL.

The record's own pull request runs this, so a record that breaks it does not
merge. With no record on `main` the live check reads no file, and the checks
also run over synthetic records in a temporary directory, so this is never
green by having nothing to read.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_agent_proof_record.py -v
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import operator_decide  # noqa: E402

DOCS = ROOT / "docs"
PATTERN = "operator-agent-proof-*.md"

ALLOWED_EMAIL = "person@example.com"
ALLOWED_URLS = ("https://github.com/dreadnought-foundry/",
                "https://linear.app/dreadnoughtfoundry/")

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL = re.compile(r"https?://[^\s)\"'\]>`]+")
LONG_NUMBER = re.compile(r"\d{12,}")
TAGGED = re.compile(re.escape(operator_decide.LOG_TAG) + r" DRE-\d+")
#: The rest of a per-card line after `<LOG_TAG> DRE-<n>`: the three forms.
FORMS = (
    re.compile(r"^ → (?:%s) — \S.*? · step: \S" % "|".join(
        re.escape(o) for o in operator_decide.OUTCOMES)),
    re.compile(r"^ unread — \S"),
    re.compile(r"^ left alone — \S"),
)


def problems(text: str) -> list[str]:
    """Everything in one record that breaks the public limits. [] for a
    record that keeps them."""
    found = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for match in TAGGED.finditer(line):
            rest = line[match.end():]
            if not any(form.match(rest) for form in FORMS):
                found.append(f"line {line_no}: {match.group(0)} is not a whole log line "
                             "in one of the three forms")
    for email in EMAIL.findall(text):
        if email != ALLOWED_EMAIL:
            found.append("an email address other than the example one")
    outside = text
    for url in URL.findall(text):
        if not url.startswith(ALLOWED_URLS):
            found.append(f"a URL outside the two prefixes: {url.split('/')[2]}")
        else:
            outside = outside.replace(url, " ")
    if LONG_NUMBER.search(outside):
        found.append("a run of 12 or more digits outside a permitted URL")
    return found


def records(directory: Path) -> list[Path]:
    return sorted(directory.glob(PATTERN))


def check(directory: Path) -> dict:
    return {path.name: found for path in records(directory)
            if (found := problems(path.read_text(encoding="utf-8")))}


def test_every_proof_record_in_docs_keeps_the_public_limits():
    assert check(DOCS) == {}


# --------------------------------------------------------------------------- #
# over synthetic records                                                       #
# --------------------------------------------------------------------------- #

RUN_URL = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41412271124"
GOOD = f"""# Operator agent proof, 2026-11

The pass ran as {RUN_URL} and its log read:

```
operator-agent: DRE-9001 → do — the model read the card · step: delete the variable
operator-agent: DRE-9002 → studio — the step needs production deploy · step: deploy it · spent
operator-agent: DRE-9003 unread — not read by the model on this pass
operator-agent: DRE-8748 left alone — the switch that turns this agent on
```

Card: https://linear.app/dreadnoughtfoundry/issue/DRE-9001 — ask person@example.com.
"""


def _record_dir(tmp_path, text) -> Path:
    (tmp_path / "operator-agent-proof-2026-11.md").write_text(text, encoding="utf-8")
    (tmp_path / "unrelated.md").write_text("me@realcompany.io 123456789012345", encoding="utf-8")
    return tmp_path


def test_a_record_that_keeps_the_limits_passes_with_its_11_digit_run_url(tmp_path):
    directory = _record_dir(tmp_path, GOOD)
    assert [p.name for p in records(directory)] == ["operator-agent-proof-2026-11.md"]
    assert check(directory) == {}


@pytest.mark.parametrize("bad", [
    "operator-agent: DRE-9001 → do — the model read the card",
    "operator-agent: DRE-9001 → do — the model read the card ·\nstep: delete it",
    "operator-agent: DRE-9001 → maybe — a reason · step: a step",
    "operator-agent: DRE-9001 was decided by hand",
    "operator-agent: DRE-9001 unread",
    "see operator-agent: DRE-9001 left alone",
])
def test_a_per_card_line_not_in_one_of_the_three_forms_fails(tmp_path, bad):
    found = check(_record_dir(tmp_path, GOOD + "\n" + bad + "\n"))
    assert found and "three forms" in " ".join(found["operator-agent-proof-2026-11.md"])


def test_an_email_address_other_than_the_example_fails(tmp_path):
    found = check(_record_dir(tmp_path, GOOD + "\nWritten by someone@realcompany.io.\n"))
    assert "an email address other than the example one" in found[
        "operator-agent-proof-2026-11.md"]


@pytest.mark.parametrize("url", ["https://example.org/a/page",
                                 "https://github.com/someone-else/repo",
                                 "http://linear.app/dreadnoughtfoundry/issue/DRE-1"])
def test_a_url_outside_the_two_prefixes_fails(tmp_path, url):
    found = check(_record_dir(tmp_path, GOOD + f"\nSee {url} for more.\n"))
    assert [p for p in found["operator-agent-proof-2026-11.md"] if p.startswith("a URL")]


def test_a_12_digit_run_outside_a_url_fails(tmp_path):
    found = check(_record_dir(tmp_path, GOOD + "\nAccount 123456789012 was read.\n"))
    assert "a run of 12 or more digits outside a permitted URL" in found[
        "operator-agent-proof-2026-11.md"]


def test_a_12_digit_run_inside_a_permitted_url_passes(tmp_path):
    url = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/412271124999"
    assert check(_record_dir(tmp_path, GOOD + f"\nAlso {url}.\n")) == {}


def test_a_12_digit_run_inside_an_outside_url_still_fails(tmp_path):
    found = check(_record_dir(tmp_path, GOOD + "\nAt https://example.org/123456789012.\n"))
    assert "a run of 12 or more digits outside a permitted URL" in found[
        "operator-agent-proof-2026-11.md"]
