#!/bin/zsh
# Poe reruns of the 40 ctxfix40 cells, one model after another: claude-sonnet-5.5, gemini-3.1-pro, GPT-5.4 (page crop).
# Runs whenever the balance is >= 3000 points; checks hourly; stops at the end of the list, after 6 passes without
# progress on one model, or on 2026-11-13. Answers are staged in <model>/vision_ctxfix40; released answers are untouched.
cd "${0:A:h}"
L=../runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/llm_baseline_v30
IDS=$L/ctxfix40_task_ids.json; LOG=logs_ctxfix40_20261104.txt
grep -q '^POE_API_KEY=' ../../cosmo-datamind/.env || { echo "$(date) POE_API_KEY missing, abort" >> $LOG; exit 1; }
count() { ls "$1" 2>/dev/null | wc -l | tr -d ' '; }
bal() { python3 - <<'PY'
import json,urllib.request
k=[l.split("=",1)[1].strip().strip('"') for l in open("../../cosmo-datamind/.env") if l.startswith("POE_API_KEY=")][0]
try: print(json.load(urllib.request.urlopen(urllib.request.Request("https://api.poe.com/usage/current_balance",headers={"Authorization":"Bearer "+k}),timeout=30))["current_point_balance"])
except Exception as e: print("balance-error", repr(e)[:80])
PY
}
echo "$(date) v4 started (claude, gemini, gpt)" >> $LOG
for MODEL in claude-sonnet-5.5 gemini-3.1-pro GPT-5.4; do
  D=$L/poe_$MODEL/vision_ctxfix40/answers; stall=0
  while true; do
    n=$(count $D)
    [[ $n -ge 40 ]] && { echo "$(date) $MODEL COMPLETE 40/40" >> $LOG; break; }
    [[ $(date +%Y%m%d) -ge 20261113 ]] && { echo "$(date) STOPPED at $MODEL $n/40: cut-off date" >> $LOG; exit 0; }
    b=$(bal); echo "$(date) $MODEL $n/40 balance $b" >> $LOG
    if [[ $b == <-> && $b -ge 3000 ]]; then
      python3 llm_baseline_v30.py --provider poe --model $MODEL --mode vision --only $IDS --tag ctxfix40 >> $LOG 2>&1; rc=$?
      m=$(count $D); echo "$(date) $MODEL pass exit $rc answers $m balance $(bal)" >> $LOG
      if [[ $m -le $n ]]; then stall=$((stall + 1)); else stall=0; fi
      [[ $stall -ge 6 ]] && { echo "$(date) $MODEL STALLED at $m/40" >> $LOG; break; }
      continue
    fi
    sleep 3600
  done
done
echo "$(date) v4 finished" >> $LOG
