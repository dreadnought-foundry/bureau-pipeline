# PROOF record — DRE-8821: PROOF: bureau-pipeline's plan runs post the mechanical note from a file with no exit 126, and the argv check runs in the nightly suite (epic DRE-8814)

**Status: PASS.**

## How this was recorded

- Run: [41888398323](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41888398323), a shadow run of this card on claude-sonnet-5-5 (DRE-7286). It observed at 2026-11-04 10:40 PT on `main` at `2e69206a`, and wrote only this file.
- Release observed: the epic's three build cards merged on 2026-11-02 — DRE-8819 (pull request 1404) at 07:56 PT, DRE-8818 (pull request 1407) at 08:11 PT and DRE-8820 (pull request 1410) at 08:40 PT. The newest merge is `91b46782`. This repo's release record is its moving `stable` tag (`.github/bureau/release.json`, surface `pipeline-channel`, `record: channel`). The `Promote Channel` run [41484255066](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41484255066) at `91b4678` finished with `success` at 2026-11-02 08:50 PT, and that is the release time used below. `stable` has moved on since, to `2e69206a`, so the tag's contents at the release are read at the commit it carried, `91b46782`, and again at `stable` today.
- Window for criteria 1 to 3: runs that started from 2026-11-02 08:50 PT to 2026-11-04 08:50 PT. It had closed before this run began.
- Linear: 6 read requests: 5 for the card's epic number (`linear_ops.py children-json` four times, `epics-in-flight` once) and 1 by the name guard (`name_redact.py guard`, which reported `dry — roster unreadable: github members`). The epic is DRE-8814, read as the parent of DRE-8818, DRE-8819, DRE-8820 and this card.
- Local run: `PROOF_LOCAL_STATUS` is `none`. This card has no `Local screen:` row.

**Reading "runs of the `plan.yml` workflow".** `plan.yml` is a reusable workflow (`on: workflow_call`), so GitHub lists no runs under it: `actions/workflows/plan.yml/runs` answers `total_count` 0. Its runs are the jobs of the caller, this repo's `self-plan.yml` (workflow name `Agent Plan`, `uses: dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@main`). The runs below are those `self-plan.yml` runs. The step names are `plan.yml`'s own.

## Identities

- **GitHub, observed: `GH_READ_TOKEN`.** Every GitHub read in this record was made as it: the run lists, the job lists, the job logs, the contents API reads, the compare reads and the pull request reads. The step summary's `proof identity: github read — …` line is not part of this run's context, so the permissions the token holds are not copied here. The one refused write is below.
- **The one refused write.** `GH_TOKEN=$GH_READ_TOKEN gh api -X POST repos/dreadnought-foundry/bureau-pipeline/git/refs -f ref=refs/heads/proof-write-probe-41888398323 -f sha=2e69206a7bba5dc44315feb331f3987d71f5edac` answered `{"message":"Resource not accessible by integration","documentation_url":"https://docs.github.com/rest/git/refs#create-a-reference","status":"403"}` and `gh: Resource not accessible by integration (HTTP 403)`. No ref was created.
- **GitHub, written: `GH_TOKEN`.** Not used. A shadow run pushes nothing.
- **Linear: `LINEAR_API_KEY`.** Read only, through `linear_ops.py`, with `BUREAU_LINEAR_READ_ONLY=1`. No heartbeat, receipt, comment or escalation was written. `linear requests: 6 of 40`.
- **AWS:** `aws: none`. This card needs none.
- **Console proof-reader:** not used. This card has no `Sandbox press:` row. No scratch id was made.

## Criteria

| Criterion | Result |
| -- | -- |
| In bureau-pipeline's runs of the `plan.yml` workflow that started within 48 hours after the release, every run that reached one of the three steps `Mechanical findings — posted before the critic reads them`, `Mechanical findings — the revised plan` or `Mechanical findings — the revised plan (review)` concluded that step with success, and that step's log carries a `commented on <epic>` line, whole or `(part i of n)`. The record lists each run id, epic and step, and quotes the line from the run's log. | Met. The window held 83 `self-plan.yml` runs (first started 2026-11-02 10:33 PT, last 2026-11-04 07:33 PT), all concluded `success`. Read through `actions/runs/<id>/jobs`, 33 of them reached at least one of the three steps, 37 step runs in all: 11 of `posted before the critic reads them`, 4 of `the revised plan` and 22 of `the revised plan (review)`. All 37 concluded `success`, and no step of the three concluded `failure` or `cancelled` in any of the 83 runs. Each step's log, cut from its own `##[group]Run` block, carries exactly one `commented on <epic>` line. Table A lists each run id, epic and step and quotes the line. |
| None of those steps' logs carries `Argument list too long`, `exit code 126` or the warning title `Mechanical note not posted`; the record names the runs it searched and the phrases it searched for, and quotes the `commented on` line from each as its positive control that the log was read. | Met. Searched: the 37 step logs of Table A, from the 33 runs named there, for the phrases `Argument list too long`, `exit code 126` and `Mechanical note not posted`, plus the emitted form of that warning, `##[warning]The list (` / `could not be posted on`. Result: none carries any of them as output. The title `Mechanical note not posted` does appear in each step's log once, but only inside the echoed source of the step's own script (`|| echo "::warning title=Mechanical note not posted::…`), which is the fallback line and not an emitted warning, and no `##[warning]The list (` line was emitted in any of them. I also searched the 33 whole job logs for `Argument list too long`, `exit code 126` and `##[warning]The list`, and found none. Positive control: each of the 37 logs was read and returned its `commented on <epic>` line, quoted in Table A. |
| The record says, for each of those steps, whether the note went up whole or in parts, read off the `commented on` lines in its log, and says plainly whether any note in the window went up in parts. | Met. Every one of the 37 steps in Table A went up whole: each log's line is `commented on <epic>` with no `(part i of n)` suffix, and `scripts/linear_ops.py` prints `commented on <id> (part <i> of <n>)` for a note posted in parts and `commented on <id>` for a whole one. No note in the window went up in parts. The 21 `Review —` steps that post the re-plan summary and also reached (Table B) went up whole as well. |
| The first nightly full `Pipeline Tests` run on `main` after the release concluded success, and its `pytest -v` log shows every test in `tests/test_check_argv_bodies.py` and `tests/test_linear_comment_parts.py` as `PASSED`; the record quotes those lines with the run id. | Met. The first `schedule` run of `Pipeline Tests` (`tests.yml`) on `main` after the release is run [41621740268](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41621740268), started 2026-11-03 02:44 PT at `493a0919`, which is ahead of the release commit `91b46782` (compare `ahead`). It concluded `success`, and all of its jobs did: the five `scripts unit tests (part n)` parts, `scripts unit tests`, `act registry consumers` and `unit test durations refresh`, with `TDD commit discipline` skipped as the nightly's one named exemption. In its `pytest -v` logs `tests/test_check_argv_bodies.py` ran in part 2 (job 125868895463) and `tests/test_linear_comment_parts.py` in part 4 (job 125868895458). Every test of both files is `PASSED`: 34 of 34 and 12 of 12, the 10 test functions of `test_linear_comment_parts.py` with two parametrized into 4 cases. The counts match the files read at `493a0919`. No line of either file reads anything else. All 46 lines are quoted in Section C. |
| At the release tag, `.github/workflows/plan.yml` read through the GitHub contents API holds no line containing `"$(cat "$NOTE")"` and no `linear_ops.py comment` command carrying `${CHANGED}`; the record quotes the posting command of each of the three `Mechanical findings` steps and of the two `Review —` steps that post the re-plan summary. | Met. `plan.yml` read through `repos/dreadnought-foundry/bureau-pipeline/contents/.github/workflows/plan.yml?ref=<ref>`, at the release commit `91b46782` (blob `ba30ea66`) and at `stable` today (blob `1d6ff23e`): 0 lines contain `"$(cat "$NOTE")"` and 0 lines contain `${CHANGED}`, in either read. Positive controls: the same read at `93ece27f`, the parent of the first build merge, holds 3 lines containing `${CHANGED}`, and the read at `6bb17159`, the parent of DRE-8758's merge, holds 4 lines containing `"$(cat "$NOTE")"`. The five posting commands, at `91b46782` with their line numbers, are in Section D. |

## Table A — the 37 `Mechanical findings` steps, read run by run

| # | Run id | Run started | Epic | `Mechanical findings —` step | Step conclusion | Line quoted from the step's log |
| -- | -- | -- | -- | -- | -- | -- |
| 1 | [41501721664](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41501721664) | 2026-11-02 10:33 PT | DRE-8788 | posted before the critic reads them | success | `commented on DRE-8788` |
| 2 | [41501724008](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41501724008) | 2026-11-02 10:33 PT | DRE-8789 | posted before the critic reads them | success | `commented on DRE-8789` |
| 3 | [41505706765](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41505706765) | 2026-11-02 10:59 PT | DRE-8789 | the revised plan (review) | success | `commented on DRE-8789` |
| 4 | [41506498677](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41506498677) | 2026-11-02 11:04 PT | DRE-8788 | the revised plan (review) | success | `commented on DRE-8788` |
| 5 | [41508021785](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41508021785) | 2026-11-02 11:14 PT | DRE-8789 | the revised plan (review) | success | `commented on DRE-8789` |
| 6 | [41509499187](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41509499187) | 2026-11-02 11:23 PT | DRE-8788 | the revised plan (review) | success | `commented on DRE-8788` |
| 7 | [41509887737](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41509887737) | 2026-11-02 11:26 PT | DRE-8789 | the revised plan (review) | success | `commented on DRE-8789` |
| 8 | [41511356740](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41511356740) | 2026-11-02 11:35 PT | DRE-8789 | posted before the critic reads them | success | `commented on DRE-8789` |
| 9 | [41512512160](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41512512160) | 2026-11-02 11:42 PT | DRE-8789 | the revised plan (review) | success | `commented on DRE-8789` |
| 10 | [41515916843](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41515916843) | 2026-11-02 12:29 PT | DRE-8739 | posted before the critic reads them | success | `commented on DRE-8739` |
| 11 | [41522051938](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41522051938) | 2026-11-02 12:45 PT | DRE-8739 | the revised plan (review) | success | `commented on DRE-8739` |
| 12 | [41525787998](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41525787998) | 2026-11-02 13:09 PT | DRE-8739 | the revised plan (review) | success | `commented on DRE-8739` |
| 13 | [41539704448](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41539704448) | 2026-11-02 14:43 PT | DRE-8713 | posted before the critic reads them | success | `commented on DRE-8713` |
| 14 | [41542100148](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41542100148) | 2026-11-02 15:01 PT | DRE-8713 | the revised plan (review) | success | `commented on DRE-8713` |
| 15 | [41545325222](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41545325222) | 2026-11-02 15:25 PT | DRE-8713 | posted before the critic reads them | success | `commented on DRE-8713` |
| 16 | [41546414023](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41546414023) | 2026-11-02 15:34 PT | DRE-8713 | the revised plan (review) | success | `commented on DRE-8713` |
| 17 | [41547758530](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41547758530) | 2026-11-02 15:44 PT | DRE-8713 | the revised plan (review) | success | `commented on DRE-8713` |
| 18 | [41550853004](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41550853004) | 2026-11-02 16:09 PT | DRE-8713 | the revised plan (review) | success | `commented on DRE-8713` |
| 19 | [41572875679](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41572875679) | 2026-11-02 19:33 PT | DRE-8955 | posted before the critic reads them | success | `commented on DRE-8955` |
| 20 | [41572875679](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41572875679) | 2026-11-02 19:33 PT | DRE-8955 | the revised plan | success | `commented on DRE-8955` |
| 21 | [41574476573](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41574476573) | 2026-11-02 19:49 PT | DRE-8955 | the revised plan (review) | success | `commented on DRE-8955` |
| 22 | [41578282840](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41578282840) | 2026-11-02 20:27 PT | DRE-8740 | posted before the critic reads them | success | `commented on DRE-8740` |
| 23 | [41578282840](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41578282840) | 2026-11-02 20:27 PT | DRE-8740 | the revised plan | success | `commented on DRE-8740` |
| 24 | [41579138197](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41579138197) | 2026-11-02 20:36 PT | DRE-8713 | posted before the critic reads them | success | `commented on DRE-8713` |
| 25 | [41579138197](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41579138197) | 2026-11-02 20:36 PT | DRE-8713 | the revised plan | success | `commented on DRE-8713` |
| 26 | [41580568873](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41580568873) | 2026-11-02 20:51 PT | DRE-8740 | the revised plan (review) | success | `commented on DRE-8740` |
| 27 | [41581496093](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41581496093) | 2026-11-02 21:02 PT | DRE-8713 | the revised plan (review) | success | `commented on DRE-8713` |
| 28 | [41582559267](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41582559267) | 2026-11-02 21:12 PT | DRE-8740 | the revised plan (review) | success | `commented on DRE-8740` |
| 29 | [41708291706](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41708291706) | 2026-11-03 11:58 PT | DRE-8790 | posted before the critic reads them | success | `commented on DRE-8790` |
| 30 | [41708291706](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41708291706) | 2026-11-03 11:58 PT | DRE-8790 | the revised plan | success | `commented on DRE-8790` |
| 31 | [41714106153](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41714106153) | 2026-11-03 12:36 PT | DRE-8790 | the revised plan (review) | success | `commented on DRE-8790` |
| 32 | [41716120884](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41716120884) | 2026-11-03 12:49 PT | DRE-8790 | the revised plan (review) | success | `commented on DRE-8790` |
| 33 | [41718550616](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41718550616) | 2026-11-03 13:05 PT | DRE-8790 | the revised plan (review) | success | `commented on DRE-8790` |
| 34 | [41720688612](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41720688612) | 2026-11-03 13:19 PT | DRE-8790 | posted before the critic reads them | success | `commented on DRE-8790` |
| 35 | [41721737163](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41721737163) | 2026-11-03 13:25 PT | DRE-8790 | the revised plan (review) | success | `commented on DRE-8790` |
| 36 | [41724152551](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41724152551) | 2026-11-03 13:42 PT | DRE-8790 | the revised plan (review) | success | `commented on DRE-8790` |
| 37 | [41726376149](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/41726376149) | 2026-11-03 13:56 PT | DRE-8790 | the revised plan (review) | success | `commented on DRE-8790` |

## Table B — the 21 `Review —` steps that post the re-plan summary and reached in the window

These are the two steps of Section D, `the revised plan is reviewed again` and `the bound parks the plan in Triage`. Every one concluded `success`.

| Run id | `Review —` step | Step conclusion | Line quoted from the step's log |
| -- | -- | -- | -- |
| 41505706765 | the revised plan is reviewed again | success | `commented on DRE-8789` |
| 41506498677 | the revised plan is reviewed again | success | `commented on DRE-8788` |
| 41508021785 | the revised plan is reviewed again | success | `commented on DRE-8789` |
| 41509499187 | the revised plan is reviewed again | success | `commented on DRE-8788` |
| 41509887737 | the bound parks the plan in Triage | success | `commented on DRE-8789` |
| 41512512160 | the revised plan is reviewed again | success | `commented on DRE-8789` |
| 41522051938 | the revised plan is reviewed again | success | `commented on DRE-8739` |
| 41525787998 | the revised plan is reviewed again | success | `commented on DRE-8739` |
| 41546414023 | the revised plan is reviewed again | success | `commented on DRE-8713` |
| 41547758530 | the revised plan is reviewed again | success | `commented on DRE-8713` |
| 41550853004 | the bound parks the plan in Triage | success | `commented on DRE-8713`; `commented on DRE-8713` |
| 41574476573 | the revised plan is reviewed again | success | `commented on DRE-8955` |
| 41580568873 | the revised plan is reviewed again | success | `commented on DRE-8740` |
| 41581496093 | the revised plan is reviewed again | success | `commented on DRE-8713` |
| 41582559267 | the revised plan is reviewed again | success | `commented on DRE-8740` |
| 41714106153 | the revised plan is reviewed again | success | `commented on DRE-8790` |
| 41716120884 | the revised plan is reviewed again | success | `commented on DRE-8790` |
| 41718550616 | the bound parks the plan in Triage | success | `commented on DRE-8790` |
| 41721737163 | the revised plan is reviewed again | success | `commented on DRE-8790` |
| 41724152551 | the revised plan is reviewed again | success | `commented on DRE-8790` |
| 41726376149 | the bound parks the plan in Triage | success | `commented on DRE-8790`; `commented on DRE-8790` |

## C — the quoted `pytest -v` lines of nightly run 41621740268

`tests/test_check_argv_bodies.py`, job 125868895463 (`scripts unit tests (part 2)`), 34 lines:

```
tests/test_check_argv_bodies.py::test_the_repos_workflows_carry_no_site PASSED
tests/test_check_argv_bodies.py::test_check_exits_0_on_the_repo_and_counts_every_workflow PASSED
tests/test_check_argv_bodies.py::test_the_check_is_not_blind_to_the_end_of_any_real_run_block PASSED
tests/test_check_argv_bodies.py::test_the_fixture_holds_the_exact_line PASSED
tests/test_check_argv_bodies.py::test_the_fixture_is_exactly_one_site_at_that_line PASSED
tests/test_check_argv_bodies.py::test_check_pointed_at_the_fixture_exits_1 PASSED
tests/test_check_argv_bodies.py::test_a_variable_read_from_a_file_and_passed_to_a_command PASSED
tests/test_check_argv_bodies.py::test_a_variable_is_followed_transitively PASSED
tests/test_check_argv_bodies.py::test_a_read_nested_inside_another_substitution PASSED
tests/test_check_argv_bodies.py::test_a_command_inside_a_substitution_is_read_too PASSED
tests/test_check_argv_bodies.py::test_a_redirect_read PASSED
tests/test_check_argv_bodies.py::test_a_backquoted_cat PASSED
tests/test_check_argv_bodies.py::test_a_backslash_continued_command_is_one_command PASSED
tests/test_check_argv_bodies.py::test_an_unbounded_head_and_one_past_the_bound PASSED
tests/test_check_argv_bodies.py::test_a_heredoc_written_output_in_an_env_value PASSED
tests/test_check_argv_bodies.py::test_a_heredoc_written_output_in_a_job_or_workflow_env PASSED
tests/test_check_argv_bodies.py::test_a_heredoc_written_output_in_a_run_command_argument PASSED
tests/test_check_argv_bodies.py::test_a_heredoc_written_output_through_a_variable PASSED
tests/test_check_argv_bodies.py::test_a_prefix_assignment_puts_a_read_in_the_commands_environment PASSED
tests/test_check_argv_bodies.py::test_a_prefix_assignment_through_a_variable PASSED
tests/test_check_argv_bodies.py::test_an_exported_read_reaches_the_next_external_command PASSED
tests/test_check_argv_bodies.py::test_a_read_exported_after_it_is_assigned PASSED
tests/test_check_argv_bodies.py::test_an_exported_read_reaches_a_command_inside_a_substitution PASSED
tests/test_check_argv_bodies.py::test_a_site_message_names_the_file_line_step_limit_and_fix PASSED
tests/test_check_argv_bodies.py::test_a_body_file_is_not_a_site PASSED
tests/test_check_argv_bodies.py::test_a_bounded_head_is_not_a_site PASSED
tests/test_check_argv_bodies.py::test_a_read_into_a_builtin_redirected_to_a_file_is_not_a_site PASSED
tests/test_check_argv_bodies.py::test_a_workflow_command_echo_is_not_a_site PASSED
tests/test_check_argv_bodies.py::test_a_read_that_only_reaches_builtins_is_not_a_site PASSED
tests/test_check_argv_bodies.py::test_a_bounded_or_builtin_environment_is_not_a_site PASSED
tests/test_check_argv_bodies.py::test_a_single_line_output_in_env_is_not_a_site PASSED
tests/test_check_argv_bodies.py::test_a_cat_of_a_here_document_is_not_a_site PASSED
tests/test_check_argv_bodies.py::test_text_inside_single_quotes_a_comment_or_a_here_document_is_not_read PASSED
tests/test_check_argv_bodies.py::test_a_new_workflow_is_found_by_glob PASSED
```

`tests/test_linear_comment_parts.py`, job 125868895458 (`scripts unit tests (part 4)`), 12 lines:

```
tests/test_linear_comment_parts.py::test_a_200_kib_body_posts_through_body_file_and_reads_back_whole PASSED
tests/test_linear_comment_parts.py::test_a_body_from_stdin_still_posts_whole PASSED
tests/test_linear_comment_parts.py::test_a_size_refusal_naming_a_maximum_posts_numbered_parts[graphql] PASSED
tests/test_linear_comment_parts.py::test_a_size_refusal_naming_a_maximum_posts_numbered_parts[413] PASSED
tests/test_linear_comment_parts.py::test_a_one_line_longer_than_the_maximum_is_cut_at_the_size PASSED
tests/test_linear_comment_parts.py::test_parts_split_on_line_boundaries_where_a_line_fits PASSED
tests/test_linear_comment_parts.py::test_a_retried_part_is_not_doubled PASSED
tests/test_linear_comment_parts.py::test_a_size_refusal_naming_no_number_is_not_split[graphql-no-number] PASSED
tests/test_linear_comment_parts.py::test_a_size_refusal_naming_no_number_is_not_split[413-no-number] PASSED
tests/test_linear_comment_parts.py::test_a_receipt_composed_with_act_is_never_split PASSED
tests/test_linear_comment_parts.py::test_a_parted_post_prints_one_line_per_part PASSED
tests/test_linear_comment_parts.py::test_a_whole_post_prints_the_line_it_always_has PASSED
```

## D — the posting commands, as read at `91b46782`

The three `Mechanical findings` steps each post the note from a file, and a refused post never stops the critic. The command, with its line number in `plan.yml`:

- `Mechanical findings — posted before the critic reads them`, lines 3146 to 3148:

  ```
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" \
    --body-file="$NOTE" \
    || echo "::warning title=Mechanical note not posted::The list ($(wc -c < "$NOTE") bytes) could not be posted on $EPIC. The critic still reads it, and it is printed in this step's log above."
  ```

- `Mechanical findings — the revised plan`, lines 3663 to 3665:

  ```
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" \
    --body-file="$NOTE" \
    || echo "::warning title=Mechanical note not posted::The list ($(wc -c < "$NOTE") bytes) could not be posted on $EPIC. The critic still reads it, and it is printed in this step's log above."
  ```

- `Mechanical findings — the revised plan (review)`, lines 5433 to 5435:

  ```
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" \
    --body-file="$NOTE" \
    || echo "::warning title=Mechanical note not posted::The list ($(wc -c < "$NOTE") bytes) could not be posted on $EPIC. The critic still reads it, and it is printed in this step's log above."
  ```

The two `Review —` steps that post the re-plan summary build each comment in `$COMMENT_FILE` and post it from the file:

- `Review — the revised plan is reviewed again`, lines 5521 and 5528 (the dispatched branch and the failed-dispatch branch):

  ```
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" --body-file="$COMMENT_FILE"
  ```

- `Review — the bound parks the plan in Triage`, line 5594:

  ```
  python3 .bureau-pipeline/scripts/linear_ops.py comment "$EPIC" --body-file="$COMMENT_FILE"
  ```

At `stable` today the same five commands read the same, at lines 3164 to 3166, 3681 to 3683, 5451 to 5453, 5539 and 5546, and 5612.
