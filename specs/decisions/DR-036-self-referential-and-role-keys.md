# DR-036 · 自引用与角色键发现

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-31
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: [[DR-035-unified-adjudication-core]]、[[DR-030-ontology-health-check]]、[[IR-008-adjudication-core-convergence]];实证=`dao_core.py`(role_targets/name_ok)、`quick_build.py`(枚举)、`health_check.py`(自反豁免)、`tests/unit/test_dao_core.py`/`test_quick_build.py`/`test_health_check.py`;108 表 demo 新增 1 条真自引用边

## 上下文 / Context

两份裁决实现都**结构性发现不了自引用外键**(论文点名的 relaxed key matching 盲区):

- `quick_build` 枚举跳过 `pt==t`(父表≠子表),自引用无从进入候选;
- 且 `reports_to` 这类**无 `_id/_code` 后缀**的角色键连词干都derive不出(`else: continue`),直接被丢;
- 命名校验 `key_name_ok("reports_to","employee_id")` 单看两键词根 → False,即便放开枚举也会被判无据。

结果:`Employees.ReportsTo→EmployeeId`、`BOM.ParentPart`、类别树 `parent_id` 等**整类层级/组织/BOM 边**不可见。

## 决定 / Decision

1. **角色键词典**(dao_core `role_targets`):`reports_to/manager/supervisor/mgr/boss`→(self+员工属类词);
   `parent/predecessor/successor/prior/prev/next`→(self)。先驼峰归一(`ReportsTo→reports_to`)+ 剥后缀,
   再直接命中或按 `<role>_` 前缀/`_<role>` 后缀命中(`parent_part`→parent)。
2. **角色感知命名校验**(dao_core `name_ok`):先走 `key_name_ok`(非角色键与之**逐值等价**,既有行为不变);
   角色键补两路——self→父表即子表(层级自引用)、genus→属类词是父表名子串(`orders.manager_id→employees`)。
3. **枚举放开(仅对角色键)**:`pt==t` 仅对含 `self` 的角色键开放;非自引用的角色键按
   「stem 在父表名 **或** genus 命中父表名」准入,**非角色键沿用原表名子串校验,不广泛放宽**
   (避免 `parent_id` 扫到无关表造假阳)。自引用仍需过数据裁决(重叠≥θ∧父键唯一)。
4. **自引用标记 + 语义化**:自引用边打 `self_ref=True`、`verb=上级`。
5. **health_check 豁免**(DR-030 联动):标 `self_ref` 的边**不判 self_loop 硬错误**——
   那是合法层级建模,不是同名列自连的抽取误判。

## 后果 / Consequences

- (+) 恢复整类自引用/角色边:测试证 `reports_to`(snake)与 `ManagerId`(驼峰)均被发现为 verified 自引用。
- (+) **真实 108 表 demo 精准命中**:新增**恰 1 条** `dim_supplier_category.parent_id→category_id`(供应商类别树),
  0 条既有关系被改动或消失——只加真边、不扰既有。
- (+) 保守放开:仅已知角色模式获特殊待遇,非角色键 `name_ok≡key_name_ok`、枚举校验不变,demo 零副作用验证在案。
- (−) 角色词典为启发式(中英文各行业角色词有限);异形角色词(如「归属上级」)需后续扩表,漏配从严(当作普通键)。
- **口径**:自引用 verified 仍是「数据见证」——parent_id 值域须真落在本表 PK 才 verified,不是靠名字硬判。
