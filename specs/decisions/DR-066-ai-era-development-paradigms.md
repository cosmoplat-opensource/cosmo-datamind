# DR-066 · AI 时代的开发范式:SDD / STDD / BDD / TDD / DDD / MDD / ODD 组成闭环

- **状态 / Status**: accepted
- **日期 / Date**: 2026-10-01
- **关联 / Refs**: [[DR-045-engineering-harness-and-tdd]]、[[DR-046-deterministic-module-unit-tests]]、
  [[DR-065-end-to-end-parity-code-contracts-and-scoped-keys]];
  实证=`specs/acceptance.json`、`specs/features/*.feature`、`specs/models/*.json`、`docs/pipelines/ontology_build.yaml`、
  `ontology_domain.py`、`observability.py`、`scripts/stdd.py`、`scripts/gen_models.py`、`tests/bdd/`、
  `tests/meta/test_models.py`、`tests/meta/test_context_map.py`、`tests/meta/test_stdd_traceability.py`、
  `tests/unit/test_properties.py`、`tests/unit/test_observability.py`、`AGENTS.md`

## 上下文 / Context

本仓已有 SDD(决策记录、需求表)与 TDD(近千项测试),但最近几轮(DR-059…065)暴露出几类问题,
单靠这两种范式防不住:

- **需求没登记**:需求表停在 IR-015 的 14 条,最近七个决策的行为没有一条进入需求表,只有散落的单测。
- **行为说不清**:「按租户作用域成立的关系问数须带租户」这类规则,读单测很难看出业务含义。
- **领域词汇散落**:有效行规则名在复核脚本、验收规则、问数三处各写一份。
- **流水线说明漂移**:构建流水线说明停在 2.4 版,新加的七个步骤没有登记。
- **问题靠人翻日志发现**:模型超时后回退构建全库、口径校验误拦、关联缺租户计数虚高,都是人工对照才看到。
- **边界只在脑子里**:哪些模块是领域层、哪些是 Web 层,没有可检查的定义。

## 决定 / Decision

七种范式各落一个可执行的产物,并串成一个闭环:

| 范式 | 产物 | 检验 |
|---|---|---|
| SDD | `specs/acceptance.json` 为需求唯一真相来源;新需求写明限界上下文、决策、场景、测试、模型、指标 | `test_acceptance_traceability`、`test_stdd_traceability` |
| BDD | `specs/features/*.feature` 中文 Given-When-Then 场景,覆盖两种数据场景的关键行为 | `tests/bdd`(自带最小执行器,每个场景一条测试) |
| STDD | 需求 ↔ 场景 ↔ 测试双向追溯;`scripts/stdd.py new` 先写规格与待实现场景(红),`status --strict` 为 CI 关卡 | `test_stdd_traceability`、`test_stdd_tool`、CI `stdd` 关卡 |
| TDD | 性质测试(固定种子)覆盖表族、代码契约、键名相容、观测脱敏 | `tests/unit/test_properties.py` |
| DDD | `specs/models/context_map.json` 限界上下文与通用语言;共享内核 `ontology_domain.py` | `test_context_map`(架构适应度:归属唯一、依赖方向、领域层不依赖 Web、技能脚本自包含) |
| MDD | `specs/models/relation_lifecycle.json` 关系状态机为代码常量的来源;流水线模型登记全部构建步骤;`scripts/gen_models.py` 生成图 | `test_models`(步骤双向一致、迁移合法、投影同步)、CI `models` 关卡 |
| ODD | `observability.py` 构建与问数的追踪(run_id、步骤耗时)和指标;`/api/obs/metrics`、`/api/obs/runs` | `test_observability`、BDD 场景 OBS-01/02 |

实现要点:

- 不新增运行时依赖:Gherkin 执行器、模型读取、观测都只用标准库;模型用 JSON,流水线模型沿用 YAML 但以正则读取。
- 观测只记计数、耗时与指纹:问句、SQL、口令等敏感属性只留 SHA-256 前 12 位与长度,字符串去除 IP 与主机;
  写盘失败不影响业务。
- 关系状态迁移的角色(裁决器 / 复核 / 人)写进模型;测试保证自动流程不产生 asserted / rejected,
  复核的每次升降级都是模型允许的迁移。

## 过程中发现并修正的问题(范式本身的产出)

- **TDD 性质测试**发现表族识别在小湖仓上的缺陷:某厂独用一种命名约定时,厂码学不成前缀,
  各厂副本归不到一起。修正为门槛随规模(至少 4 种、且不少于表数 1%),并在湖仓形态下(≥30% 的表带数仓层级前缀)
  按尾部对齐、以列相似度把关。业务库不做尾部对齐——能碳实测中尾部相同、列相近的
  「系统设备关系 / 企业设备关系」「指标周期的实例与定义」是不同概念。
- **ODD 单测**发现追踪在手动结束后被上下文管理器重复结束。
- **MDD 步骤核对**发现流水线说明缺少 DR-059…065 新增的 12 个步骤,已补登记(3.0 版)。
- **变异验证**:临时让领域模块导入 Web 层,架构适应度测试当场指出越界依赖。

## 后果 / Consequences

- 需求 27 条(本迭代新增 13 条),行为场景 12 个,全部双向追溯;CI 单元套件新增 `stdd`、`models` 两道具名关卡。
- 与专用脚本的归并一致率不再作为精确度标准:人工核对分歧后,多数是专用脚本漏剥小来源前缀(wm_ / cz_ / jz_),
  通用算法把同一实体并回一族是对的。
- 新增 2 条路由(137 总数)。

## 备选与不做 / Alternatives

- **引入 pytest-bdd、hypothesis**:不做。自带执行器与固定种子生成足够,且不改变「按 requirements 装完就能全绿」的承诺。
- **OpenTelemetry 导出**:暂不做。事件格式与 OTel 的 span 语义对应(run = trace,step = span),需要时可加导出器。
- **由模型生成代码骨架**:不做。模型生成文档与常量、约束代码,但不生成业务代码——避免重走 MDD 过度生成的老路。
