#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DataMind 动作层 MCP server(DR-016)。

把动作中心暴露给任意 MCP 客户端(Claude Code / hermes / 其它 Agent):
  - list_actions       读:动作类型目录(参数 schema / 风险级)
  - invoke_action      写(唯一):保存决策记录 —— 低风险直接登记,高风险进入人工审批队列
  - get_action_status  读:跟踪动作记录及人工审批结果

当前接口采用 decision_capture 模式,不写回业务系统。executed 是兼容历史的
记录状态值,表示决策已登记,不表示设备动作或业务修改已执行。

治理语义全部保留在 DataMind 服务端(单一实现):参数校验、风险分级、审批队列、
决策捕获审计。审批(approve/deny)**故意不暴露**为工具 —— 批准是人的专属入口
(动作中心页面),外部 Agent 接入即自动继承"只能提议改变,不能批准改变"的规范。

传输:MCP stdio(逐行 JSON-RPC 2.0)。日志走 stderr,stdout 只出协议消息。
配置示例(Claude Code): claude mcp add datamind-actions -- python3 <本文件绝对路径>
环境变量:DATAMIND_URL;未设时由 DATAMIND_HOST/DATAMIND_PORT 组合(缺省 localhost:8092)
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error

_LOG_CTRL = re.compile(r"\r\n|[\r\n\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

def _log(msg):
    """写一行诊断到 stderr(stdout 是 JSON-RPC 协议通道,不能混日志)。

    先抹掉换行与控制字符再输出:BASE 来自 DATAMIND_URL、工具名来自 MCP 客户端,
    两者都可能带换行 —— 不净化就能在 stderr 里拼出一条伪造的日志记录(CWE-117),
    ESC 序列还能操纵运维的终端显示。
    """
    print(_LOG_CTRL.sub("␊", str(msg)), file=sys.stderr, flush=True)


def _default_base():
    """默认服务地址由 DATAMIND_HOST/PORT 组合而来,与 server.py 的监听配置同源。
    不再写死 127.0.0.1:8092 —— 那份字面量与 server 的实际监听值会各自漂移。
    仍以回环为缺省(本服务无鉴权,默认不跨机)。"""
    host = os.environ.get("DATAMIND_HOST") or "localhost"
    if host in ("0.0.0.0", "::"):        # 服务端绑全网卡时,客户端仍走回环访问本机
        host = "localhost"
    return "http://%s:%s" % (host, os.environ.get("DATAMIND_PORT") or "8092")

BASE = (os.environ.get("DATAMIND_URL") or _default_base()).rstrip("/")
# 支持的 MCP 协议版本,新在前。本服务只用 tools 能力,三个版本间该子集语义一致:
# 2025-03-26 增加的 annotations 与 2025-06-18 增加的工具级 title 都是纯增量字段,
# 旧客户端按规范忽略未知字段即可。协商规则见 initialize 分支。
SUPPORTED_PROTOS = ("2025-06-18", "2025-03-26", "2024-11-05")
_DECISION_CAPTURE_NOTE = "当前接口仅保存决策记录，未写回业务系统（decision_capture，real_writeback=False）。"

# annotations(MCP 2025-03-26 起):向客户端声明行为提示,便于其做审批分流与
# 并发调度。按规范这些只是 hint、不构成安全边界——真正的治理(风险分级/人审
# 队列)仍在 DataMind 服务端强制执行。
TOOLS = [
    {
        "name": "list_actions",
        "title": "列出动作类型",
        "description": "列出本体动作类型目录:每个动作的 id、名称、作用对象、风险级(low=直接登记/high=须人审批)、参数 schema 与记录内容说明。当前只保存决策记录,不写回业务系统;executed 表示已登记。发起动作前先查询准确的参数名。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "invoke_action",
        "title": "发起动作",
        "description": ("发起动作并保存决策记录,这是本服务器唯一的写路径。低风险直接登记;"
                        "高风险进入人工审批队列(pending),由人在 DataMind 动作中心批准或驳回,"
                        "Agent 无法审批。当前不写回业务系统,批准仅形成决策记录。"
                        "operator 必填(发起人姓名或工号,写入审计)。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_id": {"type": "string", "description": "动作类型 id(见 list_actions)"},
                "params": {"type": "object", "description": "动作参数,键名须与该动作的参数 schema 一致"},
                "operator": {"type": "string", "description": "发起人(姓名或工号,必填,入审计)"},
            },
            "required": ["action_id", "params", "operator"],
            "additionalProperties": False,
        },
        # 只新增记录/入队,不改写不删除,故非破坏性;重复发起会产生两条动作记录,故非幂等
        "annotations": {"readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "get_action_status",
        "title": "查询动作状态",
        "description": "查询动作决策记录:pending(待人批)/ executed(已登记)/ denied(被驳回),并返回审批意见及记录内容。当前不写回业务系统;executed 不表示业务修改或设备动作已经执行。",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "invoke_action 返回的动作记录 id"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    # ── 只读语义工具(DR-055):检索概念画像、查看指标口径、按 certified 口径取数;不新增任何写路径 ──
    {
        "name": "search_concept",
        "title": "检索业务概念",
        "description": "按业务词检索本体概念画像:返回命中的对象(中文名/表/定义/属性/沿已验证关系可达的邻居/绑定指标)。先用它锚定问题涉及的对象,再决定查哪个指标。",
        "inputSchema": {"type": "object",
                        "properties": {"q": {"type": "string", "description": "业务词或问题片段"},
                                       "graph": {"type": "string", "description": "图谱键,缺省为示例本体"}},
                        "required": ["q"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "describe_metric",
        "title": "查看指标口径",
        "description": "返回指标的契约口径(聚合/过滤/时间列/可用维度/状态/编译 SQL)。状态含义:certified=业务已确认;verified=执行核验与参照一致;candidate=未核验,不得据此作答。",
        "inputSchema": {"type": "object",
                        "properties": {"name": {"type": "string"}, "graph": {"type": "string"}},
                        "required": ["name"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "query_metric",
        "title": "按口径取数",
        "description": "按指标契约确定性编译 SQL 并只读执行(按时间粒度分桶)。只接受 certified 指标;verified/candidate 会被拒绝并说明原因——对外取数只走业务确认过的口径。",
        "inputSchema": {"type": "object",
                        "properties": {"name": {"type": "string"}, "graph": {"type": "string"}},
                        "required": ["name"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]


def _http(method, path, payload=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 headers={"Content-Type": "application/json"},
                                 data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        # 只透出 DataMind 自己给的业务错误(它已按端点约定裁剪过);
        # 解析不出就只给状态码 —— 上游返回体可能含栈/路径,不该原样转给 Agent。
        try:
            msg = json.loads(e.read().decode()).get("error")
            return None, (str(msg)[:200] if msg else f"HTTP {e.code}")
        except Exception:
            return None, f"HTTP {e.code}"
    except Exception as e:
        # 只报异常类型:异常消息里常含主机名/端口/证书路径等部署细节;
        # 完整现场进 stderr(运维可见),不进协议通道(Agent/模型可见)。
        _log("[datamind-actions] 请求失败:%r" % (e,))
        return None, "DataMind 服务不可达(%s);请确认服务已启动、DATAMIND_URL 配置正确" % type(e).__name__


def call_tool(name, args):
    """→ (text, is_error)"""
    if not isinstance(args, dict):
        return "工具参数必须为 JSON 对象", True
    if name == "list_actions":
        d, err = _http("GET", "/api/actions")
        if err: return err, True
        out = [f"动作类型 {len(d.get('types', []))} 个 · 待人批 {d.get('pending', 0)} · 已登记 {d.get('executed', 0)}",
               _DECISION_CAPTURE_NOTE, ""]
        for t in d.get("types", []):
            ps = "; ".join(f"{p['name']}({p['cn']}{',必填' if p.get('required') else ''}"
                           + (f",可选值:{'/'.join(p['options'])}" if p.get("options") else "") + ")"
                           for p in t.get("params", []))
            out.append(f"- id={t['id']} 「{t['cn']}」 对象:{t.get('object','-')} 风险:{t.get('risk')}"
                       f"({'高风险,须人审批' if t.get('risk')=='high' else '低风险,直接登记'})\n  参数:{ps}\n  说明:{t.get('desc','')}")
        return "\n".join(out), False
    if name == "invoke_action":
        params = args.get("params", {})
        if not isinstance(params, dict):
            return "动作 params 必须为 JSON 对象", True
        payload = {"action_id": args.get("action_id"), "params": params,
                   "operator": args.get("operator")}
        d, err = _http("POST", "/api/action/invoke", payload)
        if err: return f"发起失败:{err}", True
        if d.get("status") == "pending":
            return (f"已提交,记录 id={d['id']},状态=pending:该动作为高风险,已进入人工审批队列,"
                    f"须由人在 DataMind 动作中心批准或驳回。可用 get_action_status 跟踪。"
                    + _DECISION_CAPTURE_NOTE), False
        return (f"已保存动作决策记录并写入审计日志,记录 id={d['id']},状态={d.get('status', 'unknown')}。"
                + _DECISION_CAPTURE_NOTE), False
    if name == "get_action_status":
        d, err = _http("GET", "/api/action/log")
        if err: return err, True
        it = next((x for x in d.get("items", []) if x.get("id") == args.get("id")), None)
        if not it: return f"未找到动作记录 {args.get('id')}", True
        lines = [f"动作「{it['action_cn']}」 状态:{it['status']} 发起人:{it['operator']} 时间:{it['ts']}",
                 _DECISION_CAPTURE_NOTE]
        if it.get("approver"):
            lines.append(f"审批人:{it['approver']}" + (f" 意见:{it['approve_comment']}" if it.get("approve_comment") else ""))
        if it.get("effects"):
            lines.append("记录内容:" + " / ".join(it["effects"]))
        if it["status"] == "pending":
            lines.append("仍在等待人工审批(Agent 无法批准,请等待或提醒审批人)。")
        return "\n".join(lines), False
    if name == "search_concept":
        from urllib.parse import quote
        g = quote(str(args.get("graph") or "demo"))
        d, err = _http("GET", f"/api/ont/profile?graph={g}&q={quote(str(args.get('q') or ''))}")
        if err: return err, True
        if not d.get("hits"): return "未命中任何概念;换一个业务词或先列出图谱对象。", False
        return "\n\n".join(d.get("rendered") or []), False
    if name in ("describe_metric", "query_metric"):
        from urllib.parse import quote
        g, n = quote(str(args.get("graph") or "demo")), quote(str(args.get("name") or ""))
        d, err = _http("GET", f"/api/metric/contract?graph={g}&name={n}")
        if err: return err, True
        m = d.get("metric") or {}
        st = m.get("status") or "candidate"
        if name == "describe_metric":
            lines = [f"指标「{m.get('name')}」 状态:{st} 分层:{d.get('layer')} 绑定表:{m.get('table') or '-'}"]
            if d.get("caliber"): lines.append("口径:" + d["caliber"])
            if d.get("compiled_sql"): lines.append("SQL:" + d["compiled_sql"])
            ad = d.get("allowed_dimensions") or {}
            if ad: lines.append("可下钻:" + "、".join(ad.get("columns") or []) + " | 关联对象:" + "、".join(o["object"] for o in ad.get("objects") or []))
            if st != "certified": lines.append("提示:该口径尚未经业务确认(certified),对外作答请注明。")
            return "\n".join(lines), False
        if st != "certified":
            return f"拒绝取数:指标「{m.get('name')}」状态为 {st},只有 certified 口径可对外取数;请先在 DataMind 由业务确认口径。", True
        d2, err = _http("GET", f"/api/metric/quick?graph={g}&name={n}")
        if err: return err, True
        rows = d2.get("data", {}).get("rows") or []
        head = [f"指标「{d2.get('metric')}」 单位:{d2.get('unit') or '-'} 口径:{d2.get('agg_note')} SQL:{d2.get('sql')}"]
        return "\n".join(head + [json.dumps(r, ensure_ascii=False) for r in rows[:60]]), False
    return f"未知工具:{name}", True


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue                       # 解析失败取不到 id,JSON-RPC 规定无从应答
        if not isinstance(msg, dict):
            continue                       # 数组/标量不是请求对象;一行畸形输入不该终止整个服务
        mid, method = msg.get("id"), msg.get("method", "")
        params = msg.get("params", {})

        def reply(result=None, error=None, mid=mid):   # mid 默认参数绑定当轮消息 id,不随循环推进漂移
            if mid is None:  # notification,不回
                return
            out = {"jsonrpc": "2.0", "id": mid}
            if error is not None:
                out["error"] = error
            else:
                out["result"] = result
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        if not isinstance(params, dict):
            reply(error={"code": -32602, "message": "params must be an object"})
            continue

        if method == "initialize":
            # MCP 版本协商:客户端请求的版本若在支持列表内则沿用它,否则回自己
            # 最新支持的版本,由客户端决定是否接受(规范§Lifecycle)。绝不回显未知
            # 版本——那等于声称支持任意版本,客户端按更新语义调用即行为未定义。
            want = params.get("protocolVersion")
            reply({"protocolVersion": want if want in SUPPORTED_PROTOS else SUPPORTED_PROTOS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "datamind-actions", "title": "DataMind 动作层",
                                  "version": "0.1.0"}})
        elif method in ("notifications/initialized", "initialized"):
            pass
        elif method == "ping":
            reply({})
        elif method == "tools/list":
            reply({"tools": TOOLS})
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {})
            if not isinstance(args, dict):
                reply(error={"code": -32602, "message": "arguments must be an object"})
                continue
            try:
                text, is_err = call_tool(name, args)
            except Exception as e:
                # 同上:异常类型足以让调用方判断该重试还是该报人工,细节留在 stderr
                _log("[datamind-actions] 工具 %s 执行异常:%r" % (name, e))
                text, is_err = "工具执行异常(%s),详见服务端日志" % type(e).__name__, True
            reply({"content": [{"type": "text", "text": text}], "isError": bool(is_err)})
        else:
            reply(error={"code": -32601, "message": f"method not found: {method}"})


if __name__ == "__main__":
    _log("datamind-actions MCP server 启动 · BASE=" + BASE)
    main()
