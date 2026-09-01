#!/usr/bin/env bash
# COSMO DataMind 启动器
cd "$(dirname "$0")"
mkdir -p workdir

# 引擎运行时选择(可用 CLAW_DRIVER=hermes ./start.sh 覆盖)
: "${CLAW_DRIVER:=claude-code}"; export CLAW_DRIVER
: "${DATAMIND_PORT:=8092}"; export DATAMIND_PORT

# 按端口停旧实例(lsof 缺失或端口空闲时不误触 kill 用法错误)
if command -v lsof >/dev/null 2>&1; then
  PIDS=$(lsof -ti :"$DATAMIND_PORT" 2>/dev/null)
  if [ -n "$PIDS" ]; then
    kill $PIDS 2>/dev/null; sleep 1
    PIDS=$(lsof -ti :"$DATAMIND_PORT" 2>/dev/null)
    [ -n "$PIDS" ] && kill -9 $PIDS 2>/dev/null; sleep 1
  fi
else
  echo "提示: 未找到 lsof,跳过旧实例清理(如端口被占请手动处理)"
fi
nohup python3 server.py > workdir/server.log 2>&1 &

echo "COSMO DataMind → http://127.0.0.1:$DATAMIND_PORT (引擎: $CLAW_DRIVER)"
