"""The promotion gate's refusals, read back as a clock (DRE-4207).

`reconcile.promote_ready` refuses or holds a Backlog card, prints why, and
posts the refusal on the card once. Nothing read that refusal again, so a card
refused on every sweep for five days looked exactly like a card waiting its
turn (DRE-4198). `scripts/promotion_stall.py` is the pure half of the fix: it
turns a standing refusal receipt into a stall verdict, the stall notice and the
idle-board line. The wiring card (DRE-4210) only calls it.

The sweep's clock is a fixture here — `now` is always passed in — and the
registry of act tags is read at test time, never copied.
"""
from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import promotion_stall  # noqa: E402
from promotion_stall import Refused  # noqa: E402

NOW = "2026-10-09T18:00:00.000Z"  # 11:00 PT
CAP = 8


def ago(minutes: float) -> str:
    """The ISO time `minutes` before NOW, in the shape Linear's createdAt takes."""
    at = datetime(2026, 10, 9, 18, 0, tzinfo=UTC) - timedelta(minutes=minutes)
    return at.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def registry_tags() -> list[str]:
    doc = json.loads((ROOT / "config" / "pipeline-acts.json").read_text(encoding="utf-8"))
    return [act["tag"] for act in doc["acts"]]


# --- stalled ---------------------------------------------------------------


def test_stalled_is_none_under_the_window():
    refused = Refused("DRE-1", "routing-no-verdict", ago(promotion_stall.STALL_MINUTES - 1))
    assert promotion_stall.stalled(refused, NOW) is None


def test_stalled_is_the_age_at_and_over_the_window():
    at = Refused("DRE-1", "routing-no-verdict", ago(promotion_stall.STALL_MINUTES))
    over = Refused("DRE-1", "plan-critic-post-died", ago(promotion_stall.STALL_MINUTES + 90))
    assert promotion_stall.stalled(at, NOW) == pytest.approx(promotion_stall.STALL_MINUTES)
    assert promotion_stall.stalled(over, NOW) == pytest.approx(promotion_stall.STALL_MINUTES + 90)


def test_stalled_is_none_when_first_seen_is_unknown():
    """Unknown age is never stale: a refusal whose receipt could not be dated
    is standing, and nothing can say it has stood two hours."""
    refused = Refused("DRE-1", "routing-no-verdict", None)
    assert promotion_stall.stalled(refused, NOW) is None


def test_stalled_is_none_for_a_held_record_whatever_its_age():
    """A held tag is counted at the board level and never clocked per card."""
    for tag in promotion_stall.HELD_TAGS:
        refused = Refused("DRE-1", tag, ago(180))
        assert promotion_stall.stalled(refused, NOW) is None, tag


def test_stalled_reads_an_unparseable_first_seen_as_unknown():
    refused = Refused("DRE-1", "routing-no-verdict", "not a time")
    assert promotion_stall.stalled(refused, NOW) is None


# --- clocked ---------------------------------------------------------------


def test_clocked_is_true_for_exactly_the_six_clocked_tags():
    """The sixth is the epic cap's hold (DRE-6618): a sweep asks the cap only
    for an epic a blocker released, so a hold on any other stands until a
    person acts, and is clocked like every refusal no sweep clears."""
    assert promotion_stall.CLOCKED_TAGS == (
        "routing-no-verdict", "mid-epic-no-verdict",
        "plan-critic-post-unread", "plan-critic-post-sent-back",
        "plan-critic-post-died", "epic-cap-undecided",
    )
    for tag in promotion_stall.CLOCKED_TAGS:
        assert promotion_stall.clocked(tag) is True, tag


def test_clocked_is_false_for_held_tags_declared_waits_and_none():
    assert promotion_stall.HELD_TAGS == (
        "needs-human", "agent-blocker", "stale-verdict", "live-recheck",
    )
    for tag in (*promotion_stall.HELD_TAGS, "wave-not-green-lit", "routing-not-fleet",
                "prose-blocker-no-relation", "stranded-watchdog", None):
        assert promotion_stall.clocked(tag) is False, tag


def test_wave_not_green_lit_is_not_clocked():
    """The sweep's own epic-advance path clears `wave-not-green-lit` when the
    predecessor epic reaches Done, and that wait is legitimately days long — a
    clock on it would call a scheduled wait a stall."""
    assert promotion_stall.clocked("wave-not-green-lit") is False


def test_needs_human_is_not_clocked():
    """`needs-human` was said once, at the park — a second receipt per card is
    noise. It is counted at the board level instead, by `idle_board`."""
    assert promotion_stall.clocked("needs-human") is False


# --- notice ----------------------------------------------------------------


def test_notice_names_the_card_tag_receipt_time_and_age():
    refused = Refused("DRE-4198", "routing-no-verdict", "2026-10-09T15:00:00.000Z")
    body = promotion_stall.notice(refused, 180.0, 0, CAP)
    assert body.startswith("🚨 promotion-stalled:")
    assert body.startswith(promotion_stall.STALL_MARK)
    assert "DRE-4198" in body
    assert "routing-no-verdict" in body
    assert "2026-10-09 08:00 PT" in body  # 15:00Z is 08:00 PDT
    assert "3.0 hours" in body
    assert "no sweep will clear it" in body
    assert "a person must act" in body
    assert "refusal receipt already on this card" in body


def test_the_epic_caps_hold_is_clocked_under_its_own_tag():
    """Spelled here, never imported: this module imports neither `reconcile`
    nor `linear_ops`, and `epic_cap` imports the second."""
    import epic_cap  # noqa: PLC0415 — the test reads the one definition
    assert epic_cap.UNDECIDED_TAG in promotion_stall.CLOCKED_TAGS


def test_notice_and_ledger_name_the_epic_when_the_record_carries_one():
    """An `epic-cap-undecided` card waits on its EPIC, and the act is on the
    epic (DRE-6618), so the stall says which one."""
    refused = Refused("DRE-6066", "epic-cap-undecided", "2026-10-10T10:50:00.000Z",
                      epic="DRE-6064")
    body = promotion_stall.notice(refused, 180.0, 0, CAP)
    assert body.startswith(promotion_stall.STALL_MARK + " DRE-6066 ")
    assert "DRE-6064" in body
    line = promotion_stall.ledger_line(refused, 180.0)
    assert line.startswith(promotion_stall.LEDGER_STALL_OPENER + "DRE-6066:")
    assert "DRE-6064" in line


def test_a_record_with_no_epic_reads_as_before():
    refused = Refused("DRE-4198", "routing-no-verdict", "2026-10-09T15:00:00.000Z")
    assert refused.epic is None
    assert "epic" not in promotion_stall.notice(refused, 180.0, 0, CAP).split(" — ")[0]


def test_notice_carries_no_other_acts_tag():
    """The registry refuses a body that carries a foreign tag. Read off the
    registry at test time: a copied list would pass the day a tag joins it."""
    tags = [t for t in registry_tags() if t != promotion_stall.STALL_TAG]
    assert tags
    for clocked_tag in promotion_stall.CLOCKED_TAGS:
        body = promotion_stall.notice(Refused("DRE-7", clocked_tag, ago(200)), 200.0, 3, CAP)
        for tag in tags:
            assert tag not in body, (clocked_tag, tag)


def test_notice_and_ledger_carry_no_file_path():
    refused = Refused("DRE-7", "plan-critic-post-unread", ago(200))
    for text in (promotion_stall.notice(refused, 200.0, 0, CAP),
                 promotion_stall.ledger_line(refused, 200.0)):
        assert ".py" not in text and "/" not in text.replace(f"0/{CAP}", "")


# --- ledger_line -----------------------------------------------------------


def test_ledger_line_opens_with_the_stall_opener_and_the_card():
    refused = Refused("DRE-4198", "mid-epic-no-verdict", ago(150))
    line = promotion_stall.ledger_line(refused, 150.0)
    assert promotion_stall.LEDGER_STALL_OPENER == "promotion-stalled "
    assert line.startswith(promotion_stall.LEDGER_STALL_OPENER + "DRE-4198:")
    assert line.startswith("promotion-stalled DRE-4198:")
    assert "mid-epic-no-verdict" in line
    assert "2.5h" in line
    assert "a person must act" in line
    assert "\n" not in line


# --- idle_board ------------------------------------------------------------


def test_idle_board_is_silent_when_wip_is_above_zero_or_nothing_is_refused():
    aged = [Refused("DRE-1", "routing-no-verdict", ago(300))]
    assert promotion_stall.idle_board(2, 0, CAP, aged, NOW) == (None, None)
    assert promotion_stall.idle_board(0, 0, CAP, [], NOW) == (None, None)


def test_idle_board_says_the_line_and_no_entry_under_the_window():
    young = [Refused("DRE-12", "routing-no-verdict", ago(promotion_stall.IDLE_BOARD_MINUTES - 5))]
    line, entry = promotion_stall.idle_board(0, 0, CAP, young, NOW)
    assert line is not None
    assert entry is None


def test_idle_board_says_both_once_the_oldest_record_is_that_old():
    records = [
        Refused("DRE-30", "routing-no-verdict", ago(10)),
        Refused("DRE-12", "plan-critic-post-sent-back", ago(promotion_stall.IDLE_BOARD_MINUTES + 30)),
        Refused("DRE-9", "needs-human", ago(5)),
    ]
    line, entry = promotion_stall.idle_board(0, 0, CAP, records, NOW)
    assert promotion_stall.IDLE_LINE_OPENER == "promotion: idle board — WIP 0/"
    assert line.startswith(promotion_stall.IDLE_LINE_OPENER + str(CAP))
    assert line.startswith(f"promotion: idle board — WIP 0/{CAP}")
    assert "3 cards" in line
    assert "DRE-9" in line  # the lowest-numbered, by number and not by text
    assert "a person must act" in line
    assert promotion_stall.LEDGER_IDLE_OPENER == "idle board — WIP 0/"
    assert entry.startswith(promotion_stall.LEDGER_IDLE_OPENER + f"{CAP}:")
    assert entry.startswith(f"idle board — WIP 0/{CAP}:")
    assert "3 cards" in entry
    assert "DRE-12" in entry  # the oldest receipt
    assert f"{(promotion_stall.IDLE_BOARD_MINUTES + 30) / 60:.1f}h" in entry


def test_idle_board_is_silenced_by_active_or_spent_on_its_own():
    """WIP 0 is two numbers: what the sweep started with and what it
    dispatched this pass. Either one alone means the board is not idle."""
    aged = [Refused("DRE-1", "routing-no-verdict", ago(300))]
    assert promotion_stall.idle_board(0, 1, CAP, aged, NOW) == (None, None)
    assert promotion_stall.idle_board(1, 0, CAP, aged, NOW) == (None, None)


def test_idle_board_over_only_held_records_is_the_alarm():
    """A board idling on nothing but held cards is the alarm — the entry's age
    is read off the one held record whose receipt is dated."""
    records = [
        Refused("DRE-40", "needs-human", ago(120)),
        Refused("DRE-41", "agent-blocker", None),
    ]
    line, entry = promotion_stall.idle_board(0, 0, CAP, records, NOW)
    assert line is not None and "2 cards" in line
    assert entry is not None
    assert "DRE-40" in entry
    assert "2.0h" in entry


def test_idle_board_with_every_first_seen_unknown_says_the_line_and_no_entry():
    records = [
        Refused("DRE-40", "needs-human", None),
        Refused("DRE-41", "routing-no-verdict", None),
    ]
    line, entry = promotion_stall.idle_board(0, 0, CAP, records, NOW)
    assert line is not None and "2 cards" in line
    assert entry is None


def test_idle_board_mixed_the_oldest_known_decides_and_none_changes_nothing():
    known = [
        Refused("DRE-50", "routing-no-verdict", ago(90)),
        Refused("DRE-51", "live-recheck", ago(70)),
    ]
    _, alone = promotion_stall.idle_board(0, 0, CAP, known, NOW)
    _, mixed = promotion_stall.idle_board(
        0, 0, CAP, [Refused("DRE-49", "needs-human", None), *known], NOW)
    assert alone is not None and "DRE-50" in alone and "1.5h" in alone
    assert mixed is not None and "DRE-50" in mixed and "1.5h" in mixed
    assert "DRE-49" not in mixed  # the undated record is counted, never named as oldest
    assert "3 cards" in mixed


def test_idle_board_strings_carry_no_registry_tag():
    records = [Refused("DRE-60", tag, ago(300))
               for tag in (*promotion_stall.CLOCKED_TAGS, *promotion_stall.HELD_TAGS)]
    line, entry = promotion_stall.idle_board(0, 0, CAP, records, NOW)
    for tag in registry_tags():
        if tag == promotion_stall.STALL_TAG:
            continue
        assert tag not in line and tag not in entry, tag


# --- windows and imports ---------------------------------------------------


@pytest.fixture
def reload_with(monkeypatch):
    """Re-import the module under an environment, and restore it afterwards."""
    def _reload(**env):
        for name in ("PROMOTION_STALL_MINUTES", "IDLE_BOARD_MINUTES"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(promotion_stall)
    yield _reload
    monkeypatch.undo()
    importlib.reload(promotion_stall)


def test_windows_default_to_120_and_60(reload_with):
    module = reload_with()
    assert module.STALL_MINUTES == 120
    assert module.IDLE_BOARD_MINUTES == 60


def test_windows_honor_their_environment_override(reload_with):
    module = reload_with(PROMOTION_STALL_MINUTES="45", IDLE_BOARD_MINUTES="15")
    assert module.STALL_MINUTES == 45
    assert module.IDLE_BOARD_MINUTES == 15
    refused = module.Refused("DRE-1", "routing-no-verdict", ago(50))
    assert module.stalled(refused, NOW) == pytest.approx(50)
    _, entry = module.idle_board(0, 0, CAP, [module.Refused("DRE-1", "needs-human", ago(20))], NOW)
    assert entry is not None


def test_the_module_imports_neither_reconcile_nor_linear_ops():
    source = (ROOT / "scripts" / "promotion_stall.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "reconcile" not in imported
    assert "linear_ops" not in imported
    # Nor through anything it imports: a fresh interpreter, nothing preloaded.
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'scripts'); import promotion_stall; "
         "print(sorted({'reconcile', 'linear_ops'} & set(sys.modules)))"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "[]"
