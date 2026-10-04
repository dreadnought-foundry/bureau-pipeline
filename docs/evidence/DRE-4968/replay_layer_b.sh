#!/usr/bin/env bash
# Layer B (DRE-4967's read-only verify agent) replayed over proposal b9eecae64787,
# the same four steps the verify leg runs (prepare, the card's repo at its default
# branch, the agent with the step's own prompt, verdict) — DRE-5459 §2's shape.
#
#   BP=<bureau-pipeline tree at main>  OUT=<dir holding proposal-replay.json from Layer A>
#   CARDS="DRE-2897 DRE-2382"   (default: every card in verify-targets.json)
#   LINEAR_API_KEY must be live for `targets` (it reads each card's text; reads only).
#   The agent runs on the operator's local Claude login. ~$0.35-0.45 and ~1 min per card.
set -euo pipefail
: "${BP:?}" "${OUT:?}"
cd "$OUT"
python3 "$BP/scripts/groom_verify_agent.py" targets --proposal proposal-replay.json \
  --out verify-targets.json --matrix-out matrix.json
MODEL=$(python3 "$BP/scripts/model_fallback.py" select verifier --explain-file model-why.txt --effort-file model-effort.txt)
EFFORT=$(cat model-effort.txt 2>/dev/null || true)
echo "model: $MODEL effort: ${EFFORT:-none}"; cat model-why.txt; echo
CARDS=${CARDS:-$(python3 -c 'import json; print(" ".join(r["card"] for r in json.load(open("matrix.json"))))')}
PROMPT='Read the file verify-input.md at the workspace root and do what it
says. The repository to check is checked out under target/.

Write only the one answer file verify-input.md names. Change
nothing under target/, run no commands, and do not commit, push or
open a pull request.'
for CARD in $CARDS; do
  REPO=$(python3 -c 'import json,sys; print(next((r["repository"] for r in json.load(open("matrix.json")) if r["card"]==sys.argv[1]), ""))' "$CARD")
  D="leg-$CARD"; rm -rf "$D"; mkdir -p "$D"; cp verify-targets.json "$D/"; cd "$D"
  python3 "$BP/scripts/groom_verify_agent.py" prepare --targets verify-targets.json --card "$CARD" \
    --out verify-input.md --started-at-out started.txt
  OUTCOME=skipped
  if [ -n "$REPO" ]; then
    mkdir target
    gh api "repos/$REPO/tarball" > t.tgz
    tar -xzf t.tgz -C target --strip-components=1
    rm t.tgz
    if claude -p "$PROMPT" --max-turns 40 --model "$MODEL" ${EFFORT:+--effort "$EFFORT"} \
         --allowedTools "Read,Glob,Grep,Write" --output-format json > execution.json; then
      OUTCOME=success; else OUTCOME=failure; fi
  fi
  python3 "$BP/scripts/groom_verify_agent.py" verdict --card "$CARD" --targets verify-targets.json \
    --raw verify-verdict.json --execution-file execution.json --step-outcome "$OUTCOME" \
    --started-at started.txt --out "../verdict-$CARD.json"
  cd ..
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(sys.argv[2], d["verdict"], ["%s:%s" % (p.get("file"), p.get("line")) for p in d.get("proof") or [] if p.get("file")][:3])' "verdict-$CARD.json" "$CARD"
done
