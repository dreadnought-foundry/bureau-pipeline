"""No `linear_ops.py comment` or `set-description` call passes a file's contents as an argument (DRE-8758).

`linear_ops.py comment "$EPIC" "$(cat "$NOTE")"` hands the whole file to the
kernel as ONE argument, and Linux refuses any single argument over 128 KiB
with E2BIG, which bash reports as exit code 126. On 2026-11-01 that killed
the plan run of an epic built from a 199-row mockup inventory before either
critic ran (`tests/test_linear_body_from_file.py` has the incident). Every
body that lives in a file goes in with `--body-file=PATH` (or `-` for stdin).

This check reads every workflow in `.github/workflows/` and every shell
script under `scripts/`, joins `\\`-continued lines into one command, and
fails on any command that runs `linear_ops.py comment` or
`linear_ops.py set-description` with a command substitution that reads a
file: `$(cat …)`, `$(< …)` or a backquoted `cat`, nested inside another
substitution or not. It names every offender by file and line.
"""

import glob
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

#: The two subcommands whose body is free text of any length.
_CALL = re.compile(r"linear_ops\.py[\"']?\s+(?:comment|set-description)(?=\s|$)")

#: A file's contents read inline: `$(cat F)`, `$(< F)` or `` `cat F` ``.
_FILE_READ = re.compile(r"\$\(\s*(?:cat\b|<)|`\s*cat\b")


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


def offenders(text: str) -> list[int]:
    """The line each offending command starts on."""
    return [n for n, cmd in _commands(text)
            if _CALL.search(cmd) and _FILE_READ.search(cmd)]


def _files():
    paths = glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml"))
    paths += glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yaml"))
    paths += glob.glob(os.path.join(ROOT, "scripts", "**", "*.sh"), recursive=True)
    return sorted(paths)


def test_the_check_reads_the_workflows_and_the_scripts():
    files = [os.path.relpath(p, ROOT) for p in _files()]
    assert ".github/workflows/plan.yml" in files
    assert "scripts/post_verdict.sh" in files


def test_no_body_is_passed_as_a_files_contents():
    found = []
    for path in _files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        found += [f"{os.path.relpath(path, ROOT)}:{n}" for n in offenders(text)]
    assert not found, (
        "these linear_ops.py comment / set-description calls pass a file's "
        "contents as one argument, which Linux refuses over 128 KiB (exit 126). "
        "Use --body-file=PATH instead:\n  " + "\n  ".join(found))


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


def test_the_check_passes_the_file_form_and_other_commands():
    passed = [
        'python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" --body-file="$NOTE"',
        'python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \\\n'
        '  --body-file="$RUNNER_TEMP/x.md" --act=run-failure-diagnosed',
        'python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" "a short literal"',
        'N=$(python3 .bureau-pipeline/scripts/linear_ops.py count-comments "$CARD" "$(cat key.txt)")',
        'gh pr comment "$PR" --body "$(cat body.md)"',
    ]
    for text in passed:
        assert offenders(text) == [], text
