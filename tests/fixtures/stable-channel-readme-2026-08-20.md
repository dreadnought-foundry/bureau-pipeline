<!-- README.md, verbatim at af3f7b05 (2026-08-20): the channel entry as it read before
     DRE-2553 moved the fleet onto `@stable` on 2026-08-23. Pinned for
     tests/test_self_hosting_docs.py (DRE-2709) — the stable-channel guard must
     FAIL on this text. Do not edit it to match today's docs. -->

**`stable`: proven automatically, pinned by nobody (DRE-2551).** There is
now a second ref in this repo. `promote-channel.yml` keeps a moving tag,
`stable`, on the newest commit on `main` that carries a green
`integration-harness` stamp — `harness.yml` runs on every push to `main`,
so trunk commits have stamps of their own to read. The move is pushed with
the bot App identity, never `github.token`, precisely so `release-gate.yml`
(whose trigger now reads `["v*", "stable"]`) actually fires and validates
it; a repository variable, `CHANNEL_HOLD`, pauses promotion when set to a
reason, and unset means run. **Write that reason as
`who=<name> since=<ISO date> <why>`** — the staleness alarm below reports a
held channel back to the CEO, and GitHub will not tell it who set a variable
or when (that API needs an admin token the workflows do not carry), so a hold
that skips the convention is reported as a hold nobody will own. What
automatic promotion deliberately does **not** do:

- **No product repo pins `@stable`.** Nothing consumes this ref. No stub
  was re-pointed, and the pairing rule above is untouched — the fleet is on
  `vN` with a matching `pipeline_ref: vN`, exactly as before.
- **`vN` promotion is unchanged and remains operator-only**: the `git tag
  -f` and the stub re-point above are still a human's job, and still the
  gate between a merge here and anything the fleet runs.

Its value today is that the pre-tag question — *which sha has the harness
proved?* — is answered continuously instead of by a hand-run dispatch; the
operator still picks the soaked sha and still cuts it. Whether the
fleet ever pins `stable` directly and the manual cut retires is a **later
decision, not part of DRE-2551**; until it is made and written here, `vN`
is the only channel the fleet consumes.
