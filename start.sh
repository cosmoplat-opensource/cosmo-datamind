#!/usr/bin/env bash
# Cosmo DataMind 启动器
cd "$(dirname "$0")"
mkdir -p workdir
[ -f workdir/demo_ir.json ] || cp /tmp/demo_ir.json workdir/ 2>/dev/null
# 引擎默认 claude-code(hermes 走的 Codex/ChatGPT 订阅易 429);覆盖:CLAW_DRIVER=hermes ./start.sh
: "${CLAW_DRIVER:=claude-code}"; export CLAW_DRIVER
# 按端口停旧实例(进程实际命令行为 `python3 server.py`,pkill -f "cosmo-datamind/server.py" 匹配不到)
kill $(lsof -ti :8092) 2>/dev/null; sleep 1; kill -9 $(lsof -ti :8092) 2>/dev/null; sleep 1
nohup python3 server.py > workdir/server.log 2>&1 &
# 自动带起经典工作台(serve_claw:8091,SPARQL/对话编辑/锻造/版本库)
lsof -ti :8091 >/dev/null 2>&1 || nohup python3 ../上游本体引擎/engine/serve_claw.py --port 8091 > workdir/classic.log 2>&1 &
echo "Cosmo DataMind → http://127.0.0.1:8092 (引擎: $CLAW_DRIVER)  |  经典工作台 → http://127.0.0.1:8091"
