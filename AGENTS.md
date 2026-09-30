# AGENTS.md · 给 AI 编程助手(与人)的开发约定

本仓用七种范式组成一个闭环(见 `specs/decisions/DR-066-ai-era-development-paradigms.md`)。
改动系统行为时按下面的顺序走;每一步都有可执行的检查,跳步会在 CI 变红。

## 一次改动的顺序

1. **找上下文(DDD)**:读 `specs/models/context_map.json`,确定改动落在哪个限界上下文、用哪些通用语言。
   新模块先在地图里登记;模块只能依赖地图允许的上游,领域上下文不得依赖 Web 框架。
2. **先写规格(SDD / STDD)**:
   ```
   python3 scripts/stdd.py new REQ-ID "验收标准" --context <上下文> --feature <功能> --decision DR-xxx
   ```
   它在 `specs/acceptance.json` 登记需求,并在 `specs/features/<功能>.feature` 追加带 `@REQ-ID` 标签的待实现场景。
3. **写行为场景(BDD)**:把场景里的 `<…>` 换成业务语言(假如 / 当 / 那么)。此时运行
   `pytest tests/bdd` 是红的(步骤未定义)。
4. **写失败的测试(TDD)**:在 `tests/bdd/steps.py` 补步骤,在 `tests/unit` 写单测;确定性模块再补性质测试
   (`tests/unit/test_properties.py`,固定种子)。确认它们是红的。
5. **改模型再改代码(MDD)**:关系状态、有效行规则改 `specs/models/relation_lifecycle.json`
   (代码经 `ontology_domain.py` 读取);构建步骤增删改 `docs/pipelines/ontology_build.yaml` 的 `sse_steps`。
   然后写代码让测试变绿,运行 `python3 scripts/gen_models.py` 更新模型投影。
6. **埋观测(ODD)**:新流程接入 `observability.Run` / `count` / `observe`;需求里写上 `metrics`。
   敏感内容只记指纹。
7. **收口**:把测试写进需求的 `tests`,运行
   ```
   python3 scripts/stdd.py status --strict
   python3 -m pytest tests/ -q
   python3 scripts/check.py --suite unit
   ```

## 不变的原则

- `verified` 只来自可回放的数据证据;模型不得自评,人工结论(asserted / rejected)只能由人授予。
- 源库只读;凭据、内网地址、客户名不进入代码、技能、测试与提示词。
- 需求表、场景、模型是唯一真相来源;文档里的数字以测试为准。
