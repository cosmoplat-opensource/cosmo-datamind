#!/usr/bin/env python3
"""Metadata (dim_table_metadata / dim_column_metadata) for the 108-table demo database (DR-076).

Starts from the original descriptions where they are right, fills the 134 columns that had
none (all summary-table measures among them), and corrects descriptions the simulation made
precise (units, VAT convention, grains, foreign keys).  Standard library only.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import demo108_dws as W

COLUMN_CN = {
    "date": ("日期", "统计日期"), "dim_emp_id": ("业务员ID", "外键,关联dim_employee"), "dim_dept_id": ("部门ID", "外键,关联dim_department"),
    "dim_region_id": ("区域ID", "外键,关联dim_region"), "dim_cust_id": ("客户ID", "外键,关联dim_customer"),
    "dim_prod_id": ("产品ID", "外键,关联dim_product"), "dim_line_id": ("产线ID", "外键,关联dim_production_line"),
    "dim_workshop_id": ("车间ID", "外键,关联dim_workshop"), "dim_equipment_id": ("设备ID", "外键,关联dim_equipment"),
    "dim_cc_id": ("成本中心ID", "外键,关联dim_cost_center"), "dim_supplier_id": ("供应商ID", "外键,关联dim_supplier"),
    "dim_industry": ("行业", "客户所属行业(冗余自dim_customer)"),
    "emp_name": ("业务员姓名", "冗余自dim_employee.emp_name"), "dept_name": ("部门名称", "冗余自dim_department.dept_name"),
    "region_name": ("区域名称", "冗余自dim_region.region_name"), "cust_name": ("客户名称", "冗余自dim_customer.cust_name"),
    "prod_name": ("产品名称", "冗余自dim_product.prod_name"), "line_name": ("产线名称", "冗余自dim_production_line.line_name"),
    "workshop_name": ("车间名称", "冗余自dim_workshop.workshop_name"), "equipment_name": ("设备名称", "冗余自dim_equipment.equipment_name"),
    "cc_name": ("成本中心名称", "冗余自dim_cost_center.cc_name"), "supplier_name": ("供应商名称", "冗余自dim_supplier.supplier_name"),
    "order_count": ("订单数", "当日新建订单数(生产日汇总为当日完工工单数)"), "undelivered_order_count": ("未交付订单数", "期末(日末/月末)尚未交付完的订单数"),
    "return_count": ("退货次数", "退货记录条数"), "return_quantity": ("退货数量", "单位:吨"),
    "goods_in_transit_qty": ("发出商品数量", "已发货未开票的数量,单位:吨"), "goods_in_transit_amt": ("发出商品金额", "已发货未开票的金额,不含税,元"),
    "avg_invoice_days": ("平均开票天数", "当日开具发票的(开票日-发货日)平均值"), "bank_acceptance_ratio": ("银承占比", "当日回款中银行承兑汇票金额占比,%"),
    "kpi_dept_count": ("部门销量达标人数", "截至当日月累计销量达到按日折算目标的部门人数"),
    "kpi_region_count": ("区域销量达标人数", "按部门统计(销售部门与区域一一对应)"),
    "kpi_person_count": ("本人销量是否达标", "1=达标,0=未达标"),
    "kpt_dept_count": ("部门毛利达标人数", "截至当日月累计毛利达到按日折算目标的部门人数"),
    "kpt_region_count": ("区域毛利达标人数", "按部门统计"), "kpt_person_count": ("本人毛利是否达标", "1=达标,0=未达标"),
    "market_capacity": ("市场容量", "客户年度改性塑料采购总量估算,单位:吨/年"), "market_share": ("市场份额", "近365天本公司销量/市场容量,%"),
    "existing_customer_count": ("存量客户", "1=近90天内有成交的老客户"), "new_customer_count": ("新客户", "1=首单在90天内"),
    "dormant_customer_count": ("休眠客户", "1=最近一单距今91~180天"), "lost_customer_count": ("流失客户", "1=最近一单距今超过180天"),
    "risk_customer_count": ("风险客户", "1=有超过到期日60天未收回的应收"), "guide_price_deviation": ("指导价偏离", "当日成交单价相对指导价的平均偏离,%"),
    "sales_expense": ("销售费用", "当日报销的销售费用,元"), "expense_budget": ("费用预算", "部门月度费用预算按天与人数折算,元"),
    "sales_quantity": ("销量", "订单数量,吨"), "sales_amount": ("销售额", "订单金额,不含税,元"),
    "gross_profit_actual": ("实际毛利", "销售额-实际成本(发出批次的生产成本),元"), "gross_profit_standard": ("标准毛利", "销售额-标准成本,元"),
    "accounts_receivable": ("应收账款余额", "日末/月末应收余额,含税,元"), "sales_quantity_target": ("销量目标", "吨"),
    "profit_target": ("毛利目标", "元"), "payment_target": ("回款目标", "含税,元"), "sales_plan_quantity": ("计划发货量", "吨"),
    "opp_estimated_quantity": ("商机预估量", "当日新建商机的预估年用量,吨"), "opp_count": ("商机数", "当日新建商机数"),
    "lost_opp_count": ("丢单数", "当日关闭为丢单的商机数"), "stalled_opp_count": ("搁置商机数", "当日新建且目前搁置的商机数"),
    "important_opp_count": ("重要商机数", "预估金额≥300万元的商机"), "opp_cycle": ("商机周期", "当日关闭商机的平均(关闭日-创建日)天数"),
    "sample_quantity": ("送样量", "吨"), "sample_count": ("送样次数", "次"), "overdue_inventory_qty": ("超期库存量", "在库超过90天的成品,吨"),
    "overdue_inventory_amt": ("超期库存金额", "按批次平均成本,元"), "negative_margin_qty": ("负毛利订单量", "当日实际毛利为负的订单数量,吨"),
    "overdue_ar_amt": ("超期应收金额", "已过到期日未收回的余额,含税,元"), "overdue_ar_count": ("超期应收笔数", "笔"),
    "actual_run_hours": ("实际运行时长", "小时"), "downtime_hours": ("停机时长", "非计划停机,小时"), "downtime_count": ("停机次数", "次"),
    "alarm_count": ("报警次数", "次"), "maint_wo_count": ("新建维护工单数", "张"), "maint_completed_count": ("完成维护工单数", "张"),
    "oee": ("设备综合效率", "时间开动率×性能开动率×合格品率,%"), "budget_amount": ("预算金额", "元"), "actual_amount": ("实际金额", "元"),
    "expense_amount": ("销售费用", "元"), "material_cost": ("材料成本", "元"), "labor_cost": ("人工成本", "元"), "overhead_cost": ("制造费用", "元"),
    "total_product_cost": ("产品成本合计", "元"), "dep_amount": ("折旧", "元"), "po_count": ("采购订单数", "张"), "po_amount": ("采购金额", "不含税,元"),
    "gr_count": ("收货批次", "批"), "gr_quantity": ("合格收货量", "吨"), "inspection_pass_rate": ("来料合格率", "%"),
    "ap_amount": ("新增应付", "含税,元"), "payment_amount": ("付款金额", "元"), "incident_count": ("事故数", "不含未遂事件"),
    "near_miss_count": ("未遂事件数", "起"), "risk_assessment_count": ("风险评估数", "次"), "high_risk_count": ("高风险评估数", "L3及以上"),
    "permit_count": ("作业许可数", "张"), "training_count": ("培训场次", "场"), "audit_score": ("审计得分", "0~100"),
    "env_compliant_rate": ("环境监测合规率", "%"), "opp_quantity": ("商机预估量", "吨"), "opp_won_count": ("赢单商机数", "个"),
    "sample_success_count": ("送样成功数", "次"), "sample_feedback_count": ("送样反馈数", "次"), "unit_code": ("计量单位", "指标单位"),
    "metric_id": ("指标ID", "主键"), "metric_name": ("指标名称", ""), "category": ("分类", ""), "metric_type": ("指标类型", "原子/派生/复合"),
    "description": ("说明", ""), "is_base_metric": ("是否基础指标", ""), "perspective": ("视角", ""), "table_source": ("来源表", ""),
    "dws_table": ("汇总表", ""), "date_column": ("日期列", ""), "value_column": ("取值列", ""), "config_id": ("配置ID", "主键"),
    "derived_metric_name": ("派生指标名称", ""), "atomic_metric_names": ("原子指标", ""), "filter_column": ("筛选列", ""),
    "calc_method": ("计算方式", ""), "group_by_columns": ("分组列", ""), "formula": ("公式", ""), "composite_metric_name": ("复合指标名称", ""),
    "component_metric_names": ("组成指标", ""), "table_id": ("表ID", "主键"), "table_name": ("表名", ""), "table_name_cn": ("表中文名", ""),
    "table_desc": ("表说明", ""), "table_category": ("表分类", "维度表/事实表/汇总表"), "table_level": ("表层级", ""),
    "column_id": ("字段ID", "主键"), "column_name": ("字段名", ""), "column_name_cn": ("字段中文名", ""), "column_desc": ("字段说明", ""),
    "ordinal": ("序号", "字段在表中的顺序"),
}
# 原注释与仿真数据含义不符、需要更正的字段
COLUMN_FIX = {
    ("fact_material_consumption", "material_id"): ("原材料ID", "外键,关联dim_material(按BOM投入的原料)"),
    ("fact_material_consumption", "planned_qty"): ("配方用量", "按BOM与实际产量计算的理论用量,吨"),
    ("fact_material_consumption", "scrap_qty"): ("工艺损耗", "实际投料-配方用量,吨"),
    ("fact_material_consumption", "batch_no"): ("原料批次号", "RM物料-收货日-收货单号;以 -INIT 结尾的为系统上线(2025-01-01)时的期初库存,上线前已入库,不在收货表中"),
    ("fact_sales_order", "amount"): ("订单金额", "数量×单价,不含税,元"),
    ("fact_sales_order", "unit_price"): ("成交单价", "不含税,元/吨"),
    ("fact_sales_order", "actual_cost_amount"): ("实际成本", "按发出批次的生产成本计算;未发货部分按当月同产品平均成本,元"),
    ("fact_delivery", "amount"): ("发货金额", "发货数量×订单单价,不含税,元"),
    ("fact_ar_aging", "balance_amount"): ("未收余额", "期末(2026-06-30)未收回的金额,含税,元"),
    ("fact_ap", "amount"): ("应付金额", "收货数量×采购单价×1.13,含税,元"),
    ("fact_production_order", "yield_rate"): ("成材率", "实际产量/实际投料量×100"),
    ("fact_production_order", "runtime_hours"): ("运行时长", "净运行小时,不含开工后的停机;结束时间-开始时间=运行+停机"),
    ("fact_energy_consumption", "unit_energy_consumption"): ("单位电耗", "用电量/当日产量,kWh/吨;当日无产量为空"),
    ("fact_equipment_runtime", "plan_run_hours"): ("负荷时长", "当日运行+停机小时(停机含故障等非计划停机与换产,不含计划保养)"),
    ("fact_equipment_runtime", "downtime_hours"): ("停机时长", "当日非计划停机与换产小时,按停机事件逐日切分;不含计划保养"),
    ("fact_equipment_runtime", "oee"): ("OEE", "可用率(运行/负荷时长)×性能稼动率×质量合格率×100"),
    ("fact_downtime", "reason_code"): ("停机原因ID", "外键,关联dim_downtime_reason.reason_id(存原因ID 1~8,不是 'D001' 等原因编码)"),
    ("fact_invoice", "amount_no_tax"): ("不含税金额", "元;退货开具的红字发票为负数"),
    ("fact_invoice", "tax_amount"): ("税额", "不含税金额×13%,元;红字发票为负数"),
    ("fact_invoice", "total_amount"): ("价税合计", "元;红字发票为负数"),
    ("fact_ap", "due_date"): ("到期日", "月结:收货当月月末+账期天数"),
    ("fact_production_plan", "planned_quantity"): ("计划产量", "排程在当月完工的工单计划量,吨"),
    ("dim_fixed_asset", "equipment_id"): ("设备ID", "外键,关联dim_equipment;厂房、仓库与公用工程为空"),
    ("dim_customer", "first_deal_date"): ("首次成交日期", "老客户为系统上线(2025-01-01)前的历史首单日;新客户为首张订单日"),
    ("dim_customer_credit", "credit_limit"): ("授信额度", "近半年月末应收峰值的1.3倍(不低于50万),含税,元"),
    ("dim_metric_catalog", "category"): ("指标分类", "原子指标/派生指标/复合指标"),
    ("dim_metric_catalog", "metric_type"): ("指标业务类型", "产量/比率/计数/时长/能耗/销量/价格/金额/评分"),
    ("dim_derived_metric_config", "group_by_columns"): ("分组列", "汇总表中的分组列名(带 dim_ 前缀)"),
    ("dim_customer", "status"): ("客户状态", "期末状态:active=近90天有成交;dormant=91~180天;lost=超过180天;risk=有超期60天以上应收"),
    ("dim_customer_credit", "used_credit"): ("已用额度", "期末应收余额,含税,元"),
    ("fact_contract", "contract_type"): ("合同类型", "purchase=采购框架合同(party_id为供应商);sales=销售框架合同(party_id为客户)"),
    ("fact_contract", "party_id"): ("签约方ID", "采购合同为dim_supplier.supplier_id,销售合同为dim_customer.cust_id"),
    ("dim_hazard", "location"): ("所在位置", "车间及区域"),
    ("fact_env_record", "unit"): ("单位", "mg/L、mg/m³、dB(A)"),
}
TABLE_FIX = {
    "fact_contract": ("合同", "采购与销售框架合同,记录合同编号、类型、签约方、有效期、预计总金额、状态。"),
    "fact_material_consumption": ("投料记录", "生产投料记录:按工单×原料记录BOM理论用量、实际投料与工艺损耗,原料批次可追溯到收货单。"),
    "dim_team": ("生产班组", "生产班组:每条挤出线甲乙丙丁四个班组,四班三运转(每天三班在岗、一班轮休),记录班组长与所属产线。"),
    "fact_expense": ("费用报销", "销售费用报销:差旅、招待、市内交通、通讯、办公,以及月末按发货吨数计提的运输费、按开票额计提的渠道佣金和季度市场推广费。"),
    "fact_ap": ("应付账款", "应付账款:每张合格收货单一条,金额为收货数量×采购单价×1.13;到期日为收货当月月末加账期天数;付清为 closed,否则 open。"),
    "fact_return": ("退货", "销售退货:每笔退货开具红字发票冲减原发票应收;退回货物按不合格品报废处理,不回到成品库存。"),
    "fact_invoice": ("发票", "销售发票:蓝字发票与发货单一一对应,不含税金额=发货金额,税额按13%增值税;退货开具红字发票(金额为负,关联原发货单),冲减原发票应收。"),
    "dim_employee": ("员工", "员工档案:管理、职能岗位与各线班组长等关键岗位人员;一线操作工按班组计,不逐人建档。"),
}


# 枚举类字段的说明按实际取值生成,避免说明与数据不符
ENUM_COLUMNS = {("dim_supplier", "credit_level"), ("dim_sensor", "sensor_type"), ("fact_alarm", "alarm_type"), ("fact_alarm", "severity"),
                ("fact_expense", "expense_type"), ("dim_customer", "industry"), ("fact_purchase_order", "payment_terms"), ("fact_ap", "status"),
                ("fact_purchase_order", "status"), ("fact_maintenance_wo", "maint_type"), ("fact_maintenance_wo", "status"),
                ("fact_incident", "severity"), ("fact_production_order", "status"), ("fact_sales_order", "status"),
                ("fact_quality_inspection", "result"), ("fact_goods_receipt", "inspection_result"), ("dim_customer_credit", "credit_level"),
                ("fact_return", "reason"), ("dim_fixed_asset", "asset_type")}

# 指标目录(沿用原库)中的错误:派生指标的分组列应带 dim_ 前缀;几个复合指标公式与说明不对;部分说明与单位不符
CATALOG_FIX = [
    "UPDATE dim_derived_metric_config SET group_by_columns='dim_'||group_by_columns WHERE group_by_columns NOT LIKE 'dim_%'",
    "UPDATE dim_derived_metric_config SET derived_metric_name='累计实际支出', calc_method='SUM', description='按成本中心汇总实际支出' WHERE derived_metric_name='平均执行率'",
    "UPDATE dim_composite_metric_config SET description='销售收入与采购支出之比(未计人工、制造费用与期间费用,不表示盈亏)' WHERE composite_metric_name='投入产出比'",
    "UPDATE dim_composite_metric_config SET component_metric_names='累计收入,总成本', formula='(累计收入-总成本)/NULLIF(累计收入,0)*100', "
    "description='以当期产品生产总成本近似已售成本的毛利率;制造费用已含折旧,不再重复扣减;未扣期间费用' WHERE composite_metric_name='经营利润率'",
    "UPDATE dim_composite_metric_config SET formula='总运行小时/NULLIF(总运行小时+总停机时长,0)*100', description='运行时长占(运行+停机)时长的比例' WHERE composite_metric_name='设备可用率'",
    "UPDATE dim_composite_metric_config SET description='来料检验合格率的日均值(按天简单平均,未按批次加权)' WHERE composite_metric_name='一次合格率'",
    "UPDATE dim_composite_metric_config SET component_metric_names='累计实际支出,累计预算', formula='(累计实际支出-累计预算)/NULLIF(累计预算,0)*100', "
    "description='实际支出偏离预算的百分比' WHERE composite_metric_name='预算偏差度'",
    "UPDATE dim_metric_catalog SET metric_name='累计实际支出', description='按成本中心汇总实际支出' WHERE metric_name='平均执行率'",
    "UPDATE dim_metric_catalog SET description=(SELECT description FROM dim_composite_metric_config c WHERE c.composite_metric_name=dim_metric_catalog.metric_name) "
    "WHERE category='复合指标'",
    "UPDATE dim_metric_catalog SET description='工单计划量(按完工日汇总)' WHERE metric_name='计划产量'",
    "UPDATE dim_metric_catalog SET description='工单实际产量(按完工日汇总)' WHERE metric_name='实际产量'",
    "UPDATE dim_metric_catalog SET unit_code='%' WHERE metric_name='指导价偏差'",
    # 「销售收入」实为按下单日汇总的订单金额,改名为接单金额,引用它的派生、复合指标同步改名
    "UPDATE dim_metric_catalog SET metric_name='接单金额', description='按下单日汇总的订单金额(不含税),含未交付与后续退货部分;与按发货或开票确认的收入不同' WHERE metric_name='销售收入'",
    "UPDATE dim_derived_metric_config SET atomic_metric_names='接单金额' WHERE atomic_metric_names='销售收入'",
    "UPDATE dim_derived_metric_config SET derived_metric_name=replace(derived_metric_name,'收入','接单金额'), description=replace(description,'收入','接单金额') "
    "WHERE derived_metric_name IN ('累计收入','日均收入','最高单日收入')",
    "UPDATE dim_metric_catalog SET metric_name=replace(metric_name,'收入','接单金额'), description=replace(description,'收入','接单金额') "
    "WHERE metric_name IN ('累计收入','日均收入','最高单日收入')",
    "UPDATE dim_composite_metric_config SET component_metric_names=replace(component_metric_names,'累计收入','累计接单金额'), "
    "formula=replace(formula,'累计收入','累计接单金额') WHERE component_metric_names LIKE '%累计收入%'",
    "UPDATE dim_metric_catalog SET metric_name='新增应付账款', description='当日新增应付(含税,按收货日);期末未付余额 = 应付金额 − 已付金额(fact_ap)' WHERE metric_name='应付账款余额'",
    "UPDATE dim_metric_catalog SET description='产线运行时长(按日历日,工单运行时间扣除非计划停机)' WHERE metric_name='设备运行时长'",
    "UPDATE dim_metric_catalog SET description='设备运行时长:取所在产线当日运行时长,每台设备各记一份;按设备汇总约为产线运行时长的 5 倍' WHERE metric_name='设备运行小时'",
    "UPDATE dim_metric_catalog SET description='当日末未清应收余额(含税),时点值;跨日只能取期末一天,不能按日累加' WHERE metric_name='应收账款余额'",
    # 派生、复合指标补上业务类型
    "UPDATE dim_metric_catalog SET metric_type=COALESCE((SELECT a.metric_type FROM dim_derived_metric_config d JOIN dim_metric_catalog a "
    "ON a.metric_name=d.atomic_metric_names WHERE d.derived_metric_name=dim_metric_catalog.metric_name), metric_type) WHERE category='派生指标'",
    "UPDATE dim_metric_catalog SET metric_type=CASE metric_name WHEN '安全健康度' THEN '评分' WHEN '单位产出成本' THEN '金额' ELSE '比率' END WHERE category='复合指标'",
]


def fix_catalog(con: sqlite3.Connection) -> None:
    for sql in CATALOG_FIX:
        con.execute(sql)


def write(con: sqlite3.Connection, base: sqlite3.Connection) -> None:
    old_t = {r[0]: (r[1], r[2], r[3], r[4]) for r in base.execute("SELECT table_name, table_name_cn, table_desc, table_category, table_level FROM dim_table_metadata")}
    old_c = {(r[0], r[1]): (r[2], r[3]) for r in base.execute("SELECT table_name, column_name, column_name_cn, column_desc FROM dim_column_metadata")}
    con.execute("DELETE FROM dim_table_metadata")
    con.execute("DELETE FROM dim_column_metadata")
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    cn_default = {"DWS_EQUIPMENT_DAILY": "设备日汇总", "DWS_FINANCE_DAILY": "财务月汇总", "DWS_PROCUREMENT_DAILY": "采购日汇总",
                  "DWS_SAFETY_DAILY": "安全日汇总"}
    cid = 0
    for tid, tname in enumerate(tables, start=1):
        cn, desc, cat, lvl = old_t.get(tname, (None, None, None, None))
        cn = TABLE_FIX.get(tname, (cn,))[0] or cn_default.get(tname) or cn or tname
        desc = TABLE_FIX.get(tname, (None, desc))[1] or desc
        if tname in W.GRAINS:
            head = desc.split(W.GRAINS[tname])[0] if desc and W.GRAINS[tname] in desc else (desc.split("。")[0] + "。" if desc else "")
            desc = head + W.GRAINS[tname] + "。由明细表汇总生成,可与明细对账。"     # 以本库为底重跑时不重复追加
        cat = "维度表" if tname.startswith("dim_") else "事实表" if tname.startswith("fact_") else "汇总表"
        con.execute("INSERT INTO dim_table_metadata VALUES (?,?,?,?,?,?)", (tid, tname, cn, desc, cat, lvl or "core"))
        for ordinal, info in enumerate(con.execute(f'PRAGMA table_info("{tname}")').fetchall(), start=1):
            col = info[1]
            cn_c, desc_c = COLUMN_FIX.get((tname, col)) or old_c.get((tname, col)) or (None, None)
            if not cn_c or cn_c == col:
                cn_c, desc_c = COLUMN_CN.get(col, (None, None))
                if cn_c is None and info[5]:
                    cn_c, desc_c = "主键", "主键,自增"
            if cn_c is None:
                raise KeyError(f"no Chinese name for {tname}.{col}")
            if (tname, col) in ENUM_COLUMNS:
                vals = [str(r[0]) for r in con.execute(f'SELECT DISTINCT "{col}" FROM "{tname}" WHERE "{col}" IS NOT NULL ORDER BY 1')]
                desc_c = f"{cn_c}。取值:{'/'.join(vals)}"
            cid += 1
            con.execute("INSERT INTO dim_column_metadata VALUES (?,?,?,?,?,?)", (cid, tname, col, cn_c, desc_c or cn_c, ordinal))


def table_list(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


__all__: list[Any] = ["write", "table_list", "COLUMN_CN"]
