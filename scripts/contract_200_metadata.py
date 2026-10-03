#!/usr/bin/env python3
"""Chinese column names and table descriptions for the generated tables (generator v2).

v1 translated column names token by token with a 45-word dictionary and left the
rest in upper case (``RISKLEVEL标识``), and translated every ``line`` as 明细, so
a production-line reference was named 明细标识.  Here reference columns are named
after the parent table, whole column names have phrase translations, and any
token without a translation is an error.  Standard library only.
"""

from __future__ import annotations

from typing import Any

PHRASES = {
    "document_no": "单据号", "line_no": "行号", "business_date": "业务日期", "event_time": "发生时间", "posting_date": "过账日期",
    "status": "状态", "priority": "优先级", "reason_code": "原因码", "source_system": "来源系统", "source_record_id": "来源记录标识",
    "etl_batch_id": "ETL 批次号", "trace_id": "追溯标识", "tenant_code": "租户编码", "created_at": "创建时间", "updated_at": "更新时间",
    "created_by": "创建人", "updated_by": "更新人", "approved_by": "审批人", "approved_at": "审批时间",
    "source_document_no": "来源单据号", "row_version": "行版本", "data_quality_score": "数据质量分", "is_deleted": "逻辑删除标记",
    "remarks": "备注", "unit_code": "计量单位", "quantity": "数量", "base_quantity": "基本单位数量", "planned_quantity": "计划数量",
    "actual_quantity": "实际数量", "qualified_quantity": "合格数量", "rejected_quantity": "不合格数量", "currency_code": "币种",
    "exchange_rate": "汇率", "unit_price": "单价", "amount": "金额", "local_amount": "本位币金额", "tax_amount": "税额",
    "cost_amount": "成本金额", "budget_amount": "预算金额", "duration_minutes": "时长(分钟)", "rate_value": "比率值", "score_value": "评分",
    "code": "编码", "name": "名称", "name_en": "英文名称", "short_name": "简称", "description": "说明", "parent_code": "上级编码",
    "category_code": "分类编码", "type_code": "类型编码", "hierarchy_level": "层级", "sort_order": "排序号", "effective_from": "生效日期",
    "effective_to": "失效日期", "external_code": "外部编码", "legacy_code": "旧系统编码", "approval_status": "审批状态",
    "version_no": "版本号", "is_workday": "是否工作日", "is_holiday": "是否节假日", "is_functional": "是否本位币",
    "is_intentional": "是否有意构造", "sha256": "SHA256", "base_sha256": "底库 SHA256", "kg": "千克",
    "gross_weight_kg": "毛重(千克)", "carbon_emission_kg": "碳排放量(千克)", "rate_to_cny": "对人民币汇率",
    "probability": "赢单概率", "margin_rate": "毛利率", "yield_rate": "良率", "po": "采购订单",
    "customer_po_no": "客户采购单号", "bom_qty_per_ton": "BOM 吨耗", "kpi_code": "KPI 编码", "kpi_name": "KPI 名称",
    "cycle_time_seconds": "节拍(秒)", "setup_minutes": "换型时间(分钟)", "run_minutes": "运行时间(分钟)",
    "downtime_minutes": "停机时间(分钟)", "sampling_interval_seconds": "采样间隔(秒)", "break_minutes": "休息时间(分钟)",
    "crosses_midnight": "是否跨零点", "night_allowance_rate": "夜班津贴比例", "follow_up_date": "跟进日期",
    "sign_in_time": "签到时间", "clock_in": "上班打卡", "clock_out": "下班打卡", "lead_time_days": "提前期(天)",
    "supplier_response_days": "供应商响应天数", "freeze_horizon_days": "冻结期(天)", "replacement_lead_days": "补岗周期(天)",
    "default_term_days": "默认期限(天)", "net_days": "账期(天)", "discount_days": "折扣期(天)", "validity_months": "有效期(月)",
    "renewal_months": "复训周期(月)", "minimum_experience_years": "最低工作年限", "useful_life_years": "使用年限",
    "remaining_life_hours": "剩余寿命(小时)", "labor_hours": "工时", "regular_hours": "正常工时", "overtime_hours": "加班工时",
    "paid_hours": "计薪工时", "capacity_hours": "产能工时", "credit_hours": "学分学时", "training_hours": "培训学时",
    "duration_minutes_": "时长", "month_end_flag": "是否月末", "week_start_date": "周起始日期", "holiday_name": "节假日名称",
    "calendar_date": "日历日期", "calendar_year": "年", "calendar_quarter": "季度", "calendar_month": "月", "calendar_week": "周",
    "day_of_month": "日", "day_of_week": "星期", "fiscal_year": "会计年度", "fiscal_period": "会计期间", "timezone_name": "时区",
    "shift_start": "班次开始", "shift_end": "班次结束", "team_size": "班组人数", "numeric_code": "数字代码", "decimal_places": "小数位数",
    "country_code": "国家代码", "rate_source": "汇率来源", "dimension_type": "量纲", "base_unit_code": "基本单位",
    "conversion_factor": "换算系数", "allow_fraction": "是否允许小数", "reference_standard": "参考标准",
    "symbol": "符号", "tax_rate": "税率", "tax_type": "税种", "invoice_type": "发票类型", "deductible_flag": "是否可抵扣",
    "legal_basis": "法律依据", "accounting_scope": "核算范围", "discount_rate": "折扣率", "payment_method": "付款方式",
    "settlement_method": "结算方式", "installment_count": "分期数", "applies_to": "适用对象", "incoterm_version": "术语版本",
    "risk_transfer_point": "风险转移点", "freight_paid_by": "运费承担方", "insurance_paid_by": "保险承担方",
    "export_clearance_by": "出口清关方", "import_clearance_by": "进口清关方", "transport_mode": "运输方式",
    "abbreviation": "缩写", "display_value": "显示值", "usage_scope": "使用范围", "allow_manual_entry": "是否允许手工录入",
    "requires_approval": "是否需审批", "severity_level": "严重等级", "reason_group": "原因分组",
    "first_grade_rate": "一等品率", "valid_from": "生效日期", "expiry_date": "失效日期", "manufacture_date": "生产日期",
    "recount_required": "是否需复盘", "online_flag": "是否线上", "renewable_flag": "是否可再生", "capitalized_flag": "是否已资本化",
    "exceed_flag": "是否超标", "passed_flag": "是否通过", "destructive_flag": "是否破坏性", "critical_skill": "是否关键技能",
    "critical_position": "是否关键岗位", "calibration_required": "是否需校准", "renewal_required": "是否需复审",
    "requires_legal_review": "是否需法务审核", "is_deleted_": "删除", "old_unit_price": "原单价", "new_unit_price": "新单价",
    "adjust_rate": "调价幅度", "next_due_date": "下次到期日", "due_date": "到期日", "closed_date": "关闭日期",
    "action_due_date": "整改期限", "response_due_date": "响应期限", "signed_date": "签收日期", "handover_date": "交接日期",
    "release_date": "发布日期", "planned_start_date": "计划开始日期", "planned_end_date": "计划结束日期",
    "requested_date": "需求日期", "promised_date": "承诺日期", "received_date": "到货日期",
    "requested_delivery_date": "要求交货日期", "promised_delivery_date": "承诺交货日期", "expected_close_date": "预计成交日期",
    "detected_at": "发现时间", "resolved_at": "解决时间", "start_time": "开始时间", "end_time": "结束时间",
    "table_count": "表数", "column_count": "列数", "random_seed": "随机种子", "generator_version": "生成器版本",
    "release_name": "版本名称", "owner_team": "责任团队", "case_type": "案例类型", "target_table": "目标表", "target_column": "目标列",
    "expected_behavior": "预期行为", "detection_rule": "检测规则", "expected_count": "预期数量", "tolerance_value": "容差",
    "remediation_hint": "处理建议", "origin": "来源", "rule_code": "规则编码", "check_name": "检查名称", "expected_value": "期望值",
    "actual_value": "实际值", "exception_count": "异常数", "resolution_status": "处理状态", "evidence_path": "证据路径",
    "validation_query": "校验查询", "severity": "严重度", "likelihood": "可能性", "risk_score": "风险分值",
    "operation_seq": "工序号", "order_no": "工单号", "batch_no": "批次号", "lot_no": "批号", "bin_code": "库位编码",
    "work_center_code": "工作中心编码", "routing_version": "工艺路线版本", "process_stage": "工艺阶段", "shift_code": "班次编码",
    "scrap_quantity": "报废数量", "available_quantity": "可用数量", "reserved_quantity": "预留数量", "book_quantity": "账面数量",
    "counted_quantity": "实盘数量", "variance_quantity": "差异数量", "standard_quantity": "标准用量", "usage_variance_rate": "用量差异率",
    "demand_quantity": "需求数量", "supply_quantity": "供应数量", "backlog_quantity": "欠交数量", "safety_stock_quantity": "安全库存",
    "minimum_order_qty": "最小订货量", "stock_before": "领用前库存", "stock_after": "领用后库存", "used_cycles": "已用次数",
    "remaining_life": "剩余寿命", "wear_rate": "磨损率", "meter_start": "起始读数", "meter_end": "结束读数",
    "reading_value": "读数", "reading_unit": "读数单位", "lower_threshold": "下限阈值", "upper_threshold": "上限阈值",
    "measured_value": "测量值", "nominal_value": "名义值", "lower_limit": "下限", "upper_limit": "上限",
    "lower_spec_limit": "规格下限", "upper_spec_limit": "规格上限", "lower_control_limit": "控制下限", "upper_control_limit": "控制上限",
    "standard_value": "标准值", "error_value": "误差", "tolerance": "允差", "emission_value": "排放值", "limit_value": "限值",
    "regulatory_limit": "法定限值", "emission_factor": "排放因子", "standard_coal_factor": "折标煤系数",
    "health_index": "健康指数", "satisfaction_score": "满意度", "assessment_score": "评估得分", "exam_score": "考试成绩",
    "confidence_level": "置信度", "forecast_accuracy": "预测准确率", "utilization_rate": "利用率", "achievement_rate": "达成率",
    "target_value": "目标值", "commission_rate": "佣金比例", "target_margin": "目标毛利率", "growth_target": "增长目标",
    "annual_potential": "年度潜力", "residual_rate": "残值率", "capitalization_threshold": "资本化门槛", "budget_ceiling": "预算上限",
    "net_book_value": "账面净值", "asset_value_added": "新增资产价值", "compensation_amount": "赔偿金额", "exam_fee": "考试费",
    "debit_amount": "借方金额", "credit_amount": "贷方金额", "account_code": "科目编码", "cost_center_code": "成本中心编码",
    "profit_center_code": "利润中心编码", "voucher_no": "凭证号", "clearing_document": "清账单据", "reference_document": "参考单据",
    "invoice_ref": "发票号", "gl_account_code": "总账科目", "mat_ref": "物料编码(遗留系统)", "freight_amount": "运费", "handled_by_code": "经办人编码",
    "inspector_code": "检验员编码", "summary_text": "摘要",
}

TOKENS = {
    "absence": "缺勤", "acceptance": "验收", "account": "客户", "action": "措施", "agreement": "协议", "alarm": "报警", "approval": "审批",
    "assessment": "评估", "assessor": "评估人", "asset": "资产", "attendance": "出勤", "authority": "机构", "basis": "依据",
    "buyer": "采购员", "calculation": "计算", "campaign": "营销活动", "carrier": "承运商", "case": "案例", "category": "类别",
    "cause": "原因", "certificate": "证书", "channel": "渠道", "characteristic": "特性", "city": "城市", "claim": "索赔",
    "clause": "条款", "collection": "采集", "competitor": "竞争对手", "complaint": "投诉", "contact": "联系", "containment": "围堵",
    "contract": "合同", "control": "控制", "count": "盘点", "criticality": "关键度", "customer": "客户", "defect": "缺陷",
    "delivery": "交货", "depreciation": "折旧", "disposition": "处置", "education": "学历", "element": "要素", "employee": "员工",
    "evaluator": "评价人", "event": "事件", "evidence": "证据", "exception": "例外", "family": "族", "failure": "故障",
    "forecast": "预测", "frequency": "频次", "from": "发自", "funding": "资金", "gauge": "量具", "grade": "职级", "hazard": "危险",
    "headcount": "编制", "industry": "行业", "inspection": "检验", "instrument": "仪器", "investment": "投资", "issue": "领用",
    "issuing": "发证", "job": "岗位", "lead": "线索", "level": "等级", "levels": "等级数", "location": "位置", "maintenance": "维护",
    "mandatory": "强制", "market": "市场", "measurement": "计量", "medium": "介质", "meter": "计量表", "method": "方法",
    "monitoring": "监测", "movement": "移动", "next": "下一步", "observation": "观察", "operator": "操作员", "opportunity": "商机",
    "package": "包装", "parameter": "参数", "part": "备件", "permit": "许可", "phase": "阶段", "plan": "计划", "planner": "计划员",
    "planning": "计划", "point": "点", "pollutant": "污染物", "position": "岗位", "price": "价格", "proficiency": "熟练度",
    "project": "项目", "province": "省份", "quality": "质量", "quote": "报价", "rating": "评分", "reaction": "反应", "receiver": "接收人",
    "region": "区域", "regulatory": "法规", "reporting": "报告", "requester": "申请人", "requisition": "请购", "result": "结果",
    "review": "评审", "risk": "风险", "role": "角色", "root": "根本", "sales": "销售", "sample": "样本", "sampling": "采样",
    "scenario": "情景", "scope": "范围", "segment": "细分", "sensor": "传感器", "service": "服务", "site": "地点", "size": "规模",
    "skill": "技能", "sourcing": "寻源", "stage": "阶段", "stock": "库存", "strategy": "策略", "supervisor": "主管",
    "supplier": "供应商", "task": "任务", "template": "模板", "terms": "条款", "tier": "档次", "tool": "工装", "topic": "主题",
    "tracking": "追踪", "training": "培训", "transfer": "调拨", "type": "类型", "valuation": "计价", "vehicle": "车辆",
    "verification": "验证", "warehouse": "仓库", "code": "编码", "no": "编号", "method_": "方法", "ship": "发运", "to": "至",
    "list": "表", "policy": "政策", "responsible": "负责", "education_": "学历", "minimum": "最低", "experience": "经验",
    "requirement": "要求", "mode": "方式", "class": "类别", "cost": "成本", "object": "对象", "item": "项目", "group": "分组",
    "unit": "单位", "flag": "标记", "approved": "批准", "bucket": "期间", "calibration": "校准", "capacity": "产能",
    "credit": "信用", "effective": "生效", "for": "", "incoterm": "贸易术语", "legal": "法务", "measure": "措施", "payment": "付款",
    "period": "期间", "precision": "精度", "reason": "原因", "reference": "参考", "renewal": "续约", "resolution": "处理",
    "rule": "规则", "source": "来源", "standard": "标准", "version": "版本", "work": "工作", "scale": "精度位", "term": "条件", "date": "日期", "time": "时间", "rate": "比率", "score": "评分", "value": "值", "id": "ID",
}


def column_cn(table: str, column: str, refs: dict[str, Any], table_cn: dict[str, str]) -> str:
    """refs: {列: Ref};table_cn: {表: 中文名}。引用列按「角色 + 父表中文名 + ID」命名。"""
    if column in refs:
        ref = refs[column]
        parent = table_cn.get(ref.parent_table, ref.parent_table)
        role = column[: -len(ref.parent_column)].rstrip("_") if column.endswith(ref.parent_column) and column != ref.parent_column else ""
        role_cn = {"source": "来源", "target": "目标", "owner": "负责人", "owner_emp": "负责人"}.get(role, "")
        if not role_cn and column.split("_")[0] in ("source", "target", "owner"):
            role_cn = {"source": "来源", "target": "目标", "owner": "负责人"}[column.split("_")[0]]
        return f"{role_cn}{parent}ID"
    if column in PHRASES:
        return PHRASES[column]
    parts = []
    for tok in column.split("_"):
        if tok in PHRASES:
            parts.append(PHRASES[tok])
        elif tok in TOKENS:
            parts.append(TOKENS[tok])
        else:
            raise KeyError(f"no Chinese name for token '{tok}' in {table}.{column}")
    return "".join(parts)


def table_description(spec: Any, table_cn: dict[str, str], header_cn: str | None) -> str:
    cn = spec.cn_name
    if spec.kind == "dim":
        grain = f"每行一个{cn}"
    elif header_cn:
        grain = f"每行是一张{header_cn}的一条明细"
    else:
        grain = f"每行一条{cn}记录"
    parents = []
    for ref in spec.refs:
        p = table_cn.get(ref.parent_table, ref.parent_table)
        if p not in parents:
            parents.append(p)
    rel = f";关联{'、'.join(parents)}" if parents else ""
    return f"{cn}({spec.domain}域):{grain}{rel}。合成数据,不对应真实业务记录。"
