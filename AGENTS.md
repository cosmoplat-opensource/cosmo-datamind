# AGENTS.md · 给 AI 编程助手(与人)的开发约定

改动系统行为时:

1. **先写失败的测试**,再写代码使其通过。确定性模块放 `tests/unit`;输入空间大、个例测不全的逻辑
   (如表族识别)补一条固定种子的性质测试(`tests/unit/test_properties.py`)。
2. **登记需求**:新能力在 `specs/acceptance.json` 加一条「需求 → 验收标准 → 测试」,并写决策记录
   `specs/decisions/DR-xxx`。`tests/meta/test_acceptance_traceability.py` 会检查引用的测试存在。
3. **保持以下约束**(`tests/meta` 自动检查):
   - 增删构建步骤时同步 `docs/pipelines/ontology_build.yaml` 的 `sse_steps`;
   - 有效行规则只改 `specs/models/valid_rows_rules.json`(验收与问数经 `ontology_domain.py` 读取);
   - 领域模块不依赖 Web 层;技能脚本只用标准库。
4. **新流程接入观测**:`observability.Run` / `count`;问句、SQL 等敏感内容只记指纹。
5. **动了构建或问数链路**:在 `tests/scenarios/fixtures.py` 的合成库里补上对应的问题场景和标准答案,
   运行 `python3 scripts/scenario_bench.py` 查看逐项检查结果(不连模型,约 1 秒)。
6. 提交前运行 `python3 scripts/check.py --suite unit`(lint、类型、单测、场景基准、覆盖率)。

不变的原则:`verified` 只来自可回放的数据证据,人工结论只能由人授予;源库只读;
凭据、内网地址、客户名与客户系统的具体名称不进入代码、技能、测试与文档。
