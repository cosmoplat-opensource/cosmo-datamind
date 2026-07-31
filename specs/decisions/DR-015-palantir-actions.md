# DR-015 动作层:Palantir ActionType 范式落地(类型化 · 风险分级 · 审批 · 决策捕获)

日期:2026-07-25 · 状态:已实现(PoC) · 回归:test_all.py 119/119

## 背景

DR-001 元模型自始声明「动作↔API,高风险动作禁止 LLM 直接执行须人审」,deck P5 亦有「动作:派工·报修(可执行)」;但系统里动作只是描述性概念(应用本体 action 对象仅 name/remark),示例 IR actions=[],零可执行功能。

## Palantir → DataMind 映射

| Palantir Foundry | 本系统落法 |
|---|---|
| ActionType | `workdir/action_types.json` 注册表(4 个种子:派工/报修/调交期/编制预算,均锚定本体对象与表) |
| Parameters(typed) | params schema(text/number/select/textarea + required/options),弹窗动态表单 |
| Submission criteria | 服务端必填/枚举/数字校验 + 操作人必填(复用评审人纪律) |
| Rules(实例写回) | **PoC=决策捕获**:执行=追加事件式审计日志,**绝不写只读源库**;webhook 仅登记「预留,未外呼」 |
| Permissioning + Audit | 低风险直执行;**高风险进审批队列**(批准执行/驳回必填意见),操作人/审批人/时间全留痕 |
| AIP(agent 提议→人确认) | 诊断结果卡「按清单发起检查工单」参数预填→人补齐→提交;LLM 永不直接执行 |

## 实现

- API:`GET /api/actions`(类型+计数)、`POST /api/action/invoke`(校验→低风险 executed/高风险 pending)、`GET /api/action/log`、`POST /api/action/approve`(approve 生效/deny 必填意见)。存储 action_log.json(原子写+锁,cap 1000)。
- UI:新「**动作中心**」页(本体治理组):动作类型卡(风险签+发起)/待审批高亮区(批准·驳回走页面内弹窗,无原生对话框)/执行日志表。`#ac_modal` 动态参数表单,操作人与评审人共用 localStorage。
- 集成:**图谱页对象卡**按绑定表匹配显示「可执行动作」;**诊断卡**检查清单一键派工(entity/清单预填)——诊断→动作→审批→审计闭环。

## 验证(实测)

低风险报修直执行✓;高风险派工进审批✓;缺必填/枚举违规/驳回缺意见均 400✓;批准后效果生效且审计三要素齐✓;弹窗预填(3 号线+清单)✓;设备对象卡显示「设备报修·直执行」✓;test_all.py 119/119✓。

## 边界(如实)

- 决策捕获≠真实写回:webhook 未外呼,源库只读纪律不破;真实写回需目标系统接口+幂等与回滚设计(Phase 2)。
- 无 RBAC:操作人/审批人为自报身份(与评审人同级纪律),未接认证体系。
- submission criteria 仅参数级,未做对象状态前置条件(如"仅运行中设备可报修")。
