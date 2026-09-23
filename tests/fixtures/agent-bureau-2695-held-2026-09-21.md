<!-- agent-bureau #2695, the verdict the evidence gate held three times on
     2026-09-21 (17:28, 19:48 and 22:16 PT) as `job-coverage` defects. The
     three sentences the 17:28 hold quoted are reproduced verbatim below;
     none of them makes a claim about what any CI job did (DRE-4648). -->
VERDICT: REQUEST_CHANGES cause:defect

## Summary

The change records a run per repository, and nothing proves the roster and
the recorder agree. That is worth settling before this merges.

## For the fixing agent

I could not execute the suite in this checkout: `python3 -m pytest` reports `No module named pytest` and the repo is a depth-1 clone (`git log --oneline HEAD^2` → `fatal: ambiguous argument 'HEAD^2'`)…

Every finding below is from the diff and the surrounding source, not from a run, and I assert nothing about what any job did.

The roster and the recorder can disagree, and the diff has nowhere that says so out loud.

Settle it one of three ways, and say which in the body: (a) show that an uncovered repo is impossible by construction — the recorder is org-wide and every `REPOS` entry is in it — and pin it with a check that fails when a roster entry has no recorded runs…
