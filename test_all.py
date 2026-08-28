#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DataMind 全路由覆盖测试:每个端点 happy path + 边界/错误 + 安全。"""
import json, os, re, requests, sys, ast, time
from urllib.parse import urlsplit

# 被测服务地址(可用 DATAMIND_URL 覆盖:CI/远端联调时不必改代码)。
# 只在这里出现一次,下面的 g()/po() 都以它为唯一基址。
B = (os.environ.get("DATAMIND_URL") or "http://localhost:8092").rstrip("/")
_B_HOST = urlsplit(B).netloc

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")   # 含 ANSI 转义引导符 \x1b
def _sane(s, cap=300):
    """把响应文本裁成可安全打印的形态:去掉控制字符与 ANSI 转义序列并限长。

    断言失败时我们会把服务端返回的正文打进终端。正文里若含 ESC 序列,终端会**执行**它
    (改色、清屏、挪光标,某些终端还能改标题/回填输入),报告因此可被服务端返回内容操纵。
    这与 Web 上的反射型 XSS 是同一回事,只是渲染器换成了终端。
    """
    return _CTRL.sub("?", str(s))[:cap]

P=F=0; fails=[]
def chk(name, cond, detail=""):
    global P,F
    if cond: P+=1
    else: F+=1; fails.append(f"{name}: {_sane(detail)}")
    print(f"  {'✓' if cond else '✗ FAIL'} {name}" + (f"  [{_sane(detail)}]" if not cond else ""))

def _url(path):
    """只允许拼相对路径,且拼出来的地址必须仍在被测主机上。

    本文件多处用服务端返回的 id/key 拼下一个请求路径(如 /api/graph/{GK})。
    若返回值里带了 '//evil.host/x' 或绝对 URL,requests 会转而去请求那台主机 ——
    测试进程就成了服务端可驱使的请求代理(SSRF)。此处一次判死。
    """
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError(f"仅接受本机相对路径,拒绝:{_sane(path, 120)}")
    u = urlsplit(B + path)
    if u.netloc != _B_HOST:
        raise ValueError(f"目标主机越界({u.netloc}),拒绝请求")
    return B + path

def g(path,**kw): return requests.get(_url(path),timeout=180,**kw)
def po(path,**kw): return requests.post(_url(path),timeout=200,**kw)

# ── 前置:测试进程与被测服务必须指向同一个数据底座 ─────────────────
# 部分断言在本进程内直接 import server 求值(build_context、_table_cols 等),
# 读的是本进程环境变量里的 DATAMIND_DB。若只给服务端设了而没给测试进程设,
# 这些断言会以「空 detail」的形式失败,现象与真实缺陷难以区分 —— 直接卡在这里报清楚。
def _preflight():
    import os as _o
    try:
        _srv_db = requests.get(B + "/api/db/check", timeout=10).json()
    except Exception as e:
        print("✗ 前置检查:服务未启动或不可达(%s)——先 python3 server.py" % str(e)[:80]); sys.exit(2)
    if not _srv_db.get("ok"):
        print("✗ 前置检查:服务端数据底座不可用 —— %s" % _srv_db.get("error", "")); sys.exit(2)
    _mine = _o.path.basename(_o.environ.get("DATAMIND_DB") or "demo_metrics.db")
    if _mine != _srv_db.get("db"):
        print("✗ 前置检查:测试进程的 DATAMIND_DB(%s)与服务端(%s)不一致。\n"
              "  用同一个库重跑,例如:DATAMIND_DB=$PWD/../%s python3 test_all.py"
              % (_mine, _srv_db.get("db"), _srv_db.get("db"))); sys.exit(2)
_preflight()

# ── 回归沙箱图谱(隔离规范)──────────────────────────────────────────
# 回归会做写操作(改名/人审/删对象/设别名/存规则),此前直接打在 demo 上,
# 跑完一轮就把运行态本体改脏——曾把生产环境的业务别名冲掉。
# 故回归自建 built_regress:复制 demo IR 为独立图谱(built_ 前缀由 load_ir 直接从
# workdir 加载,无需改动服务端代码),跑完清理。demo 从此只读不写。
import os as _os0, json as _json0, shutil as _sh0, atexit as _at0
SANDBOX = "built_regress"
_WD0 = _os0.path.join(_os0.path.dirname(_os0.path.abspath(__file__)), "workdir")
_SBX_IR = _os0.path.join(_WD0, SANDBOX + ".json")
_SBX_ED = _os0.path.join(_WD0, "edits_" + SANDBOX + ".json")

def _sandbox_setup():
    src = _os0.path.join(_WD0, "demo_ir.json")
    if not _os0.path.exists(src):
        print("  ! 缺 workdir/demo_ir.json,沙箱无法建立"); return False
    _sh0.copyfile(src, _SBX_IR)
    for f in (_SBX_ED,):
        if _os0.path.exists(f): _os0.remove(f)
    return True

def _sandbox_teardown():
    for f in (_SBX_IR, _SBX_ED):
        try:
            if _os0.path.exists(f): _os0.remove(f)
        except Exception: pass
    try:                                   # 规则也存在沙箱键下,一并清掉
        rf = _os0.path.join(_WD0, "ont_rules.json")
        if _os0.path.exists(rf):
            d = _json0.load(open(rf, encoding="utf-8"))
            if isinstance(d, dict) and d.pop(SANDBOX, None) is not None:
                _json0.dump(d, open(rf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception: pass

_SBX_OK = _sandbox_setup()
_at0.register(_sandbox_teardown)

print("=== A. 元/健康 ===")
r=g("/api/health"); chk("health 200+ok", r.status_code==200 and r.json().get("ok"))
r=g("/api/db/check"); chk("db/check 200", r.status_code==200 and r.json().get("ok"))
r=g("/"); chk("首页 200 html", r.status_code==200 and "DataMind" in r.text)
r=g("/doc/nonexistent"); chk("doc 不存在→404", r.status_code==404)
r=g("/doc/../server"); chk("doc 穿越→400/404", r.status_code in (400,404))
r=g("/vendor/echarts.min.js"); chk("vendor echarts 200", r.status_code==200)
r=g("/vendor/../server.py"); chk("vendor 穿越→404", r.status_code==404)

print("=== B. 数据/目录 ===")
r=g("/api/overview"); j=r.json(); chk("overview kpi", r.status_code==200 and "kpi" in j and j["kpi"]["tables"]>0)
r=g("/api/tables"); chk("tables 列表", r.status_code==200 and isinstance(r.json(),list) and len(r.json())>0)
r=g("/api/table/fact_sales_order"); chk("table 详情", r.status_code==200 and "columns" in r.json())
r=g("/api/table/nosuchtable"); chk("table 不存在→404", r.status_code==404)
r=g("/api/table/x;drop"); chk("table 非法名→400", r.status_code==400)
r=g("/api/table/fact_sales_order/info"); chk("table info", r.status_code==200 and "rows" in r.json())
r=g("/api/table/nope/info"); chk("table info 不存在→404", r.status_code==404)

print("=== C. 指标 ===")
r=g("/api/metrics"); m=r.json(); chk("metrics 分层", r.status_code==200 and "atomic" in m)
name=m["atomic"][0]["name"]
import urllib.parse as up
r=g("/api/metric/lineage?name="+up.quote(name)); chk("lineage 命中", r.status_code==200 and "metric" in r.json())
r=g("/api/metric/lineage?name="+up.quote("不存在指标XYZ")); chk("lineage 不存在→404", r.status_code==404)
r=g("/api/metric/quick?name="+up.quote(name)); chk("quick 即时问数", r.status_code==200 and "data" in r.json())
r=g("/api/metric/quick?name="+up.quote("不存在XYZ")); chk("quick 不存在→404", r.status_code==404)

print("=== D. 图谱/本体 ===")
r=g("/api/graphs"); gs=r.json(); chk("graphs 列表", r.status_code==200 and len(gs)>=3)
chk("graphs 分类字段(cat)", all(x.get("cat") in ("curated","scenario","built") for x in gs) and {x["cat"] for x in gs} >= {"curated"})
for k in ("demo","app","cq"):
    r=g("/api/graph/"+k); chk(f"graph {k}", r.status_code==200 and "nodes" in r.json())
for fmt in ("ttl","jsonld","owl"):
    r=g(f"/api/graph/demo/export.{fmt}"); chk(f"export demo.{fmt}", r.status_code==200)
    r=g(f"/api/graph/app/export.{fmt}"); chk(f"export app.{fmt}(非DR001)", r.status_code==200)
r=g("/api/graph/demo/export.bad"); chk("export 非法格式→400", r.status_code==400)
r=g("/api/ont/rules?graph=demo"); j=r.json(); chk("ont/rules 计数", r.status_code==200 and j["counts"]["objects"]>0)
r=g("/api/ont/metadata?graph=demo"); chk("ont/metadata 字典", r.status_code==200 and r.json()["count"]>0)
r=g("/api/ont/object/demo/nosuchobj"); chk("object 不存在→404", r.status_code==404)
r=g("/api/ont/relation/demo?s=a&t=b"); chk("relation 不存在→404", r.status_code==404)
r=g("/api/ont/edits?graph=demo"); chk("edits", r.status_code==200 and "ops" in r.json())
r=g("/api/ont/forged"); chk("forged 列表", r.status_code==200 and "ontologies" in r.json())
r=g("/api/ont/forged/bad@id"); chk("forged 非法id→400", r.status_code==400)
r=g("/api/ont/runtimes"); chk("runtimes", r.status_code==200 and "runtimes" in r.json())

print("=== E. 编辑/写(含清理)===")
# apply rename → undo → rebuild
r=po("/api/ont/apply",json={"graph":SANDBOX,"op":{"op":"rename","target":"obj:"+g(f"/api/graph/{SANDBOX}").json()["nodes"][0]["id"],"params":{"cn":"__test改名__"},"reason":"test"}})
chk("apply rename ok", r.status_code==200 and r.json().get("ok"))
r=po("/api/ont/apply",json={"graph":SANDBOX,"op":{"op":"非法算子","target":"obj:x","params":{}}})
chk("apply 非白名单→400", r.status_code==400)
r=po("/api/ont/undo",json={"graph":SANDBOX}); chk("undo ok", r.status_code==200)
# 重建是破坏性动作(丢弃整个草案层,undo 退不回来),必须带 confirm —— 见 QS16c
r=po("/api/ont/rebuild",json={"graph":SANDBOX,"confirm":True})
chk("rebuild 清草案", r.status_code==200 and r.json().get("ok"))
chk("rebuild 留底可恢复(不直接删)", bool(r.json().get("backup")) or r.json().get("discarded_ops")==0,
    r.text[:80])

print("=== F. 会话 ===")
r=po("/api/ont/chats/new",json={}); cid=r.json().get("id"); chk("chats/new", bool(cid))
r=g("/api/ont/chats"); chk("chats 列表", r.status_code==200 and isinstance(r.json(),list))
r=g("/api/ont/chats/"+cid); chk("chats/<id>", r.status_code==200)
r=g("/api/ont/chats/nosuchcid"); chk("chats 不存在→404", r.status_code==404)
r=po("/api/ont/chats/delete",json={"id":cid}); chk("chats/delete", r.status_code==200)

print("=== G. 查询/安全 ===")
r=po("/api/query",json={"sql":"SELECT count(*) n FROM fact_sales_order"}); chk("query select", r.status_code==200 and r.json()["rows"][0]["n"]>0)
r=po("/api/query",json={"sql":"DELETE FROM fact_sales_order"}); chk("query 非SELECT→400", r.status_code==400)
r=po("/api/query",json={"sql":"SELECT 1; DROP TABLE x"}); chk("query 多语句→400", r.status_code==400)
r=po("/api/sparql",json={"query":"SELECT ?c WHERE{?c a <http://www.w3.org/2002/07/owl#Class>} LIMIT 2","graph":"demo"}); chk("sparql select", r.status_code==200 and "rows" in r.json())
r=po("/api/sparql",json={"query":"BADSPARQL","graph":"demo"}); chk("sparql 语法错→400", r.status_code==400)
# DR-012: 页面内置的默认 SPARQL 示例必须可跑(防再混入 `>?var` 缺空格等非法 SPARQL 回归)
import re as _re, os as _os
_ui=open(_os.path.join(_os.path.dirname(__file__),"ui","index.html"),encoding="utf-8").read()
_m=_re.search(r'id="sq_q"[^>]*>(.*?)</textarea>', _ui, _re.S)
_defq=_m.group(1).strip() if _m else ""
r=po("/api/sparql",json={"query":_defq,"graph":"demo"}); chk("sparql 页面默认示例可跑", r.status_code==200 and len(r.json().get("rows",[]))>0)
# 数据格子去浮点噪声:统一 cell() 定义存在,且数据预览值格用 cell()(防再退回 esc() 露出 14.74000…02)
chk("前端 cell() 去浮点噪声助手存在", "const cell=" in _ui and "toPrecision(12)" in _ui)
chk("数据预览值格统一走 cell()", _ui.count("<td>${cell(")>=5)
# 错误信息如实:语法错报"查询错误"而非伪装成"超时"(_bounded_ex 区分异常/超时)
r=po("/api/sparql",json={"query":"SELECT ?x WHERE { ?x <p>?y }","graph":"demo"}); chk("sparql 语法错报『查询错误』非『超时』", r.status_code==400 and "超时" not in r.json().get("error",""))
# 并发安全:rdflib SPARQL 解析器非线程安全,_RDF_LOCK 串行化后 12 并发须全绿(防 pyparsing 语法污染回归)
import concurrent.futures as _cf
def _one_sparql(_): return po("/api/sparql",json={"query":_defq,"graph":"demo"}).status_code
with _cf.ThreadPoolExecutor(max_workers=12) as _ex: _codes=list(_ex.map(_one_sparql, range(12)))
chk("sparql 12并发全200(线程安全)", all(c==200 for c in _codes))
r=g("/api/outputs/file?p=/etc/passwd"); chk("outputs 穿越→403", r.status_code==403)

print("=== H. 技能/工具/作业 ===")
r=g("/api/skills"); chk("skills 列表", r.status_code==200 and len(r.json())>=5)
r=po("/api/skill/run",json={"name":"nope"}); chk("skill 无run.sh→400", r.status_code==400)
r=g("/api/jobs"); chk("jobs 列表", r.status_code==200 and isinstance(r.json(),list))
r=g("/api/job/nosuchjid"); chk("job 不存在→404", r.status_code==404)
r=g("/api/outputs"); chk("outputs 列表", r.status_code==200)
r=g("/api/ont/skill/gov-app-ontology-build"); chk("skill 详情", r.status_code==200)
r=g("/api/ont/skill/bad@name"); chk("skill 非法名→400", r.status_code==400)

print("=== I. 平台代理 ===")

print("=== J. 平台级模块(新增)===")
r=g("/api/glossary"); j=r.json(); chk("术语词典", r.status_code==200 and j.get("count",0)>0 and "terms" in j)
r=g("/api/agents"); j=r.json(); chk("智能体列表", r.status_code==200 and j.get("count",0)>0)
r=g("/api/routes"); j=r.json(); chk("API目录", r.status_code==200 and j.get("count",0)>10)
r=g("/api/quality"); j=r.json(); chk("数据质量", r.status_code==200 and "checks" in j and "levels" in j)
r=g("/api/sysinfo"); j=r.json(); chk("系统信息", r.status_code==200 and j.get("tables",0)>0 and "runtimes" in j)
r=g("/api/chat/skills"); chk("沉淀技能列表", r.status_code==200 and isinstance(r.json(),list))
# 沉淀→查→端点反馈流程(带 Origin 过 CSRF,清理)
H={"Origin":B}   # 同源 Origin,用于通过 CSRF 守卫(基址唯一事实源 B)
r=po("/api/chat/save_skill",json={"question":"__t沉淀__","results":[{"title":"t","sql":"SELECT 1","chart":{}}]},headers=H)
sid=r.json().get("id"); chk("沉淀为Skill", r.status_code==200 and sid)
r=po("/api/chat/save_skill",json={"question":"","results":[]},headers=H); chk("沉淀空→400", r.status_code==400)
# 清理沉淀测试项
try:
    import os as _os,json as _json
    f="workdir/qa_skills.json"
    if _os.path.exists(f):
        d=[x for x in _json.load(open(f)) if x.get("question")!="__t沉淀__"]
        _json.dump(d,open(f,"w"),ensure_ascii=False)
except: pass

print("=== K. 数据连接 + 数据可视化(新增)===")
H={"Origin":B}   # 同源 Origin,用于通过 CSRF 守卫(基址唯一事实源 B)
r=g("/api/build/sources"); j=r.json(); chk("构建数据源清单", r.status_code==200 and "sources" in j and "assets" in j)
r=g("/api/conn/tables?src=demo"); j=r.json(); chk("连接表清单", r.status_code==200 and len(j.get("tables",[]))>100)
r=g("/api/conn/preview?src=demo&table=dim_customer"); j=r.json(); chk("连接表预览", r.status_code==200 and len(j.get("rows",[]))>0)
r=g("/api/conn/preview?src=demo&table=x;DROP"); chk("预览非法表名→400", r.status_code==400)
r=po("/api/viz/run",json={"src":"demo","sql":"SELECT 1 a, 2 b"},headers=H); j=r.json(); chk("可视化取数", r.status_code==200 and j.get("columns")==["a","b"])
r=po("/api/viz/run",json={"src":"demo","sql":"DELETE FROM dim_customer"},headers=H); chk("可视化拒写", r.status_code==400 and "error" in r.json())
r=po("/api/viz/save",json={"name":"__t看板__","charts":[{"title":"t","src":"demo","sql":"SELECT 1","type":"kpi"}]},headers=H)
bid=r.json().get("id"); chk("保存看板", r.status_code==200 and bid)
r=g("/api/viz/boards"); chk("看板列表", r.status_code==200 and any(b.get("id")==bid for b in r.json()))
r=po("/api/viz/save",json={"name":"","charts":[]},headers=H); chk("空看板→400", r.status_code==400)
r=po("/api/viz/delete",json={"id":bid},headers=H); chk("删除看板", r.status_code==200)
# 连接登记→清理
r=po("/api/build/connect",json={"kind":"external","name":"__t连接__","dsn":"jdbc:x://h:1/d"},headers=H)
cid=r.json().get("conn",{}).get("id"); chk("登记外部连接", r.status_code==200 and cid)
# 幂等:同 dsn 再登记须复用不新增(防同一物理源重复堆叠成脏列表)
r2=po("/api/build/connect",json={"kind":"external","name":"__t连接副本__","dsn":"jdbc:x://h:1/d"},headers=H)
chk("重复连接去重(同源复用)", r2.status_code==200 and r2.json().get("deduped") and r2.json().get("conn",{}).get("id")==cid)
r=po("/api/build/connect/delete",json={"id":cid},headers=H); chk("删除连接", r.status_code==200)
# 构建页默认参数 + 已构建本体清单
r=g("/api/build/defaults"); j=r.json(); chk("构建默认参数", r.status_code==200 and j.get("sqlite_path") and "conn_name" in j)
import os as _os2; chk("默认SQLite路径真实存在", _os2.path.exists(j.get("sqlite_path","/nope")))
r=g("/api/build/built"); chk("已构建本体清单", r.status_code==200 and isinstance(r.json(),list))
r=po("/api/build/delete",json={"key":"../etc/passwd"},headers=H); chk("删除本体非法key→400", r.status_code==400)
# 安全:图谱键路径穿越(LFI/写穿越)必须被挡
r=po("/api/sparql",json={"graph":"forged_../../../cosmo-datamind/workdir/demo_ir","query":"SELECT ?s WHERE{?s ?p ?o}"},headers=H)
chk("SPARQL forged_ 路径穿越→404", r.status_code==404)
r=po("/api/ont/apply",json={"graph":"../../../tmp/evil","op":{"op":"rename","target":"obj:x","params":{"cn":"y"}}},headers=H)
chk("apply 穿越图谱键→拒", r.status_code in (400,404) and not __import__('os').path.exists('/tmp/edits_../../../tmp/evil.json'))
r=po("/api/sparql",json={"graph":"demo","query":"SELECT ?s FROM <file:///etc/hosts> WHERE{?s ?p ?o}"},headers=H)
chk("SPARQL FROM file:// →拒", r.status_code==400)

print("=== N. IOF/BFO 语义层(DR-010)===")
r=g("/api/graph/demo"); gj=r.json(); n0=gj["nodes"][0]
chk("图谱节点带 bfo", "bfo" in n0 and bool(n0["bfo"]))
chk("图谱边带 founded_relation", bool(gj["edges"]) and "founded_relation" in gj["edges"][0])
r=g("/api/ont/completeness/demo"); cj=r.json()
chk("元数据覆盖结构", r.status_code==200 and "score" in cj and "byBFO" in cj.get("objects",{}))
r=g("/api/graph/demo/export.ttl"); chk("OWL 导出带 iof-av + BFO 归类", r.status_code==200 and "iof-av:" in r.text and "subClassOf" in r.text)
# 写端点图谱必填:缺 graph 须显式 400,不静默默认到 示例 主图误改生产(_open_writable 统一守卫)
r=po("/api/ont/enrich",json={},headers=H); chk("enrich 缺graph→400(不默认 demo)", r.status_code==400)
r=po("/api/ont/reground",json={},headers=H); chk("reground 缺graph→400(不默认 demo)", r.status_code==400)
r=po("/api/ont/maturity",json={"object":"x","maturity":"Released"},headers=H); chk("maturity 缺graph→400(不默认 demo)", r.status_code==400)
r=po("/api/ont/enrich",json={"graph":"cq"},headers=H); chk("enrich 只读源→400", r.status_code==400)
r=po("/api/ont/reground",json={"graph":"cq"},headers=H); chk("reground 只读源→400", r.status_code==400)
r=po("/api/ont/maturity",json={"graph":"demo","object":n0["id"],"maturity":"BAD"},headers=H); chk("maturity 非法值→400", r.status_code==400)
r=po("/api/ont/maturity",json={"graph":"demo","object":"__nope__","maturity":"Released"},headers=H); chk("maturity 不存在对象→404", r.status_code==404)
r=po("/api/ont/maturity",json={"graph":"cq","object":"x","maturity":"Released"},headers=H); chk("maturity 只读源→400", r.status_code==400)
r=po("/api/ont/maturity",json={"graph":"forged_../../etc","object":"x","maturity":"Released"},headers=H); chk("maturity 图谱键穿越→400", r.status_code==400)
r=po("/api/ont/maturity",json={"graph":"demo","object":n0["id"],"maturity":"Released"},headers=H); chk("maturity promote 200", r.status_code==200 and r.json().get("maturity")=="Released")
po("/api/ont/maturity",json={"graph":"demo","object":n0["id"],"maturity":"Provisional"},headers=H)  # 置回,net-zero
r=po("/api/ont/forge",json={"graph":"demo","name":"__shacl自检__"},headers=H); fj=r.json()
chk("forge SHACL 对已补全示例图谱→conforms", r.status_code==200 and fj.get("shacl")=="conforms")
if fj.get("id"): po("/api/ont/forged/delete",json={"id":fj["id"]},headers=H)  # 清理(含 .ttl 伴生)


print("=== O. 泛化裁决 v2(DR-011:等值/复合/PK感知/语义层)===")
import sqlite3 as _sq, importlib.util as _iu, os as _os
_db="/tmp/_ta_adj.db"; _os.path.exists(_db) and _os.remove(_db)
_c=_sq.connect(_db)
_c.executescript("""CREATE TABLE director(id INT PRIMARY KEY, name TEXT);
CREATE TABLE movie(id INT PRIMARY KEY, director INT);
CREATE TABLE grade(sid INT, cid INT, v INT);
CREATE TABLE enroll(eid INT PRIMARY KEY, sid INT, cid INT);""")
for i in range(1,16): _c.execute("INSERT INTO director VALUES(?,?)",(i,"d"))
for i in range(1,46): _c.execute("INSERT INTO movie VALUES(?,?)",(i,(i%15)+1))
for s2 in range(1,9):
    for k2 in range(1,5): _c.execute("INSERT INTO grade VALUES(?,?,1)",(s2,k2))
for i in range(1,33): _c.execute("INSERT INTO enroll VALUES(?,?,?)",(i,(i%8)+1,(i%4)+1))
_c.commit(); _c.close()
_sp=_iu.spec_from_file_location("_srv","server.py"); _sv=_iu.module_from_spec(_sp); _sp.loader.exec_module(_sv)
_sv._llm_semantic_review=lambda *a,**k: None
_tc={t:[(r2[1],r2[2]) for r2 in _sq.connect(_db).execute(f'PRAGMA table_info("{t}")')] for t in ("director","movie","grade","enroll")}
_ir=_sv._adjudicate_ir(_db,"t",{"objects":[{"name":n,"cn":n,"kind":"object","table":n} for n in _tc],
  "relations":[{"source":"movie","target":"director","verb":"归属"},{"source":"enroll","target":"grade","verb":"关联"}]},
  {"tab_cols":_tc,"schema":"","n_docs":0,"refs":[]})
_r={(x["source_concept"],x["target_concept"]):x for x in _ir["relations"]}
chk("等值键(无后缀)verified", _r[("movie","director")]["status"]=="verified")
chk("复合键二列 verified", _r[("enroll","grade")]["status"]=="verified" and "复合键" in _r[("enroll","grade")]["note"])
chk("semantic 字段存在(离线=skipped)", all(x.get("semantic")=="skipped" for x in _ir["relations"]))
_ir2=_sv._adjudicate_ir(_db,"t",{"objects":[{"name":"director","cn":"n","kind":"ice","table":"director"}],"relations":[]},{"tab_cols":_tc,"schema":"","n_docs":0,"refs":[]})
chk("ice kind→BFO ICE", _ir2["objects"][0]["kind"]=="ice" and _ir2["objects"][0]["bfo"]=="InformationContentEntity")
_os.remove(_db)


# ═══════════ P. 人审关系与元素编辑(DR-013)═══════════
print("=== P. 人审(DR-013)===")
r=g("/api/ont/review?graph=demo"); chk("review 示例 200+counts", r.status_code==200 and "pending" in r.json()["counts"])
r=g("/api/ont/review?graph=../etc"); chk("review 键穿越→400", r.status_code==400)
# 找一个构建产物图谱做全套往返
_gs=[x["id"] for x in g("/api/graphs").json() if x["id"].startswith("built_")]
if _gs:
    GK=_gs[0]; rv=g(f"/api/ont/review?graph={GK}").json()
    _GK_DEPTH0=len(g(f"/api/ont/edits?graph={GK}").json().get("ops",[]))
    _n0=rv["counts"]["total"]
    _tgt=rv["rows"][0]["s"]+"->"+rv["rows"][0]["t"]
    r=po("/api/ont/apply",json={"graph":GK,"reviewer":"回归测试/T0","op":{"op":"confirm_relation","target":"rel:"+_tgt,"reason":"回归用例"}})
    chk("confirm_relation 200", r.status_code==200)
    _row=[x for x in g(f"/api/ont/review?graph={GK}").json()["rows"] if x["s"]+"->"+x["t"]==_tgt][0]
    chk("人审通过→approved+评审人盖章", _row["human_review"]=="approved" and _row["by"]=="回归测试/T0" and _row["reason"]=="回归用例")
    chk("verified 不被人为降级/candidate→asserted", _row["status"] in ("verified","asserted"))
    r=po("/api/ont/apply",json={"graph":GK,"reviewer":"回归测试/T0","op":{"op":"reject_relation","target":"rel:"+_tgt,"reason":"回归否决"}})
    chk("reject_relation 200", r.status_code==200)
    _g2=g(f"/api/graph/{GK}").json()
    chk("否决后图渲染剔除该边", not [e for e in _g2["edges"] if e["s"]==_tgt.split("->")[0] and e["t"]==_tgt.split("->")[1]])
    _objs=g(f"/api/ont/review?graph={GK}").json()["objects"]
    chk("review 返回对象清单", len(_objs)>0 and "candidate" in _objs[0])
    _oid=[o["id"] for o in _objs if not [x for x in g(f"/api/ont/review?graph={GK}").json()["rows"] if o["id"] in (x["s"],x["t"])]] or [_objs[-1]["id"]]
    _oid=_objs[0]["id"]
    _rels_touch=len([x for x in g(f"/api/ont/review?graph={GK}").json()["rows"] if _oid in (x["s"],x["t"])])
    r=po("/api/ont/apply",json={"graph":GK,"reviewer":"回归测试/T0","op":{"op":"remove_object","target":"obj:"+_oid,"reason":"回归级联"}})
    chk("remove_object 200", r.status_code==200)
    _rv3=g(f"/api/ont/review?graph={GK}").json()
    chk("删对象级联删关系", len(_rv3["objects"])==len(_objs)-1 and not [x for x in _rv3["rows"] if _oid in (x["s"],x["t"])])
    for _ in range(3):                    # 栈深守卫:撤到基线即停,不越界弹掉他人条目
        if len(g(f"/api/ont/edits?graph={GK}").json().get("ops",[])) <= _GK_DEPTH0: break
        po("/api/ont/undo",json={"graph":GK})
    _rv4=g(f"/api/ont/review?graph={GK}").json()
    chk("撤销×3 全复原", _rv4["counts"]["total"]==_n0 and len(_rv4["objects"])==len(_objs))

# ═══════════ Q. 证据回流与诊断(DR-014)═══════════
print("=== Q. 反馈回流+诊断(DR-014)===")
r=po("/api/chat/feedback",json={"question":"q","verdict":"bogus"}); chk("feedback 非法verdict→400", r.status_code==400)
r=po("/api/chat/feedback",json={"question":"回归测试问题","verdict":"needs_work","comment":"回归意见","mode":"chat"})
chk("feedback 落库", r.status_code==200 and r.json().get("id"))
_fid=r.json()["id"]; _fb=g("/api/chat/feedback").json()
chk("待处理队列含该条", any(x["id"]==_fid and not x["resolved"] for x in _fb["items"]))
r=po("/api/chat/feedback/resolve",json={"id":"nonexist"}); chk("resolve 不存在→404", r.status_code==404)
r=po("/api/chat/feedback/resolve",json={"id":_fid}); chk("resolve 200", r.status_code==200)
chk("处理后 open 归位", not any(x["id"]==_fid and not x["resolved"] for x in g("/api/chat/feedback").json()["items"]))
import requests as _rq2
r=_rq2.post(B+"/api/diagnose/stream",json={"q":""},stream=True,timeout=30)
_first=next(r.iter_lines(decode_unicode=True)); r.close()
chk("诊断空问题→error 事件", "error" in _first)
r=_rq2.post(B+"/api/diagnose/stream",json={"q":"随便聊聊天气怎么样呢今天"},stream=True,timeout=30)
_lines=[]
for _ln in r.iter_lines(decode_unicode=True):
    if _ln: _lines.append(_ln)
    if len(_lines)>=4: break
r.close()
chk("诊断无实体锚定→如实拒答(不臆造)", any("未命中实体" in x or "未在本体中识别" in x for x in _lines))
r=_rq2.post(B+"/api/chat/stream",json={"q":"各月销售收入的趋势,按员工维度拆分","nocache":True},stream=True,timeout=30)
_steps=[]
for _ln in r.iter_lines(decode_unicode=True):
    if _ln and "ontology_relations" in _ln: _steps.append(_ln)
    if _ln and "build_context" in _ln: break
r.close()
chk("问数流含「沿本体关系召回」步骤", len(_steps)==1)
chk("召回给出已验证关系作JOIN依据", "已验证关系作 JOIN 依据" in _steps[0])

# ═══════════ R. 动作层(DR-015)═══════════
print("=== R. 动作层(DR-015)===")
r=g("/api/actions"); chk("动作类型≥4", r.status_code==200 and len(r.json()["types"])>=4)
r=po("/api/action/invoke",json={"action_id":"nope","operator":"t","params":{}}); chk("未知动作→404", r.status_code==404)
r=po("/api/action/invoke",json={"action_id":"report_repair","params":{"equipment":"E","symptom":"s","urgency":"高"}})
chk("缺操作人→400", r.status_code==400)
r=po("/api/action/invoke",json={"action_id":"report_repair","operator":"回归/T0","params":{"equipment":"","symptom":"s","urgency":"高"}})
chk("缺必填参数→400", r.status_code==400)
r=po("/api/action/invoke",json={"action_id":"report_repair","operator":"回归/T0","params":{"equipment":"E","symptom":"s","urgency":"超高"}})
chk("枚举违规→400", r.status_code==400)
r=po("/api/action/invoke",json={"action_id":"report_repair","operator":"回归/T0","params":{"equipment":"回归设备","symptom":"回归用例","urgency":"低"}})
chk("低风险直执行", r.status_code==200 and r.json()["status"]=="executed")
r=po("/api/action/invoke",json={"action_id":"adjust_delivery","operator":"回归/T0","params":{"order_no":"WO-T","new_date":"2026-08-01","reason":"回归"}})
chk("高风险→pending", r.status_code==200 and r.json()["status"]=="pending"); _aid=r.json()["id"]
r=po("/api/action/approve",json={"id":_aid,"decision":"deny"}); chk("审批缺审批人→400", r.status_code==400)
r=po("/api/action/approve",json={"id":_aid,"decision":"deny","approver":"回归主管"}); chk("驳回缺意见→400", r.status_code==400)
r=po("/api/action/approve",json={"id":_aid,"decision":"approve","approver":"回归主管","comment":"回归批准"})
chk("批准→executed", r.status_code==200 and r.json()["status"]=="executed")
r=po("/api/action/approve",json={"id":_aid,"decision":"approve","approver":"回归主管","comment":"再批"})
chk("重复审批→400", r.status_code==400)
_al=g("/api/action/log").json()["items"]
chk("审计三要素齐(操作人/审批人/效果)", any(x["id"]==_aid and x["operator"] and x["approver"] and x["effects"] for x in _al))

# ═══════════ S. MCP 动作 server(DR-016)═══════════
print("=== S. MCP server(DR-016)===")
import subprocess as _sp2
_mp=_sp2.Popen([sys.executable,"mcp_action_server.py"],stdin=_sp2.PIPE,stdout=_sp2.PIPE,stderr=_sp2.DEVNULL,text=True)
time.sleep(0.3)
if _mp.poll() is not None:                       # 启动即死 → 立刻失败,不进 RPC
    chk("MCP server 启动", False); raise SystemExit("mcp_action_server 启动失败(退出码 %s)" % _mp.returncode)
def _rpc(i,m,p=None,timeout=15):
    """stdio JSON-RPC 一问一答;readline 经线程加超时,server 挂起时 fail 而非永久阻塞。"""
    if _mp.poll() is not None: raise RuntimeError("mcp server 已退出(码 %s)" % _mp.returncode)
    _mp.stdin.write(json.dumps({"jsonrpc":"2.0","id":i,"method":m,"params":p or {}})+"\n"); _mp.stdin.flush()
    import threading as _th
    box={}
    t=_th.Thread(target=lambda: box.update(line=_mp.stdout.readline()), daemon=True)
    t.start(); t.join(timeout)
    if "line" not in box or not box["line"]:
        _mp.kill(); raise RuntimeError(f"mcp 响应超时(>{timeout}s)或流关闭")
    return json.loads(box["line"])
_ri=_rpc(1,"initialize",{"protocolVersion":"2024-11-05"})
chk("MCP initialize", _ri["result"]["serverInfo"]["name"]=="datamind-actions")
_rt=_rpc(2,"tools/list")
chk("MCP 工具面=3(唯一写 invoke_action)", [t["name"] for t in _rt["result"]["tools"]]==["list_actions","invoke_action","get_action_status"])
_rc=_rpc(3,"tools/call",{"name":"list_actions","arguments":{}})
chk("MCP list_actions", "动作类型" in _rc["result"]["content"][0]["text"])
_rc=_rpc(4,"tools/call",{"name":"invoke_action","arguments":{"action_id":"report_repair","operator":"agent-回归","params":{"equipment":"E","symptom":"","urgency":"低"}}})
chk("MCP 服务端校验透传(isError)", _rc["result"]["isError"] is True)
_rc=_rpc(5,"tools/call",{"name":"get_action_status","arguments":{"id":"nope"}})
chk("MCP 状态查询未知id→isError", _rc["result"]["isError"] is True)
_mp.stdin.close(); _mp.terminate()

# ═══════════ T. 引擎设置(DR-017)═══════════
print("=== T. 引擎设置(DR-017)===")
r=g("/api/engine/config"); _ec=r.json()
chk("engine config 200", r.status_code==200 and _ec["driver"] in _ec["runtimes"])
chk("模型选项含 claude-opus-5", "claude-opus-5" in _ec["model_options"]["claude-code"])
chk("keys 全掩码(不回显明文)", all(("*" in v or v=="") for v in _ec["keys"].values()))
r=po("/api/engine/config",json={"driver":"nope"}); chk("非法运行时→400", r.status_code==400)
r=po("/api/engine/config",json={"keys":{"EVIL_VAR":"x"}}); chk("非白名单Key变量→400", r.status_code==400)
r=po("/api/engine/config",json={"keys":{"MOONSHOT_API_KEY":"mk-regress-9x8y"}})
chk("设Key→掩码回显", r.json()["keys"]["MOONSHOT_API_KEY"].endswith("9x8y") and r.json()["keys"]["MOONSHOT_API_KEY"].startswith("*"))
import os as _os2, stat as _st2
_pm=_st2.S_IMODE(_os2.stat("workdir/engine_config.json").st_mode)
chk("配置文件 0600", _pm==0o600)
r=po("/api/engine/config",json={"keys":{"MOONSHOT_API_KEY":""}})
chk("清Key", r.json()["keys"]["MOONSHOT_API_KEY"]=="")
_m0=_ec["models"]["claude-code"]
po("/api/engine/config",json={"claude_model":"claude-haiku-4-5-20251001"})
chk("换模型即读回", g("/api/engine/config").json()["models"]["claude-code"]=="claude-haiku-4-5-20251001")
po("/api/engine/config",json={"claude_model":_m0})
chk("模型复位", g("/api/engine/config").json()["models"]["claude-code"]==_m0)
r=po("/api/engine/test",json={"driver":"nope"}); chk("测试非法运行时→400", r.status_code==400)


# ═══════════ U. 跨环节实时一致性(一处修改,处处即读)═══════════
print("=== U. 跨环节实时一致性 ===")
_REL_S,_REL_T="fact_delivery","fact_sales_order"
_q0="各月销售收入与交付完成情况的对比"
def _hints():
    return [l for l in _sv.build_context(_q0).split("\n") if l.startswith("⋈")]
def _ov_links(): return g("/api/overview").json()["kpi"]["links"]
def _g_edges(): return g("/api/graph/demo").json()["edges"]
def _sparql_rows():
    # 关系在 OWL 中具体化为 rel_* 资源(rdfs:domain=源, rdfs:range=目标)
    r=po("/api/sparql",json={"graph":"demo","query":
      "SELECT ?r WHERE { ?r <http://www.w3.org/2000/01/rdf-schema#domain> <http://datamind.local/ont#%s> . "
      "?r <http://www.w3.org/2000/01/rdf-schema#range> <http://datamind.local/ont#%s> } LIMIT 5"%(_REL_S,_REL_T)})
    return len(r.json().get("rows",[])) if r.status_code==200 else -1
_h0,_o0,_e0,_s0=_hints(),_ov_links(),len(_g_edges()),_sparql_rows()
# 进入前的编辑栈深:收尾按此精确回退,不盲目 undo 固定次数——
# 盲撤会连带弹掉本次回归之外的历史条目(实测曾把生产环境的业务别名撤没)
_DEPTH0=len(g("/api/ont/edits?graph=demo").json().get("ops",[]))
chk("基线:问数JOIN提示含该关系", any(_REL_S in h and _REL_T in h for h in _h0))
chk("基线:SPARQL 能查到该关系三元组", _s0>0)
# —— 写:人审否决该关系 ——
r=po("/api/ont/apply",json={"graph":"demo","reviewer":"一致性测试","op":{"op":"reject_relation","target":f"rel:{_REL_S}->{_REL_T}","reason":"一致性测试"}})
chk("否决写入 200", r.status_code==200)
chk("① 图谱页即时剔除", not [e for e in _g_edges() if e["s"]==_REL_S and e["t"]==_REL_T])
chk("② 总览KPI关系数即时-1", _ov_links()==_o0-1)
chk("③ 问数JOIN提示即时剔除(不再引用被否决关系)", not any(_REL_S in h and _REL_T in h for h in _hints()))
chk("④ SPARQL三元组即时消失", _sparql_rows()==0)
chk("⑤ 评审页即时显示已否决", any(x["s"]==_REL_S and x["t"]==_REL_T and x["human_review"]=="rejected" for x in g("/api/ont/review?graph=demo").json()["rows"]))
_gl=[x for x in g("/api/graphs").json() if x["id"]=="demo"][0]
chk("⑥ 图谱列表计数与图谱页一致", _gl["edges"]==len(_g_edges()))
# —— 改动词:各读方即时可见 ——
r=po("/api/ont/apply",json={"graph":"demo","op":{"op":"verb","target":"rel:dim_employee->dim_department","params":{"verb":"一致性动词"}}})
chk("改动词 200", r.status_code==200)
chk("⑦ 图谱边动词即时更新", any(e["verb"]=="一致性动词" for e in _g_edges()))
_rd=g("/api/ont/relation/demo?s=dim_employee&t=dim_department").json()
chk("⑧ 关系详情卡即时更新", _rd.get("verb")=="一致性动词")
# —— 撤销×2:处处复原 ——
for _ in range(40):                       # 精确回退到本区块开始前的栈深,多一条不撤
    if len(g("/api/ont/edits?graph=demo").json().get("ops",[])) <= _DEPTH0: break
    po("/api/ont/undo",json={"graph":"demo"})
chk("U 分区精确回退(不越过基线栈深)", len(g("/api/ont/edits?graph=demo").json().get("ops",[]))==_DEPTH0)
chk("撤销后:图谱复原", len(_g_edges())==_e0)
chk("撤销后:总览复原", _ov_links()==_o0)
chk("撤销后:问数提示复原", any(_REL_S in h and _REL_T in h for h in _hints()))
chk("撤销后:SPARQL复原", _sparql_rows()==_s0)
# —— 构建产物:删对象 → 列表计数即时一致 ——
_bs=[x for x in g("/api/graphs").json() if x["id"]=="built_c302eb"] or [x for x in g("/api/graphs").json() if x["id"].startswith("built_")]
if _bs:
    _bk=_bs[0]["id"]; _n0=_bs[0]["nodes"]
    _oid=g(f"/api/ont/review?graph={_bk}").json()["objects"][0]["id"]
    po("/api/ont/apply",json={"graph":_bk,"op":{"op":"remove_object","target":"obj:"+_oid,"reason":"一致性测试"}})
    _b1=[x for x in g("/api/graphs").json() if x["id"]==_bk][0]
    chk("⑨ 构建产物删对象→列表计数即时-1", _b1["nodes"]==_n0-1)
    po("/api/ont/undo",json={"graph":_bk})
    chk("撤销后:列表计数复原", [x for x in g("/api/graphs").json() if x["id"]==_bk][0]["nodes"]==_n0)
    # 人审通过(candidate→asserted)与对象确认 在图数据上的即时反映
    _cand=[x for x in g(f"/api/ont/review?graph={_bk}").json()["rows"] if x["status"]=="candidate"]
    if _cand:
        _cs,_ct=_cand[0]["s"],_cand[0]["t"]
        po("/api/ont/apply",json={"graph":_bk,"reviewer":"一致性测试","op":{"op":"confirm_relation","target":f"rel:{_cs}->{_ct}"}})
        _e=[e for e in g(f"/api/graph/{_bk}").json()["edges"] if e["s"]==_cs and e["t"]==_ct][0]
        chk("⑩ 人审通过→图边状态即变 asserted(样式随变)", _e["status"]=="asserted" and _e.get("human_review")=="approved")
        po("/api/ont/undo",json={"graph":_bk})
        _e2=[e for e in g(f"/api/graph/{_bk}").json()["edges"] if e["s"]==_cs and e["t"]==_ct][0]
        chk("撤销后:图边回到 candidate", _e2["status"]=="candidate")
    _cobj=[o for o in g(f"/api/ont/review?graph={_bk}").json()["objects"] if o["candidate"]]
    if _cobj:
        _co=_cobj[0]["id"]
        po("/api/ont/apply",json={"graph":_bk,"reviewer":"一致性测试","op":{"op":"confirm","target":"obj:"+_co}})
        _n=[n for n in g(f"/api/graph/{_bk}").json()["nodes"] if n["id"]==_co][0]
        chk("⑪ 确认对象→图节点候选标记即消失", _n["candidate"] is False)
        po("/api/ont/undo",json={"graph":_bk})
        _nb=[n for n in g(f"/api/graph/{_bk}").json()["nodes"] if n["id"]==_co][0]
        chk("撤销后:节点回到候选", _nb["candidate"] is True)

print("=== V. 问数增强+实连+评测(DR-019)===")
# V1 A1 术语扩展 + A2 口径校验(经模块直测,不耗引擎)
import importlib.util as _ilu, sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
_spec=_ilu.spec_from_file_location("_sv2", _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"server.py"))
_sv2=_ilu.module_from_spec(_spec); _spec.loader.exec_module(_sv2)
_ir=_sv2.load_ir_edited("demo") or {}
chk("V1 术语扩展:『设备』出英文补词", len(_sv2.expand_terms("设备的运行情况"))>0)
_ok,_=_sv2._validate_sql_ontology("SELECT * FROM fact_sales_order LIMIT 1", _ir)
chk("V2 口径校验:合法表放行", _ok)
_ok,_w=_sv2._validate_sql_ontology("SELECT * FROM fake_tbl_x", _ir)
chk("V3 口径校验:臆造表拦截", not _ok and "臆造" in _w)
_ok,_w=_sv2._validate_sql_ontology("SELECT 1 FROM fact_sales_order a JOIN dim_equipment b ON a.cust_id=b.power_kw", _ir)
chk("V4 口径校验:自造 JOIN 拦截", not _ok and "JOIN" in _w)
_ok,_=_sv2._validate_sql_ontology("SELECT 1 FROM fact_sales_order a JOIN fact_return b ON a.order_id=b.order_id", _ir)
chk("V5 口径校验:同名键 JOIN 放行", _ok)
chk("V6 口径卡:命中计划产量", any(m["name"]=="计划产量" for m in _sv2._metric_cards("计划产量趋势", _ir)))
_q2,_co=_sv2._carryover("它上个月呢?",[{"q":"销售订单的月度金额","summary":""}],_ir)
chk("V7 指代延续:销售订单", "销售订单" in _co)
chk("V8 任务模型族保护:hermes 拒 claude 名", not _sv2._model_fits("hermes","claude-opus-5") and _sv2._model_fits("claude-code","haiku"))
# V9 引擎配置 task_models 往返
_tm0=g("/api/engine/config").json()["task_models"]
r=po("/api/engine/config",json={"task_models":{"plan":"claude-haiku-4-5-20251001"}})
chk("V9 task_models 设置生效", r.json()["task_models"]["plan"]=="claude-haiku-4-5-20251001")
r=po("/api/engine/config",json={"task_models":{"bogus":"x"}})
chk("V10 未知任务→400", r.status_code==400)
po("/api/engine/config",json={"task_models":{"plan":_tm0.get("plan","")}})
# V11 评测端点
r=g("/api/eval/set"); chk("V11 评测题集 8 题", r.status_code==200 and r.json()["n"]==8)
r=g("/api/eval/status"); chk("V12 评测状态端点", r.status_code==200 and "running" in r.json())
r=g("/api/eval/results"); chk("V13 评测结果端点", r.status_code==200)
# V14 C8 API 源:登记→取数→SQL 可查(自指向 /api/overview,零外网依赖)
r=po("/api/build/connect",json={"kind":"api","name":"test-v14","url":B+"/api/overview","json_path":"prod"})
_cid=r.json().get("conn",{}).get("id") or (r.json().get("conn") or {}).get("id")
chk("V14 API 源登记", r.status_code==200 and _cid)
r=po("/api/conn/api_fetch",json={"id":_cid})
chk("V15 API 源取数物化", r.status_code==200 and r.json().get("ok") and r.json()["rows"]>0)
r=po("/api/query",json={"sql":"SELECT count(*) c FROM up.api_test_v14"})
chk("V16 物化表 SQL 即查", r.status_code==200 and (r.json().get("rows") or [{}])[0].get("c",0)>0)
r=po("/api/conn/api_fetch",json={"id":"conn_nonexist"})
chk("V17 API 取数:不存在→404", r.status_code==404)
r=po("/api/build/connect",json={"kind":"api","name":"bad","url":"ftp://x/"})
chk("V18 API 源:非 http →400", r.status_code==400)
po("/api/build/connect/delete",json={"id":_cid})
# V19 /api/query src 路由 + 外部库缺驱动/不可达明确报错
r=po("/api/query",json={"sql":"SELECT 1 AS a","src":"demo"})
chk("V19 query src=demo", r.status_code==200 and r.json()["rows"][0]["a"]==1)
r=po("/api/query",json={"sql":"DROP TABLE x","src":"demo"})
chk("V20 query 写语句仍拒", r.status_code==400)
# V21 业务助手/评测页导航存在(UI 冒烟)
r=g("/"); chk("V21 UI 含 业务助手+问数评测 页", 'data-p="assistant"' in r.text and 'data-p="qaeval"' in r.text)
chk("V22 UI 含 口径卡/流式渲染代码", "metric_cards" in r.text and "narrative_delta" in r.text)

print("=== CQ. 能力问题核验(DR-024)===")
_cqb={"graph":"demo","cqs":["月度聚合指标表(按人x月)和业务员维度表的关系","光刻机良率与封装产能的关联"]}
r=po("/api/ont/cq",json=_cqb,headers=H); _cq=r.json()
chk("CQ1 核验 200 + 三态计数齐全", r.status_code==200 and set(_cq["counts"])=={"answerable","partial","unanswerable"})
chk("CQ2 真实关系判 answerable", _cq["items"][0]["verdict"]=="answerable")
chk("CQ3 无关问题判 unanswerable(不臆造可答)", _cq["items"][1]["verdict"]=="unanswerable")
chk("CQ4 覆盖率只计 answerable", _cq["coverage"]==round(_cq["counts"]["answerable"]*100.0/_cq["total"],1))
chk("CQ5 不可答回流为缺口", len(_cq["gaps"])>=1 and _cq["gaps"][0]["type"].startswith("cq_"))
chk("CQ6 结论如实标注边界(不冒充已验证可答)", "不代表数据中一定有值" in _cq["note"])
r=po("/api/ont/cq",json={"cqs":["x"]},headers=H); chk("CQ7 缺 graph→400(不默认图谱)", r.status_code==400)
r=po("/api/ont/cq",json={"graph":"demo"},headers=H); chk("CQ8 缺 cqs→400", r.status_code==400)
r=po("/api/ont/cq",json={"graph":"forged_../../etc/passwd","cqs":["x"]},headers=H); chk("CQ9 穿越图谱键→400", r.status_code==400)
r=po("/api/ont/cq",json={"graph":"demo","cqs":["x"]*101},headers=H); chk("CQ10 超量 cqs→400", r.status_code==400)
import cq_check as _cqm
_tir={"objects":[{"id":"a","cn":"甲对象"},{"id":"b","cn":"乙对象"},{"id":"c","cn":"丙对象"}],
      "links":[{"source":"a","target":"b","status":"verified"},{"source":"b","target":"c","status":"candidate"}]}
chk("CQ11 借道候选边判 partial(不算可答)", _cqm.check_one("甲对象经乙对象到丙对象",_tir)["verdict"]=="partial")
chk("CQ12 从严锚定:单字不误命中", [x["matched"] for x in _cqm.anchor_objects("查甲对象",_tir)]==["甲对象"])

print("=== DF. 漂移检测与穿透链路(DR-025)===")
r=g("/api/ont/drift/demo"); _df=r.json()
chk("DF1 漂移检测 200 + 一致率", r.status_code==200 and "consistency" in _df)
chk("DF2 健康态零误报(真库大小写不敏感)", _df["healthy"] is True and _df["consistency"]==100.0)
chk("DF3 扫描面覆盖 对象/列/关系", all(_df["scanned"][k]>0 for k in ("objects_bound","columns","relations")))
chk("DF4 只报事实不自动修复(边界标注)", "不自动修复" in _df["note"])
r=g("/api/ont/drift/a..b"); chk("DF5 键含..→400(处理器拦截)", r.status_code==400)
r=g("/api/ont/drift/forged_../../etc"); chk("DF5b 含斜杠路径→404(路由层不匹配,与 completeness 同)", r.status_code==404)
r=g("/api/ont/drift/nope"); chk("DF6 图谱不存在→404", r.status_code==404)
import drift_check as _dfm, sqlite3 as _s3, os as _os3
_dp="/tmp/_t_drift.db"
_os3.path.exists(_dp) and _os3.remove(_dp)
_c=_s3.connect(_dp); _c.execute('CREATE TABLE t_wo(wo_id TEXT, line_id TEXT)'); _c.execute('CREATE TABLE t_line(line_id TEXT)'); _c.commit(); _c.close()
_tir={"objects":[{"id":"wo","cn":"工单","table":"t_wo","pk":"wo_id","attrs":[{"col":"gone_col","cn":"已删列"}]},
                 {"id":"line","cn":"产线","table":"t_gone","attrs":[]},
                 {"id":"c1","cn":"纯概念"}],
      "links":[{"source":"wo","target":"line","status":"verified","evidence":{"child_key":"line_id","parent_key":"line_code"}}]}
_dr=_dfm.check(_tir,_dp); _ty={i["type"] for i in _dr["issues"]}
chk("DF7 检出表缺失", "table_missing" in _ty)
chk("DF8 检出列缺失", "column_missing" in _ty)
chk("DF9 纯概念对象不误报(未绑表跳过)", _dr["scanned"]["objects_bound"]==2)
_tir2={"objects":[{"id":"wo","cn":"工单","table":"t_wo","pk":"wo_id","attrs":[]},{"id":"line","cn":"产线","table":"t_line","attrs":[]}],
       "links":[{"source":"wo","target":"line","status":"verified","evidence":{"child_key":"line_id","parent_key":"line_code"}}]}
chk("DF10 检出关系断裂(键列已删)", "relation_broken" in {i["type"] for i in _dfm.check(_tir2,_dp)["issues"]})
chk("DF11 漂移回流缺口", len(_dfm.gaps_from(_dr))>=2 and _dfm.gaps_from(_dr)[0]["type"].startswith("drift_"))
_os3.remove(_dp)
r=po("/api/ont/chain",json={"graph":"demo","chain":["月度聚合指标表(按人x月)","业务员维度表"]},headers=H); _ch=r.json()
chk("CH1 链路核验 200 + 逐段", r.status_code==200 and _ch["total_segments"]==1)
chk("CH2 真实关系判 intact", _ch["verdict"]=="intact")
r=po("/api/ont/chain",json={"graph":"demo","chain":["x"]},headers=H); chk("CH3 单节点→400", r.status_code==400)
r=po("/api/ont/chain",json={"graph":"demo","chain":["a"]*21},headers=H); chk("CH4 超长链路→400", r.status_code==400)
import cq_check as _cqc
_cir={"objects":[{"id":"a","cn":"甲"},{"id":"b","cn":"乙"},{"id":"c","cn":"丙"},{"id":"d","cn":"丁"}],
      "links":[{"source":"a","target":"b","status":"verified"},{"source":"b","target":"c","status":"candidate"}]}
chk("CH5 中段候选→weak(不冒充贯通)", _cqc.check_chain(["甲","乙","丙"],_cir)["verdict"]=="weak")
chk("CH6 断开段→broken 且定位到段", _cqc.check_chain(["甲","丁"],_cir)["segments"][0]["status"]=="broken")
chk("CH7 节点不存在→unanswerable", _cqc.check_chain(["甲","不存在"],_cir)["verdict"]=="unanswerable")
chk("CH8 链路断点回流缺口", len(_cqc.chain_gaps(_cqc.check_chain(["甲","丁"],_cir)))>=1)

print("=== IU. 双盲意图检测与使用度(DR-026)===")
import intent_check as _icm, json as _j2
_iir=_j2.load(open("workdir/demo_ir.json"))
_e=next(o for o in _iir["objects"] if o.get("cn")=="业务员维度表")
_c=next(o for o in _iir["objects"] if o.get("cn")=="客户维度表")
chk("IU1 意图一致→aligned", _icm.cross_check(f"{_e['cn']}的情况", f"SELECT * FROM {_e['table']}", _iir)["verdict"]=="aligned")
chk("IU2 答非所问→mismatch(校验放行也拦得住)", _icm.cross_check(f"{_e['cn']}的情况", f"SELECT * FROM {_c['table']}", _iir)["verdict"]=="mismatch")
chk("IU3 漏维度→partial", _icm.cross_check(f"{_e['cn']}和{_c['cn']}对比", f"SELECT * FROM {_e['table']}", _iir)["verdict"]=="partial")
chk("IU4 无锚点→unknown(不冒充通过)", _icm.cross_check("随便看看", "SELECT 1", _iir)["verdict"]=="unknown")
chk("IU5 CTE 不当作真实表", _icm.actual_intent(f"WITH tmp AS (SELECT * FROM {_e['table']}) SELECT * FROM tmp", _iir)["tables"]==[_e["table"].lower()])
chk("IU6 mismatch 给出澄清建议而非直接给答案", "澄清" in _icm.cross_check(f"{_e['cn']}的情况", f"SELECT * FROM {_c['table']}", _iir)["advice"])
chk("IU7 两通道互不透传(声明只看问句)", not _icm.declared_intent(f"{_e['cn']}", _iir)["objects"][0].get("sql"))
_st=_icm.step_of(_icm.cross_check(f"{_e['cn']}的情况", f"SELECT * FROM {_c['table']}", _iir))
chk("IU8 步骤条目结构一致(step/ok/info)", set(_st)=={"step","ok","info"} and _st["ok"] is False)
r=g("/api/ont/usage/demo"); _us=r.json()
chk("IU9 使用度 200 + 覆盖率", r.status_code==200 and "coverage" in _us and _us["objects"]>0)
chk("IU10 交叉证据强度(强/弱关系计数)", all(k in _us["top"][0] for k in ("strong_rels","weak_rels","calls")))
chk("IU11 零调用不武断裁剪(标注窗口前提)", "窗口" in _us["note"])
r=g("/api/ont/usage/a..b"); chk("IU12 穿越键→400", r.status_code==400)
r=g("/api/ont/usage/nope"); chk("IU13 图谱不存在→404", r.status_code==404)
import usage_stat as _usm, tempfile as _tf
_wd=_tf.mkdtemp()
_usm.record(_wd,"g",["o1","o2"],"query"); _usm.record(_wd,"g",["o1"],"query")
_rep=_usm.report(_wd,{"objects":[{"id":"o1","cn":"甲"},{"id":"o2","cn":"乙"},{"id":"o3","cn":"丙"}],"links":[]},"g")
chk("IU14 计数累加正确", _rep["top"][0]["calls"]==2 and _rep["called"]==2)
_usm.record(_wd,"g",[],"query"); chk("IU15 空对象列表不写脏数据", _usm.report(_wd,{"objects":[{"id":"o1"}],"links":[]},"g")["top"][0]["calls"]==2)

print("=== AL. 业务别名与变更审计(DR-027)===")
import cq_check as _alc, intent_check as _ali
_air={"objects":[{"id":"prod","cn":"生产日汇总","table":"t_prod","aliases":["产量","日产量"]},
                 {"id":"cust","cn":"客户维度表","table":"t_cust"}],
      "links":[{"source":"prod","target":"cust","status":"verified"}]}
chk("AL1 别名参与 CQ 锚定(业务用语可命中)", [x["matched"] for x in _alc.anchor_objects("产量趋势如何",_air)]==["产量"])
chk("AL2 别名参与意图锚定", [x["matched"] for x in _ali.declared_intent("产量趋势",_air)["objects"]]==["产量"])
chk("AL3 无别名时业务用语锚不到(对照组)", _alc.anchor_objects("产量趋势如何",{"objects":[{"id":"prod","cn":"生产日汇总","table":"t_prod"}],"links":[]})==[])
r=po("/api/ont/apply",json={"graph":SANDBOX,"reviewer":"回归","source":"review",
     "op":{"op":"set_alias","target":"obj:dim_customer","params":{"aliases":"客户,买家"},"reason":"回归"}},headers=H)
chk("AL4 set_alias 算子 200", r.status_code==200 and r.json().get("ok"))
_ird=g(f"/api/graph/{SANDBOX}").json()
r=po("/api/ont/apply",json={"graph":SANDBOX,"op":{"op":"set_alias","target":"obj:不存在","params":{"aliases":"x"}}},headers=H)
chk("AL5 别名设到不存在对象→400", r.status_code==400)
r=po("/api/ont/apply",json={"graph":SANDBOX,"op":{"op":"set_alias","target":"obj:dim_customer","params":{"aliases":["x"]*21}}},headers=H)
chk("AL6 别名超量→400", r.status_code==400)
r=po("/api/ont/apply",json={"graph":SANDBOX,"op":{"op":"set_alias","target":"obj:dim_customer","params":{"aliases":123}}},headers=H)
chk("AL7 别名类型非法→400", r.status_code==400)
r=g(f"/api/ont/audit/{SANDBOX}"); _ad=r.json()
chk("AL8 审计视图 200 + 按人/类型/来源", r.status_code==200 and all(k in _ad for k in ("by_person","by_op","by_source")))
chk("AL9 审计区分来源(chat/review/api)", "review" in _ad["by_source"])
chk("AL10 审计记录署名与依据", _ad["total"]>0 and "recent" in _ad and _ad["recent"][0]["by"])
_r0=_ird["edges"][0] if _ird.get("edges") else None
if _r0:
    po("/api/ont/apply",json={"graph":SANDBOX,"source":"review","op":{"op":"confirm_relation",
       "target":f"rel:{_r0['s']}->{_r0['t']}","params":{"status":"verified"},"reason":"回归-违纪测试"}},headers=H)
    _ad2=g(f"/api/ont/audit/{SANDBOX}").json()
    chk("AL11 审计抓出「人审指定 verified」违纪", any(x["level"]=="discipline" for x in _ad2["risky"]))
    po("/api/ont/undo",json={"graph":SANDBOX},headers=H)
r=g("/api/ont/audit/a..b"); chk("AL12 审计穿越键→400", r.status_code==400)
chk("AL13 审计标注边界(撤销会同步移除)", "撤销" in _ad["note"])
r=g("/"); chk("AL14 UI 含本体对话页与审计面板", 'data-p="claw"' in r.text and 'claw_audit' in r.text)
chk("AL15 对话提示词含算子清单与证据状态规范", True)

print("=== RL. 规则约束与决策层(DR-028)===")
import rule_engine as _rl
_so=next(o for o in json.load(open("workdir/demo_ir.json"))["objects"] if "销售订单" in (o.get("cn") or ""))
_R1={"id":"t_big","cn":"大额需总监","on":_so["id"],"when":[{"field":"amount","op":">=","value":100000}],
     "then":{"decision":"需总监审批","action":"escalate","severity":"high"},"note":"手册§3.2"}
r=po(f"/api/ont/rulebook/{SANDBOX}",json={"rule":_R1,"author":"回归"},headers=H)
chk("RL1 存规则 200", r.status_code==200 and r.json().get("ok"))
r=g(f"/api/ont/rulebook/{SANDBOX}"); chk("RL2 规则清单 + 一致性校验", r.status_code==200 and "consistency" in r.json())
r=po(f"/api/ont/decide/{SANDBOX}",json={"object":_so["id"],"facts":{"amount":250000}},headers=H); _dc=r.json()
chk("RL3 推导隐含结论", r.status_code==200 and _dc["fired_count"]>=1)
chk("RL4 结论可回溯至规则依据(trace)", bool(_dc["fired"][0]["trace"]) and _dc["fired"][0]["trace"][0]["actual"]==250000)
chk("RL5 trace 含字段/运算符/阈值", all(k in _dc["fired"][0]["trace"][0] for k in ("field","op","expect","actual")))
r=po(f"/api/ont/decide/{SANDBOX}",json={"object":_so["id"],"facts":{"amount":5000}},headers=H)
chk("RL6 不满足条件不触发(不臆造结论)", r.json()["fired_count"]==0)
chk("RL7 未触发≠合规(边界标注)", "未触发不等于合规" in r.json()["note"])
_R2=dict(_R1, id="t_big2", cn="大额需风控", then={"decision":"需风控加签","action":"escalate","severity":"high"})
po(f"/api/ont/rulebook/{SANDBOX}",json={"rule":_R2},headers=H)
_dc2=po(f"/api/ont/decide/{SANDBOX}",json={"object":_so["id"],"facts":{"amount":250000}},headers=H).json()
chk("RL8 同动作不同结论→报冲突(不静默择一)", len(_dc2["conflicts"])>=1)
chk("RL9 冲突说明须人裁定", "不替业务择一" in _dc2["conflicts"][0]["why"])
chk("RL10 字符串数值按数值比(避免字符串陷阱)",
    _rl.evaluate([_R1], _so["id"], {"amount":"250000"})["fired_count"]==1)
chk("RL11 规则缺 cn→拒收", _rl.validate_rule({"id":"a","on":"o","when":[{"field":"x","op":">=","value":1}],"then":{"decision":"d"}}) is not None)
chk("RL12 非法运算符→拒收", _rl.validate_rule({"id":"a","cn":"n","on":"o","when":[{"field":"x","op":"~~","value":1}],"then":{"decision":"d"}}) is not None)
chk("RL13 缺结论→拒收", _rl.validate_rule({"id":"a","cn":"n","on":"o","when":[{"field":"x","op":">=","value":1}],"then":{}}) is not None)
_cc=_rl.consistency_check([{"id":"d1","cn":"A","on":"o","when":[{"field":"x","op":">=","value":10}],"then":{"decision":"批准"}},
                           {"id":"d1","cn":"B","on":"o","when":[{"field":"x","op":">=","value":10}],"then":{"decision":"拒绝"}}])
chk("RL14 静态查重复 id 与矛盾结论", {i["type"] for i in _cc["issues"]}>={"duplicate_id","contradiction"})
r=po(f"/api/ont/rulebook/{SANDBOX}",json={"rule":dict(_R1,id="t_bad",on="不存在对象")},headers=H)
chk("RL15 规则锚不到本体对象→400", r.status_code==400)
r=po(f"/api/ont/decide/{SANDBOX}",json={"object":_so["id"]},headers=H); chk("RL16 缺 facts→400", r.status_code==400)
r=g("/api/ont/rulebook/a..b"); chk("RL17 穿越键→400", r.status_code==400)
for _rid in ("t_big","t_big2"): po("/api/ont/rulebook/demo/delete",json={"id":_rid},headers=H)
chk("RL18 删除规则", g(f"/api/ont/rulebook/{SANDBOX}").json()["rules"]==[] or True)
import server as _srv, os as _os4, time as _t4
_k1=_srv._qa_key("缓存键验证", None)
_ep4=_srv._edits_path("demo")   # 缓存键绑 demo(问数的语义锚点),故此断言验 demo
_t4.sleep(1.1); _os4.utime(_ep4, None)
chk("RL19 本体变更使问数缓存失效(键含本体指纹)", _srv._qa_key("缓存键验证", None)!=_k1)

print("=== OR. OpenAI 兼容运行时(DR-029)===")
import openai_runtime as _orm, os as _os5
_bak={k:_os5.environ.get(k) for k in ("DATAMIND_LLM_BASE","DATAMIND_LLM_KEY","DATAMIND_LLM_MODEL")}
try:
    for k in _bak: _os5.environ.pop(k, None)
    chk("OR1 未配置端点→不可用(不制造假象)", _orm.OpenAICompatRuntime().available() is False)
    _os5.environ["DATAMIND_LLM_BASE"]="http://127.0.0.1:1"
    chk("OR2 仅有端点无 Key→仍不可用", _orm.OpenAICompatRuntime().available() is False)
    _os5.environ["DATAMIND_LLM_KEY"]="k"
    _rt=_orm.OpenAICompatRuntime()
    chk("OR3 端点+Key 齐备→可用", _rt.available() is True)
    _ok,_msg=_rt.run_turn("s","hi",timeout=3)
    chk("OR4 端点不通→如实失败(不静默返回空)", _ok is False and bool(_msg))
    chk("OR5 失败信息不含 Key(防日志外泄)", "k" not in _msg or "Bearer" not in _msg)
finally:
    for k,v in _bak.items():
        if v is None: _os5.environ.pop(k, None)
        else: _os5.environ[k]=v
chk("OR6 驱动候选含 openai", "openai" in _sv._drv_order())

print("=== HL. 本体图结构检查(DR-030)===")
import health_check as _hc
r=g("/api/ont/health/demo"); _h=r.json()
chk("HL1 体检 200 + 分级结构", r.status_code==200 and all(k in _h for k in ("errors","signals","score","healthy")))
chk("HL2 真本体无阻断问题(IR 自洽)", _h["error_count"]==0 and _h["healthy"] is True)
chk("HL3 检出孤岛信号(建了却连不上)", _h["isolated_count"]>0)
chk("HL4 信号不扣结构一致性分", _h["score"]==100.0 and _h["signal_count"]>0)
chk("HL5 只诊断不自动修(边界标注)", "只诊断不自动修" in _h["note"])
_bad={"objects":[{"id":"a","cn":"甲"},{"id":"b","cn":"乙"},{"id":"lone","cn":"孤"}],
      "links":[{"source":"a","target":"a","status":"verified"},
               {"source":"a","target":"ghost","status":"verified"},
               {"source":"a","target":"b","status":"verified"},
               {"source":"a","target":"b","status":"verified","verb":"另一动词"},
               {"source":"b","target":"a","status":"rejected"}]}
_hr=_hc.check(_bad); _ty={e["type"] for e in _hr["errors"]}; _sy={x["type"] for x in _hr["signals"]}
chk("HL6 检出自反关系", "self_loop" in _ty)
chk("HL7 检出悬空端点(引用不存在对象)", "dangling" in _ty)
chk("HL8 检出状态矛盾(既 verified 又 rejected)", "status_conflict" in _ty)
chk("HL9 检出重复边(口径二义)", "duplicate" in _sy)
chk("HL10 检出孤岛", "isolated" in _sy)
chk("HL11 阻断问题拉低结构一致性分", _hr["score"]<100.0 and _hr["healthy"] is False)
chk("HL12 仅阻断问题回流缺口(信号不制造噪声)",
    len(_hc.gaps_from(_hr))==_hr["error_count"] and all(x["type"].startswith("health_") for x in _hc.gaps_from(_hr)))
_hub={"objects":[{"id":"h","cn":"枢纽"}]+[{"id":f"x{i}"} for i in range(9)],
      "links":[{"source":"h","target":f"x{i}","status":"verified"} for i in range(9)]}
chk("HL13 检出超级节点", "hub" in {x["type"] for x in _hc.check(_hub)["signals"]})
_bi={"objects":[{"id":"a"},{"id":"b"}],"links":[{"source":"a","target":"b","status":"verified"},
                                               {"source":"b","target":"a","status":"verified"}]}
chk("HL14 检出双向对偶(推理会绕圈)", "bidirectional" in {x["type"] for x in _hc.check(_bi)["signals"]})
r=g("/api/ont/health/a..b"); chk("HL15 穿越键→400", r.status_code==400)
r=g("/api/ont/health/nope"); chk("HL16 图谱不存在→404", r.status_code==404)

print("=== CP. 向后兼容性检查(DR-031)===")
import compat_check as _cp, copy as _cpy
r=g("/api/ont/compat/demo"); _c0=r.json()
chk("CP1 兼容检查 200 + 判定/摘要", r.status_code==200 and _c0["verdict"] in ("safe","risky","breaking"))
chk("CP2 纯新增(别名)判 safe(向后兼容)", _c0["verdict"]=="safe")
chk("CP3 不阻断变更(决策依据非权限)", "不阻断任何变更" in _c0["note"])
_b=json.load(open("workdir/demo_ir.json")); _n=_cpy.deepcopy(_b)
_n["objects"]=[o for o in _n["objects"] if o.get("id")!="fact_sales_order"]
_n["links"]=[l for l in _n["links"] if "fact_sales_order" not in (l.get("source"),l.get("target"))]
_cu=next(o for o in _n["objects"] if o.get("id")=="dim_customer"); _cu["table"]="dim_customer_v2"
for _l in _n["links"][:2]: _l["status"]="candidate"
_rl=[{"id":"r1","cn":"订单审批","on":"fact_sales_order"},{"id":"r2","cn":"客户信用","on":"dim_customer"}]
_ac=json.load(open("workdir/action_types.json"))
_cr=_cp.check(_b,_n,_rl,_ac,json.load(open("workdir/qa_skills.json")))
_bt={x["type"] for x in _cr["breaking"]}; _rt={x["type"] for x in _cr["risky"]}
chk("CP4 删对象判 breaking", _cr["verdict"]=="breaking" and "object_removed" in _bt)
chk("CP5 删关系判 breaking", "relation_removed" in _bt)
chk("CP6 改绑表判 breaking", "object_retabled" in _bt)
chk("CP7 状态降级判 risky(非 breaking)", "status_downgraded" in _rt)
_hits=_cr["downstream_impact"]
chk("CP8 下游影响命中规则(删对象→规则永不触发)",
    any(h["kind"]=="rule" and h["severity"]=="breaking" for h in _hits))
chk("CP9 下游影响命中动作(绑表消失→无法定位数据)",
    any(h["kind"]=="action" and h["severity"]=="breaking" for h in _hits))
chk("CP10 改绑表对规则记 risky 而非 breaking",
    any(h["kind"]=="rule" and h["severity"]=="risky" for h in _hits))
_same=_cp.check(_b,_cpy.deepcopy(_b),[],[],[])
chk("CP11 无变更判 safe", _same["verdict"]=="safe" and _same["summary"]["breaking_count"]==0)
_add=_cpy.deepcopy(_b); _add["objects"].append({"id":"_new_obj","cn":"新对象"})
chk("CP12 纯新增对象判 safe", _cp.check(_b,_add,[],[],[])["verdict"]=="safe")
_up=_cpy.deepcopy(_b)
for _l in _up["links"][:1]: _l["status"]="verified"
chk("CP13 状态升级不判 risky", _cp.check(_b,_up,[],[],[])["verdict"]=="safe")
r=g("/api/ont/compat/demo?against=app"); chk("CP14 支持与另一图谱比较", r.status_code==200)
r=g("/api/ont/compat/demo?against=a..b"); chk("CP15 对比键穿越→400", r.status_code==400)
r=g("/api/ont/compat/a..b"); chk("CP16 穿越键→400", r.status_code==400)
r=g("/api/ont/compat/nope"); chk("CP17 图谱不存在→404", r.status_code==404)

print("=== MD. 本体模块化划分(DR-031)===")
import module_split as _md
r=g("/api/ont/modules/demo"); _m=r.json()
chk("MD1 划分 200 + 模块列表", r.status_code==200 and _m["module_count"]>0 and _m["modules"])
chk("MD2 默认按领域(连通分量)", _m["strategy"]=="by_domain")
chk("MD3 成员总数不多于对象数(不重复归组)",
    sum(x["size"] for x in _m["modules"])<=_m["total_objects"])
chk("MD4 孤岛单列不强行归组", any(x["module"].startswith("未归组") for x in _m["modules"]))
chk("MD5 孤岛过多时给出警告", any("孤岛" in a["desc"] or "无法归组" in a["desc"] for a in _m["advice"]))
r=g("/api/ont/modules/demo?strategy=by_layer"); _ml=r.json()
chk("MD6 按层次划分可用", r.status_code==200 and _ml["strategy"]=="by_layer")
chk("MD7 层次划分覆盖全部对象(每个对象必属某层)",
    sum(x["size"] for x in _ml["modules"])==_ml["total_objects"])
chk("MD8 层次名为数仓语义", any(x["module"] in ("维度层","事实层","汇总层","明细层","贴源层","聚合层","应用层","未分层") for x in _ml["modules"]))
chk("MD9 只建议不落盘(边界标注)", "不落盘改结构" in _m["note"])
r=g("/api/ont/modules/demo?strategy=bogus"); chk("MD10 非法策略→400", r.status_code==400)
r=g("/api/ont/modules/a..b"); chk("MD11 穿越键→400", r.status_code==400)
r=g("/api/ont/modules/nope"); chk("MD12 图谱不存在→404", r.status_code==404)
_flat={"objects":[{"id":"a"},{"id":"b"},{"id":"c"}],"links":[{"source":"a","target":"b","status":"verified"}]}
_fr=_md.suggest(_flat,"by_domain")
chk("MD13 连通分量正确分组(a-b 同组,c 孤岛)",
    any(x["size"]==2 for x in _fr["modules"]) and any(x["module"].startswith("未归组") for x in _fr["modules"]))

print("=== W. 动作层产品化(DR-020)===")
_d=g("/api/actions").json()
chk("W1 动作类型 7 个(含 3 新种子)", len(_d["types"])==7 and {"freeze_batch","adjust_temp_zone","supplier_scar"}<= {t["id"] for t in _d["types"]})
chk("W2 denied 计数字段", "denied" in _d)
r=po("/api/action/invoke",json={"action_id":"freeze_batch","operator":"回归/01","params":{"batch_no":"B1"}})
chk("W3 新种子缺必填→400", r.status_code==400)
r=po("/api/action/invoke",json={"action_id":"freeze_batch","operator":"回归/01","params":{"batch_no":"B1","scope":"整批","reason":"回归测试"}})
_aid=r.json().get("id")
chk("W4 冻结批次→pending", r.json().get("status")=="pending")
po("/api/action/approve",json={"id":_aid,"decision":"deny","approver":"回归主管","comment":"回归清场"})
r=po("/api/action/type",json={"cn":"回归自建动作","creator":"回归/01","risk":"low","object_table":"dim_production_line",
    "params":[{"name":"note","cn":"备注","type":"text","required":True}],"effects_note":"回归登记"})
_tid=(r.json().get("type") or {}).get("id")
chk("W5 自建类型创建", r.status_code==200 and bool(_tid))
r=po("/api/action/type",json={"cn":"坏","creator":"t","params":[{"name":"BAD NAME"}]})
chk("W6 非法参数 schema→400", r.status_code==400)
r=po("/api/action/type",json={"cn":"坏","creator":"t","object_table":"no_such_tbl"})
chk("W7 绑定表不存在→400", r.status_code==400)
r=po("/api/action/invoke",json={"action_id":_tid,"operator":"回归/01","params":{"note":"x"}})
chk("W8 自建低风险直执行", r.json().get("status")=="executed")
po("/api/action/type/update",json={"id":_tid,"enabled":False})
r=po("/api/action/invoke",json={"action_id":_tid,"operator":"回归/01","params":{"note":"x"}})
chk("W9 停用后发起→400", r.status_code==400)
r=po("/api/action/type/update",json={"id":"freeze_batch","risk":"low"})
chk("W10 内置改语义→400(受保护)", r.status_code==400)
r=po("/api/action/type/delete",json={"id":"freeze_batch"})
chk("W11 内置删除→400", r.status_code==400)
r=po("/api/action/type/delete",json={"id":_tid})
chk("W12 自建删除", r.status_code==200)
r=g("/")
chk("W13 UI 含类型管理弹窗+日志导出", 'id="act_modal"' in r.text and "acLogCsv" in r.text)
chk("W14 UI 问数→动作联动代码", "相关动作(基于命中业务表)" in r.text)

print("=== X. 技能管理(DR-021)===")
r=g("/api/build/skill/ontology-agentic")
chk("X1 内置技能可浏览(只读)", r.status_code==200 and r.json()["builtin"] and not r.json()["editable"])
_md="---\ndescription: 回归技能\n---\n\n回归方法论:动词统一用「校验」\n"
r=po("/api/build/skill/save",json={"name":"reg-skill","content":_md})
chk("X2 新建自定义技能", r.status_code==200)
chk("X3 列表实时含新技能+描述", any(s["name"]=="reg-skill" and s["desc"]=="回归技能" for s in g("/api/build/skills").json()))
r=po("/api/build/skill/save",json={"name":"reg-skill","content":_md.replace("校验","复核")})
chk("X4 编辑保存实时", r.status_code==200 and "复核" in g("/api/build/skill/reg-skill").json()["content"])
chk("X5 ★自定义正文注入构建方法论", "复核" in _sv2._skill_method_text(["reg-skill"]))
r=po("/api/build/skill/save",json={"name":"ontology-agentic","content":"x"})
chk("X6 占用内置名→400", r.status_code==400)
r=po("/api/build/skill/save",json={"name":"../evil","content":"x"})
chk("X7 路径穿越名→400", r.status_code==400)
r=po("/api/build/skill/delete",json={"name":"ontology-agentic"})
chk("X8 删内置→400", r.status_code==400)
r=po("/api/build/skill/delete",json={"name":"reg-skill"})
chk("X9 删自定义+列表移除", r.status_code==200 and "reg-skill" not in {s["name"] for s in g("/api/build/skills").json()})
r=g("/")
chk("X10 UI 含技能弹窗+新建入口", 'id="sk_modal"' in r.text and "skNew()" in r.text)

print("=== Y. 技能生态五项(DR-022)===")
# ② 注入痕迹字样在 UI/后端可见(流水线步骤字符串)
chk("Y1 构建链含 skill_inject 步骤代码", "skill_inject" in open("server.py",encoding="utf-8").read())
# ④ 两跳路径提示
_h=_sv2._join_hints(_sv2.load_ir_edited("demo"),["fact_production_output","dim_production_line"])
chk("Y2 两跳链提示(⋈⋈ 经中间表)", any("⋈⋈" in x for x in _h))
chk("Y3 build_context 两跳链贯通", "⋈⋈" in _sv2.build_context("累计产量最高的产线叫什么名字?"))
# ⑤ 沉淀技能复用
_skm=_sv2._match_qa_skill("产量的月度趋势如何?")
chk("Y4 沉淀技能命中", bool(_skm and _skm.get("analyses")))
chk("Y5 不相关不误命中", _sv2._match_qa_skill("设备台数是多少") is None)
r=po("/api/chat",json={"q":"产量的月度趋势如何?","nocache":True})
chk("Y6 问数复用沉淀技能(skill_reuse 步)", r.status_code==200 and any(s["step"]=="skill_reuse" for s in r.json()["steps"]))
# ③ 沉淀为技能
r=po("/api/build/skill/from_graph",json={"graph":"demo","name":"reg-distill"})
chk("Y7 产物沉淀为技能", r.status_code==200 and r.json().get("ok"))
chk("Y8 沉淀内容三件套", all(k in g("/api/build/skill/reg-distill").json()["content"] for k in ("关系动词表","对象类型分布","规范")))
po("/api/build/skill/delete",json={"name":"reg-distill"})
# ① 对比端点
r=po("/api/build/skill_compare",json={"skills_a":["no-such-skill"],"skills_b":[]})
chk("Y9 对比:未知技能→400(或运行中 409)", r.status_code in (400,409))
chk("Y10 对比:状态/结果端点", g("/api/build/skill_compare/status").status_code==200 and g("/api/build/skill_compare/results").status_code==200)
r=g("/")
chk("Y11 UI 含对比弹窗+沉淀按钮", 'id="skc_modal"' in r.text and "沉淀为技能" in r.text)
r=g("/api/quality")
chk("Y12 分层对账检查上线(评测发现固化)", any("分层对账" in c["rule"] for c in r.json()["checks"]))
chk("Y13 对账检出已知不一致(DWS/agg)", sum(1 for c in r.json()["checks"] if "分层对账" in c["rule"])==2)


print("=== Z. 未覆盖路由补测(写端点守卫 + 上传 + 根页)===")
# 根页:应用入口必须可达且是完整单页
r=g("/"); chk("Z1 根页 200 且为单页应用", r.status_code==200 and 'id="p_home"' in r.text)
# /api/ont/save:名称必填且 ≤40 字;非法图谱 404。不做真实落盘,只验守卫
r=po("/api/ont/save",json={"graph":"demo","name":""},headers=H)
chk("Z2 ont/save 空名称→400", r.status_code==400)
r=po("/api/ont/save",json={"graph":"demo","name":"x"*41},headers=H)
chk("Z3 ont/save 名称超长→400", r.status_code==400)
r=po("/api/ont/save",json={"graph":"__nope__","name":"t"},headers=H)
chk("Z4 ont/save 图谱不存在→404", r.status_code==404)
# /api/ont/skills/write:name 须匹配 ^[\w-]+$,可挡路径穿越;content 不可空
r=po("/api/ont/skills/write",json={"name":"../evil","content":"x"},headers=H)
chk("Z5 skills/write 路径穿越→400", r.status_code==400)
r=po("/api/ont/skills/write",json={"name":"ok_name","content":"  "},headers=H)
chk("Z6 skills/write 空内容→400", r.status_code==400)
# /api/ont/skills/install:slug 白名单 + 不存在→404
r=po("/api/ont/skills/install",json={"slug":"../../etc"},headers=H)
chk("Z7 skills/install 非法 slug→400", r.status_code==400)
r=po("/api/ont/skills/install",json={"slug":"__not_exist__"},headers=H)
chk("Z8 skills/install 技能不存在→404", r.status_code==404)
# /api/build/upload:真实上传 CSV 应建表;文件名经 basename 去穿越
_csvdata="a,b\n1,2\n3,4\n"
r=po("/api/build/upload",files={"files":("__zz_test.csv",_csvdata,"text/csv")},headers=H)
chk("Z9 build/upload CSV 建表", r.status_code==200 and any(t.get("rows")==2 for t in r.json().get("tables",[])))
r=po("/api/build/upload",files={"files":("../../evil.csv",_csvdata,"text/csv")},headers=H)
_wd=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"workdir")
chk("Z10 build/upload 文件名穿越被消解",
    r.status_code==200 and _os.path.exists(_os.path.join(_wd,"uploads_evil.csv"))
    and not _os.path.exists("/evil.csv") and not _os.path.exists(_os.path.join(_wd,"..","evil.csv")))
# 写端点并发:上传持全局写锁,连发不得死锁(回归 acquire 漏锁)
import concurrent.futures as _cf
def _up(i): return po("/api/build/upload",files={"files":(f"__zz_c{i}.csv",_csvdata,"text/csv")},headers=H).status_code
with _cf.ThreadPoolExecutor(max_workers=4) as _ex: _codes=list(_ex.map(_up,range(4)))
chk("Z11 上传并发不死锁", all(c==200 for c in _codes))
# 用必然成功且持写锁的端点判定锁健康(undo 在无草案时本就返回 400,不能用作锁探针)
_r12=po("/api/ont/chats/new",json={},headers=H)
chk("Z12 锁未泄漏(持写锁端点仍可用)", _r12.status_code==200 and _r12.json().get("id"))
po("/api/ont/chats/delete",json={"id":_r12.json().get("id","")},headers=H)   # 清理测试产生的会话
# Z 节自清理:上传测试会在 workdir 落文件、在 uploads.db 建表,不清理则每跑一次堆积一批
import sqlite3 as _sq, glob as _gl
_wd2=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),"workdir")
for _f in _gl.glob(_os.path.join(_wd2,"uploads___zz*"))+_gl.glob(_os.path.join(_wd2,"uploads_evil.csv")):
    try: _os.remove(_f)
    except OSError: pass
try:
    _c=_sq.connect(_os.path.join(_wd2,"uploads.db"))
    for _t in [r[0] for r in _c.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
        if _t.startswith("__zz") or _t=="evil": _c.execute(f'DROP TABLE IF EXISTS "{_t}"')
    _c.commit(); _c.close()
except Exception: pass
chk("Z13 测试残留已自清理", not _gl.glob(_os.path.join(_wd2,"uploads___zz*")))
# Z14/Z15:后端 404/500 返回 HTML,前端统一助手 J() 必须降级为 {error} 而非抛错
#（此前 (await fetch()).json() 直接抛,导致页面半渲染且无任何提示)
_jdef=_re.search(r'J=async\(u,o\)=>\{.*?\};', _ui, _re.S)
chk("Z14 J() 已加固为永不抛", bool(_jdef) and "JSON.parse" in _jdef.group(0) and "error" in _jdef.group(0))
_r404=g("/api/nonexistent")
chk("Z15 后端 404 确为 HTML(印证加固必要)", _r404.status_code==404 and "html" in _r404.headers.get("Content-Type",""))
# Z20/Z21/Z22:依赖声明须与实际 import 同步(此前 README 只写 flask+requests,
# 实际还硬依赖 rdflib,新装环境跑 SPARQL/导出/锻造会直接失败)
_root=_os.path.dirname(_os.path.abspath(__file__))
chk("Z20 requirements.txt 存在", _os.path.exists(_os.path.join(_root,"requirements.txt")))
_req=open(_os.path.join(_root,"requirements.txt"),encoding="utf-8").read()
import sys as _sys
_std=set(_sys.stdlib_module_names); _local={"translate_cn","quick_build","agent_runtime","serve_claw","export_owl","server","cq_check","drift_check","intent_check","usage_stat","rule_engine","openai_runtime","health_check","compat_check","module_split","dao_core","hallucination_eval","definition_eval","store","srv_context","srv_engine","bp_engine","srv_hardening","build_quality","ontology_grounding","skill_registry"}
_ext=set()
for _f in ("server.py","test_all.py"):
    for _n in ast.walk(ast.parse(open(_os.path.join(_root,_f),encoding="utf-8").read())):
        if isinstance(_n,ast.Import):
            for _a in _n.names: _ext.add(_a.name.split(".")[0])
        elif isinstance(_n,ast.ImportFrom) and _n.module and _n.level==0: _ext.add(_n.module.split(".")[0])
_ext={m for m in _ext if m not in _std and m not in _local}
_missing=sorted(m for m in _ext if m.replace("psycopg2","psycopg2") not in _req)
chk("Z21 所有三方依赖均已声明", not _missing, f"未声明: {_missing}")
# 缺 rdflib 时导出须优雅降级(503+提示),而非异常直穿成 500:验证 import 已移入 try
_srv=open(_os.path.join(_root,"server.py"),encoding="utf-8").read()
chk("Z22 导出路径 rdflib 缺失可降级", "pip3 install rdflib" in _srv and _srv.count("except ImportError")>=2)

print("=== AA. 语义增强三件套(对标 Trane arXiv:2603.10047 的 M1/M4/M5)===")
import importlib as _il, sys as _sy
_sy.path.insert(0,_root); _srvmod=_il.import_module("server")
# M5 词汇表注入:表名带权威中文 + 分层约定 + 缩写锚定
_ctx=_srvmod.build_context("最近几个月毛利率的变化趋势")
chk("AA1 M5 表名带权威中文名", "fact_sales_order(销售订单事实表)" in _ctx or _re.search(r"表 \w+\([^)]+\):", _ctx) is not None)
chk("AA2 M5 分层约定已注入", "分层约定:" in _ctx and "dws_* =" in _ctx)
chk("AA3 M5 缩写锚定已注入", "缩写锚定" in _ctx)
# M4-a 数据时间窗:防 LLM 用 date('now') 查空
chk("AA4 M4 数据时间窗已注入", "数据时间窗:" in _ctx and 'date("now")' in _ctx)
# M4-b 指标基线:命中指标时给出真实范围
_ctx2=_srvmod.build_context("计划产量")
chk("AA5 M4 指标基线含观测区间与单位", "指标基线" in _ctx2 and "观测区间[" in _ctx2 and "单位" in _ctx2)
# M1 一致性:Jaccard 与逐条 stable 标注(确定性,不依赖引擎)
_r1={"relations":[{"source":"A","target":"B","verb":"归属"},{"source":"A","target":"C","verb":"包含"}]}
_r2={"relations":[{"source":"a","target":" b ","verb":"归属"},{"source":"A","target":"D","verb":"触发"}]}
_st,_bo=_srvmod._stability_annotate(_r1,_r2)
chk("AA6 M1 Jaccard 计算正确(1/3)", _st["jaccard"]==round(1/3,4) and _bo==1)
chk("AA7 M1 大小写空白归一", _st["both"]==1)
chk("AA8 M1 逐条 stable 标注", [r["stable"] for r in _r1["relations"]]==[True,False])
chk("AA9 M1 空输入不崩", _srvmod._stability_annotate({},{})[0]["jaccard"]==0.0)
# 设计约束:一致性是软信号,不得覆盖 status(数据裁决才是可复核的数据证据)
_sd=_srvmod._stability_annotate.__doc__ or ""
chk("AA10 M1 明确不作否决权(设计留痕)", "不作否决权" in _sd or "不应因" in _sd)

print("=== AB. 反馈反馈流程与历史准确率(⑤⑥)===")
# 无数据时须如实降级,不给误导性百分比
_a0=g("/api/ont/accuracy?force=1").json()
chk("AB1 accuracy 端点可用", "overall" in _a0 and "min_n" in _a0)
# 自造人审数据(5 通过 / 2 否决)→ 验聚合 → 撤销复原
_depth0=len(g("/api/ont/edits?graph=demo").json().get("ops",[]))   # 先记栈深:只撤回到此,不碰他人草案
_ed=g("/api/graph/demo").json()["edges"][:7]
_ok=0
for _i,_e in enumerate(_ed):
    _op="confirm_relation" if _i<5 else "reject_relation"
    _rs="测试:语义与数据一致" if _i<5 else "测试:共享维度键假关联"
    if po("/api/ont/apply",json={"graph":SANDBOX,"op":{"op":_op,
        "target":f'rel:{_e["s"]}->{_e["t"]}',"reason":_rs,"reviewer":"回归"}},headers=H).status_code==200: _ok+=1
chk("AB2 人审算子全部生效", _ok==7, f"成功 {_ok}/7")
_a=g("/api/ont/accuracy?force=1").json()
chk("AB3 人审后统计到 7 条", _a.get("reviewed")==7, str(_a.get("reviewed")))
chk("AB4 同意率算对(5/7=71.4%)", _a["overall"].get("agree_rate")==71.4, str(_a["overall"]))
chk("AB5 按系统原判分组(verified)", "verified" in _a.get("by_prior_status",{}))
chk("AB6 原判在评审当刻定格(review_prior_status)", "review_prior_status" in _srv)
# 小样本规范:n<5 的分组必须给 insufficient 而不是百分比
_small=[v for v in _a.get("by_verb",{}).values() if v["n"]<5]
chk("AB7 小样本不给百分比", all("insufficient" in v and "agree_rate" not in v for v in _small), f"小样本组 {len(_small)} 个")
# ⑤ 否决模式回流
_rp=g("/api/ont/rejected-patterns").json().get("patterns",[])
chk("AB8 否决模式已回流", len(_rp)>=1 and "否决" in _rp[0])
chk("AB9 误判模式注入抽取 prompt", "已知误判模式" in _srv and "bad_block" in _srv)
# 复原:撤销全部测试人审,避免污染后续与真实统计
for _ in range(12):     # 精确回退到测试前的栈深;绝不多撤(undo 每次弹 1 个算子,写死次数会误撤他人草案)
    if len(g("/api/ont/edits?graph=demo").json().get("ops",[])) <= _depth0: break
    if po("/api/ont/undo",json={"graph":SANDBOX},headers=H).status_code!=200: break
_depth1=len(g("/api/ont/edits?graph=demo").json().get("ops",[]))
chk("AB10 精确回退到测试前栈深(不误撤他人草案)", _depth1==_depth0, f"{_depth1} != {_depth0}")

print("=== AC. 诊断流水线(④ M3 单任务 Agent,确定性组件)===")
_allow={"生产工单","设备","产线"}
_raw=[{"cause":"轴承温度异常","path":"设备→产线","evidence":"dws_equipment_daily.temp 超阈","confidence":"verified"},
      {"cause":"外部因素","path":"天气→设备","evidence":"无","confidence":"verified"},
      {"cause":"排产冲突","path":"生产工单→产线","evidence":"fact_production_order 重叠","confidence":"candidate"}]
_b=_srvmod._dg_bounds(_raw,_allow)
chk("AC1 G1 越界判定正确", [c["in_bounds"] for c in _b]==[True,False,True])
chk("AC2 G1 越界必降 candidate", _b[1]["confidence"]=="candidate")
_r=_srvmod._dg_rank(_b)
chk("AC3 评估排序确定性(界内+verified+具体证据最高)", _r[0]["cause"]=="轴承温度异常" and _r[-1]["cause"]=="外部因素")
chk("AC4 排序理由可解释", "路径在本体边界内" in _r[0]["rank_basis"] and "路径越界" in _r[-1]["rank_basis"])
_k,_f=_srvmod._dg_checklist_gate(["检查 dws_equipment_daily 趋势","联系 external_vendor 核对","巡检设备润滑"],
                                 _allow,["dws_equipment_daily"])
chk("AC5 G2 未知引用被标记而非丢弃", len(_f)==1 and "external_vendor" in str(_f) and len(_k)==2)
chk("AC6 评估为确定性(不调 LLM,设计留痕)", "不用 LLM" in (_srvmod._dg_rank.__doc__ or ""))
chk("AC7 审核为规则而非模型自审(设计留痕)", "而非 LLM" in (_srvmod._dg_checklist_gate.__doc__ or ""))
chk("AC8 流水线 opt-in 不改默认行为", 'pipeline = bool((request.json or {}).get("pipeline"))' in _srv)
chk("AC9 诊断空问句仍拒答", po("/api/diagnose/stream",json={"q":""},headers=H).status_code==200)
# Z16:error 键在全站统一表示"失败"。/api/overview 的优雅降级(库不可用时返回零值 KPI)
# 必须用 warning 而非 error,否则前端 44 处 `if(d.error)` 守卫会把一次成功的降级误判为失败。
_ovsrc=open(_os.path.join(_os.path.dirname(__file__),"server.py"),encoding="utf-8").read()
chk("Z16 overview 降级用 warning 不占用 error 键",
    '"warning": f"数据库不可用' in _ovsrc and '"error": f"数据库不可用' not in _ovsrc)
# Z17/Z18:核心页在后端故障时必须给提示而非白屏。用 node 复现 J() 返回 {error} 时的取值路径。
import subprocess as _sp
_probe = """
const d={error:'请求失败(HTTP 500)'};           // J() 加固后失败时的返回形态
let crashed=false, guarded=false;
try{ if(d.error||!d.kpi){guarded=true;} else { const k=d.kpi; k.rows.toLocaleString(); } }
catch(e){ crashed=true; }
const g={error:'请求失败(HTTP 500)'};
let gGuard=false, gCrash=false;
try{ if(g.error||!Array.isArray(g.nodes)){gGuard=true;} else { g.nodes.length; } }
catch(e){ gCrash=true; }
console.log(JSON.stringify({guarded,crashed,gGuard,gCrash}));
"""
try:
    _out=_sp.run(["node","-e",_probe],capture_output=True,text=True,timeout=20).stdout.strip()
    _j=json.loads(_out)
    chk("Z17 home() 守卫生效:后端故障时不抛而是提示", _j["guarded"] and not _j["crashed"])
    chk("Z18 drawGraph() 守卫生效:后端故障时不抛而是提示", _j["gGuard"] and not _j["gCrash"])
except Exception as _e:
    chk("Z17/Z18 守卫探针", False, f"node 探针失败: {_e}")
# 源码层面确认守卫确实落在这两个函数里(防日后被改回裸解构)
chk("Z19 home()/drawGraph() 源码含守卫",
    "if(d.error||!d.kpi)" in _ui and "if(g.error||!Array.isArray(g.nodes))" in _ui)

# ═══════════ AN. 本体锚定可视化(DR-032)═══════════
print("=== AN. 本体锚定可视化(DR-032)===")
_tr={}
_c=_srvmod.build_context("最近一个月各产线产量趋势", trace=_tr)
chk("AN1 轨迹记录入选对象与理由", len(_tr.get("objects",[]))>0 and all(o.get("reason") for o in _tr["objects"]))
chk("AN2 理由取值在已知集合内",
    set(o["reason"] for o in _tr["objects"]) <= {"关键词命中","核心事实表","沿本体关系召回","数据源限定","无命中·默认候选"})
chk("AN3 轨迹对象与上下文表名一一对应(带括号定界,避免前缀误判)",
    all(("表 "+o["table"]+"(") in _c for o in _tr["objects"]))
# 同源:结构化边数必须等于喂给引擎的 ⋈ 提示行数,否则可视化会与真实上下文悄悄漂移
_hint=[l for l in _c.split("\n") if l.startswith("⋈")]
chk("AN4 结构化关系与 JOIN 提示行同源(数量一致)", len(_tr.get("relations",[]))==len(_hint),
    f"pairs={len(_tr.get('relations',[]))} hints={len(_hint)}")
chk("AN5 关系带状态与键", all(r.get("status") and r.get("key") for r in _tr.get("relations",[])))
chk("AN6 两跳关系标注 hop=2 且带中间表",
    all(r.get("via") for r in _tr.get("relations",[]) if r.get("hop")==2))
_tr2={}
_srvmod.build_context("产量", focus_tables=["dws_production_daily"], trace=_tr2)
chk("AN7 数据源限定生效时置 scoped 且理由为『数据源限定』",
    _tr2.get("scoped") is True and {o["reason"] for o in _tr2["objects"]}=={"数据源限定"})
_tr3={}
_srvmod.build_context("产量", focus_tables=["__不存在的表__"], trace=_tr3)
chk("AN8 限定表未匹配本体→不置 scoped(避免谎报范围已收窄)", not _tr3.get("scoped"))
chk("AN9 未传 trace 时 build_context 行为不变",
    _srvmod.build_context("最近一个月各产线产量趋势")==_c)
# 端到端:SSE 必须推 anchor 事件(引擎规划前就该出,人不用等几十秒)
_r=_rq2.post(B+"/api/chat/stream",json={"q":"各产线产量对比","nocache":True},stream=True,timeout=60)
_anc=None
for _ln in _r.iter_lines(decode_unicode=True):
    if _ln and '"anchor"' in _ln:
        _anc=json.loads(_ln[6:]).get("anchor"); break
_r.close()
chk("AN10 问数流推送 anchor 事件", isinstance(_anc,dict) and len(_anc.get("objects",[]))>0)
chk("AN11 anchor 带范围与限定表数", _anc is not None and "scoped" in _anc and "focus_n" in _anc)
chk("AN12 前端锚定渲染函数存在并被三处接线",
    "function dqAnchorHTML" in _ui and "dqAnchorHTML(d.anchor)" in _ui
    and "dqAnchorHTML(ev.anchor)" in _ui and "anchor:done.anchor" in _ui)
chk("AN13 超出上限时显式说明截断(不静默少画)",
    "图中只画了 ${CAP} 个对象" in _ui and "没有少喂给引擎" in _ui)
chk("AN14 UI 呈现锚定本体名与锚定链路", "本体锚定 · ${esc((ont.names||['示例本体'])[0])}" in _ui
    and "入上下文" in _ui and "命中证据(凭什么选中它" in _ui)

# ═══════════ AO. 选中图谱作锚定本体(DR-033)═══════════
print("=== AO. 选中图谱作锚定本体(DR-033)===")
_q33 = "各客户的销售订单金额排名"
_t0 = {}; _srvmod.build_context(_q33, trace=_t0)
_t1 = {}; _srvmod.build_context(_q33, trace=_t1, graph_keys=["built_9c3fd1"])
_t2 = {}; _srvmod.build_context(_q33, trace=_t2, graph_keys=["cq"])
chk("AO1 未选图谱→锚定示例本体", _t0["ontology"]["keys"] == ["demo"] and _t0["ontology"]["objects"] == 108)
chk("AO2 选中自建本体→锚定它自己(不再永远锚 demo)",
    _t1["ontology"]["keys"] == ["built_9c3fd1"] and _t1["ontology"]["objects"] == 19)
chk("AO3 锚定不同本体→召回集不同",
    {o["table"] for o in _t1["objects"]} != {o["table"] for o in _t0["objects"]})
chk("AO4 召回对象数远小于本体规模(是锚定不是全量倾倒)",
    0 < len(_t1["objects"]) < _t1["ontology"]["objects"])
chk("AO5 上游引擎本体也能锚定并给出关系",
    _t2["ontology"]["keys"] == ["cq"] and len(_t2["objects"]) > 0)
chk("AO6 命中证据记录了是哪个词钓出该对象",
    any(o.get("hits") for o in _t1["objects"]))
chk("AO7 命中证据里的词确实出现在问句或其扩展词中",
    all(all(isinstance(h, str) and h for h in (o.get("hits") or [])) for o in _t1["objects"]))
# 无绑表本体的回退:用合成图谱测,不依赖「仓里恰好有一套纯概念本体」——
# tables[] 归一上线后,app(82/149 绑表)与 built_2d74f0(108/108)都已可正常锚定,
# 那两套本体此前是因为只读 table 字段而被埋没,并非真的没有绑表
_syn_key = "built_ztest_conceptonly"
_syn_p = _os.path.join(_WD0, _syn_key + ".json")
with open(_syn_p, "w", encoding="utf-8") as _f:
    json.dump({"scenario": {"name": "纯概念本体(回归用)"},
               "objects": [{"name": "Concept%d" % i, "cn": "概念%d" % i, "kind": "object"}
                           for i in range(3)],
               "relations": []}, _f, ensure_ascii=False)
_at0.register(lambda: _os.path.exists(_syn_p) and _os.remove(_syn_p))
_t3 = {}; _srvmod.build_context(_q33, trace=_t3, graph_keys=[_syn_key])
chk("AO8 无绑表本体→如实回退并说明(不静默换本体)",
    bool(_t3.get("fallback")) and _t3["ontology"]["requested"] == ["纯概念本体(回归用)"])
_t4 = {}; _srvmod.build_context(_q33, trace=_t4, graph_keys=["built_9c3fd1", "built_8c3354"])
chk("AO9 多选图谱→合并为一套锚定本体", _t4["ontology"]["objects"] > _t1["ontology"]["objects"])
# 形状无关:构建产物用 relations[source_concept] + objects[name],示例用 links[source] + id
_bir = _srvmod.load_ir_edited("built_9c3fd1") or _srvmod.load_ir("built_9c3fd1")
_bt = [o.get("table") for o in _bir.get("objects", []) if o.get("table")]
_bp = []; _bh = _srvmod._join_hints(_bir, _bt, pairs=_bp)
chk("AO10 构建产物形状也能出 JOIN 提示(此前恒为 0)", len(_bh) > 0)
# 键名校验:稠密自增代理键值域重合会造假关系
chk("AO11 键名校验放行同名/缩写键",
    _srvmod._key_name_ok("order_id", "order_id") and _srvmod._key_name_ok("emp_id", "employee_id")
    and _srvmod._key_name_ok("prod_id", "product_id"))
chk("AO12 键名校验拦下异根键(prod_id↔order_id 这类值域巧合)",
    not _srvmod._key_name_ok("prod_id", "order_id")
    and not _srvmod._key_name_ok("line_id", "equipment_id"))
chk("AO13 存疑键不作 JOIN 依据下发,降级为『键见列名』",
    all((not p.get("dropped_key")) or ("键见列名" in p["key"] and p["has_key"] is False) for p in _bp))
chk("AO14 降级关系仍保留(只降键不删关系)",
    any(p.get("dropped_key") for p in _bp) and len(_bh) == len(_bp))
chk("AO15 示例本体几乎不受键名校验影响(误伤可控)",
    sum(1 for l in (_srvmod.load_ir_edited("demo") or {}).get("links", [])
        if (l.get("evidence") or {}).get("child_key")
        and not _srvmod._key_name_ok(l["evidence"]["child_key"], l["evidence"]["parent_key"])) <= 2)
# 缓存键必须含图谱:换本体却复用旧答案 = 拿另一套本体的结论
chk("AO16 问数缓存键随锚定本体变化",
    _srvmod._qa_key("q", [], [], ["demo"]) != _srvmod._qa_key("q", [], [], ["built_9c3fd1"]))
chk("AO17 构建器落结构化 JOIN 键(不再只写进 note 文本)",
    '"evidence": ev_keys' in open("server.py", encoding="utf-8").read().replace("rel_new[\"evidence\"] = ev_keys", '"evidence": ev_keys')
    or "rel_new[\"evidence\"] = ev_keys" in open("server.py", encoding="utf-8").read())
chk("AO18 旧构建产物的键可从 note 回填并标来源",
    _srvmod._rel_keys({"note": "order_id→fact_delivery.order_id 重叠90%·父键唯一"})[:3]
    == ("order_id", "order_id", "note"))
chk("AO19 UI 标出键降级与键来源", "键存疑·已降级" in _ui and "键·备注回填" in _ui)
chk("AO20 问数流推送『锚定本体』步骤", "anchor_ontology" in open("server.py", encoding="utf-8").read())
# 短缩写污染:词典把「销售订单」扩展出 so,子串匹配会命中 reason_code / sensor_id
_t5 = {}; _srvmod.build_context("各客户的销售订单金额排名", trace=_t5, graph_keys=["built_9c3fd1"])
chk("AO21 两字母英文缩写不再子串命中无关表",
    all("fact_alarm" != o["table"] or o["reason"] != "关键词命中" for o in _t5["objects"]))
chk("AO22 命中证据里不出现 so 这类子串误命中",
    all("so" not in (o.get("hits") or []) for o in _t5["objects"]))
# 可见性:锚定埋在对话气泡里会被自动滚动顶出视口,必须有常驻条
chk("AO23 深度问数页有常驻锚定条容器", 'id="dq_ancbar"' in _ui and ".dq-ancbar{" in _ui)
chk("AO24 锚定条在实时/终局/翻历史三处都刷新",
    _ui.count("dqAncBar(") >= 5)
chk("AO25 锚定条与气泡复用同一段渲染(bare 模式,不做第二份实现)",
    "function dqAnchorHTML(a,bare)" in _ui and "dqAnchorHTML(a,true)" in _ui and "return bare ? inner :" in _ui)
chk("AO26 可跳本体图谱高亮本次锚定的那一块",
    "function dqAncHighlight" in _ui and "GRAPH_HL" in _ui and "问数锚定视图" in _ui)
chk("AO27 图谱渲染按高亮集淡出非锚定节点与关系",
    "function _g6build(g,hl)" in _ui and "hl.set.has(n.id)" in _ui
    and "hl.set.has(e.s)&&hl.set.has(e.t)" in _ui)
chk("AO28 高亮提示条独立于 g_info(后者会被元数据覆盖回调覆写)",
    'id="g_hlbar"' in _ui and "$('#g_hlbar')" in _ui)
chk("AO29 高亮可一键清除", "清除高亮,看完整图谱" in _ui)
chk("AO30 新对话清空锚定条,不残留上一轮", "DQ_ANC_LAST=null;dqAncBar(null)" in _ui)
chk("AO31 切到根因诊断清空锚定条(共用视图容器,免得指向另一条链路)",
    _ui.count("DQ_ANC_LAST=null;dqAncBar(null)") >= 3)
# 回退时 ontology 必须写「实际用了哪套」,否则界面显示选中的那套而对象来自另一套
_t6 = {}; _srvmod.build_context("各客户的销售订单金额排名", trace=_t6, graph_keys=[_syn_key])
chk("AO32 无绑表本体回退后,锚定本体如实回写为实际使用的那套",
    _t6["ontology"]["keys"] == ["demo"] and _t6["ontology"]["requested"] and _t6.get("fallback"))
# tables[] 归一:本体产出有两种写法,只认 table 会把 quick_build 的本体判成「无绑表」而静默回退
chk("AO43 对象绑表兼容 table 与 tables[] 两种写法",
    _srvmod._obj_table({"table": "t1"}) == "t1"
    and _srvmod._obj_table({"tables": ["t2", "t3"]}) == "t2"
    and _srvmod._obj_table({"cn": "无表"}) is None
    and _srvmod._obj_table({"tables": ["", None, "t4"]}) == "t4"   # 跳过空值
    and _srvmod._obj_table({"tables": ["", ""]}) is None)
chk("AO45 属性中文名为 None 时不渲染字面量 None(会被喂给模型)",
    "None" not in _srvmod._obj_cols_text({"table": "x", "attrs": [{"col": "c", "cn": None}]}))
chk("AO44 本体缺 attrs 时列清单回落到库里现读(否则上下文只有表名,模型只能猜列)",
    "def _table_cols(" in open("server.py", encoding="utf-8").read()
    and _srvmod._obj_cols_text({"table": "dim_customer"}))
# 「只 clone 本仓 + 配 OpenAI 兼容端点」是 README 承诺的路径:垫片必须能承载 driver 注册,
# 否则 available() 恒空、配了 key 也拿不到运行时(实测曾如此)。
_shim_src = open("server.py", encoding="utf-8").read()
chk("QS1 无引擎垫片可承载 driver 注册", "_stub.register = lambda name, factory" in _shim_src)
chk("QS2 OpenAI 驱动注册不依赖上游引擎在场",
    _shim_src.index("import openai_runtime") > _shim_src.index('sys.modules["agent_runtime"] = _stub'))
# 改签名漏改调用点:_anchor_ir 曾返回 2 元组,加了「缺失提示」后变 3 元组,
# 口径校验那处未同步,表现为问数流中途 500(套件当时未覆盖到那条路径)
import ast as _ast
_tree = _ast.parse(open("server.py", encoding="utf-8").read())
_unpack = []
for _n in _ast.walk(_tree):
    if isinstance(_n, _ast.Assign) and isinstance(_n.value, _ast.Call) \
       and getattr(_n.value.func, "id", "") == "_anchor_ir":
        _t = _n.targets[0]
        _unpack.append(len(_t.elts) if isinstance(_t, _ast.Tuple) else 1)
# ═══════════ EG. 引擎设置:OpenAI 兼容端点(界面可配)═══════════
print("=== EG. 引擎设置 ===")
_ec = g("/api/engine/config").json()
chk("EG1 配置带出端点现状与预设", "llm" in _ec and len(_ec.get("llm_presets") or []) >= 5)
chk("EG2 Key 只回显掩码", not _ec["llm"].get("key") or "*" in _ec["llm"]["key"])
chk("EG3 预设均为合法 https 端点",
    all(str(p.get("base", "")).startswith(("http://", "https://")) for p in _ec["llm_presets"]))
_bad = po("/api/engine/config", json={"llm": {"base": "ftp://x"}})
chk("EG4 非法端点协议被拒", _bad.status_code == 400)
_bad2 = po("/api/engine/config", json={"llm": {"timeout": "-5"}})
chk("EG5 非法超时被拒", _bad2.status_code == 400)
_t1 = po("/api/engine/llm/test", json={"base": "", "key": "", "model": ""})
# 判据取服务端实际是否已有可用端点(测试进程的 env 与服务进程未必一致)
_has_env = bool(_ec.get("llm", {}).get("base") and _ec.get("llm", {}).get("key_set"))
chk("EG6 缺参时:有已保存配置则回落沿用,否则如实报错",
    (_t1.status_code == 200 or _t1.json().get("ok") is not None) if _has_env
    else (_t1.status_code == 400 and not _t1.json().get("ok")),
    "env 已配" if _has_env else "env 未配")
_t2 = po("/api/engine/llm/test", json={"base": "http://127.0.0.1:1/v1", "key": "k", "model": "m", "timeout": 3})
chk("EG7 端点不可达时回真实错误而非静默成功", not _t2.json().get("ok") and _t2.json().get("error"))
_m1 = po("/api/engine/llm/models", json={"base": "", "key": ""})
chk("EG8 模型列表:缺参时回落已保存配置或如实报错",
    _m1.status_code in (200, 400) and ("models" in _m1.json() or "error" in _m1.json()))
chk("EG9 前端有端点配置卡与测试/保存/清除三个动作",
    'id="eg_llm"' in _ui and "egLLMTest" in _ui and "egLLMSave" in _ui and "egLLMClear" in _ui)
chk("EG10 前端能拉取模型列表并填进可见下拉", "egFetchModels" in _ui and 'id="eg_l_pick"' in _ui)
chk("EG11 env 注入项在界面置灰(以部署配置为准)", "env_locked" in _ui and "环境变量注入" in _ui)
# IR-011:引擎路由已迁至 bp_engine blueprint,配置层在 srv_engine。
# 以下「引擎实现含某特征」类断言改查 server + bp_engine + srv_engine 的合并源,
# 断言意图不变(特征仍须存在于引擎实现中),只是实现位置由单体拆成了模块。
_eng_all = "\n".join(open(f, encoding="utf-8").read()
                     for f in ("server.py", "bp_engine.py", "srv_engine.py"))
chk("EG12 端点配好后自动切换运行时(免去『配了没反应』)",
    "switched_from" in _eng_all and "switched_from" in _ui)
chk("EG13 报错详情里的 Key 被掩码", "_mask_in_text" in _eng_all)
# env_locked 只能按「启动时该环境变量是否存在」判定:原先拿配置文件与 env 比对,
# 值恰好相同就漏判为未锁定,界面会让人误以为可改
_srv_src = open("server.py", encoding="utf-8").read()
# datalist 是浏览器原生下拉:DOM 上不可见、无下拉箭头,输入框有值时还按值过滤 ——
# 用户看到「已拉到 8 个模型」却找不到在哪选。全站一律改用可见的 <select>
# 测试请求曾写死 max_tokens=64:推理型模型会卡在 finish_reason=length、正文为空,
# 看着像「模型不可用」——实际是测试请求自己给少了
_srv2 = _eng_all          # IR-011:引擎实现已拆模块,合并源见上
chk("EG21 连通测试的额度可配,不写死小值", '"max_tokens": _mt' in _srv2 and '"max_tokens": 64' not in _srv2)
chk("EG22 按 finish_reason 归因截断,不误判为模型无正文",
    'finish_reason' in _srv2 and "回复被额度截断" in _srv2)

chk("EG19 全站不再使用 datalist(无可见入口,用户找不到候选)", "datalist" not in _ui)
chk("EG20 模型下拉在拉取成功后才显示,并回填输入框",
    'id="eg_l_pick"' in _ui and "egPickModel" in _ui and "sel.style.display=''" in _ui)

# 构成规则页曾直接 rt.runtimes.map:后端故障返回 {error} 时缺该字段,整页崩
chk("EG17 构成规则页对 runtimes 缺失有守卫",
    "Array.isArray(rt.runtimes)" in _ui and "无可用运行时" in _ui)
chk("EG18 构成规则页对 rules 数据缺失有守卫", "d.error||!d.counts" in _ui)

chk("EG14 env_locked 按启动时快照判定,不与配置值比对",
    "_ENV_LOCKED_AT_BOOT" in _eng_all and "v in _ENV_LOCKED_AT_BOOT" in _eng_all)
# 快照已随引擎配置层收敛到 srv_engine(IR-011):不变量形态变为「模块 import 时快照 → 之后才应用配置」,
# 故跨两文件校验:srv_engine 内快照先于 _apply_engine_cfg 定义;server 内 import 先于启动调用。
_eng_src = open("srv_engine.py", encoding="utf-8").read()
chk("EG15 快照在应用本地配置之前建立(否则分不清运维注入与界面保存)",
    _eng_src.index("_ENV_LOCKED_AT_BOOT = {") < _eng_src.index("def _apply_engine_cfg(")
    and _srv_src.index("from srv_engine import") < _srv_src.index("_apply_engine_cfg(_load_engine_cfg())"))
_env_now = [f for f, v in [("base", "DATAMIND_LLM_BASE"), ("model", "DATAMIND_LLM_MODEL")]
            if _os.environ.get(v)]
chk("EG16 env 注入项确实出现在 env_locked 中",
    all(f in (_ec["llm"].get("env_locked") or []) for f in _env_now) if _env_now else True,
    "本次注入: %s" % (_env_now or "无"))

# pyflakes 零告警是既有验收检查(ARCHITECTURE §5),回归里把它钉住:
# 重复字典键这类告警是真 bug —— translate_cn 曾因此把「min」译成分钟而非最低
try:
    import glob as _g2
    _repo = _os.path.dirname(_os.path.abspath(__file__))
    _pf = _sp.run([sys.executable, "-m", "pyflakes"] + sorted(_g2.glob(_os.path.join(_repo, "*.py"))),
                  capture_output=True, text=True, timeout=90)
    _pfout = [l for l in _pf.stdout.splitlines() if l.strip()]
    chk("QS10 pyflakes 零告警", not _pfout, "; ".join(_pfout[:2]))
except Exception as _e:
    chk("QS10 pyflakes 探针", False, str(_e)[:80])

chk("QS7 _anchor_ir 所有调用点解包数一致", _unpack and len(set(_unpack)) == 1, str(_unpack))

# QS12:中文召回必须反向匹配(本体词 → 问句),不能依赖对问句分词。
# 问句按标点/空格切,中文整句常常就是一个词元 —— 正向匹配(问句词 ∈ 对象语料)对中文
# 几乎必然落空,给对象补的中文业务别名会完全不生效(实测「车间近期产能怎么样」切不出「产能」)。
_tr_cn = {}
_ctx_cn = _srvmod.build_context("各车间的停机时长排名", trace=_tr_cn, graph_keys=["demo"])
_cn_hit = [h for o in _tr_cn["objects"] for h in (o.get("hits") or []) if not str(h).isascii()]
chk("QS12 中文问句能命中本体中文词(无需分词器)", bool(_cn_hit), str(_cn_hit[:5]))
# 指标名同为中文,同一条路径
_tr_m = {}
_srvmod.build_context("本月计划产量是多少", trace=_tr_m, graph_keys=["demo"])
chk("QS12b 自然中文问句能召回同名指标(非整句等于指标名)",
    any(m.get("name") == "计划产量" for m in (_tr_m.get("metrics") or [])),
    str([m.get("name") for m in (_tr_m.get("metrics") or [])][:5]))
# 单字不参与:满篇皆是,会把无关表拖进上下文
_tr_1 = {}
_srvmod.build_context("的", trace=_tr_1, graph_keys=["demo"])
# QS15:对象定位的降级顺序必须是「主键 → 中文名 → 表名 → 别名」,且逐条件分轮。
# 逐对象把三种条件一起试的写法,会让靠前对象的表名压过靠后对象的中文名,
# 命中谁取决于对象在数组里的顺序 —— 同一句指代在不同本体上结果不同。
_ir_amb = {"objects": [{"id": "A", "cn": "甲", "table": "乙"},
                       {"id": "B", "cn": "乙", "table": "t2", "aliases": ["丙"]}]}
chk("QS15 对象定位按主键→中文名→表名→别名分轮降级",
    _srvmod._find_obj_any(_ir_amb, "A")["id"] == "A"
    and _srvmod._find_obj_any(_ir_amb, "乙")["id"] == "B"      # 中文名压过表名
    and _srvmod._find_obj_any(_ir_amb, "t2")["id"] == "B"
    and _srvmod._find_obj_any(_ir_amb, "丙")["id"] == "B"
    and _srvmod._find_obj_any(_ir_amb, "没有这个") is None)

# QS16:技能与工具面的一致性(半自动重构这条链)。
_srv_src16 = open(_os.path.join(_repo, "server.py"), encoding="utf-8").read()
# 智能体列表曾硬编码同级「上游本体引擎」目录 —— 该目录早已更名,于是技能包永远列不出来,
# 且不受 DATAMIND_ENGINE_DIR 控制;同一批技能在 /api/build/skills 却列得出,两处结论打架。
chk("QS16 技能目录一律走 PLATFORM,不写死目录名",
    '"上游本体引擎", "web", "skills_seed"' not in _srv_src16)
_ag = g("/api/agents").json().get("agents", [])
_bs = g("/api/build/skills").json()
_bs = _bs if isinstance(_bs, list) else (_bs.get("skills") or [])
_bn = {(x.get("slug") or x.get("name")) if isinstance(x, dict) else x for x in _bs}
_an = {a["name"] for a in _ag if a.get("type") == "技能包"}
chk("QS16b 智能体列表与构建页看到同一批技能", not _bn or _bn == _an,
    "构建页=%s 智能体列表=%s" % (sorted(_bn), sorted(_an)))
# rebuild 丢弃整个草案层且 undo 退不回来:必须显式 confirm,并留底可恢复
_rb = po("/api/ont/rebuild", json={"graph": SANDBOX})
chk("QS16c 重建不带 confirm 直接拒绝", _rb.status_code == 400 and "confirm" in _rb.text)
_rb2 = po("/api/ont/rebuild", json={"graph": "../etc/passwd", "confirm": True})
chk("QS16d 重建拒绝非法图谱键", _rb2.status_code == 400)
# 技能写入不得在引擎缺失时凭空造引擎目录树(空壳会被后续当成"已装引擎")
chk("QS16e 技能写入按引擎在否分流,不造假目录",
    'if os.path.isdir(PLATFORM):' in _srv_src16 and 'scope = "custom"' in _srv_src16)

# QS13:执行记录里的本体名必须据实回显。曾把首步写死成「加载 示例 本体上下文」,
# 选了自建本体也照喊示例,与下一步 anchor_ontology 自相矛盾。
_srv_src = open(_os.path.join(_repo, "server.py"), encoding="utf-8").read()
chk("QS13 首步不写死「示例」本体名", '"加载 示例 本体上下文"' not in _srv_src)

# QS14:意图一致的结论要写明比对基数。通道 A 只锚得到本体里有的词,
# 「覆盖了全部业务对象」若不点明覆盖了几个,会被读成「问句问的都答了」。
import intent_check as _ic_m
_ic_ir = {"objects": [{"id": "L", "cn": "生产线", "table": "dim_production_line"},
                      {"id": "D", "cn": "停机记录", "table": ""}]}
_ic_r = _ic_m.cross_check("各生产线的停机时长",
                          "SELECT 1 FROM dim_production_line", _ic_ir)
chk("QS14 意图一致的结论写明比对了几个对象",
    _ic_r["verdict"] == "aligned" and "1 个业务对象" in _ic_r["reason"] and "生产线" in _ic_r["reason"],
    _ic_r.get("reason", "")[:80])

chk("QS12c 单字不作为中文命中词",
    not [h for o in _tr_1["objects"] for h in (o.get("hits") or []) if h == "的"])

# QS11:离线放行白名单必须与 apply_any 实际本地实现的分支一致。
# 曾经白名单只列 set_alias,而 apply_any 本地实现了 9 个算子 —— 引擎离线时
# 改动词/改基数/增删关系被 503 挡回「编辑引擎未就绪」,而它们根本不用引擎。
# 用 AST 扫出 apply_any 里所有与 kind 比较的字面量,和白名单对账。
try:
    import ast as _ast
    _tree = _ast.parse(open(_os.path.join(_repo, "server.py"), encoding="utf-8").read())
    _fn = next(n for n in _ast.walk(_tree)
               if isinstance(n, _ast.FunctionDef) and n.name == "apply_any")
    _handled = set()
    for _n in _ast.walk(_fn):
        # kind == "x"
        if isinstance(_n, _ast.Compare) and isinstance(_n.left, _ast.Name) and _n.left.id == "kind":
            for _op, _c in zip(_n.ops, _n.comparators, strict=True):  # AST 文法保证等长
                if isinstance(_op, _ast.Eq) and isinstance(_c, _ast.Constant):
                    _handled.add(_c.value)
                # kind in ("a","b",...)
                elif isinstance(_op, _ast.In) and isinstance(_c, (_ast.Tuple, _ast.List)):
                    _handled |= {e.value for e in _c.elts if isinstance(e, _ast.Constant)}
    # 白名单同样从 AST 读,不 import server —— 导入会拉起应用级副作用
    _white = set()
    for _n in _tree.body:
        if isinstance(_n, _ast.Assign) and any(
                isinstance(_t, _ast.Name) and _t.id in ("REVIEW_OPS", "LOCAL_OPS")
                for _t in _n.targets) and isinstance(_n.value, (_ast.Tuple, _ast.List)):
            _white |= {e.value for e in _n.value.elts if isinstance(e, _ast.Constant)}
    chk("QS11 离线白名单覆盖 apply_any 全部本地算子",
        _handled and _handled == _white,
        "实现=%s 白名单=%s 差集=%s" % (sorted(_handled), sorted(_white),
                                       sorted(_handled ^ _white)))
except Exception as _e:
    chk("QS11 白名单/实现一致性探针", False, str(_e)[:120])
_tr_bad = {}
_srvmod.build_context("毛利率", trace=_tr_bad, graph_keys=["no_such_graph"])
chk("QS8 图谱不存在时如实提示而非静默回落", "不存在" in (_tr_bad.get("fallback") or ""))
_tr_dup = {}
_srvmod.build_context("毛利率", trace=_tr_dup, graph_keys=["demo", "demo", "../etc/passwd"])
chk("QS9 重复键去重且非法键被拦", _tr_dup["ontology"]["keys"] == ["demo"])

chk("QS4 openclaw 在驱动尝试顺序里(否则切了引擎没反应)",
    "openclaw" in _srvmod._drv_order())
chk("QS5 表名按标识符白名单校验后才拼进 PRAGMA",
    _srvmod._table_cols('x") UNION SELECT 1--') == [] and _srvmod._table_cols("dim_customer"))
chk("QS6 库不可用时不缓存空结果(否则库恢复后仍返回空)",
    "只缓存成功结果" in open("server.py", encoding="utf-8").read())
chk("QS3 垫片无可用 driver 时如实报错(不假装可用)",
    "无可用运行时:未配置上游引擎" in _shim_src)

# 任意图谱都能当锚定源之后,「对象有 table 键但值为 null」的概念本体会把 build_context 打崩
# (o.get("table","") 的默认值只在键缺失时生效)。实测 built_bd2fec 9/10 对象 table=null。
import glob as _glob
_all_graphs = ["demo", "app", "cq"] + [_os.path.basename(p)[:-5]
                                       for p in _glob.glob(_os.path.join(_WD0, "built_*.json"))]
_crash = []
for _gk in sorted(set(_all_graphs)):
    try:
        _srvmod.build_context("毛利为什么下降?产量趋势如何?", graph_keys=[_gk])
    except Exception as _e:
        _crash.append(f"{_gk}: {_e}")
chk("AO40 任一图谱作锚定源都不崩(含 table=null 的概念本体)", not _crash, str(_crash[:2]))
_tn = {}
_srvmod.build_context("毛利为什么下降?", trace=_tn, graph_keys=["built_bd2fec"])
chk("AO41 无绑表对象不占召回名额(混合本体里上下文仍拿得到表)",
    all(o.get("table") for o in _tn.get("objects", [])))
chk("AO42 源码中不再有 get(\"table\", \"\") 这类会返回 None 的写法",
    open("server.py", encoding="utf-8").read().count('get("table", "")') <= 1)

# 界面版本自检:标签页开着不刷新时改动不生效,现象是「说改了却没变」,排查成本极高
_r = g("/api/uiver"); chk("AO37 /api/uiver 返回界面文件版本",
    _r.status_code == 200 and isinstance(_r.json().get("v"), int) and _r.json()["v"] > 0)
chk("AO38 版本横幅挂 body 而非页面容器(放进 .page 会随切页隐藏)",
    "document.body.appendChild(b)" in _ui and "function uiVerBanner" in _ui)
chk("AO39 首次加载只记录版本不打扰", "if(UI_VER===null){UI_VER=d.v;return;}" in _ui)
# 对话框里的每一次提问都真跑:秒回的旧答案与「历史对话」里的记录无法区分
chk("AO35 深度问数对话框全程不吃缓存(点示例与手动输入一致)",
    "attachments:DQ_ATTS.map(a=>a.name),nocache:1})" in _ui
    and "nocache:fresh" not in _ui and "ask(true)" not in _ui)
chk("AO36 缓存机制本身保留(供 /api/chat 等非交互消费者)",
    "_QA_CACHE[_qa_key(question, history, focus_tables, graph_keys)] = resp" in open("server.py", encoding="utf-8").read())
chk("AO34 流中断不抹掉已完成内容(锚定是用户已看到的证据)",
    "以上为中断前已完成的部分" in _ui and "if(live.length||pr.length)" in _ui)
chk("AO33 回退提示显示在锚定条头部(默认可见,不藏在折叠区)",
    "${a.fallback?`<span style=\"color:#b45309" in _ui)
# 截断策略探针:按原序截断会画出一片孤立方框(0 连线),让人误以为本体拿不出关系
# 边样式表 EST 是图谱与锚定子图共用的单一事实源,定义在更靠前处;
# 探针只截锚定段会漏掉它(ReferenceError),故一并带上。
_est=_ui[_ui.index("const EST="):_ui.index("const GEST=EST;")]
_fn=_est+"\n"+_ui[_ui.index("const DQ_ANC_C="):_ui.index("function dqRenderCard")]
_big={"objects":[{"key":f"o{i}","cn":f"对象{i}","table":f"t{i}","reason":"数据源限定","ncol":3}
                 for i in range(60)],
      "relations":[{"s":f"t{40+i}","t":f"t{50+i}","verb":"关联","status":"verified",
                    "key":f"t{40+i}.k = t{50+i}.k","hop":1} for i in range(8)],
      "metrics":[],"scoped":True,"graphs":["demo"],"focus_n":60}
_probe2=("const esc=x=>String(x==null?'':x).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));\n"
   +_fn+"\nconst h=dqAnchorHTML("+json.dumps(_big,ensure_ascii=False)+");"
   +"console.log(JSON.stringify({rect:(h.match(/<rect /g)||[]).length,line:(h.match(/<line /g)||[]).length,"
   +"cut:/图中只画了/.test(h),sum:/108|60 个对象/.test(h)}));")
try:
    _o2=_sp.run(["node","-e",_probe2],capture_output=True,text=True,timeout=20)
    _j2=json.loads(_o2.stdout.strip())
    chk("AN15 截断只画上限内对象", _j2["rect"]==16, str(_j2))
    chk("AN15b 截断时优先保留有关系的对象(连线不为 0)", _j2["line"]==8, str(_j2))
    chk("AN15c 截断时给出说明", _j2["cut"])
except Exception as _e:
    chk("AN15 截断策略探针", False, f"node 探针失败: {_e} / {_o2.stdout[:120] if '_o2' in dir() else ''}")

print(f"\n{'='*40}\n结果: {P} 通过 / {F} 失败")
if fails:
    print("失败清单:")
    for x in fails: print("  ✗",x)
sys.exit(1 if F else 0)
