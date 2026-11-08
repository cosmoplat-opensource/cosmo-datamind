#!/usr/bin/env python3
"""本地对比实验补充:把 DataMind 的本体约束校验作为 MCP 工具提供给 ReAct 智能体(条件 E1)。

工具 check_sql(sql):按 DataMind 构建的本体(数据裁决的已验证关系、角色键、有效行与租户要求)
校验 SQL 的表与 JOIN 键,返回是否通过、原因,以及所用表之间可用的已验证连接。
只读;不执行 SQL。本体来自 08_对比实验/runs/datamind(graphs.json 指定每个库的图谱)。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import mcp.types as types
from mcp.server import stdio
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

EXP = Path(os.environ.get("DM_EXP_DIR", "."))
DM_ROOT = EXP.parent / "cosmo-datamind"


def _load_gate(db_id: str, db_path: str):
    os.environ["DATAMIND_WORKDIR"] = str(EXP / "runs" / "datamind" / "workdir")
    os.environ["DATAMIND_DB"] = db_path
    for k in [k for k in os.environ if k.startswith("DATAMIND_LLM_")]:
        os.environ.pop(k, None)                       # 校验不调用模型
    sys.path.insert(0, str(DM_ROOT))
    import server as dm                               # noqa: E402  DataMind 服务模块(只用其校验函数)
    graphs = json.loads((EXP / "runs" / "datamind" / os.environ.get("DM_GRAPHS", "graphs.json")).read_text())
    ir = dm.load_ir(graphs[db_id]["graph"])
    return dm, ir


class GateServer:
    def __init__(self, db_id: str, db_path: str):
        self.dm, self.ir = _load_gate(db_id, db_path)
        self.server = Server("datamind-gate-mcp")

        @self.server.list_tools()
        async def list_tools() -> list[types.Tool]:
            return [types.Tool(
                name="check_sql",
                description=("Check a draft SQL against the data-verified ontology before you FINISH: "
                             "tables must exist, and every JOIN key must be a relation verified on the data "
                             "(value overlap and unique parent key). Returns ok/reason and the verified join keys "
                             "among the tables you used. Call it on your final SQL; if it fails, fix the joins."),
                inputSchema={"type": "object", "properties": {"sql": {"type": "string"}},
                             "required": ["sql"], "additionalProperties": False})]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
            sql = str((arguments or {}).get("sql") or "")
            ok, why = self.dm._validate_sql_ontology(sql, self.ir, strict=False)
            stripped = re.sub(r"'(?:[^']|'')*'", "''", sql)
            tables = [m.group(1) for m in re.finditer(r"\b(?:from|join)\s+[`\"]?(\w+)", stripped, re.I)]
            hints = self.dm._join_hints(self.ir, tables)
            out = {"ok": bool(ok), "reason": why or "passes the ontology constraints",
                   "verified_joins_among_used_tables": hints[:12]}
            return [types.TextContent(type="text", text=json.dumps(out, ensure_ascii=False))]

    async def run(self):
        async with stdio.stdio_server() as (r, w):
            await self.server.run(r, w, InitializationOptions(
                server_name="datamind-gate-mcp", server_version="0.1",
                capabilities=self.server.get_capabilities(NotificationOptions(), {})))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-id", required=True)
    ap.add_argument("--db-path", required=True)
    a = ap.parse_args()
    asyncio.run(GateServer(a.db_id, a.db_path).run())
