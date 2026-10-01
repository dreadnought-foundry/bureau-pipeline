"""RED-first tests for DRE-5380 — one module owns the line a step uses to call its script.

DRE-3488 moves five oversized workflow steps out of their `run: |` blocks
into `scripts/<name>.sh`. Before anything moves, `scripts/step_shell.py`
owns the delegation grammar and the readers that see through it, so the
checkers, the tests and the five move cards all use one spelling instead of
five. This suite pins:

  * the grammar — the two delegating forms, a trailing newline tolerated, and
    every near miss (`bash -e`, a path outside `scripts/`, a second command,
    a `${{ }}`) read as NOT delegated;
  * the readers — `step_shell` and `workflow_source` see through the line, and
    `workflow_source` round-trips a `move`: the step's `run`, read back and
    parsed with `yaml.safe_load`, equals the original body, comments included;
  * `code_lines` — what `verify` compares, with a heredoc body kept whole;
  * `move` — mechanical, and refusing a body that still holds a `${{` once its
    `--env` substitutions are applied;
  * `verify` — red on a changed code line, a dropped env key and a dropped
    `DRE-` reference, green on a comment-only edit;
  * `rehearse` — all five moves of the contract table, applied to a throwaway
    copy of THIS tree, each verified against HEAD.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import step_shell  # noqa: E402

SCRIPT = ROOT / "scripts" / "step_shell.py"


# --- the grammar ------------------------------------------------------------


def test_the_bureau_pipeline_form_delegates_and_a_trailing_newline_is_tolerated():
    assert (
        step_shell.delegated_script("bash .bureau-pipeline/scripts/report_agent_result.sh\n")
        == "scripts/report_agent_result.sh"
    )


def test_the_pipeline_dir_form_delegates():
    assert (
        step_shell.delegated_script('bash "$PIPELINE_DIR/scripts/post_verdict.sh"')
        == "scripts/post_verdict.sh"
    )


@pytest.mark.parametrize(
    "run",
    [
        # an option on bash is not the line
        "bash -e .bureau-pipeline/scripts/x.sh",
        # a path outside scripts/
        "bash .bureau-pipeline/tools/x.sh",
        "bash .bureau-pipeline/scripts/sub/x.sh",
        "bash .bureau-pipeline/scripts/../x.sh",
        "bash scripts/x.sh",
        # a second command on the line
        "bash .bureau-pipeline/scripts/x.sh && echo done",
        "bash .bureau-pipeline/scripts/x.sh; echo done",
        "bash .bureau-pipeline/scripts/x.sh\necho done",
        'bash "$PIPELINE_DIR/scripts/x.sh" | tee out',
        # a ${{ }} in the line
        "bash .bureau-pipeline/scripts/${{ inputs.name }}.sh",
        'bash "$PIPELINE_DIR/scripts/x.sh" ${{ github.repository }}',
        # names outside [a-z0-9_]+
        "bash .bureau-pipeline/scripts/Report.sh",
        "bash .bureau-pipeline/scripts/report-result.sh",
        # the forms crossed
        'bash ".bureau-pipeline/scripts/x.sh"',
        "bash $PIPELINE_DIR/scripts/x.sh",
    ],
)
def test_near_misses_do_not_delegate(run):
    assert step_shell.delegated_script(run) is None


# --- the readers ------------------------------------------------------------


def _write_script(root: Path, name: str, body: str) -> None:
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / f"{name}.sh").write_text("#!/usr/bin/env bash\nset -e\n" + body)


def test_step_shell_reads_the_script_of_a_delegating_step(tmp_path):
    _write_script(tmp_path, "hello", "echo hello\n")
    step = {"name": "Hi", "run": "bash .bureau-pipeline/scripts/hello.sh"}
    assert step_shell.step_shell(step, root=tmp_path) == (
        "#!/usr/bin/env bash\nset -e\necho hello\n"
    )


def test_step_shell_returns_an_inline_run_unchanged(tmp_path):
    step = {"name": "Hi", "run": "echo hello\n# a comment\n"}
    assert step_shell.step_shell(step, root=tmp_path) == "echo hello\n# a comment\n"


FIXTURE = textwrap.dedent(
    """\
    name: fixture
    on: workflow_dispatch
    jobs:
      work:
        runs-on: ubuntu-latest
        steps:
          - name: Before
            run: echo before

          # The step that moves (DRE-1111).
          - name: Do the thing
            id: thing
            if: always()
            env:
              # a comment inside env
              TOKEN: ${{ secrets.TOKEN }}
              CARD: ${{ inputs.card }}
            run: |
              # Lead comment (DRE-2222).
              set -euo pipefail
              echo "card $CARD"

              if [ -n "$TOKEN" ]; then
                # nested comment
                echo "has token"
              fi
              cat <<'EOF' > /tmp/out
              # not a comment, heredoc body

              EOF
              gh pr view 7 --repo ${{ github.repository }}
              echo "${{ inputs.mode || 'auto' }}"

          - name: After
            run: |
              echo after
    """
)


def _fixture_root(tmp_path: Path, text: str = FIXTURE) -> Path:
    root = tmp_path / "tree"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "fixture.yml").write_text(text)
    return root


def _step(text: str, name: str) -> dict:
    data = yaml.safe_load(text)
    (step,) = [
        s for job in data["jobs"].values() for s in job["steps"] if s.get("name") == name
    ]
    return step


def _move(root: Path, *extra: str, step: str = "Do the thing", script: str = "do_thing"):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "move", "--root", str(root),
         "--workflow", "fixture.yml", "--step", step, "--script", script, *extra],
        capture_output=True, text=True,
    )


ENVS = (
    "--env", "REPO=github.repository",
    "--env", "MODE=inputs.mode || 'auto'",
)


def test_workflow_source_returns_a_workflow_with_no_delegating_step_unchanged(tmp_path):
    root = _fixture_root(tmp_path)
    path = root / ".github" / "workflows" / "fixture.yml"
    assert step_shell.workflow_source(path, root=root) == FIXTURE


def test_move_then_workflow_source_round_trips_the_body_comments_included(tmp_path):
    original = _step(FIXTURE, "Do the thing")["run"]
    root = _fixture_root(tmp_path)

    result = _move(root, *ENVS)
    assert result.returncode == 0, result.stdout + result.stderr

    path = root / ".github" / "workflows" / "fixture.yml"
    moved = path.read_text()
    step = _step(moved, "Do the thing")
    assert step["run"] == "bash .bureau-pipeline/scripts/do_thing.sh"

    script = (root / "scripts" / "do_thing.sh").read_text()
    assert script.splitlines()[:2] == ["#!/usr/bin/env bash", "set -e"]

    expected = original.replace("${{ github.repository }}", "${REPO}").replace(
        "${{ inputs.mode || 'auto' }}", "${MODE}"
    )
    assert "# Lead comment (DRE-2222)." in expected and "# nested comment" in expected
    assert script == "#!/usr/bin/env bash\nset -e\n" + expected

    source = step_shell.workflow_source(path, root=root)
    assert _step(source, "Do the thing")["run"] == expected
    # the rest of the step and the workflow are untouched by the move
    for name in ("Before", "After"):
        assert _step(source, name) == _step(FIXTURE, name)
    read_back = _step(source, "Do the thing")
    assert {k: v for k, v in read_back.items() if k not in ("run", "env")} == {
        "name": "Do the thing", "id": "thing", "if": "always()",
    }


def test_a_move_without_substitutions_round_trips_to_the_exact_body(tmp_path):
    text = FIXTURE.replace(
        "          gh pr view 7 --repo ${{ github.repository }}\n"
        "          echo \"${{ inputs.mode || 'auto' }}\"\n",
        "",
    )
    original = _step(text, "Do the thing")["run"]
    root = _fixture_root(tmp_path, text)

    result = _move(root)
    assert result.returncode == 0, result.stdout + result.stderr

    path = root / ".github" / "workflows" / "fixture.yml"
    assert _step(step_shell.workflow_source(path, root=root), "Do the thing")["run"] == original
    assert step_shell.step_shell(_step(path.read_text(), "Do the thing"), root=root) == (
        "#!/usr/bin/env bash\nset -e\n" + original
    )


def test_move_adds_each_env_substitution_to_the_steps_env(tmp_path):
    root = _fixture_root(tmp_path)
    assert _move(root, *ENVS).returncode == 0
    env = _step((root / ".github/workflows/fixture.yml").read_text(), "Do the thing")["env"]
    assert env == {
        "TOKEN": "${{ secrets.TOKEN }}",
        "CARD": "${{ inputs.card }}",
        "REPO": "${{ github.repository }}",
        "MODE": "${{ inputs.mode || 'auto' }}",
    }


def test_move_creates_an_env_block_when_the_step_has_none(tmp_path):
    text = FIXTURE.replace(
        "        env:\n"
        "          # a comment inside env\n"
        "          TOKEN: ${{ secrets.TOKEN }}\n"
        "          CARD: ${{ inputs.card }}\n",
        "",
    )
    root = _fixture_root(tmp_path, text)
    assert _move(root, *ENVS).returncode == 0
    step = _step((root / ".github/workflows/fixture.yml").read_text(), "Do the thing")
    assert step["env"] == {
        "REPO": "${{ github.repository }}",
        "MODE": "${{ inputs.mode || 'auto' }}",
    }


def test_move_via_pipeline_dir_writes_the_pipeline_dir_form(tmp_path):
    text = FIXTURE.replace("${{ github.repository }}", "x").replace(
        "${{ inputs.mode || 'auto' }}", "y"
    )
    root = _fixture_root(tmp_path, text)
    assert _move(root, "--via", "pipeline-dir").returncode == 0
    step = _step((root / ".github/workflows/fixture.yml").read_text(), "Do the thing")
    assert step["run"] == 'bash "$PIPELINE_DIR/scripts/do_thing.sh"'
    assert step_shell.delegated_script(step["run"]) == "scripts/do_thing.sh"


def test_move_refuses_a_body_that_still_holds_an_expression(tmp_path):
    root = _fixture_root(tmp_path)
    before = (root / ".github/workflows/fixture.yml").read_text()

    # one of the two expressions is left in the body
    result = _move(root, "--env", "REPO=github.repository")

    assert result.returncode != 0
    assert "${{" in result.stdout + result.stderr
    assert (root / ".github/workflows/fixture.yml").read_text() == before
    assert not (root / "scripts" / "do_thing.sh").exists()


def test_move_refuses_a_step_the_workflow_does_not_have(tmp_path):
    root = _fixture_root(tmp_path)
    result = _move(root, step="No such step")
    assert result.returncode != 0
    assert not (root / "scripts" / "do_thing.sh").exists()


# --- code_lines -------------------------------------------------------------


def test_code_lines_drops_the_opening_lines_blanks_and_full_line_comments():
    text = textwrap.dedent(
        """\
        #!/usr/bin/env bash
        set -e
        # header (DRE-5380)

        set -euo pipefail
          # indented comment
        echo one  # trailing comment stays with its code
        echo two
        """
    )
    assert step_shell.code_lines(text) == [
        "set -euo pipefail",
        "echo one  # trailing comment stays with its code",
        "echo two",
    ]


def test_code_lines_keeps_set_e_when_it_is_not_line_two():
    assert step_shell.code_lines("echo one\nset -e\n") == ["echo one", "set -e"]
    assert step_shell.code_lines("#!/usr/bin/env bash\nset -eu\necho x\n") == [
        "set -eu", "echo x",
    ]


def test_code_lines_keeps_a_heredoc_body_whole():
    text = textwrap.dedent(
        """\
        cat <<'EOF' > out
        # kept: heredoc body

        line
        EOF
        # dropped
        cat <<-END
        \t# kept too
        \tEND
        echo after
        """
    )
    assert step_shell.code_lines(text) == [
        "cat <<'EOF' > out",
        "# kept: heredoc body",
        "",
        "line",
        "EOF",
        "cat <<-END",
        "\t# kept too",
        "\tEND",
        "echo after",
    ]


# --- verify -----------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def moved(tmp_path):
    """A git repo whose HEAD holds FIXTURE, and a separate tree where the
    step has been moved with `move` — the shape every move card verifies."""
    repo = _fixture_root(tmp_path)
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    out = tmp_path / "out"
    out.mkdir()
    tree = _fixture_root(out)
    assert _move(tree, *ENVS).returncode == 0
    return repo, tree


def _verify(repo: Path, tree: Path, *envs: str) -> list[str]:
    return step_shell.verify(
        "HEAD", "fixture.yml", "Do the thing",
        envs=step_shell.parse_envs(envs), root=tree, repo=repo,
    )


def _env_args(args=ENVS) -> list[str]:
    return [a for a in args if a != "--env"]


def test_verify_passes_a_mechanical_move(moved):
    repo, tree = moved
    assert _verify(repo, tree, *_env_args()) == []


def test_verify_passes_a_comment_only_edit(moved):
    repo, tree = moved
    script = tree / "scripts" / "do_thing.sh"
    text = script.read_text().replace("set -e\n", "set -e\n# The header a move card writes.\n\n", 1)
    text = text.replace("    # nested comment\n", "    # a reworded nested comment\n")
    script.write_text(text)
    assert _verify(repo, tree, *_env_args()) == []


def test_verify_fails_a_changed_code_line(moved):
    repo, tree = moved
    script = tree / "scripts" / "do_thing.sh"
    script.write_text(script.read_text().replace('echo "has token"', 'echo "has a token"'))
    problems = _verify(repo, tree, *_env_args())
    assert problems and any("code" in p for p in problems)


def test_verify_fails_a_dropped_env_key(moved):
    repo, tree = moved
    wf = tree / ".github" / "workflows" / "fixture.yml"
    wf.write_text(wf.read_text().replace("          CARD: ${{ inputs.card }}\n", ""))
    problems = _verify(repo, tree, *_env_args())
    assert problems and any("CARD" in p for p in problems)


def test_verify_fails_a_dropped_substitution_env_key(moved):
    repo, tree = moved
    wf = tree / ".github" / "workflows" / "fixture.yml"
    wf.write_text(wf.read_text().replace("          REPO: ${{ github.repository }}\n", ""))
    problems = _verify(repo, tree, *_env_args())
    assert problems and any("REPO" in p for p in problems)


def test_verify_fails_a_dropped_dre_reference(moved):
    repo, tree = moved
    script = tree / "scripts" / "do_thing.sh"
    script.write_text(script.read_text().replace("# Lead comment (DRE-2222).\n", "# Lead comment.\n"))
    problems = _verify(repo, tree, *_env_args())
    assert problems and any("DRE-2222" in p for p in problems)


def test_verify_fails_a_step_that_does_not_delegate(moved):
    repo, _tree = moved
    # the base tree itself: its step is still the inline block
    assert _verify(repo, repo, *_env_args())


def test_verify_cli_exit_codes(moved):
    repo, tree = moved
    # verify runs from the checkout whose history holds REF
    copy = repo / "scripts"
    copy.mkdir()
    (copy / "step_shell.py").write_text(SCRIPT.read_text())
    cmd = [sys.executable, str(copy / "step_shell.py"), "verify", "--base", "HEAD",
           "--root", str(tree), "--workflow", "fixture.yml", "--step", "Do the thing", *ENVS]
    ok = subprocess.run(cmd, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    script = tree / "scripts" / "do_thing.sh"
    script.write_text(script.read_text().replace("fi\n", "fi\nexit 3\n"))
    bad = subprocess.run(cmd, capture_output=True, text=True)
    assert bad.returncode == 1, bad.stdout + bad.stderr


# --- rehearse: the five moves on a copy of this tree -------------------------


def test_the_rehearsal_table_is_the_contracts_five_moves():
    table = [
        (m.workflow, m.step, m.script, m.via, dict(m.envs)) for m in step_shell.REHEARSAL
    ]
    assert table == [
        ("agent-task.yml", "Report result to Linear", "report_agent_result", None, {}),
        ("agent-fix.yml", "Resolve PR, mode, and attempt budget", "resolve_fix_pr", None, {
            "PR_NUMBER": "github.event.issue.number || github.event.inputs.pr_number",
            "REPO": "github.repository",
        }),
        ("agent-fix.yml", "Report", "report_fix_result", None, {}),
        ("merge-gate.yml", "Evaluate and merge", "evaluate_and_merge", None, {}),
        ("qa-review.yml", "Post verdict or neutral status", "post_verdict", "pipeline-dir", {}),
    ]


def test_rehearse_moves_all_five_and_each_verifies_against_head(tmp_path):
    out = tmp_path / "r"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "rehearse", "--out", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (out / ".git").exists()

    for move in step_shell.REHEARSAL:
        wf = out / ".github" / "workflows" / move.workflow
        step = _step(wf.read_text(), move.step)
        assert step_shell.delegated_script(step["run"]) == f"scripts/{move.script}.sh"
        assert (out / "scripts" / f"{move.script}.sh").is_file()

        cmd = [sys.executable, str(SCRIPT), "verify", "--base", "HEAD", "--root", str(out),
               "--workflow", move.workflow, "--step", move.step]
        for name, expr in move.envs:
            cmd += ["--env", f"{name}={expr}"]
        checked = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
        assert checked.returncode == 0, (move.step, checked.stdout + checked.stderr)


def test_rehearse_refuses_a_non_empty_out_dir(tmp_path):
    (tmp_path / "stale").write_text("x")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "rehearse", "--out", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
