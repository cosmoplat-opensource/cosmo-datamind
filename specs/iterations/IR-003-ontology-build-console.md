# IR-003 · 本体构建问询台(多模态 LLM 自动建模)

- **状态**: delivered
- **关联**: [[DR-002-multimodal-llm-anti-fraud-build]]、[[DR-003-runtime-neutral-naming]]、[[DR-008-datasource-connection-model]]、[[DR-009-object-cn-display]]

## 目标 / Goal
把「本体构建」重做为对话式**多模态 LLM × 多智能体自动建模**问询台(算法亮点):上传多模态数据 / 连接多库 / 编排技能,
以对话驱动构建可审计企业本体(反造假取证),并在页内可视化结果、可跑通全流程。

## 交付 / Deliverables
- [x] SSE 流式 agentic 步骤:证据聚合 → LLM 抽取 → 反造假裁决 → 摘要(`/api/build/inquire`);方法徽章如实标注 LLM/兜底。
- [x] 左工作区:多数据源(内置 + SQLite + 外部库登记)、多模态上传(表格/文档/建表代码/图像;Excel 知识包 openpyxl 解析)、技能编排(内置 5 + 自有 SKILL.md)、已构建本体清单。
- [x] 对话台:新建对话、**历史对话**(localStorage `bc_convs` 持久化,「历史对话 ▾」弹窗可回看/切换/删除,回放含结果卡与内联预览)、欢迎示例、流式执行记录、结果卡(五格统计 + 摘要)、**对话内内联 G6 本体预览**(点节点看绑定表/字段/指标)、动态面板高度(随对话增长,封顶于左栏)。
- [x] 表单预填真实可用默认参数(SQLite 绝对路径/命名/默认技能 ontology-agentic);默认多模态数据为真实知识包「示例经营看板指标-一级.xlsx」。
- [x] 对象/事件显示有意义中文名(cn),抽取 prompt 强化中文命名。

## 任务 / Tasks
1. `_gather_evidence`/`_llm_extract_ontology`/`_adjudicate_ir` + `build_inquire` SSE(超时 220/250s,证据瘦身)。
2. `/api/build/defaults`·`built`·`delete`·`sources`·`connect`·`skills`·`skill/upload`。
3. 前端 `bcInquire`/`bcRenderViz`/`bcNew`/`bcBuilt`/`bcSyncHeight` + 内联 G6 + 动态高度。

## 验收 / Acceptance
- 实测:示例主库 + xlsx 证据 → LLM 抽取 77 对象、中文名(企业/事业部/工厂/部门/客户),反造假 verified/candidate;183.6s 成功(超时修复后)。
- 兜底:LLM 离线 → quick_build 108 对象,方法徽章标注。
- 内联预览渲染、节点点选详情、新建对话清空(销毁 G6 实例防泄漏);`test_all.py` 构建相关断言通过。
