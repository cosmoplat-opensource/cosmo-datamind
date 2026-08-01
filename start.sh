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

# 若配置了上游引擎目录,顺带拉起经典工作台(serve_claw:8091,SPARQL/对话编辑/锻造/版本库)
ENGINE_DIR="${DATAMIND_ENGINE_DIR:-../ontology-engine}"
if [ -f "$ENGINE_DIR/engine/serve_claw.py" ]; then
  lsof -ti :8091 >/dev/null 2>&1 || nohup python3 "$ENGINE_DIR/engine/serve_claw.py" --port 8091 > workdir/classic.log 2>&1 &
  echo "Cosmo DataMind → http://127.0.0.1:$DATAMIND_PORT (引擎: $CLAW_DRIVER)  |  经典工作台 → http://127.0.0.1:8091"
else
  echo "Cosmo DataMind → http://127.0.0.1:$DATAMIND_PORT (独立运行;设 DATAMIND_ENGINE_DIR 可启用引擎能力)"
fi
