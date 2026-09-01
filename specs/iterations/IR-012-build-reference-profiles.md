# IR-012 · 恢复行业参照与本体标准构建配置

- **状态 / Status**: delivered
- **日期 / Date**: 2026-09-01
- **关联 / Refs**: [[DR-052-build-industry-and-standard-references]]、
  [[DR-050-executable-ontology-build-gate]]

## 目标 Goal

把半自动构建早期原型中遗失的“选择或不选择行业/本体标准”能力，恢复为可追溯、
可验收、能在新建与继续构建中稳定工作的正式功能。

## 交付 Deliverables

- [x] `build_references.py` 单一目录与配置归一：制造/化工/PCBA；BFO+IOF/ISA-95/UFO；
  none/reference/constraint 完整语义。
- [x] `GET /api/build/references` 与构建请求校验；未知 id/mode 在启动 SSE/LLM 前返回 400。
- [x] 选择进入 LLM 提示；显式 none 不含 BFO/IOF 输出字段或规则。
- [x] 对整图做行业/标准候选注释；软参照缺口待审、强约束缺口阻断。
- [x] 配置写入 manifest/history；继续构建自动继承且可覆盖。
- [x] 前端正式控件、模式说明、当前状态、过程卡、结果卡与历程回显；逻辑抽到
  `ui/modules/build-references.js`，`ui/index.html` 仍低于 2600 行。
- [x] 图谱、对象详情与 OWL 导出尊重显式非 BFO 选择；旧 IR 保持兼容。
- [x] 单元/接口/静态 UI/系统级断言与 SDD 流水线文档同步。

## 任务 Tasks

1. 建立版本化配置、目录、提示片段、确定性注释与参考质量报告。
2. 接入构建端点、manifest/history、迭代继承、整图应用与质量聚合。
3. 增加前端模块与控件，展示输入、过程、结果和历史。
4. 使图谱/详情/OWL 避免在显式 none/其它标准下回填 BFO/IOF。
5. 增加 TDD 回归并更新 DR/IR/流水线/架构说明。

## 验收 Acceptance

- [x] 制造/化工/PCBA 与 BFO+IOF/ISA-95/UFO/none 均能通过稳定 API 选择。
- [x] 显式 none 的提示、IR、图谱与 OWL 不含自动 BFO/IOF 映射；未传字段仍兼容旧默认。
- [x] 同一行业缺口在 reference 模式为 review，在 constraint 模式为 fail。
- [x] 迭代历史保存每轮配置；未显式覆盖时继承最近一轮。
- [x] 非法配置在调用模型和读取外部业务系统前拒绝。
- [x] 针对性单元/接口/UI 契约测试全绿；全量 `pytest` 381 项通过，隔离 WSGI
  系统回归 545 项通过、0 失败、8 项因上游引擎未配置而条件跳过。
