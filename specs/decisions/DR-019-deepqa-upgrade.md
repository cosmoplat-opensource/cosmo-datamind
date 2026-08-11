# DR-019 深度问数十项升级(术语/口径校验/口径卡/选模/指代/流式/实连/API源/评测/业务助手)

日期:2026-07-26 · 状态:已实现 · 回归:test_all.py 212/212(V 节 +22)+ E2E 6/6 + UI 冒烟 13/13

## 背景

对照平台《能力整体架构》(深度问数架构图)逐层审计后,把 A(快)/B(中)/C(大)三档改进一次做完:

## 决定(按档)

**A1 术语扩展检索**:`expand_terms()` 把术语管理词典(translate_cn 四张表,655 条)接进 `build_context` 关键词匹配 —— 问句命中中文→补英文同义词,反之亦然(≥2 字中文/≥3 字英文,上限 12);流式加 `term_expand` 步。此前 655 条词典只做展示,是被浪费的现成资产。

**A2 SQL 口径拦截**(P8「口径错了直接拦截」落地):`_validate_sql_ontology()` 执行前校验 ① FROM/JOIN 表须在 本体/数据目录/up.*/CTE 白名单(拦臆造表名);② `ON a.x=b.y` 两侧列名不同时,键对须落在本体 verified/asserted 关系的 child/parent 键上(同名键等值 JOIN 放行)。流式 `ontology_gate` 步;stream 与非 stream 两链都拦。

**A3 指标口径卡**:done 事件带 `metric_cards[]`(名称/分层/口径 desc/出处表.列/单位),答案区渲染卡片 + 血缘↗。

**B4 按任务选模**:`agent_runtime` 各 runtime `run_turn/_cmd/stream_turn` 加 `model=None` 直通(claude=--model,hermes=-m;向后兼容,不支持的 runtime 由 `_llm_turn` TypeError 回落)。engine_config 增 `task_models{plan/narrative/diagnose}`,UI 引擎设置页三卡。**族保护 `_model_fits`**:claude 系模型只喂 claude-code,防切到 hermes 后 claude 模型名塞进 `-m` 全线报错(实测发现的真坑)。

**B5 多轮指代**:`_carryover()` 问句含指代词(它/该/上述…或 ≤12 字短追问)时,把上文命中的本体对象显式并入问句(「指代延续:上文对象 …」),`coreference` 步。本体即实体注册表,不引通用 NLP。中文后缀剥离要多套(事实表/维度表/汇总表/明细表/表)——「销售订单事实表」须按「销售订单」命中。

**B6 叙述流式**:`narrative_llm(emit=)` claude-code 走 `stream_turn`(stream-json)逐段回调;chat_stream 后台线程+轮询外推 `narrative_delta` SSE;前端 `#_dqnarr` 实时区,done 整卡替换。其它引擎回落一次性 emit。

**C7 外部库实连**:连接器层 `_ext_query/_ext_tables`(mysql/doris=pymysql 已装,postgres=psycopg2 未装则明确报错;只读:`sql_is_readonly` + pg 会话 readonly)。`/api/conn/tables|preview`、`/api/viz/run`、`/api/query`(新增 src 参数)全部路由外部源;连接页外部行可点击实连浏览;SQL 工作台加数据源下拉。**凭据规范**:user/password 存 `workdir/conn_secrets.json`(0600),只写不回显、不入连接列表、删连接连带清除 —— 与 engine keys 同套规范。

**C8 API 型数据源**:kind=api(url+json_path)。`/api/conn/api_fetch` 拉 JSON(≤2MB)→ json_path 下钻(支持数组下标)→ 对象数组(≤5000 行)物化为 uploads.db `api_<slug>` 表 → up.api_* 即刻进问数上下文/SQL/可视化。仅 http(s);连接页「取数」按钮。

**C9 问数评测**(P20 落地):`benchmark/qa_set.json` 8 题金标(**全部经主库实测校准**,含 1 道需沿本体关系两跳 JOIN 的产线题)。三组同题对照:A=朴素 Text2SQL(仅英文表列)/ B=图谱增强(中文语义+关系)/ C=本体全量(+指标+术语扩展+口径校验,gate 生效)。判分=数值容差(万元题接受 ×10000 元口径)/字符串等值 + 出处引用(SQL 须含金标表)+ C 组口径拦截计数。后台线程每题落盘(中断保留部分);`/api/eval/run|status|results|set`;UI「问数评测」页可跑分/轮询/逐题三组对照。

**C10 业务助手**(对齐平台「业务助手」场景):角色卡(生产主管/质量工程师/设备运维)组装 常用问题→问数预填、异常诊断→诊断模式预填、我的动作→动作中心、待办聚合(待人审关系/待批动作/反馈待处理/评测 C 组正确率,全部实时来自现有 API,无新状态)。角色只是入口组装,能力仍是同一套。

## 不变量

- 口径校验只拦不改:拦截原因全文入执行记录,可审计;不改写 SQL。
- 人审/反幻觉规范不动;评测 C 组的 gate 误拦也如实计入(诚实成本)。
- 凭据与 Key 同规范:0600、只写不回显、删除连带清理、永不入日志。
- API 源只物化进 uploads.db(可丢弃层),绝不写主库。

## 验证(实测)

- 模块直测 10/10(扩展/校验/卡/指代/族保护);E2E 流式 6/6(term_expand→ontology_gate→narrative_delta→metric_cards 全链可见,69s);
- C8 全链:登记→物化 17 行→up.api_* 直查 ✓;C7:0600 ✓ 列表不回显 ✓ pymysql 真连报错明确 ✓;
- test_all.py 212/212(V 节 22 条新断言);Playwright 冒烟 13/13(助手跳转预填/评测页/任务选模卡/API 表单/密码框/源下拉)。

## 增补(2026-07-26 下午):评测实跑与实时联动验证

- **评测首轮实跑**(claude-haiku 出 SQL,同题同模型):A 朴素 Text2SQL **3/8 正确·1/8 出处**;B 图谱增强 **2/8·1/8**;C 本体全量 **6/8 正确·6/8 出处**,口径拦截 0。
  - 有价值的发现:**B<A** —— 只加中文语义与关系、不加指标/术语扩展,反而会错选表(Q4 在错误表数出 12 台设备);语义信息要成套喂,半套比不喂更危险。
  - 败题在册:Q7(两跳 JOIN 选错聚合路径)、Q8(月均分母口径),即下一轮改进靶点。
- **实时联动矩阵 19/19**:人审否决→口径校验实时拦截同一 JOIN SQL、⋈ 提示实时剔除、撤销复原;反馈→评审队列/助手待办实时增减;高风险动作→待批计数/审计留痕;API 源取数→问数上下文与 SQL 实时可用;task_models 写→下次调用即读;评测结果→助手卡实时显示。
- **拟人走查 19/19**(录屏 `datamind_录屏/06_实时联动走查.mp4`):助手→问数(term_expand/ontology_gate/口径卡/流式)→反馈→评审队列实时出现→否决/撤销→连接页取数→SQL 工作台即查→评测页→引擎设置→回助手计数 +1。
- 对照页 v2 已把评测实测截图与数字补进净增量(`深度问数-平台架构×DataMind对照-Jinze Yu.pptx`)。

## 教训

- 任务级模型覆盖必须做**运行时族校验**:多引擎系统里「模型名」不是全局有效的。
- 金标题集先跑金标 SQL 再入库:8/8 实测校准,评测才有权威性。
- 后缀剥离(事实表/维度表…)是中文本体实体匹配的基本功,一处做对多处受益(诊断/指代/评审搜索)。

## 关联

- [[DR-008-datasource-connection-model]](问数编排)· [[DR-014-ontology-in-the-loop]](证据回流:助手待办聚合复用)· [[DR-015-palantir-actions]](动作层:助手动作入口)· [[DR-017-engine-settings]](引擎设置:task_models 落同一配置文件)· [[DR-018-realtime-consistency]](单一真相:口径校验/评测读 load_ir_edited)
