#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
COSMO DataMind · 数据智脑 — 数据治理、本体与深度问数原型
整合:DATAMIND_DB 指定的只读数据源 + 可选上游本体引擎(引擎/技能/IR) + outputs(成果库)
深度问数:hermes/claude-code(经 agent_runtime)生成 SQL 计划 → 本地 SQLite 执行 → 洞察;引擎不可用时走内置模板兜底。
启动:python3 server.py  → http://127.0.0.1:8092
"""
import json, os, re, sqlite3, subprocess, threading, time, uuid, sys, glob
from itertools import combinations
import logging
import urllib.request, urllib.error
from typing import Any
import dao_core   # DR-035/044:命名校验/词根等裁决原语的单一事实源
import build_quality
import build_references
import action_ontology
import ir_shape
import skill_registry
import ontology_grounding
import content_quality

# ── 运维日志(结构化、可分级、可重定向)──────────────────────────────
# 诊断信息一律走 logging 而非 print:print 混在 stdout 里既无级别也无时间戳,
# 且容易把内部路径/异常细节直接摊到用户可见的输出上。此处只对运维可见,
# 面向 HTTP 调用方的错误另行裁剪(见各端点的 str(e)[:N])。
# .env 里留空的变量 source 后是空串而非未设置,get(name, default) 会返回 ""——
# 空串传给 basicConfig 会抛 ValueError,服务直接起不来。故一律用 or 兜默认值。
logging.basicConfig(level=(os.environ.get("DATAMIND_LOG_LEVEL") or "INFO").upper(),
                    format="%(asctime)s %(levelname)s [datamind] %(message)s")

class _LogSanitizer(logging.Filter):
    """日志净化(CWE-117 防日志伪造):抹掉最终消息里的换行与其它控制字符。

    日志里一旦混进换行,一条记录就能被拆成两条 —— 攻击者可以拼出一行以假乱真的
    「INFO … 操作成功」把审计线索搅浑;ESC 序列还能在终端里改色、清屏、挪光标。
    本服务会把环境变量(DATAMIND_HOST)、文件路径、异常类型名等写进日志,这些都可能
    带换行,故统一净化。

    装在 **handler** 上而不是逐个调用点净化:调用点会不断新增,过滤器不会漏;
    子 logger(datamind.store 等)的记录传播到 root handler 时同样被覆盖。
    只处理消息体,不动 exc_info —— 回溯本就是多行的,那是它该有的样子。
    """
    _CTRL = re.compile(r"\r\n|[\r\n\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:                      # 参数与格式串不匹配:交给 handler 原样报错
            return True
        clean = self._CTRL.sub("␊", msg)       # 用可见符号替代,既断开伪造又不悄悄吞字
        if clean != msg:
            record.msg, record.args = clean, ()
        return True

for _h in logging.getLogger().handlers:        # basicConfig 建的 root handler
    _h.addFilter(_LogSanitizer())
_LOG = logging.getLogger("datamind")
# DR-043 蓝图化前置:基础路径与原语(路径/只读连接/只读SQL判定/写锁/原子写)收敛到共享上下文,与后续 blueprint 共用
from srv_context import (HERE, ROOT, DB, UPLOAD_DB, WORK, confine,
                         ro_connect, sql_is_readonly, _WRITE_LOCK, _atomic_json, _atomic_text,
                         _atomic_bytes)
# 引擎运行时与配置层(跨簇共享,故先于路由抽出;见 srv_engine 模块头)
# engine 路由迁出后,server 仅用这几项:运行时选择、引擎回复语义、启动自举与按任务选模
from srv_engine import (runtime_cached, _drv_order, _looks_like_error,
                        _load_engine_cfg, _apply_engine_cfg)
from srv_actions import ACTION_TYPES_F, load_action_types as _load_ats
from flask import Flask, jsonify, request, send_from_directory, send_file

# ── 可配置路径(env 覆盖):HERE/ROOT/DB/UPLOAD_DB/WORK 已收敛到 srv_context(见文件头 import)。
#   PLATFORM/OUTPUTS 与引擎 sys.path 自举与装配耦合,留在此处。
#   DATAMIND_ENGINE_DIR 上游本体引擎目录(可选;缺失则 LLM 构建降级为纯数据驱动)
#   DATAMIND_OUTPUTS_DIR 成果库目录(可选);DATAMIND_HOST/PORT 监听地址与端口
def _env_dir(var, default):
    """取目录型环境变量,归一化成一个**确定的**绝对路径。

    这两个变量决定「从哪读引擎/成果」,并会被拼进 sys.path 与各处文件路径,属于配置面。
    相对值(.env.example 里就是 `../ontology-engine`)一律按**程序所在目录**解析,
    而不是按进程 cwd —— 否则同一份配置在不同启动目录指向不同地方,既是运维陷阱,
    也让"用 cwd 把它挪到一个受控目录"成为一种可利用的手法。
    """
    v = (os.environ.get(var) or "").strip()
    if not v:
        return default
    return os.path.normpath(v if os.path.isabs(v) else os.path.join(HERE, v))

PLATFORM = _env_dir("DATAMIND_ENGINE_DIR",  os.path.join(ROOT, "ontology-engine"))
OUTPUTS  = _env_dir("DATAMIND_OUTPUTS_DIR", os.path.join(ROOT, "outputs"))
LOCAL_SKILL_ROOT = os.path.join(HERE, "skills_seed")
_BUILTIN_SKILL_ROOTS = (LOCAL_SKILL_ROOT, os.path.join(PLATFORM, "web", "skills_seed"))
# 引擎目录只在**确实存在**时入 sys.path,且用 append 而非 insert(0):
# 置顶会让该目录里的同名模块(json.py/re.py…)盖过标准库,把一个「配错的目录」
# 升级成「可劫持解释器导入」的面;追加到末尾则只补充、不遮蔽。
_ENGINE_PKG = os.path.join(PLATFORM, "engine")
if os.path.isdir(_ENGINE_PKG) and _ENGINE_PKG not in sys.path:
    sys.path.append(_ENGINE_PKG)

# 监听地址与端口的单一事实源:默认只绑回环(本地原型的安全默认——本服务无鉴权,
# 绑 0.0.0.0 等于把建库/删文件/跑技能的接口开给整个网段)。需要对外时由部署方显式设置。
LISTEN_HOST = os.environ.get("DATAMIND_HOST") or "127.0.0.1"   # 空串会被 Flask 当成 0.0.0.0
try:
    LISTEN_PORT = int(os.environ.get("DATAMIND_PORT") or "8092")
except ValueError:
    LISTEN_PORT = 8092

# ── 上游引擎缺失时的降级垫片 ──────────────────────────────────────────
# 本仓库不含上游本体引擎(agent_runtime 由 DATAMIND_ENGINE_DIR 提供)。
# 未配置时注册一个同名空实现,使所有 `from agent_runtime import ...` 的调用点
# 都能导入成功并如实得到「无可用运行时」——而不是抛 ModuleNotFoundError 让端点 500。
# available() 返回空列表后,各处 `if drv not in available(): continue` 会自然跳过,
# 深度问数/构建随之走内置模板与纯数据驱动的兜底路径,行为与引擎离线时一致。
try:
    import agent_runtime as _ar                      # noqa: F401
    ENGINE_AVAILABLE = True
except Exception:
    import types as _types
    ENGINE_AVAILABLE = False
    # 垫片也要能承载 driver 注册:否则「只 clone 本仓 + 配 OpenAI 兼容 LLM」这条
    # README 承诺的路径拿不到任何运行时(实测 runtimes 为空,配了 key 也用不上)。
    _stub = _types.ModuleType("agent_runtime")
    _stub.__doc__ = "fallback shim — 未配置 DATAMIND_ENGINE_DIR;仅承载本仓自带 driver"
    _stub._REGISTRY = {}  # type: ignore[attr-defined]
    _stub.register = lambda name, factory: _stub._REGISTRY.__setitem__(name, factory)  # type: ignore[attr-defined]
    _stub.available = lambda: sorted(_stub._REGISTRY)  # type: ignore[attr-defined]
    def _get_runtime(name=None):
        asked = name or os.environ.get("CLAW_DRIVER") or ""
        if asked:
            f = _stub._REGISTRY.get(asked)
            if f is None:                            # 点名了却没注册:如实报错,
                raise RuntimeError(                  # 不能悄悄换成另一个驱动顶替
                    "运行时 %r 未注册;当前可用:%s。未配置上游引擎时仅本仓自带驱动可用。"
                    % (asked, _stub.available() or "(无)"))
            return f()
        if not _stub._REGISTRY:
            raise RuntimeError("无可用运行时:未配置上游引擎(DATAMIND_ENGINE_DIR),"
                               "也未配置 OpenAI 兼容端点(DATAMIND_LLM_BASE/_KEY/_MODEL)。"
                               "构建可改用纯数据驱动路径(quick_build)。")
        return list(_stub._REGISTRY.values())[0]()
    _stub.get_runtime = _get_runtime  # type: ignore[attr-defined]
    class _AR:                                       # driver 基类:垫片下也要能被继承
        def supports(self, _cap): return False
    _stub.AgentRuntime = _AR  # type: ignore[attr-defined]
    sys.modules["agent_runtime"] = _stub
    _ar = _stub

try:                                                 # DR-029:注册 OpenAI 兼容驱动
    import openai_runtime                            # 未配置端点则不注册,不制造"看似可用"
    openai_runtime.register(_ar)
except ImportError:
    pass                                             # 模块不在:正常形态,静默
except Exception as _e:                              # 其余是真故障,吞掉会让人查不出
    # 只报异常类型,不带消息:该消息里常含端点 URL/路径等部署细节,进日志即可能外泄。
    # 需要细节时把 DATAMIND_LOG_LEVEL=DEBUG 打开,由运维显式取用。
    _LOG.warning("OpenAI 兼容驱动注册失败:%s(细节见 DEBUG 级日志)", type(_e).__name__)
    _LOG.debug("驱动注册失败详情", exc_info=True)

app = Flask(__name__, static_folder=None)


def _positive_int_env(name, default, minimum=1024, maximum=200 * 1024 * 1024):
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


# 请求体统一上限覆盖 multipart 上传与聊天 JSON/base64 附件。默认 25 MiB；超限在
# 进入任何路由、落盘或解码前由 Flask 拒绝，避免单个请求耗尽内存/磁盘。
MAX_REQUEST_BYTES = _positive_int_env("DATAMIND_MAX_REQUEST_BYTES", 25 * 1024 * 1024)
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES


@app.errorhandler(413)
def _request_too_large(_error):
    return jsonify({"error": "请求体过大", "max_bytes": app.config["MAX_CONTENT_LENGTH"]}), 413
# IR-011/DR-043 蓝图化:引擎设置路由已迁出为 blueprint。
# 注意 app 级 before_request(下方 CSRF 守卫)对 blueprint 路由同样生效,安全模型不变。
# 须在 app 之后导入并注册,避免顺序歧义
from bp_engine import bp_engine as _bp_engine   # noqa: E402
app.register_blueprint(_bp_engine)

# ── CSRF 防护:阻止恶意网页跨站触发本机写/执行接口(deploy/build/skill/删除等)──
# 浏览器跨源写请求必带 Origin;同源 UI 的 Origin 即本机,放行。非浏览器工具(无 Origin/Referer)不在威胁模型内。
from urllib.parse import urlparse as _urlparse
@app.before_request
def _csrf_guard():
    if request.method in ("GET", "HEAD", "OPTIONS"): return
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin: return                       # curl/requests 等无源,非 CSRF 面
    host = _urlparse(origin).netloc
    if host and host != request.host:
        return jsonify({"error": "跨站请求被拒绝(CSRF 防护)"}), 403

# ── IR 加载(本体图谱源:示例 数据本体 / 应用本体 / 构建产物)──
IR_SOURCES = {
    # workdir 为持久权威副本(优先);/tmp 仅作后备(重启即失效,且可能是旧版)
    "demo": {"name": "示例企业数据本体(数据驱动)", "paths": [os.path.join(WORK, "demo_ir.json"), "/tmp/demo_ir.json"]},
    "app":  {"name": "示例应用本体(概念+流程)", "paths": [os.path.join(WORK, "app_ontology_ir.json"), "/tmp/appont_flow/app_ontology_ir.json"]},
    "cq":   {"name": "生产制造本体(上游引擎构建)", "paths": [os.path.join(PLATFORM, "web", "ontology-data.js")]},
}
def _load_json(paths):
    for p in paths if isinstance(paths, list) else [paths]:
        if p and os.path.exists(p):
            try:
                txt = open(p).read()
                if p.endswith(".js"):
                    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", txt, re.S)
                    return json.loads(m.group(1)) if m else None
                return json.loads(txt)
            except Exception: pass
    return None
_GKEY = re.compile(r"^[A-Za-z0-9_.\-]+$")
def _bad_gkey(key):
    """图谱键合法性:仅字母数字下划线点连字符,且不含 '..' —— 防 forged_../.. 之类路径穿越读/写任意文件"""
    return (not isinstance(key, str)) or (not _GKEY.match(key)) or (".." in key)

# ── 安全原语(防路径穿越 / SQL 标识符注入 / SSRF)──────────────────────
# 集中放这几条裁决,供所有「用户可影响 → 落盘/拼 SQL / 外联」的调用点复用;
# 即便上游已校验,在 sink 处再裁一次是纵深防御,也让静态分析能看见约束。
import ipaddress
# SQL 标识符白名单:字母/下划线/中文开头,后随字母数字下划线中文。
# 表名/列名经此过滤后才可安全地拼进 "..." 引用——含双引号或路径符的值会越出标识符边界(SQL 注入)。
_IDENT = re.compile(r"^[A-Za-z_一-鿿][A-Za-z0-9_一-鿿]*$")
def _safe_ident(name):
    """SQL 标识符(表/列)合法性:非法返 None。IR/上传等可被编辑的来源里的表名列名,
    拼进 SQL 前必须过此关,否则一个含 " 的列名即可越出 "..." 注入任意 SQL 片段。"""
    if isinstance(name, str) and _IDENT.match(name): return name
    return None

def _quote_ident(name):
    """把任意串包成合法的 SQL 双引号标识符/别名:内嵌 " 按 SQL 规范成对转义(" → "")。
    用于**必须允许任意字符**的位置(如中文指标名做结果列别名),此处白名单太严会误杀;
    转义后该值再也无法闭合引号越出标识符边界,注入面即被封死。"""
    return '"' + str(name or "").replace('"', '""') + '"'

def _safe_argv(val, cap=60, default="untitled"):
    """把用户/LLM 给的自由文本裁成可安全传给子进程的单个 argv:
    去掉控制字符与引号反引号等 shell 元字符、限长、禁止以 '-' 开头(避免被当成选项)。
    调用点均为 list 形式的 subprocess(无 shell),此处是第二道闸:即便将来有人改成
    shell=True 或子进程内部再把参数丢进 shell,这个值也拼不出命令。"""
    s = re.sub(r"[\x00-\x1f\x7f]", "", str(val or ""))        # 控制字符/换行
    s = re.sub(r"[`$;&|<>\\\"'\n\r]", "", s).strip()          # shell 元字符
    s = s[:cap].strip().lstrip("-").strip()
    return s or default

# 路径限定单一实现收于 srv_context.confine(realpath 版,连符号链接逃逸也拦)。
# 纵深防御语义不变:即便键已过 _bad_gkey,真正 open()/remove() 前仍再裁一次。
_confined = confine

def _resolved_ips(host):
    """主机名 → 解析到的 IP 对象列表;解析不出返回 None(调用方按"不可达"处理)。"""
    import socket
    if not host: return None
    try:
        infos = socket.getaddrinfo(host.strip(), None)
    except Exception:
        return None
    out = []
    for _fam, _stp, _cn, _sa, sockaddr in infos:
        try:
            out.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    return out or None

# 严格模式:连私网/回环也一并禁止外联。默认**不开**,原因见 _check_fetch_url 的说明。
_STRICT_FETCH = (os.environ.get("DATAMIND_BLOCK_INTERNAL_FETCH", "") or "").lower() in ("1", "true", "yes")

def _check_fetch_url(url):
    """服务端外联(API 数据源)前的 SSRF 裁决:→ 错误串(拒绝)或 None(放行)。

    分两档,因为「内网」对本系统而言不是攻击面而是**工作面**:
    这是一套装在企业内网、专门去连内网库与内网 API 的数据治理工具,把私网一律封死
    等于把 API 数据源这个功能废掉,使用者只会把开关打开 —— 那种默认值是安全表演。

    **默认拦死的**(任何正当用法都不需要,拦了零成本):
      · 非 http/https:file:// gopher:// dict:// 等协议走私,可读本地文件或打内网服务;
      · 链路本地 169.254.0.0/16 与 fe80::/10:云元数据端点(169.254.169.254)在此,
        一次请求就能取走实例的临时云凭据 —— SSRF 里危害最大的一类目标;
      · 未指定/保留地址(0.0.0.0、::、保留段)。
    **严格模式再加**(DATAMIND_BLOCK_INTERNAL_FETCH=1,给可暴露到不可信网络的部署):
      · 回环与私网(127/8、10/8、172.16/12、192.168/16 等)。
    """
    from urllib.parse import urlparse
    try:
        u = urlparse((url or "").strip())
    except Exception:
        return "非法 URL"
    if u.scheme not in ("http", "https"): return "仅允许 http/https(其它协议可被用于读本地文件或探内网)"
    if not u.hostname: return "URL 缺少主机名"
    ips = _resolved_ips(u.hostname)
    if ips is None: return "主机名无法解析,拒绝请求"
    # 回环单独放行再判其余:ipaddress 把 IPv6 回环 ::1 归入 is_reserved,
    # 若不先排除,凡用 localhost(解析出 ::1)登记的数据源都会被误拦 —— 而
    # 「自指向本机 API」恰是本功能最常见的正当用法(自带回归用例就是这么用的)。
    # 回环该不该拦由严格模式决定,不该由 IPv6 的地址分类顺带决定。
    def _blocked(ip):
        if ip.is_loopback: return False
        return ip.is_link_local or ip.is_unspecified or ip.is_reserved or ip.is_multicast
    if any(_blocked(ip) for ip in ips):
        return "目标为链路本地/保留/多播等特殊地址(含云元数据端点),已按 SSRF 防护拒绝"
    if _STRICT_FETCH and any(ip.is_private or ip.is_loopback for ip in ips):
        return "严格模式(DATAMIND_BLOCK_INTERNAL_FETCH=1)下禁止访问内网/回环地址"
    return None

def load_ir(key):
    if _bad_gkey(key): return None
    if key.startswith("built_"):
        raw = _load_json(_confined(WORK, key + ".json"))
        # 历史构建文件保留原样供审计；API/UI 使用深拷贝后的净化视图，不展示无来源
        # 正例，并纠正“工单/记录=过程”这类可机械判定的类型混淆。
        return content_quality.sanitize_ir(raw) if raw else raw
    if key.startswith("forged_"):
        return _load_json(_confined(os.path.join(PLATFORM, "data", "forged"), key[7:] + ".json"))
    return _load_json(IR_SOURCES.get(key, {}).get("paths", []))

# ── BFO 2020 / IOF Core 对齐(官方 IRI + 定义域/值域检查)──
_KIND_BFO = ontology_grounding.KIND_DEFAULTS

def _bfo_enabled_for_ir(ir):
    """旧 IR 保持历史 BFO/IOF 行为；带新构建配置的 IR 只在确实选择时启用。"""
    raw = (ir.get("build_manifest") or {}).get("references") if isinstance(ir, dict) else None
    if not isinstance(raw, dict) and isinstance(ir, dict):
        raw = (ir.get("scenario") or {}).get("build_references")
    if not isinstance(raw, dict):
        return True
    try:
        return build_references.uses_bfo_iof(raw)
    except ValueError:
        return False


def _ground_verb(verb, source_bfo=None, target_bfo=None):
    """返回可安全映射的标准关系名与时间说明；无法确认时返回 ``(None, None)``。"""
    item = ontology_grounding.from_verb(verb, source_bfo, target_bfo)
    return ((item["relation"] or None), (item["temporal"] or None))

def _iof_node_ann(o, bfo_enabled=True):
    """透传 IOF-AV 注释字段到图节点(缺失则从 kind 推 BFO 范畴);老 IR 无这些字段时优雅降级。"""
    kind = o.get("kind", "object")
    return {"bfo": (o.get("bfo") or _KIND_BFO.get(kind, "Continuant")) if bfo_enabled else None,
            "definition": o.get("definition", ""), "isPrimitive": o.get("isPrimitive"),
            "example": o.get("example", ""), "counterExample": o.get("counterExample", ""),
            "provenance": o.get("provenance"), "maturity": o.get("maturity", ""),
            "industry_reference": o.get("industry_reference"),
            "standard_alignment": o.get("standard_alignment"),
            "reused_from": o.get("reused_from"), "foundational_kind": o.get("foundational_kind"),
            "action_id": o.get("action_id"), "action_spec": o.get("action_spec")}
def _iof_edge_ann(r, verb, source_bfo=None, target_bfo=None, bfo_enabled=True):
    """规范化关系接地；旧 IR 的非官方名称和类别不相容映射不会进入导出。"""
    if not bfo_enabled:
        return {"founded_relation": "", "grounding_iri": "", "grounding_status": "not_selected",
                "grounding_reason": "本轮未选择 BFO/IOF", "temporal": "",
                "semantic": r.get("semantic", ""),
                "semantic_status": r.get("semantic_status") or r.get("semantic", ""),
                "evidence_status": r.get("evidence_status") or r.get("status", "")}
    item = ontology_grounding.normalize(
        r.get("founded_relation"), r.get("temporal"), verb, source_bfo, target_bfo,
    )
    return {
        "founded_relation": item["relation"],
        "grounding_iri": item["iri"],
        "grounding_status": item["status"],
        "grounding_reason": item["reason"],
        "temporal": item["temporal"],
        "semantic": r.get("semantic", ""),
        "semantic_status": r.get("semantic_status") or r.get("semantic", ""),
        "evidence_status": r.get("evidence_status") or r.get("status", ""),
    }

def ir_to_graph(key, ir):
    """统一成 {nodes:[{id,name,kind,...IOF-AV}], edges:[{s,t,verb,status,founded_relation,temporal}]}"""
    nodes, edges = [], []
    if not isinstance(ir, dict): return {"nodes": nodes, "edges": edges}
    bfo_enabled = _bfo_enabled_for_ir(ir)
    if "relations" in ir and "objects" in ir and ir["objects"] and "kind" in ir["objects"][0]:
        # 应用本体 IR:节点显示名优先用中文名(cn),id 仍用 name 以保证边引用稳定
        for o in ir["objects"]:
            nodes.append({"id": o["name"], "name": o.get("cn") or o["name"], "kind": o.get("kind", "object"),
                          "candidate": bool(o.get("candidate", False)),
                          "tables": o.get("tables", []), "indicators": o.get("indicators", []), "fields": o.get("field_count", 0),
                          **_iof_node_ann(o, bfo_enabled)})
        categories = {n["id"]: n.get("bfo") for n in nodes}
        for r in ir["relations"]:
            if r.get("source_concept") is None or r.get("target_concept") is None: continue
            if r.get("status") == "rejected": continue          # 人审否决的关系不再渲染(评审页可见、可恢复)
            verb = r.get("verb", "关联")
            edges.append({"s": r["source_concept"], "t": r["target_concept"], "verb": verb, "status": r.get("status", ""),
                          "overlap": r.get("overlap"), "human_review": r.get("human_review", ""),
                          **_iof_edge_ann(r, verb, categories.get(r["source_concept"]),
                                         categories.get(r["target_concept"]), bfo_enabled)})
    else:
        # 示例 数据 IR(DR-001)
        for o in ir.get("objects", []):
            nodes.append({"id": o["id"], "name": o.get("cn") or o.get("name"), "kind": o.get("kind", "object"),
                          "candidate": bool(o.get("candidate", False)),
                          "tables": [o.get("table")], "indicators": o.get("supported_metrics", []), "fields": o.get("attr_count", len(o.get("attrs", []))),
                          **_iof_node_ann(o, bfo_enabled)})
        categories = {n["id"]: n.get("bfo") for n in nodes}
        for l in ir.get("links", []):
            if l.get("source") is None or l.get("target") is None: continue
            if l.get("status") == "rejected": continue           # 人审否决的关系不再渲染(评审页可见、可恢复)
            verb = l.get("verb", "关联")
            edges.append({"s": l["source"], "t": l["target"], "verb": verb, "status": l.get("status", ""),
                          "overlap": l.get("overlap_pct", l.get("overlap")), "human_review": l.get("human_review", ""),
                          **_iof_edge_ann(l, verb, categories.get(l["source"]),
                                         categories.get(l["target"]), bfo_enabled)})
    return {"nodes": nodes, "edges": edges}

# ── SQLite 工具(只读查询)──
def q(sql, db=None, limit=500, attach_uploads=False):
    # 以只读模式打开(mode=ro):即便 SQL 含写操作,引擎层也会拒绝,杜绝改/删库
    path = db or DB
    con = ro_connect(path)
    con.row_factory = sqlite3.Row
    # 深度问数带上传数据时:只读挂载 uploads.db,上传表可作 up.<表> 查询
    if attach_uploads and os.path.exists(UPLOAD_DB):
        try: con.execute(f"ATTACH DATABASE 'file:{UPLOAD_DB}?mode=ro' AS up")
        except Exception: pass
    # 语句级超时:防笛卡尔积/慢查询拖垮进程(约 8s 后中断)
    deadline = [time.time() + 8.0]
    con.set_progress_handler(lambda: 1 if time.time() > deadline[0] else 0, 10000)
    try:
        cur = con.execute(sql)
        rows = [dict(r) for r in cur.fetchmany(limit)]
        return {"columns": [c[0] for c in cur.description or []], "rows": rows}
    finally: con.close()
# 运行时缓存 _RT_CACHE / runtime_cached / _drv_order 已收敛到 srv_engine(见文件头 import)
def table_list(db=None):
    con = ro_connect(db or DB)
    try:
        tabs = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        cn_by_table = {}
        try:
            for obj in (load_ir_edited("demo") or {}).get("objects", []):
                table = str(obj.get("table") or "").strip().lower()
                cn = str(obj.get("cn") or "").strip()
                if table and cn and cn not in cn_by_table.setdefault(table, []):
                    cn_by_table[table].append(cn)
        except Exception:
            pass                            # IR 不可用时目录仍可展示物理表
        out = []
        for t in tabs:
            quoted = t.replace('"', '""')
            n = con.execute(f'SELECT count(*) FROM "{quoted}"').fetchone()[0]
            cols = con.execute(f'PRAGMA table_info("{quoted}")').fetchall()
            out.append({"name": t, "cn": "、".join(cn_by_table.get(t.lower(), [])),
                        "rows": n, "cols": len(cols)})
        return out
    finally:
        con.close()

# IR-011/DR-043 第二个路由簇：动作 API 依赖主应用的数据目录函数，以显式注入代替
# blueprint 反向导入 server，避免循环依赖；动作文件存储与 schema 原语在 srv_actions。
from bp_actions import bp_actions as _bp_actions, configure_actions as _configure_actions  # noqa: E402
_configure_actions(table_list)
app.register_blueprint(_bp_actions)

# ── 后台作业(技能运行/本体构建)──
JOBS: dict[str, dict[str, Any]] = {}
# _WRITE_LOCK 已收敛到 srv_context(server 与 blueprint 共用同一把锁)
# rdflib 的 SPARQL 解析器基于 pyparsing,其 packrat 缓存/语法状态为进程级全局且非线程安全:
# 多请求并发跑 SPARQL(或 SPARQL 与 pyshacl 内部 SPARQL 相撞)会污染语法,报出
# 『Expected SelectQuery, found OPTIONAL』『postParse2() missing arg』等假语法错。故串行化所有 SPARQL 语法操作。
_RDF_LOCK = threading.Lock()
def run_job(cmd, cwd=None, env=None, tag=""):
    jid = uuid.uuid4().hex[:8]
    logf = os.path.join(WORK, f"job_{jid}.log")
    if len(JOBS) > 200:                                   # 防无界增长:超 200 淘汰最早
        for k in list(JOBS)[:len(JOBS) - 200]: JOBS.pop(k, None)
    JOBS[jid] = {"id": jid, "tag": tag, "status": "running", "log": logf, "cmd": " ".join(cmd)[:200], "ts": time.time()}
    def _run():
        with open(logf, "w") as f:
            try:
                p = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, stdout=f, stderr=subprocess.STDOUT, timeout=1800)
                JOBS[jid]["status"] = "done" if p.returncode == 0 else f"exit_{p.returncode}"
            except Exception as e:
                f.write(f"\nJOB ERROR: {e}"); JOBS[jid]["status"] = "error"
    threading.Thread(target=_run, daemon=True).start()
    return jid

def _bounded(fn, secs, default=None):
    """守护线程内跑 fn,最多等 secs 秒;超时则放弃并返回 default,保证外层请求永不卡死。"""
    v, _err, _to = _bounded_ex(fn, secs, default=default)
    return v

def _bounded_ex(fn, secs, default=None):
    """同 _bounded,但显式区分三种结局,返回 (value, error, timed_out):
    - 正常完成 → (返回值, None, False)
    - fn 抛异常 → (default, 异常对象, False)   ← 关键:异常不再被误报成『超时』
    - 超时未完成 → (default, None, True)
    调用方据此给出准确错误(异常 vs 超时),避免『瞬时失败却提示 8s 超时』的自相矛盾。"""
    box = {"v": default, "done": False, "err": None}
    def run():
        try: box["v"] = fn()
        except Exception as e: box["err"] = e
        box["done"] = True
    t = threading.Thread(target=run, daemon=True); t.start(); t.join(secs)
    if not box["done"]:
        return default, None, True
    return box["v"], box["err"], False

def _bounded_stream(fn, secs, tick=2.0):
    """有界执行的流式版本：fn(log) 在守护线程里跑，期间通过 log(text) 回报过程；
    本生成器按到达顺序产出 ("log", text) 与 ("tick", 已用秒数)，结束时产出
    ("done", 返回值, 异常或 None, 是否超时)。

    动机：本体构建里的 LLM 抽取一跑就是两三分钟，此前只有一条静态状态文案，
    用户看不到引擎在做什么、有没有卡死。这里把过程变成可流出的事件，前端折叠
    展示；超时语义与 _bounded_ex 一致（超时不杀线程，只放弃等待）。"""
    import queue as _q
    qq = _q.Queue()
    box = {"v": None, "err": None}
    def log(text):
        try: qq.put(("log", str(text)[:800]))
        except Exception: pass
    def run():
        try: box["v"] = fn(log)
        except Exception as e: box["err"] = e
        finally: qq.put(("__end__", None))
    t0 = time.time()
    threading.Thread(target=run, daemon=True).start()
    last_tick = t0
    while True:
        try:
            kind, payload = qq.get(timeout=tick)
        except _q.Empty:
            kind, payload = None, None
        if kind == "log":
            yield ("log", payload)
        elif kind == "__end__":
            yield ("done", box["v"], box["err"], False); return
        now = time.time()
        if now - last_tick >= tick:
            last_tick = now
            yield ("tick", round(now - t0, 1))
        if now - t0 > secs:
            yield ("done", None, None, True); return

# ── 深度问数编排(hermes/claude-code → SQL 计划 → 本地执行 → 洞察)──
def _obj_key(o, i=0):
    """对象主键:示例 IR 用 id,构建产物用 name"""
    return o.get("id") or o.get("name") or f"_obj{i}"


# 命名校验/词根收敛到 dao_core 单一事实源(DR-035;消 server 内此前的第三份副本,含复合键分支)。
# 保留 _key_stem/_key_name_ok 名称,现有调用点不改。语义与旧实现逐值一致(测试对照在案)。
_key_stem = dao_core.key_stem
_key_name_ok = dao_core.key_name_ok


_KEY_NOTE_RE = re.compile(r"([A-Za-z_]\w*)→[A-Za-z_]\w*\.([A-Za-z_]\w*)")
_KEY_FK_RE = re.compile(r"声明FK\s+([A-Za-z_]\w*)→([A-Za-z_]\w*)")


def _rel_keys(r):
    """关系的 JOIN 键 → (child_key, parent_key, 来源)。

    优先结构化 evidence。早期构建产物把算出来的键只写进 note 自由文本(DR-033 前),
    退而从 note 解析并把来源标成 note —— 键的可信度不同,不能混为一谈。"""
    ev = r.get("evidence") or {}
    ck, pk = ev.get("child_key"), ev.get("parent_key")
    if ck and pk:
        return ck, pk, ev.get("source") or "evidence"
    note = r.get("note") or ""
    m = _KEY_NOTE_RE.search(note) or _KEY_FK_RE.search(note)
    if m:
        return m.group(1), m.group(2), "note"
    return None, None, ""


def _join_hints(ir, tables, pairs=None):
    """选中表之间的本体关系 → JOIN 提示行(⋈ 前缀;沿本体关系召回的实现)。
    只给 verified/asserted(人审断言)关系;键取关系证据里的 child_key/parent_key。

    形状无关(DR-033):示例 IR 的 links[source/target] 与构建产物的
    relations[source_concept/target_concept] 都能读——否则选中自建本体时一条 JOIN 都给不出。

    pairs 非 None 时同步收集结构化边(DR-032 锚定可视化)。刻意与提示行同源产出——
    另起一段代码重新推导,可视化会与真正喂给引擎的内容悄悄漂移。"""
    tl = {str(t).lower() for t in tables if t}
    rels, sk, tk = _rels(ir)
    o2t = {_obj_key(o, i): o.get("table") for i, o in enumerate(ir.get("objects", []))}
    out = []
    for l in rels:
        if l.get("status") not in ("verified", "asserted"): continue
        st, tt = o2t.get(l.get(sk)), o2t.get(l.get(tk))
        if not st or not tt or st.lower() not in tl or tt.lower() not in tl: continue
        ck, pk, ksrc = _rel_keys(l)
        bad = bool(ck and pk) and not dao_core.name_ok(ck, tt, pk, child_table=st)
        if bad: ksrc = "name_mismatch"                # 疑为自增键值域巧合:保留语义关系,不下发该键
        key = (f"{st}.{ck} = {tt}.{pk}" if (ck and pk and not bad)
               else f"{st} 关联 {tt}(键见列名)")
        out.append(f"⋈ {key}  [{l.get('verb','关联')} · {l.get('status')}]")
        if pairs is not None:
            pairs.append({"s": st, "t": tt, "verb": l.get("verb") or "关联",
                          "status": l.get("status"), "key": key, "hop": 1,
                          "key_src": ksrc, "has_key": bool(ck and pk and not bad),
                          "dropped_key": (f"{ck}↔{pk}" if bad else "")})
        if len(out) >= 12: break
    # 两跳路径召回(DR-022):选中表间无直接关系、但经一张中间表可达 → 给出完整 JOIN 链。
    # 「累计产量最高的产线」这类跨两跳聚合,缺路径提示时引擎最易自造错误 JOIN。
    if len(out) < 12:
        adj = {}                                     # table → [(邻表, 本端键, 邻端键)]
        for l in rels:
            if l.get("status") not in ("verified", "asserted"): continue
            ck, pk, _ = _rel_keys(l)
            st, tt2 = o2t.get(l.get(sk)), o2t.get(l.get(tk))
            if not (ck and pk and st and tt2) or not dao_core.name_ok(ck, tt2, pk, child_table=st): continue
            adj.setdefault(st.lower(), []).append((tt2.lower(), ck, pk, st, tt2))
            adj.setdefault(tt2.lower(), []).append((st.lower(), pk, ck, tt2, st))
        tl_list = sorted(tl)
        added = 0
        for i, a in enumerate(tl_list):
            for b in tl_list[i + 1:]:
                if added >= 3: break
                # 已有直接提示则跳过
                if any(a in h.lower() and b in h.lower() for h in out): continue
                hit = None
                for (m, k1, k2, at, mt1) in adj.get(a, []):
                    if m == b: hit = None; break     # 有直连(未入 out 因非选中态),不补链
                    for (b2, k3, k4, _mt2, bt) in adj.get(m, []):
                        if b2 == b and m not in tl:
                            hit = (at, k1, mt1, k2, k3, bt, k4); break
                    if hit: break
                if hit:
                    at, k1, mtab, k2, k3, bt, k4 = hit
                    out.append(f"⋈⋈ {at}.{k1} = {mtab}.{k2} ∧ {mtab}.{k3} = {bt}.{k4}(经中间表 {mtab},两跳链)")
                    if pairs is not None:
                        pairs.append({"s": at, "t": bt, "via": mtab, "verb": "两跳可达",
                                      "status": "path", "hop": 2, "has_key": True, "key_src": "chain",
                                      "key": f"{at}.{k1} = {mtab}.{k2} ∧ {mtab}.{k3} = {bt}.{k4}"})
                    added += 1
            if added >= 3: break
    return out

_REG_CACHE: dict[str, Any] = {"win": None, "win_ts": 0, "base": {}, "base_ts": 0}

def _data_window():
    """M4-a 数据时间窗:显式告知 LLM 业务数据截止到哪天。

    不告知的后果是确定的:LLM 会用 date('now','-3 month') 之类相对区间,而合成/历史库
    的数据往往早于今天,查询因此命中极少甚至为空(实测本库 214/4190 行),且随时间流逝
    只会更糟。窗口由确定性 SQL 探测,算不出则返回空串——不臆造。缓存 10 分钟。
    """
    if _REG_CACHE["win"] is not None and time.time() - _REG_CACHE["win_ts"] < 600:
        return _REG_CACHE["win"]
    win = ""
    for tbl, col in (("fact_sales_order", "order_date"), ("dws_production_daily", "date")):
        try:
            r = q(f'SELECT MIN("{col}") a, MAX("{col}") b FROM "{tbl}"', limit=1)
            row = (r.get("rows") or [{}])[0]
            if row.get("a") and row.get("b"):
                win = (f'数据时间窗: {row["a"]} ~ {row["b"]}(业务数据截止于此;'
                       f'问"最近N个月"请以 {row["b"]} 为基准倒推,不要用 date("now"),否则查空)')
                break
        except Exception:
            continue
    _REG_CACHE.update(win=win, win_ts=time.time())
    return win

def _metric_baselines(mets):
    """M4-b 指标统计基线:让 LLM 看到的不是一个列名,而是一个有取值范围与数据边界的业务量。

    对标 Trane M4(五法中唯一 100/100):在数据进入 AI 之前先做一次业务语义封装。此处封装
    全部由确定性 SQL 得出——与数据裁决同源,零幻觉;单个指标算不出就跳过,绝不猜数。
    口径说明:给出的是**观测区间**(实际数据的 min/max),不是工艺规范意义上的"正常范围"
    ——后者需要工艺标准输入,系统无从推导,不可用观测极值冒充(极值可能本就是异常样本)。
    负值/零值单独标注:它们通常是业务异常信号(如负毛利订单),LLM 若默认"毛利必为正"会写错过滤条件。
    """
    out = []
    for m in mets[:6]:
        # 表名/列名来自 IR,而 IR 可经 /api/ont/apply 编辑 —— 即用户可影响。
        # 它们要拼进 SQL 的标识符位,必须过白名单:一个含 " 的列名足以越出 "..." 注入任意片段。
        tbl, col = _safe_ident(m.get("table")), _safe_ident(m.get("col"))
        nm = m.get("name")
        if not (tbl and col and nm): continue
        ck = f"{tbl}.{col}"
        if ck in _REG_CACHE["base"] and time.time() - _REG_CACHE["base_ts"] < 600:
            out.append(_REG_CACHE["base"][ck]); continue
        try:
            r = q(f'SELECT MIN("{col}") mn, MAX("{col}") mx, ROUND(AVG("{col}"),2) av, '
                  f'COUNT("{col}") n, SUM(CASE WHEN "{col}"<0 THEN 1 ELSE 0 END) neg '
                  f'FROM "{tbl}"', limit=1)
            row = (r.get("rows") or [{}])[0]
            if row.get("n") in (None, 0) or row.get("mn") is None: continue
            u = (m.get("unit") or "").strip()
            seg = (f'{nm}({ck}): 观测区间[{row["mn"]}, {row["mx"]}] 均值{row["av"]} 非空{row["n"]}行'
                   + (f' 单位{u}' if u else ""))
            if row.get("neg"): seg += f' 含{row["neg"]}行负值(业务异常,勿假设恒为正)'
            _REG_CACHE["base"][ck] = seg
            _REG_CACHE["base_ts"] = time.time()
            out.append(seg)
        except Exception:
            continue
    return out

_LAYER_SEMANTICS = {   # 数仓分层前缀的权威语义:LLM 若不知约定,会按通用语境猜表用途
    "ods": "贴源层(原始系统镜像)", "stg": "暂存层(清洗中间态)", "dim": "维度表(主数据/档案)",
    "dwd": "明细层(单据粒度)", "dws": "汇总层(按日或按维度预聚合)",
    "fact": "事实表(业务过程流水)", "agg": "聚合层(跨维汇总)", "up": "用户上传表",
}

def _glossary_block(picked):
    """M5 领域词汇表注入:把本次上下文涉及的分层前缀语义与歧义缩写的权威译法钉进 prompt。

    工业缩写在不同语境含义完全不同(OA 在暖通=新风量,在办公语境=办公自动化);不锚定时
    LLM 只能用『大概率正确』的通用解释填空——在本系统里可能是致命误解。表名与列名的中文
    由 IR 自带(已人工校订),此处只补两类 IR 里没有的语义:分层约定 + 短缩写词元。
    """
    prefixes, out = set(), []
    for o in picked:
        t = (o.get("table") or "").lower()
        p = t.split("_", 1)[0]
        if p in _LAYER_SEMANTICS: prefixes.add(p)
    if prefixes:
        out.append("分层约定: " + "; ".join(f"{p}_* = {_LAYER_SEMANTICS[p]}" for p in sorted(prefixes)))
    # 只锚定"出现在本次列名里 + 短到易歧义(≤4 字符)"的词元,避免把 434 条词表全灌进上下文
    try:
        import translate_cn as T
        tok = getattr(T, "TOKEN", {}) or {}
        blob = " ".join((a.get("col") or "").lower() for o in picked for a in o.get("attrs", []))
        hits = [(en, cn) for en, cn in tok.items()
                if en and cn and len(en) <= 4 and re.search(r"(^|_)" + re.escape(en.lower()) + r"($|_)", blob)]
        if hits:
            out.append("缩写锚定(本系统内的确定含义,勿按通用语境另作解释): "
                       + "; ".join(f"{en}={cn}" for en, cn in sorted(hits)[:20]))
    except Exception:
        pass
    return out

def _graph_name(key):
    """图谱显示名:内置源取注册表,构建产物取场景名"""
    if key in IR_SOURCES: return IR_SOURCES[key]["name"]
    ir = load_ir(key) or {}
    return ((ir.get("scenario") or {}).get("name") or key)


def _obj_table(o):
    """对象绑定的表名。两种写法都要认:示例/构建端点用 `table`,quick_build 产出用 `tables[]`。
    只认 `table` 会把 quick_build 的本体判成「无绑表对象」而静默回退 demo ——
    「自己建的本体拿不来问数」这条主链路曾因此是断的。"""
    t = o.get("table")
    if t: return t
    # 跳过 tables 里的空值/None:取第一个真正有内容的,否则空串会被当成"有表"
    for x in (o.get("tables") or []):
        if x: return x
    return None


_SQL_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"   # 标识符白名单(表名/列名),全站消毒共用
_COLS_CACHE: dict[str, list[str]] = {}


def _table_cols(table):
    """从数据源现读列名(带进程内缓存)。本体产出未必带 attrs —— quick_build 就不带,
    那样喂给引擎的上下文是「表 X(): 」一个列都没有,模型只能猜列名,SQL 必然报
    no such column。缺列宁可现查,也不能让模型盲写。"""
    raw = (table or "").lower()
    db_path = DB
    t = raw
    if raw.startswith("up."):
        db_path, t = UPLOAD_DB, raw[3:]
    # 表名来自本体产物,按标识符白名单校验后才拼进 PRAGMA —— 与全站标识符消毒口径一致
    if not t or not re.fullmatch(_SQL_IDENT, t): return []
    cache_key = raw
    if cache_key in _COLS_CACHE: return _COLS_CACHE[cache_key]
    try:
        con = ro_connect(db_path)
        try:
            cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % t)]
        finally:
            con.close()
    except Exception:
        # 只缓存成功结果:库临时不可用时若把空列表缓存下来,库恢复后仍会一直返回空
        return []
    _COLS_CACHE[cache_key] = cols
    return cols


def _obj_cols_text(o, limit=18):
    """对象的列清单文本:优先本体自带 attrs(含中文名),缺失则回落到库里现读的列名。"""
    attrs = o.get("attrs") or []
    if attrs:
        # cn 可能是 None:用 or "" 兜住,否则会渲染出字面量 "None" 喂给模型
        return ", ".join(f'{a["col"]}({a.get("cn") or ""})' for a in attrs[:limit] if a.get("col"))
    return ", ".join(_table_cols(o.get("table"))[:limit])


def _normalize_tables(ir):
    """把 tables[] 归一出 table 字段(不改原文件,只改内存副本),使下游一律读 table。"""
    for o in ir.get("objects", []):
        if not o.get("table"):
            t = _obj_table(o)
            if t: o["table"] = t
    return ir


def _anchor_ir(graph_keys=None):
    """锚定本体 = 用户选中的图谱(可多选合并);未选时用示例本体。

    此前问数无论选哪个图谱都锚定 demo,选中的图谱只被当表名过滤器用——
    「选了本体却没按这套本体作答」是 DR-033 要修的核心问题。"""
    # 去重并保序:重复键会让合并路径重复扫同一套本体,规模统计也会翻倍
    seen_k, keys = set(), []
    for k in (graph_keys or []):
        if not k or k in seen_k: continue
        if _bad_gkey(k):                     # 路径穿越/非法键:不进锚定,不静默当作有效
            continue
        seen_k.add(k); keys.append(k)
    keys = keys or ["demo"]
    if len(keys) == 1:
        import copy as _cp
        _ir = load_ir_edited(keys[0]) or load_ir(keys[0])
        if not _ir:                          # 图谱不存在:如实回落示例本体并标注,不静默顶替
            return (load_ir_edited("demo") or {}), ["demo"], "图谱 %s 不存在或为空,已回落示例本体" % keys[0]
        return _normalize_tables(_cp.deepcopy(_ir)), keys, ""
    objs, links, seen, sl = [], [], set(), set()
    for k in keys:                                   # 多选:归一到示例形状后合并,按主键/端点对去重
        ir = load_ir_edited(k) or load_ir(k) or {}
        rels, sk, tk = _rels(ir)
        for idx, o in enumerate(ir.get("objects", [])):
            kk = _obj_key(o, idx)
            if kk in seen: continue
            seen.add(kk); o = dict(o); o["id"] = kk
            if not o.get("table"):
                _t = _obj_table(o)
                if _t: o["table"] = _t
            objs.append(o)
        for r in rels:
            pair = (r.get(sk), r.get(tk))
            if not all(pair) or pair in sl: continue
            sl.add(pair); r = dict(r); r["source"], r["target"] = pair; links.append(r)
    return {"objects": objs, "links": links}, keys, ""


def _qa_table_inventory():
    """返回深度问数当前进程真正能执行的主库表与上传库表。"""
    def names(path):
        if not os.path.exists(path): return set()
        try:
            con = ro_connect(path)
            try:
                return {str(r[0]).lower() for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            finally:
                con.close()
        except Exception:
            return set()
    return names(DB), names(UPLOAD_DB)


def _qa_graph_profile(ir, inventory=None):
    """计算一套本体在当前问数连接中的可执行范围。"""
    main, uploads = inventory or _qa_table_inventory()
    bound, seen = [], set()
    for obj in (ir or {}).get("objects", []):
        table = str(_obj_table(obj) or "").strip()
        low = table.lower()
        if table and re.fullmatch(_SQL_IDENT, table) and low not in seen:
            seen.add(low); bound.append(table)
    available, upload_available, unavailable = [], [], []
    for table in bound:
        low = table.lower()
        if low in main:
            available.append(table)
        elif low in uploads:
            upload_available.append("up." + table)
        else:
            unavailable.append(table)
    usable = available + upload_available
    if usable:
        reason = (f"当前查询连接可用 {len(usable)}/{len(bound)} 张绑定表"
                  + (f"；另有 {len(unavailable)} 张未接入" if unavailable else ""))
    elif bound:
        reason = f"绑定的 {len(bound)} 张表均未接入当前查询连接"
    else:
        reason = "本体尚未绑定数据表"
    return {"queryable": bool(usable), "bound_tables": bound,
            "available_tables": usable, "unavailable_tables": unavailable,
            "reason": reason}


def _qa_graph_summary(ir, inventory=None):
    """图谱列表只回传计数，避免把多套百表清单重复塞进首屏响应。"""
    profile = _qa_graph_profile(ir, inventory)
    return {"queryable": profile["queryable"], "reason": profile["reason"],
            "bound_table_count": len(profile["bound_tables"]),
            "available_table_count": len(profile["available_tables"]),
            "unavailable_table_count": len(profile["unavailable_tables"])}


def _qa_anchor_ir(graph_keys=None):
    """取得问数实际使用的本体，并剔除当前查询连接不可执行的表绑定。"""
    ir, keys, fallback = _anchor_ir(graph_keys)
    profile = _qa_graph_profile(ir)
    main = {x.lower() for x in profile["available_tables"] if not x.lower().startswith("up.")}
    uploads = {x[3:].lower() for x in profile["available_tables"] if x.lower().startswith("up.")}
    for obj in ir.get("objects", []):
        table = str(_obj_table(obj) or "").strip()
        if not table: continue
        low = table.lower()
        if low in main:
            obj["table"] = table
        elif low in uploads:
            obj["table"] = "up." + table
        else:
            obj["qa_unavailable_table"] = table
            obj.pop("table", None)
            obj["tables"] = []
    return ir, keys, fallback, profile


def _qa_scope_error(requested_keys, actual_keys, fallback, profile):
    """显式选中的本体不可执行时给出可操作错误；缺省示例/图谱缺失回落不拦。"""
    if not requested_keys or fallback or actual_keys == ["demo"] or profile.get("queryable"):
        return ""
    names = "、".join(_graph_name(key) for key in actual_keys)
    missing = profile.get("unavailable_tables") or []
    detail = "、".join(missing[:5]) + ("…" if len(missing) > 5 else "")
    if missing:
        return (f"本体「{names}」绑定的数据表尚未接入当前问数连接：{detail}。"
                "请先在数据连接中接入并物化这些表，或选择带有可查询表的本体。")
    return (f"本体「{names}」尚未绑定可查询的数据表，当前只能浏览和治理，不能直接问数。"
            "请先在本体构建或评审中完成对象与数据表绑定。")


def _trace_objs(trace, objs, reason, hits=None):
    """把一批入选对象按入选理由记进锚定轨迹(DR-032);同一对象只记首次理由。
    hits: {表名: [命中的问句词]} —— 锚定「凭什么选中它」的证据(DR-033)。
    按表名关联而非对象主键:主键在缺 id/name 时靠序号兜底,两处序号未必一致。"""
    if trace is None: return
    seen = {o["table"] for o in trace.setdefault("objects", []) if o.get("table")}
    for i, o in enumerate(objs):
        t = o.get("table")
        if not t or t in seen: continue
        seen.add(t)
        k = _obj_key(o, i)
        trace["objects"].append({"key": k, "cn": o.get("cn") or o.get("name") or k,
                                 "table": t, "reason": reason,
                                 "hits": (hits or {}).get(t) or [],
                                 "aliases": (o.get("aliases") or [])[:4],
                                 "ncol": len(o.get("attrs") or [])})


def build_context(question, focus_tables=None, trace=None, graph_keys=None):
    """从本体挑相关表/列/指标,组紧凑 schema 上下文。

    graph_keys:用户选中的本体图谱 → 作为锚定本体源(DR-033)。
    focus_tables:用户显式点选的表 → 直接限定(点了就用这几张,不再打分)。
    trace 传入 dict 时,同步记录**这次召回锚定到了本体的哪些对象与关系、凭什么命中**(DR-032)。"""
    ir, akeys, _miss, _profile = _qa_anchor_ir(graph_keys)
    if trace is not None and _miss:
        trace["fallback"] = _miss
    if trace is not None:
        _rl = _rels(ir)[0]
        trace["ontology"] = {"keys": akeys,
                             "names": [_graph_name(k) for k in akeys],
                             "objects": len(ir.get("objects", [])), "relations": len(_rl),
                             "queryable": _profile["queryable"],
                             "bound_tables": len(_profile["bound_tables"]),
                             "available_tables": len(_profile["available_tables"])}
        if _profile["unavailable_tables"]:
            trace["scope_warning"] = (f"{len(_profile['unavailable_tables'])} 张绑定表未接入当前查询连接，"
                                      "本次只使用可执行表")
    mets = []
    _ml = ir.get("metric_layers")
    # 锚定源现在可能是任意图谱,其 metric_layers 未必是 {层: [指标]} —— 形状不符就跳过,不炸
    for k, arr in (_ml if isinstance(_ml, dict) else {}).items():
        if not isinstance(arr, list): continue
        for m in arr:
            if isinstance(m, dict):
                mets.append({"name": m.get("name"), "table": m.get("table"), "col": m.get("value_col"), "layer": k, "unit": m.get("unit") or ""})
    kws = [w for w in re.split(r"[,，。？?\s]+", question) if w]
    kws += expand_terms(question)          # A1 术语扩展:词典同义/中英互补词并入匹配
    def hits_of(txt):
        """问句词/扩展词在该对象语料里的命中。

        ≤2 字符的英文缩写只认整词:术语词典把「销售订单」扩展出 so,而 so 作子串会命中
        reason_code、sensor_id,把停机、报警这类无关表拉进上下文(命中证据视图暴露的真实污染)。"""
        out, toks = [], None
        for w in kws:
            if not w: continue
            if w.isascii() and len(w) <= 2:
                if toks is None: toks = set(re.split(r"[^0-9A-Za-z]+", txt.lower())) - {""}
                if w.lower() in toks: out.append(w)
            elif w in txt: out.append(w)
        return out
    def score(txt): return len(hits_of(txt))
    def cn_hits(*groups):
        """反向匹配:拿本体自己的中文词去问句里找。

        中文问句不做分词,按标点/空格切出来常常整句就是一个词元 ——「车间近期产能怎么样」
        切不出「产能」,所以正向匹配(问句词 ∈ 对象语料)对中文几乎必然落空,中文召回一直
        只能靠术语词典折成英文。反过来把本体自带的中文词当词典去问句里查,不需要分词器,
        结果确定,且给对象补的业务别名从此真正生效。
        ≤1 字的词不参与:单字满篇皆是,会把无关表拉进上下文。"""
        out = []
        for g in groups:
            for t in (g or []):
                t = str(t or "").strip()
                if len(t) >= 2 and not t.isascii() and t in question and t not in out:
                    out.append(t)
        return out
    def obj_cn_hits(o):
        return cn_hits([o.get("cn")], o.get("aliases"),
                       [a.get("cn") for a in (o.get("attrs") or [])])
    ft = set(t.lower() for t in (focus_tables or []))
    if ft:   # 用户在『数据源』里显式点了表 → 只喂这些表(点了就用这几张,不再打分)
        picked = [o for o in ir.get("objects", []) if (o.get("table") or "").lower() in ft]
        if picked:
            lines = []
            for o in picked:
                cols = _obj_cols_text(o)
                _al = "、".join(o.get("aliases") or [])
                lines.append(f'表 {o["table"]}({o.get("cn","")}{",业务别称:" + _al if _al else ""}): {cols}')
            _trace_objs(trace, picked, "数据源限定")
            if trace is not None: trace["scoped"] = True
            _pairs = [] if trace is not None else None
            jh = _join_hints(ir, [o.get("table") for o in picked], pairs=_pairs)
            if trace is not None: trace["relations"] = _pairs
            if jh: lines.append("表间关系(本体已验证,JOIN 优先用这些键):\n" + "\n".join(jh))
            up = _uploads_schema()
            if up: lines.append("上传数据(作 up.<表> 查询): " + up)
            dw = _data_window()                       # M4-a 数据时间窗
            if dw: lines.append(dw)
            lines += _glossary_block(picked)          # M5 词汇表注入
            return "\n".join(lines)
        if trace is not None:
            trace["focus_miss"] = len(ft)             # 点选的表在本体里一张都没有 → 转打分召回,不静默当作已限定
    tabs, hmap = [], {}
    # 只有绑表对象能进 schema 上下文;混合本体(部分对象是纯概念)里若不滤,
    # 召回名额会被无表对象挤占,上下文可能一张表都没有
    for _i, o in enumerate([x for x in ir.get("objects", []) if x.get("table")]):
        # DR-027:别名并入评分语料——业务用语("产量")与表名中文("生产日汇总")常常不同,
        # 不认别名会让问数召回不到正确的表,进而生成查错表的 SQL
        blob = ((o.get("cn") or "") + (o.get("table") or "") + "".join(o.get("aliases") or [])
                + " ".join((a.get("cn") or "") + (a.get("col") or "") for a in o.get("attrs", [])))
        hs = hits_of(blob)
        hs += [h for h in obj_cn_hits(o) if h not in hs]     # 中文走反向匹配,见 cn_hits
        if hs and o.get("table"): hmap[o["table"]] = hs[:6]
        tabs.append((len(hs), o))
    tabs.sort(key=lambda x: -x[0])
    _hit = [o for s0, o in tabs[:8] if s0 > 0]
    picked = _hit or [o for _, o in tabs[:5]]
    _trace_objs(trace, picked, "关键词命中" if _hit else "无命中·默认候选", hmap)
    core = {"fact_sales_order", "fact_production_output", "dws_production_daily"}   # 核心事实表始终入上下文
    have = {o["table"].lower() for o in picked}
    for o in ir.get("objects", []):
        _t = (o.get("table") or "").lower()
        if _t and _t in core and _t not in have:
            picked.append(o); _trace_objs(trace, [o], "核心事实表", hmap)
    # 沿本体关系召回:命中表的一跳邻居(维表等)拉进上下文,JOIN 才有另一端
    have = {o["table"].lower() for o in picked if o.get("table")}
    rels, sk, tk = _rels(ir)
    o_by_id = {_obj_key(o, i): o for i, o in enumerate(ir.get("objects", []))}
    extras = []
    for l in rels:
        if l.get("status") not in ("verified", "asserted"): continue
        so, to = o_by_id.get(l.get(sk)), o_by_id.get(l.get(tk))
        if not so or not to: continue
        st, tt = (so.get("table") or "").lower(), (to.get("table") or "").lower()
        if st in have and tt and tt not in have and len(extras) < 4:
            extras.append(to); have.add(tt)
        elif tt in have and st and st not in have and len(extras) < 4:
            extras.append(so); have.add(st)
    picked += extras
    _trace_objs(trace, extras, "沿本体关系召回", hmap)
    lines = []
    for o in picked:
        cols = _obj_cols_text(o)
        _al = "、".join(o.get("aliases") or [])
        lines.append(f'表 {o["table"]}({o.get("cn","")}{",业务别称:" + _al if _al else ""}): {cols}')
    _pairs = [] if trace is not None else None
    jh = _join_hints(ir, [o.get("table") for o in picked], pairs=_pairs)
    if trace is not None: trace["relations"] = _pairs
    if jh: lines.append("表间关系(本体已验证,JOIN 优先用这些键):\n" + "\n".join(jh))
    # 指标名同为中文,同样要反向匹配:否则「毛利率的变化趋势」召不回名为「毛利率」的指标
    hit_m = [m for m in mets if score(m["name"] or "") or cn_hits([m["name"]])][:10]
    if trace is not None:
        trace["metrics"] = [{"name": m["name"], "table": m["table"], "col": m["col"]} for m in hit_m]
    if hit_m:
        lines.append("相关指标: " + "; ".join(f'{m["name"]}←{m["table"]}.{m["col"]}' for m in hit_m))
        bl = _metric_baselines(hit_m)                 # M4-b 指标统计基线(确定性 SQL)
        if bl: lines.append("指标基线(真实数据算出,勿自行假设量级):\n" + "\n".join("  " + b for b in bl))
    up = _uploads_schema()
    if up: lines.append("上传数据(作 up.<表> 查询): " + up)
    dw = _data_window()                              # M4-a 数据时间窗
    if dw: lines.append(dw)
    lines += _glossary_block(picked)                  # M5 词汇表注入
    return "\n".join(lines)


def _uploads_schema():
    """uploads.db 各表列结构(供深度问数带上传文件时喂进上下文,SQL 用 up.<表> 引用)"""
    if not os.path.exists(UPLOAD_DB): return ""
    try:
        con = sqlite3.connect(f"file:{UPLOAD_DB}?mode=ro", uri=True)
        tabs = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        parts = []
        for t in tabs[:8]:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')][:20]
            parts.append(f'up.{t}({", ".join(cols)})')
        con.close(); return "; ".join(parts)
    except Exception:
        return ""

def _llm_timeout(default=180):
    """LLM 单轮超时(秒),可经 DATAMIND_LLM_TIMEOUT 覆盖。
    原先规划调用写死 60s —— 推理型模型思考就要 60s+,每次刚好超时,
    表现为「返回几十字符」的静默失败,极难定位。"""
    try: return max(10, int(os.environ.get("DATAMIND_LLM_TIMEOUT") or default))
    except (TypeError, ValueError): return default


def _eng_label(drv):
    """对外中性引擎名:执行记录里不暴露底层多智能体库(hermes/claude-code/openclaw)"""
    return {"hermes": "智能引擎", "claude-code": "智能引擎(备选)", "claude": "智能引擎(备选)",
            "openclaw": "经典引擎"}.get(drv, "智能引擎")

# ── 深度问数增强(DR-019):术语扩展 / 口径拦截 / 口径卡 / 指代延续 / 按任务选模 ──
def _task_model(task):
    """B4 按任务选模:engine_config.task_models[task] 非空则单次覆盖模型(claude=--model, hermes=-m)。"""
    try: return ((_load_engine_cfg().get("task_models") or {}).get(task) or "").strip() or None
    except Exception: return None

def _model_fits(drv, m):
    """任务模型须与运行时同族:claude 系模型只喂 claude-code,其余喂 hermes/openclaw。
    防止引擎切到 hermes 后,任务覆盖里的 claude 模型名被塞给 hermes -m 而全线报错。"""
    lm = (m or "").lower()
    is_claude = lm.startswith("claude") or lm in ("opus", "sonnet", "haiku")
    return is_claude if drv == "claude-code" else not is_claude

def _llm_turn(rt, sid, prompt, timeout, task=None):
    """统一 LLM 调用:带任务级模型覆盖(仅当模型与运行时同族);不支持 model 参数则自动回落。→ (ok, reply)"""
    m = _task_model(task) if task else None
    if m and not _model_fits(getattr(rt, "name", ""), m):
        m = None
    if m:
        try: return rt.run_turn(sid, prompt, timeout=timeout, model=m)
        except TypeError: pass
    return rt.run_turn(sid, prompt, timeout=timeout)

_GLOSS_CACHE = {"ts": 0.0, "pairs": []}
def _gloss_pairs():
    """术语管理词典(translate_cn 四张词表)→ (en, cn) 对;5 分钟缓存。"""
    if time.time() - _GLOSS_CACHE["ts"] < 300 and _GLOSS_CACHE["pairs"]:
        return _GLOSS_CACHE["pairs"]
    pairs = []
    try:
        import translate_cn as T
        for src in (getattr(T, "TABLE_PHRASE", {}), getattr(T, "COL_PHRASE", {}),
                    getattr(T, "PHRASE", {}), getattr(T, "TOKEN", {})):
            for en, cn in src.items():
                if en and cn: pairs.append((str(en), str(cn)))
    except Exception:
        pass
    _GLOSS_CACHE.update(ts=time.time(), pairs=pairs)
    return pairs

def expand_terms(question):
    """A1 术语扩展检索:问句命中词典中文 → 补英文同义词(反之亦然),提升表/列匹配召回。
    只扩展长度≥2 的中文 / ≥3 的英文,防单字噪声;上限 12,防上下文爆炸。"""
    ql = (question or "").lower()
    out, seen = [], set()
    for en, cn in _gloss_pairs():
        if len(out) >= 12: break
        w = None
        if len(cn) >= 2 and cn in question and en.lower() not in ql: w = en
        elif len(en) >= 3 and en.lower() in ql and cn not in question: w = cn
        if w and w not in seen:
            seen.add(w); out.append(w)
    return out

_SQL_KW = {"on", "where", "group", "order", "left", "right", "inner", "outer", "cross",
           "join", "select", "limit", "using", "as", "union", "having", "with"}
def _validate_sql_ontology(sql, ir, strict=False):
    """A2 口径拦截(P8『口径错了直接拦截』落地):SQL 执行前对照本体校验。
    ① 表白名单:FROM/JOIN 的表必须在本体/数据目录/上传库(up.)/CTE 内 —— 拦臆造表名;
    ② JOIN 键校验:ON a.x=b.y 两侧列名不同时,该键对必须落在本体 verified/asserted 关系
      的 child/parent 键上(同名键等值 JOIN 放行,列名本身即口径)—— 拦自造 JOIN。
    → (ok, reason)"""
    s = sql or ""
    ctes = {m.group(1).lower() for m in re.finditer(r"(?:\bwith|,)\s*(%s)\s+as\s*\(" % _SQL_IDENT, s, re.I)}
    known = {(o.get("table") or "").lower() for o in ir.get("objects", []) if o.get("table")}
    if not strict:
        try: known |= {t["name"].lower() for t in table_list()}
        except Exception: pass
    up = set()
    try: up = {x.split("(")[0][3:].lower() for x in _uploads_schema().split("; ") if x.startswith("up.")}
    except Exception: pass
    alias, used = {}, []
    for m in re.finditer(r"\b(?:from|join)\s+(up\.)?(%s)(?:\s+(?:as\s+)?(%s))?" % (_SQL_IDENT, _SQL_IDENT), s, re.I):
        pre, t, al = m.group(1), m.group(2).lower(), (m.group(3) or "").lower()
        if al in _SQL_KW: al = ""
        full = ("up." + t) if pre else t
        used.append(full)
        alias[al or t] = full
    for full in used:
        base = full[3:] if full.startswith("up.") else full
        if full.startswith("up."):
            if base not in up: return False, f"上传表 {full} 不存在"
            if strict and full not in known and base not in known:
                return False, f"上传表 {full} 不在所选本体边界内"
        elif base not in known and base not in ctes:
            return False, f"表 {base} 不在本体/数据目录中(疑似臆造表名)"
    o2t = {o.get("id"): (o.get("table") or "").lower() for o in ir.get("objects", [])}
    relkeys = set()
    for l in ir.get("links", []):
        if l.get("status") not in ("verified", "asserted"): continue
        ev = l.get("evidence") or {}
        ck, pk = (ev.get("child_key") or "").lower(), (ev.get("parent_key") or "").lower()
        st, tt = o2t.get(l.get("source")), o2t.get(l.get("target"))
        if ck and pk and st and tt:
            relkeys.add((st, ck, tt, pk)); relkeys.add((tt, pk, st, ck))
    for m in re.finditer(r"\bon\s+(%s)\.(%s)\s*=\s*(%s)\.(%s)" % ((_SQL_IDENT,) * 4), s, re.I):
        a, ca, b, cb = (m.group(i).lower() for i in (1, 2, 3, 4))
        ta, tb = alias.get(a, a), alias.get(b, b)
        if ca == cb: continue
        if ta in ctes or tb in ctes or ta.startswith("up.") or tb.startswith("up."): continue
        if (ta, ca, tb, cb) not in relkeys:
            return False, f"JOIN 键 {ta}.{ca}={tb}.{cb} 不在本体已验证关系上(口径未证实,已拦截)"
    return True, ""


def _qa_validate_sql(sql, ir, strict=False):
    """问数口径校验调用层；兼容仍实现旧两参数签名的测试替换与扩展。"""
    if not strict:
        return _validate_sql_ontology(sql, ir)
    try:
        return _validate_sql_ontology(sql, ir, strict=True)
    except TypeError as exc:
        if "strict" not in str(exc):
            raise
        return _validate_sql_ontology(sql, ir)

_METRIC_LAYER_CN = {"atomic": "原子指标", "derived": "派生指标", "composite": "复合指标"}
def _metric_cards(question, ir):
    """A3 口径卡:问句命中的指标 → 名称/分层/口径说明/数据出处(表.列)/单位,随答案展示。"""
    cards = []
    for k, arr in (ir.get("metric_layers") or {}).items():
        for m in arr:
            n = (m.get("name") or "").strip()
            if len(n) >= 2 and n in question:
                cards.append({"name": n, "layer": _METRIC_LAYER_CN.get(k, k),
                              "table": m.get("table") or "", "col": m.get("value_col") or "",
                              "unit": m.get("unit") or "", "desc": m.get("desc") or ""})
    return cards[:4]

_PRONOUN = re.compile(r"它|这个|该|上述|同样|这些|其中|再看|还有|呢[??]?$")
def _carryover(question, history, ir):
    """B5 多轮指代:问句含指代词(或极短追问)时,把上文命中的本体对象显式延续进本问。
    本体即实体注册表 —— 不做通用 NLP,只在图谱对象里找。→ (增强问句, 延续对象串)"""
    if not history: return question, ""
    if not (_PRONOUN.search(question) or len(question) <= 12): return question, ""
    prev = " ".join(h.get("q", "") for h in history[-2:])
    hits = []
    for o in ir.get("objects", []):
        cn = (o.get("cn") or "").strip()
        cands = {cn, o.get("table") or ""}
        for suf in ("事实表", "维度表", "汇总表", "明细表", "表"):   # 「销售订单事实表」也按「销售订单」匹配
            if cn.endswith(suf) and len(cn) > len(suf) + 1: cands.add(cn[:-len(suf)])
        for c in cands:
            if c and len(c) >= 2 and c in prev:
                nm = cn or o.get("table")
                if nm and nm not in hits: hits.append(nm)
                break
        if len(hits) >= 3: break
    if not hits: return question, ""
    return question + "(指代延续:上文对象 " + "、".join(hits) + ")", "、".join(hits)

def agent_sql_plan(question, context, steps):
    try:
        from agent_runtime import get_runtime, available
    except Exception as e:
        steps.append({"step": "agent_runtime", "ok": False, "info": str(e)[:100]}); return None
    prompt = f"""你是数据分析引擎。基于 SQLite 库(方言:SQLite,日期是 TEXT 'YYYY-MM-DD')回答业务问题。
问题: {question}
可用表结构:
{context}
只输出一个 JSON(不要其它文字): {{"analyses":[{{"title":"...","sql":"SELECT ...","chart":{{"type":"line|bar|pie","x":"列名","y":["列名"]}}}}], "note":"一句话分析思路"}}
要求: 最多3个分析;SQL 只用上面列出的表列;若上下文给出「表间关系(⋈)」,跨表 JOIN 必须优先使用这些键,不要自造 JOIN 条件;聚合趋势用 substr(日期列,1,7) 按月;LIMIT 500 以内;「⋈⋈」为两跳链,须完整沿链 JOIN 中间表;均值口径:『月均/日均』分母用 count(DISTINCT 期间),不得除以固定常数;『最…的』单值问题:JOIN 后聚合再 ORDER BY … LIMIT 1。"""
    for drv in _drv_order():
        try:
            if drv not in available(): continue
            t0 = time.time()
            rt = get_runtime(drv)
            ok, reply = _llm_turn(rt, f"dm_{uuid.uuid4().hex[:6]}", prompt, _llm_timeout(), task="plan")
            steps.append({"step": f"llm_plan({_eng_label(drv)})", "ok": bool(ok), "info": f"{time.time()-t0:.1f}s {len(reply or '')}字符"})
            if ok and reply:
                m = re.search(r"\{[\s\S]*\}", reply)
                if m:
                    try: return json.loads(m.group(0))
                    except Exception: pass
        except Exception as e:
            steps.append({"step": f"llm_plan({_eng_label(drv)})", "ok": False, "info": str(e)[:120]})
    return None

def fallback_plan(question):
    """无引擎时的内置模板,保证可用"""
    p = []
    # 高频的「客户 × 销售订单」不能被宽泛的“销售”词误降成月度收入趋势。
    # 引擎超时时，兜底也必须回答原问题，而不是仅仅返回一条能执行的 SQL。
    if re.search(r"客户|customer|cust", question, re.I) and re.search(r"排名|排行|金额|销售订单", question):
        p.append({"title": "客户销售订单金额排名",
                  "sql": "SELECT c.cust_name 客户, round(sum(s.amount),2) 订单金额 FROM fact_sales_order s JOIN dim_customer c ON s.cust_id=c.cust_id GROUP BY c.cust_id,c.cust_name ORDER BY 订单金额 DESC LIMIT 50",
                  "chart": {"type": "bar", "x": "客户", "y": ["订单金额"]}})
        return {"analyses": p, "note": "内置模板(问数引擎离线兜底)"}
    if re.search(r"毛利|利润|margin", question):
        p.append({"title": "月度毛利与毛利率", "sql": "SELECT substr(order_date,1,7) 月, round(sum(gross_profit_actual)/10000,1) 毛利_万, round(sum(gross_profit_actual)*100.0/sum(amount),1) 毛利率_pct FROM fact_sales_order GROUP BY 1 ORDER BY 1", "chart": {"type": "line", "x": "月", "y": ["毛利_万", "毛利率_pct"]}})
    if re.search(r"收入|销售|营收", question):
        p.append({"title": "月度收入", "sql": "SELECT substr(order_date,1,7) 月, round(sum(amount)/10000,1) 收入_万 FROM fact_sales_order GROUP BY 1 ORDER BY 1", "chart": {"type": "bar", "x": "月", "y": ["收入_万"]}})
    if re.search(r"产量|生产", question):
        p.append({"title": "月度产量", "sql": "SELECT substr(output_date,1,7) 月, round(sum(quantity),0) 产量 FROM fact_production_output GROUP BY 1 ORDER BY 1", "chart": {"type": "line", "x": "月", "y": ["产量"]}})
    if not p:
        p.append({"title": "销售概览", "sql": "SELECT substr(order_date,1,7) 月, round(sum(amount)/10000,1) 收入_万, round(sum(gross_profit_actual)/10000,1) 毛利_万 FROM fact_sales_order GROUP BY 1 ORDER BY 1", "chart": {"type": "line", "x": "月", "y": ["收入_万", "毛利_万"]}})
    return {"analyses": p, "note": "内置模板(问数引擎离线兜底)"}

def _rule_summary(results):
    """规则化数据摘要(引擎离线/超时的兜底,始终基于真实数据,不编造)"""
    outs = []
    for r in results:
        rows = r["data"]["rows"]
        if rows and len(rows) >= 2:
            outs.append(f"「{r['title']}」共 {len(rows)} 条结果,末行 {json.dumps(rows[-1], ensure_ascii=False)}")
        elif rows:
            outs.append(f"「{r['title']}」{json.dumps(rows[0], ensure_ascii=False)}")
    return ("数据摘要:" + ";".join(outs)) if outs else "已取到数据,请展开各分析查看明细。"

# _ERR_REPLY / _looks_like_error 已收敛到 srv_engine(引擎回复语义,跨簇共用;见文件头 import)

def narrative_llm(question, results, steps, emit=None):
    """引擎生成业务洞察;成功返回文本,失败/离线/引擎报错返回 None(由调用方兜底为 _rule_summary)。
    emit(片段) 非空且引擎支持流式(claude-code stream-json)时逐段回调 —— B6 叙述流式;
    其它引擎自动回落一次性(emit 收到整段)。"""
    from agent_runtime import get_runtime, available
    # 长序列取「首3 + 末7 行」而非只取前6——否则时序按月升序时 LLM 只看到最早几期、看不到最新期(如毛利率末期骤降),
    # 会写出与图表矛盾、且把最早几期误当"最近"的洞察。末尾即最新期,是趋势/异常的关键。
    def _samp(rows): return rows if len(rows) <= 10 else (rows[:3] + rows[-7:])
    data_brief = json.dumps([{"title": r["title"], "总行数": len(r["data"]["rows"]), "样本(首3+末7,末尾为最新期)": _samp(r["data"]["rows"])} for r in results], ensure_ascii=False)[:3200]
    prompt = f"问题: {question}\n各分析结果样本(长序列取首尾,末尾为最新期): {data_brief}\n用中文给出 3-5 句结论式业务洞察,**重点关注最新期(数据末尾)的变化与异常**(基于数据,不要编造),直接输出文本。"
    for drv in _drv_order():
        if drv not in available(): continue
        rt = get_runtime(drv)
        sid = f"dmn_{uuid.uuid4().hex[:6]}"
        if emit is not None and drv == "claude-code" and hasattr(rt, "stream_turn"):
            m = _task_model("narrative")
            def _push(ev):
                if ev.get("type") == "text" and ev.get("text"): emit(ev["text"])
            try:
                try: ok, reply = rt.stream_turn(_push, sid, prompt, timeout=45, model=m)
                except TypeError: ok, reply = rt.stream_turn(_push, sid, prompt, timeout=45)
            except Exception as e:
                ok, reply = False, str(e)
        else:
            ok, reply = _llm_turn(rt, sid, prompt, 45, task="narrative")
            if ok and reply and emit is not None and not _looks_like_error(reply): emit(reply.strip()[:1500])
        if ok and reply and not _looks_like_error(reply):
            steps.append({"step": f"llm_insight({_eng_label(drv)})", "ok": True, "info": f"{len(reply)}字符"})
            return reply.strip()[:1500]
        if reply and _looks_like_error(reply):
            steps.append({"step": f"llm_insight({_eng_label(drv)})", "ok": False, "info": f"引擎报错,跳过: {reply.strip()[:80]}"})
    return None

# ══════════ 路由 ══════════
@app.get("/")
def index():
    response = send_from_directory(os.path.join(HERE, "ui"), "index.html")
    # 单页应用与后端必须作为同一版本加载。曾出现旧进程仍在、index.html 已更新，
    # 浏览器拿到新版 HTML 后却请求了旧进程尚未注册的模块路由，最终只剩一个不能
    # 正常新建问数的半渲染页面。入口和自研模块都禁止中间缓存，重启后立即收敛。
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/api/uiver")
def ui_version():
    """界面文件版本(mtime)。开着的标签页据此发现自己是旧版——
    我们改了界面而用户没刷新时,现象是「说改了却没生效」,排查成本很高。"""
    try:
        ui_files = [os.path.join(HERE, "ui", "index.html")]
        ui_files += glob.glob(os.path.join(HERE, "ui", "modules", "*.js"))
        ui_files += glob.glob(os.path.join(HERE, "ui", "styles", "*.css"))
        response = jsonify({"v": int(max(os.path.getmtime(path) for path in ui_files))})
        response.headers["Cache-Control"] = "no-store"
        return response
    except Exception as e:
        return jsonify({"v": 0, "error": str(e)})

@app.get("/doc/<name>")
def doc(name):
    """服务 ui/ 下的文档页(根因分析等),仅限 .html,防穿越"""
    if not re.match(r"^[A-Za-z0-9_-]+$", name): return "bad", 400
    p = _confined(os.path.join(HERE, "ui"), name + ".html")
    if not os.path.exists(p): return "not found", 404
    return send_file(p)

@app.get("/vendor/<path:f>")
def vendor(f):
    """本地静态库(echarts 等),不依赖外网 CDN"""
    # source map 一律不提供:压缩包里带着 //# sourceMappingURL 注释,浏览器会顺手来取。
    # 本仓不含 .map,但只要有人把构建产物整目录拷进来,前端库(乃至将来自研前端)的
    # 完整源码就会经这条路径泄露 —— 与其依赖"文件恰好不在",不如把这条路堵死。
    if f.lower().endswith(".map"): return "not found", 404
    base = os.path.join(HERE, "ui", "vendor")
    rp = os.path.realpath(os.path.join(base, f))
    if not rp.startswith(os.path.realpath(base) + os.sep) or not os.path.exists(rp): return "not found", 404
    return send_file(rp)


@app.get("/assets/<path:f>")
def ui_asset(f):
    """自研前端模块；仅开放 modules/*.js 与 styles/*.css，不暴露 ui 下其它文件。"""
    if ".." in f or not re.match(r"^(modules|styles)/[A-Za-z0-9_./-]+\.(js|css)$", f):
        return "not found", 404
    base = os.path.realpath(os.path.join(HERE, "ui"))
    path = os.path.realpath(os.path.join(base, f))
    if not path.startswith(base + os.sep) or not os.path.isfile(path):
        return "not found", 404
    response = send_file(path)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.get("/api/overview")
def overview():
    ir = load_ir_edited("demo") or {}
    ml = ir.get("metric_layers") or {}
    try:
        tabs = table_list()
    except Exception as e:
        # 优雅降级:库不可用时仍返回零值 KPI 让看板可渲染。用 warning 而非 error——
        # error 在全站约定中表示"请求失败",占用它会让前端守卫误判这次成功的降级响应。
        return jsonify({"warning": f"数据库不可用: {str(e)[:120]}", "kpi": {"tables": 0, "rows": 0, "metrics": 0, "objects": len(ir.get("objects", [])), "links": len(ir.get("links", []))}, "trend": [], "prod": []}), 200
    kpi = {"tables": len(tabs), "rows": sum(t["rows"] for t in tabs),
           "metrics": sum(len(v) for v in ml.values()),
           "objects": len(ir.get("objects", [])),
           "links": sum(1 for l in ir.get("links", []) if l.get("status") != "rejected")}
    def _safe(sql):
        try: return q(sql)["rows"]
        except Exception: return []
    trend = _safe("SELECT substr(order_date,1,7) m, round(sum(amount)/10000,1) rev, round(sum(gross_profit_actual)/10000,1) gp FROM fact_sales_order GROUP BY 1 ORDER BY 1")
    prod = _safe("SELECT substr(output_date,1,7) m, round(sum(quantity),0) qty FROM fact_production_output GROUP BY 1 ORDER BY 1")
    return jsonify({"kpi": kpi, "trend": trend, "prod": prod})

@app.get("/api/tables")
def tables(): return jsonify(table_list())

@app.get("/api/table/<name>")
def table_detail(name):
    if not re.match(r"^[A-Za-z0-9_]+$", name): return jsonify({"error": "bad name"}), 400
    ir = load_ir_edited("demo") or {}
    cn_map = {}
    for o in ir.get("objects", []):
        if (o.get("table") or "").lower() == name.lower():
            cn_map = {a["col"]: a.get("cn", "") for a in o.get("attrs", [])}
    con = ro_connect(DB)
    cols = [{"col": r[1], "type": r[2], "pk": bool(r[5]), "cn": cn_map.get(r[1], "")} for r in con.execute(f'PRAGMA table_info("{name}")')]
    con.close()
    if not cols: return jsonify({"error": "表不存在"}), 404
    prev = q(f'SELECT * FROM "{name}" LIMIT 20')
    return jsonify({"columns": cols, "preview": prev})

@app.get("/api/graphs")
def graphs():
    out = []
    inventory = _qa_table_inventory()
    for k, v in IR_SOURCES.items():
        ir = load_ir_edited(k)
        if ir:
            g = ir_to_graph(k, ir)
            out.append({"id": k, "name": v["name"], "nodes": len(g["nodes"]), "edges": len(g["edges"]), "cat": "curated", **_qa_graph_summary(ir, inventory)})
    for p2 in sorted(glob.glob(os.path.join(PLATFORM, "data", "forged", "*.json"))):
        k = "forged_" + os.path.basename(p2)[:-5]
        ir = load_ir_edited(k)
        if not isinstance(ir, dict): continue
        g = ir_to_graph(k, ir)
        out.append({"id": k, "name": ((ir.get("scenario") or {}).get("name") or k), "nodes": len(g["nodes"]), "edges": len(g["edges"]), "cat": "scenario", **_qa_graph_summary(ir, inventory)})
    for p in sorted(glob.glob(os.path.join(WORK, "built_*.json")), key=os.path.getmtime, reverse=True):
        k = os.path.basename(p)[:-5]
        ir = load_ir_edited(k); g = ir_to_graph(k, ir)
        out.append({"id": k, "name": (ir.get("scenario") or {}).get("name") or k, "nodes": len(g["nodes"]), "edges": len(g["edges"]), "cat": "built", **_qa_graph_summary(ir, inventory)})
    return jsonify(out)

@app.get("/api/graph/<key>")
def graph(key): return jsonify(ir_to_graph(key, load_ir_edited(key)))

@app.get("/api/ont/accuracy")
def ont_accuracy():
    """⑥ 可解释性第三问「历史上类似情况准确率?」:人审同意率(按系统原判 / 按动词)。

    工业AI 要取得工程师信任,每条输出须能答三问:基于什么数据(已有 provenance)、
    依据什么规则(已有关系接地)、**历史上类似情况准确率**(此端点)。
    样本不足时如实返回 insufficient,不给百分比。
    """
    return jsonify(_review_history(force=bool(request.args.get("force"))))

@app.get("/api/ont/rejected-patterns")
def ont_rejected_patterns():
    """人审否决过的关系模式(即注入构建提示的『已知误判模式』),供页面展示与审计。"""
    return jsonify({"patterns": _rejected_patterns(limit=int(request.args.get("limit") or 8))})

@app.get("/api/ont/review")
def ont_review():
    """人机协同人审队列:全部关系 + 状态/语义评审/取值重合/人审结论;待审 = 未人审的 candidate 或语义存疑"""
    key = request.args.get("graph", "demo")
    if _bad_gkey(key): return jsonify({"error": "bad key"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    rels, ks, kt = _rels(ir)
    disp = {}
    for o in ir.get("objects", []):
        oid = str(o.get("id") or o.get("name"))
        disp[oid] = o.get("cn") or o.get("name") or oid
    rows = []
    for l in rels:
        s, t = str(l.get(ks)), str(l.get(kt))
        st, sem, hr = l.get("status", ""), l.get("semantic", ""), l.get("human_review", "")
        rows.append({"s": s, "t": t, "sn": disp.get(s, s), "tn": disp.get(t, t),
                     "verb": l.get("verb", "关联"), "card": l.get("card", ""), "status": st,
                     "semantic": sem, "overlap": l.get("overlap", l.get("overlap_pct")),
                     "human_review": hr, "reason": l.get("review_reason", ""),
                     "by": l.get("review_by", ""), "time": l.get("review_time", ""),
                     "pending": (hr == "") and (st == "candidate" or sem == "fail")})
    rows.sort(key=lambda r: (not r["pending"], r["status"] == "rejected", r["s"]))
    objs = []
    for o in ir.get("objects", []):
        oid = str(o.get("id") or o.get("name"))
        cand = bool(o.get("candidate", False)) and not o.get("confirmed", False)
        objs.append({"id": oid, "name": o.get("cn") or o.get("name") or oid, "kind": o.get("kind", "object"),
                     "candidate": cand, "tables": [x for x in (o.get("tables") or [o.get("table")]) if x],
                     "by": o.get("review_by", ""), "reason": o.get("review_reason", ""), "time": o.get("review_time", "")})
    objs.sort(key=lambda x: (not x["candidate"], x["id"]))
    counts = {"total": len(rows), "pending": sum(1 for r in rows if r["pending"]),
              "approved": sum(1 for r in rows if r["human_review"] == "approved"),
              "rejected": sum(1 for r in rows if r["human_review"] == "rejected"),
              "disputed": sum(1 for r in rows if r["semantic"] == "fail"),
              "objects": len(objs), "obj_candidate": sum(1 for x in objs if x["candidate"])}
    return jsonify({"graph": key, "counts": counts, "rows": rows, "objects": objs})

@app.get("/api/metrics")
def metrics():
    ir = load_ir_edited("demo") or {}
    return jsonify(ir.get("metric_layers") or {})

@app.get("/api/metric/lineage")
def metric_lineage():
    name = request.args.get("name", "").strip()
    ir = load_ir_edited("demo") or {}
    hit = None
    for k, arr in (ir.get("metric_layers") or {}).items():
        for m in arr:
            if m.get("name") == name:
                hit = {**m, "layer": k}; break
        if hit: break
    if not hit: return jsonify({"error": "指标不存在"}), 404
    refs = []
    app_ir = load_ir_edited("app") or {}
    for o in app_ir.get("objects", []):
        if name in (o.get("indicators") or []):
            refs.append({"object": o["name"], "kind": o.get("kind"), "tables": o.get("tables", [])})
    # 派生/复合的输入指标
    inputs = hit.get("inputs") or []
    return jsonify({"metric": hit, "source_table": hit.get("table"), "source_col": hit.get("value_col"),
                    "referenced_by": refs, "inputs": inputs, "formula": hit.get("formula", "")})

@app.get("/api/metric/quick")
def metric_quick():
    """即时问数:指标 → 自动生成月度聚合 SQL → 秒出数据(不走 LLM)"""
    name = request.args.get("name", "").strip()
    ir = load_ir_edited("demo") or {}
    hit = None
    for k, arr in (ir.get("metric_layers") or {}).items():
        for m in arr:
            if m.get("name") == name: hit = {**m, "layer": k}; break
        if hit: break
    if not hit or not hit.get("table"): return jsonify({"error": "指标不存在或未绑表"}), 404
    # 表名/取值列取自 IR。IR 可经 /api/ont/apply 编辑,故对本端点而言是**用户可控**的,
    # 而它们直接落在 SQL 的标识符位上 —— 必须过 _safe_ident 白名单,不能只靠"来自 IR"这层假设。
    tbl, vcol = _safe_ident(hit["table"]), _safe_ident(hit.get("value_col"))
    if not tbl: return jsonify({"error": "指标绑定的表名非法"}), 400
    # 找该表日期列(IR attrs 中 DATE 类型优先,退而求 *date* 命名)
    dcol = None
    for o in ir.get("objects", []):
        if (o.get("table") or "").lower() == tbl.lower():
            dates = [a["col"] for a in o.get("attrs", []) if "DATE" in (a.get("type", "").upper())]
            named = [a["col"] for a in o.get("attrs", []) if "date" in a["col"].lower()]
            dcol = _safe_ident((dates or named or [None])[0])
    if not (vcol and dcol):
        return jsonify({"error": f"缺日期列或取值列(date={dcol}, value={vcol})"}), 400
    # 聚合口径:比率/百分比 → 平均(不可求和);存量/快照(余额/库存/在册/期末/人数)→ 月均(按日求和会 ~30x 高估);流量 → 求和
    mtype, unit = (hit.get("type") or ""), (hit.get("unit") or "")
    is_ratio = ("比率" in mtype) or ("占比" in mtype) or unit.strip() == "%" or bool(re.search(r"率|占比|比率|均", name))
    is_stock = bool(re.search(r"余额|库存|在册|期末|存量|头寸|人数|结存|在制", name))
    if is_ratio: agg, agg_note = "avg", "比率→月均(该列为逐行率值,取月度均值近似)"
    elif is_stock: agg, agg_note = "avg", "存量/快照→月均(按日求和会高估,故取均值)"
    else: agg, agg_note = "sum", "流量→月度求和"
    # 结果列别名直接来自 query string,可含任意字符(中文指标名合法,故不能套 _safe_ident);
    # 用 _quote_ident 把内嵌的 " 成对转义,别名便无法越出引号边界闭合出新的 SQL 片段。
    sql = (f'SELECT substr("{dcol}",1,7) 月, round({agg}("{vcol}"),2) {_quote_ident(name)} '
           f'FROM "{tbl}" GROUP BY 1 ORDER BY 1')
    try:
        data = q(sql)
        return jsonify({"metric": name, "unit": unit, "layer": hit["layer"],
                        "sql": sql, "agg": agg, "agg_note": agg_note, "source": f"{tbl}.{vcol}", "data": data})
    except Exception as e:
        return jsonify({"error": str(e), "sql": sql}), 400

@app.get("/api/table/<name>/info")
def table_info(name):
    """表基本信息:行/列 + 所属本体对象 + 支撑指标(iip 元数据详情范式)"""
    if not re.match(r"^[A-Za-z0-9_]+$", name): return jsonify({"error": "bad name"}), 400
    con = ro_connect(DB)
    try:
        rows = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        ncols = len(con.execute(f'PRAGMA table_info("{name}")').fetchall())
    except Exception:
        return jsonify({"error": "表不存在"}), 404
    finally:
        con.close()
    app_ir = load_ir_edited("app") or {}
    objs = [{"name": o["name"], "kind": o.get("kind")} for o in app_ir.get("objects", [])
            if name.lower() in [t.lower() for t in o.get("tables", [])]]
    ir = load_ir_edited("demo") or {}
    mets = [{"name": m.get("name"), "layer": k} for k, arr in (ir.get("metric_layers") or {}).items()
            for m in arr if (m.get("table") or "").lower() == name.lower()]
    return jsonify({"table": name, "rows": rows, "cols": ncols, "objects": objs, "metrics": mets})

# ══ M1: serve_claw 能力原生吸收(不依赖 8091) ══
def _edits_path(key):
    if _bad_gkey(key): key = "__invalid__"      # 非法键落到固定安全名,绝不逃逸 WORK 目录(防写穿越)
    return _confined(WORK, f"edits_{key}.json")  # sink 处再裁一次,纵深防御
def _load_edits(key):
    path = _edits_path(key)
    if not os.path.exists(path):
        return {"version": 1, "ops": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)
REVIEW_OPS = ("confirm_relation", "reject_relation")   # 人机协同人审:通过(→asserted)/否决(→剔除)
# DataMind 本地算子:apply_any 里自己实现、完全不依赖上游引擎的那些。
# 引擎离线时这些必须照常放行 —— 曾经只列了 set_alias,导致本地已实现的改动词/改基数/
# 增删关系被 503 挡回「编辑引擎未就绪」,而它们根本不需要引擎。test_all.py 的
# QS11 用 AST 核对本表与 apply_any 的实际分支一致,防止再次漂移。
LOCAL_OPS = ("set_alias", "confirm", "remove_object", "verb", "set_card",
             "remove_relation", "add_relation")

def _rels(ir):
    """关系列表 + 端点键名(IR 形状兼容规则收于 ir_shape,此处保留 create 语义)。"""
    return ir_shape.rels(ir, create=True)

def _okey(ir, s):
    """把对象的任意指代(主键/中文名/表名/别名)规范化为主键;找不到就原样返回。"""
    o = _find_obj_any(ir, s)
    return str(o.get("id") or o.get("name")) if o else str(s)

def _find_rel_any(ir, rid):
    m = re.match(r"^(.+?)->(.+)$", (rid or "").replace("rel:", "", 1))
    if not m: return None
    rels, ks, kt = _rels(ir)
    # 端点先规范化:关系里存的是主键,而对话里给的常是中文名
    src, dst = _okey(ir, m.group(1)), _okey(ir, m.group(2))
    for l in rels:
        if str(l.get(ks)) == src and str(l.get(kt)) == dst: return l
    return None

def _find_obj_any(ir, oid):
    """按主键定位对象;主键不中时再按中文名、表名、业务别名找。

    对话式改本体时,人和模型都会用中文名指代(如 obj:销售订单),而主键是英文
    (SalesOrder)—— 只认主键会让「照着助手的提议点确认」直接报「对象不存在」。
    仅在主键无匹配时才降级匹配,避免中文名重名时抢掉精确命中。"""
    key = str(oid); low = key.lower()
    objs = ir.get("objects", [])
    # 按条件分轮,而不是逐对象把三种条件一起试:后者会让靠前对象的表名
    # 压过靠后对象的中文名,命中谁取决于对象顺序,不可预期
    for probe in (lambda o: str(o.get("id") or o.get("name")) == key,
                  lambda o: str(o.get("cn") or "") == key,
                  lambda o: str(o.get("table") or "").lower() == low,
                  lambda o: any(str(a) == key for a in (o.get("aliases") or []))):
        for o in objs:
            if probe(o): return o
    return None

def _stamp_review(x, op):
    """把评审人/意见/时间盖到元素上(op 内字段随编辑日志持久化,回放确定)"""
    reason = (op.get("reason") or "").strip()
    if reason: x["review_reason"] = reason
    if op.get("reviewer"): x["review_by"] = op["reviewer"]
    if op.get("ts"): x["review_time"] = op["ts"]

def apply_any(ir, op):
    """白名单编辑统一入口:关系类算子(人审通过/否决 + 动词/基数/增删)与对象确认/删除本地实现、
    两种 IR 形状通吃;其余算子(属性类等)沿用平台 apply_op。人审规范:人只产生 asserted,永不冒充 verified(错误关系控制)。"""
    kind = op.get("op"); t = op.get("target", "")
    params = op.get("params") or {}; reason = (op.get("reason") or "").strip()
    if kind == "confirm":                      # 确认候选对象(两种形状)
        o = _find_obj_any(ir, t.replace("obj:", "", 1))
        if not o: raise ValueError(f"对象不存在: {t}")
        o["confirmed"] = True
        if "candidate" in o: o["candidate"] = False
        _stamp_review(o, op)
        return
    if kind == "set_alias":                    # DR-027 业务别名:让业务用语可锚定到本体对象
        o = _find_obj_any(ir, t.replace("obj:", "", 1))
        if not o: raise ValueError(f"对象不存在: {t}")
        raw = params.get("aliases")
        if isinstance(raw, str): raw = [x for x in re.split(r"[,,、;;\s]+", raw) if x]
        if not isinstance(raw, list): raise ValueError("params.aliases 需为列表或分隔字符串")
        # 上限在去重前校验:否则「21 个相同别名」去重后剩 1 个而绕过限制,
        # 大批量输入即可绕开防线(去重是清洗,不是防线)
        if len(raw) > 20: raise ValueError("别名过多(上限 20)")
        seen, out = set(), []
        for a in raw:
            a = str(a).strip()[:40]
            # 与对象自身名称重复的别名无意义(锚定本就能命中),去重后丢弃
            if not a or a in seen or a in (o.get("cn"), o.get("name"), o.get("id"), o.get("table")):
                continue
            seen.add(a); out.append(a)
        o["aliases"] = out
        _stamp_review(o, op)
        return
    if kind == "remove_object":                # 删对象(两种形状),级联删其关系
        o = _find_obj_any(ir, t.replace("obj:", "", 1))
        if not o: raise ValueError(f"对象不存在: {t}")
        # 用对象自身的主键做级联,不能用调用方传来的指代 —— 传中文名时二者不同,
        # 拿中文名去筛关系会一条都匹配不上,删完对象留下悬空关系
        oid = str(o.get("id") or o.get("name"))
        ir["objects"].remove(o)
        rels, ks, kt = _rels(ir)
        rels[:] = [l for l in rels if str(l.get(ks)) != str(oid) and str(l.get(kt)) != str(oid)]
        return
    if kind in ("confirm_relation", "reject_relation", "verb", "set_card", "remove_relation", "add_relation"):
        rels, ks, kt = _rels(ir)
        if kind == "add_relation":
            m = re.match(r"^(.+?)->(.+)$", t.replace("rel:", "", 1))
            if not m: raise ValueError("add_relation 目标格式: rel:<source>-><target>")
            src, dst = _okey(ir, m.group(1)), _okey(ir, m.group(2))
            ids = {str(o.get("id") or o.get("name")) for o in ir.get("objects", [])}
            if src not in ids or dst not in ids: raise ValueError("源/目标对象不存在")
            if src == dst: raise ValueError("不允许自环关系")
            if _find_rel_any(ir, t): raise ValueError("该关系已存在")
            verb = (params.get("verb") or "关联").strip()
            if not verb or len(verb) > 12 or re.search(r"[<>\"'&]", verb): raise ValueError("verb 1~12字, 不含 < > \" ' &")
            card = params.get("card") if params.get("card") in ("1:N", "N:1", "1:1", "N:N") else "N:1"
            nl = {ks: src, kt: dst, "verb": verb, "card": card, "status": "asserted",
                  "human_review": "approved", "review_reason": reason or "人工新增"}
            _stamp_review(nl, op)
            rels.append(nl)
            return
        l = _find_rel_any(ir, t)
        if not l: raise ValueError(f"关系不存在: {t}(格式 rel:<source>-><target>)")
        if kind == "confirm_relation":     # 人审通过:candidate/gap/rejected → asserted;verified 只记通过、不动状态
            l.setdefault("review_prior_status", l.get("status"))   # 原判在此刻定格:改写 status 后就再也反推不出来
            if l.get("status") != "verified": l["status"] = "asserted"
            l["human_review"] = "approved"
            _stamp_review(l, op)
            return
        if kind == "reject_relation":      # 人审否决:标 rejected,渲染剔除;记录留痕,可撤销
            l.setdefault("review_prior_status", l.get("status"))   # 同上:先存原判,再改写
            l["status"] = "rejected"; l["human_review"] = "rejected"
            _stamp_review(l, op)
            return
        if kind == "verb":
            verb = (params.get("verb") or "").strip()
            if not verb or len(verb) > 12 or re.search(r"[<>\"'&]", verb): raise ValueError("verb 需要 1~12 字中文动词, 不含 < > \" ' &")
            l["verb"] = verb
            _stamp_review(l, op)
            return
        if kind == "set_card":
            card = params.get("card")
            if card not in ("1:N", "N:1", "1:1", "N:N"): raise ValueError("card 须为 1:N/N:1/1:1/N:N")
            l["card"] = card
            return
        if kind == "remove_relation":
            rels.remove(l)
            return
    import serve_claw as SC
    return SC.apply_op(ir, op)

def load_ir_edited(key):
    """IR + 编辑日志回放(草案层,不动构建产物;与 serve_claw 同机制)"""
    ir = load_ir(key)
    if not ir: return None
    ir = json.loads(json.dumps(ir))
    try:
        for op in _load_edits(key)["ops"]:
            try: apply_any(ir, op)
            except Exception: pass
    except Exception: pass
    return ir

@app.post("/api/ont/apply")
def ont_apply():
    """白名单编辑(confirm/rename/verb/add_*/remove_*/set_*),按图谱记操作日志,可撤销"""
    body = request.json or {}
    key, op = body.get("graph", "demo"), body.get("op") or {}
    _kind = op.get("op")
    try:
        import serve_claw as SC
        _allowed = tuple(SC.ALLOWED_OPS) + REVIEW_OPS + LOCAL_OPS
    except Exception as e:
        # 引擎缺失时仍放行本地算子:别名/人审是 DataMind 自有能力,不该被上游离线卡住
        if _kind not in (REVIEW_OPS + LOCAL_OPS):
            return jsonify({"error": f"编辑引擎未就绪: {str(e)[:120]}"}), 503
        _allowed = REVIEW_OPS + LOCAL_OPS
    if _kind not in _allowed:
        return jsonify({"error": f"非白名单操作: {_kind}"}), 400
    reviewer = (body.get("reviewer") or op.get("reviewer") or "").strip()[:40]
    if reviewer: op["reviewer"] = reviewer
    op.setdefault("ts", time.strftime("%Y-%m-%d %H:%M"))
    # DR-027 审计来源:chat=对话建议被人采纳 / review=评审台人工发起 / api=外部直调。
    # 必须可区分——「AI 提的被采纳」与「人自己决定的」责任归属不同,审计要分得开。
    src = (body.get("source") or op.get("source") or "api").strip()[:20]
    op["source"] = src if src in ("chat", "review", "graph", "api") else "api"
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try: apply_any(ir, op)
    except Exception as e: return jsonify({"error": f"应用失败: {e}"}), 400
    with _WRITE_LOCK:
        ed = _load_edits(key); ed["ops"].append(op)
        _atomic_json(_edits_path(key), ed)
        n = len(ed["ops"])
    return jsonify({"ok": True, "ops": n})

@app.post("/api/ont/undo")
def ont_undo():
    key = (request.json or {}).get("graph", "demo")
    with _WRITE_LOCK:
        ed = _load_edits(key)
        if not ed["ops"]: return jsonify({"error": "无可撤销操作"}), 400
        last = ed["ops"].pop()
        _atomic_json(_edits_path(key), ed)
        n = len(ed["ops"])
    return jsonify({"ok": True, "undone": last, "ops": n})

@app.get("/api/ont/edits")
def ont_edits():
    return jsonify(_load_edits(request.args.get("graph", "demo")))

# 铸造产物落盘位置。历史上写在上游引擎目录下,独立运行(引擎目录不存在或只读)时
# forge 会以 500 裸栈失败 —— 而独立运行正是本仓的默认形态。改为:装了引擎仍写引擎目录
# (老产物原地可用),否则写自己的 workdir。读取始终并两处,升级不丢已铸本体。
_FORGED_ENGINE = os.path.join(PLATFORM, "data", "forged")
FORGED_DIR = _FORGED_ENGINE if os.path.isdir(PLATFORM) else os.path.join(WORK, "forged")

def _forged_dirs():
    """读取时要看的目录:当前写入目录 + 引擎目录(去重,只保留真实存在的)"""
    out = []
    for d in (FORGED_DIR, _FORGED_ENGINE):
        if d not in out and os.path.isdir(d): out.append(d)
    return out

def _safe_fname(name):
    """把任意输入裁成单一安全文件名组件:丢掉目录部分与路径穿越符,非法则落回占位名。
    供 _forged_path / _custom_skill_path 在 sink 处再裁一次,调用方已校验时也多一道关。"""
    n = os.path.basename(str(name or ""))
    if not n or n in (".", "..") or "/" in n or "\\" in n or ".." in n or n.startswith("."):
        return "__invalid__"
    return n

def _forged_path(fid):
    """按 fid 找已存在的产物;都不存在时返回当前写入目录下的路径(供新建)。
    fid 经 _safe_fname 裁为单组件并过 _confined,杜绝穿越(纵深防御)。"""
    fid = _safe_fname(fid)
    for d in _forged_dirs():
        p = _confined(d, fid + ".json")
        if os.path.exists(p): return p
    return _confined(FORGED_DIR, fid + ".json")
@app.get("/api/ont/forged")
def ont_forged():
    out, seen = [], set()
    for dirp in _forged_dirs():
        for p2 in sorted(glob.glob(os.path.join(dirp, "*.json"))):
            fid = os.path.basename(p2)[:-5]
            if fid in seen: continue          # 同 id 以先扫到的(当前写入目录)为准
            seen.add(fid)
            try:
                doc = json.load(open(p2)); sc = doc.get("scenario") or {}
                out.append({"id": fid, "name": sc.get("name") or doc.get("name"),
                            "objects": len(doc.get("objects", [])), "links": len(doc.get("links", []))})
            except Exception: pass
    return jsonify({"ontologies": out})

@app.get("/api/ont/forged/<fid>")
def ont_forged_one(fid):
    if not re.match(r"^[\w\-\u4e00-\u9fff·]+$", fid): return jsonify({"error": "bad id"}), 400
    p2 = _forged_path(fid)
    if not os.path.exists(p2): return jsonify({"error": "不存在"}), 404
    return jsonify(json.load(open(p2)))

@app.post("/api/ont/save")
def ont_save():
    """保存当前(含编辑)IR 为版本 → 平台本体库 data/forged/(与 serve_claw 同存储)"""
    body = request.json or {}
    key, name = body.get("graph", "demo"), (body.get("name") or "").strip()
    if not name or len(name) > 40: return jsonify({"error": "需要 name(≤40字)"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    fid = "onto_" + uuid.uuid4().hex[:8]
    ir.setdefault("scenario", {})["name"] = name
    os.makedirs(FORGED_DIR, exist_ok=True)
    with _WRITE_LOCK:                                    # 原子写 + 串行化,避免中途崩溃截断已存本体版本(对齐 DR-006 写入原子性)
        _atomic_json(os.path.join(FORGED_DIR, fid + ".json"), ir)
    return jsonify({"ok": True, "id": fid, "name": name})

@app.post("/api/ont/forged/delete")
def ont_forged_delete():
    fid = (request.json or {}).get("id", "")
    if not re.match(r"^[\w\-]+$", fid): return jsonify({"error": "bad id"}), 400
    p2 = _forged_path(fid)
    if not os.path.exists(p2): return jsonify({"error": "不存在"}), 404
    os.remove(p2)
    # 同删 .ttl 伴生文件,避免孤儿。按 json 的实际所在目录取 —— 产物可能在引擎目录,
    # 而写入目录是 workdir,拿写入目录去拼会漏删
    ttl = p2[:-5] + ".ttl"
    if os.path.exists(ttl): os.remove(ttl)
    return jsonify({"ok": True})

@app.get("/api/ont/runtimes")
def ont_runtimes():
    try:
        from agent_runtime import available
        av = available()
        cur = os.environ.get("CLAW_DRIVER") or "hermes"
        # current 只是回显 CLAW_DRIVER,不代表它真的注册了。配错时(如把 CLAW_DRIVER 设成
        # openai 却漏配端点)界面会显示「当前:openai」而实际不工作 —— 必须如实标注。
        return jsonify({"runtimes": av, "current": cur, "current_ready": cur in av,
                        "hint": ("" if cur in av else
                                 "CLAW_DRIVER=%s 未注册;当前可用:%s。"
                                 "接 OpenAI 兼容端点需同时配 DATAMIND_LLM_BASE 与 DATAMIND_LLM_KEY。"
                                 % (cur, "、".join(av) or "无"))})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.get("/api/ont/skill/<name>")
def ont_skill_detail(name):
    if not re.match(r"^[\w\-]+$", name): return jsonify({"error": "bad"}), 400
    item = skill_registry.find(name, _BUILTIN_SKILL_ROOTS)
    if not item: return jsonify({"error": "不存在"}), 404
    return jsonify({"name": name, "content": open(item["skill_md"], encoding="utf-8", errors="replace").read()})

# _atomic_json / _atomic_text 已收敛到 srv_context(见文件头 import)
CHATS_F = os.path.join(WORK, "ont_chats.json")
def _chats():
    if not os.path.exists(CHATS_F): return {}
    try:
        with open(CHATS_F) as fp: return json.load(fp)
    except Exception:
        return {}
def _chats_w(d):
    _atomic_json(CHATS_F, d)

@app.get("/api/ont/chats")
def ont_chats():
    key = (request.args.get("graph") or "").strip()
    if key and _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    rows = [{"id": k, "title": v.get("title", ""), "n": len(v.get("messages", [])),
             "graph": v.get("graph") or "demo"} for k, v in _chats().items()]
    return jsonify([row for row in rows if not key or row["graph"] == key])

@app.post("/api/ont/chats/new")
def ont_chats_new():
    key = str((request.json or {}).get("graph") or "demo").strip()
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    cid = "c" + uuid.uuid4().hex[:10]
    with _WRITE_LOCK:
        d = _chats(); d[cid] = {"title": "", "messages": [], "graph": key}; _chats_w(d)
    return jsonify({"id": cid, "graph": key})

@app.post("/api/ont/chat")
def ont_chat():
    """原生对话式本体完善:agent_runtime(hermes/claude-code)+ 本体上下文;编辑建议以 op JSON 返回由前端确认执行"""
    body = request.json or {}
    cid, msg, key = body.get("id", ""), (body.get("message") or "").strip(), body.get("graph", "demo")
    if not msg: return jsonify({"error": "empty"}), 400
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    d = _chats()
    sid = cid or "c_default"
    if sid in d and (d[sid].get("graph") or "demo") != key:
        return jsonify({"error": "会话所属图谱与当前图谱不一致，请新建会话"}), 409
    sess = d.setdefault(sid, {"title": "", "messages": [], "graph": key})
    sess["graph"] = key
    def _od(o):
        al = "、".join(o.get("aliases") or [])
        return f'{o.get("cn") or o.get("name")}({o.get("id")}{"|别称:" + al if al else ""})'
    objs = "; ".join(_od(o) for o in ir.get("objects", [])[:60])
    prompt = f"""你是本体治理助手。当前图谱[{key}]对象: {objs}
历史: {json.dumps(sess["messages"][-4:], ensure_ascii=False)[:800]}
用户: {msg}

可用编辑算子(仅这些,不得杜撰):
  rename        改中文名          params: {{"cn": "新名"}}
  set_alias     设业务别名(重要)   params: {{"aliases": "别名1,别名2"}}
  confirm       确认候选对象       params: {{}}
  verb          改关系动词         target: "rel:<源>-><目标>", params: {{"verb": "动词"}}
  add_relation  新增关系          target: "rel:<源>-><目标>", params: {{"verb": "动词"}}
  remove_object / remove_relation  删除(不可逆,需谨慎)

规范:人工确认只产生 asserted,**永不指定 verified**(verified 只能由数据裁决产生)。
若业务用语与对象中文名不同(如业务说「产量」而对象叫「生产日汇总」),优先建议 set_alias。

若用户要求修改本体,回答末尾附一行 EDIT_OP:{{"op":"...","target":"obj:<id>","params":{{...}},"reason":"改动依据"}} 供人确认后执行;
否则直接中文回答(基于给出的对象,不编造)。"""
    reply = None
    try:
        from agent_runtime import available
        for drv in _drv_order():
            if drv not in available(): continue
            ok, r = runtime_cached(drv).run_turn(cid or "c_default", prompt, timeout=120)   # 缓存实例→多轮续接
            if ok and r: reply = r.strip(); break
    except Exception as e:
        reply = f"(引擎异常: {e})"
    reply = reply or "(引擎离线)"
    op = None
    m = re.search(r"EDIT_OP:(\{.*\})", reply, re.S)
    if m:
        try: op = json.loads(m.group(1))
        except Exception: pass
    sess["messages"] += [{"role": "user", "text": msg}, {"role": "ai", "text": reply}]
    sess["title"] = sess["title"] or msg[:24]
    with _WRITE_LOCK:   # 重读→只更新本会话→写回,避免并发覆盖其它会话
        cur = _chats(); cur[sid] = sess; _chats_w(cur)
    return jsonify({"reply": reply, "suggested_op": op})

@app.post("/api/ont/skills/install")
def ont_skill_install():
    """装技能到 agent 工作区(openclaw workspace),对齐平台 skills/install"""
    slug = (request.json or {}).get("slug", "")
    # \w 含中文与下划线但不含 / \ .,故 slug 天然是单一目录名;下方 _confined 在
    # rmtree/copytree 这两个**破坏性** sink 前再裁一次——此处一旦逃逸就是任意目录删除。
    if not re.match(r"^[\w\-]+$", slug): return jsonify({"error": "bad slug"}), 400
    item = skill_registry.find(slug, _BUILTIN_SKILL_ROOTS)
    if not item: return jsonify({"error": "技能不存在"}), 404
    src = item["directory"]
    skill_root = os.path.expanduser("~/.openclaw/workspace/skills")
    dst = _confined(skill_root, slug)
    import shutil
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return jsonify({"ok": True, "installed": dst})

@app.post("/api/ont/skills/write")
def ont_skill_write():
    """写/建 SKILL.md(技能库编辑,对齐平台 skills/write)"""
    body = request.json or {}
    name, content = body.get("name", ""), body.get("content", "")
    if not re.match(r"^[\w\-]+$", name) or not content.strip(): return jsonify({"error": "需要合法 name+content"}), 400
    # 装了引擎就写引擎的技能库(编辑上游技能),否则写自己的 custom_skills。
    # 此前无条件 makedirs 到引擎目录:父目录可写时会凭空造出一棵假引擎目录树
    # (随后 /api/build/skills 就把这个空壳当成已装引擎),只读位置则 500 裸栈。
    if os.path.isdir(PLATFORM):
        d = _confined(os.path.join(PLATFORM, "web", "skills_seed"), name)
        scope = "engine"
    else:
        d = _BUILD_SKILL_D
        scope = "custom"
    try:
        os.makedirs(d, exist_ok=True)
        # 目录与文件名两级都过 _confined:name 已过 ^[\w\-]+$,这里是 sink 处的纵深防御
        path = _confined(d, "SKILL.md" if scope == "engine" else name + ".md")
        _atomic_text(path, content)
    except OSError as e:
        return jsonify({"error": "技能写入失败:%s" % str(e)[:120]}), 500
    return jsonify({"ok": True, "path": path, "scope": scope})

@app.post("/api/ont/chat/stream")
def ont_chat_stream():
    """SSE 流式对话:阶段事件 + 最终回复(引擎仍阻塞,流式透出状态)"""
    body = request.json or {}
    cid, msg, key = body.get("id", ""), (body.get("message") or "").strip(), body.get("graph", "demo")
    atts = body.get("attachments") or []
    from flask import Response, stream_with_context
    import queue as _q
    def gen():
        yield 'data: {"type":"status","text":"加载本体上下文…"}\n\n'
        att_note = ""
        updir = os.path.join(WORK, "chat_uploads"); os.makedirs(updir, exist_ok=True)
        for a in atts[:5]:
            try:
                import base64
                # 先按字符白名单洗一遍(去掉分隔符),再 _safe_fname 挡住 ".."/隐藏文件这类
                # 白名单洗不掉的形态,最后 _confined 保证落点仍在 chat_uploads 内。
                fn = _safe_fname(re.sub(r"[^\w.\-一-鿿]", "_", a.get("name", "f"))[:60])
                raw = base64.b64decode(a.get("b64", ""))
                _atomic_bytes(_confined(updir, fn), raw)
                txt = ""
                if fn.lower().endswith((".txt", ".md", ".csv", ".json", ".sql")):
                    txt = raw.decode("utf-8", "replace")[:2000]
                att_note += f"\n[附件 {fn}]{(':' + txt) if txt else '(二进制已存档)'}"
            except Exception: pass
        if att_note: yield 'data: {"type":"status","text":"附件已解析入上下文"}\n\n'
        yield 'data: {"type":"status","text":"智能引擎分析中…"}\n\n'
        out = _q.Queue()
        def run():
            with app.test_request_context(json={"id": cid, "message": msg + att_note, "graph": key}):
                try: out.put(ont_chat().get_json())
                except Exception as e: out.put({"reply": f"(异常:{e})"})
        threading.Thread(target=run, daemon=True).start()
        t0 = time.time()
        while True:
            try:
                d = out.get(timeout=8)
                yield "data: " + json.dumps({"type": "done", **d}, ensure_ascii=False) + "\n\n"; break
            except Exception:
                yield f'data: {{"type":"status","text":"引擎运行中 {int(time.time()-t0)}s…"}}\n\n'
    return Response(stream_with_context(gen()), mimetype="text/event-stream")

@app.post("/api/ont/forge")
def ont_forge():
    """原生锻造:当前(含编辑)IR → OWL Turtle + 结构校验(+pyshacl 若装) → 存入本体库(IR+TTL)"""
    body = request.json or {}
    key, name = body.get("graph", "demo"), (body.get("name") or "").strip()
    if not name: return jsonify({"error": "需要 name"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try:
        ttl = _ir_to_turtle(key, ir)                     # 统一走 IOF 注释化 Turtle(与导出/SPARQL 同底)
        import rdflib
        g = rdflib.Graph(); g.parse(data=ttl, format="turtle")
    except ImportError:
        return jsonify({"error": "锻造需要 rdflib(pip3 install rdflib)"}), 503
    except Exception as e:
        return jsonify({"error": f"OWL 生成失败: {e}"}), 400
    shacl = "未装 pyshacl(跳过)"
    try:
        import pyshacl, rdflib as _rl
        sg = _rl.Graph(); sg.parse(data=_IOF_SHACL, format="turtle")   # IOF 形状约束校验
        with _RDF_LOCK:   # pyshacl 内部跑 SPARQL,同受 pyparsing 非线程安全影响,串行化
            conforms, _, txt = pyshacl.validate(g, shacl_graph=sg, inference="none")
        nviol = (txt or "").count("Constraint Violation")
        shacl = "conforms" if conforms else f"{nviol} violations"
    except ImportError: pass
    except Exception as e: shacl = f"shacl error: {str(e)[:60]}"
    fid = "onto_" + uuid.uuid4().hex[:8]
    ir.setdefault("scenario", {})["name"] = name
    os.makedirs(FORGED_DIR, exist_ok=True)
    with _WRITE_LOCK:                                     # 原子写(对齐 DR-006 写入原子性)
        _atomic_json(os.path.join(FORGED_DIR, fid + ".json"), ir)
        _atomic_text(os.path.join(FORGED_DIR, fid + ".ttl"), ttl)
    return jsonify({"ok": True, "id": fid, "triples": len(g), "shacl": shacl})

@app.get("/api/ont/rules")
def ont_rules():
    """构成规则:建模规则映射 + 从当前IR派生的真实计数(同平台rules.js口径,不臆造)"""
    key = request.args.get("graph", "demo")
    ir = load_ir_edited(key) or {}
    objs, links = ir.get("objects", []), ir.get("links", []) or ir.get("relations", [])
    kinds = {}
    for o in objs: kinds[o.get("kind", "object")] = kinds.get(o.get("kind", "object"), 0) + 1
    ml = ir.get("metric_layers") or {}
    verified = sum(1 for l in links if (l.get("status") == "verified"))
    cand = sum(1 for o in objs if o.get("candidate"))   # 仅显式 candidate=True 计为候选;DR-001 建成对象默认为已确认
    return jsonify({"graph": key, "counts": {
        "objects": len(objs), "links": len(links), "verified": verified,
        "candidate_objs": cand, "confirmed_objs": len(objs) - cand, "kinds": kinds,
        "metrics": {k: len(v) for k, v in ml.items()},
        "hierarchy": len((ir.get("hierarchy") or {}).get("families", [])), "gaps": len(ir.get("gaps", []))},
        "methodology": [
            {"name": "斯坦福七步法", "map": "确定范围→复用→列举术语→定义类→类层次→定义属性→创建实例;对应 抽取列结构/枚举表→对象定义→hierarchy families→attrs→绑定实数据"},
            {"name": "Palantir 操作型本体四层", "map": "对象↔表 / 属性↔列 / 链接↔FK+取值重叠(≥60%∧列名有据=verified) / 指标↔DWS列(原子/派生/复合)"},
            {"name": "W3C RDF/OWL+SHACL", "map": "导出类、数据属性、对象属性和 SKOS 指标；执行 RDF 解析与 SHACL 校验"},
            {"name": "证据状态规范", "map": "verified 仅由数据裁决;人工/LLM 断言记 asserted/candidate;弱证据送审;编辑走白名单op+可撤销"}],
        "pipeline": [
            {"stage": "领域与源界定", "io": "数据源探活 → 表清单/连接", "rule": "真实查询探活(非端口探测)"},
            {"stage": "复用领域知识包", "io": "指标Excel/术语 → glossary", "rule": "知识包驱动命名与指标分层"},
            {"stage": "列举术语·对象与属性", "io": "表结构 → 对象+属性(中文)", "rule": "SchemaDump 单连接;过滤分区伪列"},
            {"stage": "关系发现·取值重叠", "io": "候选键对 → verified/candidate", "rule": "重叠≥60% ∧ 列名有据(name_score≥1);父键唯一度≥0.95;子键 distinct≥3"},
            {"stage": "类层次发现", "io": "对象 → hierarchy families", "rule": "证据化类层次(IR-007)"},
            {"stage": "动作·事件·指标分层", "io": "日志/API/DWS → 事件/动作/指标", "rule": "原子/派生/复合三层(DR-001)"},
            {"stage": "W3C 标准导出+校验", "io": "IR → OWL/JSON-LD + SHACL", "rule": "执行 RDF 解析与 SHACL conforms 检查"}],
        "owl_projection": {"owlClass": len(objs), "owlObjProp": len(links), "owlDataProp": sum(len(o.get("attrs", [])) for o in objs),
            "owlFunctional": sum(1 for l in links if str(l.get("card", "")).startswith("N:1")),
            "skos": (ml.get("atomic") and len(ml["atomic"]) or 0) + (ml.get("derived") and len(ml["derived"]) or 0)}})

@app.post("/api/ont/runtime")
def ont_runtime_set():
    name = (request.json or {}).get("name", "")
    try:
        from agent_runtime import available
        if name not in available(): return jsonify({"error": "无此运行时", "available": available()}), 400
    except Exception as e: return jsonify({"error": str(e)}), 500
    os.environ["CLAW_DRIVER"] = name
    return jsonify({"ok": True, "current": name})

@app.get("/api/health")
def health(): return jsonify({"ok": True, "ts": int(time.time()), "graphs": len(IR_SOURCES) + 1})

@app.get("/api/db/check")
def db_check():
    try:
        # 必须走 ro_connect:直连 sqlite3.connect 会在库缺失时静默新建空库,
        # 此后所有只读连接都能打开却查不到表 —— 把「库没了」伪装成「库是空的」,
        # 恰是健康检查最该报出来的那类故障。连接显式关闭,避免每次探活泄漏一个句柄。
        con = ro_connect(DB)
        try:
            n = con.execute(
                "SELECT count(*) FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        finally:
            con.close()
        return jsonify({"ok": True, "db": os.path.basename(DB), "tables": n})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/api/ont/chats/<cid>")
def ont_chat_get(cid):
    d = _chats().get(cid)
    return jsonify(d) if d else (jsonify({"error": "无此会话"}), 404)

@app.post("/api/ont/chats/delete")
def ont_chat_del():
    cid = (request.json or {}).get("id", "")
    with _WRITE_LOCK:
        d = _chats()
        if cid in d: d.pop(cid); _chats_w(d); return jsonify({"ok": True})
    return jsonify({"error": "无此会话"}), 404

@app.get("/api/ont/object/<key>/<oid>")
def ont_object(key, oid):
    """单对象详情:属性(含中文/证据)/关系/指标 —— 对齐平台 /api/object/<id>"""
    ir = load_ir_edited(key) or {}
    o = next((x for x in ir.get("objects", []) if x.get("id") == oid or x.get("name") == oid), None)
    if not o: return jsonify({"error": "对象不存在"}), 404
    rels = [{"dir": "out" if l.get("source") == o.get("id") else "in", "verb": l.get("verb"),
             "other": l.get("target") if l.get("source") == o.get("id") else l.get("source"), "status": l.get("status")}
            for l in ir.get("links", []) if o.get("id") in (l.get("source"), l.get("target"))]
    object_key = o.get("id") or o.get("name")
    rels.extend({"dir": "out" if relation.get("source_concept") == object_key else "in",
                 "verb": relation.get("verb"),
                 "other": (relation.get("target_concept") if relation.get("source_concept") == object_key
                           else relation.get("source_concept")),
                 "status": relation.get("status"),
                 "evidence_status": relation.get("evidence_status") or relation.get("status"),
                 "semantic_status": relation.get("semantic_status") or relation.get("semantic", "")}
                for relation in ir.get("relations", [])
                if object_key in (relation.get("source_concept"), relation.get("target_concept")))
    # 对象描述/说明(对齐平台『对象描述』):由 kind/表/中文/证据来源构造
    KMAP = {"object": "对象", "event": "事件", "action": "动作", "asset": "资产", "role": "角色"}
    tb = o.get("table") or ""
    ev = o.get("evidence", {}) or {}
    src = "、".join(ev.get("sources", []) or []) or "IR"
    bfo_enabled = _bfo_enabled_for_ir(ir)
    bfo = (o.get("bfo") or _KIND_BFO.get(o.get("kind"), "MaterialEntity")) if bfo_enabled else ""
    defn = (o.get("definition") or "").strip()
    cex = (o.get("counterExample") or "").strip()
    desc = f'{KMAP.get(o.get("kind"),"对象")}「{o.get("cn") or o.get("name")}」' + (f'(BFO:{bfo})' if bfo else '') + (f',绑定表 {tb}' if tb else '') + f';字段 {len(o.get("attrs",[]))} 个;证据来源 {src}。'
    if defn: desc += f' 定义:{defn}'                      # IOF 属加种差定义
    if cex: desc += f'(反例:{cex})'                       # IOF counterExample,辅助辨伪
    # 业务指标:按表匹配指标目录,带编码/描述/类型(对齐平台『业务指标管理』)
    LNAME = {"atomic": "原子指标", "derived": "派生指标", "composite": "复合指标"}
    metrics = []
    for lk, arr in (ir.get("metric_layers") or {}).items():
        for m in arr:
            if (m.get("table") or "").lower() == tb.lower() and tb:
                metrics.append({"name": m.get("name"), "code": m.get("id"), "desc": m.get("desc"),
                                "type": LNAME.get(lk, lk), "unit": m.get("unit")})
    return jsonify({"id": o.get("id"), "cn": o.get("cn"), "name": o.get("name"), "kind": o.get("kind"),
                    "table": o.get("table"), "pk": o.get("pk"), "candidate": o.get("candidate"),
                    "description": desc, "scene": o.get("scene") or o.get("scenario") or "",
                    "attrs": o.get("attrs", []), "supported_metrics": o.get("supported_metrics", []),
                    "metrics": metrics, "evidence": ev, "relations": rels,
                    # ── IOF-AV 机读注释(供审计/导出/前端展示)──
                    "bfo": bfo, "definition": defn, "isPrimitive": o.get("isPrimitive"),
                    "industry_reference": o.get("industry_reference"),
                    "standard_alignment": o.get("standard_alignment"),
                    "reused_from": o.get("reused_from"), "foundational_kind": o.get("foundational_kind"),
                    "example": o.get("example", ""), "counterExample": cex,
                    "maturity": o.get("maturity", ""), "provenance": o.get("provenance"),
                    "action_id": o.get("action_id"), "action_spec": o.get("action_spec")})

@app.get("/api/ont/relation/<key>")
def ont_relation(key):
    """关系详情(证据卡)—— src/tgt 由 query 传;对齐 render.js 连线详情面板"""
    src, tgt = request.args.get("s", ""), request.args.get("t", "")
    ir = load_ir_edited(key) or {}
    links = ir.get("links") or []
    l = next((x for x in links if x.get("source") == src and x.get("target") == tgt), None)
    if not l:
        rels = ir.get("relations") or []
        l = next((x for x in rels if x.get("source_concept") == src and x.get("target_concept") == tgt), None)
        if l: l = {"source": src, "target": tgt, "verb": l.get("verb"), "status": l.get("status"),
                   "evidence_status": l.get("evidence_status"), "semantic_status": l.get("semantic_status"),
                   "founded_relation": l.get("founded_relation"), "temporal": l.get("temporal"),
                   "semantic": l.get("semantic"), "note": l.get("note", ""),
                   "evidence": {**(l.get("evidence") or {}), "overlap_pct": l.get("overlap")}}
    if not l: return jsonify({"error": "关系不存在"}), 404
    ev = l.get("evidence", {})
    categories = {}
    for obj in ir.get("objects", []):
        oid = obj.get("id") or obj.get("name")
        if oid:
            categories[oid] = obj.get("bfo") or ontology_grounding.default_category(obj.get("kind"))
    grounding = _iof_edge_ann(l, l.get("verb"), categories.get(src), categories.get(tgt),
                              _bfo_enabled_for_ir(ir))
    return jsonify({"source": src, "target": tgt, "verb": l.get("verb"), "status": l.get("status"),
                    "evidence_status": l.get("evidence_status") or l.get("status"),
                    "semantic_status": l.get("semantic_status") or l.get("semantic", ""),
                    "candidate": l.get("candidate"), "note": l.get("note", ""), "card": l.get("card", ""),
                    "child_key": ev.get("child_key"), "parent_key": ev.get("parent_key"),
                    "overlap_pct": ev.get("overlap_pct"), "child_distinct": ev.get("child_distinct"),
                    "sources": ev.get("sources", []),
                    "founded_relation": grounding["founded_relation"],
                    "grounding_iri": grounding["grounding_iri"],
                    "grounding_status": grounding["grounding_status"],
                    "grounding_reason": grounding["grounding_reason"],
                    "temporal": grounding["temporal"],
                    "semantic": l.get("semantic", "")})   # IOF/BFO 映射检查 + 语义复核标注

# IOF 风格 SHACL 形状:非原始类须有定义、每个类须有标签(本体质量校验,借鉴 IOF『非原始类须有定义』)
_IOF_SHACL = """@prefix sh:     <http://www.w3.org/ns/shacl#> .
@prefix owl:    <http://www.w3.org/2002/07/owl#> .
@prefix rdfs:   <http://www.w3.org/2000/01/rdf-schema#> .
@prefix iof-av: <https://spec.industrialontologies.org/ontology/annotation/> .
[] a sh:NodeShape ; sh:targetClass owl:Class ;
   sh:property [ sh:path rdfs:label ; sh:minCount 1 ; sh:message "IOF: 类必须有 rdfs:label" ] ;
   sh:or ( [ sh:path iof-av:isPrimitive ; sh:hasValue true ]
           [ sh:path iof-av:naturalLanguageDefinition ; sh:minCount 1 ] ) ;
   sh:message "IOF: 非原始类必须有 naturalLanguageDefinition 定义" .
"""

# BFO 2020 / IOF Core 上层类 IRI(供 rdfs:subClassOf 归类;借鉴 Industrial Ontology Foundry)
_BFO_UP = ontology_grounding.UPPER_CLASS_IRIS
_TTL_XSD = {"int": "integer", "integer": "integer", "bigint": "integer", "smallint": "integer", "tinyint": "integer",
            "decimal": "decimal", "numeric": "decimal", "double": "decimal", "float": "decimal", "real": "decimal",
            "date": "date", "datetime": "dateTime", "timestamp": "dateTime", "bool": "boolean", "boolean": "boolean"}

def _ir_to_turtle(key, ir):
    """IR → OWL2 Turtle，带 BFO/IOF 上层类别、IOF-AV 注释和关系映射状态。
    自包含、确定性纯函数;所有图谱(示例/应用/quick_build/forged)统一走此路径,ttl/jsonld/owl 三格式一致且带注释。"""
    g = ir_to_graph(key, ir)
    bfo_enabled = _bfo_enabled_for_ir(ir)
    raw = {}                                              # 原始对象索引:取 attrs 补字段级数据类型属性,不丢信息
    for o in (ir.get("objects") or []):
        for kk in (o.get("id"), o.get("name")):
            if kk: raw[str(kk)] = o
    def loc(x):
        s = re.sub(r"[^0-9A-Za-z_]", "_", str(x))
        return ("_" + s) if (s and s[0].isdigit()) else (s or "_")
    def esc(x):                                           # 转义 + C0 控制字符→\uXXXX,兼容严格 TTL 解析器(Jena 等)
        s = str(x).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", lambda m: "\\u%04X" % ord(m.group(0)), s)
    xsd = lambda t: "xsd:" + _TTL_XSD.get(str(t or "").split("(")[0].strip().lower(), "string")
    scen = (ir.get("scenario") or {}).get("name") or key
    L = ["@prefix :       <http://datamind.local/ont#> .",
         "@prefix owl:    <http://www.w3.org/2002/07/owl#> .",
         "@prefix rdfs:   <http://www.w3.org/2000/01/rdf-schema#> .",
         "@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> ."]
    if bfo_enabled:
        L.extend(["@prefix obo:    <http://purl.obolibrary.org/obo/> .",
                  "@prefix iof:    <https://spec.industrialontologies.org/ontology/construct/> .",
                  "@prefix iof-av: <https://spec.industrialontologies.org/ontology/annotation/> .",
                  "@prefix iof-ind:<https://spec.industrialontologies.org/ontology/individual/> ."])
    L.extend(["", ":temporal a owl:AnnotationProperty ; rdfs:label \"关系的时间限定\"@zh .",
              ":groundingStatus a owl:AnnotationProperty ; rdfs:label \"上层关系映射状态\"@zh .",
              ":groundingNote a owl:AnnotationProperty ; rdfs:label \"上层关系映射说明\"@zh .",
              ":lifecycleStatus a owl:AnnotationProperty ; rdfs:label \"本地生命周期状态\"@zh .",
              ":naturalLanguageDefinition a owl:AnnotationProperty ; rdfs:label \"自然语言定义\"@zh .",
              ":isPrimitive a owl:AnnotationProperty ; rdfs:label \"是否原始概念\"@zh .",
              ":example a owl:AnnotationProperty ; rdfs:label \"正例\"@zh .",
              ":counterExample a owl:AnnotationProperty ; rdfs:label \"反例\"@zh .",
              ":usageNote a owl:AnnotationProperty ; rdfs:label \"使用说明\"@zh .",
              ":industryConcept a owl:AnnotationProperty ; rdfs:label \"行业参照概念\"@zh .",
              ":standardCandidate a owl:AnnotationProperty ; rdfs:label \"本体标准候选对齐\"@zh ."])
    L.append((f':  a owl:Ontology ; rdfs:label "{esc(scen)}"@zh ; iof-av:maturity iof-ind:Provisional .'
              if bfo_enabled else f':  a owl:Ontology ; rdfs:label "{esc(scen)}"@zh .'))
    L.append("")
    used_iris = set()
    for n in g["nodes"]:
        cid = loc(n["id"]); up = _BFO_UP.get(n.get("bfo") or "", "")
        parts = [f':{cid} a owl:Class']
        if up: parts.append(f'rdfs:subClassOf {up}')     # BFO/IOF 上层归类
        parts.append(f'rdfs:label "{esc(n["name"])}"@zh')
        defn = (n.get("definition") or "").strip()
        ann = "iof-av:" if bfo_enabled else ":"
        if defn: parts.append(f'{ann}naturalLanguageDefinition "{esc(defn)}"@zh')
        if n.get("isPrimitive") is not None:
            parts.append(f'{ann}isPrimitive "{str(bool(n["isPrimitive"])).lower()}"^^xsd:boolean')
        if (n.get("example") or "").strip(): parts.append(f'{ann}example "{esc(n["example"])}"@zh')
        if (n.get("counterExample") or "").strip(): parts.append(f'{ann}counterExample "{esc(n["counterExample"])}"@zh')
        mat = (n.get("maturity") or "").strip() or "Provisional"
        if bfo_enabled and mat in ("Provisional", "Released"):
            parts.append(f'iof-av:maturity iof-ind:{mat}')
        elif mat:
            parts.append(f':lifecycleStatus "{esc(mat)}"')
        prov = n.get("provenance") or {}
        if bfo_enabled:
            if prov.get("directSource"): parts.append(f'iof-av:directSource "{esc(prov["directSource"])}"')
            for ad in (prov.get("adaptedFrom") or []):
                if ad: parts.append(f'iof-av:adaptedFrom "{esc(ad)}"')
        industry_ref = n.get("industry_reference") or {}
        if industry_ref.get("concept"):
            parts.append(f':industryConcept "{esc(industry_ref.get("industry"))}::{esc(industry_ref.get("concept"))}"')
        standard_ref = n.get("standard_alignment") or {}
        if standard_ref.get("candidate"):
            parts.append(f':standardCandidate "{esc(standard_ref.get("standard"))}::{esc(standard_ref.get("candidate"))}"')
        L.append(" ;\n    ".join(parts) + " .")
        for a in ((raw.get(str(n["id"])) or {}).get("attrs") or [])[:60]:   # 字段级数据类型属性
            col = a.get("col")
            if not col: continue
            L.append(f':{loc(cid + "_" + col)} a owl:DatatypeProperty ; rdfs:domain :{cid} ; '
                     f'rdfs:range {xsd(a.get("type"))} ; rdfs:label "{esc(a.get("cn") or col)}"@zh .')
    for i2, e2 in enumerate(g["edges"]):
        # 关系 IRI 带 verb+序号,避免同一对节点的多条不同关系塌缩成一个属性(丢边)
        rid = f'rel_{loc(e2["s"])}_{loc(e2.get("verb",""))}_{loc(e2["t"])}_{i2}'
        seg = [f':{rid} a owl:ObjectProperty', f'rdfs:domain :{loc(e2["s"])}',
               f'rdfs:range :{loc(e2["t"])}', f'rdfs:label "{esc(e2.get("verb",""))}"@zh']
        grounding_iri = e2.get("grounding_iri") or ""
        grounding_status = e2.get("grounding_status") or "unmapped"
        if bfo_enabled:
            if grounding_status == "mapped" and grounding_iri:
                seg.append(f'rdfs:subPropertyOf {grounding_iri}')
                used_iris.add(grounding_iri)
            seg.append(f':groundingStatus "{esc(grounding_status)}"')
            if e2.get("grounding_reason"):
                seg.append(f':groundingNote "{esc(e2["grounding_reason"])}"@zh')
            if e2.get("temporal"):
                seg.append(f':temporal "{esc(e2["temporal"])}"')
        if e2.get("status"): seg.append(f'{"iof-av:" if bfo_enabled else ":"}usageNote "{esc("status=" + str(e2["status"]))}"')
        L.append(" ;\n    ".join(seg) + " .")
    for iri in sorted(used_iris):                         # 声明实际引用的官方关系,保证离线解析
        L.append(f'{iri} a owl:ObjectProperty .')
    return "\n".join(L)

@app.get("/api/graph/<key>/export.<fmt>")
def graph_export_fmt(key, fmt):
    """OWL 多格式导出:ttl / jsonld / owl(rdfxml) —— 三格式对所有图谱一致可用"""
    if fmt not in ("ttl", "jsonld", "owl", "rdf", "xml"):
        return jsonify({"error": "格式仅支持 ttl/jsonld/owl/rdf/xml"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    from flask import Response
    ttl = _ir_to_turtle(key, ir)
    if fmt == "ttl":
        return Response(ttl, mimetype="text/turtle", headers={"Content-Disposition": f"attachment;filename={key}.ttl"})
    try:
        import rdflib                                    # 置于 try 内:缺依赖时给可操作提示,而非异常直穿成 500
        g = rdflib.Graph(); g.parse(data=ttl, format="turtle")
    except ImportError:
        return jsonify({"error": "该格式需要 rdflib(pip3 install rdflib);Turtle 格式无此依赖,可直接导出"}), 503
    except Exception as e:
        return jsonify({"error": f"图谱序列化失败: {str(e)[:120]}"}), 400
    if fmt == "jsonld":
        return Response(g.serialize(format="json-ld"), mimetype="application/ld+json", headers={"Content-Disposition": f"attachment;filename={key}.jsonld"})
    return Response(g.serialize(format="xml"), mimetype="application/rdf+xml", headers={"Content-Disposition": f"attachment;filename={key}.owl"})

@app.post("/api/ont/cq")
def ont_cq():
    """验收问题核验(DR-024):在已建成的本体上判定「这些业务问题答不答得了」。

    验收问题即学界所称「能力问题 / Competency Questions」。对外文案一律用
    「验收问题」——CQ 是行话,业务方看不懂;字段名 cq/cqs 保持不变。

    与元数据覆盖率互补：覆盖率只反映定义等字段是否存在，验收问题检查业务问题所需对象和关系是否可达。
    定义和标准关系映射覆盖率为 100% 的本体，仍可能缺少业务问题所需关系。

    判定为确定性图计算(对象锚定 + 路径可达 + 边状态),不调 LLM:
    让模型自评「能不能答」会把「看起来能答」当成「能答」,与证据状态规范相悖。

    请求: {"graph": "<键>", "cqs": ["问题…", {"q": "问题…", "expect": ["对象名"]}]}
    """
    body = request.get_json(silent=True) or {}
    key = (body.get("graph") or "").strip()
    if not key: return jsonify({"error": "缺 graph(不默认任何图谱)"}), 400
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    cqs = body.get("cqs") or []
    if not isinstance(cqs, list) or not cqs: return jsonify({"error": "缺 cqs(能力问题列表)"}), 400
    if len(cqs) > 100: return jsonify({"error": "cqs 过多(上限 100)"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try:
        import cq_check
        rep = cq_check.check_all(cqs, ir)
        rep["graph"] = key
        rep["gaps"] = cq_check.gaps_from(rep)      # 供人审队列/构建下一轮消费
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"验收问题核验失败: {e}"}), 500

@app.post("/api/ont/chain")
def ont_chain():
    """穿透链路核验(DR-025):逐段判定一条业务追溯链路通不通。

    《本体智能研究报告》四个行业案例方法同构——定义 5-6 类核心实体后,
    关键在建立一条纵向穿透链路(停电事件—设备—线路—用户 / 订单—资源—工单—用户 /
    飞机—子系统—零部件—供应商 / 客户—账户—交易—关联方)。本体的价值不在对象多,
    而在能否从一端穿到另一端;首尾通但中段断的链路在业务上是断的,故逐段判定。

    请求: {"graph": "<键>", "chain": ["停电事件", "配电设备", "线路", "用户"]}
    """
    body = request.get_json(silent=True) or {}
    key = (body.get("graph") or "").strip()
    if not key: return jsonify({"error": "缺 graph(不默认任何图谱)"}), 400
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    chain = body.get("chain") or []
    if not isinstance(chain, list) or len(chain) < 2:
        return jsonify({"error": "缺 chain(至少 2 个节点的对象名列表)"}), 400
    if len(chain) > 20: return jsonify({"error": "chain 过长(上限 20 节点)"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try:
        import cq_check
        rep = cq_check.check_chain(chain, ir)
        rep["graph"] = key
        rep["gaps"] = cq_check.chain_gaps(rep)
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"链路核验失败: {e}"}), 500


@app.get("/api/ont/drift/<key>")
def ont_drift(key):
    """概念漂移与关系断裂检测(DR-025):本体还对不对得上数据源。

    《本体智能研究报告》阶段六点名「引入自动化检测工具监控本体与数据源的一致性,
    及时发现概念漂移与关系断裂」。本体建成之日与库一致,但库会继续演进——
    表改名、列删除、主键换名,此时本体不报错,只在问数时静默产出错误 SQL。

    确定性 schema 比对,不调 LLM;只报事实不自动修复(漂移的正解可能是改本体、
    也可能是数据源回滚,须人判断)。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    if not os.path.exists(DB):
        return jsonify({"error": "数据源不可用,无法比对", "checked": False}), 503
    try:
        import drift_check
        rep = drift_check.check(ir, DB)
        rep["graph"] = key
        rep["gaps"] = drift_check.gaps_from(rep)
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"漂移检测失败: {e}"}), 500

@app.get("/api/ont/usage/<key>")
def ont_usage(key):
    """本体使用度与建模优先级(DR-026):以使用数据驱动建模迭代。

    报告阶段六:「定期评估本体的业务调用频次与决策支撑效果,以使用数据驱动优化迭代」。
    统计本身不是目的——把调用频次与证据状态交叉,直接产出优先级:
    高频却仍有候选关系 → 优先补裁决;零调用 → 疑似建模过度(须先确认统计窗口够长)。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try:
        import usage_stat
        return jsonify(usage_stat.report(WORK, ir, key))
    except Exception as e:
        return jsonify({"error": f"使用度统计失败: {e}"}), 500

@app.get("/api/ont/audit/<key>")
def ont_audit(key):
    """本体变更审计(DR-027):谁在何时改了什么,以及哪些改动值得复核。

    与 /api/ont/edits 的区别:后者是原始日志(给回放用),这里是**审计视图**——
    按人/类型/来源聚合,并主动标出风险项。报告阶段六要求「明确本体治理的责任主体」,
    责任要能追溯到人,就必须能回答「这条改动是谁做的、依据什么、AI 建议还是人自己定的」。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    ed = _load_edits(key)
    ops = ed.get("ops", [])
    by_person, by_op, by_src = {}, {}, {}
    no_reviewer, no_reason, risky = [], [], []
    for i, o in enumerate(ops):
        who = (o.get("reviewer") or "").strip() or "(未署名)"
        kind = o.get("op", "?")
        src = o.get("source", "api")
        by_person[who] = by_person.get(who, 0) + 1
        by_op[kind] = by_op.get(kind, 0) + 1
        by_src[src] = by_src.get(src, 0) + 1
        if not (o.get("reviewer") or "").strip():
            no_reviewer.append(i)
        if not (o.get("reason") or "").strip():
            no_reason.append(i)
        # 风险项:删除类不可逆影响面大;人审试图直接指定 verified 违反证据状态规范
        if kind in ("remove_object", "remove_relation", "reject_relation"):
            risky.append({"idx": i, "op": kind, "target": o.get("target"),
                          "by": who, "ts": o.get("ts"), "level": "destructive",
                          "why": "删除/否决类操作影响面大且需级联,建议复核"})
        if str((o.get("params") or {}).get("status", "")).lower() == "verified":
            risky.append({"idx": i, "op": kind, "target": o.get("target"),
                          "by": who, "ts": o.get("ts"), "level": "discipline",
                          "why": "人审试图直接指定 verified —— 违反证据状态规范"
                                 "(verified 只能由数据裁决产生,人只产生 asserted)"})
    return jsonify({
        "graph": key, "total": len(ops),
        "by_person": by_person, "by_op": by_op, "by_source": by_src,
        "unsigned": len(no_reviewer), "no_reason": len(no_reason),
        "risky": risky,
        "recent": [{"idx": i, "op": o.get("op"), "target": o.get("target"),
                    "by": o.get("reviewer") or "(未署名)", "ts": o.get("ts"),
                    "source": o.get("source", "api"), "reason": o.get("reason", "")}
                   for i, o in list(enumerate(ops))[-20:]][::-1],
        "note": "审计视图基于编辑日志;日志是回放的单一真相,撤销会同步移除条目——"
                "故本视图反映的是当前生效的变更集,不是历史全量操作流水",
    })

_RULES_F = os.path.join(WORK, "ont_rules.json")

def _load_rules(key):
    try:
        d = json.load(open(_RULES_F, encoding="utf-8"))
        return d.get(key, []) if isinstance(d, dict) else []
    except Exception:
        return []

def _save_rules(key, rules):
    with _WRITE_LOCK:
        try:
            d = json.load(open(_RULES_F, encoding="utf-8"))
            if not isinstance(d, dict): d = {}
        except Exception:
            d = {}
        d[key] = rules
        _atomic_json(_RULES_F, d)

@app.get("/api/ont/rulebook/<key>")
def ont_rulebook(key):
    """业务规则与约束清单(DR-028 · 报告语义层第四要素)+ 静态一致性校验。"""
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    import rule_engine
    rules = _load_rules(key)
    return jsonify({"graph": key, "rules": rules,
                    "consistency": rule_engine.consistency_check(rules)})

@app.post("/api/ont/rulebook/<key>")
def ont_rulebook_save(key):
    """新增/更新一条业务规则。结构非法一律拒收——规则是逻辑边界,带病入库会污染全部下游判定。"""
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    import rule_engine
    r = (request.get_json(silent=True) or {}).get("rule") or {}
    err = rule_engine.validate_rule(r)
    if err: return jsonify({"error": f"规则非法: {err}"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    keys = {o.get("id") or o.get("name") for o in ir.get("objects", [])}
    if r["on"] not in keys:
        return jsonify({"error": f"作用对象 {r['on']} 不在本体中 —— 规则须锚定到已建模的对象"}), 400
    r["ts"] = time.strftime("%Y-%m-%d %H:%M")
    who = ((request.get_json(silent=True) or {}).get("author") or "").strip()[:40]
    if who: r["author"] = who
    rules = [x for x in _load_rules(key) if x.get("id") != r["id"]] + [r]
    if len(rules) > 500: return jsonify({"error": "规则过多(上限 500)"}), 400
    _save_rules(key, rules)
    return jsonify({"ok": True, "total": len(rules),
                    "consistency": rule_engine.consistency_check(rules)})

@app.post("/api/ont/rulebook/<key>/delete")
def ont_rulebook_del(key):
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    rid = (request.get_json(silent=True) or {}).get("id", "")
    rules = _load_rules(key)
    left = [x for x in rules if x.get("id") != rid]
    if len(left) == len(rules): return jsonify({"error": "规则不存在"}), 404
    _save_rules(key, left)
    return jsonify({"ok": True, "total": len(left)})

@app.post("/api/ont/decide/<key>")
def ont_decide(key):
    """决策层求值(DR-028):由规则推导隐含结论,每条结论可回溯至具体规则依据。

    报告决策层要求「逻辑推理基于语义层的概念关系与业务规则,推导出未显式记录的
    隐含结论」「形成完整可追溯的决策路径——每一条结论均可回溯至具体规则依据」。
    确定性求值,不调 LLM:规则是业务写死的逻辑边界,用模型推理会把概率当逻辑。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    body = request.get_json(silent=True) or {}
    obj = (body.get("object") or "").strip()
    facts = body.get("facts")
    if not obj: return jsonify({"error": "缺 object(作用对象)"}), 400
    if not isinstance(facts, dict) or not facts:
        return jsonify({"error": "缺 facts(该实例的字段字典)"}), 400
    import rule_engine
    res = rule_engine.evaluate(_load_rules(key), obj, facts)
    res["graph"] = key
    return jsonify(res)

@app.get("/api/ont/health/<key>")
def ont_health(key):
    """本体图结构检查(DR-030 · 报告阶段六「异常关系检测」与「定期评审结构一致性」)。

    与既有三项检测互补——它们都不看图结构本身:
      验收问题答「够不够用」· 漂移答「还对不对得上数据」· 元数据覆盖答「定义填没填全」
    而一个三项全过的本体,结构上仍可能是病的:一半对象是孤岛、存在自反关系、
    同一对语义重复连了多条边。这些不会让任何现有检查报错,却会让问数召回选错表。

    分级:dangling/self_loop/status_conflict 是阻断问题(IR 不自洽);
    isolated/hub/duplicate/bidirectional 是待核查信号。结构一致性分只由阻断问题扣分——
    否则业务枢纽对象会被不恰当地扣分，导致评分失去区分度。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try:
        import health_check
        rep = health_check.check(ir)
        rep["graph"] = key
        rep["gaps"] = health_check.gaps_from(rep)
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"图结构检查失败: {e}"}), 500

@app.get("/api/ont/compat/<key>")
def ont_compat(key):
    """向后兼容性检查(DR-031 · 报告阶段五/六「版本管理与向后兼容性保障」)。

    比较**基线 IR** 与**当前编辑后 IR**:发布这批草案编辑会破坏什么。

    本体是语义契约——问数靠它召回表与口径、规则靠它锚定对象、动作靠它绑表、
    穿透链路靠它连通。删掉一个对象可能让几条规则失效、几个动作绑不到表,
    而这些往往直到线上报错才被发现。

    重点在**下游影响**而非结构 diff:只报「删了 3 个对象」没有决策价值,
    报「删掉的对象上挂着 2 条规则和 1 个动作」才让人知道该不该删。
    不阻断变更——兼容性是决策依据不是权限,判断权在人。

    可选 ?against=<另一图谱键> 改为与另一图谱比较。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    against = (request.args.get("against") or "").strip()
    if against and _bad_gkey(against): return jsonify({"error": "非法对比图谱键"}), 400
    new_ir = load_ir_edited(key)
    if not new_ir: return jsonify({"error": "图谱不存在"}), 404
    base_ir = load_ir_edited(against) if against else load_ir(key)
    if not base_ir: return jsonify({"error": "基线图谱不存在"}), 404
    try:
        import compat_check
        rules = _load_rules(key)
        try:
            actions = json.load(open(os.path.join(WORK, "action_types.json"), encoding="utf-8"))
            actions = actions if isinstance(actions, list) else []
        except Exception:
            actions = []
        try:
            qas = json.load(open(os.path.join(WORK, "qa_skills.json"), encoding="utf-8"))
            qas = qas if isinstance(qas, list) else []
        except Exception:
            qas = []
        rep = compat_check.check(base_ir, new_ir, rules, actions, qas)
        rep["graph"] = key
        rep["baseline"] = against or f"{key}(未编辑基线)"
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"兼容性检查失败: {e}"}), 500

@app.get("/api/ont/modules/<key>")
def ont_modules(key):
    """本体模块化划分建议(DR-031 · 报告阶段二「模块化策略:按领域或按层次拆分」)。

    我们的本体是一张平图,108 个对象平铺没有模块边界。后果很具体:
    改一处不知影响范围、想按域交给不同团队维护无从下手、新人打开图谱建立不了认知。

    ?strategy=by_domain(默认,连通分量+词根聚类)或 by_layer(数仓分层)。
    只建议不落盘:模块边界最终是业务决策,算法只给结构上的自然分界——
    自动切分会把一个错误的边界固化进本体。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    st = (request.args.get("strategy") or "by_domain").strip()
    if st not in ("by_domain", "by_layer"):
        return jsonify({"error": "strategy 需为 by_domain 或 by_layer"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try:
        import module_split
        rep = module_split.suggest(ir, st)
        rep["graph"] = key
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"模块划分失败: {e}"}), 500

@app.get("/api/ont/completeness/<key>")
def ont_completeness(key):
    """本体元数据覆盖情况:定义、示例、反例、上层类别和标准关系映射。

    该指标只用于定位待补元数据，不代表本体语义完备性或业务正确性。
    """
    from collections import Counter
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    g = ir_to_graph(key, ir)
    nodes, edges = g["nodes"], g["edges"]
    cnt = lambda pred: sum(1 for n in nodes if pred(n))
    has = lambda f: cnt(lambda n: bool(str(n.get(f) or "").strip()))
    obj = {"total": len(nodes),
           "withDefinition": has("definition"), "withExample": has("example"),
           "withCounterExample": has("counterExample"), "primitive": cnt(lambda n: n.get("isPrimitive") is True),
           "bound": cnt(lambda n: any(n.get("tables") or [])), "withBFO": has("bfo"), "withMaturity": has("maturity"),
           "byBFO": dict(Counter((n.get("bfo") or "—") for n in nodes)),
           "byMaturity": dict(Counter((n.get("maturity") or "—") for n in nodes))}
    grounded = sum(1 for e in edges if e.get("grounding_status") == "mapped")
    rel = {"total": len(edges), "grounded": grounded,
           "verified": sum(1 for e in edges if e.get("status") == "verified"),
           "candidate": sum(1 for e in edges if e.get("status") == "candidate"),
           "semanticReviewed": sum(1 for e in edges if e.get("semantic") in ("pass", "fail")),
           "semanticDisputed": sum(1 for e in edges if e.get("semantic") == "fail"),
           "byTemporal": dict(Counter((e.get("temporal") or "—") for e in edges))}
    frac = lambda x, d: (x / d) if d else 0
    nn = len(nodes)
    # 元数据覆盖率:默认 Provisional 不作为质量加分项，避免把系统默认值误当成人工验收。
    score = round(100 * (0.50 * frac(obj["withDefinition"], nn) + 0.15 * frac(obj["withCounterExample"], nn) +
                         0.15 * frac(obj["withBFO"], nn) +
                         0.20 * frac(grounded, len(edges))), 1) if nn else 0.0
    gaps = [n["name"] for n in nodes if not (n.get("definition") or "").strip()][:20]   # 待补定义清单
    return jsonify({"key": key, "metric": "metadata_coverage", "score": score,
                    "objects": obj, "relations": rel, "gaps": gaps,
                    "note": "覆盖率用于元数据补全排查，不等同于本体完备性或正确性"})

def _ir_write_path(key):
    """图谱键 → 可回写的 IR JSON 路径;只读平台源(cq 的 .js)或非法键返 None,绝不逃逸。"""
    if _bad_gkey(key): return None
    if key.startswith("built_"): return _confined(WORK, key + ".json")
    if key.startswith("forged_"):
        p = _forged_path(key[7:]); return p if os.path.exists(p) else None
    src = IR_SOURCES.get(key)
    if src:
        for p in src["paths"]:
            if p.endswith(".json"): return p              # demo→WORK/demo_ir.json、app→WORK/app_ontology_ir.json
    return None

def _open_writable(key, need_objects=False):
    """IR 写端点(enrich/reground/maturity)统一前奏:图谱必填校验 + 可回写路径守卫 + 载图 + 存在性校验。
    成功返回 (ir, write_path, None);失败返回 (None, None, (响应, 状态码))——缺图谱参数/只读源/穿越 400,缺图/缺对象 404。"""
    if not key:   # 写端点必须显式指定图谱,不静默默认到 示例 主图(防漏传/拼错时误改生产本体)
        return None, None, (jsonify({"error": "需要指定图谱(graph)"}), 400)
    wp = _ir_write_path(key)
    if not wp:
        return None, None, (jsonify({"error": "该图谱为只读平台源,不支持回写(请选 示例/应用/构建/锻造图谱)"}), 400)
    ir = load_ir(key)
    if not ir:
        return None, None, (jsonify({"error": "图谱不存在"}), 404)
    if need_objects and not isinstance(ir.get("objects"), list):
        return None, None, (jsonify({"error": "图谱无对象"}), 404)
    return ir, wp, None

def _llm_define(objs):
    """批量提出属加种差定义和概念反例；返回按对象名索引的建议。

    本端点只有结构元数据，没有实例行，因而不得生成正例。返回文本均为待复核的
    自然语言注释，不构成 OWL 充要定义。引擎离线或超时时返回空。
    """
    from agent_runtime import get_runtime, available
    items = []
    for o in objs:
        cols = ", ".join(a.get("col", "") for a in (o.get("attrs") or [])[:12])
        tb = o.get("table") or (o.get("tables") or [""])[0]
        items.append(f'- name={o.get("name") or o.get("id")}; cn={o.get("cn") or ""}; kind={o.get("kind")}; table={tb}; 列[{cols}]')
    prompt = ("你是企业本体定义专家。为下列对象各写一条 IOF 风格『属加种差』定义和一个易混淆的概念反例。\n"
              "对象清单:\n" + "\n".join(items) +
              '\n\n只输出一个 JSON(无其它文字):{"<name>":{"definition":"X 是一种 Y,且…(属加种差,简洁准确,基于给定语义,不编造)",'
              '"example":"","counterExample":"类别层面的易混淆概念及辨析(如 报价单仅表达价格意向，不是销售订单)"}, ...}\n'
              "要求:①定义用中文属加种差句式;②不虚构表/列中没有的语义;③反例要有辨析价值;④JSON 的 key 必须是上面给出的 name;"
              "⑤**非循环**:定义体中不得复用被定义术语名本身及其中文名(如定义『销售订单』不得出现『销售订单』字样),用上位类(属)+区别特征(种差)描述,避免自指;"
              "⑥没有实例数据，example 必须为空；反例不得包含虚构的企业名、编号、日期、数量或金额。")
    for drv in _drv_order():
        if drv not in available(): continue
        ok, reply = get_runtime(drv).run_turn(f"def_{uuid.uuid4().hex[:6]}", prompt, timeout=110)
        if ok and reply and not _looks_like_error(reply):
            m = re.search(r"\{[\s\S]*\}", reply)
            if m:
                try:
                    d = json.loads(m.group(0))
                    if isinstance(d, dict): return d
                except Exception: pass
    return {}

@app.post("/api/ont/enrich")
def ont_enrich():
    """为缺少注释的对象生成属加种差定义、正例和反例建议并回写 IR。

    默认只补缺失项；``force=1`` 会替换既有自然语言注释。生成内容保持 Provisional，
    仍需人工复核，也不会因此自动宣称符合 IOF。
    """
    body = request.json or {}
    key = body.get("graph"); force = bool(body.get("force")); limit = min(int(body.get("limit") or 60), 120)
    ir, wp, err = _open_writable(key, need_objects=True)
    if err: return err
    objs = ir["objects"]
    targets = [o for o in objs if force or not (o.get("definition") or "").strip()][:limit]
    if not targets: return jsonify({"ok": True, "enriched": 0, "note": "所有对象已有定义,无需补全"})
    try:
        from agent_runtime import available
        if not available(): return jsonify({"error": "智能引擎离线,无法生成定义(本功能不编造定义)"}), 503
    except Exception:
        return jsonify({"error": "智能引擎不可用"}), 503
    idx = {(o.get("name") or o.get("id")): o for o in objs}
    enriched = 0
    for i in range(0, len(targets), 20):                  # 分批≤20,控 prompt 体量防超时
        gen = _bounded(lambda b=targets[i:i + 20]: _llm_define(b), 120) or {}
        for nm, ann in (gen.items() if isinstance(gen, dict) else []):
            o = idx.get(nm)
            if not o or not isinstance(ann, dict): continue
            d = (ann.get("definition") or "").strip()
            if not d: continue
            # 自然语言定义不等于 OWL 等价类公理，仍按原始类（primitive class）处理。
            o["definition"] = d; o["isPrimitive"] = True
            # 该端点没有读取实例行；模型即使违反提示返回正例，也不得落入产物。
            if not ((o.get("example_provenance") or {}).get("status") == "observed"):
                o["example"] = ""
                o["example_provenance"] = {"status": "withheld_no_source",
                                             "note": "定义补全没有实例证据输入"}
            if (ann.get("counterExample") or "").strip(): o["counterExample"] = ann["counterExample"].strip()
            o["definition_status"] = "model_proposed_pending_review"
            if (o.get("counterExample") or "").strip():
                o["counterexample_status"] = "illustrative_pending_review"
            o.setdefault("maturity", "Provisional")
            o["bfo"] = o.get("bfo") or _KIND_BFO.get(o.get("kind"), "MaterialEntity")
            enriched += 1
    if enriched:
        with _WRITE_LOCK: _atomic_json(wp, ir)            # 原子回写(只增注释字段,非破坏)
    return jsonify({"ok": True, "enriched": enriched, "targets": len(targets),
                    "note": f"已为 {enriched}/{len(targets)} 个对象生成待复核的定义和概念反例；因未读取实例行，不生成正例" + ("" if enriched else ",引擎未产出有效建议")})

_VERB_VOCAB = ["归属", "包含", "组成", "产生", "输出", "触发", "输入", "服务", "参与", "承载", "实现", "描述"]
def _rel_ends(r):
    """兼容 links(source/target)与 relations(source_concept/target_concept)两种关系结构。"""
    if "source" in r or "target" in r: return r.get("source"), r.get("target")
    return r.get("source_concept"), r.get("target_concept")

def _llm_verbs(pairs):
    """pairs: [(s,s_cn,t,t_cn)]。让 LLM 从受控词表为每条关系选具体动词。返回 {(s,t): verb}。"""
    from agent_runtime import get_runtime, available
    lines = [f'{i}. {sn}({scn}) → {tn}({tcn})' for i, (sn, scn, tn, tcn) in enumerate(pairs)]
    prompt = ("你是本体关系标注专家。为每条『源→目标』关系,从受控词表选一个最贴切的关系动词。\n"
              f"受控词表(只能选其一):{'|'.join(_VERB_VOCAB)}\n关系清单:\n" + "\n".join(lines) +
              '\n\n只输出 JSON(无其它文字):{"<序号>":"动词", ...};动词必须来自受控词表,序号对应上面每条。')
    allowed = set(_VERB_VOCAB)
    for drv in _drv_order():
        if drv not in available(): continue
        ok, reply = get_runtime(drv).run_turn(f"vb_{uuid.uuid4().hex[:6]}", prompt, timeout=110)
        if ok and reply and not _looks_like_error(reply):
            m = re.search(r"\{[\s\S]*\}", reply)
            if m:
                try:
                    d = json.loads(m.group(0)); out = {}
                    for k, v in d.items():
                        try: i = int(k)
                        except Exception: continue
                        if 0 <= i < len(pairs) and v in allowed: out[(pairs[i][0], pairs[i][2])] = v
                    return out
                except Exception: pass
    return {}

@app.post("/api/ont/reground")
def ont_reground():
    """为通用关系建议受控动词，并按关系定义域和值域检查上层本体映射。

    LLM 只建议动词；标准关系是否可用由确定性类别检查决定。无法确认的关系仍作为
    本地对象属性保存，不构造 BFO/IOF IRI。
    """
    body = request.json or {}; key = body.get("graph")
    ir, wp, err = _open_writable(key)
    if err: return err
    rels = ir.get("links") if isinstance(ir.get("links"), list) and ir.get("links") else ir.get("relations")
    if not isinstance(rels, list) or not rels: return jsonify({"ok": True, "grounded": 0, "note": "无关系"})
    cn = {(o.get("name") or o.get("id")): (o.get("cn") or o.get("name") or o.get("id")) for o in ir.get("objects", [])}
    categories = {(o.get("name") or o.get("id")):
                  (o.get("bfo") or ontology_grounding.default_category(o.get("kind")))
                  for o in ir.get("objects", [])}
    todo = [r for r in rels if (r.get("verb") or "关联") in ("", "关联")]
    if todo:
        try:
            from agent_runtime import available
            if not available(): return jsonify({"error": "智能引擎离线,无法标注关系动词(不臆造)"}), 503
        except Exception:
            return jsonify({"error": "智能引擎不可用"}), 503
    annotated = 0
    for i in range(0, len(todo), 30):
        chunk = todo[i:i + 30]
        pairs = [(s, cn.get(s, s), t, cn.get(t, t)) for r in chunk for (s, t) in [_rel_ends(r)]]
        vmap = _bounded(lambda p=pairs: _llm_verbs(p), 120) or {}
        for r in chunk:
            s, t = _rel_ends(r); v = vmap.get((s, t))
            if v:
                r["verb"] = v
                annotated += 1
    mapped = 0
    for r in rels:                                        # 新旧 IR 统一做官方 IRI 与类别相容性检查
        s, t = _rel_ends(r)
        item = ontology_grounding.normalize(
            r.get("founded_relation"), r.get("temporal"), r.get("verb", "关联"),
            categories.get(s), categories.get(t),
        )
        r["founded_relation"] = item["relation"]
        r["grounding_iri"] = item["iri"]
        r["grounding_status"] = item["status"]
        r["grounding_reason"] = item["reason"]
        r["temporal"] = item["temporal"]
        mapped += item["status"] == "mapped"
    with _WRITE_LOCK: _atomic_json(wp, ir)
    return jsonify({"ok": True, "annotated": annotated, "targets": len(todo), "mapped": mapped,
                    "unmapped": len(rels) - mapped,
                    "note": (f"已为 {annotated}/{len(todo)} 条待标注关系生成受控动词；"
                             f"{mapped}/{len(rels)} 条关系通过 BFO/IOF 类别相容性检查")})

@app.post("/api/ont/maturity")
def ont_maturity():
    """成熟度人审:把对象成熟度置为 Provisional/Released/Deprecated(IOF 治理),回写 IR。"""
    body = request.json or {}; key = body.get("graph")
    oid = (body.get("object") or "").strip(); mat = (body.get("maturity") or "").strip()
    if mat not in ("Provisional", "Released", "Deprecated"):
        return jsonify({"error": "maturity 仅 Provisional/Released/Deprecated"}), 400
    ir, wp, err = _open_writable(key)
    if err: return err
    o = next((x for x in ir.get("objects", []) if x.get("name") == oid or x.get("id") == oid), None)
    if not o: return jsonify({"error": "对象不存在"}), 404
    o["maturity"] = mat
    with _WRITE_LOCK: _atomic_json(wp, ir)
    return jsonify({"ok": True, "object": oid, "maturity": mat})

@app.get("/api/ont/metadata")
def ont_metadata():
    """拼音↔中文字典(对齐平台 /api/metadata / export_metadata)"""
    ir = load_ir_edited(request.args.get("graph", "demo")) or {}
    dic = {}
    for o in ir.get("objects", []):
        dic[o.get("name") or o.get("id")] = o.get("cn")
        for a in o.get("attrs", []):
            if a.get("cn"): dic[f'{o.get("id")}.{a["col"]}'] = a["cn"]
    return jsonify({"count": len(dic), "dict": dic})

@app.post("/api/ont/rebuild")
def ont_rebuild():
    """重建:清空该图谱的草案编辑层,回到构建产物基线(对齐平台 /api/rebuild)。

    两道保险,缺一不可:
    ① 必须显式 confirm —— 这是丢弃全部人审与编辑成果的破坏性动作,而 undo 只退一步,
       退不回来。上游引擎的同名端点一直要求 confirm,auto-ontology 技能也照此写明
       「不带 confirm 服务端会拒绝」;此处若不要求,照技能行事的 agent 会在这里踩空。
    ② 不直接删,改名留底 —— 误触后还能从 .discarded 找回。"""
    body = request.json or {}
    key = body.get("graph", "demo")
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    ep = _edits_path(key)
    n_ops = len((_load_edits(key) or {}).get("ops") or []) if os.path.exists(ep) else 0
    if body.get("confirm") is not True:
        return jsonify({"error": "重建会丢弃该图谱草案层的全部编辑(当前 %d 条)且 undo 退不回来,"
                                 "请先向用户确认,然后带 {\"confirm\": true} 重试" % n_ops,
                        "pending_ops": n_ops}), 400
    discarded = ""
    if os.path.exists(ep):
        with _WRITE_LOCK:
            discarded = ep + ".discarded"
            os.replace(ep, discarded)          # 留底而非删除:误触可恢复
    ir = load_ir(key)
    return jsonify({"ok": True, "objects": len(ir.get("objects", [])) if ir else 0,
                    "discarded_ops": n_ops,
                    "backup": os.path.basename(discarded) if discarded else ""})

@app.post("/api/sparql")
def sparql():
    """原生 SPARQL:优先代理经典引擎(rdflib);未就绪时对当前 IR 做轻量三元组匹配兜底"""
    body = request.json or {}
    query = body.get("query", ""); key = body.get("graph", "demo")
    # 拒绝 SERVICE 联邦子句:rdflib 会向任意 URL 发外连,构成 SSRF
    if re.search(r"\bSERVICE\b", query, re.I):
        return jsonify({"error": "出于安全,SPARQL SERVICE 联邦查询已禁用"}), 400
    # 拒绝 FROM/FROM NAMED 引外部 IRI(file:///、http(s)://…):防经数据集子句读本地文件或外连(SSRF/LFI)
    if re.search(r"\bFROM\b[\s\w]*<\s*(?:file|https?|ftp)\s*:", query, re.I):
        return jsonify({"error": "出于安全,SPARQL FROM 外部数据集(file/http)已禁用"}), 400
    try:
        import rdflib
        ir = load_ir_edited(key)
        if not ir: return jsonify({"error": f"图谱不存在: {key}"}), 404
        # 用本系统的 IOF 注释化 Turtle 作 substrate:让 iof-av 定义/反例/BFO 归类/关系接地都可被 SPARQL 查询
        g = rdflib.Graph(); g.parse(data=_ir_to_turtle(key, ir), format="turtle")
        def _run():
            with _RDF_LOCK:   # 串行化 SPARQL 解析+执行,规避 pyparsing 语法状态并发污染
                qres = g.query(query)
                vs = [str(v) for v in (qres.vars or [])]
                return vs, [{v: str(r[i]) for i, v in enumerate(vs)} for r in qres][:500]
        out, err, timed_out = _bounded_ex(_run, 8, default=None)   # 限时执行,防止病态查询占满 CPU
        if timed_out:
            return jsonify({"error": "SPARQL 执行超时(>8s):查询过重或图谱过大,请增加过滤或 LIMIT"}), 400
        if err is not None:   # 语法/语义错误——如实回报,不再伪装成超时
            return jsonify({"error": f"SPARQL 查询错误: {str(err)[:200]}"}), 400
        vars_, rows = out
        return jsonify({"type": "SELECT", "vars": vars_, "rows": rows, "total": len(rows), "graph": key})
    except Exception as e:
        return jsonify({"error": f"SPARQL 失败: {str(e)[:200]}"}), 400

@app.post("/api/query")
def query():
    body = request.json or {}
    sql = body.get("sql", "")
    src = body.get("src") or "demo"
    if not sql_is_readonly(sql): return jsonify({"error": "仅允许只读 SELECT/WITH 查询"}), 400
    # SQL 工作台的 SQL 本就由使用者书写;能力边界由「只读 + 单语句 + 只读连接」界定。
    # 单语句这一条对外部库尤其关键:pymysql/psycopg2 可能执行堆叠语句,而 sql_is_readonly 只看开头。
    if not _single_statement(sql): return jsonify({"error": "仅允许单条查询语句"}), 400
    try:
        if src in ("demo", ""): return jsonify(q(sql, attach_uploads=True))
        db, nm = _resolve_src(src)
        if db: return jsonify(q(sql, db=db))
        conn = _find_conn(src)
        if conn and conn.get("kind") not in ("sqlite", "api"):    # C7 SQL 工作台直查外部库
            return jsonify({"live": True, **_ext_query(conn, sql)})
        return jsonify({"error": f"数据源「{nm}」不可查询"}), 400
    except Exception as e: return jsonify({"error": str(e)}), 400

_QA_CACHE: dict[str, Any] = {}   # 深度问数结果缓存:仅供明确允许复用的非交互调用
_QA_CACHE_LOCK = threading.Lock()


def _qa_bypass_cache(body):
    """交互式问数的强制刷新策略；兼容历史 ``nocache`` 字段。

    bypass 不只是“不读”：还会清除同键旧值，且本次结果不再写入缓存。否则用户
    点过一次示例后，下一次虽真实执行，完成结果仍会重新污染缓存，造成行为反复。
    """
    policy = str(body.get("cache_policy") or "").strip().lower()
    if policy in {"bypass", "no-store", "reload"}:
        return True
    value = body.get("nocache")
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _qa_cache_begin(question, history, focus_tables, graph_keys, bypass=False):
    """为一次问数固定缓存键，并以线程安全方式读取或清除旧值。"""
    key = _qa_key(question, history, focus_tables, graph_keys)
    with _QA_CACHE_LOCK:
        if bypass:
            _QA_CACHE.pop(key, None)
            return key, None
        return key, _QA_CACHE.get(key)


def _qa_cache_store(key, response):
    with _QA_CACHE_LOCK:
        if len(_QA_CACHE) >= 200:
            _QA_CACHE.pop(next(iter(_QA_CACHE)))
        _QA_CACHE[key] = response


def _qa_scope(body):
    """归一问数请求的表/图谱作用域；同步与流式端点共用。"""
    raw_tables = body.get("tables") or []
    raw_graphs = body.get("graphs") or []
    if not isinstance(raw_tables, list): raw_tables = []
    if not isinstance(raw_graphs, list): raw_graphs = []
    focus_tables = []
    for value in raw_tables:
        table = str(value or "").strip()
        if table and re.fullmatch(_SQL_IDENT, table) and table not in focus_tables:
            focus_tables.append(table)
    graph_keys = []
    for value in raw_graphs:
        key = str(value or "").strip()
        if key and not _bad_gkey(key) and key not in graph_keys:
            graph_keys.append(key)
    return focus_tables[:200], graph_keys[:30]


def _qa_anchor_explanation(anchor, results=None):
    """把本体规划候选与最终 SQL 事实分开，供解释型界面直接呈现。

    ``anchor.objects/relations`` 是交给规划器的候选范围，不等同于 SQL 实际使用。
    最终表清单只从成功返回的结果 SQL 的 FROM/JOIN 反解；JOIN 数量只统计 SQL
    中真实出现的 JOIN 关键字，避免把“7 条候选关系”误说成“执行了 7 次 JOIN”。
    """
    objects = [o for o in (anchor or {}).get("objects", []) if isinstance(o, dict)]
    relations = [r for r in (anchor or {}).get("relations", []) if isinstance(r, dict)]
    ontology = (anchor or {}).get("ontology") or {}
    by_reason = {}
    for obj in objects:
        reason = str(obj.get("reason") or "其他依据")
        by_reason.setdefault(reason, []).append(str(obj.get("table") or ""))

    if results is None:
        return {
            "phase": "planning",
            "ontology_object_count": ontology.get("objects") or len(objects),
            "context_object_count": len(objects),
            "relation_candidate_count": len(relations),
            "used_tables": [],
            "context_only_tables": [str(o.get("table") or "") for o in objects if o.get("table")],
            "sql_join_count": None,
            "result_sql_count": 0,
            "selection": [{"reason": k, "count": len(v), "tables": v}
                          for k, v in by_reason.items()],
        }

    used_tables, join_count, sql_count = [], 0, 0
    ident = r'[A-Za-z_][A-Za-z0-9_."`\[\]]*'
    for result in results or []:
        sql = str((result or {}).get("sql") or "")
        if not sql:
            continue
        sql_count += 1
        join_count += len(re.findall(r"\bjoin\b", sql, re.I))
        ctes = {m.group(1).lower() for m in
                re.finditer(r"(?:\bwith\b|,)\s*(%s)\s+as\s*\(" % ident, sql, re.I)}
        for match in re.finditer(r"\b(?:from|join)\s+(%s)" % ident, sql, re.I):
            table = match.group(1).strip().strip('"`[]').lower()
            if table.startswith("up."):
                table = table.split(".")[-1]
            if table and table not in ctes and table not in used_tables:
                used_tables.append(table)

    used_set = set(used_tables)
    context_only = [str(o.get("table") or "") for o in objects
                    if o.get("table") and str(o.get("table")).lower() not in used_set]
    return {
        "phase": "complete",
        "ontology_object_count": ontology.get("objects") or len(objects),
        "context_object_count": len(objects),
        "relation_candidate_count": len(relations),
        "used_tables": used_tables,
        "context_only_tables": context_only,
        "sql_join_count": join_count,
        "result_sql_count": sql_count,
        "selection": [{"reason": k, "count": len(v), "tables": v}
                      for k, v in by_reason.items()],
    }


def _qa_key(question, history, focus=None, graphs=None):
    import hashlib
    up_sig = ""                                          # 上传库指纹:上传数据变更后作废旧缓存,避免同名表复用陈旧结果
    try:
        if os.path.exists(UPLOAD_DB): up_sig = str(int(os.path.getmtime(UPLOAD_DB)))
    except Exception: pass
    # 本体编辑指纹:本体是问数的语义锚点(召回/口径/双盲全靠它),改了本体却复用旧答案,
    # 用户会持续拿到旧语义下的结果——别名新增后仍答不上就是这么来的(实测发现)
    ont_sig = ""
    for _gk in (sorted(graphs) if graphs else ["demo"]):     # 锚定本体各自的编辑指纹都要进键
        try:
            _ep = _edits_path(_gk)
            if os.path.exists(_ep): ont_sig += _gk + ":" + str(int(os.path.getmtime(_ep)))
        except Exception: pass
    sig = json.dumps([question, [h.get("q", "") for h in (history or [])[-2:]], sorted(focus or []),
                      sorted(graphs or []), up_sig, ont_sig], ensure_ascii=False)
    return hashlib.md5(sig.encode("utf-8")).hexdigest()

@app.post("/api/chat")
def chat():
    body = request.json or {}
    question = body.get("q", "").strip()
    history = body.get("history") or []          # [{q, summary}] 最近几轮
    focus_tables, graph_keys = _qa_scope(body)
    if not question: return jsonify({"error": "empty"}), 400
    bypass_cache = _qa_bypass_cache(body)
    cache_key, hit = _qa_cache_begin(
        question, history, focus_tables, graph_keys, bypass=bypass_cache)
    if hit:
        return jsonify({**hit, "cached": True})
    ir_gate, gate_keys, fallback, qa_profile = _qa_anchor_ir(graph_keys)
    scope_error = _qa_scope_error(graph_keys, gate_keys, fallback, qa_profile)
    if scope_error:
        return jsonify({"error": scope_error, "code": "ontology_not_queryable",
                        "ontology": {"keys": gate_keys, **qa_profile}}), 422
    load_info = "加载本体上下文 · " + "、".join(_graph_name(key) for key in gate_keys)
    if fallback: load_info += " · " + fallback
    steps = [{"step": "load_ontology", "ok": True, "info": load_info}]
    q_eff, co = _carryover(question, history, ir_gate)          # B5 指代延续
    if co: steps.append({"step": "coreference", "ok": True, "info": f"多轮指代 · 延续上文对象:{co}"})
    anchor = {"objects": [], "relations": [], "metrics": [], "scoped": False,
              "graphs": graph_keys, "focus_n": len(focus_tables), "question": q_eff}
    ctx = build_context(q_eff, focus_tables=focus_tables, trace=anchor, graph_keys=graph_keys)
    if history:
        hist_txt = "\n".join(f"上轮问: {h.get('q','')}\n上轮结果摘要: {h.get('summary','')[:800]}" for h in history[-2:])
        ctx = f"[对话历史,供追问理解指代]\n{hist_txt}\n\n[库结构]\n{ctx}"
        steps.append({"step": "load_history", "ok": True, "info": f"带入 {len(history[-2:])} 轮上下文"})
    steps.append({"step": "build_context", "ok": True, "info": f"{len(ctx)}字符 schema"})
    plan = None
    _sk = _match_qa_skill(question)                   # #5 沉淀技能复用(与流式链同语义)
    if _sk:
        plan = {"analyses": (_sk.get("analyses") or [])[:4], "note": "沉淀技能复用"}
        steps.append({"step": "skill_reuse", "ok": True, "info": f"命中沉淀技能,复用 {len(plan['analyses'])} 条已验证 SQL"})
    # 硬性时间预算:引擎 90s 内不出计划就走内置模板,保证请求永不卡死
    if plan is None:
        plan = _bounded(lambda: agent_sql_plan(q_eff, ctx, steps), 90)
    if not plan:
        steps.append({"step": "plan_timeout", "ok": False, "info": "引擎超时/离线 → 内置模板兜底"})
        plan = fallback_plan(question)
    results = []
    for a in (plan.get("analyses") or [])[:4]:
        sql = a.get("sql", "")
        if not sql_is_readonly(sql):
            steps.append({"step": "exec_sql", "ok": False, "info": "非只读SQL被拒: " + sql[:60]}); continue
        okv, why = _qa_validate_sql(
            sql, ir_gate, strict=bool(graph_keys and gate_keys != ["demo"] and not fallback))
        if not okv:
            steps.append({"step": "ontology_gate", "ok": False, "info": "口径拦截:" + why}); continue
        # DR-026 双盲意图检测:口径校验管「SQL 合不合规」,这里管「答的是不是问的那件事」。
        # 只观测不阻断——确定性反解也会有漏判(如口径卡走视图名),
        # 因误判挡住正确答案的代价远高于标注一句存疑。
        try:
            import intent_check, usage_stat
            _ic = intent_check.cross_check(question, sql, ir_gate)
            steps.append(intent_check.step_of(_ic))
            # DR-026 使用度埋点:复用双盲已反解出的对象,零额外解析开销;失败静默(旁路)
            usage_stat.record(WORK, gate_keys[0], [o["key"] for o in _ic["actual"]["objects"]], "query")
            for _o in _ic["actual"]["objects"]:
                if _o.get("table") and _o["table"] not in anchor.setdefault("used", []):
                    anchor["used"].append(_o["table"])
        except Exception:
            pass
        try:
            data = q(sql, attach_uploads=True)
            steps.append({"step": "exec_sql", "ok": True, "info": f'{a.get("title","")} → {len(data["rows"])}行'})
            results.append({"title": a.get("title", "分析"), "sql": sql, "chart": a.get("chart") or {}, "data": data})
        except Exception as e:
            steps.append({"step": "exec_sql", "ok": False, "info": f'{a.get("title","")}: {str(e)[:100]}'})
    if results:
        text = _bounded(lambda: narrative_llm(question, results, steps), 50) or _rule_summary(results)
    else:
        text = "查询均失败,请换个问法或检查指标是否绑表。"
    summary = "; ".join(f"{r['title']}[{r['sql'][:120]}]→{len(r['data']['rows'])}行,末行{json.dumps(r['data']['rows'][-1] if r['data']['rows'] else {}, ensure_ascii=False)[:150]}" for r in results)[:1200]
    anchor["explanation"] = _qa_anchor_explanation(anchor, results)
    anchor["used"] = anchor["explanation"]["used_tables"]
    resp = {"steps": steps, "results": results, "narrative": text, "note": plan.get("note", ""),
            "summary": summary, "metric_cards": _metric_cards(question, ir_gate), "anchor": anchor}
    if results and not bypass_cache:              # 强制刷新请求既不读缓存，也不回写缓存
        _qa_cache_store(cache_key, resp)
    return jsonify(resp)

@app.post("/api/chat/stream")
def chat_stream():
    """深度问数流式:SSE 逐条推送执行步骤(对齐平台 chat-bi 的实时执行记录),末尾 done 事件带完整结果。"""
    body = request.json or {}
    question = (body.get("q") or "").strip()
    history = body.get("history") or []
    bypass_cache = _qa_bypass_cache(body)
    # default=str:即便某列是 BLOB/bytes 等不可 JSON 序列化的值,也不会让整条 SSE 流因异常静默中断(前端卡在"运行中")
    def sse(obj): return "data: " + json.dumps(obj, ensure_ascii=False, default=str) + "\n\n"
    def gen():
        import time as _t
        t0 = _t.time(); session = uuid.uuid4().hex[:8]
        if not question:
            yield sse({"type": "error", "error": "empty"}); return
        focus_tables, graph_keys = _qa_scope(body)          # 与同步 /api/chat 共用作用域归一
        cache_key, hit = _qa_cache_begin(
            question, history, focus_tables, graph_keys, bypass=bypass_cache)
        if hit:
            yield sse({"type": "done", **hit, "cached": True, "elapsed": 0.0, "session": session}); return
        def stp(step, ok, info=""):
            return {"step": step, "ok": ok, "info": info, "ts": _t.strftime("%H:%M:%S")}
        steps = []
        def push(step, ok, info=""):
            s = stp(step, ok, info); steps.append(s); return sse({"type": "step", **s})
        if focus_tables:
            yield push("scope_source", True, f"数据源限定 · {len(focus_tables)} 张表")
        ir_gate, _gate_keys, _gate_fallback, qa_profile = _qa_anchor_ir(graph_keys)
        # 本体名要据实回显:此处曾写死「示例」,选了自建本体也照喊示例,
        # 与下一步 anchor_ontology 打架,读日志的人会以为锚错了本体
        yield push("load_ontology", True,
                   "加载本体上下文 · %s" % "、".join(_graph_name(k) for k in _gate_keys))
        scope_error = _qa_scope_error(graph_keys, _gate_keys, _gate_fallback, qa_profile)
        if scope_error:
            yield push("scope_ontology", False, qa_profile.get("reason") or "所选本体不可问数")
            yield sse({"type": "error", "code": "ontology_not_queryable", "error": scope_error,
                       "ontology": {"keys": _gate_keys, **qa_profile}})
            return
        q_eff, co = _carryover(question, history, ir_gate)      # B5 多轮指代:上文本体对象延续
        if co:
            yield push("coreference", True, f"多轮指代 · 延续上文对象:{co}")
        _exp = expand_terms(q_eff)                               # A1 术语扩展检索(术语管理词典)
        if _exp:
            yield push("term_expand", True, f"术语扩展 · 词典命中 {len(_exp)} 个同义/中英对照词:{'、'.join(_exp[:6])}{'…' if len(_exp) > 6 else ''}")
        # scoped 取「实际限定到表」而非「选了图谱」:选中图谱若无绑表,召回其实是全库,
        # 标成「数据源限定」会让人误以为范围已收窄
        anchor = {"objects": [], "relations": [], "metrics": [],
                  "scoped": False, "graphs": graph_keys,
                  "focus_n": len(focus_tables), "question": q_eff}
        ctx = build_context(q_eff, focus_tables=focus_tables, trace=anchor, graph_keys=graph_keys)
        anchor["explanation"] = _qa_anchor_explanation(anchor)
        _ont = anchor.get("ontology") or {}
        yield push("anchor_ontology", True,
                   f"锚定本体 · {'、'.join(_ont.get('names') or ['示例'])}"
                   f"({_ont.get('objects',0)} 对象 / {_ont.get('relations',0)} 关系)"
                   + (" · " + anchor["fallback"] if anchor.get("fallback") else ""))
        # 锚定视图先推一次:引擎规划要几十秒,这期间人已经能看到「本体锚到了哪些对象」
        yield sse({"type": "anchor", "anchor": anchor})
        if history:
            hist_txt = "\n".join(f"上轮问: {h.get('q','')}\n上轮结果摘要: {h.get('summary','')[:800]}" for h in history[-2:])
            ctx = f"[对话历史,供追问理解指代]\n{hist_txt}\n\n[库结构]\n{ctx}"
            yield push("load_history", True, f"带入 {len(history[-2:])} 轮上下文")
        ntab = ctx.count("\n表 ") + (1 if ctx.startswith("表 ") else 0)
        yield push("match_schema", True, f"匹配相关表/指标 · 命中 {ntab} 张表")
        _nrel = ctx.count("⋈")
        yield push("ontology_relations", True,
                   f"沿本体关系召回 · 提供 {_nrel} 条候选 JOIN 依据(是否采用以最终 SQL 为准)" if _nrel else "选中表间暂无已验证关系,JOIN 由引擎按列名推断")
        yield push("build_context", True, f"组装库结构上下文 · {len(ctx)} 字符")
        plan = None
        _sk = _match_qa_skill(question)               # #5 沉淀技能复用:命中即免引擎规划(秒级)
        if _sk:
            plan = {"analyses": (_sk.get("analyses") or [])[:4], "note": "沉淀技能复用"}
            yield push("skill_reuse", True, f"命中沉淀技能「{(_sk.get('question') or '')[:24]}」· 复用 {len(plan['analyses'])} 条已验证 SQL,跳过引擎规划")
        else:
            yield sse({"type": "status", "text": "智能引擎生成 SQL 分析计划(约 40-90s)…"})
        # 引擎计划:后台线程跑,主循环轮询 plan_steps 实时外推 + 心跳
        plan_steps, box = [], {"v": None, "done": False}
        def _run():
            try: box["v"] = agent_sql_plan(q_eff, ctx, plan_steps)
            except Exception: pass
            box["done"] = True
        if plan is not None:
            box["done"] = True                        # 技能复用命中:不启动引擎
        else:
            th = threading.Thread(target=_run, daemon=True); th.start()
        emitted = 0; waited = 0.0
        while not box["done"] and waited < 95:
            th.join(1.0); waited += 1.0
            while emitted < len(plan_steps):
                s = plan_steps[emitted]; emitted += 1; steps.append(s)
                yield sse({"type": "step", **({"ts": _t.strftime("%H:%M:%S")}), **s})
            if not box["done"] and int(waited) % 4 == 0:
                yield sse({"type": "status", "text": f"引擎推理中… {int(waited)}s"})
        while emitted < len(plan_steps):
            s = plan_steps[emitted]; emitted += 1; steps.append(s)
            yield sse({"type": "step", **s})
        plan = plan or box["v"]
        if not plan:
            yield push("plan_timeout", False, "引擎超时/离线 → 内置模板兜底")
            plan = fallback_plan(question)
        analyses = (plan.get("analyses") or [])[:4]
        yield push("plan_ready", True, f"分析计划就绪 · 拆解为 {len(analyses)} 个子分析")
        results = []
        for idx, a in enumerate(analyses, 1):
            title = a.get("title", "分析")
            sql = a.get("sql", "")
            yield push("gen_sql", True, f"[{idx}/{len(analyses)}] 生成 SQL 规范:{title}")
            if not sql_is_readonly(sql):
                yield push("exec_sql", False, f"[{idx}] 非只读SQL被拒: " + sql[:50]); continue
            okv, why = _qa_validate_sql(
                sql, ir_gate,
                strict=bool(graph_keys and _gate_keys != ["demo"] and not _gate_fallback))
            if not okv:
                yield push("ontology_gate", False, f"[{idx}] 口径拦截:{why}"); continue
            yield push("ontology_gate", True, f"[{idx}] 本体校验通过 · 表与 JOIN 键均在本体边界内")
            try:                                     # DR-026 双盲意图检测(只观测不阻断)+ 使用度埋点
                import intent_check, usage_stat
                _ic = intent_check.cross_check(question, sql, ir_gate)
                yield push("intent_crosscheck", _ic["verdict"] in ("aligned", "unknown"),
                           f"[{idx}] " + intent_check.step_of(_ic)["info"])
                usage_stat.record(WORK, _gate_keys[0], [o["key"] for o in _ic["actual"]["objects"]], "query")
                # 将 SQL 实际使用的表回填锚定视图，区分召回对象与实际使用对象。
                for _o in _ic["actual"]["objects"]:
                    if _o.get("table") and _o["table"] not in anchor.setdefault("used", []):
                        anchor["used"].append(_o["table"])
            except Exception:
                pass
            try:
                data = q(sql, attach_uploads=True)
                nrow = len(data["rows"])
                yield push("exec_sql", True, f"[{idx}] 执行查询 → {nrow} 行 × {len(data['columns'])} 列")
                conf = 95 if nrow >= 6 else (70 if nrow >= 2 else 30)
                yield push("data_check", nrow > 0, f"[{idx}] 数据质量校验:{'通过' if nrow>=2 else '数据偏少'} (confidence={conf}%)")
                ct = (a.get("chart") or {}).get("type", "line")
                yield push("gen_chart", True, f"[{idx}] 生成图表规范:{ct} 图")
                one = {"title": title, "sql": sql, "chart": a.get("chart") or {}, "data": data}
                results.append(one)
                yield sse({"type": "result", "index": idx - 1, "result": one})   # 渐进推送:每完成一图即出
            except Exception as e:
                yield push("exec_sql", False, f"[{idx}] {title}: {str(e)[:90]}")
        if results:
            yield push("review", True, f"审视分析结果 · 检查数据完整性与口径 · {len(results)}/{len(analyses)} 项有效")
            yield sse({"type": "status", "text": "汇总数据,生成业务洞察报告…"})
            # B6 叙述流式:后台线程生成,片段进 chunks;主循环轮询逐段外推 narrative_delta
            chunks, nbox = [], {"v": None, "done": False}
            def _run_n():
                try: nbox["v"] = narrative_llm(question, results, steps, emit=chunks.append)
                except Exception: pass
                nbox["done"] = True
            nth = threading.Thread(target=_run_n, daemon=True); nth.start()
            n_emit, n_wait = 0, 0.0
            while not nbox["done"] and n_wait < 55:
                nth.join(0.5); n_wait += 0.5
                while n_emit < len(chunks):
                    yield sse({"type": "narrative_delta", "text": chunks[n_emit]}); n_emit += 1
            while n_emit < len(chunks):
                yield sse({"type": "narrative_delta", "text": chunks[n_emit]}); n_emit += 1
            text = nbox["v"] or _rule_summary(results)
            yield push("narrative", True, f"生成分析报告 · {len(text)} 字")
        else:
            yield push("review", False, "无有效数据,无法生成报告")
            text = "查询均失败,请换个问法或检查指标是否绑表。"
        summary = "; ".join(f"{r['title']}[{r['sql'][:120]}]→{len(r['data']['rows'])}行,末行{json.dumps(r['data']['rows'][-1] if r['data']['rows'] else {}, ensure_ascii=False)[:150]}" for r in results)[:1200]
        anchor["explanation"] = _qa_anchor_explanation(anchor, results)
        anchor["used"] = anchor["explanation"]["used_tables"]
        resp = {"steps": steps, "results": results, "narrative": text, "note": plan.get("note", ""),
                "summary": summary, "metric_cards": _metric_cards(question, ir_gate),
                "anchor": anchor}   # 随 done 落一份:命中缓存与历史回放时锚定视图不丢
        if results and not bypass_cache:
            _qa_cache_store(cache_key, resp)
        yield sse({"type": "done", **resp, "cached": False, "elapsed": round(_t.time() - t0, 1), "session": session})
    from flask import Response, stream_with_context
    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                             "Pragma": "no-cache", "Expires": "0", "X-Accel-Buffering": "no"})

_QA_SKILLS_F = os.path.join(WORK, "qa_skills.json")

def _match_qa_skill(question):
    """#5 沉淀技能复用(修「存而不用」):问题归一化后与沉淀技能匹配(等值或互为包含),
    命中即复用其已验证 SQL、跳过引擎规划 —— 沉淀自此参与回答,不再只是展示品。"""
    try: store = json.load(open(_QA_SKILLS_F)) if os.path.exists(_QA_SKILLS_F) else []
    except Exception: return None
    norm = lambda s: re.sub(r"[\s??。.,,、;;::!!()()\-—]+", "", str(s or "")).lower()
    qn = norm(question)
    if len(qn) < 4: return None
    for sk in store:
        sn = norm(sk.get("question"))
        if sn and (sn == qn or (len(sn) >= 6 and (sn in qn or qn in sn))) and sk.get("analyses"):
            return sk
    return None
@app.post("/api/diagnose/stream")
def diagnose_stream():
    """最小根因诊断(P21 四步的前三步):本体识别意图 → 沿本体关系召回 → schema 约束生成(≤3 根因+检查清单)。
    输出必须落在本体边界内:根因路径只允许引用召回的对象/关系,越界降 candidate;引擎离线则如实拒答(不臆造)。"""
    q = ((request.json or {}).get("q") or "").strip()
    pipeline = bool((request.json or {}).get("pipeline"))   # M3 opt-in:拆成 诊断→(确定性评估)→方案,每节点单一职责
    def sse(o): return "data: " + json.dumps(o, ensure_ascii=False, default=str) + "\n\n"
    def gen():
        import time as _t
        def push(step, ok, info=""):
            return sse({"type": "step", "step": step, "ok": ok, "info": info, "ts": _t.strftime("%H:%M:%S")})
        if not q:
            yield sse({"type": "error", "error": "请描述异常现象"}); return
        ir = load_ir_edited("demo") or {}
        objs, links = ir.get("objects", []), ir.get("links", [])
        # ① 本体识别意图:关键词对齐 对象(cn/表名) 与 指标
        def _score(o):
            cands = set()
            cn = (o.get("cn") or "").strip()
            if cn:
                cands.add(cn)
                for suf in ("事实表", "维度表", "汇总表", "明细表", "表"):   # 「退货事实表」也按「退货」匹配(与指代延续同套剥离)
                    if cn.endswith(suf) and len(cn) > len(suf) + 1: cands.add(cn[:-len(suf)])
            for v in (o.get("table") or "", o.get("name") or ""):
                if v: cands.add(str(v))
            best = 0
            for c in cands:
                if len(c) >= 2 and (c in q or c.lower() in q.lower()): best = max(best, len(c))
            return best
        ents = sorted(((_score(o), o) for o in objs), key=lambda x: -x[0])
        ents = [o for sc, o in ents[:2] if sc > 0]
        mets = []
        for _k, arr in (ir.get("metric_layers") or {}).items():
            for m in arr:
                if m.get("name") and m["name"] in q: mets.append(m)
        if not ents:
            yield push("intent", False, "未在本体中识别到相关对象——请在问题里带上业务对象名(如 生产工单/设备/产线)")
            yield sse({"type": "error", "error": "本体中未命中实体,诊断需先锚定对象(不作无锚定的自由生成)"}); return
        yield push("intent", True, "本体识别意图 · 实体:" + "、".join(o.get("cn") or o["id"] for o in ents)
                   + (" · 指标:" + "、".join(m["name"] for m in mets[:3]) if mets else "") + " · 类型:异常根因诊断")
        # ② 沿本体关系召回:从命中实体出发 BFS 2 跳
        adj = {}
        for l in links:
            if l.get("status") in ("rejected",): continue
            adj.setdefault(l.get("source"), []).append(l); adj.setdefault(l.get("target"), []).append(l)
        seen = {o["id"] for o in ents}; used_links = []; frontier = list(seen)
        for _hop in range(2):
            nxt = []
            for oid in frontier:
                for l in adj.get(oid, []):
                    other = l["target"] if l["source"] == oid else l["source"]
                    if l not in used_links and len(used_links) < 14: used_links.append(l)
                    if other not in seen and len(seen) < 8:
                        seen.add(other); nxt.append(other)
            frontier = nxt
        o_by_id = {o["id"]: o for o in objs}
        rel_objs = [o_by_id[i] for i in seen if i in o_by_id]
        rel_lines = []
        for l in used_links:
            sc, tc = (o_by_id.get(l["source"], {}).get("cn") or l["source"]), (o_by_id.get(l["target"], {}).get("cn") or l["target"])
            rel_lines.append(f"{sc} --{l.get('verb','关联')}({l.get('status')})--> {tc}")
        yield push("recall", True, f"沿本体关系召回 · 关联对象 {len(rel_objs)} 个 · 关系 {len(used_links)} 条(2 跳)")
        # ③ schema 约束生成(LLM 只能在召回边界内作答)
        allowed = sorted({(o.get("cn") or o["id"]) for o in rel_objs})
        schema = "\n".join(f"表 {o.get('table')}({o.get('cn')}): " + ", ".join(a_.get("cn") or a_["col"] for a_ in (o.get("attrs") or [])[:10]) for o in rel_objs if o.get("table"))
        yield sse({"type": "status", "text": "受本体边界约束生成候选根因(约 30-90s)…"})
        prompt = f"""你是制造业根因诊断专家。仅依据下面给定的本体对象、关系与表结构,诊断异常。
异常现象:{q}
[允许引用的对象(白名单,路径只能用这些)]:{"、".join(allowed)}
[对象间关系(带验证状态)]:
{chr(10).join(rel_lines)}
[表结构]:
{schema[:4000]}
只输出一个 JSON(无其它文字):
{{"causes":[{{"cause":"候选根因(一句话)","path":"对象A→对象B(只用白名单对象名)","evidence":"依据哪张表/字段/关系","confidence":"verified|candidate"}}],
  "checklist":["现场检查项(短句)"]}}
要求:causes 至多 3 条;凡依据的关系状态非 verified、或仅单表佐证,confidence 必须为 candidate;checklist 3-5 条;不得引用白名单之外的对象或编造字段。"""
        ans = None
        try:
            from agent_runtime import get_runtime, available
            for drv in _drv_order():
                if drv not in available(): continue
                ok, reply = _bounded(lambda d=drv: _llm_turn(get_runtime(d), f"dg_{uuid.uuid4().hex[:6]}", prompt, 150, task="diagnose"), 170, (False, "")) or (False, "")
                if ok and reply:
                    m = re.search(r"\{[\s\S]*\}", reply)
                    if m:
                        try: ans = json.loads(m.group(0)); break
                        except Exception: pass
        except Exception: pass
        if not isinstance(ans, dict) or not ans.get("causes"):
            yield push("llm_causes", False, "引擎超时/离线 —— 根因诊断需引擎在线,不作无证据的臆造")
            yield sse({"type": "error", "error": "引擎不可用,本次不产出根因(证据状态规范:宁可不答,不编结论)"}); return
        # ④ 边界校验:路径越界 → 降 candidate
        allow_set = set(allowed)
        causes = _dg_bounds(ans.get("causes"), allow_set)[:3]          # G1 边界校验(确定性)
        checklist = [str(x)[:60] for x in (ans.get("checklist") or [])[:5]]
        cl_flagged = []
        if pipeline:
            # M3 流水线:诊断已出 → 确定性评估排序 → 方案节点只据已排序根因出清单 → G2 校验
            causes = _dg_rank(causes)
            yield push("assess", True, "评估(确定性打分):" + " | ".join(f'#{c["rank"]} {c["cause"][:18]}' for c in causes[:3]))
            _top = "\n".join(f'#{c["rank"]} {c["cause"]}(路径 {c["path"]};依据 {c["evidence"]})' for c in causes[:3])
            p2 = ("你只负责一件事:为下列已确认的候选根因给出现场检查清单。不要重新诊断、不要改动根因。\n"
                  "[已排序候选根因]\n" + _top + "\n[允许引用的对象]:" + "、".join(allowed) + "\n"
                  '只输出 JSON:{"checklist":["现场检查项(短句,只引用上面对象或真实表名)"]}  3-5 条。')
            yield sse({"type": "status", "text": "方案节点:仅据已排序根因生成检查清单…"})
            _cl = None
            try:
                for drv in _drv_order():          # get_runtime/available 已在上方诊断节点导入,复用
                    if drv not in available(): continue
                    ok2, rp2 = _bounded(lambda d=drv: _llm_turn(get_runtime(d), f"dg2_{uuid.uuid4().hex[:6]}", p2, 90, task="diagnose"), 110, (False, "")) or (False, "")
                    if ok2 and rp2:
                        mm = re.search(r"\{[\s\S]*\}", rp2)
                        if mm:
                            try: _cl = json.loads(mm.group(0)); break
                            except Exception: pass
            except Exception: pass
            if isinstance(_cl, dict) and _cl.get("checklist"):
                _tbls = [o.get("table") for o in rel_objs if o.get("table")]
                kept, cl_flagged = _dg_checklist_gate(_cl.get("checklist"), allow_set, _tbls)   # G2 清单校验
                checklist = kept
                yield push("plan_gate", True, f"方案节点 · 清单 {len(kept)} 条通过" + (f" · {len(cl_flagged)} 条引用未知对象已标记" if cl_flagged else ""))
            else:
                yield push("plan_gate", False, "方案节点失败/超时 → 沿用诊断节点的清单(不臆造)")
        n_out = sum(1 for c in causes if not c["in_bounds"])
        yield push("bounds_check", True, f"本体边界校验 · {len(causes)} 条根因" + (f" · {n_out} 条路径越界已降 candidate" if n_out else " · 全部在界内"))
        yield sse({"type": "done", "entity": "、".join(o.get("cn") or o["id"] for o in ents),
                   "metrics": [m["name"] for m in mets[:3]], "causes": causes, "checklist": checklist,
                   "relations_used": len(used_links), "pipeline": pipeline,
                   "checklist_flagged": cl_flagged})
    from flask import Response
    return Response(gen(), mimetype="text/event-stream")

# ── 引擎设置(DR-017)── 常量与配置读写/应用/掩码已收敛到 srv_engine(跨簇共享,见文件头 import)
_apply_engine_cfg(_load_engine_cfg())        # 启动即应用持久化配置(覆盖 start.sh 缺省)

# ── 引擎设置路由已迁至 bp_engine blueprint(IR-011/DR-043);共享层在 srv_engine ──
# ── C9 问数评测(P20 落地):参考问题集 × 三组对照(A朴素 / B图谱 / C本体全量),自动判分 ──
_EVAL_SET_F = os.path.join(HERE, "benchmark", "qa_set.json")
_EVAL_RES_F = os.path.join(WORK, "eval_results.json")
EVAL_JOB = {"running": False, "progress": "", "done": 0, "total": 0, "started": ""}

def _eval_items():
    try: return (json.load(open(_EVAL_SET_F)) or {}).get("items") or []
    except Exception: return []

def _ctx_naive():
    """A 组:朴素 Text2SQL 基线 —— 只有英文表名+列名(截断),无中文语义/无关系/无指标。"""
    lines = []
    try:
        con = ro_connect(DB)
        for (t,) in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')][:10]
            lines.append(f"{t}({', '.join(cols)})")
        con.close()
    except Exception:
        pass
    out = "\n".join(lines)
    return out[:4000]

def _ctx_graph(question):
    """B 组:GraphRAG 式 —— 相关表+中文列注+本体关系 JOIN 提示,无指标层/无术语扩展。"""
    ir = load_ir_edited("demo") or {}
    kws = [w for w in re.split(r"[,，。？?\s]+", question) if w]
    def score(txt): return sum(1 for w in kws if w and w in txt)
    tabs = []
    for o in ir.get("objects", []):
        blob = (o.get("cn") or "") + (o.get("table") or "") + " ".join((a.get("cn") or "") + (a.get("col") or "") for a in o.get("attrs", []))
        tabs.append((score(blob), o))
    tabs.sort(key=lambda x: -x[0])
    picked = [o for s, o in tabs[:8] if s > 0] or [o for _, o in tabs[:5]]
    lines = [f'表 {o["table"]}: ' + ", ".join(f'{a["col"]}({a.get("cn","")})' for a in o.get("attrs", [])[:18]) for o in picked]
    jh = _join_hints(ir, [o.get("table") for o in picked])
    if jh: lines.append("表间关系(JOIN 用这些键):\n" + "\n".join(jh))
    return "\n".join(lines)

def _eval_sql_gen(question, ctx, model=None):
    """单题出 SQL(单分析,只要一个 JSON {\"sql\":...});→ (sql or None, err)"""
    try:
        from agent_runtime import get_runtime, available
    except Exception as e:
        return None, str(e)[:80]
    prompt = (f"你是数据分析引擎。基于 SQLite 库(日期是 TEXT 'YYYY-MM-DD')写一条 SQL 回答问题。\n"
              f"问题: {question}\n可用表结构:\n{ctx}\n"
              f'只输出一个 JSON(不要其它文字): {{"sql": "SELECT ..."}}\n'
              f"要求: 只用上面列出的表列;单条 SELECT;若问「万元」记得除以 10000;「⋈/⋈⋈」是可信 JOIN 键(⋈⋈ 沿链经中间表),不要自造 JOIN;『月均』分母用 count(DISTINCT 月),不得除以固定常数;『最…的』问题:聚合排序 LIMIT 1。")
    for drv in _drv_order():
        if drv not in available(): continue
        rt = get_runtime(drv)
        sid = f"ev_{uuid.uuid4().hex[:6]}"
        try:
            if model and _model_fits(drv, model):
                try: ok, reply = rt.run_turn(sid, prompt, timeout=75, model=model)
                except TypeError: ok, reply = rt.run_turn(sid, prompt, timeout=75)
            else:
                ok, reply = _llm_turn(rt, sid, prompt, 75, task="plan")
        except Exception as e:
            ok, reply = False, str(e)
        if ok and reply:
            m = re.search(r"\{[\s\S]*?\}", reply)
            if m:
                try: return (json.loads(m.group(0)).get("sql") or "").strip(), ""
                except Exception: pass
    return None, "引擎未产出 SQL"

def _eval_value(data):
    """结果 → 单值:首行第一个数值列;无数值取第一格字符串。"""
    rows = data.get("rows") or []
    if not rows: return None
    row = rows[0]
    for v in row.values():
        if isinstance(v, (int, float)) and not isinstance(v, bool): return v
    for v in row.values():
        if v is not None: return str(v)
    return None

def _eval_judge(item, val):
    """判分:数值容差(万元题同时接受 ×10000 的元口径);字符串等值/包含。"""
    if val is None: return False
    if "gold_str" in item:
        return str(val).strip() == item["gold_str"] or item["gold_str"] in str(val)
    try: v = float(val)
    except Exception: return False
    gold, tol = float(item["gold"]), float(item.get("tol") or 0.0)
    def near(g): return abs(v - g) <= max(tol * abs(g), 1e-9) + (0.5 if tol == 0 else 0)
    return near(gold) or (item.get("unit") == "万元" and near(gold * 10000))

def _run_eval_thread(model=None, limit=None):
    items = _eval_items()[: (limit or 99)]
    ir = load_ir_edited("demo") or {}
    arms = [("A", "朴素 Text2SQL(仅英文表列)", lambda it: _ctx_naive(), False),
            ("B", "图谱增强(中文语义+关系)", lambda it: _ctx_graph(it["q"]), False),
            ("C", "本体全量(语义+关系+指标+术语+口径校验)", lambda it: build_context(it["q"]), True)]
    EVAL_JOB.update(running=True, done=0, total=len(items) * len(arms),
                    started=time.strftime("%Y-%m-%d %H:%M:%S"), progress="启动")
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model or (_task_model("plan") or "(引擎缺省)"),
           "items": [], "arms": {k: {"name": nm, "exec": 0, "correct": 0, "cite": 0, "gate_block": 0} for k, nm, _, _ in arms}}
    for it in items:
        row = {"id": it["id"], "q": it["q"], "gold": it.get("gold", it.get("gold_str")), "arms": {}}
        for key, _nm, ctx_fn, gated in arms:
            EVAL_JOB["progress"] = f'{it["id"]} · {key} 组'
            sql, err = _eval_sql_gen(it["q"], ctx_fn(it), model=model)
            rec = {"sql": sql or "", "err": err, "exec": False, "value": None, "correct": False, "cite": False, "gate_block": False}
            if sql and sql_is_readonly(sql):
                if gated:
                    okv, why = _validate_sql_ontology(sql, ir)
                    if not okv:
                        rec["gate_block"] = True; rec["err"] = "口径拦截:" + why
                        out["arms"][key]["gate_block"] += 1
                if not rec["gate_block"]:
                    try:
                        data = q(sql, attach_uploads=True)
                        rec["exec"] = True; rec["value"] = _eval_value(data)
                        rec["correct"] = _eval_judge(it, rec["value"])
                    except Exception as e:
                        rec["err"] = str(e)[:120]
                sql_l = (sql or "").lower()
                rec["cite"] = all(t.lower() in sql_l for t in it.get("gold_tables") or [])
            a = out["arms"][key]
            a["exec"] += 1 if rec["exec"] else 0
            a["correct"] += 1 if rec["correct"] else 0
            a["cite"] += 1 if rec["cite"] else 0
            row["arms"][key] = rec
            EVAL_JOB["done"] += 1
        out["items"].append(row)
        _atomic_json(_EVAL_RES_F, out)          # 每题落盘:中断也保留已跑部分
    out["n"] = len(items)
    _atomic_json(_EVAL_RES_F, out)
    EVAL_JOB.update(running=False, progress="完成")

@app.post("/api/eval/run")
def eval_run():
    """启动一轮三组评测(后台线程,每题落盘);可传 model 覆盖本轮出 SQL 的模型、limit 限题数。"""
    if EVAL_JOB["running"]: return jsonify({"error": "评测已在运行", "job": EVAL_JOB}), 409
    if not _eval_items(): return jsonify({"error": "题集缺失(benchmark/qa_set.json)"}), 500
    body = request.json or {}
    model = (body.get("model") or "").strip()[:80] or None
    limit = body.get("limit")
    threading.Thread(target=_run_eval_thread, kwargs={"model": model, "limit": limit}, daemon=True).start()
    return jsonify({"ok": True, "total": len(_eval_items()[: (limit or 99)]) * 3}), 202

@app.get("/api/eval/status")
def eval_status(): return jsonify(EVAL_JOB)

@app.get("/api/eval/results")
def eval_results():
    try: return jsonify(json.load(open(_EVAL_RES_F)))
    except Exception: return jsonify({"empty": True, "note": "尚未跑过评测"})

@app.get("/api/eval/set")
def eval_set(): return jsonify({"items": _eval_items(), "n": len(_eval_items())})

QA_FB = os.path.join(WORK, "qa_feedback.json")

def _load_fb():
    try: return json.load(open(QA_FB))
    except Exception: return []

@app.post("/api/chat/feedback")
def chat_feedback():
    """问数/诊断结果反馈落库:needs_work 进入本体迭代候选队列(评审页处理)——P24 证据回流环"""
    body = request.json or {}
    verdict = body.get("verdict")
    if verdict not in ("useful", "needs_work"): return jsonify({"error": "verdict 须为 useful/needs_work"}), 400
    item = {"id": uuid.uuid4().hex[:8], "ts": time.strftime("%Y-%m-%d %H:%M"),
            "question": str(body.get("question") or "")[:300], "verdict": verdict,
            "comment": str(body.get("comment") or "")[:500], "mode": str(body.get("mode") or "chat")[:16],
            "resolved": False}
    with _WRITE_LOCK:
        fb = _load_fb(); fb.insert(0, item); _atomic_json(QA_FB, fb[:500])
    return jsonify({"ok": True, "id": item["id"]})

@app.get("/api/chat/feedback")
def chat_feedback_list():
    fb = _load_fb()
    return jsonify({"items": fb, "open": sum(1 for x in fb if x["verdict"] == "needs_work" and not x.get("resolved")),
                    "useful": sum(1 for x in fb if x["verdict"] == "useful")})

@app.post("/api/chat/feedback/resolve")
def chat_feedback_resolve():
    fid = (request.json or {}).get("id", "")
    with _WRITE_LOCK:
        fb = _load_fb()
        it = next((x for x in fb if x["id"] == fid), None)
        if not it: return jsonify({"error": "反馈不存在"}), 404
        it["resolved"] = True; it["resolved_ts"] = time.strftime("%Y-%m-%d %H:%M")
        _atomic_json(QA_FB, fb)
    return jsonify({"ok": True})

@app.post("/api/chat/save_skill")
def chat_save_skill():
    """把一次深度问数(问题 + 生成的 SQL/图表规格)沉淀为可复用技能(对齐平台『沉淀为 Skill』)。"""
    body = request.json or {}
    question = (body.get("question") or "").strip()
    results = body.get("results") or []
    if not question or not results: return jsonify({"error": "缺少问题或结果"}), 400
    sk = {"id": "qas_" + uuid.uuid4().hex[:8], "question": question,
          "analyses": [{"title": r.get("title"), "sql": r.get("sql"), "chart": r.get("chart")} for r in results],
          "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    with _WRITE_LOCK:
        try: store = json.load(open(_QA_SKILLS_F)) if os.path.exists(_QA_SKILLS_F) else []
        except Exception: store = []
        store.insert(0, sk); store = store[:100]
        _atomic_json(_QA_SKILLS_F, store)
    return jsonify({"ok": True, "id": sk["id"], "count": len(sk["analyses"])})

@app.get("/api/chat/skills")
def chat_skills():
    try: return jsonify(json.load(open(_QA_SKILLS_F)) if os.path.exists(_QA_SKILLS_F) else [])
    except Exception: return jsonify([])

@app.get("/api/glossary")
def glossary():
    """术语管理:英文↔中文业务词典(来自 translate_cn 词表,对齐平台『术语管理』)"""
    try:
        import translate_cn as T
    except Exception as e:
        return jsonify({"terms": [], "count": 0, "error": str(e)[:100]})
    terms = []
    for src, typ in ((getattr(T, "TABLE_PHRASE", {}), "表名"), (getattr(T, "COL_PHRASE", {}), "列名"),
                     (getattr(T, "PHRASE", {}), "短语"), (getattr(T, "TOKEN", {}), "词元")):
        for en, cn in src.items(): terms.append({"en": en, "cn": cn, "type": typ})
    return jsonify({"terms": terms, "count": len(terms)})

@app.get("/api/routes")
def api_routes():
    """API 服务目录(对齐平台『数据服务』):列出本系统全部 HTTP 接口"""
    routes = []
    for r in app.url_map.iter_rules():
        if r.endpoint == "static": continue
        methods = sorted(m for m in r.methods if m in ("GET", "POST", "PUT", "DELETE"))
        grp = "页面/静态"
        if r.rule.startswith("/api/ont"): grp = "本体治理"
        elif r.rule.startswith("/api/chat") or r.rule.startswith("/api/metric"): grp = "智能问数/指标"
        elif r.rule.startswith("/api/graph") or r.rule.startswith("/api/build"): grp = "建模/图谱"
        elif r.rule.startswith("/api/table") or r.rule.startswith("/api/overview") or r.rule.startswith("/api/query"): grp = "数据资产"
        elif r.rule.startswith("/api/skill") or r.rule.startswith("/api/tool") or r.rule.startswith("/api/job") or r.rule.startswith("/api/agent"): grp = "技能/作业"
        elif r.rule.startswith("/api/"): grp = "系统/其它"
        routes.append({"path": r.rule, "methods": methods, "group": grp})
    routes.sort(key=lambda x: (x["group"], x["path"]))
    return jsonify({"routes": routes, "count": len(routes)})

@app.get("/api/quality")
def data_quality():
    """数据质量检查(对齐平台『数据治理/告警中心』):空表/行数/关键口径异常,基于真实数据"""
    checks = []; scanned = 0; flagged = set()
    try:
        tl = table_list(); scanned = len(tl)
        for t in tl:
            if t["rows"] == 0:
                checks.append({"target": t["name"], "rule": "空表检测", "level": "warn", "detail": f'{t["name"]}: 0 行(源头无数据)'}); flagged.add(t["name"])
            elif t["cols"] == 0:
                checks.append({"target": t["name"], "rule": "无列检测", "level": "error", "detail": f'{t["name"]}: 无字段'}); flagged.add(t["name"])
    except Exception as e:
        checks.append({"target": "table_list", "rule": "库连通", "level": "error", "detail": str(e)[:80]})
    # 业务口径异常:毛利率偏低(<12%)的月份
    try:
        rows = q("SELECT substr(order_date,1,7) m, round(100.0*sum(gross_profit_actual)/nullif(sum(amount),0),1) mr FROM fact_sales_order GROUP BY 1 HAVING mr < 12 ORDER BY 1")["rows"]
        for r in rows:
            checks.append({"target": "fact_sales_order", "rule": "毛利率预警(<12%)", "level": "warn", "detail": f'{r["m"]} 毛利率 {r["mr"]}%'}); flagged.add("fact_sales_order")
    except Exception: pass
    # 分层对账(DR-022 评测揪出):汇总层与事实层同口径合计差异 >5% 报警——层间不一致会让问数
    # 因选表不同得出不同答案(评测 Q7/Q8 败题即由此)。
    for tag, fact_sql, agg_sql, agg_t in [
        ("产量对账", "SELECT sum(o.quantity) FROM fact_production_output o JOIN fact_production_order po ON o.order_id=po.order_id",
         "SELECT sum(actual_quantity) FROM DWS_PRODUCTION_DAILY", "DWS_PRODUCTION_DAILY"),
        ("销售额对账", "SELECT sum(amount) FROM fact_sales_order WHERE substr(order_date,1,4)='2025'",
         "SELECT sum(sales_amount) FROM agg_metric_monthly WHERE year=2025", "agg_metric_monthly"),
    ]:
        try:
            fv = (q(fact_sql)["rows"][0] or {}).popitem()[1] or 0
            av = (q(agg_sql)["rows"][0] or {}).popitem()[1] or 0
            if fv and av:
                diff = abs(fv - av) * 100.0 / max(abs(fv), 1e-9)
                if diff > 5:
                    checks.append({"target": agg_t, "rule": f"分层对账({tag})", "level": "warn",
                                   "detail": f"汇总层 {round(av):,} vs 事实层 {round(fv):,},差异 {diff:.1f}%(>5%,问数按层选表将得出不同答案)"})
                    flagged.add(agg_t)
        except Exception:
            pass
    lv = {"error": 0, "warn": 0, "ok": 0}
    for c in checks: lv[c["level"]] = lv.get(c["level"], 0) + 1
    # 通过表数 = 已检表数 − 命中异常的表数(给出扫描范围,避免"只见告警不见基数")
    return jsonify({"checks": checks, "count": len(checks), "levels": lv,
                    "scanned": scanned, "passed": max(0, scanned - len(flagged))})

@app.get("/api/sysinfo")
def sysinfo():
    """系统管理(对齐平台『系统管理』):健康/引擎/库/作业概况"""
    import platform as _pf
    # 端口取真实监听值(此前写死 8092,改了 DATAMIND_PORT 后这里会报出错误的端口)。
    # 不回 platform() 全串:操作系统版本/内核版本属于对攻击者有用、对使用者无用的系统信息。
    info = {"health": "ok", "port": LISTEN_PORT, "python": _pf.python_version(),
            "platform": _pf.system()}
    try: info["db_ok"] = os.path.exists(DB)
    except Exception: info["db_ok"] = False
    try:
        from agent_runtime import available
        info["runtimes"] = available()
        info["current_driver"] = os.environ.get("CLAW_DRIVER") or "hermes"
    except Exception: info["runtimes"] = []
    info["jobs"] = len(JOBS)
    info["qa_cache"] = len(_QA_CACHE)
    try: info["tables"] = len(table_list())
    except Exception: info["tables"] = 0
    return jsonify(info)

@app.get("/api/agents")
def agents():
    """智能体列表:深度问数内置体 + 沉淀技能 + skills_seed(对齐平台『智能体列表』)"""
    out = [{"name": "demo-deep-qa", "desc": "基于 示例 本体图谱的深度问数编排智能体", "type": "内置", "author": "系统", "ts": ""}]
    try:
        qs = json.load(open(_QA_SKILLS_F)) if os.path.exists(_QA_SKILLS_F) else []
        for s in qs:
            out.append({"name": (s.get("question") or s.get("id"))[:40], "desc": f'沉淀问数 · {len(s.get("analyses",[]))} 条 SQL',
                        "type": "沉淀", "author": "用户", "ts": s.get("ts", "")})
    except Exception: pass
    try:
        # 与构建页共用注册表，确保本仓技能和可选上游技能的合并、去重与优先级一致。
        for item in skill_registry.discover(_BUILTIN_SKILL_ROOTS):
            out.append({"name": item["name"], "desc": "本体构建技能包", "type": "技能包",
                        "author": "本仓" if item["directory"].startswith(LOCAL_SKILL_ROOT) else "上游",
                        "ts": ""})
    except Exception: pass
    return jsonify({"agents": out, "count": len(out)})

# ── 本体构建(上传多源数据 / 指向数据库 → 调技能)──
@app.post("/api/build/upload")
def build_upload():
    """上传 CSV/TSV → 入 uploads.db 成表(多源里结构化部分;其余文件存档供技能读取)"""
    os.makedirs(os.path.dirname(UPLOAD_DB), exist_ok=True)
    saved, tables = [], []
    import csv as _csv, io
    with _WRITE_LOCK:                                     # 串行化上传写,避免 uploads.db "database is locked"
        con = sqlite3.connect(UPLOAD_DB)                  # 置于 with 内:connect 抛错也由上下文管理器释放锁
        try:
            for f in request.files.getlist("files"):
                fn = _safe_fname(f.filename or "file"); raw = f.read()
                path = _confined(WORK, "uploads_" + fn)   # 落盘前再裁一次:上传文件名永远不可信
                _atomic_bytes(path, raw)
                saved.append(fn)
                if fn.lower().endswith((".csv", ".tsv")):
                    try:
                        txt = raw.decode("utf-8-sig", "replace")
                        rows = list(_csv.reader(io.StringIO(txt), delimiter="\t" if fn.lower().endswith(".tsv") else ","))
                        if len(rows) >= 2:
                            t = re.sub(r"[^A-Za-z0-9_]", "_", fn.rsplit(".", 1)[0])[:40]
                            hdr = [re.sub(r"[^A-Za-z0-9_一-鿿]", "_", h) or f"c{i}" for i, h in enumerate(rows[0])]
                            con.execute(f'DROP TABLE IF EXISTS "{t}"')
                            con.execute(f'CREATE TABLE "{t}" ({", ".join(chr(34)+h+chr(34)+" TEXT" for h in hdr)})')
                            con.executemany(f'INSERT INTO "{t}" VALUES ({",".join("?"*len(hdr))})',
                                            [r[:len(hdr)] + [""] * (len(hdr) - len(r)) for r in rows[1:]])
                            con.commit(); tables.append({"table": t, "rows": len(rows) - 1})
                    except Exception as e:
                        tables.append({"table": fn, "error": str(e)[:80]})
        finally:
            con.close()
    return jsonify({"saved": saved, "tables": tables})

@app.post("/api/build/asset/delete")
def build_asset_delete():
    """删除已上传的辅助资料。

    上传是双落点的：文件存 WORK/uploads_<名>，CSV/TSV 另按同名规则物化为
    uploads.db 里的表。删除必须两处同步，否则「资料已删、表还在」会让
    构建继续引用一份界面上已不存在的证据。文件名沿用上传时的裁剪规则
    （_safe_fname + _confined），穿越形态在此路径上同样不可达。"""
    body = request.json or {}
    fn = _safe_fname(str(body.get("name") or "").strip())
    if not fn:
        return jsonify({"error": "缺少文件名"}), 400
    path = _confined(WORK, "uploads_" + fn)
    if not os.path.exists(path):
        return jsonify({"error": "文件不存在或已删除"}), 404
    dropped = ""
    with _WRITE_LOCK:
        os.remove(path)
        if fn.lower().endswith((".csv", ".tsv")) and os.path.exists(UPLOAD_DB):
            t = re.sub(r"[^A-Za-z0-9_]", "_", fn.rsplit(".", 1)[0])[:40]
            try:
                con = sqlite3.connect(UPLOAD_DB)
                try:
                    con.execute(f'DROP TABLE IF EXISTS "{t}"')
                    con.commit()
                    dropped = t
                finally:
                    con.close()
            except Exception:
                pass                     # 表清理失败不阻断文件删除，前端以返回值区分
    return jsonify({"ok": True, "removed": fn, "dropped_table": dropped})

@app.post("/api/build/run")
def build_run():
    """构建本体:source=demo(主库)|uploads(上传库);快速数据驱动构建(表→对象,命名启发+FK/重叠),产物注册为新图谱"""
    body = request.json or {}
    src = body.get("source", "uploads")
    # 图谱名会作为 argv 传给子进程,先裁成安全 argv(同 /api/build/inquire 的处置)
    name = _safe_argv(body.get("name") or f"构建图谱{time.strftime('%m%d%H%M')}", cap=60, default="构建图谱")
    db = DB if src == "demo" else UPLOAD_DB
    if not os.path.exists(db): return jsonify({"error": "数据库不存在,请先上传"}), 400
    key = "built_" + uuid.uuid4().hex[:6]
    jid = run_job([sys.executable, _confined(HERE, "quick_build.py"), db,
                   _confined(WORK, key + ".json"), name, ACTION_TYPES_F],
                  cwd=HERE, tag=f"build:{name}")
    return jsonify({"job": jid, "graph_key": key})

# ── 本体构建·问询台:多源数据源 + 多库连接 + 技能编排 + Hermes agentic 构建 ──
_BUILD_CONN_F = os.path.join(WORK, "build_connections.json")
_BUILD_SKILL_D = os.path.join(WORK, "custom_skills")
_MOD_MAP = {"csv": "表格", "tsv": "表格", "xlsx": "表格", "xls": "表格", "json": "结构", "xml": "结构",
            "pdf": "文档", "docx": "文档", "doc": "文档", "txt": "文本", "md": "文本",
            "png": "图像", "jpg": "图像", "jpeg": "图像", "gif": "图像", "svg": "图像",
            "sql": "代码", "py": "代码", "ddl": "代码", "wav": "音频", "mp3": "音频"}
_BUILD_SKILL_DESC = {
    "ontology-semi-auto": "机器提议→语义复核→数据验证→确定性验收检查→人工复核，关系证据可追溯",
    "ontology-build": "上游兼容技能：从数据库、代码和可解析文档提出本体候选；新任务建议使用 ontology-semi-auto",
    "gov-app-ontology-build": "上游兼容技能：提出对象、动作和事件并绑定治理资产；结论仍须按当前验收规则复核",
    "ontology-forge": "上游兼容技能：生成 RDF/OWL 与 SHACL 候选产物；不自动证明符合行业标准",
    "ontology-agentic": "上游兼容技能：按范围、来源、提议、数据验证、复核和修订分步执行",
    "auto-ontology": "上游兼容技能：查询和补充既有本体；新增语义保持候选状态直至人工确认"}

def _load_conns():
    v = _load_json(_BUILD_CONN_F)
    return v if isinstance(v, list) else []

_DSN_USERINFO = re.compile(r"(?<=//)[^/@]*@")

def _split_dsn_creds(dsn):
    """DSN → (去凭据的 DSN, user, password)。

    连接串常写成 mysql://user:pass@host/db。原样存进 build_connections.json 意味着
    密码落在一个 0644 的文件里,并且 /api/build/sources 会把它整条回给前端 ——
    「凭据只写不回显」的承诺在这条路径上是不成立的。故登记时就把 userinfo 摘出来,
    密码走 0600 的加密凭据库,DSN 只留 scheme://host:port/db。
    """
    s = (dsn or "").strip()
    if not s: return "", "", ""
    u = _parse_dsn(s)
    return _DSN_USERINFO.sub("", s), u.get("user") or "", u.get("password") or ""

def _mask_conn(c):
    """对外回显用:抹掉 DSN 里可能残留的凭据(旧版本存下的记录仍带 userinfo)。"""
    if not isinstance(c, dict) or not c.get("dsn"): return c
    return {**c, "dsn": _DSN_USERINFO.sub("***@", c["dsn"])}

def _sqlite_tables(path):
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        n = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        c.close(); return n
    except Exception:
        return []

@app.get("/api/build/sources")
def build_sources():
    """构建数据源清单:内置库 + 已连接库 + 已上传多源资产"""
    srcs = [{"id": "demo", "name": "示例主库", "kind": "sqlite", "builtin": True,
             "tables": len(_sqlite_tables(DB)), "desc": "108 表合成制造数据", "ready": os.path.exists(DB)}]
    utabs = _sqlite_tables(UPLOAD_DB) if os.path.exists(UPLOAD_DB) else []
    srcs.append({"id": "uploads", "name": "上传数据库", "kind": "sqlite", "builtin": True,
                 "tables": len(utabs), "desc": "CSV/TSV 上传自动建表", "ready": bool(utabs), "table_names": utabs[:50]})
    for c in _load_conns():
        ready, tabs = False, 0
        if c.get("kind") == "sqlite" and c.get("path") and os.path.exists(c["path"]):
            t = _sqlite_tables(c["path"]); ready, tabs = bool(t), len(t)
        elif c.get("kind") == "api":             # API 源:已物化(up.api_*)即就绪
            ready = _api_conn_table(c) in utabs; tabs = 1 if ready else 0
        srcs.append({**_mask_conn(c), "tables": tabs, "ready": ready})   # DSN 里的残留凭据不回显
    assets = []
    for p in sorted(glob.glob(os.path.join(WORK, "uploads_*"))):
        fn = os.path.basename(p)[8:]
        ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
        # usable 与 _gather_evidence 的读取范围保持同一判据：
        # 可文本化(_TEXT_EXT)与 Excel 会作为证据文本注入构建，其余仅登记来源
        assets.append({"name": fn, "modality": _MOD_MAP.get(ext, "其它"),
                       "kb": round(os.path.getsize(p) / 1024, 1),
                       "usable": ext in _TEXT_EXT or ext in ("xlsx", "xls")})
    return jsonify({"sources": srcs, "assets": assets})

@app.post("/api/build/connect")
def build_connect():
    """登记数据源连接:sqlite(校验文件可读)或外部库(mysql/doris/hive,登记连接串)"""
    body = request.json or {}
    kind = body.get("kind", "sqlite"); name = (body.get("name") or "").strip()
    if not name: return jsonify({"error": "需要连接名"}), 400
    conn = {"id": "conn_" + uuid.uuid4().hex[:6], "name": name, "kind": kind, "builtin": False}
    if kind == "sqlite":
        path = (body.get("path") or "").strip()
        if not path or not os.path.exists(path): return jsonify({"error": "SQLite 文件不存在"}), 400
        if not _sqlite_tables(path): return jsonify({"error": "该文件无可读表(非 SQLite?)"}), 400
        conn["path"] = path
    elif kind == "api":                          # C8 API 型数据源:REST 端点 → 取数物化进上传库(up.api_*)
        url = (body.get("url") or "").strip()
        if not re.match(r"^https?://", url): return jsonify({"error": "API 地址须以 http(s):// 开头"}), 400
        bad = _check_fetch_url(url)                   # SSRF:登记时就挡,别等取数才发现
        if bad: return jsonify({"error": bad}), 400
        conn["url"] = url[:500]
        conn["json_path"] = (body.get("json_path") or "").strip()[:120]
        conn["note"] = "API 源已登记;「取数」将结果物化为 up.api_* 表,问数/SQL 即可用"
    else:
        # DSN 里内嵌的 user:pass 一并摘出:连接清单会把 dsn 回给前端,凭据留在里面
        # 就等于回显了密码(与"只写不回显"自相矛盾)。显式传入的 user/password 优先。
        clean_dsn, dsn_user, dsn_pwd = _split_dsn_creds(body.get("dsn"))
        conn["dsn"] = clean_dsn
        conn["note"] = "外部库已登记(取数需网络连通与驱动;凭据只写不回显)"
        if not conn["dsn"]: return jsonify({"error": "需要连接串 DSN"}), 400
        user = body.get("user") or dsn_user
        password = body.get("password") or dsn_pwd
        if user or password:                             # C7 凭据:0600 加密独立文件,永不回显/入列表
            _save_conn_secret(conn["id"], user, password)
    # 幂等:同一物理源(sqlite 按 path、api 按 url、外部库按 dsn)已登记则复用,避免重复连接堆叠成脏列表
    def _same_source(c):
        if c.get("kind") != kind: return False
        if kind == "sqlite": return c.get("path") == conn.get("path")
        if kind == "api": return c.get("url") == conn.get("url")
        return c.get("dsn") == conn.get("dsn")
    with _WRITE_LOCK:
        conns = _load_conns()
        dup = next((c for c in conns if _same_source(c)), None)
        if dup:
            return jsonify({"ok": True, "conn": _mask_conn(dup), "deduped": True})
        conns.insert(0, conn); _atomic_json(_BUILD_CONN_F, conns[:30])
    return jsonify({"ok": True, "conn": _mask_conn(conn)})

@app.post("/api/build/connect/delete")
def build_connect_del():
    cid = (request.json or {}).get("id")
    with _WRITE_LOCK:
        _atomic_json(_BUILD_CONN_F, [c for c in _load_conns() if c.get("id") != cid])
    _save_conn_secret(cid, "", "")               # 连带清除凭据
    return jsonify({"ok": True})

def _api_conn_table(conn):
    """API 源物化目标表名:api_<连接名 slug>"""
    slug = re.sub(r"[^a-z0-9_]+", "_", (conn.get("name") or "src").lower()).strip("_")[:24] or "src"
    return "api_" + slug

@app.post("/api/conn/api_fetch")
def conn_api_fetch():
    """C8 API 源取数:拉取 JSON → json_path 下钻 → 对象数组物化为 uploads.db 表(up.api_*)。
    上限 2MB / 5000 行;仅 http(s);物化后 深度问数/SQL 工作台/可视化 直接可用。"""
    cid = (request.json or {}).get("id")
    conn = _find_conn(cid)
    if not conn or conn.get("kind") != "api": return jsonify({"error": "API 连接不存在"}), 404
    url = conn.get("url") or ""
    if not re.match(r"^https?://", url): return jsonify({"error": "非法 API 地址"}), 400
    # 取数时重判一次:登记后 DNS 可能改指内网(DNS rebinding),旧记录也可能是加固前存下的
    bad = _check_fetch_url(url)
    if bad: return jsonify({"error": bad}), 400
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "DataMind/1.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read(2_000_000)
        data = json.loads(raw)
    except Exception as e:
        return jsonify({"error": f"API 拉取失败:{str(e)[:140]}"}), 502
    try:
        for part in (conn.get("json_path") or "").split("."):
            if not part: continue
            data = data[int(part)] if re.match(r"^\d+$", part) and isinstance(data, list) else data[part]
    except Exception:
        return jsonify({"error": f"json_path「{conn.get('json_path')}」在返回结构中不存在"}), 400
    if isinstance(data, dict):                   # 单对象 → 单行
        data = [data]
    if not (isinstance(data, list) and data and all(isinstance(x, dict) for x in data[:20])):
        return jsonify({"error": "json_path 需指向对象数组(list of objects)"}), 400
    data = data[:5000]
    cols, seen = [], set()
    for row in data[:50]:
        for k in row.keys():
            if k not in seen and len(cols) < 40:
                # 列名消毒后才可进 DDL:去除引号等可越出标识符边界的字符(与 CSV 上传同规则)
                safe = re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff]", "_", str(k))[:64] or f"c{len(cols)}"
                seen.add(k); cols.append(safe)
    table = _api_conn_table(conn)
    with _WRITE_LOCK:
        con = sqlite3.connect(UPLOAD_DB)
        try:
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
            con.execute(f'CREATE TABLE "{table}" ({", ".join(chr(34)+c+chr(34)+" TEXT" for c in cols)})')
            con.executemany(f'INSERT INTO "{table}" VALUES ({", ".join("?" for _ in cols)})',
                            [tuple("" if row.get(c) is None else str(row.get(c)) for c in cols) for row in data])
            con.commit()
        finally:
            con.close()
        conns = _load_conns()
        for c in conns:
            if c.get("id") == cid:
                c["last_fetch"] = time.strftime("%Y-%m-%d %H:%M:%S"); c["last_rows"] = len(data); c["table"] = table
        _atomic_json(_BUILD_CONN_F, conns)
    return jsonify({"ok": True, "table": "up." + table, "rows": len(data), "columns": cols})

@app.get("/api/build/skills")
def build_skills():
    """内置本体构建技能 + 用户上传的自定义技能说明"""
    out, tombs, builtin_names = [], _skill_tombs(), set()
    for item in _builtin_skill_entries():
        n = item["name"]; builtin_names.add(n)
        if n in tombs: continue                       # 用户已删除:列表里不再出现
        ov = _custom_skill_path(n)                    # 被改写则以覆盖件的描述为准
        desc = _BUILD_SKILL_DESC.get(n) or item["description"]
        if ov:
            try: txt = open(ov, encoding="utf-8", errors="replace").read()
            except OSError: txt = ""
            m = re.search(r"description:\s*(.+)", txt)
            if m: desc = m.group(1).strip()[:140]
        out.append({"name": n, "desc": desc, "builtin": True, "runnable": item["runnable"],
                    "editable": True, "overridden": bool(ov)})
    if os.path.isdir(_BUILD_SKILL_D):
        for f in sorted(glob.glob(os.path.join(_BUILD_SKILL_D, "*.md"))):
            nm = os.path.basename(f)[:-3]
            if nm in builtin_names: continue          # 内置的覆盖件已随内置项列出,不重复
            if nm in tombs: continue
            try: txt = open(f, errors="replace").read()
            except Exception: txt = ""
            m = re.search(r"description:\s*(.+)", txt)
            out.append({"name": nm, "desc": (m.group(1) if m else txt[:120]).strip()[:140],
                        "builtin": False, "runnable": False, "editable": True, "overridden": False})
    # 已隐藏的内置技能也回传(带 hidden 标记),否则删完就再也点不到「恢复默认」,
    # 「可恢复」就成了空话。界面把它们收在列表末尾一行,不占正常卡片位。
    for n in sorted(tombs & builtin_names):
        out.append({"name": n, "desc": _BUILD_SKILL_DESC.get(n) or "", "builtin": True,
                    "runnable": False, "editable": True, "overridden": False, "hidden": True})
    return jsonify(out)

# ── 技能管理(DR-021 · DR-051):浏览/新建/编辑/删除;内置可改写与隐藏(覆盖层+墓碑,可恢复默认);
#    技能正文真正注入构建建模规则,改写优先于出厂正文 ──
_SKILL_NAME_RE = re.compile(r"^[\w\-]{1,40}$")           # \w 含中文;禁路径字符

def _builtin_skill_entries():
    return skill_registry.discover(_BUILTIN_SKILL_ROOTS)

def _builtin_skill_names():
    return {item["name"] for item in _builtin_skill_entries()}

def _builtin_skill_dir(name):
    item = skill_registry.find(name, _BUILTIN_SKILL_ROOTS)
    return item["directory"] if item else None

def _custom_skill_path(name):
    name = _safe_fname(name)
    for ext in (".md", ".txt"):
        p = _confined(_BUILD_SKILL_D, name + ext)
        if os.path.exists(p): return p
    return None

_SKILL_TOMB_F = os.path.join(WORK, "skill_deleted.json")

def _skill_tombs():
    """被用户删除的技能名集合。

    内置技能的正文随仓分发(skills_seed/)或来自上游引擎目录,直接删文件有两个问题:
    一是仓库文件被改动,下次 git checkout 又回来,用户以为没删掉;二是上游目录不归
    本服务管辖,根本删不得。故删除记为墓碑——列表里消失、构建不再注入,而出厂正文
    原样保留,随时可「恢复默认」。自定义技能仍是真删文件。
    """
    v = _load_json(_SKILL_TOMB_F)
    return {str(x) for x in v} if isinstance(v, list) else set()

def _skill_tombs_save(names):
    _atomic_json(_SKILL_TOMB_F, sorted(names))

def _skill_overridden(name):
    """内置技能是否已被用户改写(workdir 里存在同名覆盖件)。"""
    return bool(_custom_skill_path(name)) and name in _builtin_skill_names()

def _skill_body(text):
    """去掉 front-matter 的技能正文(供注入构建 prompt)"""
    return skill_registry.skill_body(text)

@app.get("/api/build/skill/<name>")
def build_skill_get(name):
    """浏览技能内容。自定义与内置一律可编辑;内置额外回传目录清单与是否已被改写。"""
    if not _SKILL_NAME_RE.match(name): return jsonify({"error": "非法技能名"}), 400
    p = _custom_skill_path(name)
    if p:
        try: content = open(p, encoding="utf-8", errors="replace").read()
        except Exception as e: return jsonify({"error": str(e)[:100]}), 500
        builtin = name in _builtin_skill_names()
        return jsonify({"name": name, "builtin": builtin, "editable": True, "content": content,
                        "overridden": builtin})          # 内置且存在覆盖件 = 已被改写,可恢复默认
    d = _builtin_skill_dir(name)
    if d:
        sk = os.path.join(d, "SKILL.md")
        content = open(sk, encoding="utf-8", errors="replace").read() if os.path.exists(sk) else "(该内置技能无 SKILL.md 说明)"
        files = sorted(os.path.basename(x) for x in glob.glob(os.path.join(d, "*")))[:20]
        return jsonify({"name": name, "builtin": True, "editable": True, "content": content,
                        "overridden": False, "files": files})
    return jsonify({"error": "技能不存在"}), 404

@app.post("/api/build/skill/save")
def build_skill_save():
    """新建/编辑技能(在线编辑器)。内置技能同样可改:改动写成 workdir 覆盖件,出厂正文保留。"""
    body = request.json or {}
    name = str(body.get("name") or "").strip()
    content = str(body.get("content") or "")
    if not _SKILL_NAME_RE.match(name): return jsonify({"error": "技能名须为 1~40 字中英文/数字/下划线/连字符"}), 400
    if not content.strip(): return jsonify({"error": "技能内容不能为空"}), 400
    if len(content) > 200_000: return jsonify({"error": "技能内容过大(上限 200KB)"}), 400
    # 内置技能同样可编辑:改动写成 workdir 里的覆盖件,出厂正文不动,故随时可恢复默认。
    # 直接改 skills_seed/ 会弄脏仓库且下次 checkout 即失效,上游引擎目录更不归本服务管辖。
    builtin = name in _builtin_skill_names()
    os.makedirs(_BUILD_SKILL_D, exist_ok=True)
    p = _custom_skill_path(name) or _confined(_BUILD_SKILL_D, name + ".md")
    with _WRITE_LOCK:
        _atomic_text(p, content)
        tombs = _skill_tombs()
        if name in tombs:                    # 保存即恢复:被删过的名字重新出现在列表里
            tombs.discard(name); _skill_tombs_save(tombs)
    # 软校验:技能规范要求 YAML front-matter 携带 description(列表与技能摘要都读它)。
    # 缺失不拒绝——正文照常注入构建;但给出提示,否则列表只能截正文前 120 字凑数。
    hint = ""
    if not re.search(r"^---\s*\n.*?^description:\s*\S", content, re.S | re.M):
        hint = "建议在文件头加 YAML front-matter(name/description):技能列表与摘要都读 description"
    return jsonify({"ok": True, "name": name, "builtin": builtin, "overridden": builtin,
                    **({"hint": hint} if hint else {})})

@app.post("/api/build/skill/delete")
def build_skill_delete():
    name = str((request.json or {}).get("name") or "").strip()
    if not _SKILL_NAME_RE.match(name): return jsonify({"error": "非法技能名"}), 400
    builtin = name in _builtin_skill_names()
    p = _custom_skill_path(name)
    tombs = _skill_tombs()
    if not builtin and not p:
        return jsonify({"error": "技能不存在"}), 404
    if builtin and name in tombs:
        return jsonify({"error": "技能不存在"}), 404
    with _WRITE_LOCK:
        if p:
            os.remove(p)                     # 覆盖件是本服务写的,真删
        if builtin:
            # 出厂正文随仓分发或来自上游目录,不动文件,记墓碑即可:列表消失、构建不注入,
            # 但「恢复默认」还能把它找回来。真删文件会弄脏仓库,且对上游目录无权限。
            tombs.add(name); _skill_tombs_save(tombs)
    return jsonify({"ok": True, "restorable": builtin})

@app.post("/api/build/skill/restore")
def build_skill_restore():
    """恢复内置技能的出厂正文:撤销改写与删除。自定义技能无出厂版本,不适用。"""
    name = str((request.json or {}).get("name") or "").strip()
    if not _SKILL_NAME_RE.match(name): return jsonify({"error": "非法技能名"}), 400
    if name not in _builtin_skill_names():
        return jsonify({"error": "该技能没有出厂版本可恢复(自定义技能删除后不可恢复)"}), 400
    with _WRITE_LOCK:
        p = _custom_skill_path(name)
        if p: os.remove(p)
        tombs = _skill_tombs()
        if name in tombs:
            tombs.discard(name); _skill_tombs_save(tombs)
    return jsonify({"ok": True, "name": name})

@app.post("/api/build/skill/from_graph")
def build_skill_from_graph():
    """#3 构建产物→技能沉淀:从图谱提取 动词表/类型分布/定义风格 → 生成自定义技能(确定性,不经 LLM)。"""
    from collections import Counter
    body = request.json or {}
    gk = body.get("graph") or ""
    if _bad_gkey(gk): return jsonify({"error": "非法图谱键"}), 400
    ir = load_ir_edited(gk)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    rels = _rels(ir)[0]
    verbs = Counter((r.get("verb") or "关联") for r in rels if r.get("status") in ("verified", "asserted"))
    kinds = Counter((o.get("kind") or "object") for o in ir.get("objects", []))
    defs = [(o.get("cn") or o.get("name") or "", (o.get("definition") or "").strip())
            for o in ir.get("objects", []) if (o.get("definition") or "").strip()][:2]
    _sc = ir.get("scenario")
    gname = (_sc.get("name") if isinstance(_sc, dict) else _sc) or gk
    gname = str(gname).strip()[:30]
    name = str(body.get("name") or "").strip() or ("from-" + re.sub(r"[^\w\-]+", "-", gk)[:24])
    lines = [f"---\ndescription: 从构建产物「{gname}」沉淀的建模规范(动词表/类型分布/定义风格)\n---\n",
             f"## 来源\n构建产物 `{gk}`(对象 {len(ir.get('objects', []))} · 已验证/断言关系 {sum(verbs.values())}),沉淀于 {time.strftime('%Y-%m-%d')}。\n",
             "## 关系动词表(建模时优先沿用)"]
    lines += [f"- {v}({n} 次)" for v, n in verbs.most_common(8)] or ["-(该图谱暂无已验证关系)"]
    lines.append("\n## 对象类型分布(kind 判定参照)")
    lines += [f"- {k}:{n} 个" for k, n in kinds.most_common()]
    if defs:
        lines.append("\n## 定义风格样例(属加种差,非循环)")
        lines += [f"- 「{cn}」:{d[:120]}" for cn, d in defs]
    lines.append("\n## 规范\n1. 关系动词优先复用上表,不新造同义动词;\n2. 单据/台账/目录类信息记录判 kind=ice,勿与物理实体混淆;\n3. 定义用「属加种差」句式,定义体不得复用被定义术语本身。")
    content = "\n".join(lines)
    if not _SKILL_NAME_RE.match(name): return jsonify({"error": "技能名非法"}), 400
    if name in _builtin_skill_names(): return jsonify({"error": "技能名与内置冲突,请换名"}), 400
    os.makedirs(_BUILD_SKILL_D, exist_ok=True)
    with _WRITE_LOCK:
        _atomic_text(_confined(_BUILD_SKILL_D, name + ".md"), content)
    return jsonify({"ok": True, "name": name, "verbs": dict(verbs.most_common(8)), "chars": len(content)})

# ── #1 技能对比实验(DR-022):同一构建目标 × 两组技能,各跑一次真实构建,对比产物 ──
_SKILL_CMP_F = os.path.join(WORK, "skill_compare.json")
SKILL_CMP_JOB = {"running": False, "progress": "", "started": ""}

def _build_stats(ir):
    from collections import Counter
    objs = ir.get("objects", [])
    rels = _rels(ir)[0]
    ok_rels = [r for r in rels if r.get("status") in ("verified", "asserted")]
    return {"objects": len(objs), "relations": len(rels),
            "verified": sum(1 for r in rels if r.get("status") == "verified"),
            "verbs": dict(Counter((r.get("verb") or "关联") for r in ok_rels).most_common(6)),
            "kinds": dict(Counter((o.get("kind") or "object") for o in objs).most_common()),
            "def_coverage": round(sum(1 for o in objs if (o.get("definition") or "").strip()) * 100.0 / max(1, len(objs)), 1)}

def _run_skill_compare(query, arms):
    """两组顺序真实构建(LLM 抽取+关系数据验证,不走兜底编造);每组落一个 built_* 产物。"""
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "query": query, "arms": []}
    for i, skills in enumerate(arms):
        label = chr(65 + i)
        SKILL_CMP_JOB["progress"] = f"{label} 组构建中(技能:{'、'.join(skills) or '无'})"
        ev = _gather_evidence(DB)
        # 默认参数绑定当轮的 ev/skills:_bounded 超时后守护线程仍在跑,若下一轮重新赋值,
        # 闭包按引用取值会读到下一轮的证据。绑定后每轮各用各的,与循环推进解耦。
        extracted = _bounded(lambda ev=ev, skills=skills: _llm_extract_ontology(query, ev, skills), 640)
        arm = {"label": label, "skills": skills, "ok": False}
        if extracted and extracted.get("objects"):
            key = "built_" + uuid.uuid4().hex[:6]
            ir = _adjudicate_ir(DB, f"技能对比-{label}组", extracted, ev)
            _atomic_json(os.path.join(WORK, key + ".json"), ir)
            arm.update(ok=True, key=key, stats=_build_stats(ir))
        else:
            arm["error"] = "LLM 抽取失败/超时(该组如实记为失败,不用兜底数据冒充)"
        out["arms"].append(arm)
        _atomic_json(_SKILL_CMP_F, out)               # 每组落盘
    SKILL_CMP_JOB.update(running=False, progress="完成")

@app.post("/api/build/skill_compare")
def skill_compare_run():
    """启动技能对比:{query?, skills_a:[], skills_b:[]};后台跑两次真实构建(约 4-10 分钟)。"""
    if SKILL_CMP_JOB["running"]: return jsonify({"error": "对比实验进行中", "job": SKILL_CMP_JOB}), 409
    body = request.json or {}
    query = (body.get("query") or "").strip() or "从 示例主库自动识别核心对象、事件与关系,构建一张可审计的制造企业本体"
    a = [s for s in (body.get("skills_a") or []) if isinstance(s, str)][:5]
    b = [s for s in (body.get("skills_b") or []) if isinstance(s, str)][:5]
    known = _builtin_skill_names() | {os.path.basename(p).rsplit(".", 1)[0] for p in glob.glob(os.path.join(_BUILD_SKILL_D, "*"))}
    bad = [s for s in a + b if s not in known]
    if bad: return jsonify({"error": f"技能不存在: {', '.join(bad)}"}), 400
    SKILL_CMP_JOB.update(running=True, progress="启动", started=time.strftime("%Y-%m-%d %H:%M:%S"))
    threading.Thread(target=_run_skill_compare, args=(query, [a, b]), daemon=True).start()
    return jsonify({"ok": True}), 202

@app.get("/api/build/skill_compare/status")
def skill_compare_status(): return jsonify(SKILL_CMP_JOB)

@app.get("/api/build/skill_compare/results")
def skill_compare_results():
    try: return jsonify(json.load(open(_SKILL_CMP_F)))
    except Exception: return jsonify({"empty": True})

@app.post("/api/build/skill/upload")
def build_skill_upload():
    """上传自定义技能说明(仅收 .md/.txt 的 SKILL 说明,不收可执行文件,防任意代码)"""
    os.makedirs(_BUILD_SKILL_D, exist_ok=True); saved = []
    for f in request.files.getlist("files"):
        # 与另外两个上传入口(build_upload / 聊天附件)统一走 _safe_fname + _confined。
        # 这里原有的 basename + 白名单正则 + 强制后缀本身已挡住穿越,但各写各的意味着
        # 一旦有人放宽那条正则,防线就随之失守;裁决收在一处才不会各自漂移。
        fn = _safe_fname(f.filename or "skill.md")
        if not re.match(r"^[\w\-. ]+$", fn): continue
        if not fn.lower().endswith((".md", ".txt")): fn = re.sub(r"\.\w+$", "", fn) + ".md"
        try:
            _atomic_bytes(_confined(_BUILD_SKILL_D, fn), f.read()); saved.append(fn)
        except (OSError, ValueError):        # ValueError = _confined 判定越界
            pass
    return jsonify({"saved": saved})

@app.get("/api/build/defaults")
def build_defaults():
    """本体构建页各表单的可用默认参数(前端预填,可改):均指向本机真实可用资源"""
    return jsonify({
        "sqlite_path": os.path.abspath(DB),                       # 真实 示例 库绝对路径(填入即可校验通过)
        "conn_name": "示例制造数据库",
        "build_name": "示例企业本体",
        "build_query": "从 示例主库自动识别核心对象、事件与关系,构建一张可审计的制造企业本体(对象/事件/关系,每条关系带数据取证)",
        "default_skill": "ontology-semi-auto",                      # 本仓自带,无上游引擎也真实可用
        "ext": {"kind": "hive", "host": "<hive-host>", "port": "10000", "db": "default"},
    })

@app.get("/api/build/references")
def build_reference_catalog():
    """半自动构建可选的行业参照与本体标准目录。"""
    response = jsonify(build_references.catalog())
    response.headers["Cache-Control"] = "no-store"
    return response

@app.get("/api/build/built")
def build_built():
    """已构建本体清单(built_*.json),供本体构建页展示历史产物"""
    out = []
    for p in sorted(glob.glob(os.path.join(WORK, "built_*.json")), key=os.path.getmtime, reverse=True):
        k = os.path.basename(p)[:-5]; ir = _load_json(p)
        if not isinstance(ir, dict) or not ir.get("objects"): continue
        g = ir_to_graph(k, ir)
        objs = ir.get("objects", []); rels = ir.get("relations", []) or ir.get("links", [])
        ev = sum(1 for o in objs if o.get("kind") == "event")
        ver = sum(1 for r in rels if r.get("status") == "verified")
        sc = ir.get("scenario") or {}
        result = ((ir.get("build_quality") or {}).get("result") or
                  (ir.get("build_quality") or {}).get("gate") or "legacy")
        hist = ir.get("build_history") if isinstance(ir.get("build_history"), list) else []
        out.append({"key": k, "name": sc.get("name") or k, "style": sc.get("style", ""),
                    "objects": len(g["nodes"]), "events": ev, "links": len(g["edges"]), "verified": ver,
                    "quality_result": result, "quality_gate": result,
                    "rounds": len(hist),          # 构建轮次:让「继续构建」前就看得出这张图迭代过几轮
                    "ts": time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
    return jsonify(out)

@app.get("/api/build/history/<key>")
def build_history(key):
    """一张本体的构建历程:每轮的诉求、数据源、技能与并入结果。

    继续构建前先看得见「这张图是怎么建起来的」,才谈得上接着建;
    历史存在产物里而非浏览器,换设备、换浏览器都还在。
    """
    k = re.sub(r"[^A-Za-z0-9_]", "", str(key or ""))[:40]
    if not k.startswith("built_"):
        return jsonify({"error": "仅支持自建本体(built_*)"}), 400
    ir = _load_json(_confined(WORK, k + ".json"))
    if not isinstance(ir, dict) or not ir.get("objects"):
        return jsonify({"error": "本体不存在"}), 404
    sc = ir.get("scenario") or {}
    hist = ir.get("build_history") if isinstance(ir.get("build_history"), list) else []
    rounds = []
    for h in hist:
        if not isinstance(h, dict): continue
        mg = h.get("merge") or {}
        rounds.append({"round": h.get("round"), "at": h.get("at"),
                       "request": str(h.get("request") or "")[:2000],
                       "source": (h.get("source") or {}).get("name") or "",
                       "skills": h.get("skills") or [], "method": h.get("method") or "",
                       "references": h.get("references"),
                       "cq_count": len(h.get("cqs") or []),
                       "tables": (h.get("evidence") or {}).get("tables"),
                       "documents": (h.get("evidence") or {}).get("documents"),
                       "merged": {kk: mg.get(kk) for kk in
                                  ("objects_added", "relations_added", "relations_upgraded")} if mg else None})
    return jsonify({"key": k, "name": sc.get("name") or k,
                    "iterations": sc.get("iterations") or (len(rounds) or 1),
                    "objects": len(ir.get("objects") or []),
                    "relations": len(ir.get("relations") or []),
                    "references": (ir.get("build_manifest") or {}).get("references"),
                    "rounds": rounds})

@app.post("/api/build/delete")
def build_delete():
    """删除一个已构建本体产物"""
    k = (request.json or {}).get("key", "")
    if not re.match(r"^built_[\w]+$", k): return jsonify({"error": "非法 key"}), 400
    p = _confined(WORK, k + ".json")
    try:
        if os.path.exists(p): os.remove(p)
    except Exception as e: return jsonify({"error": str(e)[:80]}), 500
    return jsonify({"ok": True})

def _build_intent(q, sname, skills):
    """Hermes 解析建模意图 → {name, strategy, note}"""
    from agent_runtime import get_runtime, available
    sk = ("；可用技能:" + "、".join(skills)) if skills else ""
    prompt = f"""你是企业本体建模编排 agent。用户诉求:「{q}」;数据源:{sname}{sk}。
只输出一个 JSON(无其它文字):{{"name":"目标本体简名(≤12字)","strategy":"一句话建模策略","note":"给用户的一句话说明"}}"""
    for drv in _drv_order():
        if drv not in available(): continue
        ok, reply = get_runtime(drv).run_turn(f"bi_{uuid.uuid4().hex[:6]}", prompt, timeout=28)
        if ok and reply and not _looks_like_error(reply):
            m = re.search(r"\{[\s\S]*\}", reply)
            if m:
                try: return json.loads(m.group(0))
                except Exception: pass
    return None

def _build_summary(q, name, ir):
    """Hermes 生成本体说明(基于已构建 IR,不编造)"""
    from agent_runtime import get_runtime, available
    objs = ir.get("objects", []); rels = ir.get("relations", [])
    brief = json.dumps({"name": name,
                        "objects": [o.get("name") for o in objs[:30]],
                        "events": [o.get("name") for o in objs if o.get("kind") == "event"][:15],
                        "sample_links": [{"s": l.get("source_concept"), "v": l.get("verb"),
                                          "t": l.get("target_concept"), "status": l.get("status")} for l in rels[:15]]},
                       ensure_ascii=False)[:2500]
    prompt = f"用户想建的本体:「{q}」。已数据驱动构建出:{brief}。用中文写 3-5 句本体说明:覆盖哪些核心对象/事件、关系可信度如何、可支撑什么分析。仅基于给定内容,不要编造。直接输出文本。"
    for drv in _drv_order():
        if drv not in available(): continue
        ok, reply = get_runtime(drv).run_turn(f"bs_{uuid.uuid4().hex[:6]}", prompt, timeout=33)
        if ok and reply and not _looks_like_error(reply):
            return reply.strip()[:1400]
    return None

def _build_rule_summary(name, ir):
    objs = ir.get("objects", []); rels = ir.get("relations", [])
    ev = [o.get("name") for o in objs if o.get("kind") == "event"]
    ver = sum(1 for l in rels if l.get("status") == "verified")
    return (f"本体「{name}」共构建 {len(objs)} 个对象(含 {len(ev)} 个事件对象)、{len(rels)} 条关系"
            f"(其中 {ver} 条经取值重叠/父键唯一验证)。核心对象:{('、'.join(o.get('name') for o in objs[:8])) or '—'}。"
            f"可支撑对象画像、关系溯源与跨表指标分析。(引擎离线,此为规则化摘要)")

# ── LLM 辅助本体构建：来源整理 → 候选提议 → 数据验证 ──
_SKILL_METHOD = {
    "ontology-semi-auto": "机器提议→语义复核→数据验证→确定性验收检查→人工复核；verified 必须有可复核证据。",
    "ontology-build": "从数据库+建表代码+业务代码(视图/ETL)+行业知识构建可审计企业本体,对象/事件/关系/指标齐备。",
    "gov-app-ontology-build": "构建应用本体:显式区分对象(object)、动作(action)、事件(event),绑定治理资产。",
    "ontology-forge": "遵循 OWL2 本体公理、Palantir 操作型本体四层(对象/属性/链接/动作)、SHACL 约束与斯坦福七步法。",
    "ontology-agentic": "按范围界定、取证、提议、数据验证、复核和修订执行，只保留有数据或文档依据的结论。",
    "auto-ontology": "以对象为中心补全属性、指标与关系,标注候选语义待确认。"}
_TEXT_EXT = ("sql", "ddl", "py", "md", "txt", "json", "xml", "yaml", "yml", "csv", "tsv", "js", "java", "sh")

def _read_asset_text(fname, cap=3500):
    """读取上传的可文本化多源资产(建表代码/业务文档/知识片段/Excel 知识包);二进制/图像返回空(标注为引用证据)"""
    # fname 沿证据链一路传下来(上传文件名 → IR → 此处),不能假定它仍是单一文件名:
    # 先裁成安全组件再 _confined,读取范围锁死在 WORK 内。
    p = _confined(WORK, "uploads_" + _safe_fname(fname))
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if not os.path.exists(p): return ""
    if ext in ("xlsx", "xls"):
        # Excel 知识包(看板/指标口径等):抽工作表名 + 前若干行,喂 LLM 以获得有业务意义的中文命名
        try:
            import openpyxl
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            out = []                                     # 取样即可(供 LLM 感知术语口径),不喂全表,避免撑大 prompt 拖慢/超时
            for sn in wb.sheetnames[:5]:
                rows = []
                for i, r in enumerate(wb[sn].iter_rows(values_only=True)):
                    if i >= 12: break
                    cells = [str(c) for c in r if c not in (None, "")]
                    if cells: rows.append(" | ".join(cells)[:180])
                if rows: out.append(f"[工作表「{sn}」]\n" + "\n".join(rows))
            wb.close()
            return ("\n".join(out))[:cap]
        except Exception:
            return ""
    if ext in _TEXT_EXT:
        try: return open(p, encoding="utf-8", errors="replace").read()[:cap]
        except Exception: return ""
    return ""

def _gather_evidence(db, cap_tabs=400, cap_docs=6):
    """聚合多源证据:① 库表结构(表→列)② 上传的文档/代码文本 ③ 图像/二进制的引用清单"""
    schema, tab_cols = [], {}
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        tabs = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )][:cap_tabs]
        for t in tabs:
            try:
                cols = [(r[1], r[2] or "TEXT") for r in c.execute(f'PRAGMA table_info("{t}")')]
            except Exception:
                cols = []
            tab_cols[t] = cols                       # 保留全部表(供关系取证的表名映射)
        c.close()
    except Exception:
        pass
    def _pri(t):                                     # schema 文本按语义重要度排序,确保 fact_/dws_ 不被截断丢弃
        tl = t.lower()
        for i, p in enumerate(("fact_", "dws_", "dwd_", "agg_", "dim_", "ods_", "stg_")):
            if tl.startswith(p): return i
        return 9
    for t in sorted(tab_cols, key=lambda x: (_pri(x), x)):
        schema.append(f"{t}({', '.join(cn for cn, _ in tab_cols[t][:22])})")
    docs, refs, files, used = [], [], [], 0
    for p in sorted(glob.glob(os.path.join(WORK, "uploads_*"))):
        fn = os.path.basename(p)[8:]
        ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
        txt = _read_asset_text(fn)
        consumed = bool(txt.strip() and len(docs) < cap_docs and used < 16000)
        files.append({"name": fn[:180], "type": _MOD_MAP.get(ext, "其它"),
                      "consumed_as_text": consumed})
        if consumed:
            docs.append(f"### 证据文件「{fn}」({_MOD_MAP.get(ext,'其它')})\n{txt}")
            used += len(txt)
        elif ext in ("png", "jpg", "jpeg", "gif", "svg", "wav", "mp3"):
            refs.append(f"{fn}({_MOD_MAP.get(ext,'其它')})")
    return {"schema": "\n".join(schema), "tab_cols": tab_cols,
            "docs": "\n\n".join(docs), "n_docs": len(docs), "refs": refs, "files": files}

def _skill_method_text(skills):
    """选中技能 → 真实 SKILL.md / 自定义正文,有界注入构建 prompt。

    旧实现对内置技能只注入一行硬编码摘要,技能正文即使更新也不影响构建。现在列表、查看和
    prompt 消费共用 skill_registry;_SKILL_METHOD 仅保留给历史名称的兼容兜底。"""
    def custom(name):
        """只回传用户的覆盖件。

        不得在这里回退到 _SKILL_METHOD 的一行摘要:DR-051 把解析顺序改成「覆盖优先」后,
        非空的摘要会顶掉真正的 SKILL.md —— 4552 字的技能正文被压缩成 70 字进提示词,
        技能等于没生效。兼容兜底改由 fallback_loader 在注册表也找不到时才用。"""
        p = _custom_skill_path(name)
        if p:
            try: return open(p, encoding="utf-8", errors="replace").read()
            except OSError: return ""
        return ""
    # 已删除的技能不再注入:界面上看不见却还在影响构建,是最难查的一类不一致。
    tombs = _skill_tombs()
    picked = [n for n in (skills or []) if n not in tombs]
    text, _used = skill_registry.method_text(picked, _BUILTIN_SKILL_ROOTS, custom_loader=custom,
                                             fallback_loader=lambda n: _SKILL_METHOD.get(n, ""))
    return text

def _dg_bounds(causes, allow_set):
    """诊断检查 G1(确定性):路径必须只引用白名单对象;越界即降 candidate 并标记。
    从原单次调用的 ④ 边界校验抽出复用,流水线各节点共用同一把尺。"""
    out = []
    for c in (causes or [])[:5]:
        path = str(c.get("path") or "")
        toks = [t for t in re.split(r"[→\->,、\s]+", path) if t]
        inb = all(t in allow_set for t in toks) if toks else False
        conf = c.get("confidence") if c.get("confidence") in ("verified", "candidate") else "candidate"
        if not inb: conf = "candidate"
        out.append({"cause": str(c.get("cause") or "")[:80], "path": path[:120],
                    "evidence": str(c.get("evidence") or "")[:120], "confidence": conf, "in_bounds": inb})
    return out

def _dg_rank(causes):
    """诊断评估节点(确定性打分,不调 LLM)。

    对标 Trane M3 的"评估 Agent",但刻意不用 LLM:排序依据是可枚举的客观特征
    ——是否在本体边界内、置信度、证据是否具体到表/字段。规则打分可复现、可解释、零延迟,
    比让模型自评"严重程度"更可信(模型自评正是该文警告的循环论证)。
    """
    def score(c):
        s = 0
        if c.get("in_bounds"): s += 4                     # 路径落在已验证的本体关系内
        if c.get("confidence") == "verified": s += 3      # 依据已验证关系
        ev = c.get("evidence") or ""
        if re.search(r"[A-Za-z_]{3,}", ev): s += 2        # 证据点到了具体表/字段名
        if len(ev) >= 12: s += 1                          # 证据具体而非空话
        return s
    ranked = sorted(causes or [], key=lambda c: -score(c))
    for i, c in enumerate(ranked, 1):
        c["rank"] = i; c["rank_score"] = score(c)
        c["rank_basis"] = "、".join(filter(None, [
            "路径在本体边界内" if c.get("in_bounds") else "路径越界",
            "依据已验证关系" if c.get("confidence") == "verified" else "依据待验证关系",
            "证据指向具体字段" if re.search(r"[A-Za-z_]{3,}", c.get("evidence") or "") else "证据未指向字段"]))
    return ranked

def _dg_checklist_gate(items, allow_set, tables):
    """诊断检查 G2(确定性):检查清单只能引用白名单对象或真实表名。

    对标该文的"审核 Agent",但用规则而非 LLM——合规性检查本质是集合匹配；模型不能
    同时承担生成与独立核验职责。越界项不静默丢弃，而是显式标记以供审计。
    """
    kept, flagged = [], []
    tl = {t.lower() for t in tables if t}
    for x in (items or [])[:8]:
        s = str(x)[:60]
        refs = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", s)
        bad = [r for r in refs if r.lower() not in tl and r not in allow_set]
        (flagged if bad else kept).append({"item": s, "unknown_refs": bad[:3]} if bad else s)
    return kept, flagged

_ACC_MIN_N = 5          # 样本下限:低于此不报准确率——小样本百分比是噪声,不是证据
_ACC_CACHE = {"ts": 0, "val": None}

def _iter_all_irs():
    """遍历所有可读图谱的 IR(种子 + 构建/锻造产物),用于跨图谱聚合人审历史。"""
    # 必须用 load_ir_edited:人审结论写在编辑栈里,基线 IR 上看不到(实测基线 0 条 / 应用编辑后 7 条)
    for k in list(IR_SOURCES.keys()):
        try:
            ir = load_ir_edited(k)
        except Exception:
            ir = None
        if ir: yield k, ir
    for pth in sorted(glob.glob(os.path.join(WORK, "built_*.json"))):
        k = os.path.basename(pth)[:-5]
        try:
            ir = load_ir_edited(k)
        except Exception:
            ir = _load_json(pth)
        if isinstance(ir, dict): yield k, ir

def _review_history(force=False):
    """⑥ 可解释性第三问「历史上类似情况准确率?」——聚合人审结论。

    口径:只统计带 human_review 的关系,按**系统原判**(review_prior_status)分组算人审同意率。
    这才是工程师真正要问的那一问:"系统说 verified 时,人有多少次认同?"
    另按动词分组,暴露"哪类语义关系机器最容易判错"。

    诚实规范:样本 < _ACC_MIN_N 时返回 insufficient 且**不给百分比**——两三条记录算出的
    百分比是噪声而非证据,展示出来只会误导工程师建立错误的信任。缓存 5 分钟。
    """
    if not force and _ACC_CACHE["val"] is not None and time.time() - _ACC_CACHE["ts"] < 300:
        return _ACC_CACHE["val"]
    by_prior, by_verb, total = {}, {}, {"approved": 0, "rejected": 0}
    for _k, ir in _iter_all_irs():
        for r in (ir.get("relations") or ir.get("links") or []):
            hr = r.get("human_review")
            if hr not in ("approved", "rejected"): continue
            total[hr] += 1
            pri = r.get("review_prior_status") or "unknown"
            by_prior.setdefault(pri, {"approved": 0, "rejected": 0})[hr] += 1
            vb = (r.get("verb") or "未标注").strip()
            by_verb.setdefault(vb, {"approved": 0, "rejected": 0})[hr] += 1
    def rate(d):
        n = d["approved"] + d["rejected"]
        out = {"n": n, "approved": d["approved"], "rejected": d["rejected"]}
        if n >= _ACC_MIN_N: out["agree_rate"] = round(d["approved"] / n * 100, 1)
        else: out["insufficient"] = f"样本 {n} 条(< {_ACC_MIN_N}),不给准确率"
        return out
    n_all = total["approved"] + total["rejected"]
    val = {"min_n": _ACC_MIN_N, "reviewed": n_all,
           "overall": rate(total) if n_all else {"n": 0, "insufficient": "尚无人审记录"},
           "by_prior_status": {k: rate(v) for k, v in sorted(by_prior.items())},
           "by_verb": {k: rate(v) for k, v in sorted(by_verb.items(), key=lambda x: -(x[1]["approved"] + x[1]["rejected"]))[:12]}}
    _ACC_CACHE.update(ts=time.time(), val=val)
    return val

def _rejected_patterns(limit=8):
    """读取人工否决过的关系模式，作为后续构建的已知误判模式。

    对标 Trane M4 增强注册表里的"已知故障模式"——让 AI 看到的不只是当前输入,还有
    这类判断历史上踩过的坑。人审已经把结论写在 IR 上了,此前只是没接回提议环节。
    只回流**否决**(负样本):它是稀缺且高信息量的;通过的关系本就在图谱里当正样本。
    """
    pats, seen = [], set()
    for _k, ir in _iter_all_irs():
        objs = {o.get("id"): o for o in (ir.get("objects") or [])}
        for r in (ir.get("relations") or ir.get("links") or []):
            if r.get("human_review") != "rejected": continue
            so, to = objs.get(r.get("source")), objs.get(r.get("target"))
            s = (so or {}).get("table") or r.get("source") or ""
            t = (to or {}).get("table") or r.get("target") or ""
            key = (s.lower(), t.lower(), (r.get("verb") or "").strip())
            if not (s and t) or key in seen: continue
            seen.add(key)
            why = (r.get("review_reason") or "").strip()
            pats.append(f'{s} →[{r.get("verb") or "?"}]→ {t}' + (f'(人审否决理由:{why[:40]})' if why else "(人审判为语义不成立)"))
            if len(pats) >= limit: return pats
    return pats

def _relset(extracted):
    """关系集合指纹:规范化 (source, target, verb) 三元组,供两次独立生成做集合比较。
    大小写与首尾空白归一,避免同一关系因书写差异被判为不同。"""
    out = set()
    for r in (extracted or {}).get("relations", []) or []:
        s, t = str(r.get("source") or "").strip().lower(), str(r.get("target") or "").strip().lower()
        v = str(r.get("verb") or "").strip()
        if s and t: out.add((s, t, v))
    return out

def _stability_annotate(first, second):
    """M1 迭代相似度收敛:用两次独立生成的 Jaccard 量化提议一致性,并逐条标注。

    对标 Trane M1(arXiv:2603.10047)。与该文的关键差别在于**一致性不作否决权**:
    数据裁决(取值重叠 ∧ 父键唯一)提供可复核的验证结果；若数据验证了某关系，不应仅因
    LLM 本次提议不稳定就降级。因此这里仅执行两项处理:
      ① 给每条关系记 stable=True/False,供人审看到"该关系仅在 1/2 次生成中出现";
      ② 聚合出 jaccard 一致性分,量化引擎方差(补足论文 §8「引擎方差未量化」)。
    返回 (一致性统计, 被两次都提出的关系数)。
    """
    a, b = _relset(first), _relset(second)
    inter, union = a & b, a | b
    jac = round(len(inter) / len(union), 4) if union else 0.0
    for r in (first or {}).get("relations", []) or []:
        s, t = str(r.get("source") or "").strip().lower(), str(r.get("target") or "").strip().lower()
        r["stable"] = (s, t, str(r.get("verb") or "").strip()) in inter
    return {"jaccard": jac, "run1": len(a), "run2": len(b), "both": len(inter), "union": len(union)}, len(inter)

def _base_context_block(base_ir, max_objs=120, max_rels=80):
    """继续构建时给模型的已有本体上下文。

    不给这段,模型每轮都从零提议:已有对象会被重复提出(靠名称去重才没进图),
    已建立的关系会被再提一遍,而用户真正要补的部分反而淹没在重复里。给了之后
    模型知道「这些已经有了」，才能集中处理尚未覆盖的建模要求。

    历次建模诉求一并给出——「补上质量域」这类增量指令,脱离前几轮的语境无法理解。
    体量做上限截断:已有本体可能上百对象,整份塞进提示词会挤掉表结构证据。
    """
    if not isinstance(base_ir, dict): return ""
    objs = [o for o in (base_ir.get("objects") or []) if isinstance(o, dict) and o.get("name")]
    rels = [r for r in (base_ir.get("relations") or []) if isinstance(r, dict)]
    if not objs: return ""
    lines = []
    for o in objs[:max_objs]:
        tb = o.get("table") or ""
        lines.append(f"- {o['name']}({o.get('cn') or ''}){'[表:' + tb + ']' if tb else '[未绑表]'}")
    more_o = f"\n  …另有 {len(objs) - max_objs} 个对象未列出" if len(objs) > max_objs else ""
    rl = []
    for r in rels[:max_rels]:
        rl.append(f"- {r.get('source_concept')} {r.get('verb') or '关联'} {r.get('target_concept')}"
                  f"({r.get('status') or 'candidate'})")
    more_r = f"\n  …另有 {len(rels) - max_rels} 条关系未列出" if len(rels) > max_rels else ""
    hist = [h for h in (base_ir.get("build_history") or []) if isinstance(h, dict)]
    hl = "".join(f"\n  第{h.get('round')}轮:{str(h.get('request') or '')[:120]}" for h in hist[-6:])
    return ("\n\n[本次是在已有本体上继续构建 —— 下列内容已经存在,不要重复提议]"
            + (f"\n已建成对象({len(objs)} 个):\n" + "\n".join(lines) + more_o)
            + (f"\n已建立关系({len(rels)} 条):\n" + "\n".join(rl) + more_r if rl else "")
            + (f"\n历次建模诉求:{hl}" if hl else "")
            + "\n要求:只提议上面**没有**的新对象与新关系,用于覆盖本轮尚未满足的建模要求;"
              "已存在的对象若要被新关系引用,直接沿用其原 name(不要改名、不要另起同义对象)。")


def _llm_extract_ontology(q, ev, skills, cqs=None, log=None, base_ir=None,
                          references=None):
    """综合库结构与已解析文档/代码，提议 objects/relations(JSON)。

    ``cqs`` 为兼容既有调用保留，但验收问题刻意不进入提议提示词：否则模型会按题目
    造出可达路径，导致验收集泄漏。验收问题只在构建完成后由确定性检查消费。
    ``log`` 为可选的过程回报回调（见 _bounded_stream）：证据装配规模、引擎与模型、
    模型原始输出（引擎支持流式时按行回报）、解析结果，逐条回报给前端折叠面板。
    """
    from agent_runtime import get_runtime, available
    _log = log or (lambda *_a, **_k: None)
    method = _skill_method_text(skills)
    docs_block = ("\n[上传的多源证据:建表代码/业务文档/知识片段]\n" + ev["docs"]) if ev["docs"] else ""
    refs_block = ("\n[引用但未解析的资产:" + "、".join(ev["refs"]) + "]") if ev["refs"] else ""
    _rp = _rejected_patterns()                       # 将人工否决模式作为后续提议的负例。
    bad_block = ("\n[已知误判模式(历史上被人审否决,勿再提议同类关系)]\n" + "\n".join("- " + x for x in _rp)) if _rp else ""
    base_block = _base_context_block(base_ir)        # 迭代时把已建成的部分与历次诉求交给模型
    reference_profile = build_references.normalize(references, legacy_default=True)
    reference_block = build_references.prompt_block(reference_profile)
    # BFO/IOF 官方关系名清单从 ontology_grounding 取,不在提示词里另抄一份——
    # 抄一份就会漂移,模型填了表里没有的名字,normalize() 一律判 unmapped,
    # 表现为「接地率恒为 0」而看不出原因。
    _REL_CHOICES = " / ".join(ontology_grounding.RELATION_SPECS)
    _use_bfo = build_references.uses_bfo_iof(reference_profile)
    _standard_field = (f',"founded_relation":"可选:确实对应 BFO/IOF 官方关系才填写,否则留空——{_REL_CHOICES}"'
                       if _use_bfo else "")
    _standard_rule = (
        "⑤′founded_relation 只能取所列官方关系名,且须满足定义域/值域(如 hasInput/hasOutput 的源必须是 event 或 action 类对象;describes 的源必须是 ice);拿不准就留空——留空只是未接地,填错是伪造标准映射;"
        if _use_bfo else "")
    _definition_rule = ("借鉴 IOF 定义规范:" if _use_bfo else "定义质量规则:")
    prompt = f"""你是企业本体自动抽取智能体,综合结构化库表与多源文档证据构建本体。
建模目标:{q}
{('建模规则:' + method) if method else ''}
[数据库表结构（结构依据；对象优先绑定到这些实际存在的表）]
    {ev['schema'][:12000]}{docs_block[:9000]}{refs_block}{bad_block}{base_block}{reference_block}

只输出一个 JSON(无其它文字):
    {{"objects":[{{"name":"英文标识(能对齐表名就用表名)","cn":"有业务意义的中文名","kind":"object|event|action|asset|role|ice(信息记录:目录/单据/地址/台账等,非物理实体)","table":"绑定的真实表名或 null;动作通常为 null","action_id":"仅当证据中明确出现系统已有动作标识时填写,否则 null","evidence":"抽取依据(来自哪张表/哪份文档及段落)","definition":"属加种差定义;给不出严格定义就留空","example":"可选；只能逐字摘录输入证据中的真实实例，没有则留空","example_source":"example 的证据位置；没有 example 则留空","counterExample":"类别层面的易混淆概念及辨析，待专家复核"}}],
  "relations":[{{"source":"对象name","target":"对象name","verb":"具体关系动词(归属/产生/包含/服务/触发…)","rationale":"依据"{_standard_field},"child_key":"可选:源表候选外键(复合键用逗号)","parent_key":"可选:目标表候选键(复合键用逗号)"}}]}}
    要求:①对象尽量绑定真实表;②由文档/流程明确描述的业务事件用 kind=event；明确描述的操作、审批、下发、创建任务等用 kind=action，并用关系连接其作用对象；不得仅凭表名批量编造动作;库存记录/地址/目录/单据等信息性条目用 kind=ice,勿与物理实体混淆;③关系两端必须是上面列出的对象 name;④不虚构库表和文档中都没有的实体、动作或关系;⑤child_key/parent_key 只是待验证提示,只能填写上面 schema 真实存在的列,不得声称 verified;{_standard_rule}⑥**cn 必须是有业务意义的中文名**(如 客户 / 销售订单 / 退货事件 / 生产工单),优先复用表注释、上传文档/知识包里的中文术语,严禁用拼音或直接照搬英文表名/键名做 cn;⑦**{_definition_rule}**definition 用「属加种差」句式;**非循环**——定义体不得复用被定义术语名本身及其中文名,须用上位类(属)+区别特征(种差)描述;counterExample 只写类别层面的易混淆概念和辨析,不得虚构企业名、编号、日期、数量或金额;无法给出严格定义时 definition 留空;⑧example 只有在输入证据中存在可逐字定位的实例时才填写,并填写 example_source；没有实例证据时必须留空，不得生成“看起来合理”的示例。"""
    _log(f"证据装配 · 库表 {len(ev.get('tab_cols') or {})} 张（结构文本 {len(ev['schema'][:12000])} 字）"
         f" · 文档/代码 {ev.get('n_docs', 0)} 份（{len(docs_block[:9000])} 字）"
         f" · 引用资产 {len(ev.get('refs') or [])} 项 · 技能规则 {len(method)} 字"
         f" · 历史否决负例 {len(_rp)} 条"
         + (f" · 已有本体上下文 {len(base_block)} 字" if base_block else "")
         + f" · 构建参照 {build_references.selected_summary(reference_profile)}"
         + f" · 提示词合计 {len(prompt)} 字")
    tried = 0
    for drv in _drv_order():
        if drv not in available(): continue
        tried += 1
        rt = get_runtime(drv)
        model = getattr(rt, "model", "") or ""
        _log(f"调用引擎 {drv}{(' · 模型 ' + model) if model else ''}（单次上限 600s）…")
        t0 = time.time()
        sid = f"be_{uuid.uuid4().hex[:6]}"
        if log is not None and hasattr(rt, "run_turn_stream"):
            buf = {"s": "", "n": 0}
            def _on(delta, _b=buf):
                _b["s"] += delta
                if "\n" in _b["s"] or len(_b["s"]) >= 240:
                    _b["n"] += 1
                    _log("│ " + _b["s"].rstrip("\n").replace("\n", "\n│ ")); _b["s"] = ""
            # 思考增量按句/按块回报（前缀 ╎），让推理型模型的长时思考也可见；不入正文
            # 节流：推理型模型的思考流每秒数句，逐句回报会把面板与 SSE 刷爆；
            # 攒到 ≥700 字或距上次回报 ≥4s 才发一条（实测逐句回报达 452 条/2min，须压到每分钟几十条）
            rbuf = {"s": "", "n": 0, "t": time.time()}
            def _onr(delta, _b=rbuf):
                _b["s"] += delta
                now = time.time()
                if len(_b["s"]) >= 700 or (now - _b["t"] >= 4.0 and len(_b["s"]) >= 60):
                    _b["n"] += 1; _b["t"] = now
                    _log("╎ 思考 · " + _b["s"].strip().replace("\n", " ")[:700]); _b["s"] = ""
            ok, reply = rt.run_turn_stream(sid, prompt, timeout=600, on_delta=_on, on_reasoning=_onr)
            if rbuf["s"].strip(): _log("╎ 思考 · " + rbuf["s"].strip().replace("\n", " ")[:400])
            if rbuf["n"] or rbuf["s"].strip():
                _log(f"思考阶段结束 · 共回报 {rbuf['n'] + (1 if rbuf['s'].strip() else 0)} 段")
            if buf["s"]: _log("│ " + buf["s"])
        else:
            ok, reply = rt.run_turn(sid, prompt, timeout=600)
        _log(f"引擎返回 · {'成功' if ok else '失败'} · {len(reply or '')} 字 · 用时 {time.time() - t0:.0f}s")
        if ok and reply and not _looks_like_error(reply):
            m = re.search(r"\{[\s\S]*\}", reply)
            if m:
                try:
                    d = json.loads(m.group(0))
                    if isinstance(d.get("objects"), list) and d["objects"]:
                        _log(f"解析 JSON 成功 · 提议对象 {len(d['objects'])} 个 · 关系 {len(d.get('relations') or [])} 条")
                        return d
                    _log("解析结果不含对象，尝试下一引擎")
                except Exception as e:
                    _log(f"JSON 解析失败：{type(e).__name__}: {str(e)[:120]}")
            else:
                _log("回复中未找到 JSON 块")
        else:
            _log(f"引擎回复不可用：{(reply or '')[:160]}")
    _log("未获得有效提议（尝试引擎 %d 个）→ 回退纯数据驱动构建" % tried if tried
         else "无可用引擎 → 回退纯数据驱动构建")
    return None

def _llm_semantic_review(relations, ev):
    """三级控制环第二级:LLM 仅凭 schema 语义复审提议关系(不看数据)。
    返回 {(source,target): bool};引擎离线/超时返回 None(调用方记 skipped,绝不臆造)。
    实证依据:共享域巧合(数据为真、语义为假)只有语义评审能拦(判别实验 9 vs 2)。"""
    try:
        from agent_runtime import get_runtime, available
        if not available(): return None
    except Exception:
        return None
    sch = (ev.get("schema") or "")[:9000]
    out = {}
    for i in range(0, len(relations), 60):
        chunk = relations[i:i + 60]
        lines = [f'{j}. {r["source_concept"]} —{r.get("verb","关联")}→ {r["target_concept"]}' for j, r in enumerate(chunk)]
        prompt = ("你是数据库语义审阅专家。仅凭表结构判断每条提议关系在语义上是否为真实的业务引用"
                  "(同名巧合/共享取值域/顺序编号巧合应判 false;不要臆测数据)。\n"
                  f"表结构:\n{sch}\n提议:\n" + "\n".join(lines) +
                  '\n\n只输出JSON:{"<序号>":true/false,...}')
        got = None
        for drv in _drv_order():
            try:
                if drv not in available(): continue
                ok, rep = get_runtime(drv).run_turn(f"sr_{uuid.uuid4().hex[:6]}", prompt, timeout=110)
                if ok and rep:
                    m = re.search(r"\{[\s\S]*\}", rep)
                    if m:
                        d = json.loads(m.group(0))
                        got = {int(k): v for k, v in d.items() if str(k).isdigit()}
                        break
            except Exception:
                continue
        if got is None: return out or None
        for j, r in enumerate(chunk):
            if j in got and isinstance(got[j], bool):
                out[(r["source_concept"], r["target_concept"])] = got[j]
    return out

def _adjudicate_ir(db, name, extracted, ev):
    """关系数据验证:LLM 提议的关系用真实数据裁决(取值重叠≥60%∧父键可辨→verified;有据无量→candidate)。
    产出与 ir_to_graph 兼容的 IR(objects 带 kind/table/attrs;relations 带 status)。"""
    tc = ev["tab_cols"]; low2real = {t.lower(): t for t in tc}
    # 归一对象:绑定真实表则补 attrs/field_count;非表对象标 candidate
    objects, name2tab, seen_obj = [], {}, set()
    registered_actions = {str(item.get("id")): item for item in _load_ats() if item.get("id")}
    for o in extracted.get("objects", []):
        nm = (o.get("name") or "").strip()
        if not nm or nm in seen_obj: continue        # LLM 可能重复同名对象,去重防图谱节点 id 冲突
        seen_obj.add(nm)
        tb = o.get("table")
        real = low2real.get((tb or "").lower()) or (low2real.get(nm.lower()))
        attrs = [{"col": c, "cn": "", "type": ty} for c, ty in tc.get(real, [])] if real else []
        raw_kind = o.get("kind") if o.get("kind") in content_quality.VALID_KINDS else "object"
        kind, kind_reason = content_quality.normalize_kind(
            raw_kind, name=nm, cn=o.get("cn"), table=real or tb,
            definition=o.get("definition"))
        defn = (o.get("definition") or "").strip()
        if content_quality.definition_conflicts(defn, kind):
            defn = ""
        example = (o.get("example") or "").strip()
        # 表结构只描述字段，不能证明某个业务实例真实存在。实例仅可从用户上传的
        # 文档原文逐字定位，避免把表注释或模型补写内容误标为“已观察事实”。
        example_source = content_quality.locate_example(
            example, (("uploaded_documents", ev.get("docs") or ""),)) if example else None
        # 证据来源(IOF-AV provenance):绑定的真实表为 directSource;文档抽取依据入 adaptedFrom
        ev_src = (o.get("evidence") or "").strip()
        doc_src = [ev_src] if (ev_src and not real) else []
        obj = {"name": nm, "cn": o.get("cn") or nm, "kind": kind,
               "table": real, "tables": [real] if real else [], "field_count": len(attrs),
               "attrs": attrs, "candidate": (not bool(real)) if kind != "action" else True,
               "indicators": [], "evidence": {"sources": [ev_src] if ev_src else []},
               "remark": ev_src,
               # ── IOF-AV 机读注释(借鉴 Industrial Ontology Foundry)──
               "bfo": _KIND_BFO.get(kind, "MaterialEntity"),
               # 当前构建器不生成必要且充分条件，因此不论是否有自然语言定义均属原始类。
               "definition": defn, "isPrimitive": True,
               "example": example if example_source else "",
               "counterExample": (o.get("counterExample") or "").strip(),
               "maturity": "Provisional",                              # 新建产物默认临时,经审可升 Released
               "provenance": {"directSource": real, "adaptedFrom": doc_src, "excerptedFrom": None}}
        obj["definition_status"] = ("model_proposed_pending_review" if defn else
                                    "withheld_kind_conflict" if (o.get("definition") or "").strip() else "missing")
        obj["example_provenance"] = ({"status": "observed", "source": example_source,
                                      "reported_source": (o.get("example_source") or "").strip()}
                                     if example_source else
                                     {"status": "withheld_no_source",
                                      "note": "未能在输入证据中逐字定位"})
        if obj["counterExample"]:
            obj["counterexample_status"] = "illustrative_pending_review"
        if kind_reason:
            obj["kind_review"] = {"original": raw_kind, "normalized": kind,
                                  "rule": kind_reason, "status": "deterministic_normalization"}
        if kind == "action":
            proposed_id = str(o.get("action_id") or "").strip()
            registered = registered_actions.get(proposed_id)
            obj["action_id"] = proposed_id if registered else ""
            obj["action_spec"] = {
                "action_id": proposed_id if registered else "",
                "execution_mode": "decision_capture" if registered else "unbound_candidate",
                "real_writeback": False,
                "connector_status": "not_connected",
                "invocable": bool(registered and registered.get("enabled") is not False),
                "binding_status": "candidate",
                "source_requirement": ev_src,
            }
        objects.append(obj)
        if real: name2tab[nm] = real
    valid = {o["name"] for o in objects}
    categories = {o["name"]: o["bfo"] for o in objects}

    # 三级控制环第二级先做 schema 语义复审。它只生成软标注,不裁剪提议,因此后续数据裁决
    # 仍会完整执行并可揭示「语义存疑但数据确实见证」的争议样本。
    sem_inputs, sem_seen = [], set()
    for proposal in extracted.get("relations", []):
        ss, tt0 = (proposal.get("source") or "").strip(), (proposal.get("target") or "").strip()
        if not ss or not tt0 or ss == tt0 or ss not in valid or tt0 not in valid or (ss, tt0) in sem_seen: continue
        sem_seen.add((ss, tt0))
        sem_inputs.append({"source_concept": ss, "target_concept": tt0,
                           "verb": proposal.get("verb") or "关联"})
    _sem_budget = 90 + 120 * ((len(sem_inputs) + 59) // 60)
    sem = _bounded(lambda: _llm_semantic_review(sem_inputs, ev), _sem_budget) if sem_inputs else None

    con = None
    query_errors = []
    ungrounded = {"both": 0, "one": 0}      # 关系两端未绑真实表的计数,用于解释无法裁决的原因
    def record_query_error(operation, table="", column="", exc=None):
        if len(query_errors) < 100:
            query_errors.append({"operation": operation, "table": str(table)[:120],
                                 "column": str(column)[:120],
                                 "error_type": type(exc).__name__ if exc else "invalid_identifier"})
    try:
        con = ro_connect(db)
    except Exception as exc:
        record_query_error("connect", exc=exc)
    # ↓ 以下取数助手的 t/c 全部来自 **LLM 抽取产物**(_llm_extract_ontology 的返回),
    #   即模型可写、外部可影响的字符串,却要落在 SQL 的标识符位上。
    #   统一先过 _safe_ident:非法名直接返回空结果(等价于"取证不成立"),而不是拼进 SQL。
    #   这条裁决决定了整个关系数据验证链路不会被一个构造出来的列名反噬。
    def _ids(*names):
        """全部合法则返回元组,任一非法返回 None(调用方据此放弃本次取证)。"""
        out = [_safe_ident(n) for n in names]
        return None if any(x is None for x in out) else out

    def distinct(t, c, cap=8000):
        ok = _ids(t, c)
        if not ok:
            record_query_error("distinct", t, c)
            return set()
        t, c = ok
        try: return set(r[0] for r in con.execute(f'SELECT DISTINCT "{c}" FROM "{t}" LIMIT {int(cap)}') if r[0] not in (None, ""))
        except Exception as exc:
            record_query_error("distinct", t, c, exc)
            return set()
    def is_unique(t, c):
        ok = _ids(t, c)
        if not ok:
            record_query_error("unique", t, c)
            return False
        t, c = ok
        try:
            tot, dis = con.execute(f'SELECT COUNT("{c}"), COUNT(DISTINCT "{c}") FROM "{t}"').fetchone()
            return tot and tot == dis
        except Exception as exc:
            record_query_error("unique", t, c, exc)
            return False

    def tuple_distinct(t, columns, cap=8000):
        """多列元组取值集（复合键联合裁决用；跳过任一列为空的行）。"""
        ok = _ids(t, *columns)
        if not ok:
            record_query_error("tuple_distinct", t, ",".join(columns))
            return set()
        t, *columns = ok
        select = ", ".join(f'"{column}"' for column in columns)
        non_null = " AND ".join(f'"{column}" IS NOT NULL' for column in columns)
        try:
            return set(tuple(row) for row in con.execute(
                f'SELECT DISTINCT {select} FROM "{t}" WHERE {non_null} LIMIT {int(cap)}'))
        except Exception as exc:
            record_query_error("tuple_distinct", t, ",".join(columns), exc)
            return set()
    def tuple_unique(t, columns):
        """检查非空多列元组是否唯一，不用字符串拼接，避免分隔符碰撞。"""
        ok = _ids(t, *columns)
        if not ok:
            record_query_error("tuple_unique", t, ",".join(columns))
            return False
        t, *columns = ok
        select = ", ".join(f'"{column}"' for column in columns)
        non_null = " AND ".join(f'"{column}" IS NOT NULL' for column in columns)
        try:
            total = con.execute(f'SELECT COUNT(*) FROM "{t}" WHERE {non_null}').fetchone()[0]
            distinct_count = con.execute(
                f'SELECT COUNT(*) FROM (SELECT {select} FROM "{t}" WHERE {non_null} GROUP BY {select})'
            ).fetchone()[0]
            return bool(total and total == distinct_count)
        except Exception as exc:
            record_query_error("tuple_unique", t, ",".join(columns), exc)
            return False
    # 声明主键感知(Burr-Mondial 发现:自然键 schema 的父键不叫 *_id,须查 PK;企业 *_id 命名此前掩盖了该盲区)
    pkmap = {}
    if con:
        for tt2 in {o.get("table") for o in objects if o.get("table")}:
            safe_t = _safe_ident(tt2)
            if not safe_t:
                record_query_error("table_info", tt2)
                pkmap[tt2] = []
                continue
            try: pkmap[tt2] = [r2[1] for r2 in con.execute(f'PRAGMA table_info("{safe_t}")') if r2[5]]
            except Exception as exc:
                record_query_error("table_info", tt2, exc=exc)
                pkmap[tt2] = []

    relations, seen = [], set()
    for r in extracted.get("relations", []):
        s, t = (r.get("source") or "").strip(), (r.get("target") or "").strip()
        if not s or not t or s == t or s not in valid or t not in valid or (s, t) in seen: continue
        seen.add((s, t))
        status, overlap, note = "candidate", None, "LLM 提议·待取证"
        ev_keys = None                                # 最佳尝试也留结构化证据,不只给 verified 留痕
        ts, tt = name2tab.get(s), name2tab.get(t)
        # 数据裁决的前提是两端都落到真实表上。统计未落地的情形,好让「verified 0 条」
        # 可解释——用户否则无从区分「数据源太薄」与「裁决器坏了」。
        if not ts and not tt: ungrounded["both"] += 1
        elif not ts or not tt: ungrounded["one"] += 1
        child_hint = str(r.get("child_key") or "").strip()
        parent_hint = str(r.get("parent_key") or "").strip()
        if con and ts and tt:
            cs = [c for c, _ in tc.get(ts, [])]; ct = [c for c, _ in tc.get(tt, [])]
            ctl = [x.lower() for x in ct]
            stem = re.sub(r"^(dim_|fact_|dws_|dwd_|ods_|agg_)", "", tt, flags=re.I).lower()
            pk_t = pkmap.get(tt) or []
            # 连接键候选(多候选逐一尝试直到验证——Burr 系列实证:首个候选失败不代表无引用):
            # ⓪LLM 候选提示(仅排序,先验证列真实存在) ①后缀词干 ②等值 ③前缀 ④与父列同名
            cand_keys = []
            if child_hint and "," not in child_hint:
                hinted = next((c for c in cs if c.lower() == child_hint.lower()), None)
                if hinted: cand_keys.append(hinted)
            for c in cs:
                cl = c.lower()
                if re.search(r"_(id|code)$", c, re.I) and (re.sub(r"_(id|code)$", "", c, flags=re.I).lower() in stem or cl in ctl):
                    if c not in cand_keys: cand_keys.append(c)
            for c in cs:
                cl = c.lower()
                if c not in cand_keys and (cl == stem or cl == tt.lower()): cand_keys.append(c)
            for c in cs:
                cl = c.lower()
                if c not in cand_keys and len(stem) >= 4 and cl.startswith(stem) and re.fullmatch(r"[a-z]*\d?", cl[len(stem):]):
                    cand_keys.append(c)
            for c in cs:
                if c not in cand_keys and c.lower() in ctl and re.search(r"(id|code|key|no)$", c, re.I): cand_keys.append(c)
            best = None
            for key in cand_keys[:6]:
                # 父列候选序:声明PK优先(Mondial 发现:父表可有与表同名的非键列,同名优先会撞错列)
                pcols = []
                if parent_hint and "," not in parent_hint:
                    hinted = next((x for x in ct if x.lower() == parent_hint.lower()), None)
                    if hinted: pcols.append(hinted)
                if len(pk_t) == 1 and pk_t[0] not in pcols: pcols.append(pk_t[0])
                for x in ct:
                    if x.lower() == key.lower() and x not in pcols: pcols.append(x)
                for x in ct:
                    if x.lower() == "id" and x not in pcols: pcols.append(x)
                for x in ct:
                    if re.search(r"_(id|code)$", x, re.I) and x not in pcols: pcols.append(x)
                child = distinct(ts, key)
                if not child: continue
                cunique = is_unique(ts, key)
                for pcol in pcols[:3]:
                    ov = dao_core.overlap_pct(child, distinct(tt, pcol))
                    punique = is_unique(tt, pcol) if ov >= dao_core.MIN_OVERLAP else False
                    name_supported = dao_core.name_ok(key, tt, pcol, child_table=ts)
                    reverse = ov >= dao_core.MIN_OVERLAP and dao_core.should_reverse(cunique, punique)
                    verdict = dao_core.classify(overlap=ov, parent_unique=punique, name_ok=name_supported,
                                                child_distinct=len(child), min_distinct=1, exclude_pk_child=False)
                    trial_status, reason = verdict["status"], verdict["reason"]
                    if reverse:
                        trial_status = "candidate"
                        reason = (f"{key}→{tt}.{pcol} 方向反证:子列唯一而目标列不唯一,"
                                  "不能把唯一侧指向多侧升级为 verified")
                    trial = {"child_key": key, "parent_key": pcol, "overlap": round(ov, 1),
                             "source": "key_overlap", "parent_unique": bool(punique),
                             "child_unique": bool(cunique), "name_ok": bool(name_supported),
                             "name_score": dao_core.name_score(key, tt, pcol),
                             "theta": dao_core.MIN_OVERLAP,
                             "direction": ("reverse" if reverse else
                                           ("ambiguous" if cunique and punique else "child_to_parent")),
                             "decision": reason, "adjudication_status": trial_status}
                    if best is None or ov > best[0]: best = (ov, trial_status, reason, trial)
                    if trial_status == "verified":
                        status, overlap, note, ev_keys = "verified", round(ov, 1), reason, trial
                        break
                if status == "verified": break
            # 复合键联合裁决：单列未通过时，优先验证模型提示和声明复合主键，
            # 再尝试最多四列的同名列组合。元组整体计算重叠率和父键唯一性。
            if status != "verified":
                shared = [c for c in cs if c.lower() in ctl][:4]
                if len(pk_t) >= 2 and all(p.lower() in [c.lower() for c in cs] for p in pk_t):
                    pri = [next(c for c in cs if c.lower() == p.lower()) for p in pk_t]
                    shared = pri + [c for c in shared if c not in pri]
                pairs = []
                hc, hp = [x.strip() for x in child_hint.split(",")], [x.strip() for x in parent_hint.split(",")]
                if len(hc) == len(hp) and len(hc) >= 2:
                    rc = [next((c for c in cs if c.lower() == x.lower()), None) for x in hc]
                    rp = [next((c for c in ct if c.lower() == x.lower()), None) for x in hp]
                    if all(rc + rp): pairs.append((tuple(rc), tuple(rp)))
                if len(pk_t) >= 2 and all(p.lower() in [c.lower() for c in cs] for p in pk_t):
                    child_columns = tuple(next(c for c in cs if c.lower() == p.lower()) for p in pk_t)
                    parent_columns = tuple(pk_t)
                    if (child_columns, parent_columns) not in pairs:
                        pairs.append((child_columns, parent_columns))
                for width in range(2, min(4, len(shared)) + 1):
                    for child_columns in combinations(shared, width):
                        parent_columns = tuple(next(x for x in ct if x.lower() == c.lower()) for c in child_columns)
                        if (child_columns, parent_columns) not in pairs:
                            pairs.append((child_columns, parent_columns))
                for child_columns, parent_columns in pairs[:16]:
                    chp = tuple_distinct(ts, child_columns)
                    if not chp: continue
                    ovp = dao_core.overlap_pct(chp, tuple_distinct(tt, parent_columns))
                    punique = tuple_unique(tt, parent_columns) if ovp >= dao_core.MIN_OVERLAP else False
                    cunique = tuple_unique(ts, child_columns)
                    child_key = ",".join(child_columns)
                    parent_key = ",".join(parent_columns)
                    name_supported = dao_core.key_name_ok(child_key, parent_key)
                    verdict = dao_core.classify(overlap=ovp, parent_unique=punique, name_ok=name_supported,
                                                child_distinct=len(chp), min_distinct=1, exclude_pk_child=False)
                    reverse = ovp >= dao_core.MIN_OVERLAP and dao_core.should_reverse(cunique, punique)
                    trial_status = "candidate" if reverse else verdict["status"]
                    reason = ("复合键方向反证:子侧成对唯一而目标侧不唯一,送审" if reverse else verdict["reason"])
                    trial = {"child_key": child_key, "parent_key": parent_key,
                             "overlap": round(ovp, 1), "source": "composite_key",
                             "parent_unique": bool(punique), "child_unique": bool(cunique),
                             "name_ok": bool(name_supported), "theta": dao_core.MIN_OVERLAP,
                             "direction": ("reverse" if reverse else
                                           ("ambiguous" if cunique and punique else "child_to_parent")),
                             "decision": reason, "adjudication_status": trial_status}
                    if best is None or ovp > best[0]: best = (ovp, trial_status, reason, trial)
                    if trial_status == "verified":
                        status, overlap = "verified", round(ovp, 1)
                        note = f"复合键({child_key})→{tt} · {reason}"
                        ev_keys = trial
                        break
            if status != "verified" and best is not None:
                best_ov, _best_status, best_reason, best_evidence = best
                status, overlap, note, ev_keys = "candidate", round(best_ov, 1), best_reason, best_evidence
        verb = r.get("verb", "关联")
        # 模型给出的 founded_relation 必须参与判定,否则接地恒为 0:from_verb 只查
        # VERB_RELATIONS 这张 10 词表,而模型提的是自由业务动词(面向/订购物项/归入…),
        # 一律落到 unmapped。normalize 会核对官方关系名与定义域/值域,填错照样拒绝,
        # 所以采信模型的提名不等于放松校验——只是给它一个能被校验的入口。
        grounding = ontology_grounding.normalize(
            r.get("founded_relation"), r.get("temporal"), verb,
            categories.get(s), categories.get(t))
        rel_new = {"source_concept": s, "target_concept": t, "verb": verb,
                   "status": status, "evidence_status": status,
                   "overlap": overlap, "note": note,
                   "founded_relation": grounding["relation"],
                   "grounding_iri": grounding["iri"],
                   "grounding_status": grounding["status"],
                   "grounding_reason": grounding["reason"],
                   "temporal": grounding["temporal"],
                   "proposal": {"rationale": str(r.get("rationale") or "")[:500],
                                "child_key_hint": child_hint,
                                "parent_key_hint": parent_hint}}
        if ev_keys: rel_new["evidence"] = ev_keys      # DR-033 结构化 JOIN 键:自建本体要能驱动问数
        relations.append(rel_new)
    if con: con.close()
    # 数据证据与语义裁定分轴保存。语义 fail 不抹掉可回放的数据证据，但 CQ/发布检查
    # 不再把这类 disputed 边当成强业务路径；离线记 not_reviewed，不臆造通过。
    for rel in relations:
        v = (sem or {}).get((rel["source_concept"], rel["target_concept"]))
        rel["semantic"] = "pass" if v is True else ("fail" if v is False else "skipped")
        rel["semantic_status"] = "model_supported" if v is True else ("disputed" if v is False else "not_reviewed")
        if v is False and rel["status"] == "verified":
            rel["note"] += ";语义评审存疑(数据证据成立但不得用于验收问题的强路径,须人审)"
    # 无法进入数据裁决的原因诊断:关系两端必须都绑定到真实表才谈得上取值重叠与父键唯一。
    # 数据源只有一两张表时,LLM 从文档抽出的对象大多没有对应表,关系必然全部停在 candidate。
    # 这不是裁决器失效,但必须说清楚,否则用户只看到「verified 0 条」无从判断。
    bound_objs = sum(1 for o in objects if o.get("table"))
    diag = {"tables": len(tc), "objects": len(objects), "objects_bound": bound_objs,
            "relations": len(relations),
            "verified": sum(1 for r in relations if r["status"] == "verified"),
            "unadjudicable_both_unbound": ungrounded["both"],
            "unadjudicable_one_unbound": ungrounded["one"]}
    blocked = ungrounded["both"] + ungrounded["one"]
    if diag["verified"] == 0 and relations:
        if len(tc) <= 1:
            diag["reason"] = (f"数据源仅 {len(tc)} 张表,关系两端无法同时绑定到真实表,"
                              "数据裁决不具备前提;关系只能停在 candidate。"
                              "接入含多表且有外键/共享取值的数据源后可获得 verified。")
        elif blocked >= max(1, len(relations) // 2):
            diag["reason"] = (f"{blocked}/{len(relations)} 条关系至少有一端未绑定真实表"
                              f"(共 {len(objects)} 个对象,仅 {bound_objs} 个绑到表)。"
                              "这些对象来自文档抽取而非库表,无表可查即无从取证。")
        else:
            diag["reason"] = ("关系两端已绑表但均未通过判据:取值重叠未达阈值、"
                              "父键不唯一或键名不相容。逐条依据见各关系的 note 与 evidence。")
    ir = {"scenario": {"name": name, "style": "multimodal-llm(多源LLM抽取+关系数据验证)",
                       "object_count": len(objects), "relation_count": len(relations),
                       "query_errors": query_errors,
                       "adjudication_diagnostics": diag,
                       "evidence": {"tables": len(tc), "docs": ev["n_docs"], "refs": ev["refs"]}},
          "objects": objects, "relations": relations}
    return ir

def _merge_ir(base, new):
    """把新一轮构建结果并入既有本体,返回 (合并后 IR, 变更统计)。

    构建对话原本每轮都新建一张图,已有本体无从迭代——用户提一句「补上客户与工单的
    关系」就会得到一张互不相干的新图。此处按名称归并,并遵守两条不可退让的规则:

    1. **已确立的结论不被新一轮覆盖。** 关系状态按 asserted > verified > inferred >
       candidate 取高者:人审断言与已有数据证据都不因为模型这次没提到而降级或消失。
    2. **新增只做补充,不做删除。** 模型本轮没提到的对象/关系一律保留;要删除得走
       人审编辑(可撤销、有审计),不能由一次自由文本对话静默抹掉。

    这样迭代才是「在原图上继续做」,而不是「重来一遍并丢掉上一轮的人工成果」。
    """
    rank = {"asserted": 3, "verified": 2, "inferred": 1, "candidate": 0}
    out = json.loads(json.dumps(base))
    objs = out.setdefault("objects", [])
    rels = out.setdefault("relations", [])
    by_obj = {o.get("name"): o for o in objs if o.get("name")}
    by_rel = {(r.get("source_concept"), r.get("target_concept")): r for r in rels}
    stat = {"objects_added": 0, "objects_enriched": 0,
            "relations_added": 0, "relations_upgraded": 0, "relations_kept": 0}

    for o in new.get("objects", []) or []:
        nm = o.get("name")
        if not nm: continue
        cur = by_obj.get(nm)
        if cur is None:
            objs.append(o); by_obj[nm] = o; stat["objects_added"] += 1
            continue
        # 只补空字段:已有定义/反例/绑表等人工或前轮成果不被本轮覆盖
        for k in ("definition", "example", "counterExample", "cn", "table", "remark"):
            if not cur.get(k) and o.get(k):
                cur[k] = o[k]; stat["objects_enriched"] += 1
        if not cur.get("attrs") and o.get("attrs"):
            cur["attrs"] = o["attrs"]; cur["field_count"] = len(o["attrs"])

    for r in new.get("relations", []) or []:
        pair = (r.get("source_concept"), r.get("target_concept"))
        if not all(pair): continue
        cur = by_rel.get(pair)
        if cur is None:
            rels.append(r); by_rel[pair] = r; stat["relations_added"] += 1
            continue
        if rank.get(r.get("status"), 0) > rank.get(cur.get("status"), 0):
            cur.update(r); stat["relations_upgraded"] += 1     # 新一轮拿到更强证据才覆盖
        else:
            stat["relations_kept"] += 1                        # 否则保留原结论,不降级

    # 构建历史:out 从底本深拷贝而来,带的是底本的历史;本轮的 manifest 挂在 new 上。
    # 若不显式接上,新一轮问了什么就彻底丢失——继续构建将失去可追溯的过程记录。
    new_mf = new.get("build_manifest")
    if isinstance(new_mf, dict):
        hist = out.get("build_history")
        if not isinstance(hist, list): hist = []
        rec = {"round": len(hist) + 1, "at": new_mf.get("created_at"),
               "request": new_mf.get("request"), "source": new_mf.get("source"),
               "skills": new_mf.get("skills"), "cqs": new_mf.get("cqs"),
               "references": new_mf.get("references"),
               "method": new_mf.get("method"), "evidence": new_mf.get("evidence"),
               "merge": dict(stat)}                       # 本轮实际并入了什么,一并留痕
        hist.append(rec)
        out["build_history"] = hist[-50:]
        out["build_manifest"] = new_mf                    # 最近一轮仍指向本轮

    sc = out.setdefault("scenario", {})
    sc["object_count"] = len(objs); sc["relation_count"] = len(rels)
    sc["adjudication_diagnostics"] = (new.get("scenario") or {}).get("adjudication_diagnostics") \
        or sc.get("adjudication_diagnostics")
    sc["iterations"] = int(sc.get("iterations") or 1) + 1
    return out, stat


def _normalize_build_cqs(value, limit=30):
    """API/UI 的验收问题输入归一成 cq_check 可消费的 str/dict 列表。"""
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:500])
        elif isinstance(item, dict):
            qv = str(item.get("q") or item.get("question") or "").strip()
            if qv:
                expect = [str(x)[:120] for x in (item.get("expect") or []) if isinstance(x, (str, int, float))][:20]
                out.append({"q": qv[:500], "expect": expect})
        if len(out) >= limit: break
    return out


def _attach_build_manifest(ir, *, q, source_id, source_name, skills, cqs,
                           stability, method, evidence, created_at=None, references=None):
    """把本轮构建输入与证据摘要固化进产物，便于复现、人审和验收。

    只保存数据源 id/显示名与证据文件的 basename，不写数据库绝对路径、密钥或文档全文。
    以前这些信息只在 SSE done 事件里存在，一旦刷新页面就无法证明「该产物用了什么」。
    """
    ev = evidence or {}
    manifest_ev = {
        "tables": int(ev.get("tables") or 0),
        "documents": int(ev.get("docs") or 0),
        "references": [str(x)[:180] for x in (ev.get("refs") or [])[:30]],
        "files": [{"name": str(x.get("name") or "")[:180],
                   "type": str(x.get("type") or "其它")[:40],
                   "consumed_as_text": bool(x.get("consumed_as_text"))}
                  for x in (ev.get("files") or [])[:30] if isinstance(x, dict)],
    }
    manifest = {
        "version": 1,
        "created_at": created_at or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request": str(q or "")[:2000],
        "source": {"id": str(source_id or "")[:100], "name": str(source_name or "")[:120]},
        "skills": [str(x)[:100] for x in (skills or [])[:20] if isinstance(x, str) and x.strip()],
        "cqs": _normalize_build_cqs(cqs),
        "references": build_references.normalize(references, legacy_default=True),
        "reference_assets": build_references.asset_trace(references),
        "stability_requested": bool(stability),
        "method": str(method or "")[:120],
        "evidence": manifest_ev,
    }
    ir["build_manifest"] = manifest
    # 追加式构建历史:每轮一条,只增不改。build_manifest 保留为「最近一轮」以兼容既有读取方,
    # 但迭代场景下只看最近一轮就无从知道这张图是怎么一步步建起来的——继续构建时要把
    # 历次诉求交给模型,也要在界面上让人看见,所以历史必须留在产物里而不是只存浏览器。
    hist = ir.get("build_history")
    if not isinstance(hist, list): hist = []
    hist.append({"round": len(hist) + 1, "at": manifest["created_at"],
                 "request": manifest["request"], "source": manifest["source"],
                 "skills": manifest["skills"], "cqs": manifest["cqs"],
                 "references": manifest["references"],
                 "reference_assets": manifest["reference_assets"],
                 "method": manifest["method"], "evidence": manifest["evidence"]})
    ir["build_history"] = hist[-50:]           # 只留最近 50 轮,避免产物无限膨胀
    sc = ir.setdefault("scenario", {})
    sc["evidence"] = {"tables": manifest_ev["tables"], "docs": manifest_ev["documents"],
                      "refs": manifest_ev["references"], "files": manifest_ev["files"]}
    return manifest

@app.post("/api/build/inquire")
def build_inquire():
    """Hermes 本体构建问询台:对话式驱动 → 意图解析 → 数据驱动构建(数据验证规则) → agent 命名/摘要。SSE 流式 agentic 步骤。"""
    body = request.json or {}
    q = (body.get("q") or "").strip()
    source = body.get("source") or "uploads"
    name = (body.get("name") or "").strip()
    skills = body.get("skills") or []
    cqs = _normalize_build_cqs(body.get("cqs"))
    stability = bool(body.get("stability"))    # M1 opt-in:二次独立生成量化一致性(构建耗时翻倍)
    references_explicit = "references" in body
    try:
        requested_references = build_references.normalize(
            body.get("references"), legacy_default=not references_explicit)
    except ValueError as exc:
        return jsonify({"error": str(exc), "field": "references"}), 400
    # 在已有本体上迭代:给定则不新建图谱,而是把本轮结果并入该图(见 _merge_ir 的两条规则)。
    # 只接受本仓构建产物 built_*,示例图与应用本体不允许被对话直接改写。
    base_graph = re.sub(r"[^A-Za-z0-9_]", "", str(body.get("base_graph") or ""))[:40]
    if base_graph and not base_graph.startswith("built_"):
        return jsonify({"error": "只能在自建本体(built_*)上迭代"}), 400
    def sse(o): return "data: " + json.dumps(o, ensure_ascii=False, default=str) + "\n\n"
    def gen():
        import time as _t
        t0 = _t.time()
        references = requested_references
        def push(step, ok, info=""):
            return sse({"type": "step", "step": step, "ok": ok, "info": info, "ts": _t.strftime("%H:%M:%S")})
        if not q:
            yield sse({"type": "error", "error": "请输入构建诉求"}); return
        db, sname = None, source
        if source == "demo": db, sname = DB, "示例主库"
        elif source == "uploads": db, sname = UPLOAD_DB, "上传数据库"
        else:
            for c in _load_conns():
                if c.get("id") == source:
                    sname = c.get("name")
                    if c.get("kind") == "sqlite" and c.get("path") and os.path.exists(c["path"]):
                        db = c["path"]
                    else:
                        yield push("scope_source", False, f"「{sname}」为外部库,当前离线不可取数,请改选可用 SQLite 源")
                        yield sse({"type": "error", "error": "数据源不可用(外部库需内网/驱动)"}); return
                    break
        if not db or not os.path.exists(db):
            yield push("scope_source", False, "数据源不存在,请先上传数据或连接库")
            yield sse({"type": "error", "error": "无可用数据源"}); return
        if not _sqlite_tables(db):
            yield push("scope_source", False, f"「{sname}」无可读表,请先上传/建表")
            yield sse({"type": "error", "error": "数据源为空"}); return
        yield push("intake", True, f"接收构建诉求 · 数据源「{sname}」· 编排技能 {len(skills)} 个")
        if cqs:
            yield push("cq_holdout", True, f"验收问题 {len(cqs)} 条已留出；不进入模型提议，仅在构建后盲测")
        yield sse({"type": "status", "text": "多智能体引擎解析建模意图与范围…"})
        plan = _bounded(lambda: _build_intent(q, sname, skills), 30) or {}
        # gname 同时来自用户输入与 LLM 生成,且会作为 argv 传给 quick_build 子进程:
        # 先裁成安全 argv(去控制字符/shell 元字符、限长、不以 - 开头)再往下走。
        gname = _safe_argv(name or plan.get("name") or (q[:14] + "本体"), cap=60, default="未命名本体")
        yield push("intent", True, f"意图解析 · 目标本体「{gname}」· 策略:{plan.get('strategy', 'LLM 候选提议 + 数据验证')}")
        if skills:
            yield push("orchestrate", True, "编排技能建模规则:" + "、".join(skills[:5]))
            _mt = _skill_method_text(skills)          # #2 技能注入痕迹:建模规则进 prompt 在流水线里可见
            _nseg = sum(1 for s in skills if s in _builtin_skill_names() or s in _SKILL_METHOD or _custom_skill_path(s))
            yield push("skill_inject", bool(_mt), f"技能注入 · {_nseg} 段建模规则并入抽取 prompt(共 {len(_mt)} 字)" if _mt
                       else "技能注入 · 选中技能无可注入正文(内容为空?)")
        base_ir = None
        if base_graph:
            base_ir = load_ir(base_graph)
            if not base_ir:
                yield push("scope_base", False, f"本体「{base_graph}」不存在,无法迭代")
                yield sse({"type": "error", "error": "待迭代的本体不存在"}); return
            key = base_graph
            # 旧/程序化客户端不传 references 时，迭代默认继承底本最近一轮配置；
            # 显式传 none 或新配置则以本轮选择为准。
            if not references_explicit:
                inherited = (base_ir.get("build_manifest") or {}).get("references")
                if isinstance(inherited, dict):
                    try:
                        references = build_references.normalize(inherited)
                    except ValueError:
                        references = requested_references
            _bo = len(base_ir.get("objects") or []); _br = len(base_ir.get("relations") or [])
            yield push("scope_base", True,
                       f"在已有本体「{(base_ir.get('scenario') or {}).get('name') or base_graph}」上迭代 · "
                       f"现有对象 {_bo} 个 · 关系 {_br} 条(本轮只增补与升级,不删除既有结论)")
        else:
            key = "built_" + uuid.uuid4().hex[:6]
        yield push("reference_profile", True,
                   "构建参照 · " + build_references.selected_summary(references))
        outp = _confined(WORK, key + ".json")
        # ① 多源证据聚合(库结构 + 上传文档/代码 + 图像引用)
        yield sse({"type": "status", "text": "聚合多源证据(库表结构 / 建表代码 / 业务文档 / 图像引用)…"})
        ev = _gather_evidence(db)
        ntab = len(ev["tab_cols"])
        emeta = f"库表 {ntab} 张"
        if ev["n_docs"]: emeta += f" · 文档/代码证据 {ev['n_docs']} 份"
        if ev["refs"]: emeta += f" · 引用资产 {len(ev['refs'])} 项"
        yield push("gather_evidence", True, "取证:" + emeta)
        method = "LLM 辅助提议"
        ir = None
        # ② LLM 综合数据库结构和已解析文档提出候选本体。
        yield sse({"type": "status", "text": "智能引擎正在根据表结构与已解析文档提出对象和关系(约 2-4 分钟)…"})
        def _stream_extract():
            """把 LLM 抽取过程流出去：log → 过程输出面板；tick → 已用时长。返回抽取结果。"""
            got = None
            for item in _bounded_stream(
                    lambda lg: _llm_extract_ontology(q, ev, skills, cqs, log=lg,
                                                     base_ir=base_ir, references=references), 640):
                if item[0] == "log":
                    yield sse({"type": "log", "text": item[1], "ts": _t.strftime("%H:%M:%S")})
                elif item[0] == "tick":
                    yield sse({"type": "tick", "elapsed": item[1]})
                else:
                    _, got, _err, _to = item
                    if _err:
                        yield sse({"type": "log", "text": f"抽取异常：{type(_err).__name__}: {str(_err)[:160]}",
                                   "ts": _t.strftime("%H:%M:%S")})
                    if _to:
                        yield sse({"type": "log", "text": "抽取超时（640s），放弃等待并回退数据驱动构建",
                                   "ts": _t.strftime("%H:%M:%S")})
            return got
        extracted = yield from _stream_extract()
        if extracted and extracted.get("objects"):
            yield push("llm_extract", True, f"LLM 抽取 · 对象 {len(extracted.get('objects', []))} 个 · 提议关系 {len(extracted.get('relations', []))} 条")
            _stab = None
            if stability:      # M1:再独立生成一次,用 Jaccard 量化提议一致性(不作否决,仅记录+提示人审)
                yield sse({"type": "status", "text": "一致性门控:第二次独立生成中(用于量化引擎方差,约 2-4 分钟)…"})
                second = yield from _stream_extract()
                if second and second.get("relations") is not None:
                    _stab, _both = _stability_annotate(extracted, second)
                    yield push("stability", True,
                               f"一致性:Jaccard {_stab['jaccard']} · 两次均提出 {_stab['both']}/{_stab['union']} 条"
                               f"(仅作标注；是否通过仍由数据验证规则决定)")
                else:
                    yield push("stability", False, "第二次生成失败/超时,本次不产出一致性指标(不臆造)")
            # ③ 关系数据验证:LLM 提议的关系用真实数据裁决
            yield sse({"type": "status", "text": "关系数据验证:用真实数据校验每条提议关系(取值重叠 / 父键唯一)…"})
            ir = _adjudicate_ir(db, gname, extracted, ev)
            if _stab: ir["stability"] = _stab          # M1 一致性指标随图谱留档,供论文与人审引用
            _sc = {"pass": 0, "fail": 0, "skipped": 0}
            for _r in ir.get("relations", []): _sc[_r.get("semantic", "skipped")] = _sc.get(_r.get("semantic", "skipped"), 0) + 1
            if _sc["pass"] + _sc["fail"]:
                yield push("semantic_review", True, f"语义评审:{_sc['pass']} 通过 · {_sc['fail']} 存疑(建议人审)")
            else:
                yield push("semantic_review", True, "语义评审:引擎不可用,已跳过(不臆造)")
        else:
            # 兜底:LLM 离线/超时 → 纯数据驱动 quick_build(仍是数据验证规则)
            method = "数据驱动(LLM 离线兜底)"
            yield push("llm_extract", False, "LLM 引擎超时/离线 → 回退纯数据驱动构建(数据验证规则)")
            yield sse({"type": "status", "text": "数据驱动构建本体中(读表 / 主外键推断 / 取值重叠验证)…"})
            try:
                # 三个 argv 的来源与约束(避免"看起来像命令注入"的疑虑,也真的封死它):
                #   db   —— 只能是 DB / UPLOAD_DB / 已登记连接里的 sqlite 路径,且上文已 os.path.exists 校验;
                #   outp —— _confined(WORK, built_<uuid>.json),不含用户输入;
                #   gname—— 已过 _safe_argv。
                # 且以 list 形式调用(不经 shell),元字符不会被解释。
                # 迭代模式下写到临时文件:quick_build 会整体覆盖目标文件,若直接写 outp,
                # 一旦构建在写盘后、合并前失败(异常/无产物),用户的底本就被一份未合并的
                # 新产物顶掉且无从恢复。底本必须等合并成功后再由 _atomic_json 一次性替换。
                _bp = _confined(WORK, key + ".building.json") if base_ir is not None else outp
                proc = subprocess.Popen([sys.executable, _confined(HERE, "quick_build.py"), db, _bp, gname,
                                         ACTION_TYPES_F],
                                        cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in iter(proc.stdout.readline, ""):
                    line = line.strip()
                    if line: yield push("construct", True, line[:120])
                proc.wait(timeout=10)
            except Exception as e:
                yield push("construct", False, f"构建异常:{str(e)[:90]}")
            ir = _load_json(_bp)
            if _bp != outp:
                try: os.remove(_bp)                 # 中间产物不留在图谱目录里冒充一张图
                except OSError: pass
        if not isinstance(ir, dict) or not ir.get("objects"):
            yield sse({"type": "error", "error": "构建失败(无产物)"}); return
        action_projection = action_ontology.project_registered_actions(ir, _load_ats())
        if action_projection["added_nodes"] or action_projection["enriched_nodes"]:
            yield push("action_projection", True,
                       f"动作注册表投影 · 动作节点 {ir['scenario']['action_count']} 个 · "
                       f"对象绑定 {action_projection['added_relations']} 条；执行方式为决策记录，不声称真实写回")
        build_references.apply_profile(ir, references)
        _attach_build_manifest(
            ir, q=q, source_id=source, source_name=sname, skills=skills, cqs=cqs,
            stability=stability, method=method,
            evidence={"tables": ntab, "docs": ev["n_docs"], "refs": ev["refs"], "files": ev["files"]},
            created_at=_t.strftime("%Y-%m-%dT%H:%M:%S%z"), references=references)
        # ④′ 迭代模式:并入既有本体。放在验收检查之前,好让质量评估针对合并后的完整本体,
        # 而不是只评估本轮增量——否则「本轮没提到的部分」会被算成缺失。
        if base_ir is not None:
            ir, _mg = _merge_ir(base_ir, ir)
            # 本轮选择作用于合并后的整张图，避免底本残留上一次标准的专用字段。
            build_references.apply_profile(ir, references)
            yield push("merge", True,
                       f"并入已有本体 · 新增对象 {_mg['objects_added']} 个 · 补全字段 {_mg['objects_enriched']} 处 · "
                       f"新增关系 {_mg['relations_added']} 条 · 证据升级 {_mg['relations_upgraded']} 条 · "
                       f"保留原结论 {_mg['relations_kept']} 条(已确立的 verified/asserted 不被覆盖)")
        # ⑤ 验收检查：图结构 + verified 证据契约 + 定义质量 + CQ 可达性，结果可重复。
        # quick_build 已先检查一次(无 CQ)；此处加入本轮 CQ 后重新计算。
        quality = build_quality.evaluate(ir, cqs)
        ir["build_quality"] = quality
        ir["gaps"] = quality["gaps"]
        _atomic_json(outp, ir)
        qsum = quality["summary"]
        yield push("critic", quality["result"] != "fail",  # SSE 类型名为兼容旧客户端保留
                   f"验收检查:{build_quality.result_text(quality['result'])} · 阻断问题 {qsum['blocking_issues']} · "
                   f"待审 {qsum['review_items']} · {build_quality.cq_status_text(quality['cq'])}")
        g = ir_to_graph(key, ir)
        objs = ir.get("objects", []); rels = ir.get("relations", [])
        nev = sum(1 for o in objs if o.get("kind") == "event")
        nact = sum(1 for o in objs if o.get("kind") == "action")
        ver = sum(1 for l in rels if l.get("status") == "verified"); cand = len(rels) - ver
        yield push("verify", True, f"关系数据验证 · verified {ver} 条 · candidate/其它 {cand} 条 · "
                                  f"事件对象 {nev} 个 · 动作节点 {nact} 个")
        # verified 为 0 时把成因一并说清。多数情况不是裁决失效,而是数据源里没有可对证的表;
        # 只报一个 0 会让用户以为系统坏了,并且不知道下一步该做什么。
        _diag = (ir.get("scenario") or {}).get("adjudication_diagnostics") or {}
        if _diag.get("reason"):
            yield push("verify_diagnosis", True, "未产生 verified 关系的原因:" + _diag["reason"])
        yield sse({"type": "status", "text": "智能引擎生成本体说明与建模摘要…"})
        summ = _bounded(lambda: _build_summary(q, gname, ir), 35) or _build_rule_summary(gname, ir)
        yield push("narrate", True, f"生成本体说明 · {len(summ)} 字")
        stats = {"objects": len(g["nodes"]), "events": nev, "actions": nact,
                 "links": len(g["edges"]), "verified": ver,
                 "candidate": cand, "quality_result": quality["result"],
                 "quality_gate": quality["result"]}
        yield sse({"type": "done", "graph_key": key, "name": gname, "stats": stats, "summary": summ,
                   # 迭代信息随 done 一起回传:完成卡要能说清「并入了哪张图、并入了多少」,
                   # 否则用户只看到「构建完成」,分不出这轮是新建了一张还是并进了原图。
                   "iterated": base_ir is not None,
                   "base_graph": base_graph or "", "merge": _mg if base_ir is not None else None,
                   "iterations": (ir.get("scenario") or {}).get("iterations"),
                   "method": method, "evidence": {"tables": ntab, "docs": ev["n_docs"], "refs": ev["refs"]},
                   "references": references,
                   "quality": {"result": quality["result"], "gate": quality["result"],
                               "summary": quality["summary"], "cq": quality["cq"],
                               "references": quality["references"]},
                   "note": plan.get("note", ""), "elapsed": round(_t.time() - t0, 1)})
    from flask import Response, stream_with_context
    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.post("/api/build/quality")
def build_quality_check():
    """对已有图谱重新执行验收检查；只报告问题，不自动修改图谱。"""
    body = request.json or {}
    key = str(body.get("graph") or "").strip()
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    return jsonify({"graph": key, **build_quality.evaluate(ir, _normalize_build_cqs(body.get("cqs")))})

# ── 数据连接浏览 + 数据可视化(对齐平台『配置数据源·连接原始数据库』与『数据看板/大屏』)──
# ── C7 外部库实连:连接器层(mysql/doris=pymysql, postgres=psycopg2;只读约束;凭据 0600 只写不回显)──
_CONN_SECRETS_F = os.path.join(WORK, "conn_secrets.json")
_CONN_KEY_F = os.path.join(WORK, ".conn_key")     # 本机主密钥(0600);与密文分文件存放

def _conn_key():
    """取/建本机主密钥。与密文分开存放的意义:conn_secrets.json 被顺手带走(打包、
    备份、误提交)时不等于密码泄露 —— 还需要同机的 .conn_key。两者都在 workdir,
    这挡不住已拿到本机文件系统读权限的攻击者,但确实挡住了"随手复制一个 json"这条最常见的泄露路径。"""
    import base64
    with _WRITE_LOCK:
        if os.path.exists(_CONN_KEY_F):
            try:
                raw = open(_CONN_KEY_F, "rb").read().strip()
                if raw: return raw
            except OSError:
                pass
        raw = base64.urlsafe_b64encode(os.urandom(32))
        fd = os.open(_CONN_KEY_F, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # 创建即 0600,无可读窗口
        with os.fdopen(fd, "wb") as fp: fp.write(raw)
        return raw

def _fernet():
    """加密后端:装了 cryptography 就用 Fernet(AES-CBC + HMAC),没装返回 None。
    不自造加密算法 —— 缺依赖时如实降级并告警,好过用一个看着像加密的异或。"""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    try:
        return Fernet(_conn_key())
    except Exception:
        _LOG.warning("连接凭据主密钥不可用,本次不加密存储")
        return None

def _enc_secret(plain):
    """明文 → 存储形态。装了 cryptography 存 'enc:v1:<密文>',否则存 'plain:<明文>' 并告警。
    带前缀是为了让读侧无歧义,也让运维一眼看出哪些记录还没加密。"""
    if not plain: return ""
    f = _fernet()
    if f is None:
        _LOG.warning("未安装 cryptography,外部库密码将以明文落盘(文件权限 0600)。"
                     "建议 pip install cryptography 后在界面重存一次凭据以启用静态加密。")
        return "plain:" + plain
    return "enc:v1:" + f.encrypt(plain.encode()).decode()

def _dec_secret(stored):
    """存储形态 → 明文。兼容三种:enc:v1: 密文、plain: 明文、以及历史遗留的裸明文。"""
    s = str(stored or "")
    if s.startswith("enc:v1:"):
        f = _fernet()
        if f is None:
            _LOG.error("凭据为密文但 cryptography 不可用,无法解密;请安装后重试")
            return ""
        try:
            return f.decrypt(s[7:].encode()).decode()
        except Exception:
            _LOG.error("凭据解密失败(主密钥变更或文件损坏),请在界面重新填写")
            return ""
    if s.startswith("plain:"): return s[6:]
    return s                                       # 旧版本写下的裸明文,读得到但下次保存即升级为密文

def _conn_secret(cid):
    """→ {user, password}(password 已解密)。仅供发起连接时内部使用,永不回显给前端。"""
    try:
        rec = (_load_json(_CONN_SECRETS_F) or {}).get(cid) or {}
    except Exception:
        return {}
    if not rec: return {}
    return {"user": rec.get("user") or "", "password": _dec_secret(rec.get("password"))}

def _save_conn_secret(cid, user, password):
    with _WRITE_LOCK:
        d = _load_json(_CONN_SECRETS_F) or {}
        if user or password:
            d[cid] = {"user": (user or "")[:60], "password": _enc_secret((password or "")[:120])}
        else:
            d.pop(cid, None)
        _atomic_json(_CONN_SECRETS_F, d)
        try: os.chmod(_CONN_SECRETS_F, 0o600)
        except Exception: pass

def _migrate_conn_secrets():
    """把加固前存下的明文口令就地升级为密文(启动时跑一次)。

    只在读侧兼容明文是不够的:那意味着老部署的密码会一直明文躺在盘上,而本次整改
    要消除的正是这一条。故启动即改写 —— 内容与语义不变,只换存储形态,失败不影响启动。
    """
    if _fernet() is None: return                     # 没有加密后端,维持现状(已有告警)
    try:
        d = _load_json(_CONN_SECRETS_F)
    except Exception:
        return
    if not isinstance(d, dict) or not d: return
    changed = False
    for cid, rec in list(d.items()):
        if not isinstance(rec, dict): continue
        pw = rec.get("password") or ""
        if pw and not str(pw).startswith("enc:v1:"):
            d[cid] = {**rec, "password": _enc_secret(_dec_secret(pw))}
            changed = True
    if not changed: return
    try:
        with _WRITE_LOCK:
            _atomic_json(_CONN_SECRETS_F, d)
            try: os.chmod(_CONN_SECRETS_F, 0o600)
            except OSError: pass
        _LOG.info("已将 %s 中的明文连接口令升级为静态加密存储", os.path.basename(_CONN_SECRETS_F))
    except Exception:
        _LOG.warning("连接凭据加密升级失败,原文件未改动;下次保存凭据时会再试")

_migrate_conn_secrets()

def _parse_dsn(dsn):
    """jdbc:mysql://host:port/db 或 mysql://user:pass@host/db → 部件字典"""
    from urllib.parse import urlsplit
    s = re.sub(r"^jdbc:", "", (dsn or "").strip())
    u = urlsplit(s if "://" in s else "//" + s)
    return {"scheme": (u.scheme or "").lower(), "host": u.hostname or "", "port": u.port,
            "db": (u.path or "/").lstrip("/"), "user": u.username or "", "password": u.password or ""}

def _jsonable_rows(cols, rows):
    """外部驱动返回 Decimal/datetime/bytes → JSON 可序列化(str 兜底)"""
    import decimal, datetime as _dt
    def cv(v):
        if isinstance(v, decimal.Decimal): return float(v)
        if isinstance(v, (_dt.date, _dt.datetime, _dt.time)): return str(v)
        if isinstance(v, (bytes, bytearray)): return v.decode("utf-8", "replace")
        return v
    return [{c: cv(r[i]) for i, c in enumerate(cols)} for r in rows]

def _ext_kind(conn):
    k = (conn.get("kind") or "").lower()
    if k in ("external",):                       # 自定义 DSN:按 scheme 判型
        k = _parse_dsn(conn.get("dsn"))["scheme"] or "external"
    return {"postgresql": "postgres", "pg": "postgres"}.get(k, k)

def _single_statement(sql):
    """拒绝堆叠语句:只允许一条 SQL(末尾分号可有)。

    sqlite3 的 execute 本就只跑一条,但 pymysql/psycopg2 在部分配置下会执行多语句 ——
    "SELECT 1; DROP TABLE t" 这种堆叠能整个绕过 sql_is_readonly(它只看开头)。
    这里按引号感知地扫一遍:字符串字面量内的分号不算分隔符,语句间的分号则拦下。
    """
    s, i, n = str(sql or ""), 0, len(str(sql or ""))
    quote = None
    while i < n:
        ch = s[i]
        if quote:
            if ch == quote:
                if i + 1 < n and s[i + 1] == quote: i += 1      # 成对转义的引号,仍在字面量内
                else: quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == ";":
            if s[i + 1:].strip():                                # 分号后还有内容 → 堆叠
                return False
        i += 1
    return True

def _ext_query(conn, sql, limit=500):
    """外部库真查询:只读放行 SELECT/WITH;驱动未装/不可达给明确报错(不静默)。→ {columns, rows}"""
    if not sql_is_readonly(sql): raise ValueError("仅允许只读 SELECT/WITH 查询")
    if not _single_statement(sql): raise ValueError("仅允许单条查询语句(检测到堆叠 SQL)")
    kind = _ext_kind(conn)
    u = _parse_dsn(conn.get("dsn"))
    sec = _conn_secret(conn.get("id"))
    user = sec.get("user") or u["user"] or "root"
    pwd = sec.get("password") or u["password"] or ""
    if kind in ("mysql", "doris"):
        try: import pymysql  # type: ignore[import-untyped]
        except ImportError: raise RuntimeError("未安装 MySQL 驱动:pip install pymysql 后重启服务") from None
        con = pymysql.connect(host=u["host"], port=int(u["port"] or (9030 if kind == "doris" else 3306)),
                              user=user, password=pwd, database=u["db"] or None,
                              connect_timeout=6, read_timeout=20, charset="utf8mb4")
        try:
            cur = con.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description or []]
            return {"columns": cols, "rows": _jsonable_rows(cols, cur.fetchmany(limit))}
        finally:
            con.close()
    if kind == "postgres":
        try: import psycopg2
        except ImportError: raise RuntimeError("未安装 PostgreSQL 驱动:pip install psycopg2-binary 后重启服务") from None
        con = psycopg2.connect(host=u["host"], port=int(u["port"] or 5432), user=user,
                               password=pwd, dbname=u["db"] or "postgres", connect_timeout=6)
        try:
            con.set_session(readonly=True)       # 会话级只读,双保险
            cur = con.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description or []]
            return {"columns": cols, "rows": _jsonable_rows(cols, cur.fetchmany(limit))}
        finally:
            con.close()
    if kind == "hive":
        raise RuntimeError("Hive 实连需 pyhive/thrift 与内网通道,当前经 hive-client 命令行侧通;HTTP 取数暂未接")
    raise RuntimeError(f"不支持的连接类型: {kind}")

def _ext_tables(conn):
    """外部库表清单(表名/行数估计/列数),经 information_schema/pg_stat 一次取回"""
    kind = _ext_kind(conn)
    if kind in ("mysql", "doris"):
        d = _ext_query(conn, "SELECT t.table_name AS name, COALESCE(t.table_rows,0) AS rows_, "
                             "(SELECT COUNT(*) FROM information_schema.columns c "
                             " WHERE c.table_schema=t.table_schema AND c.table_name=t.table_name) AS cols_ "
                             "FROM information_schema.tables t WHERE t.table_schema=DATABASE() ORDER BY 1", limit=500)
        return [{"name": r["name"], "rows": int(r["rows_"] or 0), "cols": int(r["cols_"] or 0)} for r in d["rows"]]
    if kind == "postgres":
        d = _ext_query(conn, "SELECT relname AS name, n_live_tup AS rows_, "
                             "(SELECT COUNT(*) FROM information_schema.columns c WHERE c.table_name=s.relname) AS cols_ "
                             "FROM pg_stat_user_tables s ORDER BY 1", limit=500)
        return [{"name": r["name"], "rows": int(r["rows_"] or 0), "cols": int(r["cols_"] or 0)} for r in d["rows"]]
    raise RuntimeError(f"该类型不支持表清单: {kind}")

def _find_conn(src):
    return next((c for c in _load_conns() if c.get("id") == src), None)

def _resolve_src(src):
    """数据源 id → (sqlite 路径, 名称);外部库/离线返回 (None, 名称)"""
    if src == "demo": return DB, "示例主库"
    if src == "uploads": return UPLOAD_DB, "上传数据库"
    for c in _load_conns():
        if c.get("id") == src:
            if c.get("kind") == "sqlite" and c.get("path") and os.path.exists(c["path"]):
                return c["path"], c.get("name")
            return None, c.get("name")
    return None, src

@app.get("/api/conn/tables")
def conn_tables():
    """浏览某数据源的表清单(表名/行数/列数)"""
    src = request.args.get("src", "demo")
    db, nm = _resolve_src(src)
    if not db:
        conn = _find_conn(src)
        if conn and conn.get("kind") not in ("sqlite", "api"):    # C7 外部库:真连取表清单
            try:
                return jsonify({"source": nm, "live": True, "tables": _ext_tables(conn)})
            except Exception as e:
                return jsonify({"source": nm, "error": f"外部库连接失败:{str(e)[:140]}", "tables": []})
        return jsonify({"source": nm, "error": f"「{nm}」不可浏览(外部库离线,需内网与驱动)", "tables": []})
    out = []
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for (t,) in c.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            try: n = c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except Exception: n = 0
            try: cols = len(c.execute(f'PRAGMA table_info("{t}")').fetchall())
            except Exception: cols = 0
            out.append({"name": t, "rows": n, "cols": cols})
        c.close()
    except Exception as e:
        return jsonify({"source": nm, "error": str(e)[:120], "tables": []})
    return jsonify({"source": nm, "tables": out})

@app.get("/api/conn/preview")
def conn_preview():
    """预览某数据源某表前 100 行"""
    src = request.args.get("src", "demo"); table = request.args.get("table", "")
    # 表名进的是标识符位,只能白名单;长度也限住,避免超长串灌进外部库
    if not re.match(r"^[A-Za-z0-9_]{1,64}$", table): return jsonify({"error": "非法表名"}), 400
    db, nm = _resolve_src(src)
    if not db:
        conn = _find_conn(src)
        if conn and conn.get("kind") not in ("sqlite", "api"):    # C7 外部库:真连预览
            try:
                # 按方言加标识符引号(MySQL/Doris 用反引号——默认配置下双引号是字符串字面量,
                # 一律用 " 会让预览直接语法错)。表名已过 ^[A-Za-z0-9_]{1,64}$,引号内不可能出现
                # 引号字符,故这里是纯粹的边界加固,不引入新的转义问题。
                qt = "`" if _ext_kind(conn) in ("mysql", "doris") else '"'
                return jsonify({"live": True, **_ext_query(conn, f"SELECT * FROM {qt}{table}{qt} LIMIT 100", limit=100)})
            except Exception as e:
                return jsonify({"error": f"外部库预览失败:{str(e)[:140]}"})
        return jsonify({"error": f"「{nm}」不可预览(外部库离线)"})
    try:
        return jsonify(q(f'SELECT * FROM "{table}" LIMIT 100', db=db))
    except Exception as e:
        return jsonify({"error": str(e)[:120]})

@app.post("/api/viz/run")
def viz_run():
    """可视化取数:在指定数据源上执行只读 SELECT/WITH,返回列与行(供前端 ECharts 出图)"""
    body = request.json or {}
    src = body.get("src", "demo"); sql = (body.get("sql") or "").strip()
    if not sql: return jsonify({"error": "请输入查询 SQL"}), 400
    if not sql_is_readonly(sql): return jsonify({"error": "仅允许只读 SELECT/WITH 查询"}), 400
    # 本端点的 SQL 由使用者直接给出(SQL 工作台/看板取数是产品能力,不是注入),
    # 因此防线不在"过滤参数"而在"限制能力":只读 + 单语句 + 只读连接三重约束。
    if not _single_statement(sql): return jsonify({"error": "仅允许单条查询语句"}), 400
    db, nm = _resolve_src(src)
    if not db:
        conn = _find_conn(src)
        if conn and conn.get("kind") not in ("sqlite", "api"):    # C7 外部库:真连取数
            try:
                return jsonify({"source": nm, "live": True, **_ext_query(conn, sql)})
            except Exception as e:
                return jsonify({"error": f"外部库取数失败:{str(e)[:160]}"})
        return jsonify({"error": f"「{nm}」不可取数(外部库离线,需内网与驱动)"})
    try:
        data = q(sql, db=db)
        return jsonify({"source": nm, **data})
    except Exception as e:
        return jsonify({"error": str(e)[:160]})

_VIZ_BOARDS_F = os.path.join(WORK, "viz_boards.json")
def _load_boards():
    v = _load_json(_VIZ_BOARDS_F)
    return v if isinstance(v, list) else []

@app.get("/api/viz/boards")
def viz_boards():
    return jsonify(_load_boards())

@app.post("/api/viz/save")
def viz_save():
    """保存/更新数据看板(一组图表卡片规格)"""
    body = request.json or {}
    name = (body.get("name") or "").strip()
    charts = body.get("charts") or []
    if not name: return jsonify({"error": "需要看板名称"}), 400
    if not isinstance(charts, list) or not charts: return jsonify({"error": "看板至少含一张图表"}), 400
    bid = body.get("id") or ("board_" + uuid.uuid4().hex[:6])
    board = {"id": bid, "name": name, "charts": charts[:24], "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    with _WRITE_LOCK:
        boards = [b for b in _load_boards() if b.get("id") != bid]
        boards.insert(0, board); _atomic_json(_VIZ_BOARDS_F, boards[:50])
    return jsonify({"ok": True, "id": bid})

@app.post("/api/viz/delete")
def viz_delete():
    bid = (request.json or {}).get("id")
    with _WRITE_LOCK:
        _atomic_json(_VIZ_BOARDS_F, [b for b in _load_boards() if b.get("id") != bid])
    return jsonify({"ok": True})

@app.get("/api/skills")
def skills():
    out = [{"name": item["name"],
            "desc": (_BUILD_SKILL_DESC.get(item["name"]) or item["description"])[:140],
            "runnable": item["runnable"]}
           for item in _builtin_skill_entries()]
    return jsonify(out)

@app.post("/api/skill/run")
def skill_run():
    body = request.json or {}
    name = body.get("name", ""); args = body.get("args", "")
    if not re.match(r"^[\w\-]+$", name): return jsonify({"error": "非法技能名"}), 400   # 防穿越:与同族端点一致
    d = _builtin_skill_dir(name)
    if not d or not os.path.exists(os.path.join(d, "run.sh")): return jsonify({"error": "该技能无 run.sh"}), 400
    # 参数改用**白名单**:此前的黑名单(;&|`$)漏掉了换行、\、引号、> < 等,而 run.sh 内部
    # 若把参数二次求值(eval/未加引号展开),这些字符同样能拼出命令。参数本就只用于传
    # 标识/路径片段,限成字母数字与 _-./=:, 足够,且把注入面收敛到可枚举的集合。
    if args and not re.match(r"^[\w\-./=:,\s]*$", args):
        return jsonify({"error": "参数含非法字符(仅允许字母数字与 _-./=:, 及空格)"}), 400
    jid = run_job(["bash", os.path.join(d, "run.sh")] + args.split(), cwd=d, tag=f"skill:{name}",
                  env={"GOV_TOKEN": os.environ.get("GOV_TOKEN", "")})
    return jsonify({"job": jid})

@app.get("/api/jobs")
def jobs(): return jsonify(sorted(JOBS.values(), key=lambda j: -j["ts"])[:20])

@app.get("/api/job/<jid>")
def job(jid):
    j = JOBS.get(jid)
    if not j: return jsonify({"error": "no job"}), 404
    tail = ""
    if os.path.exists(j["log"]):
        tail = open(j["log"], errors="replace").read()[-4000:]
    return jsonify({**j, "tail": tail})

@app.get("/api/outputs")
def outputs():
    out = []
    for base, label in ((OUTPUTS, "outputs"),):
        if not os.path.isdir(base): continue
        for p in sorted(glob.glob(os.path.join(base, "**", "*"), recursive=True)):
            if os.path.isfile(p) and os.path.getsize(p) < 20_000_000:
                # 只回相对名:绝对路径会把部署目录结构泄露给前端,而下载只需要 name
                # (/api/outputs/file 已改为按 name 在成果库内解析)。
                out.append({"group": label, "name": os.path.relpath(p, base), "kb": round(os.path.getsize(p) / 1024, 1)})
    return jsonify(out[:400])

@app.get("/api/outputs/file")
def outputs_file():
    """下载成果库文件。入参是**成果库内的相对路径**,不是任意绝对路径。"""
    p = request.args.get("p", "")
    allowed = [OUTPUTS]
    # 相对路径按成果库根解析;绝对路径仍接受(兼容旧前端),但一律要落在白名单目录内。
    rp = os.path.realpath(p if os.path.isabs(p) else os.path.join(OUTPUTS, p))
    if not any(rp.startswith(os.path.realpath(a) + os.sep) for a in allowed): return "forbidden", 403
    return send_file(rp)

if __name__ == "__main__":
    # 监听地址/端口只有一个事实源(LISTEN_HOST/LISTEN_PORT),启动横幅照它打印,
    # 不再另写一份字面量 —— 此前改了 DATAMIND_HOST 却仍提示 127.0.0.1:8092,是误导。
    _LOG.info("COSMO DataMind → http://%s:%d", LISTEN_HOST, LISTEN_PORT)
    # DR-048:内置服务器默认不设 socket 超时、线程无上限,只发半截请求头即可长期占用连接
    # (2026-08-14 扫描实测 186s,CVE-2007-6750)。启动时装上超时与并发上限后再监听。
    import srv_hardening
    _LOG.info("%s", srv_hardening.describe())
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, debug=False,
            request_handler=srv_hardening.build_handler())
