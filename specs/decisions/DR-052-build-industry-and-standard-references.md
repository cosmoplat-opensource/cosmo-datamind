# DR-052 · 半自动构建的行业参照与本体标准配置

- **状态 / Status**: accepted
- **日期 / Date**: 2026-09-01
- **关联 / Refs**: [[DR-002-multimodal-llm-anti-hallucination-build]]、
  [[DR-010-iof-bfo-alignment]]、[[DR-050-executable-ontology-build-gate]]；
  实证=`build_references.py`、`standard_assets.py`、`ontology/standards/manifest.json`、
  `server.py:/api/build/references|build_inquire`、`tests/unit/test_build_references.py`、
  `tests/unit/test_standard_assets.py`

## 上下文 / Context

早期静态建模原型展示过制造、化工、PCBA 等行业模板，但当前半自动构建页只消费
数据源、资料、技能、构建诉求与验收问题。
构建提示词还无条件注入 BFO/IOF 字段和规则，因此既不能选行业，也不能真正选择
“不使用本体标准”。

只恢复下拉框不够：若选择没有进入提示词、确定性后处理、质量门和构建历史，用户会
看到一个不会改变结果的假配置；若只改提示词，则模型可能为凑模板编造概念。

## 决定 / Decision

### 1. 稳定配置契约

构建请求新增版本化 `references`：

```json
{
  "version": 1,
  "industry": {"id": "manufacturing", "mode": "reference"},
  "ontology_standard": {"id": "isa95", "mode": "constraint"}
}
```

两项均可选 `none`；模式为 `reference`（软参照）或 `constraint`（强约束）。
旧客户端完全不传 `references` 时，继续按历史行为使用 BFO/IOF 软参照；显式传
`none` 时不得再由图谱、详情或导出层补回 BFO/IOF。

### 2. 目录与语义

内置行业参照为制造业、化工行业、PCBA/SMT；本体标准为 BFO 2020 + IOF Core、
ISA-95 / IEC 62264、UFO / OntoUML。
目录由 `build_references.py` 单一维护，`GET /api/build/references` 供 UI 消费。

- 行业参照提供核心概念、同义词与代表关系，用于命名归一、候选发现和缺口检查。
- BFO/IOF 使用官方关系名与定义域/值域检查。
- ISA-95 与 UFO 只产生可复核的候选对齐；不复制付费标准正文，不声称认证或完整合规。

标准选择必须同时报告对应机器可读资产的本地状态。仓库固定版本保存开放许可的
BFO 2020、IOF Core、gUFO，以及 MESA International 公开且允许免费使用/再分发的
B2MML XSD（ISA-95 XML 实现）。不复制 ISA/IEC 付费标准正文。运行时只解析本地文件，
不跟随 OWL imports 或 XSD import/include 发起网络请求；解析规模、来源版本、许可与
SHA-256 内容指纹进入目录和构建追踪。

### 3. 证据优先与双级质量门

选择先进入提议提示词，但模板项只有在数据库/文档有依据时才可建成对象或关系；
没有依据必须保持缺失并报告，不能为覆盖模板而编造。

确定性后处理对整张 IR 应用本轮配置并记录候选对齐。
软参照的缺口进入 `review_queue`，强约束缺口进入 `blocking_issues`，由
`build_quality` 汇总为 pass/review/fail。

### 4. 可追溯迭代

归一后的配置写入 `build_manifest.references` 和每轮 `build_history[].references`。
所选标准的本地资产版本与指纹写入 `build_manifest.reference_assets` 和每轮历史。
继续构建且调用方未显式传配置时，服务端继承底本最近一轮配置；UI 读取历史后同步
显示，用户可在本轮覆盖。

## 后果 / Consequences

- (+) 行业/标准选择真实影响提议、注释、验收、历史、图谱与导出，不再是装饰性控件。
- (+) “都不选择”具有可测试语义；旧 API 调用方仍兼容。
- (+) 强约束能暴露证据缺口，同时“不许凑模板”守住反幻觉边界。
- (+) 标准文件随应用离线加载，构建不再依赖临时网络；资产缺失/损坏会明确进入待审或阻断。
- (−) 内置行业词表是版本化参照，不是完整行业模型；新增行业须同时补目录、匹配规则与测试。
- (−) ISA-95/UFO 对齐为候选提示，生产级标准符合性仍需合法标准资料、领域专家与独立验证工具。

## 测试 / Tests

`tests/unit/test_build_references.py` 覆盖目录、归一、非法输入、显式 none、提示注入、
三种标准注释、参照/强约束门、图谱/OWL 不回填、接口与 UI 契约。
`tests/unit/test_standard_assets.py` 覆盖本地文件、解析规模、固定内容指纹、许可和提示上下文。
`test_all.py` 增加目录可达与未知行业拒绝的系统级检查。
