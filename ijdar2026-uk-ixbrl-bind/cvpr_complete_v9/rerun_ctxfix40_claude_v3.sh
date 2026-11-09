#!/bin/zsh
# Claude Sonnet 5.5 (Poe) on the remaining ctxfix40 cells; staged in vision_ctxfix40, released answers untouched.
# Runs whenever the balance is >= 3000 (one call costs about 1000 points); checks hourly; stops at 40/40,
# after 6 passes without progress, or on 2026-11-13 so that the merge fits before the deadline.
cd "${0:A:h}"
L=../runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/llm_baseline_v30
IDS=$L/ctxfix40_task_ids.json; LOG=logs_ctxfix40_20261104.txt
D=$L/poe_claude-sonnet-5.5/vision_ctxfix40/answers
grep -q '^POE_API_KEY=' ../../cosmo-datamind/.env || { echo "$(date) POE_API_KEY missing, abort" >> $LOG; exit 1; }
count() { ls "$1" 2>/dev/null | wc -l | tr -d ' '; }
bal() { python3 - <<'PY'
import json,urllib.request
k=[l.split("=",1)[1].strip().strip('"') for l in open("../../cosmo-datamind/.env") if l.startswith("POE_API_KEY=")][0]
try: print(json.load(urllib.request.urlopen(urllib.request.Request("https://api.poe.com/usage/current_balance",headers={"Authorization":"Bearer "+k}),timeout=30))["current_point_balance"])
except Exception as e: print("balance-error", repr(e)[:80])
PY
}
stall=0
echo "$(date) v3 started" >> $LOG
while true; do
  n=$(count $D)
  [[ $n -ge 40 ]] && { echo "$(date) CLAUDE COMPLETE 40/40 balance $(bal)" >> $LOG; break; }
  [[ $(date +%Y%m%d) -ge 20261113 ]] && { echo "$(date) CLAUDE STOPPED at $n/40: cut-off date" >> $LOG; break; }
  b=$(bal); echo "$(date) claude $n/40 balance $b" >> $LOG
  if [[ $b == <-> && $b -ge 3000 ]]; then
    python3 llm_baseline_v30.py --provider poe --model claude-sonnet-5.5 --mode vision --only $IDS --tag ctxfix40 >> $LOG 2>&1; rc=$?
    m=$(count $D); echo "$(date) claude pass exit $rc answers $m balance $(bal)" >> $LOG
    if [[ $m -le $n ]]; then stall=$((stall + 1)); else stall=0; fi
    [[ $stall -ge 6 ]] && { echo "$(date) CLAUDE STALLED at $m/40 after 6 passes without progress" >> $LOG; break; }
  fi
  sleep 3600
done
