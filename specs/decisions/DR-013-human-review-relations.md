# DR-013 人机协同关系人审(评审队列 + 关系级白名单算子,两种 IR 形状通吃)

日期:2026-07-25 · 状态:已实现 · 回归:test_all.py 119/119 通过

## 背景 / 问题

方法论宣称「机器提议 → 数据裁决 → 标准把关 → 人工定夺」,但系统里**人工定夺一环缺失**:

1. `confirm` 算子只能确认**对象**(obj:<id>),机器提议的 candidate **关系**没有任何人审动作(通过/否决);没有待审队列 UI——人机协同的"人"无处下手。
2. 本体图谱页点边只读;关系编辑只埋在建模工作台的对象面板里。
3. 更深一层:平台 `find_object/find_link` 只认 示例 形状(`objects[].id` + `links[source/target]`),而**问询台构建产物(built_*)与应用本体用 `objects[].name` + `relations[source_concept/target_concept]`——最需要人审的机器构建图谱,所有编辑算子根本跑不通**。

## 决定

1. **新增关系人审算子**(server.py `REVIEW_OPS`,并入 apply 白名单):
   - `confirm_relation`:人审通过。candidate/gap/rejected → `asserted`;**verified 只记通过、不动状态**(反造假规范:verified 仅由数据裁决产生,人只产生 asserted)。记 `human_review="approved"` + `review_reason`。
   - `reject_relation`:人审否决。`status="rejected"` + `human_review="rejected"`,**渲染剔除但记录留痕**(ir_to_graph 跳过 rejected;评审页仍可见、可"恢复为断言"),经编辑日志可撤销。
2. **关系类算子本地统一实现、两种 IR 形状通吃**(`apply_any` + `_rels`/`_find_rel_any`):`confirm_relation / reject_relation / verb / set_card / add_relation / remove_relation` 对 links 形状与 relations 形状同语义;对象/属性类算子仍回落平台 `SC.apply_op`。`load_ir_edited` 回放同走 `apply_any`。
3. **评审队列 API** `GET /api/ont/review?graph=<key>`:全部关系 + 状态/语义评审/取值重合/人审结论/理由;`pending = 未人审 ∧ (candidate ∨ 语义存疑)`;counts{total/pending/approved/rejected/disputed}。
4. **UI 三处**:
   - 新模块「**本体评审**」(#review,数据建模组):图谱选择 + 待审/已通过/已否决 KPI + 关系表逐条 通过/否决/改动词 + 撤销上一步;待审行高亮。
   - **本体图谱页边详情卡**追加人审动作行(通过/否决/改动词/评审队列↗);relation 详情端点失败时用边数据降级渲染(app 形状可用)。
   - **构建结果卡**加「进入人审 ↗」——问询台构建 → 人审闭环。

## 不变量

- 人审只产生 `asserted`,永不冒充 `verified`(与 DR-007/DR-011 反造假规范一致)。
- 全部经 `/api/ont/apply` 白名单 + 编辑日志,可撤销;不动构建产物基线(草案层)。
- 非白名单操作仍 400;图谱键仍过 `_bad_gkey` 防穿越。

## 验证(实测)

- built_f2050f(76 关系/8 待审):confirm→asserted+approved、reject→rejected 且图渲染剔除、verb 改动词、undo 逐条回退 ✓;demo(links 形状)同套往返 ✓;Playwright 无头验证评审页表格/按钮与图谱页边卡动作在 示例 与 built 图谱均渲染 ✓;test_all.py 119/119 ✓。

## 增补(2026-07-25 下午):评审人 / 评审意见 / 删元素

- **评审人身份**:评审页顶栏「评审人」输入(localStorage 持久;图谱页边卡首次操作提示输入一次)。未填评审人一律拦截。`/api/ont/apply` 接收 `reviewer`,连同服务端盖的 `ts` 写入编辑日志(回放确定),`_stamp_review()` 把 `review_by / review_time / review_reason` 盖到元素上;评审页行内展示。
- **评审意见**:通过=可选意见;否决/删除=必填原因(前端强制)。
- **删元素**:关系行加「删除」(remove_relation);新增**对象区**(候选高亮),支持「确认」(confirm,两形状通吃:confirmed=True 且 candidate=False)与「删除」(remove_object 本地实现,**两形状通吃 + 级联删除该对象全部关系**)。全部可撤销。
- 实测:盖章(by/time/意见)✓;删关系 76→75 ✓;删 sensor 级联删 3 条关系 ✓;撤销×3 全复原(含盖章清除)✓;未填评审人点通过被拦 ✓;test_all.py 119/119 ✓。
- **二次增补(同日晚)**:评审动作全部改走**页面内评审表单**(`#rv_modal`,复用 dq-modal 样式)——评审人与评审意见是可见输入框,不再使用任何原生 prompt/confirm(原生弹窗可被浏览器"阻止对话框"设置静默吞掉,用户会误以为功能不存在)。评审页与图谱页边卡共用同一表单;必填校验内联提示;页头带版本标记 v0725c 便于识别旧标签页。无 dialog-handler 的无头回归:表单弹出/缺原因拦截/提交盖章/撤销复原全过。

## 教训

- 「有编辑算子」≠「有人审流程」:算子散落在对象面板里,没有队列、没有关系级裁决,人机协同就只是口号。
- 形状不统一是隐性断层:平台算子默认 示例 形状,构建产物悄悄不可编辑——统一入口(apply_any)比到处修补更稳。

## 关联

- [[DR-007]](反造假:verified 仅数据裁决)· [[DR-009]](对话编辑白名单op)· [[DR-011]](泛化裁决/semantic 字段,评审页展示其结论)
