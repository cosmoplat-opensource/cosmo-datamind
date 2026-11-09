#!/bin/zsh
# Rerun the 40 development cells (one company) whose OCR table context predates the v30 perception fix, for the
# zero-shot baselines that read it: deepseek-flash page crop and OCR text (DeepSeek API, once each) and Claude Sonnet 5.5
# (Poe), which runs until the points run out and is retried every 3 hours as the daily grant arrives.  Answers go to
# *_ctxfix40 directories; the released answers are not touched.  Run with zsh.
cd ${0:A:h}
L=../runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/llm_baseline_v30
IDS=$L/ctxfix40_task_ids.json; LOG=logs_ctxfix40_20261104.txt
grep -q '^POE_API_KEY=' ../../cosmo-datamind/.env || { echo "$(date) POE_API_KEY missing, abort" >> $LOG; exit 1; }
bal() { python3 - <<'PY'
import json,urllib.request
k=[l.split("=",1)[1].strip().strip('"') for l in open("../../cosmo-datamind/.env") if l.startswith("POE_API_KEY=")][0]
try: print(json.load(urllib.request.urlopen(urllib.request.Request("https://api.poe.com/usage/current_balance",headers={"Authorization":"Bearer "+k}),timeout=30))["current_point_balance"])
except Exception as e: print("balance-error", repr(e)[:80])
PY
}
count() { ls $1 2>/dev/null | wc -l | tr -d ' '; }
for mode in vision text; do
  for try in 1 2 3; do
    [[ $(count $L/${mode}_ctxfix40/answers) -ge 40 ]] && break
    python3 llm_baseline_v30.py --mode $mode --only $IDS --tag ctxfix40 >> $LOG 2>&1; rc=$?
    echo "$(date) deepseek $mode pass $try exit $rc answers $(count $L/${mode}_ctxfix40/answers)" >> $LOG
  done
done
D=$L/poe_claude-sonnet-5.5/vision_ctxfix40/answers; stall=0
while true; do
  n=$(count $D)
  [[ $n -ge 40 ]] && { echo "$(date) CLAUDE COMPLETE 40/40 balance $(bal)" >> $LOG; break; }
  b=$(bal); echo "$(date) claude $n/40 balance $b" >> $LOG
  if [[ $b == <-> && $b -ge 5000 ]]; then
    python3 llm_baseline_v30.py --provider poe --model claude-sonnet-5.5 --mode vision --only $IDS --tag ctxfix40 >> $LOG 2>&1; rc=$?
    m=$(count $D); echo "$(date) claude pass exit $rc answers $m balance $(bal)" >> $LOG
    if [[ $m -le $n ]]; then stall=$((stall + 1)); else stall=0; fi
    [[ $stall -ge 4 ]] && { echo "$(date) CLAUDE STALLED at $m/40 after 4 passes without progress" >> $LOG; break; }
    [[ $m -ge 40 ]] && continue
  fi
  sleep 10800
done
