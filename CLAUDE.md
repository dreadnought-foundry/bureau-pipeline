# bureau-pipeline

<!-- bureau:card-standard:begin -->
## Writing a Linear card for the bureau pipeline

Before you write or edit a Linear card, load the `dreadnought-standards:dreadnought-card-quality` Skill.
The contract lives once, in [`bureau-pipeline/standards/card-quality.md`](https://github.com/dreadnought-foundry/bureau-pipeline/blob/main/standards/card-quality.md); the Skill is generated from it, and bureau-pipeline's `scripts/validate_card.py` is the live gate that enforces it.
Skill missing or stale? `claude plugin update dreadnought-standards@dreadnought` — a first install is described in agent-bureau's `plugins/dreadnought-standards/README.md`.
A headless pipeline agent cannot load Skills; it receives the same file in its context instead.
This block is a pointer, installed from agent-bureau's `scaffold/claude-md/card-standard.md` and compared byte for byte by `make check-channel-fleet` — change the standard, never this block.
<!-- bureau:card-standard:end -->
