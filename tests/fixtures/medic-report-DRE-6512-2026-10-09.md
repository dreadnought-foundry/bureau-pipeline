The Pipeline Tests run on `main` failed twice because one automated test is out of date, not because of an outage or a missing secret. It is a code problem, not a flake: the same test fails the same way on the retry.

Why: the DRE-6490 change taught the escalation step to look up where a card has been before deciding whether it already asked the CEO a question. That change updated two of the test files that touch this step, but missed a third, which still uses a stand-in for the card tracker that has no "where has this card been" lookup. So the step can't read the history, asks again, and posts a duplicate note, where the test expects it to stay quiet.

The fix is small and needs an engineer, not an operator: update the test's stand-in so it can answer the history lookup. The product code looks correct. Until then, `main` stays red, and any branch cut from it will hit the same one failure.

Everything else passed: 5,215 tests passed, 1 failed, 1 skipped.

Technical detail: `tests/test_planning_escalation_prior_answer.py::TestTheBlockReadsTheWholeThread::test_a_retry_whose_note_is_on_the_card_never_reads_it` fails (assert at line 528) because the `_Lops` fake (line 147) has no `lane_history`. `planning_escalation.py` (about line 1274) now calls `linear_ops.lane_history(identifier)` and falls open to "asking again" on the AttributeError. Run: [https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38001512530](<https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/38001512530>), head `bcb36ad`.