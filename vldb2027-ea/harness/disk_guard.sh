#!/bin/bash
# Pause (SIGSTOP) the experiment runners and their agents when free disk space falls below 700 MB; resume above 1.2 GB.
cd "$(dirname "$0")"
paused=0
while true; do
  free=$(df -k . | tail -1 | awk '{print $4}')
  pids=$(pgrep -f "queue_v4.py run|run_evaluation.py --config configs/deepseek_|bird_datamind.py with")
  if [ "$free" -lt 716800 ] && [ $paused -eq 0 ] && [ -n "$pids" ]; then
    kill -STOP $pids 2>/dev/null; paused=1; echo "$(date '+%m-%d %H:%M') PAUSE free=${free}KB" >> runs/v4/disk_guard.log
  elif [ "$free" -gt 1258291 ] && [ $paused -eq 1 ]; then
    kill -CONT $pids 2>/dev/null; paused=0; echo "$(date '+%m-%d %H:%M') RESUME free=${free}KB" >> runs/v4/disk_guard.log
  fi
  sleep 30
done
