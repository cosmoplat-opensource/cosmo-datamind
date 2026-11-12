# DR-071 · 应用契约注册与分层检查

- **状态 / Status**：accepted（用户确认核心流程，并继续进入 T02；实现未交付）。
- **日期 / Date**：2026-11-12。
- **关联 / Refs**：[IR-025](../iterations/IR-025-application-contract-registry.md)、[总方案](../../docs/datamind-application-contract-design.md)。

## 上下文 / Context

通用本体允许保存不完整设计；当前应用准入散落在机器人消费者和深度问数内部，缺项不能统一定位和修复。

## 决定 / Decision

用户已确认以下核心流程；接口、状态与迁移细则见 [T01 冻结契约](../contracts/application-contracts-v1.md)。
T01 已冻结供 T02 测试使用，不表示已经实现。

1. 构建沿用现有 TBox＋ABox 和 LLM／离线路径，保留类别、关系、属性确认及实例化步骤；构建成功即入库。只执行既有来源、语法、设计一致性和存储完整性检查，不运行应用契约检查，不增加应用选择或契约匹配步骤，不自动加入应用类或示例实例。

2. 后端代码注册 ApplicationContract，包含 application_id、contract_version、支持的领域／载荷、checkers、repair_handlers 和 executor；不开放前端编辑或动态执行配置代码。初期注册 robotics.pick_place 和 data.deep_qa。入口与契约由后端绑定：点击“基于此本体生成行为树”触发前者，现有问数请求触发后者；用户无需选择契约。

3. 检查分 schema、execution、task、environment 四层。结构可适配与本次任务可执行分别报告；未提供任务时 task=not_evaluated，不标为整体 ready。结果状态为 passed、missing、unsupported、invalid、error、not_evaluated；warning 不掩盖阻塞项。

4. 每项问题有稳定 code、layer、severity、证据位置、相关实体和允许的 repair action。缺类别进入 TBox 修订；缺技能／绑定进入来源提取与适配；缺业务关联进入任务补全；缺连接进入数据连接。没有实现的修复器明确 unsupported，不生成无效跳转。

5. 检查绑定完整版本向量、载荷摘要、契约版本、任务摘要和环境／证据修订。入库、本体库浏览和详情展示不触发检查；目录只读已有报告的有界摘要，无报告标“尚未检查”。行为树入口不能因未经检查或历史缺项而禁用，点击后检查并引导补全；真正生成仍须通过条件。执行端重新核验，客户端 ready 标记不能授权执行。诊断不调用模型且不修改本体；显式修复才创建派生版本。

6. 契约结果是应用评估，不覆盖领域发布状态、模型配置、执行结果或证据确认。校验异常与缺项分开，禁止 fail-open。

## 后果 / Consequences

独立先行；为 IR-026、IR-027 提供接口，UI 可先用固定展示夹具。
需要版本化检查和修复记录，增加维护成本；换取构建自由、应用诊断和执行一致性。
保留 IR-024 已交付的构建和工件契约；按新需求替代即时 compatibility 展示及隐藏应用入口的行为。
冲突的旧测试在 T04 RED 阶段列明并评审，GREEN 阶段保持测试不变。
