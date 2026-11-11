# IR-024 T05 GREEN — TBox/ABox 包内核心

- 日期：2026-11-10（Asia/Shanghai）。
- 授权：用户了解 T04 测试内容与模型替身边界后要求“继续下一步”。
- 当前状态：T05 GREEN；79 项核心测试及 62 项旧分流测试通过，完整 12 阶段检查退出码 0；不是 IR-024 整轮交付。
- 机器记录：[完整结果与实现／测试摘要](IR-024-t05-green-results.json)。
- 依据：[DR-070](../../specs/decisions/DR-070-robotics-dual-build.md)、[核心接口 v1](../../specs/contracts/robotics-modeling-core-v1.md)。
- 上阶段：[T04 RED](IR-024-t04-red.md)。

## 实现内容

| 模块 | 职责 |
|---|---|
| `modeling/evidence.py` | 固定双 XML 角色、来源摘要、去重事实及完整出现位置；保留的注释／处理指令也受节点上限约束 |
| `modeling/vocabulary.py`、`data/` | 包内版本化词表与规则映射，无外部工程路径或密钥依赖 |
| `modeling/_tbox_validation.py`、`tbox.py` | 本地结构／语义验证、纯阶段转换、固定类别基线、快照确认、平台模型请求和一次格式修正 |
| `modeling/_schema.py`、`_extractor.py`、`abox.py` | 确认及来源核对、确定性实例与绑定抽取、逐项遗漏报告、带真实证据位置的错误 |
| `modeling/export.py` | 同一已选骨架与有效断言的 RDF/XML 导出，保留并集和类型，不补旧 UR 契约 |
| `modeling/common.py`、`prompts.py` | 确定性摘要、公开错误和版本化提示词 |

复用算法与修改内容可逐文件追溯到 [迁入清单](../../specs/sources/IR-024-t05-migration.json) 和原 [来源摘要](../../specs/sources/IR-024-yang-sources.json)。
词表、映射保留来源原始字节与 SHA-256；算法改为相对包导入，增加平台组合与定位，不通过 sys.path 动态加载外部工程。
来源工程的 `.env`、独立网络客户端、Web 服务、历史 sessions/generated 与 ABox 解释型模型调用没有迁入。
新增运行时语义层依赖 `jsonschema>=4.26.0`；开发依赖已有 `jsonschema==4.26.0`，未放宽检查门槛。

TBox 每轮经传入 BuildRoute 调用平台模型；不读取模型密钥、不保存原端点、不切换提供方。
一次操作最多一次格式修正；词表／来源／关系等语义失败直接报告，传输失败沿用平台错误，不自动换模型或走旧算法。
确认与编辑返回新状态，保留调用者的旧快照；类别和能力问题在属性阶段受确认基线约束。
ABox 不接收 route/model/client/prompt，只根据已确认 TBox 与 XML 规则抽取，错误源位置来自实际证据。
导出先验证传入的来源集合，不再采用上游“从结果引用反推允许来源”的历史导出做法。
设计 RDF 不伪造 artifactContract，旧消费者仍拒绝将其直接作为 UR 应用候选。

## 定向测试

```bash
.venv/bin/python -m pytest \
  tests/unit/test_robotics_modeling_evidence.py \
  tests/unit/test_robotics_tbox_design.py \
  tests/unit/test_robotics_abox_design.py \
  tests/unit/test_robotics_design_export.py \
  tests/unit/test_robotics_dual_build_routing.py \
  tests/unit/test_robotics_dual_build_http.py \
  -ra --tb=short --junitxml=.check-results/IR-024-t05-green/targeted.xml
```

141 passed、0 failed、0 skipped，用时 6.24 秒；其中 T04 新核心 79 项与 T03 原路径 62 项。
已审阅测试及夹具未修改，摘要依据 T04 RED 记录逐项核对。
新增包 ruff 与 mypy 通过。
日志：[定向日志](../../.check-results/IR-024-t05-green/targeted.log)、[JUnit](../../.check-results/IR-024-t05-green/targeted.xml)。

## 整条核心链路诊断

使用真实 XML、迁入的完整词表、真实 T03 配置解析与 BuildRoute，只有最外层 runtime.run_turn 返回两份合成 TBox 提议。
依次执行 new_design、revise、confirm_classes、revise、confirm_schema、ABox extract 和 OWL 导出。
结果：两次平台适配调用、revision=4、25 次调用、129 个绑定、2706 个 RDF 三元组；重复抽取结果相同，重复导出图同构。
网络连接被禁止；公开状态和提示中不含测试密钥；ABox 和导出不会增加模型调用。
额外诊断确认超过 20000 个保留注释或处理指令被拒绝。
记录：[核心链路](../../.check-results/IR-024-t05-green/core-probe.json)。
这是程序集成证据，不是实际 LLM 输出质量证据；真实模型调用数为 0。

## 完整回归

```bash
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" \
  .venv/bin/python scripts/check.py --suite all \
  --report-dir .check-results/IR-024-t05-green/all-final
```

最终退出码 0，12 个阶段全部通过。

| 检查 | 结果 |
|---|---|
| ruff / mypy | 通过 |
| 单元回归 | 1822 通过、5 项可选上游缺失跳过；用时约 28 分钟 |
| 覆盖率 | 84.7%，门槛 81.0% |
| HTTP 集成 | 551 通过、8 项可选上游缺失跳过 |
| 严格十轮 | 10/10 |
| UI 走查 / 实操 | 64 / 50 通过；实操 3 项真实模型未配置跳过 |
| 数据构建浏览器 | 10 通过 |
| 机器人构建 / 审阅 / 任务浏览器 | 18 / 16 / 12 通过，无跳过 |

完整日志见 [检查汇总](../../.check-results/IR-024-t05-green/all-final/summary.json)。
653 条实际验收引用、文档链接、实现／来源及已审阅测试摘要与 diff 格式检查通过。
较早一轮在复核发现注释／处理指令也需节点计数后主动中止（当时未见断言失败）；日志留在 `all/`，不作为通过证据。
修正后重新跑定向检查和完整回归；最终以 `all-final/` 为准。
检查使用临时工作目录与服务，不启动／重启用户常驻服务。

## 阶段边界

T05 已实现可调用的平台模型设计核心，但机器人 HTTP 上传接口仍明确返回 model_build_pending；未绕过后续服务与 UI 阶段。
设计会话落库、事务、幂等、并发、失败提交及中断恢复属于 T06/T07；纯状态函数不等同于持久化保证。
T08/T09 接入统一页面、对话确认、修订恢复、设计候选详情及导出，T10 再记录真实模型试运行。
摘要证明一致性，不是签名授权；可信服务须从受控确认状态取得封装并核对原始来源，不能接受客户端伪造的“已确认”对象。
XML 中有可追溯来源不等于模型解释在领域上正确，仍需用户确认设计。
整轮 Deliverables／Acceptance 保持未勾选，IR-024 继续 in-progress。
