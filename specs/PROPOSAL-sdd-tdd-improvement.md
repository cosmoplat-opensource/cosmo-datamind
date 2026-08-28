# PROPOSAL · 以 SDD+TDD 极大改进 Cosmo DataMind 与半自动本体构建算法

> 状态:提议(未立项)。日期:2026-07-31。
> 消费方式:`Consult @specs/map.md`;本文件提议一批 DR(DR-035…DR-046)与 IR(IR-007…IR-011)。
> 每句独立成行,遵循 [[meta]] 记录格式。落项时把对应 DR/IR 从本文件拆分为独立文件并入编号。
>
> **历史说明（2026-08-28）：** 本文是研发前的差距盘点，所列计数、能力缺口和拟议术语均为当时快照，
> 不能作为当前系统说明。多项工作现已实现；文中 HermiT 和“多模态”属于拟议目标，本仓当前只执行
> RDF 解析与 SHACL 校验，也不解析二进制附件中的图像或正文。当前口径见 `README.md` 与 `ARCHITECTURE.md`。

## 0. 一句话结论

系统已是**SDD 教科书级**(34 DR / 6 IR / map+meta 主轴 / 提交与规约同步),
但**TDD 缺席**——535 条 `chk()` 是**实现之后补写的表征锁**,不是**实现之前写下的失败验收**;
且**工程校验缺失**(无 CI / lint / coverage / 固定依赖),强测试与「显式报错」设计原则都靠手动维持。
算法侧,裁决核**存在两份漂移实现**(`quick_build.py` 的门槛弱于上游 `relation_discovery.py`),
且论文早已点名的召回盲区(自引用键、多信号裁决、自适应 θ、反幻觉评测)尚未落地。
本提议用 TDD 的红-绿把这些一次性收敛,并把算法从「单库、纯 schema」推进到「多信号、多源、多模态」。

## 1. 现状盘点(已一手核验)

### 1.1 强项(勿动)
- SDD 规约真实:`specs/` 34 DR + 6 IR,`ARCHITECTURE.md` 每模块对齐 DR,提交与 map/DR 同步。
- 领域层优秀:11 个确定性无 LLM 模块(`cq_check`/`drift_check`/`intent_check`/`rule_engine`/`health_check`/`compat_check`/`module_split`/`usage_stat`/`openai_runtime`/`quick_build`/`translate_cn`),各 95–299 行、可独立单测。
- 本体不变量自检成熟:够不够用(CQ)/通不通(链路)/对不对得上数据(漂移)/答的是不是问的(双盲)/结构健不健康(体检)——多数团队从不构建。
- 反幻觉认识论一致:`verified` 只由数据见证产生,LLM 不得自评,人审只到 `asserted`。

### 1.2 债务(三处集中)
| 区 | 事实(file:line) | 影响 |
|---|---|---|
| 单体路由 | `server.py` **5,109 行 / 121 路由 / 246 函数**;5 个巨函数:`_adjudicate_ir`(4557,168 行)、`diagnose_stream`(2878,167)、`chat_stream`(2698,166)、`build_context`(632,153)、`build_inquire`(4725,121) | 维护中枢风险;SSE/业务/DB/LLM 混在一个函数体 |
| 持久化缺抽象 | **15 个手搓 JSON store** + **64 处**散落 load/save;**9 个无淘汰/TTL 的全局缓存**;`EVAL_JOB`/`SKILL_CMP_JOB` 单例(进程内只能跑一个) | 加一个字段要改多处;无 schema/迁移/校验;并发丢任务 |
| 工程校验 | **无** pyproject/flake8/pytest/coverage/pre-commit/CI;**0 条固定依赖**;**94 处**裸 `except`;文档计数已漂移(ARCH 称 119/531,实为 121/535) | pyflakes 零告警、535 断言、SHACL/HermiT 全靠手动;开源不可复现 |

### 1.3 算法债务(半自动本体构建)
| 项 | 事实 | 缺口 |
|---|---|---|
| 裁决核双实现 | `quick_build.py`:θ=60 内联、父键**精确 100%** 唯一、**无 MIN_DISTINCT**、FK 枚举靠**父表名子串**且跳过 `pt==t`;`relation_discovery.py`:`MIN_OVERLAP=60.0`、**0.95** 近似唯一、`MIN_DISTINCT=3`、`name_score`、排除 PK 作子列 | 两份漂移;`quick_build` 严格更易假阳;**两者都结构性找不到自引用键** |
| 自引用/角色键 | `Employees.ReportsTo→EmployeeId`、`BOM.ParentPart`、`ManagerId` 因 `stem in parent_table` 且跳 `pt==t` 而不可见 | 整类层级/组织/BOM 边丢失(论文 M:relaxed key matching) |
| 单信号裁决 | 仅 重叠∧唯一∧命名 | 缺 包含方向 / 基数分布 / 空值率 / 类型兼容;自增代理键假阳只能靠脆弱命名校验拦截(代码自述 10/12 坏边) |
| 固定 θ | 处处 60 | 高基数命中 60% 与 5 值枚举命中 60% 证据强度天差,却同阈 |
| 采样截断 | `distinct()` `LIMIT 20000` 无 `ORDER BY` | 大表父域欠采→重叠虚低→真 FK 静默丢 |
| 反幻觉未量化 | 无对抗/植入式评测集,`step_critic` 只按 LLM 说法降级 | 「反幻觉」是规范主张而非**被测数字**(论文 M5) |
| 定义未评分 | genus-differentia 定义/反例/成熟度**只存不评** | 无参考式指标(论文 RQ3 future work) |
| 单库单源 | 一次一个 SQLite/一个 gov 源;`_MOD_MAP` 只给图像/PDF 打标签,仅 CSV/TSV 入表 | 无跨库联邦键发现;多模态视觉通道 = IR-009 TODO |

## 2. 范式升级:把 TDD 立为一等公民(本提议的元动作)

**现状**:DR 的 `Acceptance` 段是散文;实现后补 `chk()` 锁行为。
**改为**:每个 IR 先把 DR 的 `Acceptance` 翻成**失败的 `chk()`/pytest**,提交(红)→ 实现到绿。

三条落地规范:
1. **红先行**:验收断言与「预期红」输出随 IR 第一次提交入库;实现提交必须把它转绿,diff 可证「先红后绿」。
2. **校验前置**:新增 `pyproject.toml`(ruff+mypy)、`pytest` 收编现有 `chk()` 套件、`coverage` 基线、固定依赖、`pre-commit` 与最小 CI;pyflakes/SHACL/断言从「手动」变「合并即挡」。
3. **两层测试**:11 个确定性模块补**真单测**(现仅经 HTTP 间接触达);`server.py` 的 535 集成断言**转为重构安全网**(它们最好的用途)。

## 3. 提议的 DR 系列

### 算法(最高研究×产品杠杆)
| DR | 决定 | 先行的红(TDD) | 对应论文/缺口 |
|---|---|---|---|
| **DR-035 裁决核统一** | 抽 `relation_discovery.py`(+`standards_align`/`export_owl`)为可安装 `dao_core`;`quick_build` 改**导入**不再重写门槛 | 一致性金标套件(chinook/sakila/northwind/Mondial 的已知 FK):**两引擎须逐条同判**;`quick_build` 先红(弱门槛) | M1/M3 收敛;消漂移 |
| **DR-036 自引用与角色键** | 放开 `pt==t`;stem 对**父列/PK 名 + 角色词典**(reports_to↔employee、parent↔self)匹配,不再只对父表名 | 植入自 FK 夹具(`ReportsTo`/`ParentPart`)当前必漏 → 先红 | 论文 relaxed key matching |
| **DR-037 多信号裁决** | 加 包含方向 / 基数分布 / 空值率 / 类型兼容 → **标定置信度**替代 AND 链二值 | 对抗夹具(代理键碰撞、枚举重叠)带真/假标签 → P/R/AUC 门槛先红 | 杀主假阳模式;M4 下界 |
| **DR-038 自适应 θ** | θ = f(child_distinct, 随机零假设显著性) | 低基数枚举假阳 + 高基数真 FK 假阴两夹具先红 | 论文 θ 自适应 |
| **DR-039 反幻觉评测台** | 植入真/假 FK 基准 + LLM 提议器**幻觉率**指标;`recheck_window` 纳入 | 评测台即测试:幻觉率>阈即红 | 论文 M5「防线未被激活」变数字 |
| **DR-040 定义参考式评测** | genus-differentia 定义对金标术语表(`indicator_glossary.json`)打分(嵌入/LLM-judge)+ 完备度门 | 空定义/劣定义样本得分低于阈 → 红 | 论文 RQ3 future work |
| **DR-041 多源联邦构建** | 同算法跨两个已入库 schema 求重叠,边带 source-provenance | 跨库同实体(MES↔ERP)金标对先红 | 用户论文强调的「多源」 |
| **DR-042 多模态证据通道(IR-009)** | 图纸/数据字典/ER 图作**提议器**,主张仍过数据裁决 | 文档独有关系:纯 schema 找不到、加通道后可提议且被裁决 → 红 | 用户论文强调的「多模态」 |

### 系统/架构
| DR | 决定 | 先行的红/安全网 |
|---|---|---|
| **DR-043 蓝图化拆分** | `server.py` 按 6 簇拆 Flask blueprint(ontology/build/deepqa/actions/skills/engine) | 535 集成断言全程绿 = 重构安全网;新增路由计数自检断言 |
| **DR-044 持久化仓储层** | 15 JSON store 收敛到 `Store` 接口(schema+校验+迁移)或 SQLite 应用态库 | Store 单测:半写恢复 / 并发写 / 迁移(补现无的负向持久化测试) |
| **DR-045 工程校验 / TDD 底座** | pyproject+ruff+mypy+pytest+coverage+固定依赖+pre-commit+最小 CI+文档计数自检 | 校验规则本身即测试;pyflakes/断言/计数合并即挡 |
| **DR-046 确定性模块单测** | 11 个 `*_check` 模块补隔离单测(空图/环/复合键≥3列) | 每模块红-绿夹具(纯函数,天然 TDD) |
| **DR-047 缓存与任务治理** | 9 全局缓存加 TTL/LRU;`EVAL_JOB`/`SKILL_CMP_JOB` 并入 keyed `JOBS` | 缓存淘汰 + 并发双任务测试先红 |

## 4. 提议的 IR 排期(每 IR 一轮红-绿)

| IR | 目标 | 含 DR | 为何这个次序 |
|---|---|---|---|
| **IR-007 工程校验与 TDD 底座** | 建 CI/coverage/lint/pytest/固定依赖;确立**已知绿基线** | DR-045、DR-046 | 没有自动化校验做不了 TDD;先立基线 |
| **IR-008 裁决核统一与算法强化** | 单核 + 自引用键 + 多信号 + 自适应 θ | DR-035/036/037/038 | 消漂移→补召回→提精度,每步红-绿 |
| **IR-009 评测台与定义质量** | 反幻觉基准 + 参考式定义评分 | DR-039、DR-040 | 把规范主张变可回归的数字 |
| **IR-010 多源与多模态** | 跨库联邦键 + 文档/图纸提议器 | DR-041、DR-042 | 能力扩张,承接论文多源多模态 |
| **IR-011 架构收敛** | 单体拆蓝图 + 仓储层 + 缓存/任务治理 | DR-043/044/047 | 由已 CI 化的集成套件回归验证 |

## 5. TDD 红-绿样例

### 5.1 算法样例(DR-035 裁决核一致性,先红)
```python
# tests/test_dao_conformance.py — 先提交,预期 quick_build 红
GOLD = load_json("fixtures/fk_gold/northwind.json")  # 已知外键金标
@pytest.mark.parametrize("engine", [quick_build_adjudicate, dao_core.discover_relations])
def test_engines_agree_on_gold(engine):
    got = {(r.child, r.parent) for r in engine(NORTHWIND) if r.verified}
    assert got == GOLD          # quick_build 先红(缺自 FK/门槛不同)→ 统一后绿
```
### 5.2 系统样例(DR-046 确定性模块单测,先红)
```python
# tests/unit/test_health_check.py — 现无隔离单测
def test_isolated_node_flagged():
    ir = {"objects":[{"id":"A"},{"id":"B"}], "relations":[]}   # B 孤岛
    signals = health_check.run(ir)
    assert any(s.kind=="isolated" and s.node=="B" for s in signals)  # 先红:模块从未被隔离测过
```

## 6. 首步建议(可立即执行)

**IR-007 先做**:落 `pyproject.toml`(ruff+mypy)、`pytest` 收编 `chk()` 套件为可发现测试、
`coverage` 出首个基线数、固定 `requirements`、加文档计数自检(挡 121/535 这类漂移)、最小 CI。
产出「已知绿基线」后,再进 IR-008 用红-绿把两份裁决核收敛为一份。

> 立项动作(照 meta.md):把上表任一 DR 从本文件拆为 `decisions/DR-0NN-*.md`,
> 写 IR(Goal/Deliverables/Tasks/Acceptance),**先提交失败断言(红)**,每任务一次提交,读码+跑测转绿后打勾。
