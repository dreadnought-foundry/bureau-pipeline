"""Layer A (DRE-4966's deterministic check) replayed over proposal b9eecae64787.

Run from a bureau-pipeline tree at main, with the proposal artifact beside it:
    python3 replay_layer_a.py <bureau-pipeline dir> <proposal.json> <out dir>
Live run needs LINEAR_API_KEY (reads only) and GH_TOKEN (Bureau App installation
token) in the environment; with neither, every card comes back unread, which is
the smoke test.
"""
import json
import os
import sys

bp, proposal_path, out = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, os.path.join(bp, "scripts"))
import groom_verify  # noqa: E402
import linear_ops  # noqa: E402

p = json.load(open(proposal_path))
assert p["id"] == "b9eecae64787", p["id"]
res = groom_verify.check(p, lops=linear_ops)
os.makedirs(out, exist_ok=True)
json.dump(res, open(os.path.join(out, "replay-layer-a.json"), "w"), indent=1)
print("cards checked:", len(res["cards"]), "| moved to cancel:", res["cancel"],
      "| unread:", {k: (len(v["cards"]), v["why"][:120]) for k, v in res["unread"].items()})
for row in res["cards"]:
    if row["identifier"] in ("DRE-2897", "DRE-2382", "DRE-3526"):
        print(json.dumps(row))
print("DRE-3526 in the check's window:", any(r["identifier"] == "DRE-3526" for r in res["cards"]))
groom_verify.settle(p, res)
json.dump(p, open(os.path.join(out, "proposal-replay.json"), "w"))
print("wrote", os.path.join(out, "proposal-replay.json"))
