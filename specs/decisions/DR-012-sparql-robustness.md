# DR-012 · SPARQL 健壮性:线程安全 + 诚实报错 + 合法默认示例

> 状态:delivered(2026-07-20)。实操点检『SPARQL 查询』页时,默认示例点『执行』长期报
> 「SPARQL 执行超时(>8s)或无结果」。逐层定位后发现**三个互相独立的真问题**,全部修复并回归锁定。

## 症状
点『执行』→ 报「执行超时(>8s)或无结果」。但同一查询经 curl / 独立 Python 却 0.04s 返回 20 行。

## 根因(三个独立缺陷)

### 1. 错误信息自相矛盾:异常被伪装成超时
`_bounded(fn, secs)` 用守护线程跑 `fn`,`except Exception: pass` **吞掉异常**并返回 `default=None`;
调用方见 `None` 一律报「超时(>8s)」——**105ms 的瞬时异常也谎称 8s 超时**,把人引向错误方向。
- **修复**:新增 `_bounded_ex(fn, secs) -> (value, error, timed_out)` 三态区分;SPARQL 端点据此
  分别回报「查询错误: <真实异常>」/「执行超时」。`_bounded` 保留为薄封装,向后兼容。

### 2. rdflib SPARQL 解析器非线程安全(pyparsing 语法状态污染)
rdflib 的 SPARQL 解析基于 pyparsing,其语法为**进程级全局单例**,解析时会改写内部 parse-action;
多请求并发解析(或与 pyshacl 内部 SPARQL 相撞)→ 语法损坏,报出
`Param.postParse2() missing arg`、`<lambda>() takes 1 positional argument but 2`、
乃至假的 `Expected SelectQuery, found 'OPTIONAL'`。**12 并发复现:12/12 全败**。
- **修复**:`_RDF_LOCK = threading.Lock()` 串行化两个 SPARQL 语法使用点——SPARQL `g.query()` 与
  `pyshacl.validate()`(其内部跑 SPARQL)。Turtle 解析/序列化是独立递归下降解析器,实测不受影响,不加锁。
  修复后 live 端点 16/32 并发全绿。锁在 `with` 块内,异常也释放,不因解析失败而死锁。

### 3. 页面内置默认示例本身是非法 SPARQL
`ui/index.html` 的默认查询把 `rdf-schema#label>?label` 写成 **`>` 紧贴 `?label`、缺空格**,
rdflib 判为非法(`sendTextarea→ERR` vs `sendGood→ok 20`,首异点 char 131)。这是**点『执行』必败的直接原因**,
与并发无关——之前被 #1 的假「超时」信息长期掩盖。
- **修复**:补空格 `#label> ?label}`。页面点『执行』→「SELECT · 20 行」。

## 教训
- **诊断先于修复**:先修好 #1 的诚实报错,#3 的真异常才浮出水面;否则会一直在 #2 的并发方向空转。
- **别让兜底吞真相**:`except: pass` + 统一「超时」文案是本次误导之源。兜底要保留并如实透出根因。

## 回归锁定(test_all.py,+3 → 113 断言)
- `sparql 页面默认示例可跑`:**从 HTML 抽取** `#sq_q` 默认查询实跑,≥1 行——防再混入 `>?var` 类非法 SPARQL。
- `sparql 语法错报『查询错误』非『超时』`:错误文案不含「超时」——锁定 #1。
- `sparql 12并发全200(线程安全)`:12 并发默认示例全 200——锁定 #2。

## 关联
- [[DR-006-security-model]](SPARQL 禁 SERVICE/FROM 外链;本 DR 补线程安全与报错诚实)
- [[DR-010-iof-bfo-alignment]](SPARQL substrate = IOF 注释化 Turtle,与导出同底)
