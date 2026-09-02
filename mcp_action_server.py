#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DataMind 动作层 MCP server(DR-016)。

把动作中心暴露给任意 MCP 客户端(Claude Code / hermes / 其它 Agent):
  - list_actions       读:动作类型目录(参数 schema / 风险级)
  - invoke_action      写(唯一):发起动作 —— 低风险直执行,高风险进人审批队列
  - get_action_status  读:跟踪某次动作(是否已被人批准/驳回、效果)

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
PROTO = "2024-11-05"

TOOLS = [
    {
        "name": "list_actions",
        "title": "列出动作类型",
        "description": "列出本体动作类型目录:每个动作的 id、名称、作用对象、风险级(low=直执行/high=须人审批)、参数 schema 与效果说明。发起动作前先调用它拿到准确的参数名。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "invoke_action",
        "title": "发起动作",
        "description": ("发起一个本体动作 —— 这是本服务器唯一的写路径。低风险动作立即执行并写入审计日志;"
                        "高风险动作只会进入人工审批队列(pending),必须由人在 DataMind 动作中心批准后才生效,"
                        "Agent 无法绕过。operator 必填(发起人姓名或工号,写入审计)。"),
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
    },
    {
        "name": "get_action_status",
        "title": "查询动作状态",
        "description": "查询某次动作的当前状态:pending(待人批)/ executed(已执行,含效果)/ denied(被驳回,含审批意见)。发起高风险动作后可用它跟踪人审结果。",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "invoke_action 返回的动作记录 id"}},
            "required": ["id"],
            "additionalProperties": False,
        },
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
    if name == "list_actions":
        d, err = _http("GET", "/api/actions")
        if err: return err, True
        out = [f"动作类型 {len(d.get('types', []))} 个 · 待人批 {d.get('pending', 0)} · 已执行 {d.get('executed', 0)}", ""]
        for t in d.get("types", []):
            ps = "; ".join(f"{p['name']}({p['cn']}{',必填' if p.get('required') else ''}"
                           + (f",可选值:{'/'.join(p['options'])}" if p.get("options") else "") + ")"
                           for p in t.get("params", []))
            out.append(f"- id={t['id']} 「{t['cn']}」 对象:{t.get('object','-')} 风险:{t.get('risk')}"
                       f"({'高风险,须人审批' if t.get('risk')=='high' else '低风险,直执行'})\n  参数:{ps}\n  说明:{t.get('desc','')}")
        return "\n".join(out), False
    if name == "invoke_action":
        payload = {"action_id": args.get("action_id"), "params": args.get("params") or {},
                   "operator": args.get("operator")}
        d, err = _http("POST", "/api/action/invoke", payload)
        if err: return f"发起失败:{err}", True
        if d.get("status") == "pending":
            return (f"已提交,记录 id={d['id']},状态=pending:该动作为高风险,已进入人工审批队列,"
                    f"必须由人在 DataMind 动作中心批准后才会执行。可用 get_action_status 跟踪。"), False
        return f"已执行并写入审计日志,记录 id={d['id']},状态=executed。", False
    if name == "get_action_status":
        d, err = _http("GET", "/api/action/log")
        if err: return err, True
        it = next((x for x in d.get("items", []) if x.get("id") == args.get("id")), None)
        if not it: return f"未找到动作记录 {args.get('id')}", True
        lines = [f"动作「{it['action_cn']}」 状态:{it['status']} 发起人:{it['operator']} 时间:{it['ts']}"]
        if it.get("approver"):
            lines.append(f"审批人:{it['approver']}" + (f" 意见:{it['approve_comment']}" if it.get("approve_comment") else ""))
        if it.get("effects"):
            lines.append("效果:" + " / ".join(it["effects"]))
        if it["status"] == "pending":
            lines.append("仍在等待人工审批(Agent 无法批准,请等待或提醒审批人)。")
        return "\n".join(lines), False
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
        params = msg.get("params") or {}

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

        if method == "initialize":
            # MCP 规范:initialize 应答**服务器支持的**协议版本,而非回显客户端版本。
            # 回显 = 声称支持任意版本;客户端若按更新语义调用,行为未定义。
            reply({"protocolVersion": PROTO,
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": "datamind-actions", "version": "0.1.0"}})
        elif method in ("notifications/initialized", "initialized"):
            pass
        elif method == "ping":
            reply({})
        elif method == "tools/list":
            reply({"tools": TOOLS})
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
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
