#!/usr/bin/env bash
# Cosmo DataMind 启动器
cd "$(dirname "$0")"
mkdir -p workdir

# 引擎运行时选择(可用 CLAW_DRIVER=hermes ./start.sh 覆盖)
: "${CLAW_DRIVER:=claude-code}"; export CLAW_DRIVER
: "${DATAMIND_PORT:=8092}"; export DATAMIND_PORT

# 按端口停旧实例
kill $(lsof -ti :"$DATAMIND_PORT") 2>/dev/null; sleep 1
kill -9 $(lsof -ti :"$DATAMIND_PORT") 2>/dev/null; sleep 1
nohup python3 server.py > workdir/server.log 2>&1 &

echo "Cosmo DataMind → http://127.0.0.1:$DATAMIND_PORT (引擎: $CLAW_DRIVER)"
