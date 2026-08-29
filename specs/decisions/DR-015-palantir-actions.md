# DR-015 动作层:Palantir ActionType 范式落地(类型化 · 风险分级 · 审批 · 决策捕获)

日期:2026-07-25 · 状态:已实现(PoC) · 回归:test_all.py 119/119

## 背景

DR-001 元模型自始声明「动作↔API,高风险动作禁止 LLM 直接执行须人审」,deck P5 亦有「动作:派工·报修(可执行)」;但系统里动作只是描述性概念(应用本体 action 对象仅 name/remark),示例 IR actions=[],零可执行功能。

## Palantir → DataMind 映射

| Palantir Foundry | 本系统落法 |
|---|---|
| ActionType | `workdir/action_types.json` 注册表(4 个种子:派工/报修/调交期/编制预算,均锚定本体对象与表) |
| Parameters(typed) | params schema(text/number/select/textarea + required/options),弹窗动态表单 |
| Submission criteria | 服务端必填/枚举/数字校验 + 操作人必填(复用评审人规范) |
| Rules(实例写回) | **PoC=决策捕获**:执行=追加事件式审计日志,**绝不写只读源库**;webhook 仅登记「预留,未外呼」 |
| Permissioning + Audit | 低风险直接形成动作记录;**高风险进审批队列**(批准登记/驳回必填意见),操作人/审批人/时间全留痕 |
| AIP(agent 提议→人确认) | 诊断结果卡「按清单发起检查工单」参数预填→人补齐→提交;LLM 永不直接执行 |

## 实现

- API:`GET /api/actions`(类型+计数+执行方式)、`POST /api/action/invoke`(校验→低风险 executed/高风险 pending)、`GET /api/action/log`、`POST /api/action/approve`。`executed` 是兼容状态码，当前含义为“已形成决策记录”，不是业务系统写回。
- UI:「动作中心」显示动作类型、待审批和动作记录；所有可见文案明确当前为决策记录模式。
- 集成:已登记动作经 `action_ontology` 投影为本体 action 节点，并按显式对象/表配置显示绑定；诊断卡可发起派工记录。

## 验证(实测)

历史基线验证为 test_all.py 119/119。2026-08-29 起对外术语统一为“直接登记/形成动作记录”，
API 继续保留 `executed` 兼容状态，并新增 `execution_mode=decision_capture`、`real_writeback=false`。

## 边界(如实)

- 决策捕获≠真实写回:webhook 未外呼,源库只读规范不破;真实写回需目标系统接口+幂等与回滚设计(Phase 2)。
- 无 RBAC:操作人/审批人为自报身份(与评审人同级规范),未接认证体系。
- submission criteria 仅参数级,未做对象状态前置条件(如"仅运行中设备可报修")。
