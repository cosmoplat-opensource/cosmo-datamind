# DR-109 · 再去掉四个页面、27 条接口和四个不再有调用方的模块

状态：已实现。日期：2026-11-14。
关联：UI-PRUNE-02、DR-108、DR-010、DR-101；删除范围经使用者确认。

## 决定

**页面（4 个）**：本体库、技能中心、作业中心、构成规则。页面数 22 → 18。
技能的查看与编辑仍在构建页的技能编排里；构建进度在构建页，作业由服务端持有（DR-101）；
运行时切换在引擎设置里。

**接口（27 条）**：

| 类别 | 接口 |
|---|---|
| 锻造与本体库 | `/api/ont/forge`、`/api/ont/save`、`/api/ont/forged`、`/api/ont/forged/<id>`、`/api/ont/forged/delete` |
| 技能中心 | `/api/skills`、`/api/skill/run`、`/api/ont/skill/<name>`、`/api/ont/skills/install`、`/api/ont/skills/write` |
| 作业中心与旧构建入口 | `/api/jobs`、`/api/job/<id>`、`/api/build/run` |
| 成果文件 | `/api/outputs`、`/api/outputs/file`、`/doc/<name>` |
| 本体治理（界面未接入） | `/api/ont/chain`、`/api/ont/drift/<key>`、`/api/ont/health/<key>`、`/api/ont/compat/<key>`、`/api/ont/modules/<key>`、`/api/ont/rulebook/<key>`（读、写）、`/api/ont/rulebook/<key>/delete`、`/api/ont/decide/<key>` |
| 其他 | `/api/ont/rules`、`/api/viz/delete` |

路由数 140 → 113。集成套件里对应的检查点一并去掉，565 → 470。

**模块（4 个）**：`rule_engine.py`、`drift_check.py`、`compat_check.py`、`module_split.py` 及其单元测试。
上述治理接口去掉后，这四个模块在生产代码里不再有调用方。`cq_check`、`health_check` 仍被构建验收使用，`usage_stat` 仍被问数使用，保留。

**随之去掉的内部代码**：通用后台作业（`run_job`、`JOBS`）、规则库的读写函数。`/api/sysinfo` 的作业数改为正在执行的构建作业数。

## 保留下来的，以及原因

原计划删除、核对调用方后保留：

| 接口 | 保留原因 |
|---|---|
| `/api/ont/cq` | 部署验收脚本用它与本地重放结果逐项对照 |
| `/api/ont/rebuild` | 上游智能体技能按这个接口丢弃草案层，并依赖它要求显式确认 |
| `/api/eval/set` | 数据来源的验收测试通过它读取当前来源的评测题集 |
| `/api/ont/usage/<key>` | 问数持续记录对象使用度，这是唯一的读取入口 |
| `/api/ont/accuracy`、`/api/ont/rejected-patterns` | 不在确认的删除范围内 |

## 形状校验改由覆盖率接口给出

导出 Turtle 的 SHACL 形状校验此前只在「锻造」接口里执行。接口去掉后，
`GET /api/ont/completeness/<key>?shacl=1` 按需返回校验结果（是否符合、违反条数、三元组数）；
不带该参数时不执行，避免每次读取覆盖率都生成并解析整张图。界面上暂时没有入口。

## 影响

- 已经锻造的本体（`forged_*`）仍可在图谱列表里打开、查询，但不能再新建或在界面上删除。
- 能带参数运行技能脚本的入口随技能中心一起去掉，对应的参数注入风险不再存在。
- 规则约束与确定性推理、数据源漂移检查、向后兼容检查、模块化建议这四项能力从产品中移除。
  README 与架构文档里的能力描述已同步。需要时可从版本历史恢复。
- 外部调用方若依赖上述接口，会得到 404。
