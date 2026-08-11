# 输入面消费方审计(「存而不用」清查)

日期:2026-07-27 · 触发:DR-021 教训(技能上传通道存在已久,但内容从未进构建 prompt)推广为固定审计项。
原则:**每一个输入面,都必须说得出它的消费方**;说不出的,要么接上,要么在 UI 上如实标注为「仅登记/展示」。

| 输入面 | 入口 | 消费方 | 状态 |
|---|---|---|---|
| CSV/TSV 上传 | /api/build/upload | uploads.db 建表 → 问数上下文(up.*)/ SQL 工作台 / 可视化 / 口径校验白名单 | ✅ 在用 |
| 文档/代码上传(md/txt/sql/py…) | 同上(存档 uploads_*) | 构建取证 `_gather_evidence` → 抽取 prompt 的 docs 块 | ✅ 在用 |
| 图像上传(png/jpg…) | 同上 | 构建 prompt 的「引用但未解析的资产」清单(引用级,不做视觉解析) | ⚠️ 引用级消费,UI 已如实标注「图像作引用证据」 |
| 术语词典(655 条) | translate_cn 词表 | 术语管理页展示 + **问数检索扩展 expand_terms**(DR-019 接上) | ✅ 在用(曾是死代码,已修) |
| 构建技能(内置/自定义) | skills_seed / custom_skills | 抽取 prompt 方法论 `_skill_method_text`(DR-021 接上;skill_inject 步可见) | ✅ 在用(曾是死代码,已修) |
| 问数沉淀技能(qa_skills) | 问数「沉淀为 Skill」 | 智能体列表展示 + **问数命中复用 `_match_qa_skill`**(DR-022 接上;skill_reuse 步) | ✅ 在用(曾是死代码,已修) |
| 知识包(指标 Excel/看板口径) | IR knowledge_pack 字段 | 构建 cn 命名指引(prompt 要求⑤「优先复用…知识包里的中文术语」) | ✅ 在用 |
| 外部库连接(mysql/doris/pg) | /api/build/connect | conn/tables·preview·query·viz 实连(DR-019 C7 接上) | ✅ 在用(Hive 仍登记级,UI 如实标注) |
| API 数据源 | 同上 kind=api | api_fetch 物化 up.api_* → 问数/SQL/可视化 | ✅ 在用 |
| 动作 webhook(effects) | action_types.effects | 决策捕获登记(**刻意不外呼**,DR-015 边界;审批页展示效果文案) | ⚠️ 预留,属设计决定而非遗漏 |
| 评审意见/评审人 | /api/ont/apply reviewer | 元素盖章 review_by/time/reason → 评审页/图谱卡展示、编辑日志回放 | ✅ 在用 |
| 引擎配置(含 API Key) | 引擎设置页 → /api/engine/config | `srv_engine._ENGINE_STORE`(`store.JsonStore`,0600 替换前定权限 + 坏档告警)→ `_apply_engine_cfg` 注入进程 env | ✅ 在用(DR-044 首个迁移方) |
| 反馈意见(needs_work) | /api/chat/feedback | 评审页「本体迭代候选队列」+ 业务助手待办计数 | ✅ 在用 |

## 结论

- 13 个输入面中 11 个有明确消费方;2 个 ⚠️ 均为**有意为之的边界**(图像引用级、webhook 预留),且 UI 文案已如实说明——不构成「存而不用」。
- 2026-08-12 补记:`store.py` 抽象落地时一度**自身无消费方**(建了库没人调),
  按本表规范即刻接上首个消费方(引擎配置),而非留作「将来会用」。
- 历史上共发现并修复 3 起真死代码:术语词典(DR-019)、构建技能正文(DR-021)、问数沉淀技能(DR-022)。共性:**先建输入面、后忘消费方**;新功能评审时应把「消费方在哪」列为必答项。
