# IR-024 T03 GREEN 记录 — DR-070

- 日期：2026-11-10（Asia/Shanghai）。
- 授权：用户确认 T02 测试覆盖无问题，要求继续 T03 实现并让测试转绿。
- 当前状态：T03 GREEN；62 项定向测试通过，经用户批准调整旧测试后 12 个回归阶段均有通过记录；不是 IR-024 整轮交付。
- 机器记录：[最终结果及源码摘要](IR-024-t03-green-results.json)。
- 上阶段：[T02 RED](IR-024-t02-red.md)；契约：[构建契约 v1](../../specs/contracts/robotics-dual-build-v1.md)。

## 实现

| 文件 | 改动 |
|---|---|
| `domain_runs/robotics_build_routing.py` | 复用平台配置和运行时，区分未配置与错误配置；固定 provider/model/非敏感配置身份；一次请求失败不换引擎或改为离线 |
| `domains/robotics/source_admission.py` | 共同 XML 解析、DTD／实体拒绝、20000 节点／100 层限制及来源角色检查 |
| `domain_runs/build_service.py` | 公开准入注入路由解析器；解析前后均不登记半成品；重放优先；description 参与幂等冲突 |
| `bp_robotics.py` | 连接共同准入，返回带位置的问题；投影 legacy_xml/model=null；能力接口如实区分可构建与尚待接入 |
| `openai_runtime.py` | 提取现有默认模型常量，路由和运行时共享同一值，未改变默认模型或数据本体调用行为 |

没有修改 T02 已审阅的 62 项新测试；另有一项冲突的旧 HTTP 断言经用户批准更新，详见下文。
没有迁入新 TBox/ABox 生产核心，没有修改现有资产字节或数据库 schema。
原 BuildService 的内部离线调用保留后台校验行为，旧任务及执行器仍可处理既有不合法来源并产生失败报告。
对外 HTTP 新请求先做共同 XML 检查，再判定模型配置；没有把完整旧 UR 模板限制移到共同解析器。
旧 build_ontology 运行的 method/model 是读取投影，不重写历史 manifest 或旧请求指纹。

## 定向 GREEN

```bash
.venv/bin/python -m pytest \
  tests/unit/test_robotics_dual_build_routing.py \
  tests/unit/test_robotics_dual_build_http.py \
  -ra --tb=short --junitxml=.check-results/IR-024-t03-green/junit.xml
```

结果：62 passed，0 failed、0 error、0 skipped，退出码 0，报告用时 4.32 秒。
本轮生产修改文件的 ruff 和 mypy 检查通过。
记录：[定向日志](../../.check-results/IR-024-t03-green/pytest.log)、[JUnit](../../.check-results/IR-024-t03-green/junit.xml)。

## 额外隔离诊断

用临时 Flask 应用及 SQLite 资产库执行，所有 socket 连接被禁止，模型仅使用合成运行时边界。

1. 无模型准入的旧请求，在新增有效模型配置后以相同 request_id 重放，仍返回原离线运行且不增加数据库记录。
2. 相同 request_id 改变 description 时返回 409，不覆盖原运行。
3. 有效模型路径在 T03 返回 model_build_pending，能力接口 build_ready=false，不调用模型、不创建新运行或回退旧方案。
4. 交换行为树与节点模型文件角色时返回 invalid_source，不登记来源。

4 项通过，网络模型调用数为零。
记录：[诊断结果](../../.check-results/IR-024-t03-green/manual-probes.json)。

## 全量回归

首轮在旧 HTTP 用例 `test_actual_invalid_xml_is_accepted_then_reported_as_located_failure` 发现冲突：旧约定返回 202 后由后台报告坏 XML，新 DR-070 要求共同准入返回 400 且不登记来源／运行。
2026-11-10 用户明确回复“同意，更新冲突的旧测试”。
据此将该项更名为 `test_actual_invalid_xml_is_rejected_before_admission_with_location`，保留真实 HTTP、错误代码、来源角色、行号与路径不泄露检查，并改为验证数据库／文件零变更及无 run_id。
新断言单独运行 1 passed；同步 RBU-03／RBU-04 的测试引用，并从下载验收 RBU-06 移除此用例，因为准入拒绝不再生成工件。
旧内部构建的失败报告继续由 pipeline／executor 用例覆盖。
首轮记录保留在 `.check-results/IR-024-t03-green/all/`；因已收集的旧断言必然失败，在单元阶段主动中止，不将该轮记为通过。
更新后从头重跑以下完整检查，不跳过冲突用例或降低覆盖率门槛。

```bash
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" \
  .venv/bin/python scripts/check.py --suite all \
  --report-dir .check-results/IR-024-t03-green/all-final
```

完整运行已结束，退出码 1：前 11 个检查段通过，最后任务浏览器段 11 项失败、1 项通过。
单元回归 1743 passed、5 项缺少可选上游引擎跳过；覆盖率 84.5%，门槛 81.0%。
HTTP 集成 551 通过、8 项可选上游跳过；严格十轮 10/10。
旧 UI 64 通过；UI 实操 50 通过、3 项未配置真实模型跳过；数据构建、机器人构建及机器人审阅浏览器段均通过。
任务浏览器失败集中在准备资产：旧 TaskSite 启动即配置任务解析模型，随后还要求上传 XML 走旧构建；DR-070 下请求正确走模型路径并返回 model_build_pending，因此尚未到达任务语义断言。
2026-11-10 用户明确回复“同意，调整夹具并重跑受影响检查”，已应用准备顺序修正：先无模型真实构建旧资产，再启用本地合成模型测试任务。
原有 59 条 assert 和 42 条 Playwright 断言按 AST 核对全部不变，继续使用真实 HTTP／存储／编译／下载；未配置模型用例保持离线。
T02 的 62 项新测试及本轮生产实现均不因这一夹具冲突而修改。
该轮日志与汇总保留，不将退出码 1 记为全绿；用户已批准仅复跑受影响检查，结果另记于 `.check-results/IR-024-t03-green/fixture-rerun/`。
复跑沿用 check.py 的 isolated_env／run_check、临时工作目录、超时及真实浏览器入口，包含整个任务浏览器组，以及全仓 ruff／mypy。
生产文件及 T02 已审阅测试的摘要固定，复跑结束重新校验，不重跑与本次夹具变更无关的 28 分钟单元检查及其他已通过阶段。
运行环境由 check.py 隔离，未启动或重启用户的常驻服务。

### 最终复跑结果

受影响检查复跑退出码 0：全仓 ruff／mypy 通过，任务浏览器 12 passed、0 failed、0 skipped，用时 116.359 秒。
生产实现的 5 份文件及 T02 的 3 份测试／夹具 SHA-256 与完整回归时一致。
最终结果汇总使用各阶段最近一次有效记录，不改写前一次完整命令的失败日志。

| 检查 | 最终结果 |
|---|---|
| ruff / mypy | 通过（夹具调整后重跑） |
| 单元回归 | 1743 通过、5 可选上游跳过 |
| 覆盖率 | 84.5%，门槛 81.0% |
| HTTP 集成 | 551 通过、8 可选上游跳过 |
| 严格十轮 | 10/10 |
| UI 走查 / 实操 | 64 / 50 通过；实操 3 项真实模型未配置跳过 |
| 数据构建浏览器 | 10 通过 |
| 机器人构建 / 审阅浏览器 | 18 / 16 通过 |
| 机器人任务浏览器 | 12 通过（夹具调整后重跑） |

另已核对 618 条实际验收引用、87 个唯一验收编号、文档本地链接与 diff 格式。
T03 完成，但交付／验收清单保持未勾选；TBox/ABox 和设计会话的业务验收仍在后续阶段。

## 未完成的后续能力

有模型时，路由模块已能够选定运行时并处理受控调用；T03 的公开构建端点尚不执行 TBox/ABox 建模。
按 T01 的阶段边界，模型路径明确返回 503/model_build_pending，直到 T05/T07/T09 的核心、会话与 UI 接入完成。
这不是“模型失败后的离线回退”，也不是声称新建模功能已可用。
原无模型 XML 路径保持可用。
持久设计会话、人工确认、ABox 抽取、通用候选视图及应用兼容准入仍需后续任务完成。
合成模型测试不能证明真实模型的自然语言建模质量，本轮未调用真实模型。
