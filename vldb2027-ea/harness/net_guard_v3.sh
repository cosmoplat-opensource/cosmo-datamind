#!/bin/bash
# 网络守卫:DeepSeek 不可达时暂停(SIGSTOP)队列与补跑进程,恢复后继续(SIGCONT);记录到 runs/v3/net_guard.log
cd "$(dirname "$0")"
KEY=$(grep -E '^DEEPSEEK_API_KEY=' ../cosmo-datamind/.env | cut -d= -f2)
paused=0; okcount=0
while true; do
  code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' https://api.deepseek.com/chat/completions -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{"model":"deepseek-flash","messages":[{"role":"user","content":"ok"}],"max_tokens":1,"thinking":{"type":"disabled"}}')
  pids=$(pgrep -f "queue_v2.py run|queue_v3.py run|queue_v4.py run|repair_v2.py --run|bird_datamind.py qa|bird_datamind.py with|run_evaluation.py --config configs/deepseek_" | tr "\n" " ")   # 含正在跑的单元子进程,断网时一并暂停
  if [ "$code" != "200" ]; then
    okcount=0
    if [ $paused -eq 0 ] && [ -n "$pids" ]; then kill -STOP $pids 2>/dev/null; paused=1; echo "$(date '+%m-%d %H:%M:%S') PAUSE code=$code pids=$pids" >> runs/v3/net_guard.log; fi
  else
    okcount=$((okcount+1))
    if [ $paused -eq 1 ] && [ $okcount -ge 2 ]; then kill -CONT $pids 2>/dev/null; paused=0; echo "$(date '+%m-%d %H:%M:%S') RESUME pids=$pids" >> runs/v3/net_guard.log; fi
  fi
  sleep 45
done
