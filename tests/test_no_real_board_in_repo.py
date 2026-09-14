"""RED-first: no real Linear board, and no real address, is committed here
(DRE-3918).

WHY. This repository is PUBLIC and a commit is permanent. DRE-3638 committed
`tests/fixtures/board-snapshot-2026-09-12.json`: the text of every card on the
real board, naming customers, with real contact addresses in one of its
commits. It merged to main before anyone could stop it, and had to be taken
back out. `scripts/board_snapshot.py` now refuses to write inside the repo;
these guards catch the file however it arrives.

WHAT IS UNDER TEST:
  * No file under `tests/fixtures/` IS a board snapshot. The contract's
    top-level keys (`taken_at`, `team`, `lanes`, `cards`) identify one,
    whatever the file is called.
  * No file under `tests/fixtures/`, and neither the snapshot script nor its
    test, carries an email address outside the domains reserved for
    documentation (RFC 2606 / RFC 6761). The script's and its test's own
    examples use those domains.

WHY NOT A CUSTOMER-NAME LIST. The only customer names this repo declares as
data are the GitHub owners in `config/repo-map.json`, and those are already
public in that file, so matching on them protects nothing. A hand list of the
others would publish our customer relationships in a public repo, which is the
leak this guards against. The structural guard (no board snapshot at all) is
what keeps a card's prose out.

Run: cd bureau-pipeline && python3 -m pytest tests/test_no_real_board_in_repo.py -v
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

#: A board snapshot's top-level keys, spelled out here as the contract states
#: them in `scripts/board_snapshot.py`'s docstring.
SNAPSHOT_KEYS = {"taken_at", "team", "lanes", "cards"}

#: Email-shaped, independently of the script's own `EMAIL_RE`.
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")

#: Domains reserved for examples and tests, which can belong to no one.
RESERVED_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_TLDS = (".example", ".test", ".invalid", ".localhost")

#: Code that handles the real board, held to the same rule as the fixtures.
BOARD_CODE = (
    ROOT / "scripts" / "board_snapshot.py",
    ROOT / "tests" / "test_board_snapshot.py",
)


def _reserved(domain: str) -> bool:
    d = domain.lower()
    return (
        any(d == r or d.endswith("." + r) for r in RESERVED_DOMAINS)
        or d.endswith(RESERVED_TLDS)
    )


def _real_addresses(text: str) -> list[str]:
    """Domains of the non-reserved addresses in `text`. Only the domain is
    returned, so a failure message never repeats a person's address."""
    return [m.group(1) for m in EMAIL.finditer(text) if not _reserved(m.group(1))]


def _fixture_files() -> list[Path]:
    return sorted(p for p in FIXTURES.rglob("*") if p.is_file())


def test_the_reserved_domain_rule_reads_the_way_it_says():
    assert _reserved("example.com")
    assert _reserved("customer.example.com")
    assert _reserved("vendor.example.org")
    assert _reserved("host.test")
    assert not _reserved("example.com.evil.io")
    assert not _reserved("gmail.com")
    assert _real_addresses("mail ada@customer.example.com") == []
    assert _real_addresses("mail someone@realcompany.io") == ["realcompany.io"]


def test_no_fixture_is_a_board_snapshot():
    offenders = []
    for path in _fixture_files():
        if path.suffix != ".json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and SNAPSHOT_KEYS <= set(data):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "a Linear board snapshot is committed to this PUBLIC repo — take it "
        f"out and use board_snapshot.synthetic() instead: {offenders}"
    )


def test_no_fixture_carries_a_real_email_address():
    offenders = {}
    for path in _fixture_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found = _real_addresses(text)
        if found:
            offenders[str(path.relative_to(ROOT))] = sorted(set(found))
    assert not offenders, (
        "real email addresses in fixtures of a PUBLIC repo (domains shown, "
        f"addresses withheld): {offenders}"
    )


def test_the_board_code_uses_only_reserved_example_addresses():
    offenders = {}
    for path in BOARD_CODE:
        found = _real_addresses(path.read_text(encoding="utf-8"))
        if found:
            offenders[str(path.relative_to(ROOT))] = sorted(set(found))
    assert not offenders, (
        "the board snapshot's code or tests quote a real address; use a "
        f"reserved example domain (domains shown): {offenders}"
    )
