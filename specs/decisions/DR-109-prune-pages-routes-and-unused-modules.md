# DR-109 · 去掉四个页面和 12 条已被取代的接口

状态：已实现。日期：2026-11-14。
关联：UI-PRUNE-02、DR-108、DR-101；删除范围经使用者确认，并按「不能让关键功能失效」复核过一轮。

## 判据

只去掉**已有替代入口**或**只为被删页面服务**的东西。没有替代入口的独立能力，即使界面没有接入，也保留接口。

## 去掉的

**页面（4 个）**：本体库、技能中心、作业中心、构成规则。页面数 22 → 18。

**接口（12 条）**：

| 接口 | 替代入口 |
|---|---|
| `/api/build/run`、`/api/jobs`、`/api/job/<id>` | 构建统一走 `/api/build/jobs`（DR-101）；纯数据驱动构建仍是它的降级路径 |
| `/api/skills`、`/api/skill/run`、`/api/ont/skill/<name>`、`/api/ont/skills/install`、`/api/ont/skills/write` | 技能的查看、编辑、沉淀在构建页的技能编排（`/api/build/skills` 一组）。带参数运行技能脚本的入口不再提供 |
| `/api/outputs`、`/api/outputs/file`、`/doc/<name>` | 成果库页面此前已去掉；`ui/` 下没有可供 `/doc` 提供的文档页 |
| `/api/ont/rules` | 只为构成规则页提供统计；运行时切换在引擎设置里 |

路由数 140 → 128。集成套件 565 → 548 个检查点。
随之去掉通用后台作业（`run_job`、`JOBS`）；`/api/sysinfo` 的作业数改为正在执行的构建作业数。

## 复核后保留的

第一版删除方案里还包括下面这些，复核时逐条查了调用方和替代入口，全部保留：

| 接口 | 保留原因 |
|---|---|
| `/api/viz/delete` | 已保存看板的唯一删除途径；现场验收脚本靠它清理自己建的看板 |
| `/api/ont/save`、`/api/ont/forge`、`/api/ont/forged`、`/api/ont/forged/<id>`、`/api/ont/forged/delete` | 把人工编辑后的本体存成版本、锻造为 Turtle 并校验，没有别的入口；已有的版本需要能删 |
| `/api/ont/drift/<key>`、`/api/ont/chain` | 数据源漂移与链路核验（DR-025） |
| `/api/ont/rulebook/<key>`（读、写、删）、`/api/ont/decide/<key>` | 规则与确定性推理（DR-028） |
| `/api/ont/health/<key>` | 图结构检查的按需入口（DR-030） |
| `/api/ont/compat/<key>`、`/api/ont/modules/<key>` | 向后兼容影响面与模块化建议（DR-031） |
| `/api/ont/cq` | 部署验收脚本逐项对照 |
| `/api/ont/rebuild` | 上游智能体技能按此接口丢弃草案层 |
| `/api/eval/set` | 数据来源的验收测试读取评测题集 |
| `/api/ont/usage/<key>` | 问数记录的使用度由它读出 |

模块 `rule_engine.py`、`drift_check.py`、`compat_check.py`、`module_split.py` 及其单元测试随之保留。

## 新增

`GET /api/ont/completeness/<key>?shacl=1` 按需返回导出 Turtle 的 SHACL 校验结果（是否符合、违反条数、三元组数），
不必为了校验而先锻造出一个版本。不带该参数时不执行。

## 影响

- 本体库页面去掉后，本体版本的列表与删除只有接口，没有界面入口；版本仍会出现在图谱列表里，可打开、查询、导出。
- 外部调用方若依赖上表「去掉的」12 条接口，会得到 404。
