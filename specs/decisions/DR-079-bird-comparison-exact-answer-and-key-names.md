# DR-079 · 与 EvoOntology 对比实验:精确作答、驼峰与复数表名、带角色前缀的键名

- **状态 / Status**: accepted
- **日期 / Date**: 2026-10-04
- **关联 / Refs**: [[DR-077-in-db-dictionary-and-comment-veto]]、[[DR-062-cross-check-and-cleaning-as-skills]];
  实证=`tests/unit/test_exact_answer.py`、`tests/unit/test_dao_core.py`、`tests/unit/test_cross_check_skill.py`;
  实验记录=`../08_对比实验/README.md`

## 上下文 / Context

在 BIRD Mini-Dev 上与 EvoOntology(arXiv 2609.15779)做对比实验时,发现以下问题。

1. **问数只能按分析报告作答。** 规划提示词要求最多 3 个分析、按月拆趋势、LIMIT 500。BIRD 一类基准要求结果与标准答案逐行一致,报告式作答会多出列和拆分。
2. **提示词预设日期格式。** 规划提示词写死"日期是 TEXT 'YYYY-MM-DD'"。其他库的日期格式各不相同,例如 BIRD 的 `yearmonth.Date` 是 `YYYYMM`。
3. **驼峰与复数表名被判为"自增 id 碰撞"。** `UserId → users.Id`、`PostId → posts.Id` 这类关系,数据裁决为 100% 重叠且父键唯一。但交叉复核的自增 id 碰撞通道:
   - 不识别驼峰写法的 `Id` 后缀;
   - 不把复数表名还原为单数。

   结果 codebase_community 库的 9 条真外键全部被降为候选。
4. **带角色前缀的键名被判为"列名词根不一致"。** 如 `OwnerUserId → users.Id`、`ExcerptPostId → posts.Id`,这类键名在业务库中很常见(created_by_user_id 等)。

## 决定 / Decision

1. **精确作答**(`/api/chat` 的 `answer_style: "exact"`):
   - 只取 1 个分析;
   - SELECT 只包含问题要求返回的列,不把排序、筛选、计算用的中间列放进结果;
   - 不加 ROUND,除非问题要求保留小数;不加 LIMIT,除非问题问的是"最……"或"前 N 个";
   - 不调用模型生成叙述,改用规则摘要;不使用问数缓存;
   - 本体上下文、本体约束校验、按原因修正与意图核对照常进行;
   - 报告式要求(按月 substr、LIMIT 500)只放在报告式规则里;
   - 流式接口(`/api/chat/stream`)暂不支持该选项。
2. **日期格式不预设**:提示词改为"日期列的格式以列说明中的实际取值为准"。
3. **自增 id 碰撞通道**(`ontology-cross-check` 技能):
   - 先把键名由驼峰改写为下划线形式,再识别 id 后缀;
   - 父表名的复数形式先还原为单数(users → user、categories → category),再判断键名与父表是否相关。
4. **键名相容判定**(`dao_core.name_ok`):父键是泛化的 `id` 时,如果子键以 id/code/key/no 结尾,且子键词根的最后一个词元与父表名最后一个词元的单数形式相同,就认定键名有据。例:`owner_user_id → users.id`。

## 后果 / Consequences

- **BIRD 11 个库上**,关系与已声明外键对照:精确率由 93.5% 升至 94.4%,召回由 78.4% 升至 90.5%;codebase_community 库 10/10。
  - thrombosis_prediction 库的两条声明外键仍未通过:`Examination.ID` 与 `Patient.ID` 的取值重叠只有 9%,数据本身不支持。
- **负例保持拒绝:** `OwnerUserId → posts`、`OrderId → users`、`UserId → posts`、`ViewCount → users` 均有测试。
- **问数对比结果**见 `../08_对比实验/结果报告.md`。
