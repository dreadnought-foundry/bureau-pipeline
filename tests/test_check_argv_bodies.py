"""The argv-bodies check refuses a body handed whole to `execve` (DRE-8820).

Linux refuses any single argument or environment string over 131,072 bytes
(`MAX_ARG_STRLEN`) with E2BIG, and bash reports that as exit code 126. The
wall was met four times — DRE-3328, DRE-3330, DRE-6683 and DRE-8698 — and
each was fixed only where it was found. `scripts/check_argv_bodies.py` reads
every workflow under `.github/workflows/`, found by glob, and names each
SITE: a file's contents, or a step output written in the heredoc form, that
reaches a command's argument or an `env:` value.

The cases below are minimal workflows, read by the same `sites()` the check
runs over the repo. The repo's own workflows must carry no site; the fixture
`tests/fixtures/argv_bodies/exit-126.yml` holds the exact line DRE-8819
removed and must carry exactly one. A probe appends a known site to every real
`run:` block, so a green repo is never a green by a lexer gone blind.
"""

import glob
import os
import subprocess
import sys
import tempfile
import textwrap

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import check_argv_bodies as argv_bodies  # noqa: E402

SCRIPT = os.path.join(ROOT, "scripts", "check_argv_bodies.py")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "argv_bodies", "exit-126.yml")
EXIT_126_LINE = ('python3 .bureau-pipeline/scripts/linear_ops.py comment '
                 '"$EPIC" "$(cat "$NOTE")"')
LIMIT = "131,072 bytes"
FIX = "--body-file=PATH"


def workflow(run: str, env: dict = None, before: str = "") -> str:
    """A one-job workflow whose last step runs `run`, under `env`. `before`
    is steps placed ahead of it, already written as YAML list items."""
    lines = ["name: case", "on: workflow_dispatch", "jobs:", "  build:",
             "    runs-on: ubuntu-latest", "    steps:"]
    if before:
        lines += textwrap.indent(textwrap.dedent(before).strip("\n"), "      ").splitlines()
    lines.append("      - name: The step under test")
    if env:
        lines.append("        env:")
        lines += [f"          {k}: {v}" for k, v in env.items()]
    lines.append("        run: |")
    lines += textwrap.indent(textwrap.dedent(run).strip("\n"), "          ").splitlines()
    return "\n".join(lines) + "\n"


def line_of(text: str, needle: str) -> int:
    for n, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return n
    raise AssertionError(f"{needle!r} not in the workflow")


def run_check(*args):
    return subprocess.run([sys.executable, SCRIPT, "check", *args],
                          capture_output=True, text=True, cwd=ROOT)


#: A step whose output `body` is written in the heredoc form.
HEREDOC_STEP = '''
- name: Write the body
  id: compose
  run: |
    {
      echo "body<<BODY_EOF"
      cat /tmp/body.md
      echo "BODY_EOF"
    } >> "$GITHUB_OUTPUT"
'''

#: A step whose output `title` is written on one line.
ONE_LINE_STEP = '''
- name: Write the title
  id: compose
  run: |
    echo "title=$(head -c 256 /tmp/title.txt)" >> "$GITHUB_OUTPUT"
'''


# --- The repo's own workflows ------------------------------------------------

def test_the_repos_workflows_carry_no_site():
    found = []
    for path in argv_bodies.workflows(ROOT):
        with open(path, encoding="utf-8") as fh:
            found += [s.message(os.path.relpath(path, ROOT))
                      for s in argv_bodies.sites(fh.read())]
    assert not found, (
        f"{len(found)} site(s) hand a body to one argument or env string. "
        f"Linux refuses any single one over {LIMIT}; pass the path instead "
        f"({FIX}, -F PATH, or stdin), or bound the read with "
        f"$(head -c N FILE), N ≤ {argv_bodies.BOUNDED_READ_MAX}:\n  "
        + "\n  ".join(found))


def test_check_exits_0_on_the_repo_and_counts_every_workflow():
    on_disk = (glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml"))
               + glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yaml")))
    result = run_check()
    assert result.returncode == 0, result.stdout + result.stderr
    summary = result.stdout.strip().splitlines()[-1]
    assert summary.startswith(f"argv-bodies: {len(on_disk)} workflows, "), summary
    assert summary.endswith(" 0 sites"), summary
    blocks = int(summary.split(", ")[1].split()[0])
    assert blocks > len(on_disk), summary


def test_the_check_is_not_blind_to_the_end_of_any_real_run_block():
    """Each real `run:` block, with a site appended: the lexer must still be
    reading commands when it reaches the last line, so no quote, here-document
    or substitution it mis-read swallowed the rest of the block."""
    blind = []
    for path in argv_bodies.workflows(ROOT):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        doc = yaml.safe_load(text)
        for job_id, job in (doc.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                if not isinstance(step.get("run"), str):
                    continue
                text = workflow(step["run"].rstrip("\n")
                                + '\ngh argv-bodies-probe "$(cat /tmp/probe)"\n')
                probe = line_of(text, "gh argv-bodies-probe")
                if probe not in [s.line for s in argv_bodies.sites(text)]:
                    blind.append(f"{os.path.relpath(path, ROOT)} {job_id} step {index + 1}")
    assert not blind, "the check went blind before the end of:\n  " + "\n  ".join(blind)


# --- The fixture: the exact line DRE-8819 removed ----------------------------

def test_the_fixture_holds_the_exact_line():
    with open(FIXTURE, encoding="utf-8") as fh:
        text = fh.read()
    assert EXIT_126_LINE in text
    runs = [s["run"] for job in yaml.safe_load(text)["jobs"].values()
            for s in job["steps"] if "run" in s]
    assert len(runs) == 1


def test_the_fixture_is_exactly_one_site_at_that_line():
    with open(FIXTURE, encoding="utf-8") as fh:
        text = fh.read()
    found = argv_bodies.sites(text)
    assert [s.line for s in found] == [line_of(text, EXIT_126_LINE)], found
    message = found[0].message("exit-126.yml")
    assert f"exit-126.yml:{found[0].line}:" in message
    assert "Post the note to the epic" in message
    assert ("Linux refuses any single argument or environment string over "
            "131,072 bytes (MAX_ARG_STRLEN) with E2BIG; bash reports it as "
            "exit code 126") in message
    assert ("pass the path instead (`--body-file=PATH`, `-F PATH`, or stdin), "
            "or bound the read with `$(head -c N FILE)`, N ≤ 4096") in message


def test_check_pointed_at_the_fixture_exits_1():
    result = run_check(FIXTURE)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "argv-bodies: 1 workflows, 1 run blocks read, 1 sites", lines
    assert LIMIT in lines[0] and FIX in lines[0], lines[0]


# --- Sites --------------------------------------------------------------------

def assert_one_site(text: str, needle: str):
    found = argv_bodies.sites(text)
    assert [s.line for s in found] == [line_of(text, needle)], (found, text)


def test_a_variable_read_from_a_file_and_passed_to_a_command():
    text = workflow('''
        BODY=$(cat "$F")
        gh pr comment "$PR" --body "$BODY"
    ''')
    assert_one_site(text, "gh pr comment")


def test_a_variable_is_followed_transitively():
    text = workflow('''
        CHANGED="What the re-plan changed: $(cat "$S")"
        TEXT="The finding. ${CHANGED} Round two."
        python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" "...${TEXT}..."
    ''')
    assert_one_site(text, "linear_ops.py comment")


def test_a_read_nested_inside_another_substitution():
    text = workflow('''
        gh pr comment "$PR" --body "$(printf 'head\\n\\n%s' "$(cat F)")"
    ''')
    assert_one_site(text, "gh pr comment")


def test_a_command_inside_a_substitution_is_read_too():
    text = workflow('''
        N=$(python3 count.py "$(cat key.txt)")
    ''')
    assert_one_site(text, "count.py")


def test_a_redirect_read():
    text = workflow('''
        gh pr comment "$PR" --body "$(< "$F")"
    ''')
    assert_one_site(text, "gh pr comment")


def test_a_backquoted_cat():
    text = workflow('''
        gh pr comment "$PR" --body "`cat note.md`"
    ''')
    assert_one_site(text, "gh pr comment")


def test_a_backslash_continued_command_is_one_command():
    text = workflow('''
        python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" \\
          --act=note \\
          "$(cat "$NOTE")"
    ''')
    assert_one_site(text, "linear_ops.py comment")


def test_an_unbounded_head_and_one_past_the_bound():
    for read in ('$(head -c 4097 "$F")', '$(head -n 1 "$F")', '$(head "$F")'):
        text = workflow(f'''
            gh pr create --title "{read}" --body-file "$B"
        ''')
        assert_one_site(text, "gh pr create")


def test_a_heredoc_written_output_in_an_env_value():
    text = workflow('''
        gh pr comment "$PR" --body-file /tmp/body.md
    ''', env={"BODY": "${{ steps.compose.outputs.body }}"}, before=HEREDOC_STEP)
    found = argv_bodies.sites(text)
    assert [s.line for s in found] == [line_of(text, "BODY: ${{")], found
    assert "steps.compose.outputs.body" in found[0].what


def test_a_heredoc_written_output_in_a_job_or_workflow_env():
    text = workflow('echo ok', before=HEREDOC_STEP).replace(
        "    runs-on: ubuntu-latest\n",
        "    runs-on: ubuntu-latest\n    env:\n"
        "      BODY: ${{ steps.compose.outputs.body }}\n")
    assert_one_site(text, "BODY: ${{")


def test_a_heredoc_written_output_in_a_run_command_argument():
    text = workflow('''
        gh pr comment "$PR" --body "${{ steps.compose.outputs.body }}"
    ''', before=HEREDOC_STEP)
    assert_one_site(text, "gh pr comment")


def test_a_heredoc_written_output_through_a_variable():
    text = workflow('''
        BODY='${{ steps.compose.outputs.body }}'
        gh pr comment "$PR" --body "$BODY"
    ''', before=HEREDOC_STEP)
    assert_one_site(text, "gh pr comment")


def test_a_prefix_assignment_puts_a_read_in_the_commands_environment():
    text = workflow('''
        BODY=$(cat F) python3 x.py
    ''')
    found = argv_bodies.sites(text)
    assert [s.line for s in found] == [line_of(text, "python3 x.py")], found
    assert "environment string BODY" in found[0].what, found[0].what


def test_a_prefix_assignment_through_a_variable():
    text = workflow('''
        NOTE=$(cat "$F")
        BODY="$NOTE" gh pr comment "$PR" --body-file=-
    ''')
    assert_one_site(text, "gh pr comment")


def test_an_exported_read_reaches_the_next_external_command():
    text = workflow('''
        export BODY=$(cat F)
        echo "exported"
        python3 x.py
        python3 y.py
    ''')
    found = argv_bodies.sites(text)
    assert [s.line for s in found] == [line_of(text, "python3 x.py")], found
    assert "exported $BODY" in found[0].what, found[0].what
    assert "environment string" in found[0].what, found[0].what


def test_a_read_exported_after_it_is_assigned():
    for export in ("export BODY", "declare -x BODY"):
        text = workflow(f'''
            BODY=$(cat F)
            {export}
            gh pr comment "$PR" --body-file /tmp/b.md
        ''')
        assert_one_site(text, "gh pr comment")


def test_an_exported_read_reaches_a_command_inside_a_substitution():
    text = workflow('''
        export BODY=$(cat F)
        N=$(python3 count.py)
    ''')
    assert_one_site(text, "count.py")


def test_a_site_message_names_the_file_line_step_limit_and_fix():
    text = workflow('''
        gh pr comment "$PR" --body "$(cat body.md)"
    ''')
    (site,) = argv_bodies.sites(text)
    message = site.message(".github/workflows/case.yml")
    assert message.startswith(f".github/workflows/case.yml:{site.line}:")
    assert "The step under test" in message
    assert LIMIT in message and "MAX_ARG_STRLEN" in message and "126" in message
    assert FIX in message and "-F PATH" in message and "head -c N FILE" in message


# --- Not sites ----------------------------------------------------------------

def assert_no_site(text: str):
    found = argv_bodies.sites(text)
    assert found == [], (found, text)


def test_a_body_file_is_not_a_site():
    assert_no_site(workflow('''
        gh pr comment "$PR" --body-file="$F"
        python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" --body-file="$NOTE"
        gh api "repos/$R/issues/$N/comments" -F body=@"$F"
    '''))


def test_a_bounded_head_is_not_a_site():
    assert_no_site(workflow('''
        TITLE=$(head -c 256 "$F")
        gh pr create --title "$TITLE" --body-file "$B"
        gh pr edit "$PR" --title "$(head -c 256 "$F")"
        gh pr edit "$PR" --title "$(head -c 4096 "$F")"
    '''))


def test_a_read_into_a_builtin_redirected_to_a_file_is_not_a_site():
    assert_no_site(workflow('''
        printf '%s' "$(cat F)" > out
        printf '%s\\n\\n%s\\n' "$(cat /tmp/a.md)" "$KEY" \\
          > /tmp/b.md
    '''))


def test_a_workflow_command_echo_is_not_a_site():
    assert_no_site(workflow('''
        echo "::warning::$(cat F)"
        case "$(cat "$WHY_FILE")" in DEGRADED*) echo "::warning::$(cat "$WHY_FILE")" ;; esac
    '''))


def test_a_read_that_only_reaches_builtins_is_not_a_site():
    assert_no_site(workflow('''
        VAR=$(cat F)
        echo "$VAR"
        [ -n "$VAR" ] || VAR="none"
        echo "why=$VAR" >> "$GITHUB_OUTPUT"
        printf '%s' "$VAR" | gh pr comment "$PR" --body-file=-
        jq -r . <<< "$VAR"
    '''))


def test_a_bounded_or_builtin_environment_is_not_a_site():
    assert_no_site(workflow('''
        TITLE=$(head -c 256 F) python3 x.py
        BODY=$(cat F) printf '%s' "$BODY" > out
        export SHORT=$(head -c 256 F)
        python3 y.py
        export LONG=$(cat F)
        echo "$LONG" > out
    '''))


def test_a_single_line_output_in_env_is_not_a_site():
    assert_no_site(workflow('''
        gh pr create --title "$TITLE" --body-file /tmp/b.md
    ''', env={"TITLE": "${{ steps.compose.outputs.title }}"}, before=ONE_LINE_STEP))


def test_a_cat_of_a_here_document_is_not_a_site():
    assert_no_site(workflow('''
        BODY=$(cat <<'EOF'
        A literal the script carries.
        EOF
        )
        gh pr comment "$PR" --body "$BODY"
    '''))


def test_text_inside_single_quotes_a_comment_or_a_here_document_is_not_read():
    assert_no_site(workflow('''
        # gh pr comment "$PR" --body "$(cat F)"
        jq -n '$(cat F)'
        cat > /tmp/x.sh <<'EOF'
        gh pr comment "$PR" --body "$(cat F)"
        EOF
    '''))


# --- Discovery ----------------------------------------------------------------

def test_a_new_workflow_is_found_by_glob():
    with tempfile.TemporaryDirectory() as tmp:
        wf_dir = os.path.join(tmp, ".github", "workflows")
        os.makedirs(wf_dir)
        with open(os.path.join(wf_dir, "clean.yml"), "w") as fh:
            fh.write(workflow('echo ok'))
        with open(os.path.join(wf_dir, "brand-new.yaml"), "w") as fh:
            fh.write(workflow('gh pr comment "$PR" --body "$(cat F)"'))
        assert [os.path.basename(p) for p in argv_bodies.workflows(tmp)] == [
            "brand-new.yaml", "clean.yml"]
        result = run_check("--root", tmp)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "argv-bodies: 2 workflows, 2 run blocks read, 1 sites", lines
    assert "brand-new.yaml:" in lines[0], lines[0]
