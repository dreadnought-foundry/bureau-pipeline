"""No free-text body is passed as a file's contents in one argument (DRE-8758, DRE-8779).

`linear_ops.py comment "$EPIC" "$(cat "$NOTE")"` hands the whole file to the
kernel as ONE argument, and Linux refuses any single argument over 128 KiB
with E2BIG, which bash reports as exit code 126. On 2026-11-01 that killed
the plan run of an epic built from a 199-row mockup inventory before either
critic ran (`tests/test_linear_body_from_file.py` has the incident). Every
body that lives in a file goes in with `--body-file=PATH` (or `-` for stdin).

This check reads every workflow in `.github/workflows/` and every shell
script under `scripts/`, joins `\\`-continued lines into one command, and
fails on two shapes. It names every offender by file and line.

1. A `linear_ops.py` call that takes free text — `comment`, `set-description`,
   `proof-observed`, `proof-waiting`, `proof-waiting-until` or `unpark` — or a
   `pipeline_act.py receipt` call, with a command substitution that reads a
   file: `$(cat …)`, `$(< …)` or a backquoted `cat`, nested inside another
   substitution or not.
2. A `pipeline_act.py receipt` call whose `--body` value holds any shell
   expansion (`$`). A variable filled from a file a line earlier is the same
   argument (DRE-8779: `report_fix_result.sh` built its hold body as
   `HOLD_BODY="$(cat "$CNP_BODY") …"` and passed `--body "$HOLD_BODY"`), and
   the check cannot see what a variable holds, so a receipt takes a plain
   literal as `--body` or its body on `--body-file=-`.
"""

import glob
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

#: The `linear_ops.py` subcommands whose text is free and of any length.
_CALL = re.compile(
    r"linear_ops\.py[\"']?\s+"
    r"(?:comment|set-description|proof-observed|proof-waiting-until|proof-waiting|unpark)"
    r"(?=\s|$)")

#: The receipt writer's shell seam, whose `--body` is free text too (DRE-8779).
_RECEIPT = re.compile(r"pipeline_act\.py[\"']?\s+receipt(?=\s|$)")

#: A file's contents read inline: `$(cat F)`, `$(< F)` or `` `cat F` ``.
_FILE_READ = re.compile(r"\$\(\s*(?:cat\b|<)|`\s*cat\b")

#: A `--body` value, quoted or bare — never `--body-file`.
_BODY_VALUE = re.compile(r"""--body(?:=|\s+)("(?:[^"\\]|\\.)*"|'[^']*'|\S+)""")


def _commands(text: str):
    """Every logical shell line, as `(first line number, text)`: a line that
    ends in a backslash continues onto the next."""
    out = []
    buf, start = [], None
    for n, line in enumerate(text.splitlines(), 1):
        if start is None:
            start = n
        if line.rstrip().endswith("\\"):
            buf.append(line.rstrip()[:-1])
            continue
        buf.append(line)
        out.append((start, " ".join(buf)))
        buf, start = [], None
    if buf:
        out.append((start, " ".join(buf)))
    return out


def _expanded_body(cmd: str) -> bool:
    """A receipt's `--body` that the shell fills in. A single-quoted value is
    literal, so a `$` inside one expands nothing."""
    return any(not value.startswith("'") and ("$" in value or "`" in value)
               for value in _BODY_VALUE.findall(cmd[_RECEIPT.search(cmd).end():]))


def offenders(text: str) -> list[int]:
    """The line each offending command starts on."""
    found = []
    for n, cmd in _commands(text):
        if _CALL.search(cmd) and _FILE_READ.search(cmd):
            found.append(n)
        elif _RECEIPT.search(cmd) and (_FILE_READ.search(cmd) or _expanded_body(cmd)):
            found.append(n)
    return found


def _files():
    paths = glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml"))
    paths += glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yaml"))
    paths += glob.glob(os.path.join(ROOT, "scripts", "**", "*.sh"), recursive=True)
    return sorted(paths)


def test_the_check_reads_the_workflows_and_the_scripts():
    files = [os.path.relpath(p, ROOT) for p in _files()]
    assert ".github/workflows/plan.yml" in files
    assert "scripts/post_verdict.sh" in files
    assert "scripts/report_fix_result.sh" in files


def test_no_body_is_passed_as_a_files_contents():
    found = []
    for path in _files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        found += [f"{os.path.relpath(path, ROOT)}:{n}" for n in offenders(text)]
    assert not found, (
        "these linear_ops.py / pipeline_act.py receipt calls pass a body as one "
        "argument, which Linux refuses over 128 KiB (exit 126). Use "
        "--body-file=PATH, or pipe it to --body-file=-:\n  " + "\n  ".join(found))


# --- The check itself, so a pass is never a pass by blindness ---------------

def test_the_check_catches_every_spelling_of_the_old_form():
    caught = [
        'python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" "$(cat "$NOTE")"',
        'python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" \\\n'
        '            "$(cat "$NOTE")"',
        'python3 "$PIPELINE_DIR"/scripts/linear_ops.py comment "$CARD" "$(cat /tmp/x.md)" || true',
        'python3 .bureau-pipeline/scripts/linear_ops.py set-description "$CARD" "$(cat "$DESC")"',
        'python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \\\n'
        '  "$(printf \'head\\n\\n%s\' "$(cat "$SUMMARY")")"',
        'python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" "$(< "$NOTE")"',
        'python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" "`cat note.md`"',
    ]
    for text in caught:
        assert offenders(text) == [1], text


def test_the_check_catches_the_other_free_text_subcommands():
    for sub in ("proof-observed", "proof-waiting", "proof-waiting-until", "unpark"):
        text = f'python3 .bureau-pipeline/scripts/linear_ops.py {sub} "$CARD" "$(cat "$NOTE")"'
        assert offenders(text) == [1], text


def test_the_check_catches_a_receipt_body_read_from_a_file():
    caught = [
        'python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-attempt-disputed \\\n'
        '  --body "$(cat "$CNP_BODY")" --out /tmp/act.md',
        'python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-attempt-disputed '
        '--body "$(< "$CNP_BODY")" --out /tmp/act.md',
        'python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-attempt-disputed '
        '--body "`cat body.md`" --out /tmp/act.md',
    ]
    for text in caught:
        assert offenders(text) == [1], text


def test_the_check_catches_a_receipt_body_held_in_a_variable():
    """The DRE-8779 shape: the file was read into a variable a line earlier."""
    caught = [
        'python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-attempt-disputed \\\n'
        '  --body "$HOLD_BODY" --out /tmp/act-fix-refused-again.md \\\n'
        '  || printf \'%s\' "$HOLD_BODY" > /tmp/act-fix-refused-again.md',
        'python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-loop-restarted '
        '--body "${BODY}" --out /tmp/a.md',
        'python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-loop-restarted '
        '--body=$BODY --out /tmp/a.md',
        'python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-loop-restarted '
        '--body "Fix attempt $ATTEMPT pushed" --out /tmp/a.md',
    ]
    for text in caught:
        assert offenders(text) == [1], text


def test_the_check_passes_the_file_form_and_other_commands():
    passed = [
        'python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" --body-file="$NOTE"',
        'python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \\\n'
        '  --body-file="$RUNNER_TEMP/x.md" --act=run-failure-diagnosed',
        'python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" "a short literal"',
        'python3 .bureau-pipeline/scripts/linear_ops.py proof-waiting "$CARD" "$OBSERVED" "$NEEDS"',
        'N=$(python3 .bureau-pipeline/scripts/linear_ops.py count-comments "$CARD" "$(cat key.txt)")',
        'gh pr comment "$PR" --body "$(cat body.md)"',
        'gh pr comment "$PR" --body "$BODY"',
    ]
    for text in passed:
        assert offenders(text) == [], text


def test_the_check_passes_a_receipt_body_from_a_file_or_a_literal():
    passed = [
        'printf \'%s\' "$HOLD_BODY" | python3 .bureau-pipeline/scripts/pipeline_act.py '
        'receipt fix-attempt-disputed \\\n'
        '  --body-file=- --out /tmp/act-fix-refused-again.md \\\n'
        '  || printf \'%s\' "$HOLD_BODY" > /tmp/act-fix-refused-again.md',
        'python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-attempt-disputed '
        '--body-file="$CNP_BODY" --out "$OUT"',
        'python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-loop-restarted '
        '--body "a plain literal" --out "$OUT"',
        'python3 .bureau-pipeline/scripts/pipeline_act.py receipt fix-loop-restarted '
        "--body 'costs $5, literally' --out /tmp/a.md",
    ]
    for text in passed:
        assert offenders(text) == [], text
