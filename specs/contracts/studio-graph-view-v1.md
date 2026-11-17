# Studio 图谱视图契约 v1 — DR-159 / IR-031 T01

状态：frozen-for-red（2026-11-17 用户授权启动 IR-031；本版作为 T02 行为测试依据，变更须记录原因）。
关联：[DR-159](../decisions/DR-159-studio-ontology-visualization.md)、[IR-031](../iterations/IR-031-studio-robotics-visualization.md)、[源码清单](../sources/IR-031-studio-sources.json)。

## 1. 权威数据与适配入口

纯 Python 适配器位于 `ontology_assets/studio_view.py`，提供 `project_design(session)` 和 `project_version(version, payload)`。
前者只接受 DesignService 的已保存公共会话或调用方明确选定的历史 state 加 session_id/status，后者接受已经由资产服务校验来源工件的版本与固定 RDF/XML 字节。
纯投影不访问文件、网络、模型或写入存储，也不评定发布／执行资格。
现有 DesignAssets._load、资产载荷摘要校验、领域和生命周期校验仍在 HTTP 调用链执行；纯投影不能替代这些检查。
T03 新增固定版本只读 `GET /api/ontology-assets/<oid>/versions/<vid>/views/studio`，顶层身份使用请求的固定版本，禁止 latest fallback。
复用现有领域拒绝、404、409 完整性错误及历史版本生命周期规则，不为图谱新设写入权限。
设计界面复用已读 session 数据做投影／请求适配，不新增模型或确认调用；历史读取仍用 revisions 接口。
完成阶段通过 output_asset_ref 显式切换为固定版本视图；不得将 ABox 假设存放在 session.result 中。

现有详情投影可复用标签、字面值和实例分组的规则，不能直接复用其展平的 domain_ids/range_ids 来重建 unionOf。
DesignAssets.graph 丢失完整类型身份与匿名表达式，domains.robotics.views.relations 只含实例，两者不能充当本协议的完整来源。
固定版本必须重新从可信 RDF 读取类型、属性、域／值域和已支持表达式；构建 JSON 可作原始定义与来源补充，不覆盖 RDF 事实。

## 2. 视图文档

输出为可 JSON 序列化的 `schema: datamind-studio/1` 文档。
必须包含 `context`、`nodes`、`edges`、`properties`、`individuals`、`relations`、`warnings` 和 `metadata`。
同一输入输出确定，数组按稳定 ID 排序，不改变输入对象。

| 字段 | 约束 |
|---|---|
| context.kind | design 或 version；为 IR-032 预留 data，不在本轮实现 |
| context.key | design 使用 session_id；version 使用 JSON 数组序列化的 [ontology_id, version_id]，不能用显示名拼接 |
| context.revision | design 为非负整数 revision；version 为 payload_sha256 |
| context.stage / status | 保留实际阶段和状态；version stage=fixed，status 沿用 publication_state |
| context.snapshot_sha256 | design 原 snapshot_sha256（无设计时 null）；version 为 payload_sha256 |
| metadata | name（可空）、read_only=true；不产生发布资格 |
| nodes | 类别、datatype、expression；不默认混入全部实例 |
| properties | object/data 属性及已声明域和值域；不声明的约束保持空数组 |
| edges | 类别层结构、属性约束及已支持表达式连线 |
| individuals | 全部已读取显式实例、types 和数据值；默认不全部渲染 |
| relations | 真实实例间关系；不把类别定义当成事实 |
| warnings | 未绘制或不支持的公理提示，不伪造边来补全 |

节点包含 `id`、`iri`、`type`、`labels`（value/language）、`definition`。
类别另有排序去重的 `individuals`，只统计直接显式类型，不加推理所得父类实例。
属性包含 `id/iri/type/labels/domains/ranges`；type 为 object 或 data，约束节点保留完整身份。
实例包含 `id/iri/labels/types/data`，data 项保存 predicate/value/datatype/language，保留空串、0、false 与语言标记的原始含义。
边包含 `id/source/target/predicate/kind`，kind 为 subclass/object/data/expression/disjoint/membership/relation/value/propertyConstraint 等明确语义。
实体 ID 使用完整 IRI；边 ID 使用规范 JSON 数组 [kind, source, predicate, target]；有 role 的数据领域边另外包含 role，不能覆盖平行边。
表达式 ID 使用 rdflib 规范化图的 blank node 标识加 `_:` 前缀，在重解析相同 RDF 及三元组重排后稳定；不要求结构变化后保持旧表达式身份。
表达式保留 `expression: {operator: unionOf, operands: [...]}`，类别属性经 union 节点连接，不画成多个独立 domain 等价声明。
同一属性的多个独立 domain 声明保持多个约束；unionOf 与多 domain 的语义不能混同。
其它无法完整呈现的表达式进入 warnings；不修改源公理或声称支持完整 OWL 推理。

## 3. 设计阶段与结果身份

| 阶段 | 有效内容 | 确认与等待 |
|---|---|---|
| classes | result.classes 及 parent_ids；不显示遗留关系属性和实例 | result 为 null 时空图；不使用词表补造类别 |
| properties | 已确认类别 + 当前 result 的 object_properties/data_properties（若已生成） | 类别确认必须与 result.classes 相符；缺失确认拒绝投影 |
| abox | schema_confirmation.tbox 的类别、关系和属性 | 未完成保留骨架；完成产物使用 output_asset_ref 单独读取 |

result 摘要必须与 snapshot_sha256 一致，namespace 必须合法；校验失败抛 ValueError，不显示猜测结果。
properties／abox 的确认快照必须满足现有已选类别和骨架约束，投影不执行确认或修复。
running／failed 状态仍可显示最近已保存的 result，状态文字明确，不生成进度节点。
edit_classes 产生的修订只显示类别；此前失效关系、属性和实例移除。
历史视图明确标记，只读独立上下文；其 revision 低于当前也可在明确选择历史后展示，但不能覆盖当前可写会话。
离线旧本体直接进入固定版本浏览，不虚构设计阶段历史。

## 4. 共享前端边界

ES module 放在 `ui/modules/studio/`，不依赖 Node 或附件目录运行，测试通过 Node 的 ES module 模式加载。
`controller.js` 导出 `StudioController`，构造参数为 svg 和 callbacks，内部组合 GraphView 与 GraphState。
`update(document, {epoch})` 返回 true 表示接受、false 表示忽略旧响应；`beginContext(context)` 增加并返回 epoch，清空其它来源内容。
epoch 是页面显式切换设计／版本／历史视图的单调令牌；同一 epoch 拒绝来源 key 不符和较小 revision，历史切换必须调用 beginContext。
字符串摘要修订不按字典大小比较，仅以当前 epoch/固定 context 匹配接收；不能将摘要当作版本顺序。
`capture()` 返回可复制的 {context, epoch, selectedId, expanded, paused, transform, positions, changes}；changes 是 added/modified/removed 的节点 ID 数组。
`select(id)` 只触发 onSelect(id,原始实体)；`toggleClass(id)` 展开／收起，`loadMore(id)` 增加下一批；`pause(bool)`、`pin(id,bool)`、`destroy()` 分别控制布局和释放。
显示按钮由宿主提供，图渲染通过 GraphView，测试可观察 svg 上的 data-node-id/data-edge-id，键盘 Enter 可查看详情。
update 同一 context 保留现存坐标、固定状态、缩放、展开和有效选择，文字输入通知不重置布局；删除节点清理选择／展开并记录 removed。
changed 比较实体语义字段，不能因布局位置变化反复标为修改。
beginContext 清空图与详情；销毁后任何 update 均返回 false，事件／动画／ResizeObserver 不再工作。
多个 svg 使用实例唯一 marker/pattern ID，连线引用自身 defs，不能硬编码同一 edge-arrow。

## 5. 实例展开、规模与错误

按完整 IRI 稳定排序，每次展开／loadMore 加载 50 个实例，重复点击不产生重复节点。
同一实例多个显式类型去重显示，保留已展开类别的多条 membership；收起一个类别不移除另一展开类别仍引用的实例。
无实例时回调 onNotice，区分尚未构建与固定版本确实无实例；零实例不产生模拟节点。
controller 的 capture.expanded 保存各类已请求数量，更新保留上限内仍存在的实例；已移除类别的展开状态清除。
每类 displayed/total 在宿主可见，计数基于对应固定视图全部显式成员，缺页时不以已加载数充当总数。
首版投影返回全量可接受本体，前端按批渲染；超过 20,000 实体或 100,000 边明确拒绝并提示缩小范围，不静默裁剪。
同屏最多 300 节点、600 边；扩大展开超过限额时保留现有视图并提示收起其它类别／使用局部范围，不能显示虚假的“全部”。
固定版本校验拒绝 DTD/实体，损坏 RDF、摘要不符、跨版本与不适用领域按已有服务错误处理。
错误和切换不得显示上一资产作为成功结果；普通失败可保留同一上下文最近有效图并标记读取失败。

## 6. 代表性输入与性能验收

手算语义夹具在 T02 固定：Resource→Arm；Gripper；Arm/Gripper 的 union 定义域→Resource；一个同时属于 Arm/Gripper 的实例、一个仅 Arm 实例、一个空类别；字符串空值和 integer 0。
固定期望为 Arm 2、Gripper 1、Resource 0（显式口径），共享实例只绘制一次。
真实代表样例为 `ur_skill.owl` 与仓库现有离线机器人来源在临时目录生成的固定版本；新设计使用 Harness 的类别／属性／完成候选真实持久化路径。
规模夹具可重复生成：常规 30 类／60 关系／150 实例，大类 1 类／1000 实例，密集场景 150 节点／300 边；禁止修改用户工作数据。
环境为源码清单所载 Linux、24 logical CPU、Playwright Node 24.18.1、系统 Google Chrome，无 CPU／网络限速，记录精确浏览器版本与 CPU 型号。
浏览器 viewport 为 1440×1000 和 390×844；窄屏允许侧栏切换，不允许页面横向溢出或遮挡确认按钮。
预热 1 次后测 5 次：常规场景从有效文档送达 update 到节点可交互 p95≤2s（5 次以最大值计）；密集场景 p95≤5s。
拖拽事件送达至下一帧位置更新 p95≤100ms；拖动连接节点至少 80px 后，1s 内至少一个未固定邻居位置变化≥2px，固定节点变化≤0.5px。
暂停时邻居不因力导向移动；释放后布局可继续；物理移动不能当作新增设计变化。
执行 20 次挂载／更新／销毁，销毁后可观察的动画队列、观察器和宿主事件监听均归零，无跨画布回调或控制台错误。
真实浏览器与几何判据守交互，Node 测试只验证状态与资源规则，不能代替浏览器性能结果。

## 7. 回归与人审

T02 覆盖纯投影和共享控制器 RED，T04 覆盖真实 HTTP 与页面 RED，T03／T05 分别实现 GREEN。
正式 requirements 只登记已存在测试，coverage_note 明确 RED 状态与尚未完成的集成覆盖。
冻结后不修改已审断言来迁就实现；如契约确需更正，保留原失败证据并提交差异人审。
本体布局和选择不写入本体、不改变发布与应用结果；固定版本、模型、生命周期及任务回归继续保留。


## T05 补充（2026-11-17）

为满足大类别全部可访问而保持同屏上限，新增上一页／下一页实例；每页 50 项，capture.pages 记录各类起始偏移，expanded 继续记录本页已请求数量。
下一页从当前窗口末尾开始，上一页退回 50 项；范围及总数可见，可访问最后一页；不是将超过同屏上限的实例永久隐藏。
用户授权免中间 review 后增加此协议字段，不改动已冻结行为断言。
