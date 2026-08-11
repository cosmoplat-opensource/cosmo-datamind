# DR-006 · 安全模型(只读 / CSRF / 路径穿越 / 命令白名单 / SPARQL)

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-07
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: `server.py` `_csrf_guard`/`_bad_gkey`/`load_ir`/`_edits_path`/`sparql`/`q`/`sql_is_readonly`/文件服务端点

## 上下文 / Context
系统跨域代理平台引擎、执行 SQL/SPARQL、读写文件、跑技能子进程,且面向工业级——须系统性收敛攻击面。
一次针对性审计发现 `forged_` 图谱键可路径穿越读/写任意 `.json`(高危 LFI/写穿越)。

## 决定 / Decision
- **SQL 只读**:见 [[DR-001]](`mode=ro` + `sql_is_readonly` + 单句执行,三重防写)。
- **CSRF**:全局 `before_request` 守卫——非安全方法且带 Origin/Referer 且 host≠本机 → 403;无源(curl/非浏览器)非 CSRF 面放行。覆盖所有写端点。
- **路径穿越**:图谱键经 `_bad_gkey()` 校验(仅 `[A-Za-z0-9_.-]` 且禁 `..`)——`load_ir` 非法键返 None、`_edits_path` 非法键落固定安全名,堵住 `forged_../..` 读/写任意文件。文件服务(`/doc`/`/vendor`/`/platform`/`/api/outputs/file`)一律 realpath 归属校验或严格正则;`conn_preview` 表名 `^[A-Za-z0-9_]+$`;`build_delete` key `^built_[\w]+$`。
- **命令执行**:skill/tool/deploy 名走白名单、无 `shell=True`、args 拦 `;&|$\`` 元字符;子进程 `run_job` 定向 stdout、超时 1800s。
- **SPARQL**:禁 `SERVICE`(联邦 SSRF)与带 `file/http(s)/ftp` 的 `FROM`(外链 LFI/SSRF);软超时 8s(病态查询后台线程不可硬取消——本地单用户可接受,勿暴露公网多租户)。
- **上传**:文件名经 basename/正则去分隔符,无穿越;自有技能仅收 `.md/.txt` 说明,不收可执行(防任意代码)。
- **写入原子性**:`_atomic_json`(temp + os.replace);`_WRITE_LOCK` 串行化。
- **前端显示防 XSS**:一切用户/LLM 可控文本(连接名/本体名/对象名/会话标题/编辑算子 target 等)插入 `innerHTML` 前一律经 `esc()` 转义;属性值经 `jsAttr()`。防存储型 XSS——尤其 LLM 抽取的对象名未在后端做 HTML 校验,显示层转义是主要防线(如工作台编辑日志 `stEdits`)。

## 后果 / Consequences
- (+) 攻击面收敛:路径穿越、注入、CSRF、写绕过、SSRF 均有对应防护,回归含穿越读/写与 SPARQL FROM 断言锁定。
- (−) SPARQL 软超时不可硬取消运行线程(Python 限制);本地单用户原型可接受,已在 README「已知边界」标注。硬修需子进程沙箱(planned,若上公网)。

## 第9轮安全审计(2026-07-20 · 对抗输入实测)
对运行中系统实打实灌对抗输入,逐层验证:
- **路径穿越**:`/api/outputs/file?p=../../etc/passwd`(含 URL 编码)→ 403;图谱键 `../`/`forged_../` → 404。全拦。
- **CSRF**:跨源 `Origin: evil.com` 的写 POST → 403;无 Origin(同源/curl)→ 200。符合预期。
- **SQL 注入/写入**:`PRAGMA writable_schema`、`ATTACH DATABASE`、`;DROP`、`INSERT/UPDATE/CREATE` → 全 BLOCKED。
- **存储型 XSS**:改名端点在**输入层**即拒 `< > " ' &`(纵深防御,非仅靠前端转义);短/长 payload 均被拒。
- **XSS 不变式审计**:全量扫 `innerHTML` 动态插值,唯一未过 `esc()` 的是构成规则页 `methodology/pipeline/owl_projection`——数据为后端硬编码常量(无注入源,非漏洞),但破坏"innerHTML 动态值必过 esc"不变式。**已补 esc()**(纵深防御+未来若改为动态可配则不留隐患)。`${g.name}` 经 `graphOptions` 内 `esc(label(g))` 转义,安全。
**结论**:安全态势稳健;本轮仅补一处一致性 esc,回归 119/119。

## 第 10 轮:写入原子性与锁安全(2026-07-29)
按「未覆盖路由」清单补测时,在三个此前无测试的写端点附近查出三处真问题:

1. **锁泄漏风险**:`build_upload` 的 `_WRITE_LOCK.acquire()` 位于 `try` 之外,
   紧随其后的 `sqlite3.connect()` 若抛异常(磁盘满/权限),`finally` 永不执行,
   全局写锁将永久不释放,导致此后**所有**写端点阻塞。
   改为 `with _WRITE_LOCK:` 上下文管理器,异常路径亦保证释放。
   全仓 28 处写锁使用中,此为唯一手工 acquire 的例外。
2. **原子写规范被破坏**:`/api/ont/skills/write` 写 SKILL.md、`ont_forge` 写 `.ttl`
   均为裸 `open(...,"w").write(...)`——无 `with`(泄漏句柄)、无原子性(写坏即截断)。
   尤其 `.ttl` 与紧邻的 `.json` 同处一个 `with _WRITE_LOCK` 块,后者已用 `_atomic_json`,
   前者却裸写,同一份锻造产物可能出现「json 完好、ttl 截断」。
   新增 `_atomic_text(path, text)` 助手(与 `_atomic_json` 同规格:tmp + os.replace),
   两处改用之;`_atomic_json` 亦重构为其之上的薄封装,单一实现。
3. **上传文件句柄泄漏**:`open(path,"wb").write(raw)` 改 `with`。

回归:补 Z 节 13 条断言(根页、三个写端点守卫、上传建表、文件名穿越消解、
并发不死锁、锁健康探针、自清理),总数 249 → **262**。
路由测试触达率由 98/109 提升至 106/109(余 3 条为 SSE/长任务,仅测守卫路径)。

## 第 11 轮:错误语义统一与核心页容错(2026-07-30)
承接第 10 轮对 `J()` 的加固——加固后失败统一返回 `{error}`,反而暴露出两类既有问题:

1. **`error` 键语义被占用**:`/api/overview` 在数据库不可用时做优雅降级(返回零值 KPI
   让看板仍可渲染),却复用了 `error` 键并返回 HTTP 200。全站 138 处错误响应中仅此一处
   "错误即成功",且与前端 44 处 `if(d.error)` 守卫直接冲突——一次成功的降级会被误判为失败。
   改用 `warning` 键,`error` 自此全站统一表示"请求失败"。

2. **核心页无错误守卫,后端故障即白屏**:`home()` 为 `const k=d.kpi` 后直接
   `k.rows.toLocaleString()`;`drawGraph()` 直接 `g.nodes.length`。后端 500 时二者均抛
   TypeError,导致总览页与图谱页整页空白且无任何提示。二者是落地页与核心功能页,影响最大。
   已加守卫:失败时显示提示而非崩溃。

   全站 44 处"取回后解构"中,其余多已用 `d.x||[]` 兜底(仅显示为空,不崩),故只修这两处真崩点。

回归:Z16 锁定 `warning` 键不回退;Z17/Z18 以 node 探针复现 `{error}` 返回形态,证明守卫
生效且不抛;Z19 静态锁定守卫代码存在。断言 264 → 268。
