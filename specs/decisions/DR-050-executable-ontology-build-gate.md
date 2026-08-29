# DR-050 · 半自动本体构建的统一证据契约、技能输入与确定性验收检查

- 状态:accepted / delivered
- 日期:2026-08-28
- 关联:DR-002、DR-011、DR-024、DR-035、DR-037、DR-040、DR-049

## 背景

原构建页把处理过程描述为“规划→取证→提议→工具裁决→模型批评→迭代”，但实现存在三处不一致:

1. `quick_build` 使用 `dao_core`,LLM 分支仍手写阈值与命名逻辑,没有方向反证;
2. 内置技能只向 prompt 注入一行硬编码摘要,真实 `SKILL.md` 的修改不影响构建;无上游引擎时技能列表为空;
3. 构建完成后只统计 verified/candidate，没有执行图结构、证据契约、定义质量与 CQ 验收。

这会导致同一份数据因入口不同得到不同状态，技能选择不影响实际提示词，且完成页的复核步骤只有展示文案。

## 决定

### 1. 两条构建路径共用单一裁决核

LLM 关系中的 `child_key`/`parent_key` 只作为搜索提示。服务先验证列真实存在,再把重叠率、
父键唯一、子键唯一、命名证据交给 `dao_core.classify` 与 `should_reverse`。固定 60% 阈值不因
基数自动下调(DR-038 的真实数据回归结论继续有效)。泛化父键 `id/code` 不能自动放行:
`customer_id→customers.id` 必须由父表名补证,`order_id→customers.id` 必须留在 candidate。

每条提议保留最佳尝试的结构化证据,包括阈值、唯一性、命名得分、方向、裁决状态与原因。
复合键按2～4列元组联合裁决；超过4列是当前已知边界。

### 2. 技能正文成为真实输入

增加 `skill_registry.py`,合并本仓 `skills_seed/` 与可选上游 `web/skills_seed/`;同名时本仓优先。
技能列表、查看、运行与 prompt 注入共享注册表。prompt 有单技能/总长度上限,防方法论挤掉数据证据。
新增 `ontology-semi-auto` 默认技能，明确模型只提议、确定性规则验证、CQ 验收、人工确认产生 asserted，并把否决样本用于后续误判抑制。

### 3. 执行确定性验收检查

增加 `build_quality.evaluate`,在 LLM 与 quick_build 共同出口执行:

- 图结构阻断问题与待复核信号;
- 所有 verified 的可回放证据契约;
- 定义的非循环、属加种差与反例;
- 若用户提供 CQ,执行强证据路径可达性验收。

状态为 `pass/review/fail`:结构或 verified 证据破坏才 fail;候选、语义争议、定义不足、CQ 缺失/部分支持进入 review。
验收检查不自动修改图谱，报告与 gaps 随 IR 原子写入。`POST /api/build/quality` 可对已有图谱重新执行同一检查。

### 4. CQ 作为留出验收集，不进入提议环节

构建 API 和 UI 接收每行一个 CQ。CQ 不注入模型提议上下文，避免模型按题目补造对象和路径；
只在构建完成后调用 `cq_check` 做确定性盲测，与 DR-024 保持一致。
未提供 CQ 时 `coverage=null`,不得用 0 或模型自评伪造覆盖率。

构建过程对 CQ 的状态文案必须来自确定性验收报告：至少显示总数、可回答、部分支持和不可回答
数量。仅仅“提供了 CQ”不等于“CQ 已验收”；存在 `partial` 或 `unanswerable` 时不得显示
“已验收”“通过”等容易误解的结论。

### 5. 数据证据与语义裁定分轴

关系同时保存 `evidence_status` 和 `semantic_status`。数据连接成立但语义复核存疑的关系可继续
保留取证结果，但不得进入 CQ 强路径；人工复核确认后置为 `asserted`。查询异常单独写入
`scenario.query_errors`，不得按“无证据”处理。

### 6. 已登记动作投影为本体节点

`action_ontology.project_registered_actions` 把启用的动作类型投影成 `kind=action` 的一等节点，
按显式对象标识或 `object_table` 建立候选绑定。动作节点明确记录
`execution_mode=decision_capture`、`real_writeback=false`；当前只形成决策记录，不声称已写回业务系统。
模型可从有明确出处的需求/流程文档提出动作候选，但不能仅凭表名批量生成动作。

## 后果

- LLM 分支会保守地把部分历史 verified 降为 candidate,这是消除自增键/反向边假阳性的预期变化。
- quick_build 不生成 LLM 定义且通常没有 CQ，验收结果多为 review；这表示仍需补充定义或业务验收，不等同于运行失败。
- 上游引擎完全缺失时,本仓仍有可读、可注入、可校验的半自动构建技能。
- 证据契约变成下游 JOIN 提示的前置保障,构建与消费共用 `dao_core.name_ok`。

## 验证

- `tests/unit/test_server_adjudication.py`:正常、反向与泛化 `id` 假阳性三类 LLM 分支回归;
- `tests/unit/test_build_quality.py`:pass/review/fail 与语义争议;
- `tests/unit/test_build_quality.py`:CQ 状态文案区分未提供、全部可回答与存在缺口;
- `tests/unit/test_cq_check.py`:语义存疑的 verified 关系不计入 CQ 强路径;
- `tests/unit/test_action_ontology.py`:动作节点投影、显式绑定、幂等和未绑定状态;
- `tests/unit/test_request_limits.py`:上传与聊天请求体超限在路由读取前返回 413;
- `tests/unit/test_ontology_grounding.py`:官方 IRI、类别相容性、旧版虚构关系清理和 Turtle 解析;
- `tests/unit/test_skill_registry.py`:根优先级、正文注入、长度上限、路径穿越;
- `skill-creator quick_validate.py`:内置技能格式校验;
- 全量 `pytest` 与 ruff F/B 零告警。
