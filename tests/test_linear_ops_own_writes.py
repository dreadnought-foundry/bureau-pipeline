"""The job's own-write record sets the floor of its later door reads (DRE-8898).

Every card write `linear_ops` makes appends `{"card", "at"}` to
`$RUNNER_TEMP/linear-own-writes.jsonl` once Linear answered it with success.
Every step of a job shares RUNNER_TEMP and GitHub empties it per job, so a
write made in one step's process is seen by a later step's read of the same
card — and a door answer older than that write is not taken. Linear is
`LinearStub` and the door `tests/bureau_read_fakes.py`, as in
`tests/test_linear_ops_card_via_door.py`.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "scripts"))
sys.path.insert(0, str(TESTS))

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer  # noqa: E402
from test_linear_ops_card_via_door import (  # noqa: E402,F401
    WORLD, door, door_env_for, door_up, fallback_lines, linear, records, run)


def record(runner_temp) -> list[dict]:
    path = Path(runner_temp) / linear_ops.OWN_WRITES_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def runner_temp(monkeypatch, tmp_path):
    path = tmp_path / "runner-temp"
    path.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(path))
    return path


# ── each write adds one line on success, none on failure ────────────────────

WRITES = {
    "_create_comment": ("commentCreate",
                        lambda: linear_ops._create_comment("dre-7", "uuid-DRE-7", "x")),
    "_set_state": ("issueUpdate", lambda: linear_ops._set_state("DRE-7", "uuid-DRE-7", "s")),
    "set_description": ("issueUpdate", lambda: linear_ops.set_description("DRE-7", "body")),
    "set_title": ("issueUpdate", lambda: linear_ops.set_title("DRE-7", "title")),
    "add_label": ("issueUpdate", lambda: linear_ops.add_label("DRE-7", "bug")),
    "remove_label": ("issueUpdate", lambda: linear_ops.remove_label("DRE-7", "bug")),
}


def _arrange(name: str, linear) -> None:
    if name == "remove_label":
        linear.labels["DRE-7"] = [{"id": "label-bug", "name": "bug"}]


@pytest.mark.parametrize("name", sorted(WRITES))
def test_a_successful_write_adds_one_line(name, linear, runner_temp):
    _arrange(name, linear)
    before = datetime.now(timezone.utc)
    WRITES[name][1]()
    after = datetime.now(timezone.utc)
    (line,) = record(runner_temp)
    assert set(line) == {"card", "at"}
    assert line["card"] == "DRE-7"
    assert before <= _instant(line["at"]) <= after
    assert line["at"].endswith("Z") or line["at"].endswith("+00:00")


@pytest.mark.parametrize("name", sorted(WRITES))
def test_a_failed_write_adds_none(name, linear, runner_temp):
    _arrange(name, linear)
    linear.fail.add(WRITES[name][0])
    with pytest.raises(linear_ops.LinearError):
        WRITES[name][1]()
    assert record(runner_temp) == []


@pytest.mark.parametrize("name", sorted(WRITES))
def test_a_write_linear_answers_unsuccessful_adds_none(name, linear, runner_temp):
    _arrange(name, linear)
    linear.unsuccessful.add(WRITES[name][0])
    try:
        WRITES[name][1]()
    except linear_ops.LinearError:
        pass
    assert record(runner_temp) == []


def test_a_write_that_wrote_nothing_adds_none(linear, runner_temp):
    linear.labels["DRE-7"] = [{"id": "label-bug", "name": "bug"}]
    linear_ops.add_label("DRE-7", "bug")  # already carried: nothing sent
    linear_ops.remove_label("DRE-7", "other")  # not carried: nothing sent
    assert record(runner_temp) == []


def test_the_comment_command_adds_one_line(linear, runner_temp):
    linear_ops.cmd_comment("DRE-7", "x")
    assert [r["card"] for r in record(runner_temp)] == ["DRE-7"]


# ── the record sets the floor ───────────────────────────────────────────────


def test_a_write_sets_the_floor_of_a_later_read_in_the_same_job(door, linear, runner_temp,
                                                                capsys):
    linear_ops.cmd_comment("DRE-7", "x")
    (line,) = record(runner_temp)
    at = _instant(line["at"])
    door.as_of = _iso(at - timedelta(seconds=5))
    assert linear_ops.comment_records("DRE-7", whole_thread=True) == records("DRE-7")
    assert linear.thread_reads() == [linear_ops._THREAD_QUERY]
    (fallback,) = fallback_lines(capsys.readouterr().err)
    assert "read=cards reason=older-than-needed" in fallback
    linear.sent.clear()
    door.as_of = _iso(at + timedelta(seconds=1))
    assert linear_ops.comment_records("DRE-7", whole_thread=True) == records("DRE-7")
    assert linear.thread_reads() == []


def test_the_newest_write_is_the_floor(door, linear, runner_temp):
    path = runner_temp / linear_ops.OWN_WRITES_FILE
    path.write_text(
        json.dumps({"card": "DRE-7", "at": "2026-11-02T18:00:00Z"}) + "\n"
        + json.dumps({"card": "DRE-7", "at": "2026-11-02T19:00:00Z"}) + "\n"
        + json.dumps({"card": "DRE-8", "at": "2026-11-02T23:00:00Z"}) + "\n")
    door.as_of = "2026-11-02T18:30:00Z"
    assert linear_ops.card_via_door("DRE-7") is None
    door.as_of = "2026-11-02T19:00:00Z"
    assert linear_ops.card_via_door("DRE-7") is not None


def test_the_floor_is_the_latest_of_the_three(door, linear, runner_temp, monkeypatch):
    (runner_temp / linear_ops.OWN_WRITES_FILE).write_text(
        json.dumps({"card": "DRE-7", "at": "2026-11-02T18:00:00Z"}) + "\n")
    monkeypatch.setenv(linear_ops.CARD_SEEN_AFTER_ENV, "2026-11-02T17:00:00Z")
    door.as_of = "2026-11-02T17:30:00Z"
    assert linear_ops.card_via_door("DRE-7") is None
    assert linear_ops.card_via_door("DRE-7", seen_after="2026-11-02T17:00:00Z") is None
    door.as_of = "2026-11-02T18:00:00Z"
    assert linear_ops.card_via_door("DRE-7") is not None
    assert linear_ops.card_via_door("DRE-7", seen_after="2026-11-02T18:00:01Z") is None


def test_off_a_runner_the_record_is_the_processs_memory(door, linear, monkeypatch, tmp_path):
    monkeypatch.delenv("RUNNER_TEMP")
    monkeypatch.chdir(tmp_path)
    door.as_of = "2026-11-02T18:00:00Z"
    assert linear_ops.card_via_door("DRE-7") is not None
    linear_ops._set_state("DRE-7", "uuid-DRE-7", "s")
    assert linear_ops.card_via_door("DRE-7") is None
    assert linear_ops.card_via_door("DRE-8") is not None
    assert list(tmp_path.iterdir()) == []


def test_a_record_that_cannot_be_written_is_said_once(linear, monkeypatch, tmp_path, capsys):
    not_a_dir = tmp_path / "a-file"
    not_a_dir.write_text("")
    monkeypatch.setenv("RUNNER_TEMP", str(not_a_dir))
    linear_ops._set_state("DRE-7", "uuid-DRE-7", "s")
    linear_ops.set_title("DRE-7", "t")
    said = [ln for ln in capsys.readouterr().err.splitlines()
            if linear_ops.OWN_WRITES_FILE in ln]
    assert len(said) == 1, said


def test_a_record_that_cannot_be_read_is_said_once_and_sets_no_floor(door, linear,
                                                                     runner_temp, capsys):
    (runner_temp / linear_ops.OWN_WRITES_FILE).write_text("not json\n{\"card\": 7}\n")
    assert linear_ops.card_via_door("DRE-7") is not None
    assert linear_ops.card_via_door("DRE-7") is not None
    said = [ln for ln in capsys.readouterr().err.splitlines()
            if linear_ops.OWN_WRITES_FILE in ln]
    assert len(said) == 1, said


# ── read your own write, across processes ───────────────────────────────────

READ_DRE_7 = ('import json; print(json.dumps(linear_ops.comment_records('
              '"DRE-7", whole_thread=True)))')


def _comment_in_a_process(tmp_path, runner_temp, ident: str) -> datetime:
    proc, sent = run(tmp_path, {"RUNNER_TEMP": str(runner_temp)}, "cli", "comment", ident, "x")
    assert proc.returncode == 0, proc.stderr
    (line,) = record(runner_temp)
    assert line["card"] == ident
    return _instant(line["at"])


def _read_dre_7(tmp_path, door, issuer, runner_temp):
    proc, sent = run(tmp_path, door_env_for(door, issuer, RUNNER_TEMP=str(runner_temp)),
                     "py", READ_DRE_7)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == records("DRE-7")
    threads = [q for q in sent if "comments(" in q]
    return threads, fallback_lines(proc.stderr)


def test_a_later_step_reads_the_comment_an_earlier_step_posted(tmp_path):
    job = tmp_path / "job-runner-temp"
    job.mkdir()
    at = _comment_in_a_process(tmp_path, job, "DRE-7")
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.as_of = _iso(at - timedelta(seconds=5))
        threads, fallbacks = _read_dre_7(tmp_path, door, issuer, job)
        assert threads == [linear_ops._THREAD_QUERY]
        (line,) = fallbacks
        assert "read=cards reason=older-than-needed" in line

        door.as_of = _iso(at + timedelta(seconds=1))
        threads, fallbacks = _read_dre_7(tmp_path, door, issuer, job)
        assert threads == [] and fallbacks == []


def test_a_write_to_another_card_sets_no_floor(tmp_path):
    job = tmp_path / "job-runner-temp"
    job.mkdir()
    at = _comment_in_a_process(tmp_path, job, "DRE-8")
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.as_of = _iso(at - timedelta(seconds=5))
        threads, fallbacks = _read_dre_7(tmp_path, door, issuer, job)
    assert threads == [] and fallbacks == []


def test_another_jobs_runner_temp_sees_no_record(tmp_path):
    job = tmp_path / "job-runner-temp"
    job.mkdir()
    other = tmp_path / "another-job"
    other.mkdir()
    at = _comment_in_a_process(tmp_path, job, "DRE-7")
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.as_of = _iso(at - timedelta(seconds=5))
        threads, fallbacks = _read_dre_7(tmp_path, door, issuer, other)
    assert threads == [] and fallbacks == []
    assert bureau_read.FALLBACK_TAG == "read-fallback:"
