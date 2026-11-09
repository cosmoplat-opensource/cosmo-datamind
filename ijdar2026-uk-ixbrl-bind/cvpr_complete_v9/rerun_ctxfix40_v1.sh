#!/bin/zsh
# Rerun the 40 development cells whose OCR context predates the v30 perception fix, for the zero-shot baselines.
# deepseek zero-shot runs once; Claude Sonnet 5.5 (Poe) runs until the points run out, then retries every 3 hours
# as the daily grant arrives, until all 40 answers exist.  Answers go to *_ctxfix40 directories; the released
# answers are not touched until all 40 are in.
cd ${0:A:h}
L=../runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/llm_baseline_v30
IDS=$L/ctxfix40_task_ids.json; LOG=logs_ctxfix40_20261104.txt
bal() { python3 - <<'PY'
import json,urllib.request
k=[l.split("=",1)[1].strip().strip('"') for l in open("../../cosmo-datamind/.env") if l.startswith("POE_API_KEY=")][0]
try: print(json.load(urllib.request.urlopen(urllib.request.Request("https://api.poe.com/usage/current_balance",headers={"Authorization":"Bearer "+k}),timeout=30))["current_point_balance"])
except Exception as e: print("balance-error", repr(e)[:80])
PY
}
if [[ $(ls $L/vision_ctxfix40/answers 2>/dev/null | wc -l) -lt 40 ]]; then
  echo "$(date) deepseek zero-shot start" >> $LOG
  python3 llm_baseline_v30.py --mode vision --only $IDS --tag ctxfix40 >> $LOG 2>&1
  echo "$(date) deepseek zero-shot exit $? answers $(ls $L/vision_ctxfix40/answers | wc -l)" >> $LOG
fi
D=$L/poe_claude-sonnet-5.5/vision_ctxfix40/answers
while true; do
  n=$(ls $D 2>/dev/null | wc -l | tr -d ' ')
  [[ $n -ge 40 ]] && { echo "$(date) CLAUDE COMPLETE 40/40 balance $(bal)" >> $LOG; break; }
  b=$(bal); echo "$(date) claude $n/40 balance $b" >> $LOG
  if [[ $b == <-> && $b -ge 1500 ]]; then
    python3 llm_baseline_v30.py --provider poe --model claude-sonnet-5.5 --mode vision --only $IDS --tag ctxfix40 >> $LOG 2>&1
    echo "$(date) claude pass ended, answers $(ls $D | wc -l | tr -d ' ') balance $(bal)" >> $LOG
  fi
  sleep 10800
done
