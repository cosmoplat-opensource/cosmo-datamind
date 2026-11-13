# IR-026 T02 核心测试评审 — DR-072

日期：2026-11-12。状态：RED 测试已编写，待用户审阅；未进入 T03 实现。
依据：[已确认适配规约](../../specs/contracts/robotics-application-adaptation-v1.md)、[IR-026](../../specs/iterations/IR-026-robotics-application-adaptation.md)。
用户确认 robotics.complete_task 复用原业务映射步骤，并要求继续；据此冻结 T01 方向并进入 T02。
第 11 节是本轮测试的具体纯核心调用面，随测试一起评审。

## 本轮覆盖

| 文件 | 用例数 | 验证内容 |
|---|---:|---|
| [角色检查](../../tests/unit/test_robotics_application_roles.py) | 22 | 完整 IRI、实体种类、显式确认、快照摘要、必需角色、父类和关系方向／值域、类型冲突、经确认的改名、执行与任务阶段分开 |
| [执行提取](../../tests/unit/test_robotics_application_execution.py) | 27 | 4 棵树及源顺序、25 个调用、重复运动身份、端口、4 种绑定、scope、6 项公开配置、SetBlackboard 静态生产者、动态／歧义／缺赋值、类型与来源冲突 |
| [适配与工件](../../tests/unit/test_robotics_application_artifacts.py) | 21 | 原输入不变、原实例映射、扩展保留、无示例业务／设备、真实旧语义和编译器、公开参数保护、总 JSON／OWL／血缘与恢复、未知版本和篡改拒绝、禁止回退旧构建器 |

共 70 个参数化用例；68 项因目标模块尚未实现而失败，2 项既有基线通过；0 收集错误、0 跳过。
所有失败均为 ModuleNotFoundError: domains.robotics.application_adaptation；没有用 skip、xfail、假模块或空实现隐藏缺失能力。
目标模块在测试函数执行时导入；后续断言仍需 T03 实现后逐项走通，不能把本轮 RED 当作已经验证适配行为。

## 关键断言

1. 不凭名称猜角色。类别、对象属性、数据属性分开，缺角色与语义冲突有不同问题代码。
   同名但方向错误、错误父类／类型、不同 namespace、未确认或已过期映射均不能授权适配。
2. 原 XML 决定顺序与参数。保留根、树、调用和端口信息，不把 SubTree 目标技能名写成 XML 标签。
   区分父输入、局部读／写及字面量，跨 scope 同名数据不能合并。
3. 只开放原六项公开配置。抓取输入 obj_in_robot 继续来自数据流；内部速度、偏移、接口等不能变成任意用户配置。
   public position 必须追溯到 SetBlackboard 的 value，而非 output_key。
4. 静态解析要有唯一来源。缺赋值、多写入、使用后赋值、运行期值、自引用、端口冲突等必须阻塞并提供源节点。
   不能为了解析成功从示例或字符串外观猜值、单位和确认状态。
5. 真实复用而不回退。适配输出交给现有 semantics、inspect_structure 和 compiler；独立比较 XML 结构及公开值。
   显式禁止调用旧 importer.build_graph 和旧 add_domain 示例填充；也不通过只增加 artifactContract 标记绕过规则。
6. 当前总 JSON 必须完整。当前事实、原始设计、父版本、规则、覆盖、映射及实际 OWL 摘要一致。
   JSON 单独恢复 RDF 语义；current 缺失／被改、摘要错误或未知版本不能恢复为合法工件。
   v1 原字节不覆盖、不自动发布，不要求用户拼装多个业务 JSON。

## 基线、夹具与环境

真实输入使用仓库冻结的双 XML 与 ur_selected_tbox，调用现有证据、ABox 提取和 RDF 导出器。
不替换这些现有实现，不运行付费模型；no_network 夹具阻止网络访问。
两项通过的基线：原合法通用设计仍不能被旧 UR 消费者直接接受；原旧构建／编译器的六参数对照可恢复源流程。
测试使用内存结构／临时目录，不操作用户现有本体或运行服务。
新增共用夹具是内部核心输入，不冒充已经通过真实 v1 运行清单及资产权限验证；此边界交 T04 测试。

## 本轮不声称覆盖

- 诊断页面何时触发、修复按钮和原任务返回、真实角色确认记录的 HTTP 边界。
- 持久版本分配、CAS、幂等受理、并发写入、回滚与重启恢复。
- 新版任务补全后总 JSON 同步、业务对象选择／创建的完整端到端流程。
- 移走原 XML 后 TaskService／GovernanceService 的整条准备、补全与生成路径。
- 真实新本体副本，以及 real/demo、offline/strict 的完整应用策略回归。

这些属于 T04 集成 RED／T05 GREEN 与 T06 最终验收，本轮纯编译原语测试不授予业务生成资格。
RAC-01～05 保留原 criterion，登记为核心 created-red-partial，不勾选业务验收。
定向 lint、类型检查、21 项规约元测试、引用链接与 git diff --check 均通过。
未修改产品实现、旧测试、原始输入或用户数据库，未提交 Git；本轮不运行已知预期失败的全量回归。
下一步：用户审阅本轮测试及第 11 节调用面，确认后进入 T03，保持已审测试不变。
