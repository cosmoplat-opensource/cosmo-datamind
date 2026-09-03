# DR-053 · 技能元数据的单一解析器、MCP 协议协商与图谱列表缓存

- **状态 / Status**: accepted
- **日期 / Date**: 2026-09-03
- **关联 / Refs**: [[DR-016-action-layer-mcp]]、[[DR-021-skill-management]]、
  [[DR-035-single-source-of-truth]]、[[DR-050-executable-ontology-build-gate]]、
  [[DR-051-editable-builtin-skills]]；
  实证=`skill_registry.py:front_matter`、`server.py:_stat_sig|_graph_row|_qa_table_inventory`、
  `mcp_action_server.py:SUPPORTED_PROTOS|TOOLS`、`tests/unit/test_skill_registry.py`、
  `tests/unit/test_graph_cache.py`、`tests/unit/test_mcp_action_server.py`

## 上下文 / Context

三件事分别在三处暴露出同一类问题——**同一条规则被抄了多份,且各份并不一致**,
以及**对外接口的规范符合度只靠手工验证**。

### 1. 技能摘要有三份解析,一份比一份弱

技能摘要(`description`)决定技能列表里显示什么、技能选择对比读什么。它有三处实现:

| 位置 | 正则 | 行为 |
|---|---|---|
| `skill_registry.discover` | `^description:\s*(.+?)\s*$` (re.M),**全文**搜 | 正文里以 `description:` 开头的行会命中 |
| `server.py:/api/build/skills` 覆盖件分支 | `description:\s*(.+)`,无锚定 | 正文里**任意位置**出现即命中 |
| `server.py:/api/build/skills` 自定义分支 | 同上 | 同上 |

技能正文里出现 `description:` 完全正常——「输出要求」一节列字段、给 YAML 示例都会写到。
于是没写 front matter 的技能,列表里显示的"摘要"是正文里某个字段示例值。
DR-051 之后内置技能可被改写、DR-021 允许上传自定义技能,这条路径是用户可达的。

保存技能时的软校验(DR-051 加入)又是第四份正则。它与列表用的那份判定不同,
可以出现「保存时提示缺 description、列表里却显示得好好的」这种自相矛盾的反馈。

### 2. MCP 只声明单一协议版本,且缺标准注解

`initialize` 固定回 `2024-11-05`。规范要求的是**协商**:客户端请求的版本若受支持
就沿用,否则回自己最新支持的版本由客户端决定是否接受。固定回一个版本,
对声明支持更新版本的客户端而言是降级,且不表达真实能力。

工具声明缺 `annotations`(2025-03-26 起)。客户端据此做审批分流——读工具免打扰、
写工具才提示用户。不声明,客户端只能一律按最危险处理,或一律不提示。

### 3. `/api/graphs` 是六处调用的全量重算

该端点对每个图谱源跑 解析 → 净化 → 编辑回放 → 转图 → 执行画像 全流水线,
耗时随 `built_*` 文件数线性增长(18 个文件时约 47ms)。前端六处调用它:
首屏、图谱页、复核页、问数范围、编辑器、场景页。`_qa_table_inventory` 同理,
每次问数与每次列表都要重开两个库问一遍「有哪些表」。

## 决定 / Decision

### 1. front matter 解析收敛为一处

`skill_registry.front_matter(text)` 是唯一解析器,只认 `---` 围栏内的顶层标量键。
三个调用点(列表的两个分支、保存软校验)全部改走它。

顺带支持 Agent Skills 约定的可选键并原样透出:`license`、`version`、`allowed-tools`。
未知键忽略而非报错——技能文件由使用者手写,多写一个键不该让技能整个不可用。

不引入 YAML 依赖:技能元数据本就是扁平的,而注册表要在没装任何第三方包的环境里工作。

### 2. MCP 按规范协商版本,并声明行为注解

`SUPPORTED_PROTOS = ("2025-06-18", "2025-03-26", "2024-11-05")`,新在前。
本服务只用 `tools` 能力,该子集在三个版本间语义一致;新版增加的 `annotations`
与工具级 `title` 都是纯增量字段,旧客户端按规范忽略未知字段即可。

请求版本在列表内则沿用,否则回 `SUPPORTED_PROTOS[0]`。**绝不回显未知版本**——
那等于声称支持任意版本,客户端按更新语义调用即行为未定义。

三个工具补 `annotations`。`invoke_action` 标为
`readOnlyHint=false, destructiveHint=false, idempotentHint=false`:
只追加记录故非破坏,重复调用产生两条记录故非幂等。

注解按规范只是 hint,**不构成安全边界**。真正的治理——风险分级、高风险入人审队列、
approve/deny 永不作为工具暴露——仍由 DataMind 服务端强制执行(DR-016 不变)。

### 3. 图谱列表与表清单按文件指纹缓存

`_stat_sig(*paths)` 返回 `(路径, mtime_ns, size)` 元组作为失效键。
文件不存在记 `(path, 0, -1)`,使「从无到有」与「从有到无」都改变指纹。

- `_qa_table_inventory` 按两个库文件的指纹缓存,**返回副本**,
  调用方就地改集合不污染缓存。
- `_graph_row` 按 (IR 文件, 该图编辑栈, 库文件) 三者的指纹缓存单行结果;
  源文件消失的条目在每次列表末尾出清,缓存不无界增长。

不用 TTL。TTL 意味着存在一个窗口,用户改了本体却看到旧计数;
指纹失效则是改完立刻生效。

## 后果 / Consequences

- 技能摘要不再可能显示正文里的字段示例值;保存提示与列表显示由同一判定得出。
- MCP 客户端能拿到真实的版本能力与行为注解;`get_action_status`/`list_actions`
  可被客户端识别为只读而免于逐次确认。
- `/api/graphs` 47ms → 1.4ms(18 个构建文件,本机实测),前端六处调用共同受益。
- 新增 48 项单测(473 总数):MCP 层此前无单测,现覆盖协商、通知不应答、
  畸形输入不终止服务、治理边界(approve 不可暴露)、错误消息不外泄部署细节;
  缓存层覆盖六个失效面。

## 备选与不做 / Alternatives

- **引入 PyYAML 解析 front matter**:被否。为四行扁平键值增加一个依赖,
  且注册表需要在最小环境里工作。
- **`/api/graphs` 加 TTL 缓存**:被否。见上,TTL 会产生「改了看不到」的窗口。
- **把 `annotations` 当作审批依据**:被否。规范明确其为 hint,
  客户端可忽略;把治理挂在客户端善意上,等于没有治理。
