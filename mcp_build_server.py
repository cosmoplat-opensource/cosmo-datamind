#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DataMind 本体半自动构建 MCP server(DR-058)。

把「本体构建」暴露给任意 MCP 客户端(Claude Code / hermes / 其它 Agent):
  - list_build_sources  读:可构建的数据源(内置库/上传库/已连接库 + 证据资产)
  - list_build_skills   读:内置/自定义构建技能(注入 prompt 的建模方法)
  - list_built_graphs   读:历史构建产物(对象/关系/verified 计数与验收结果)
  - start_build         写(唯一):发起一次半自动构建,立即返回 job id
  - get_build / wait_build  读:跟踪构建作业(SSE 过程事件/汇总/验收结果)
  - get_quality         读:对产物重跑确定性验收(pass/review/fail)
  - review_queue        读:人审队列(candidate/语义存疑等待人处理)

治理边界与动作层 MCP(DR-016)同一哲学,刻意不暴露三类工具:
  1. 没有任何能把关系置为 verified/asserted 的工具 —— verified 只来自服务端
     数据裁决的可回放证据,asserted 只能由人在 DataMind 本体评审页授予;
  2. 没有任何能把指标置为 certified 的工具 —— 口径确认是人的专属入口;
  3. 没有删除图谱的工具 —— 删除在 UI 有确认流,Agent 侧不开放。
外部 Agent 接入即自动继承「只能发起构建与阅读结论,不能改写结论状态」的规范。

传输:MCP stdio(逐行 JSON-RPC 2.0),协议处理与 mcp_action_server 同构。
日志走 stderr,stdout 只出协议消息。
配置示例(Claude Code): claude mcp add datamind-build -- python3 <本文件绝对路径>
环境变量:DATAMIND_URL;未设时由 DATAMIND_HOST/DATAMIND_PORT 组合(缺省 localhost:8092)

构建耗时以分钟计,不能在一次工具调用里同步等完:start_build 返回 job id,
Agent 用 get_build 轮询或 wait_build 阻塞等待(单次至多 120s,可反复调用)。
"""
import json
import os
import re
import sys
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import uuid

_LOG_CTRL = re.compile(r"\r\n|[\r\n\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _log(msg):
    """写一行诊断到 stderr(stdout 是 JSON-RPC 协议通道,不能混日志)。

    先抹掉换行与控制字符再输出:BASE 与工具参数都可能带换行——不净化就能在
    stderr 里拼出伪造日志(CWE-117)或操纵终端显示。与动作层 server 同规。
    """
    print(_LOG_CTRL.sub("␊", str(msg)), file=sys.stderr, flush=True)


def _default_base():
    """默认服务地址由 DATAMIND_HOST/PORT 组合而来,与 server.py 的监听配置同源。
    服务端绑全网卡时客户端仍走回环(0.0.0.0 不是可连接地址)。"""
    host = os.environ.get("DATAMIND_HOST") or "localhost"
    if host in ("0.0.0.0", "::"):
        host = "localhost"
    return "http://%s:%s" % (host, os.environ.get("DATAMIND_PORT") or "8092")


BASE = (os.environ.get("DATAMIND_URL") or _default_base()).rstrip("/")
SUPPORTED_PROTOS = ("2025-06-18", "2025-03-26", "2024-11-05")

# 构建作业上限:单个构建的服务端硬预算(LLM 抽取 640s + 取证/验收)远小于此;
# 包一层总闸是为了 SSE 在异常时不会把作业永久挂在 running。
_JOB_DEADLINE_SECONDS = 1800
_WAIT_CAP_SECONDS = 120            # 单次 wait_build 的阻塞上限,超过让 Agent 再调
_EVENTS_KEPT = 200                 # 每作业留存的过程事件条数(只保留最近,防长跑撑爆内存)
_JOBS_KEPT = 40                    # 作业登记留存上限,超限淘汰最早的已完成作业

_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()

QUALITY_TEXT = {"pass": "通过", "review": "待复核", "fail": "不通过"}

_DECISION_NOTE = ("关系/指标状态只由服务端数据裁决与人工评审产生:"
                  "verified 需可回放数据证据,asserted/certified 只能由人在 DataMind 界面授予,"
                  "本服务器不提供改写状态的工具。")


def _http(method, path, payload=None, timeout=20):
    req = urllib.request.Request(BASE + path, method=method,
                                 headers={"Content-Type": "application/json"},
                                 data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        # 只透出 DataMind 自己给的业务错误;解析不出就只给状态码——
        # 上游返回体可能含栈/路径,不该原样转给 Agent。
        try:
            msg = json.loads(e.read().decode()).get("error")
            return None, (str(msg)[:200] if msg else f"HTTP {e.code}")
        except Exception:
            return None, f"HTTP {e.code}"
    except Exception as e:
        _log("[datamind-build] 请求失败:%r" % (e,))
        return None, "DataMind 服务不可达(%s);请确认服务已启动、DATAMIND_URL 配置正确" % type(e).__name__


def _http_stream(path, payload, on_event, timeout=90):
    """POST SSE 端点,逐事件回调 on_event(dict);返回 (事件数, 错误或 None)。

    DataMind 的构建完成语义要求 SSE 必须出现 done 事件(DR-056);连接中断、
    读超时都按错误返回,由调用方把作业标为失败,不得把半程流当成已完成。
    """
    req = urllib.request.Request(BASE + path, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "text/event-stream"},
                                 data=json.dumps(payload).encode())
    count = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except Exception:
                    continue
                count += 1
                on_event(ev)
                if isinstance(ev, dict) and ev.get("type") in ("done", "error"):
                    return count, None
        return count, None
    except Exception as e:
        _log("[datamind-build] SSE 流中断:%r" % (e,))
        return count, "构建数据流中断(%s)" % type(e).__name__


# ── 构建作业登记 ────────────────────────────────────────────────────────
def _evict_jobs_locked():
    done = [k for k, j in _JOBS.items() if j["status"] != "running"]
    while len(_JOBS) > _JOBS_KEPT and done:
        _JOBS.pop(done.pop(0), None)


def start_job(payload):
    """登记作业并起后台线程消费 SSE;返回 job id。payload 已由调用方校验。"""
    job_id = "jb_" + uuid.uuid4().hex[:8]
    job = {"status": "running", "events": [], "summary": None, "stats": None,
           "quality": None, "graph_key": None, "name": None, "method": None,
           "error": None, "done": False, "started": time.time()}
    with _JOBS_LOCK:
        _JOBS[job_id] = job
        _evict_jobs_locked()
    threading.Thread(target=_run_job, args=(job_id, payload), daemon=True).start()
    return job_id


def _record_event(job, ev):
    if not isinstance(ev, dict):
        return
    kind = ev.get("type")
    if kind == "step":
        job["events"].append("· %s%s" % (ev.get("step", "?"),
                                         (" · " + str(ev.get("info", "")))[:200] if ev.get("info") else ""))
    elif kind == "log":
        job["events"].append("  " + str(ev.get("text", ""))[:300])
    elif kind == "status":
        job["events"].append("  " + str(ev.get("text", ""))[:300])
    elif kind == "error":
        job["error"] = str(ev.get("error", "构建失败"))
    elif kind == "done":
        job["done"] = True
        job["summary"] = ev.get("summary")
        job["stats"] = ev.get("stats")
        job["quality"] = ev.get("quality")
        job["graph_key"] = ev.get("graph_key")
        job["name"] = ev.get("name")
        job["method"] = ev.get("method")
    del job["events"][:-_EVENTS_KEPT]


def _run_job(job_id, payload):
    job = _JOBS.get(job_id)
    if job is None:
        return
    deadline = time.time() + _JOB_DEADLINE_SECONDS

    def on_event(ev):
        if time.time() > deadline:
            job["error"] = "构建超过总时限 %ds,放弃等待(SSE 未完成)" % _JOB_DEADLINE_SECONDS
            return
        _record_event(job, ev)

    _n, err = _http_stream("/api/build/inquire", payload, on_event)
    if err and not job["error"]:
        job["error"] = err
    if job["error"]:
        job["status"] = "error"
    elif job["done"]:
        job["status"] = "done"
    else:
        # DR-056 同规:构建 SSE 必须有 done 才算完成;半程流不得冒充成功。
        job["status"] = "error"
        job["error"] = "构建数据流在完成前结束(未收到 done 事件),产物未确认"
    job["elapsed"] = round(time.time() - job["started"], 1)


def _render_job(job_id):
    job = _JOBS.get(job_id)
    if job is None:
        return "未找到构建作业 %s(作业随 MCP server 进程存活;server 重启后需重新发起构建)" % job_id, True
    if job["status"] == "running":
        tail = job["events"][-5:]
        return ("构建进行中 · 已用时 %ds · 过程事件 %d 条(最近):\n%s\n用 get_build 或 wait_build 继续跟踪。"
                % (int(time.time() - job["started"]), len(job["events"]),
                   "\n".join(tail) if tail else "(尚无事件)")), False
    if job["status"] == "error":
        return ("构建失败:%s\n过程尾部:\n%s"
                % (job["error"], "\n".join(job["events"][-5:]) or "(无事件)")), True
    s = job["stats"] or {}
    q = job["quality"] or {}
    lines = ["构建完成 · 图谱 key=%s 「%s」 · 方法:%s" % (job["graph_key"], job.get("name") or "-", job.get("method") or "-",
            )]
    lines.append("对象 %s · 关系 %s · verified %s · 验收:%s"
                 % (s.get("objects", "-"), s.get("links", "-"), s.get("verified", "-"),
                    QUALITY_TEXT.get(q.get("result"), q.get("result") or "-")))
    if q.get("summary"):
        lines.append("验收摘要:阻断 %s · 待审 %s"
                     % (q["summary"].get("blocking_issues", "-"), q["summary"].get("review_items", "-")))
    if job["summary"]:
        lines.append("说明:" + str(job["summary"])[:600])
    lines.append("关系状态 verified/candidate 由服务端数据裁决产生;" + _DECISION_NOTE)
    lines.append("可用 review_queue 查看待人审项,get_quality 复跑验收。")
    return "\n".join(lines), False


# ── 只读渲染(纯函数,便于单测)─────────────────────────────────────────
def _render_sources(d):
    srcs = d.get("sources") or []
    lines = ["数据源 %d 个 · 证据资产 %d 项" % (len(srcs), len(d.get("assets") or []))]
    for s in srcs:
        lines.append("- id=%s 「%s」 %s · 表 %s · %s"
                     % (s.get("id"), s.get("name"), s.get("kind", "-"),
                        s.get("tables", "-"), "就绪" if s.get("ready") else "不可用"))
    usable = [a for a in (d.get("assets") or []) if a.get("usable")]
    if d.get("assets"):
        lines.append("可作为证据文本注入构建的资产 %d 项:%s"
                     % (len(usable), "、".join(a["name"] for a in usable[:6]) or "(无)"))
    lines.append("start_build 的 source 参数取上面的 id,缺省 uploads(上传库)。")
    return "\n".join(lines), False


def _render_skills(skills):
    if not isinstance(skills, list) or not skills:
        return "暂无构建技能(本仓内置 ontology-semi-auto 应始终可见;若缺失请检查 skills_seed/)", True
    lines = ["构建技能 %d 个(start_build 的 skills 传 name 列表):" % len(skills)]
    for s in skills:
        lines.append("- %s%s: %s"
                     % (s.get("name"), "(内置)" if s.get("builtin") else "(自定义)",
                        str(s.get("desc", ""))[:120]))
    return "\n".join(lines), False


def _render_built(graphs):
    if not isinstance(graphs, list) or not graphs:
        return "尚无构建产物;用 start_build 发起第一次构建。", False
    lines = ["已构建本体 %d 张(按时间倒序):" % len(graphs)]
    for g in graphs:
        lines.append("- %s 「%s」 对象 %s · 关系 %s · verified %s · 验收:%s · %s轮 · %s"
                     % (g.get("key"), g.get("name"), g.get("objects"), g.get("links"),
                        g.get("verified"), QUALITY_TEXT.get(g.get("quality_result"), g.get("quality_result")),
                        g.get("rounds", 0), g.get("ts", "")))
    lines.append("迭代已有本体时,start_build 传 base_graph=<key>;只增补与升级,不删除既有结论。")
    return "\n".join(lines), False


def _render_quality(q):
    result = q.get("result") or q.get("gate")
    summary = q.get("summary") or {}
    lines = ["验收结果:%s" % QUALITY_TEXT.get(result, result or "-"),
             "阻断问题 %s · 待复核 %s · 已核验 verified 关系 %s"
             % (summary.get("blocking_issues", "-"), summary.get("review_items", "-"),
                summary.get("verified_checked", "-"))]
    for item in (q.get("blocking_issues") or [])[:5]:
        lines.append("✗ [%s] %s → %s" % (item.get("type"), item.get("desc"), item.get("fix", ""))[:220])
    for item in (q.get("review_queue") or [])[:5]:
        lines.append("? [%s] %s" % (item.get("type"), str(item.get("desc"))[:160]))
    cq = q.get("cq") or {}
    if cq is not None and not cq.get("provided"):
        lines.append("验收问题:未提供——覆盖率未知,不冒充已验收。")
    lines.append(_DECISION_NOTE)
    return "\n".join(lines), False


def _render_review(d):
    counts = d.get("counts") or {}
    lines = ["图谱 %s · 关系 %d 条:待人审 %d · 已确认 %d · 已否决 %d · 语义争议 %d · 候选对象 %d"
             % (d.get("graph"), counts.get("total", 0), counts.get("pending", 0),
                counts.get("approved", 0), counts.get("rejected", 0),
                counts.get("disputed", 0), counts.get("obj_candidate", 0))]
    pending = [r for r in (d.get("rows") or []) if r.get("pending")]
    for r in pending[:8]:
        lines.append("- %s(%s) —%s→ %s(%s) · status=%s semantic=%s · 重叠 %s"
                     % (r.get("sn"), r.get("s"), r.get("verb"), r.get("tn"), r.get("t"),
                        r.get("status"), r.get("semantic"), r.get("overlap")))
    if len(pending) > 8:
        lines.append("…另有 %d 条待人审(完整清单在 DataMind「本体评审」页)" % (len(pending) - 8))
    lines.append("确认/否决须由人在 DataMind 本体评审页操作;" + _DECISION_NOTE)
    return "\n".join(lines), False


_START_SCHEMA = {
    "type": "object",
    "properties": {
        "q": {"type": "string", "description": "建模诉求(建什么本体、覆盖哪些业务,越具体越好)"},
        "source": {"type": "string", "description": "数据源 id(list_build_sources 里取),缺省 uploads"},
        "name": {"type": "string", "description": "目标本体名称(可选)"},
        "skills": {"type": "array", "items": {"type": "string"},
                   "description": "构建技能 name 列表(可选,缺省不注入技能)"},
        "cqs": {"type": "array", "items": {"type": "string"},
                "description": "验收问题列表(可选;作为构建后的盲测题,不进提议环节)"},
        "stability": {"type": "boolean",
                      "description": "二次独立生成量化一致性(可选;构建耗时约翻倍)"},
        "base_graph": {"type": "string",
                       "description": "在本体上迭代的底本 key(可选,仅限 built_* 产物)"},
        "references": {"type": "object",
                       "description": "行业/本体标准参照配置(可选;不传沿用服务端默认)"},
    },
    "required": ["q"],
    "additionalProperties": False,
}

TOOLS = [
    {"name": "list_build_sources", "title": "列出可构建数据源",
     "description": "列出可用于本体构建的数据源(内置库/上传库/已连接库)与可作为证据文本的资产。发起构建前用它确认 source id 与数据就绪状态。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "list_build_skills", "title": "列出构建技能",
     "description": "列出内置与自定义的本体构建技能(建模方法论,注入构建提议环节)。start_build 的 skills 参数传这里的 name。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "list_built_graphs", "title": "列出构建产物",
     "description": "列出历史构建的本体图谱:对象/关系/verified 计数、确定性验收结果与构建轮次。迭代构建时把 base_graph 设为对应 key。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "start_build", "title": "发起本体构建",
     "description": ("发起一次半自动本体构建并立即返回作业 id。流程:LLM 只提议对象与关系,"
                     "服务端用真实数据裁决(取值重叠/父键唯一/命名证据),确定性验收后落盘;"
                     "LLM 离线时自动回退纯数据驱动。用 get_build/wait_build 跟踪。"
                     "构建耗时通常 2-10 分钟。重复发起会产生新图谱(非幂等)。"),
     "inputSchema": _START_SCHEMA,
     "annotations": {"readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False}},
    {"name": "get_build", "title": "查询构建作业",
     "description": "查询构建作业当前状态:进行中(返回最近过程事件)/完成(返回图谱 key、统计与验收结果)/失败(返回原因)。server 重启后作业登记即失效。",
     "inputSchema": {"type": "object",
                     "properties": {"job_id": {"type": "string", "description": "start_build 返回的作业 id"}},
                     "required": ["job_id"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "wait_build", "title": "等待构建完成",
     "description": "阻塞等待构建作业结束(至多 120 秒)并返回与 get_build 相同的结论;未结束时返回当前进度,可再次调用继续等。",
     "inputSchema": {"type": "object",
                     "properties": {"job_id": {"type": "string", "description": "start_build 返回的作业 id"},
                                    "timeout": {"type": "integer", "description": "本次等待秒数,≤120,缺省 120"}},
                     "required": ["job_id"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "get_quality", "title": "复跑验收检查",
     "description": ("对已有图谱重跑确定性验收:图结构、verified 证据契约、定义质量、验收问题盲测。"
                     "只报告问题,不修改图谱。结果 pass/review/fail。"),
     "inputSchema": {"type": "object",
                     "properties": {"graph": {"type": "string", "description": "图谱 key(list_built_graphs 里取)"}},
                     "required": ["graph"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "review_queue", "title": "查看人审队列",
     "description": "查看图谱的待人工复核项:candidate 关系、语义存疑关系与候选对象。确认/否决只能在 DataMind 本体评审页由人操作,Agent 只读。",
     "inputSchema": {"type": "object",
                     "properties": {"graph": {"type": "string", "description": "图谱 key"}},
                     "required": ["graph"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
]


def _norm_source(value):
    v = str(value or "uploads").strip()
    return v if re.fullmatch(r"[\w\-]{1,80}", v) else None


def call_tool(name, args):
    """→ (text, is_error)"""
    if not isinstance(args, dict):
        return "工具参数必须为 JSON 对象", True
    if name == "list_build_sources":
        d, err = _http("GET", "/api/build/sources")
        if err:
            return err, True
        return _render_sources(d)
    if name == "list_build_skills":
        d, err = _http("GET", "/api/build/skills")
        if err:
            return err, True
        return _render_skills(d)
    if name == "list_built_graphs":
        d, err = _http("GET", "/api/build/built")
        if err:
            return err, True
        return _render_built(d)
    if name == "start_build":
        q = str(args.get("q") or "").strip()
        if not q:
            return "缺少建模诉求 q:请描述要建的本体覆盖哪些业务", True
        source = _norm_source(args.get("source"))
        if args.get("source") is not None and source is None:
            return "source 含非法字符(只允许字母数字下划线连字符)", True
        payload = {"q": q[:2000], "source": source or "uploads"}
        if args.get("name"):
            payload["name"] = str(args["name"])[:60]
        if isinstance(args.get("skills"), list):
            payload["skills"] = [str(x) for x in args["skills"][:10] if str(x).strip()]
        if isinstance(args.get("cqs"), list):
            payload["cqs"] = [str(x)[:500] for x in args["cqs"][:30] if str(x).strip()]
        if isinstance(args.get("stability"), bool):
            payload["stability"] = args["stability"]
        if args.get("base_graph"):
            payload["base_graph"] = str(args["base_graph"])
        if isinstance(args.get("references"), dict):
            payload["references"] = args["references"]
        job_id = start_job(payload)
        return ("构建作业已发起:job_id=%s(source=%s)。构建耗时通常 2-10 分钟,"
                "用 wait_build(可重复调用,单次至多 %d 秒)或 get_build 跟踪。"
                % (job_id, payload["source"], _WAIT_CAP_SECONDS)), False
    if name == "get_build":
        return _render_job(str(args.get("job_id") or "").strip())
    if name == "wait_build":
        job_id = str(args.get("job_id") or "").strip()
        try:
            wait = min(max(1, int(args.get("timeout") or _WAIT_CAP_SECONDS)), _WAIT_CAP_SECONDS)
        except (TypeError, ValueError):
            wait = _WAIT_CAP_SECONDS
        deadline = time.time() + wait
        while time.time() < deadline:
            job = _JOBS.get(job_id)
            if job is not None and job["status"] != "running":
                break
            time.sleep(0.5)
        return _render_job(job_id)
    if name == "get_quality":
        g = str(args.get("graph") or "").strip()
        d, err = _http("POST", "/api/build/quality", {"graph": g})
        if err:
            return err, True
        return _render_quality(d)
    if name == "review_queue":
        g = str(args.get("graph") or "").strip()
        d, err = _http("GET", "/api/ont/review?graph=" + urllib.parse.quote(g))
        if err:
            return err, True
        return _render_review(d)
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
            continue
        mid, method = msg.get("id"), msg.get("method", "")
        params = msg.get("params", {})

        def reply(result=None, error=None, mid=mid):   # mid 默认参数绑定当轮消息 id
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
            want = params.get("protocolVersion")
            reply({"protocolVersion": want if want in SUPPORTED_PROTOS else SUPPORTED_PROTOS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "datamind-build", "title": "DataMind 本体构建",
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
                _log("[datamind-build] 工具 %s 执行异常:%r" % (name, e))
                text, is_err = "工具执行异常(%s),详见服务端日志" % type(e).__name__, True
            reply({"content": [{"type": "text", "text": text}], "isError": bool(is_err)})
        else:
            reply(error={"code": -32601, "message": f"method not found: {method}"})


if __name__ == "__main__":
    _log("datamind-build MCP server 启动 · BASE=" + BASE)
    main()
