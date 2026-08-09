"""RED-first tests for DRE-2236 — the model catalog: discover models from the
API, alert on drift, NEVER auto-adopt.

THE PROBLEM. Model ids and display labels are hardcoded in several places and
drift silently. On 2026-07-28 the console showed "Opus 4.8" on every board card
while the pipeline was already dispatching Opus 5 — the label was a string on
the tier. We found it from a screenshot. Separately, the Opus 5 rotation missed
`repairer` in agents.yaml and CI stayed green because the ladder test named
`engineer`/`planner` by hand. The fix for both is the same shape: stop learning
about model changes by eye.

WHAT THIS MODULE IS, AND WHAT IT DELIBERATELY IS NOT.

  * IS: a read-only catalog of what /v1/models actually offers, a committed
    snapshot (models.json) the console can read with no Anthropic credential,
    and a weekly check that says "the ladder is behind".
  * IS NOT: an upgrade path. Nothing here may change LADDER, agents.yaml, or
    any model id. Adoption stays a deliberate human edit, because a new model
    can ship breaking API changes (Opus 5 turned thinking ON by default, so
    max_tokens caps thinking + response together, and disabling thinking 400s
    above `high` effort) and can double the bill (Fable 5 is ~2x Opus per
    token). An unattended overnight fleet-wide adoption is exactly the failure
    the ladder's Fable exclusion already exists to prevent.

THE TRAPS THESE TESTS PIN.

  * Model ids DO NOT SORT. `"claude-opus-10" < "claude-opus-5"` is True in
    Python — a string sort ranks Opus 10 as older. Ranking is by the API's
    `created_at`, never by the id.
  * A catalog outage must never block a dispatch: a failed fetch returns an
    EMPTY catalog, drift reports nothing (never raise on missing data), and
    `model_fallback.select()` still resolves a model off the static ladder.
  * Auth is ONE shared seam (`model_fallback.auth_headers`) used by both the
    probe and the catalog — copy-pasting it is how the second copy drifts.
  * Zero network in this suite: the module-level fixture below replaces the
    real fetch with a function that raises, so any test that forgets to inject
    a fake fails loudly instead of calling Anthropic.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_catalog.py -v
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import model_catalog as mc  # noqa: E402
import model_fallback as mf  # noqa: E402

SNAPSHOT_PATH = ROOT / "models.json"
AGENTS_YAML = ROOT / "agents.yaml"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "model-drift.yml"
MEDIC_STUB = ROOT / ".github" / "workflows" / "self-medic.yml"

OPUS = "claude-opus-5"
SONNET = "claude-sonnet-4-6"
FABLE = "claude-fable-5"
RETIRED_OPUS = "claude-opus-4-8"

# Captured BEFORE the no-network fixture replaces it, so the one test that
# exercises the real fetch's no-token degrade can still reach it.
REAL_FETCH = mc._fetch_real


def model(id_, display_name=None, created_at=None):
    """One entry as the catalog exposes it."""
    return {"id": id_, "display_name": display_name, "created_at": created_at}


def payload(*models):
    """A /v1/models-shaped response envelope."""
    return {
        "data": [{"type": "model", **m} for m in models],
        "has_more": False,
    }


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Zero network calls: the real fetch raises if any test reaches it."""

    def _boom():
        raise AssertionError("a test reached the live /v1/models endpoint")

    monkeypatch.setattr(mc, "_fetch_real", _boom)
    mc.clear_catalog_cache()
    mf.clear_availability_cache()
    yield
    mc.clear_catalog_cache()
    mf.clear_availability_cache()


# --------------------------------------------------------------------------- #
# 1. The catalog exposes id / display_name / created_at                        #
# --------------------------------------------------------------------------- #

def test_parse_returns_id_display_name_and_created_at():
    out = mc.parse_catalog(
        payload(
            {
                "id": OPUS,
                "display_name": "Claude Opus 5",
                "created_at": "2026-08-01T00:00:00Z",
            }
        )
    )
    assert out == [
        {
            "id": OPUS,
            "display_name": "Claude Opus 5",
            "created_at": "2026-08-01T00:00:00Z",
        }
    ]


def test_parse_tolerates_missing_optional_fields():
    # The console needs the keys present even when the API omits them, so a
    # consumer never has to guess between "absent" and "unknown".
    assert mc.parse_catalog(payload({"id": OPUS})) == [
        {"id": OPUS, "display_name": None, "created_at": None}
    ]


def test_parse_drops_entries_without_an_id():
    out = mc.parse_catalog(payload({"display_name": "nameless"}, {"id": OPUS}))
    assert [m["id"] for m in out] == [OPUS]


@pytest.mark.parametrize("junk", [None, {}, {"data": "nope"}, [], "text"])
def test_parse_of_junk_is_an_empty_catalog(junk):
    assert mc.parse_catalog(junk) == []


# --------------------------------------------------------------------------- #
# 2. Auth is ONE shared helper — the probe's behaviour is unchanged            #
# --------------------------------------------------------------------------- #

def test_auth_is_a_single_shared_seam():
    # Not "two identical blocks" — literally the same function object. A copy
    # is what drifts the day one side gains a header.
    assert mc.auth_headers is mf.auth_headers


def test_api_key_takes_the_x_api_key_path(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oat-test")
    headers = mf.auth_headers()
    assert headers["x-api-key"] == "sk-test"
    assert headers["anthropic-version"] == "2023-06-01"
    # The API key wins when both are set — same precedence the probe had.
    assert "authorization" not in headers
    assert "anthropic-beta" not in headers


def test_oauth_token_takes_the_bearer_plus_beta_path(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oat-test")
    headers = mf.auth_headers()
    assert headers["authorization"] == "Bearer oat-test"
    assert headers["anthropic-beta"] == "oauth-2025-04-20"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "x-api-key" not in headers


def test_no_token_at_all_has_no_headers(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    assert mf.auth_headers() is None


def test_probe_degrade_on_no_token_is_unchanged(monkeypatch):
    # The probe's documented contract: no token → inconclusive → AVAILABLE, and
    # it returns before any network call. The extraction must not change that.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    assert mf._probe_real(OPUS) is True


def test_catalog_fetch_without_a_token_is_empty_not_a_crash(monkeypatch):
    # The catalog's own degrade at the same seam: no credential → no request at
    # all → empty catalog → callers fall back to the static ladder.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    assert REAL_FETCH() == {}
    assert mc.fetch_catalog(fetch=REAL_FETCH, clock=lambda: 0.0) == []


# --------------------------------------------------------------------------- #
# 3. Cache with an injectable clock — no network in tests                      #
# --------------------------------------------------------------------------- #

def test_fetch_catalog_parses_an_injected_payload():
    catalog = mc.fetch_catalog(
        fetch=lambda: payload({"id": OPUS, "display_name": "Claude Opus 5"}),
        clock=lambda: 0.0,
    )
    assert [m["id"] for m in catalog] == [OPUS]


def test_catalog_is_cached_for_the_ttl_then_refetched():
    now = [1000.0]
    calls = []

    def fake():
        calls.append(1)
        return payload({"id": OPUS})

    mc.fetch_catalog(fetch=fake, clock=lambda: now[0])
    mc.fetch_catalog(fetch=fake, clock=lambda: now[0])
    assert len(calls) == 1, "a second call inside the TTL must not refetch"

    now[0] += mc.CATALOG_TTL_SECONDS + 1
    mc.fetch_catalog(fetch=fake, clock=lambda: now[0])
    assert len(calls) == 2, "past the TTL the catalog is refreshed"


def test_fetch_failure_returns_an_empty_catalog():
    def boom():
        raise OSError("network down")

    assert mc.fetch_catalog(fetch=boom, clock=lambda: 0.0) == []


def test_a_failed_fetch_is_not_cached():
    # Caching an outage would blind us for a whole TTL window for no gain.
    def boom():
        raise OSError("network down")

    assert mc.fetch_catalog(fetch=boom, clock=lambda: 0.0) == []
    catalog = mc.fetch_catalog(fetch=lambda: payload({"id": OPUS}), clock=lambda: 0.0)
    assert [m["id"] for m in catalog] == [OPUS]


def test_unparseable_response_returns_an_empty_catalog():
    assert mc.fetch_catalog(fetch=lambda: "<html>502</html>", clock=lambda: 0.0) == []


def test_a_catalog_outage_never_blocks_a_dispatch():
    """The acceptance criterion in one test: fetch dies → empty catalog → no
    drift reported → the caller still resolves a model off the static ladder."""

    def boom():
        raise OSError("network down")

    catalog = mc.fetch_catalog(fetch=boom, clock=lambda: 0.0)
    assert catalog == []
    assert mc.stale_ladder(mf.LADDER, catalog) == []
    assert mf.select("engineer", probe=lambda m: True, clock=lambda: 0.0) == mf.LADDER[0]


# --------------------------------------------------------------------------- #
# 4. stale_ladder() ranks by created_at — never by the id                      #
# --------------------------------------------------------------------------- #

def test_model_ids_do_not_sort():
    # The trap, pinned as documentation: a lexical sort ranks Opus 10 BELOW
    # Opus 5. Everything below must rank by created_at instead.
    assert "claude-opus-10" < "claude-opus-5"


def test_opus_10_is_newer_than_opus_5_by_created_at():
    catalog = [
        model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z"),
        model("claude-opus-10", "Claude Opus 10", "2027-02-01T00:00:00Z"),
    ]
    findings = mc.stale_ladder([OPUS], catalog)
    assert [f["pinned"] for f in findings] == [OPUS]
    assert findings[0]["newer"][0]["id"] == "claude-opus-10"
    assert findings[0]["newer"][0]["display_name"] == "Claude Opus 10"


def test_the_newest_pin_is_not_stale():
    catalog = [
        model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z"),
        model(RETIRED_OPUS, "Claude Opus 4.8", "2026-05-01T00:00:00Z"),
    ]
    assert mc.stale_ladder([OPUS], catalog) == []


def test_a_newer_model_in_another_family_does_not_flag_a_tier():
    # Comparison is within the model line. Otherwise the Sonnet fallback tier
    # would report "Opus is newer" every single week, forever, and the alert
    # would be trained into noise on day one.
    catalog = [
        model(SONNET, "Claude Sonnet 4.6", "2026-06-01T00:00:00Z"),
        model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z"),
    ]
    assert mc.stale_ladder([SONNET], catalog) == []


def test_a_pin_absent_from_the_catalog_is_not_flagged():
    # Never raise on unknown/missing data — we cannot rank what we cannot see.
    catalog = [model("claude-opus-10", "Claude Opus 10", "2027-02-01T00:00:00Z")]
    assert mc.stale_ladder(["claude-mystery-1"], catalog) == []


def test_entries_without_created_at_are_never_ranked():
    catalog = [
        model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z"),
        model("claude-opus-10", "Claude Opus 10", None),
    ]
    assert mc.stale_ladder([OPUS], catalog) == []


def test_a_dated_snapshot_of_the_pinned_alias_is_not_drift():
    # `claude-opus-5-20260915` IS `claude-opus-5` — the dated form of the same
    # alias. Reporting it as a newer model would fire a card for a no-op.
    catalog = [
        model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z"),
        model("claude-opus-5-20260915", "Claude Opus 5", "2026-09-15T00:00:00Z"),
    ]
    assert mc.stale_ladder([OPUS], catalog) == []


def test_a_point_release_alias_is_drift():
    # `claude-opus-5-1` is a different model that merely shares the prefix.
    catalog = [
        model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z"),
        model("claude-opus-5-1", "Claude Opus 5.1", "2026-11-01T00:00:00Z"),
    ]
    findings = mc.stale_ladder([OPUS], catalog)
    assert [f["newer"][0]["id"] for f in findings] == ["claude-opus-5-1"]


def test_an_empty_catalog_is_never_stale():
    assert mc.stale_ladder(mf.LADDER, []) == []


def test_every_tier_of_the_ladder_is_checked_not_a_named_subset():
    # The generalised lesson from the missed `repairer`: enumerate the ladder,
    # never a hand-written list of tiers.
    catalog = [
        model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z"),
        model("claude-opus-10", "Claude Opus 10", "2027-02-01T00:00:00Z"),
        model(SONNET, "Claude Sonnet 4.6", "2026-06-01T00:00:00Z"),
        model("claude-sonnet-9", "Claude Sonnet 9", "2027-01-01T00:00:00Z"),
    ]
    findings = mc.stale_ladder([OPUS, SONNET], catalog)
    assert [f["pinned"] for f in findings] == [OPUS, SONNET]


def test_stale_ladder_accepts_the_agents_yaml_ladder_shape():
    # agents.yaml stores a ladder as [{model:, reason:}] — the console reads it
    # from there, so the same normalization model_fallback does applies here.
    catalog = [
        model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z"),
        model("claude-opus-10", "Claude Opus 10", "2027-02-01T00:00:00Z"),
    ]
    findings = mc.stale_ladder([{"model": OPUS, "reason": "preferred"}], catalog)
    assert [f["pinned"] for f in findings] == [OPUS]


# --------------------------------------------------------------------------- #
# 5. models.json — generated, keeps retired ids, adopts nothing                #
# --------------------------------------------------------------------------- #

def test_snapshot_entry_shape():
    snap = mc.build_snapshot(
        [model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z")], known_ids=()
    )
    assert snap["models"] == [
        {
            "id": OPUS,
            "display_name": "Claude Opus 5",
            "created_at": "2026-08-01T00:00:00Z",
            "in_catalog": True,
        }
    ]


def test_snapshot_keeps_ids_that_rotated_out_of_the_api():
    previous = {
        "models": [
            {
                "id": RETIRED_OPUS,
                "display_name": "Claude Opus 4.8",
                "created_at": "2026-05-01T00:00:00Z",
                "in_catalog": True,
            }
        ]
    }
    snap = mc.build_snapshot(
        [model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z")],
        previous=previous,
        known_ids=(),
    )
    retired = {m["id"]: m for m in snap["models"]}[RETIRED_OPUS]
    # The label survives the rotation — that is the whole point of the seam:
    # a card stamped with a retired id still renders as "Claude Opus 4.8".
    assert retired["display_name"] == "Claude Opus 4.8"
    assert retired["created_at"] == "2026-05-01T00:00:00Z"
    assert retired["in_catalog"] is False


def test_snapshot_carries_every_id_the_code_knows():
    # KNOWN_MODELS is the superset the markers validate against, retired ids
    # included; the snapshot must never drop one the pipeline can still read.
    snap = mc.build_snapshot([model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z")])
    assert set(mf.KNOWN_MODELS) <= {m["id"] for m in snap["models"]}


def test_snapshot_is_ordered_newest_first_and_deterministic():
    catalog = [
        model(SONNET, "Claude Sonnet 4.6", "2026-06-01T00:00:00Z"),
        model("claude-opus-10", "Claude Opus 10", "2027-02-01T00:00:00Z"),
        model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z"),
    ]
    first = mc.build_snapshot(catalog, known_ids=())
    assert [m["id"] for m in first["models"]] == ["claude-opus-10", OPUS, SONNET]
    # Deterministic ordering is what keeps an unchanged catalog a no-op commit.
    assert mc.build_snapshot(list(reversed(catalog)), known_ids=()) == first


def test_write_snapshot_reports_no_change_on_identical_content(tmp_path):
    out = tmp_path / "models.json"
    snap = mc.build_snapshot([model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z")])
    assert mc.write_snapshot(out, snap) is True
    before = out.read_bytes()
    assert mc.write_snapshot(out, snap) is False
    assert out.read_bytes() == before


def test_generating_the_snapshot_never_mutates_the_ladder_or_agents_yaml(tmp_path):
    """The no-auto-adopt guarantee, mechanically: generate a snapshot in which
    a brand-new model is by far the newest, and prove nothing selectable moved.
    """
    ladder_before = list(mf.LADDER)
    known_before = set(mf.KNOWN_MODELS)
    yaml_before = hashlib.sha256(AGENTS_YAML.read_bytes()).hexdigest()
    fallback = ROOT / "scripts" / "model_fallback.py"
    fallback_before = hashlib.sha256(fallback.read_bytes()).hexdigest()

    out = tmp_path / "models.json"
    mc.write_snapshot(
        out,
        mc.build_snapshot(
            [model("claude-opus-99", "Claude Opus 99", "2099-01-01T00:00:00Z")]
        ),
    )

    assert "claude-opus-99" in out.read_text(), "the snapshot did record it"
    assert mf.LADDER == ladder_before == [OPUS, SONNET]
    assert "claude-opus-99" not in mf.LADDER
    assert set(mf.KNOWN_MODELS) == known_before
    assert hashlib.sha256(AGENTS_YAML.read_bytes()).hexdigest() == yaml_before
    assert hashlib.sha256(fallback.read_bytes()).hexdigest() == fallback_before
    assert mf.select("engineer", probe=lambda m: True, clock=lambda: 0.0) == OPUS


def test_the_committed_snapshot_is_present_and_shaped():
    assert SNAPSHOT_PATH.is_file(), f"missing generated snapshot {SNAPSHOT_PATH}"
    snap = json.loads(SNAPSHOT_PATH.read_text())
    ids = {m["id"] for m in snap["models"]}
    # Includes ids that have rotated out — the console reads THIS file to label
    # a model, so an id the pipeline can still stamp must resolve here.
    assert RETIRED_OPUS in ids
    assert set(mf.KNOWN_MODELS) <= ids, "the snapshot dropped a known model id"
    for entry in snap["models"]:
        assert {"id", "display_name", "created_at"} <= set(entry)


def test_the_committed_snapshot_pins_no_ladder():
    # Data only. If this file ever grows a "ladder"/"selected"/"default" key it
    # has stopped being a catalog and started being a decision.
    snap = json.loads(SNAPSHOT_PATH.read_text())
    assert set(snap) <= {"source", "models"}


# --------------------------------------------------------------------------- #
# 6. CLI — the seam the workflow drives, exercised with a fake catalog         #
# --------------------------------------------------------------------------- #

def _fake_catalog_env(monkeypatch, *models):
    monkeypatch.setenv("BUREAU_FAKE_CATALOG", json.dumps(payload(*models)))


def test_cli_snapshot_writes_the_file(monkeypatch, tmp_path):
    _fake_catalog_env(
        monkeypatch,
        {"id": OPUS, "display_name": "Claude Opus 5", "created_at": "2026-08-01T00:00:00Z"},
    )
    out = tmp_path / "models.json"
    assert mc.main(["snapshot", str(out)]) == 0
    written = json.loads(out.read_text())
    assert {"id": OPUS, "display_name": "Claude Opus 5"}.items() <= written["models"][0].items()


def test_cli_snapshot_on_an_outage_leaves_a_good_file_alone(monkeypatch, tmp_path):
    _fake_catalog_env(
        monkeypatch,
        {"id": OPUS, "display_name": "Claude Opus 5", "created_at": "2026-08-01T00:00:00Z"},
    )
    out = tmp_path / "models.json"
    mc.main(["snapshot", str(out)])
    good = out.read_bytes()

    monkeypatch.setenv("BUREAU_FAKE_CATALOG", json.dumps({"data": []}))
    assert mc.main(["snapshot", str(out)]) == 0, "an outage is not a red run"
    assert out.read_bytes() == good, "an empty catalog must never blank the file"


def test_cli_check_drift_exits_3_and_writes_one_card(monkeypatch, tmp_path):
    snap = tmp_path / "models.json"
    mc.write_snapshot(
        snap,
        mc.build_snapshot(
            [
                model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z"),
                model("claude-opus-10", "Claude Opus 10", "2027-02-01T00:00:00Z"),
                model(SONNET, "Claude Sonnet 4.6", "2026-06-01T00:00:00Z"),
            ],
            known_ids=(),
        ),
    )
    title_file = tmp_path / "title.txt"
    body_file = tmp_path / "body.md"
    rc = mc.main(
        [
            "check-drift",
            str(snap),
            "--title-file",
            str(title_file),
            "--body-file",
            str(body_file),
        ]
    )
    assert rc == 3
    title = title_file.read_text().strip()
    # The title encodes the FINDING, so `linear_ops.py find-open "<title>"`
    # matches next week's identical run and no duplicate card is minted.
    assert title == f"Model drift: {OPUS} → claude-opus-10"
    body = body_file.read_text()
    assert OPUS in body and "claude-opus-10" in body
    assert "2027-02-01" in body, "the body shows created_at, the ranking basis"
    assert "do not" in body.lower(), "the body must say adoption is a human edit"
    # find-open ignores terminal cards, so a CANCELLED finding comes back as a
    # fresh card next Monday — the body has to tell the reader to park it.
    assert "backlog" in body.lower()


def test_cli_drift_title_is_stable_for_the_same_finding():
    finding = mc.stale_ladder(
        [OPUS],
        [
            model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z"),
            model("claude-opus-10", "Claude Opus 10", "2027-02-01T00:00:00Z"),
        ],
    )
    assert mc.drift_title(finding) == mc.drift_title(finding)
    assert mc.drift_title(finding) == f"Model drift: {OPUS} → claude-opus-10"


def test_cli_check_drift_is_quiet_when_the_ladder_is_current(monkeypatch, tmp_path):
    snap = tmp_path / "models.json"
    mc.write_snapshot(
        snap,
        mc.build_snapshot(
            [
                model(OPUS, "Claude Opus 5", "2026-08-01T00:00:00Z"),
                model(SONNET, "Claude Sonnet 4.6", "2026-06-01T00:00:00Z"),
            ],
            known_ids=(),
        ),
    )
    title_file = tmp_path / "title.txt"
    assert mc.main(["check-drift", str(snap), "--title-file", str(title_file)]) == 0
    assert not title_file.exists(), "no finding, no card file"


def test_cli_check_drift_on_the_committed_snapshot_never_crashes(tmp_path):
    # The bootstrap file has nothing observed yet; the check must be a quiet
    # no-op rather than an exception on the very first scheduled run.
    assert mc.main(["check-drift", str(SNAPSHOT_PATH)]) in (0, 3)


def test_cli_rejects_an_unknown_command():
    assert mc.main(["upgrade-the-ladder"]) == 2


# --------------------------------------------------------------------------- #
# 7. The scheduled workflow — one card, no duplicates, data-only commit        #
# --------------------------------------------------------------------------- #

def _workflow_doc() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text()


def _on(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _run_steps(doc: dict) -> str:
    return "\n".join(
        step.get("run", "")
        for job in (doc.get("jobs") or {}).values()
        for step in (job or {}).get("steps") or []
        if isinstance(step, dict)
    )


def test_the_drift_workflow_is_scheduled():
    schedule = _on(_workflow_doc()).get("schedule")
    assert schedule, "the drift check must run on a schedule (weekly is fine)"
    assert all("cron" in entry for entry in schedule)
    assert "workflow_dispatch" in _on(_workflow_doc()), "manual re-run must exist"


def test_the_drift_workflow_refreshes_the_snapshot_and_checks_drift():
    runs = _run_steps(_workflow_doc())
    assert "model_catalog.py snapshot models.json" in runs
    assert "model_catalog.py check-drift models.json" in runs


def test_the_drift_workflow_opens_one_card_and_never_duplicates_it():
    runs = _run_steps(_workflow_doc())
    assert "linear_ops.py find-open" in runs, "dedupe before creating"
    assert "linear_ops.py create" in runs
    assert runs.index("linear_ops.py find-open") < runs.index("linear_ops.py create"), (
        "the existing-card lookup must precede the create, or every weekly run "
        "mints a duplicate for the same finding"
    )


def test_the_drift_workflow_commits_models_json_and_nothing_else():
    runs = _run_steps(_workflow_doc())
    assert "git add models.json" in runs
    assert "git add ." not in runs and "git add -A" not in runs
    # Not just "we only staged one path" — the workflow PROVES the staged set
    # before pushing, so a future edit cannot smuggle agents.yaml along.
    assert "git diff --cached --name-only" in runs


def test_the_drift_workflow_cannot_touch_the_ladder():
    text = _workflow_text()
    assert "agents.yaml" not in text
    assert "model_fallback.py" not in text


def test_the_medic_watches_the_drift_workflow():
    # DRE-2036: every workflow that runs under its own name is in the medic
    # watch list, or its red runs go undiagnosed.
    name = _workflow_doc().get("name")
    watched = _on(yaml.safe_load(MEDIC_STUB.read_text()))["workflow_run"]["workflows"]
    assert name in watched
