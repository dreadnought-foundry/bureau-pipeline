#!/usr/bin/env bash
# Read-off for one 06:00 PT groomer morning (DRE-4968, DRE-4973, DRE-5459 obs 4). First use: 2026-10-05.
# Read-only. Run AFTER the post job has finished (expect ~06:20 PT; 06:30 PT is the bar).
# Usage: DAY=2026-10-05 bash readoff_morning.sh [OUT_DIR]   (RUN=<id> to name the run yourself)
# In PDT the morning run is created at 13:xxZ; after 2026-11-01 (PST) it is 14:xxZ — set HOUR=14.
set -euo pipefail
R=dreadnought-foundry/bureau-pipeline
DAY=${DAY:-2026-10-05}
HOUR=${HOUR:-13}
OUT=${1:-./morning-$DAY}
mkdir -p "$OUT"
cd "$OUT"

# 0. Is the groomer still on? (It was re-enabled 2026-10-04 07:31 PT; a pause turns it off again.)
gh workflow list --all -R "$R" | grep -E '^Groomer\b' | tee workflow-state.txt

# 1. The morning's runs. Take the `schedule` run created ~13:00Z whose gate says "it is 06:xx PT".
gh run list -R "$R" --workflow self-groomer.yml --created "$DAY" -L 10 \
  --json databaseId,event,createdAt,startedAt,updatedAt,status,conclusion,displayTitle | tee runs.json
RUN=${RUN:-$(python3 -c 'import json,sys; rs=[r for r in json.load(open("runs.json")) if r["event"]=="schedule" and r["createdAt"].startswith(sys.argv[1]+"T"+sys.argv[2])]; print(rs[-1]["databaseId"] if rs else "")' "$DAY" "$HOUR")}
[ -n "$RUN" ] || { echo "no ${HOUR}:xxZ schedule run on $DAY — the morning did not fire; check workflow-state.txt"; exit 1; }
echo "RUN=$RUN" | tee run-id.txt

# 2. Every job, with start and end converted to PT.
gh run view "$RUN" -R "$R" --json jobs > jobs.json
python3 - <<'PY' | tee jobs-pt.tsv
import json, datetime, zoneinfo
pt = zoneinfo.ZoneInfo("America/Los_Angeles")
def p(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(pt).strftime("%H:%M:%S PT") if s else "-"
for j in json.load(open("jobs.json"))["jobs"]:
    print(f'{j["name"]}\t{j["conclusion"]}\t{p(j["startedAt"])}\t{p(j["completedAt"])}')
PY

# 3. Logs PER JOB. `gh run view --log` on the whole run fails ("too many API requests").
gh api "repos/$R/actions/runs/$RUN/jobs?per_page=100" --jq '.jobs[] | "\(.id)\t\(.name)"' > job-ids.tsv
while IFS=$'\t' read -r id name; do
  case "$name" in
    gate|*" / groom"|*" / post"|*" / lookup ("*) gh run view -R "$R" --job "$id" --log > "job-$id.log" ;;
  esac
done < job-ids.tsv
# one verify leg with a repo, for the fold-step output (DRE-5459 obs 1). Jobs are
# `schedule / …` on the morning and `call / …` on a dispatch; both are matched.
FIRST_VERIFY=$(grep -m1 -E '(schedule|call) / verify \(DRE-[0-9]+, ' job-ids.tsv | cut -f1 || true)
[ -n "$FIRST_VERIFY" ] && gh run view -R "$R" --job "$FIRST_VERIFY" --log > "job-$FIRST_VERIFY.verify.log"
grep -h -E 'it is 0[0-9]:[0-9]{2} PT|groom-lookups:|linear-budget:|Unread:|Verify step:|commented on|^.*- (still-needed|partly-solved|done-elsewhere|obsolete|not-worth-it|unverified|excluded): |not posted|groom context:|Ranked by' job-*.log \
  | cut -c1-600 | tee key-lines.txt

# 4. Artifacts: the verified proposal, every verdict, every owner's lookups.
#    proposal/verify-targets.json is the PRE-fold targets file; each verdict's `lookup` field carries the folded state.
gh run download "$RUN" -R "$R" -n groom-proposal-verified -D verified
gh run download "$RUN" -R "$R" -n groom-proposal -D proposal
gh run download "$RUN" -R "$R" -p 'groom-verdict-*' -D verdicts
gh run download "$RUN" -R "$R" -p 'groom-lookups-*' -D lookups || true

# 5. The numbers every record quotes, straight from the artifact.
python3 - <<'PY' | tee summary.txt
import json, glob
d = json.load(open("verified/proposal-verified.json"))
print("proposal id:", d.get("id"))
pack = (d.get("judgement") or {}).get("pack") or {}
print("merged PRs in the ranked read:", pack.get("merged_prs"))
v = d.get("verify") or {}
print("verify:", json.dumps({k: v.get(k) for k in ("cards", "counts", "cost_usd", "wall_clock_seconds", "unverified")}))
print("excluded:", json.dumps(v.get("excluded")))
print("planning list, in order:")
for r in sorted(d["outcomes"]["now"], key=lambda r: r["position"]):
    m = r.get("verify") or {}
    proofs = [f'{p.get("file")}:{p.get("line")}' for p in (m.get("proof") or []) if p.get("file")]
    print(f'  {r["position"]:>2} {r["identifier"]:<9} band={r.get("band")} verdict={m.get("verdict")} proofs={proofs[:3]}')
print("cancel list:")
for r in d["outcomes"].get("dead", []):
    print(f'  {r["identifier"]:<9} {str(r.get("reason"))[:160]}')
ver = d.get("verification") or {}
print("layer A unread sources:", json.dumps({k: {"n": len(e.get("cards", [])), "why": e.get("why")} for k, e in (ver.get("unread") or {}).items()}))
print("layer A moved to cancel:", ver.get("moved_to_cancel"), "| cancels rejected:", ver.get("cancels_rejected"))
# DRE-5317's stop writes these under proposal["verify"] (groom_verify_agent.apply), not at the top level.
for k in ("all_lookups_failed", "merged_prs_unread", "lookups_failed", "not_posted_why"):
    print(f"verify.{k}:", json.dumps(v.get(k))[:600])
for k in ("cancels_refused", "stale_priorities"):
    if k in d: print(f"{k}:", json.dumps(d[k])[:600])
PY
echo "done — files in $(pwd)"
