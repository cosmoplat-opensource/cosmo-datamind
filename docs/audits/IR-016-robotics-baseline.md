# IR-016 T01 · 机器人兼容基线 — DR-065

本轮重新执行原归档的代表测试和兼容探针，结果见 [原始记录](IR-016-baseline-results.json)。
这份记录描述迁移前状态，不表示已经实现机器人领域包或通过集成验收。

## 来源与环境

来源是用户指定的 `/home/dyh/workspace/Project/yang_implementation/归档`。
冻结输入见 [夹具清单](../../tests/fixtures/robotics/manifest.json)，包括来源 XML、节点模型、构建侧规则、三份 OWL 和两份任务/配置样例。
用于复测的 17 份代码和输入文件摘要记录在原始结果的 source_sha256 中。
复测使用 DataMind 虚拟环境：Python 3.10.12、rdflib 7.6.0、pySHACL 0.31.0。
所有原代码复制到临时目录后执行，禁止网络连接，原始文件复核摘要未变。

## 实际结果

| 场景 | 本轮实际结果 | 解释 |
|---|---|---|
| ontology/test_ontology.py | 退出 0 | 原构建语义、来源往返与证据边界测试通过 |
| ontology/test_skill_runtime.py | 退出 0 | 作者侧公开配置和符号数据流测试通过 |
| agent/test_skill_runtime.py | 退出 0 | agent 固定模板与公开输入测试通过 |
| agent/test_owl_contract.py | 退出 0 | agent 加载契约及证据拒绝测试通过 |
| ontology/test_ontology_only.py | 退出 1 | 混用顶层 skill_runtime，缺少 CONDITION_SPECS；预期草稿状态的断言未通过 |
| fresh_build | 退出 0 | 新构建图通过本地 SHACL，但缺少 artifactContract |
| consumer_contract_matrix | 退出 0 | 新构建与构建侧存量 OWL 被 agent 拒绝；agent 真实与演示 OWL 可按各自模式加载 |
| draft_vs_strict_policy | 退出 0 | 源码检查确认作者编译入口显式 allow_unresolved=True；此项是策略代码证据，不冒充完整行为验收 |
| strict_incomplete_task | 退出 0 | 固定任务替身驱动真实 agent 返回 needs_information，未写候选 |

9 个探针均匹配预先声明的现状，其中跨目录测试本身失败，不能将“基线匹配”写成“全部功能通过”。
本轮只运行表中项目，前期更多测试的通过记录不计入本轮统计。
pySHACL 0.31.0 在上述项目中可运行，不代表所有集成场景都已通过依赖兼容验收。

## 复测入口

在 DataMind 仓库根目录运行以下命令，报告路径必须是尚不存在的新文件：

```bash
.venv/bin/python scripts/probe_robotics_baseline.py \
  --archive /home/dyh/workspace/Project/yang_implementation/归档 \
  --output .check-results/ir016-baseline-rerun.json
```

该工具只有审计旧项目时需要 archive 参数，未来集成运行和 T02 测试不依赖该绝对路径。
探针返回 0 表示观察符合已登记基线，输出中保留原始退出码及错误，不表示既有缺陷被修复。
冻结夹具摘要与记录可用 `.venv/bin/python -m pytest tests/unit/test_robotics_baseline.py -v` 检查。

## 对下一任务的约束

T02 首先要求显式包导入及固定模板语义，生产模块缺失应产生正常测试失败而非跳过。
T03 不能把原同名文件简单放进 sys.path，也不能为消除重复而把作者草稿策略变成严格生成策略。
新构建到消费的发布契约在 T04～T05 验证，不能用复制 agent 预置 OWL 达成通过。
