#!/usr/bin/env python3
"""Should the post-approval review run AGAIN — at what ceiling, and how is that
run asked for (DRE-3286).

One module, because two workflow cards follow this one (`review death → retry`
and `re-plan → re-review`) and both of them need the same four answers. A rule
several pieces read is split out FIRST and the siblings cite it, rather than
each inventing its own — which is exactly how `alerts._advanced_recently` came
to say *stalled* about the same field `project_rollups.is_stale` calls
*healthy* (standards/card-quality.md).

Nothing here changes `plan.yml`. It ships the contract and its tests.

WHAT IT DOES NOT OWN. The tombstone grammar, the planning cycle, the trusted
credential a record is read under, and the review's first-run ceiling are all
`plan_critic`'s (DRE-3241), and this module imports them rather than
re-deriving them:

  * `plan_critic.parse_deaths` / `trusted_bodies` — a 🪦 record counts only
    when the pipeline wrote it AND the comment says nothing else. Anyone with
    comment access on the epic can post the line.
  * `plan_critic.current_cycle` — a death from a plan that no longer exists is
    not this plan's death.
  * `plan_critic.post_release` — the sentence the sweep already tells the CEO
    about a dead review. The park note quotes it so the two never describe one
    run differently.
  * `plan_critic.post_review_turns` — the FIRST run's ceiling, sized from the
    plan. This module only says what a RETRY gets.

WHY THE MEDIC IS NOT INVOLVED IN A TURN-CAP DEATH, and this module is not
involved in any other kind. `medic_retry.decide` already retries a non-turn
death once and REFUSES a turn-cap one outright (`RULE_TURN_EXHAUSTION`: a
budget ceiling is not a flake, and the same run re-run hits the same wall).
Retrying a turn cap is only useful with a BIGGER ceiling, which is the one
thing the medic cannot change — so the retry with headroom is `after_death`'s,
every other death stays the medic's, and neither ever acts on the other's.

CLI:
  ceiling --epic E --children N --thread-file F --github-output OUT
                                      `max_turns=<int>`. NEVER exits non-zero.
  after-death --epic E --thread-file F --subtype S --github-output OUT
              [--note-file NOTE]
                                      `action=retry|park|leave`, `runs=<ids>`,
                                      and the CEO-facing note for the park.
  dispatch --epic E --repo OWNER/NAME --reason R
                                      the ACTIVATE-route repository_dispatch.
                                      Non-zero on a failed dispatch (DRE-2034:
                                      no receipt on an unconfirmed dispatch).
  card-set --before FILE --after FILE --github-output OUT
                                      `changed=`, `added=`, `removed=` over
                                      two `linear_ops.py children-json` files.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plan_critic  # noqa: E402
import plan_run  # noqa: E402

# --- The contract the siblings read -----------------------------------------

#: The lane word `plan.yml`'s route step compares `client_payload.trigger_state`
#: against to choose ACTIVATE over PLAN. Lower-cased, because that is how the
#: relay sends the lane a card entered — a capitalised copy silently takes the
#: plan route and re-plans an approved epic.
TRIGGER_STATE_ACTIVATE = "in progress"

#: Why the run was asked for, on the `reason` payload key. Three askers, three
#: words, so a run can say which of them started it.
REASON_REVIEW_RETRY = "review-retry"   # the review died; try again with headroom
REASON_RE_REVIEW = "re-review"         # the plan changed; read it again
REASON_RERUN_ACT = "re-run"            # a person asked, with the act below

#: The act. A comment whose ENTIRE body, stripped, equals this line asks for
#: the review to run again; the same words inside a sentence do not (see
#: `is_rerun_act`). The relay in agent-bureau and the console mirror this exact
#: string, so it is written once, here.
RERUN_REVIEW_ACT = "▶️ re-run the review"

#: The retry's headroom. A review that ran out of turns needs a bigger ceiling
#: or the re-run hits the same wall — but only so much bigger: above the cap a
#: larger number moves the wall without improving the read (DRE-2924, the QA
#: critic's own retry ceiling).
RETRY_MULTIPLIER = 1.5
POST_REVIEW_RETRY_CAP = 180

#: How many deaths one plan gets before a person is asked. Two, and the second
#: parks — the operator rule that two turn-cap deaths on one card mean SPLIT
#: rather than a third attempt (standards/card-quality.md).
MAX_DEATHS = 2

#: What `after_death` answers.
RETRY = "retry"
PARK = "park"
LEAVE = "leave"

#: The subtype an action reports for a run cut off at its ceiling — one of the
#: two ways a death reads as the turn cap, and `plan_critic`'s, not a second
#: copy of the string. The other way is the row's own numbers
#: (`plan_critic.hit_the_turn_cap`), which is what run 34144302622 needed:
#: it FINISHED at turn 51 of a 48-turn ceiling and reported `success`.
#: Every death that is neither is the medic's, which retries it once already.
TURN_CAP_SUBTYPE = plan_critic.TURN_CAP_SUBTYPE


# --- The ceilings ------------------------------------------------------------

def retry_ceiling(ceiling) -> int:
    """The ceiling a RETRY of a dead review gets, from the one it died at.

    `ceil(ceiling x 1.5)`, capped. Rounds UP so a ceiling never shrinks on the
    retry, and an UNKNOWN ceiling is sized from `POST_REVIEW_TURNS_DEFAULT`
    rather than from zero — a tombstone Linear could not report a number for
    is unknown, not nothing (standards/console-honesty.md rule 2).
    """
    try:
        base = int(ceiling)
    except (TypeError, ValueError):
        base = plan_critic.POST_REVIEW_TURNS_DEFAULT
    if base <= 0:
        base = plan_critic.POST_REVIEW_TURNS_DEFAULT
    return min(POST_REVIEW_RETRY_CAP, math.ceil(base * RETRY_MULTIPLIER))


def deaths_since_last_round(bodies: list, epic: str | None = None) -> list[dict]:
    """The post-stage tombstones on this planning attempt that NO round has
    answered yet, oldest→newest.

    A tombstone older than a post-stage round is history: the re-run it asked
    for happened, and that round is the record. This is the same reading
    `plan_critic.post_release` takes of the same thread — a death newer than
    every round is POST_DIED, an older one is not — so the sweep and this
    module never disagree about one epic.

    The credential is `plan_critic`'s and is not re-implemented here: the
    pipeline wrote the comment, and the comment says nothing but the record.
    """
    open_deaths: list[dict] = []
    for body in plan_critic.trusted_bodies(
            plan_critic.current_cycle(bodies, epic)):
        rounds = plan_critic.parse_markers([body])
        if rounds and rounds[0]["stage"] == plan_critic.STAGE_POST:
            # A round answered everything before it.
            open_deaths = []
            continue
        deaths = plan_critic.parse_deaths([body])
        if deaths and deaths[0]["stage"] == plan_critic.STAGE_POST:
            open_deaths.append(deaths[0])
    return open_deaths


def ceiling_for_run(children, bodies: list,
                    epic: str | None = None) -> tuple[int, str]:
    """`(max_turns, why)` for the post-approval review about to run.

    Sized from the plan when the newest post-stage record is a round or there
    is none — `plan_critic.post_review_turns`, never a second copy of it. When
    the newest record is a TOMBSTONE the review is being retried, and it gets
    the dead run's ceiling with headroom instead.
    """
    deaths = deaths_since_last_round(bodies, epic)
    if deaths:
        died_at = deaths[-1]["ceiling"]
        turns = retry_ceiling(died_at)
        at = died_at if died_at is not None else "an unknown ceiling"
        return turns, f"retry after a death at {at}: {turns}"
    turns = plan_critic.post_review_turns(children)
    try:
        how_many = str(int(children))
    except (TypeError, ValueError):
        how_many = "an unknown number of"
    return turns, f"sized from {how_many} cards"


# --- What happens after a death ---------------------------------------------

def _runs(deaths: list[dict]) -> list[str]:
    return [str(d.get("run") or "?") for d in deaths]


def after_death(bodies: list, epic: str | None,
                subtype: str | None) -> tuple[str, str]:
    """`(action, why)` — what to do about the review death just recorded.

    Read AFTER the tombstone is on the epic, so the death being decided is the
    newest row `deaths_since_last_round` returns.

      * `leave` — a death that is not the turn cap. That death is the medic's:
        it retries a non-turn death once already, and it refuses a turn-cap one
        (`medic_retry.RULE_TURN_EXHAUSTION`). Checked FIRST, so the two never
        both act on one death whatever the count says.
      * `retry` — the first turn-cap death since the last round. The re-run
        gets `retry_ceiling`'s headroom, which is the only thing that makes
        re-running a budget ceiling worth doing.
      * `park` — the second or later. Naming every run in the tombstones,
        because "it died twice" is the fact that asks a person to split the
        plan rather than pay for a third attempt.

    WHAT MAKES IT THE TURN CAP is the RECORD, not the enum alone (DRE-3501):
    the subtype the action reported, OR the tombstone's own
    `turns >= ceiling` — the question `plan_critic.hit_the_turn_cap` answers
    for the tombstone sentence too, asked of the row this run just wrote
    rather than of a flag the workflow would have to pass twice. Run
    34144302622 reported `success` at turn 51 of a 48-turn ceiling: `success`
    is not `error_max_turns`, so this answered `leave`, the medic refuses a
    turn cap, and nothing re-ran the review.
    """
    deaths = deaths_since_last_round(bodies, epic)
    newest = deaths[-1] if deaths else {}
    if subtype != TURN_CAP_SUBTYPE and not plan_critic.hit_the_turn_cap(newest):
        return LEAVE, (
            f"the review died `{subtype or '?'}` inside its ceiling, which is "
            "not a turn cap — that death belongs to the medic, which retries "
            "it once; nothing here acts on it"
        )
    if len(deaths) >= MAX_DEATHS:
        listed = ", ".join(_runs(deaths))
        return PARK, (
            f"{len(deaths)} reviews of this plan have run out of turns "
            f"(runs {listed}) — the bound, so it parks for a person rather "
            "than paying for a third attempt"
        )
    ceiling = deaths[-1]["ceiling"] if deaths else None
    return RETRY, (
        "the first review death since the last round, and it ran out of "
        f"turns — retrying at {retry_ceiling(ceiling)} turns"
    )


def park_note(bodies: list, epic: str) -> str:
    """The CEO-facing note for the park, in plain English.

    The death sentence itself is `plan_critic.post_release`'s, quoted rather
    than rewritten, so the note and the sweep's own refusal describe the same
    run in the same words.
    """
    deaths = deaths_since_last_round(bodies, epic)
    _state, detail = plan_critic.post_release(bodies, epic)
    listed = ", ".join(_runs(deaths))
    return (
        f"🛑 **The post-approval review of {epic} has run out of turns "
        f"{len(deaths)} times** — runs {listed}. It is not being started "
        "again.\n\n"
        f"The most recent one: {plan_critic.one_line(detail)}.\n\n"
        "Nothing has been found wrong with the plan and nothing has started "
        "building. A review that keeps running out of turns is a plan that is "
        "too big to read in one pass, so this needs a person rather than a "
        "third attempt.\n\n"
        f"**What to do:** split the plan into smaller epics — then, once it "
        f"is smaller, {plan_critic.REAPPROVE_HOW}."
    )


def is_rerun_act(body: str | None) -> bool:
    """Is this comment the re-run act?

    The WHOLE body, stripped, or it is not the act. The line inside a sentence
    is somebody talking about re-running the review, and a notice this pipeline
    posts that happened to contain the words would otherwise re-run the review
    every time it was written (`plan_critic._sole_record`'s rule, for the same
    reason: a record that shares a comment with prose is a record prose can
    forge).
    """
    return (body or "").strip() == RERUN_REVIEW_ACT


# --- Which cards the re-plan changed ----------------------------------------

def card_set_diff(before: list[str], after: list[str]) -> dict:
    """`{changed, added, removed}` over two lists of child identifiers.

    Order-insensitive: a re-plan that rewrote every card body but kept the same
    children has not changed the SET, and the question this answers is which
    cards appeared and which went away.
    """
    b = {str(x) for x in (before or []) if x}
    a = {str(x) for x in (after or []) if x}
    added = sorted(a - b)
    removed = sorted(b - a)
    return {"changed": bool(added or removed), "added": added, "removed": removed}


def activate_payload(card: dict, reason: str | None = None) -> dict:
    """The `client_payload` that asks for the ACTIVATE route.

    `plan_run.payload`'s, with `trigger_state` set — never a second hand-built
    copy of the six base fields, or the two dispatchers would come to describe
    one card differently.
    """
    return plan_run.payload(card, trigger_state=TRIGGER_STATE_ACTIVATE,
                            reason=reason)


# --- The CLI seams ----------------------------------------------------------

def _write_outputs(path: str | None, pairs: list[tuple[str, str]]) -> None:
    """`$GITHUB_OUTPUT`, one line per key. Same writer shape as
    `plan_critic._write_outputs`, including the collapse to one line: a note an
    agent wrote must never smuggle a second output key."""
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            for key, value in pairs:
                f.write(f"{key}={plan_critic.one_line(value)}\n")
    except OSError as exc:
        print(f"review rerun: could not write step outputs: {exc}")


def _thread(path: str | None) -> list:
    """The comment thread from a `dump-comments --with-authors` file.

    Unreadable is EMPTY, never an exception: every caller of this degrades to
    the un-retried answer, and a review that cannot start is worse than one
    that starts at its first-run ceiling.
    """
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"review rerun: could not read the thread ({exc}) — "
              "reading it as empty")
        return []
    return loaded if isinstance(loaded, list) else []


def _identifiers(path: str | None) -> list[str]:
    """The child identifiers out of a `children-json` file."""
    rows = _thread(path)
    out = []
    for row in rows:
        ident = row.get("identifier") if isinstance(row, dict) else row
        if ident:
            out.append(str(ident))
    return out


def _cmd_ceiling(args) -> int:
    """The review's ceiling as a step output. ALWAYS 0: a sizing that failed
    must degrade to `post_review_turns`, never wedge the review — the same
    contract `plan_critic post-turns` carries, and the workflow keeps a static
    fallback on top of it."""
    try:
        turns, why = ceiling_for_run(args.children, _thread(args.thread_file),
                                     args.epic)
    except Exception as exc:  # noqa: BLE001 — see the docstring
        turns = plan_critic.post_review_turns(args.children)
        why = f"could not read the thread ({exc}) — sized from the plan"
    _write_outputs(args.github_output, [("max_turns", str(turns))])
    print(f"post-approval review ceiling: {turns} turns — {why}")
    return 0


def _cmd_after_death(args) -> int:
    thread = _thread(args.thread_file)
    action, why = after_death(thread, args.epic, args.subtype)
    deaths = deaths_since_last_round(thread, args.epic)
    _write_outputs(args.github_output, [
        ("action", action),
        ("runs", ",".join(_runs(deaths))),
    ])
    if args.note_file and action == PARK:
        with open(args.note_file, "w", encoding="utf-8") as f:
            f.write(park_note(thread, args.epic) + "\n")
    print(f"{action}: {why}")
    return 0


def _cmd_dispatch(args) -> int:
    """Ask for the epic's ACTIVATE run.

    Imported here rather than at module scope so `ceiling` and `card-set` — the
    two seams that must never fail — carry no Linear client at all.

    Non-zero on a failed dispatch, and the error printed: DRE-2034's rule is
    that a receipt is never written on an unconfirmed dispatch, and the step
    that writes one reads this exit code.
    """
    import linear_ops  # noqa: PLC0415 — see the docstring
    try:
        card = (linear_ops.gql(plan_run.CARD_QUERY, {"id": args.epic}) or
                {}).get("issue")
    except Exception as exc:  # noqa: BLE001 — an unreadable card is not a crash
        print(f"ERROR: review rerun {args.epic}: could not read the card: {exc}",
              file=sys.stderr)
        return 1
    if not card:
        print(f"ERROR: review rerun {args.epic}: Linear returned no such card",
              file=sys.stderr)
        return 1
    ok, err = plan_run.fire(card, args.repo,
                            trigger_state=TRIGGER_STATE_ACTIVATE,
                            reason=args.reason)
    if not ok:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"asked {args.repo} for {args.epic}'s post-approval review "
          f"({args.reason})")
    return 0


def _cmd_card_set(args) -> int:
    diff = card_set_diff(_identifiers(args.before), _identifiers(args.after))
    _write_outputs(args.github_output, [
        ("changed", "true" if diff["changed"] else "false"),
        ("added", ",".join(diff["added"])),
        ("removed", ",".join(diff["removed"])),
    ])
    print(json.dumps(diff))
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("ceiling", help="the review's turn ceiling this run")
    c.add_argument("--epic", default=None)
    # A string on purpose, exactly as `plan_critic post-turns` takes it: the
    # workflow hands over whatever `linear_ops.py children` printed, and an
    # unreadable count must size to the default rather than fail the parse.
    c.add_argument("--children", default="")
    c.add_argument("--thread-file", default=None)
    c.add_argument("--github-output", default=None)
    c.set_defaults(fn=_cmd_ceiling)

    d = sub.add_parser("after-death", help="retry, park, or leave it to the medic")
    d.add_argument("--epic", default=None)
    d.add_argument("--thread-file", default=None)
    d.add_argument("--subtype", default="")
    d.add_argument("--github-output", default=None)
    d.add_argument("--note-file", default=None)
    d.set_defaults(fn=_cmd_after_death)

    f = sub.add_parser("dispatch", help="ask for the ACTIVATE-route run")
    f.add_argument("--epic", required=True)
    f.add_argument("--repo", required=True)
    f.add_argument("--reason", required=True)
    f.set_defaults(fn=_cmd_dispatch)

    s = sub.add_parser("card-set", help="which children a re-plan added or removed")
    s.add_argument("--before", required=True)
    s.add_argument("--after", required=True)
    s.add_argument("--github-output", default=None)
    s.set_defaults(fn=_cmd_card_set)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
