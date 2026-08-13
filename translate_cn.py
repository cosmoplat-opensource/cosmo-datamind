#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""translate_cn.py [ir.json] — 为数据本体 IR 补中文名(对象 cn / 字段 attrs[].cn)。
零编造原则:① 优先复用 IR 内已有的人工/LLM 确认翻译(精确列名映射);② 其余按制造/财务域词元词典逐词翻译,
**仅当整列所有词元都命中词典时才填**,任一词元未知则留空(宁缺勿错)。可重复运行,幂等。"""
import json, re, sys

IR = sys.argv[1] if len(sys.argv) > 1 else "workdir/demo_ir.json"

# 词元词典(英文/拼音 stem → 中文),制造(MOM 域)+财务+销售域
TOKEN = {
    "id":"ID","code":"编码","no":"编号","name":"名称","type":"类型","status":"状态","state":"状态",
    "date":"日期","time":"时间","datetime":"时间","ts":"时间戳","count":"数量","cnt":"数量","qty":"数量","quantity":"数量",
    "amount":"金额","amt":"金额","cost":"成本","price":"单价","fee":"费用","rate":"率","ratio":"比率","pct":"百分比","percent":"百分比",
    "unit":"单位","total":"合计","sum":"合计","subtotal":"小计","target":"目标","plan":"计划","planned":"计划","actual":"实际",
    "category":"类别","cat":"类别","class":"类别","level":"等级","grade":"等级","score":"评分","value":"值","val":"值",
    "description":"描述","desc":"说明","remark":"备注","note":"备注","comment":"备注","location":"位置","loc":"位置","addr":"地址","address":"地址",
    "standard":"标准","std":"标准","budget":"预算","aging":"账龄","payment":"付款","pay":"付款","profit":"利润","revenue":"收入","income":"收入",
    "sales":"销售","sale":"销售","supplier":"供应商","vendor":"供应商","material":"物料","equipment":"设备","device":"设备",
    "line":"产线","hours":"工时","hour":"小时","duration":"时长","group":"分组","region":"区域","area":"区域","zone":"区域",
    "dept":"部门","department":"部门","emp":"员工","employee":"员工","staff":"员工","customer":"客户","cust":"客户","client":"客户",
    "product":"产品","prod":"产品","item":"项目","order":"订单","warehouse":"仓库","wh":"仓库","inventory":"库存","stock":"库存",
    "incident":"异常事件","event":"事件","quality":"质量","qc":"质检","yield":"良率","output":"产出","input":"投入","throughput":"产能",
    "gross":"毛","net":"净","month":"月","monthly":"月度","year":"年","yearly":"年度","annual":"年度","day":"日","daily":"日","week":"周","weekly":"周",
    "avg":"平均","average":"平均","mean":"平均","max":"最高","maximum":"最高","min":"最低","minimum":"最低","cum":"累计","cumulative":"累计",
    "delivered":"已交付","delivery":"交付","undelivered":"未交付","shipped":"已发货","return":"退货","returned":"退货","refund":"退款",
    "defect":"缺陷","defective":"不良","pass":"合格","passed":"合格","fail":"不合格","failed":"不合格","reject":"拒收","scrap":"报废",
    "mo":"制令","wo":"工单","bom":"BOM","po":"采购订单","so":"销售订单","kpi":"KPI","roi":"投资回报","oee":"设备综合效率",
    "start":"开始","end":"结束","begin":"起始","finish":"完成","create":"创建","created":"创建","update":"更新","updated":"更新","modify":"修改",
    "flag":"标志","is":"是否","has":"是否","enabled":"启用","active":"有效","valid":"有效","deleted":"已删除","source":"来源","src":"来源",
    "operation":"操作","operator":"操作员","process":"工序","step":"步骤","stage":"阶段","batch":"批次","lot":"批号","serial":"序列号","sn":"序列号",
    "工厂":"工厂","factory":"工厂","plant":"工厂","workshop":"车间","shift":"班次","team":"班组","project":"项目","contract":"合同",
    "invoice":"发票","account":"账户","balance":"余额","credit":"信用","debit":"借方","tax":"税","currency":"币种",
    "sku":"SKU","model":"型号","spec":"规格","version":"版本","seq":"序号","index":"序号","key":"键","parent":"父级","child":"子级","org":"组织",
    "eval":"评估","evaluation":"评估","review":"评审","audit":"审计","approval":"审批","approve":"审批","submit":"提交","confirm":"确认",
    "performance":"绩效","perf":"绩效","efficiency":"效率","utilization":"利用率","availability":"可用率","downtime":"停机","uptime":"运行时长",
    "energy":"能耗","power":"电力","water":"水","gas":"燃气","labor":"人工","overhead":"制造费用","depreciation":"折旧","expense":"费用",
    "after":"售后","service":"服务","warranty":"保修","complaint":"投诉","satisfaction":"满意度","nps":"净推荐值","finance":"财务","fin":"财务",
    "row":"行","col":"列","period":"期间","fiscal":"财务","planning":"计划","forecast":"预测","forecasted":"预测","achieve":"达成","achievement":"达成",
    "maint":"维护","maintenance":"维护","capacity":"产能","hazard":"隐患","sensor":"传感器","training":"培训",
    "column":"列","table":"表","cash":"现金","receivable":"应收","payable":"应付","inspection":"检验","inspect":"检验","repair":"维修",
    "alarm":"报警","threshold":"阈值","reading":"读数","temperature":"温度","temp":"温度","pressure":"压力","speed":"转速","vibration":"振动",
    "delay":"延误","delayed":"延误","overdue":"逾期","first":"首","last":"末","current":"当前","previous":"上期","ytd":"年初至今","mtd":"月初至今",
    "attendance":"出勤","absence":"缺勤","turnover":"周转","headcount":"人数","recruit":"招聘","resignation":"离职","promotion":"晋升",
    # 本数据集上下文已确证的缩写(唯一含义):
    "cc":"成本中心","bu":"事业部","gr":"收货","dep":"折旧","ap":"应付账款","ar":"应收账款","coa":"会计科目","opp":"商机",
    "kwh":"千瓦时","env":"环境","carrier":"承运商","transport":"运输","mode":"方式","contact":"联系","phone":"电话","person":"人员",
    "leaf":"叶子","direction":"方向","ordinal":"序号","config":"配置","component":"组成","limit":"限额","used":"已用","days":"天数",
    "share":"份额","guide":"指导","deviation":"偏差","sample":"样品","cycle":"周期","estimated":"预估","est":"预估",
    "run":"运行","negative":"负","margin":"毛利","assessment":"评估","enterprise":"企业","accounting":"会计","effective":"生效",
    "bank":"银行","acceptance":"承兑","near":"接近","miss":"未遂","permit":"许可","risk":"风险","high":"高","low":"低",
    "transit":"在途","goods":"货物","opportunity":"商机","enrollment":"参保","enroll":"参保","accounts":"账款","guarantee":"担保","deposit":"保证金","collection":"回款","dso":"回款周期","dpo":"付款周期","dio":"库存周期","wip":"在制品",
    # 制造/安全/资产/供应链域补充(本数据集出现,均无歧义):
    "work":"工作","center":"中心","fixed":"固定","asset":"资产","spare":"备件","part":"零件","storage":"存储","tool":"工具",
    "routing":"工艺路线","pricing":"定价","reason":"原因","metadata":"元数据","catalog":"目录","requisition":"申请",
    "receipt":"收货","record":"记录","iot":"物联网","inv":"调查","quotation":"报价","safety":"安全","objective":"目标",
    "accumulated":"累计","accuracy":"精度","action":"措施","required":"所需","applicant":"申请人","sqm":"平方米",
    "assess":"评估","assessor":"评估人","assigned":"指派","tech":"技术","atomic":"原子","attendee":"参与","auditor":"审计员","calc":"计算",
    "consequence":"后果","contributory":"诱因","control":"控制","needed":"所需","cooperation":"合作","since":"起","deadline":"截止",
    "design":"设计","life":"寿命","years":"年","emission":"排放","established":"成立","expired":"到期","fatality":"死亡","filter":"筛选",
    "finding":"发现","handled":"处理","ids":"ID","injury":"受伤","inspector":"检验员","install":"安装","interval":"间隔","investigator":"调查员",
    "issued":"签发","lead":"提前","leader":"负责人","legal":"法定","rep":"代表","likelihood":"可能性","manufacturer":"制造商","measured":"实测",
    "book":"账面","next":"下次","op":"工序","original":"原始","paid":"已付","party":"往来单位","ppe":"防护装备","priority":"优先级",
    "property":"财产","loss":"损失","purchase":"采购","received":"已收","reported":"上报","requester":"申请人","responsible":"责任",
    "root":"根本","cause":"原因","runtime":"运行","setup":"准备","severity":"严重度","steam":"蒸汽","topic":"主题","trainer":"培训师",
    "useful":"可用","from":"起","until":"至","kw":"千瓦","ton":"吨","tph":"吨每小时","hr":"小时","mins":"分钟","m3":"立方米","tons":"吨",
    "pc":"利润中心","wc":"工作中心","business":"业务","obj":"对象","data":"数据",
    "composite":"复合","derived":"派生"}
# 全表名短语覆盖(tr_table 优先查)
TABLE_PHRASE = {
    "dws_in_sales_daily":"内销日汇总","dws_market_daily":"市场日汇总","dws_pre_sales_daily":"售前日汇总","dws_procurement_daily":"采购日汇总",
    "dws_production_daily":"生产日汇总","dws_safety_daily":"安全日汇总","dim_business_unit":"事业部","dim_column_metadata":"列元数据",
    "dim_composite_metric_config":"复合指标配置","dim_cost_center":"成本中心","dim_derived_metric_config":"派生指标配置","dim_downtime_reason":"停机原因",
    "dim_fixed_asset":"固定资产","dim_metric_catalog":"指标目录","dim_pricing":"定价","dim_profit_center":"利润中心","dim_routing":"工艺路线",
    "dim_spare_part":"备件","dim_storage_location":"存储位置","dim_table_metadata":"表元数据","dim_tool":"工具","dim_work_center":"工作中心",
    "fact_env_record":"环境记录","fact_goods_receipt":"收货记录","fact_incident_inv":"事故调查","fact_iot_data":"物联网数据","fact_maintenance_record":"维护记录",
    "fact_payment_out":"付款记录","fact_purchase_order":"采购订单","fact_purchase_requisition":"采购申请","fact_quotation":"报价单","fact_safety_audit":"安全审计",
    "fact_safety_objective":"安全目标","fact_safety_plan":"安全计划","fact_safety_training":"安全培训","fact_work_permit":"作业许可"
}
# 短语级覆盖(整列名 → 更自然的中文,优先于逐词组合)
PHRASE = {
    "gross_profit":"毛利","gross_profit_actual":"实际毛利","gross_profit_target":"目标毛利","gross_margin":"毛利率",
    "unit_price":"单价","yield_rate":"良品率","pass_rate":"合格率","defect_rate":"不良率","return_rate":"退货率",
    "on_time_rate":"准时率","delivery_rate":"交付率","completion_rate":"完成率","utilization_rate":"利用率",
    "planned_quantity":"计划产量","actual_quantity":"实际产量","output_quantity":"产出数量","input_quantity":"投入数量",
    "total_cost":"总成本","total_amount":"总金额","total_product_cost":"产品总成本","labor_cost":"人工成本",
    "material_cost":"物料成本","budget_amount":"预算金额","sales_amount":"销售金额","order_amount":"订单金额",
    "order_date":"订单日期","output_date":"产出日期","create_date":"创建日期","update_date":"更新日期","stat_date":"统计日期",
    "emp_id":"业务员ID","dept_id":"部门ID","product_id":"产品ID","customer_id":"客户ID","supplier_id":"供应商ID",
    "order_no":"订单编号","batch_no":"批次号","serial_no":"序列号","invoice_no":"发票号",
    "operating_profit":"经营利润","operating_margin":"经营利润率","input_output_ratio":"投入产出比",
    "equipment_availability":"设备可用率","overall_yield":"综合良率","cumulative_output":"累计产量",
    "market_share":"市场份额","market_capacity":"市场容量","is_leaf":"是否叶子节点","gr_count":"收货单数","gr_quantity":"收货数量",
    "ap_amount":"应付金额","ar_balance":"应收余额","ar_amount":"应收金额","overdue_ar_amt":"逾期应收金额","dep_amount":"折旧金额",
    "cc_name":"成本中心名称","cc_code":"成本中心编码","cc_id":"成本中心ID","cc_type":"成本中心类型","dim_cc_id":"成本中心ID",
    "near_miss_count":"未遂事件数","unit_energy_consumption":"单位能耗","electricity_kwh":"用电量","guide_price_deviation":"指导价偏差",
    "accounts_receivable":"应收账款","negative_margin_qty":"负毛利数量","env_compliant_rate":"环境合规率","days_overdue_max":"最大逾期天数",
    "credit_limit":"信用额度","used_credit":"已用信用","transport_mode":"运输方式","contact_person":"联系人","contact_phone":"联系电话",
    "bu_id":"事业部ID","bu_name":"事业部名称","bu_type":"事业部类型","coa_id":"会计科目ID","coa_code":"会计科目编码","coa_name":"会计科目名称",
    "actual_run_hours":"实际运行工时","goods_in_transit_qty":"在途货物数量","goods_in_transit_amt":"在途货物金额","qty_per_ton":"单吨用量",
    "bank_acceptance_ratio":"银行承兑比率","avg_invoice_days":"平均开票天数","opp_estimated_quantity":"商机预估数量","opp_cycle":"商机周期",
    "opp_quantity":"商机数量","sample_quantity":"样品数量","risk_assessment_count":"风险评估数量","high_risk_count":"高风险数量","permit_count":"许可证数量",
    "maint_completed_count":"维护完成数量","accounting_standard":"会计准则","component_metric_names":"组成指标名称","composite_metric_name":"复合指标名称",
    "ar_aging":"应收账龄","ap_aging":"应付账龄","cash_balance":"现金余额","gross_margin_rate":"毛利率","operating_margin_rate":"经营利润率"
}

def norm(cn, name):
    return bool(cn) and cn.strip() != "" and cn.strip().upper() != (name or "").upper()

def tr_col(col):
    lc = col.lower()
    if lc in COL_PHRASE: return COL_PHRASE[lc]
    if lc in PHRASE: return PHRASE[lc]
    toks = [t for t in re.split(r"[_\s]+", lc) if t]
    if not toks: return None
    out = []
    for t in toks:
        if t in TOKEN: out.append(TOKEN[t])
        else: return None            # 任一词元未知 → 留空,不编造
    return "".join(out)

# 表名前缀 → 语义后缀
PFX = {"dws":"汇总","dim":"","fact":"","agg":"聚合","ads":"应用","dwd":"明细","ods":"贴源"}

COL_PHRASE = {
    "accumulated_dep":"累计折旧","net_book_value":"账面净值","original_value":"原值","root_cause":"根本原因","contributory_factors":"诱因",
    "aging_1m":"1个月账龄","aging_2m":"2个月账龄","aging_3m":"3个月账龄","aging_4m":"4个月账龄","aging_5m":"5个月账龄","aging_6m":"6个月账龄",
    "aging_6_12m":"6-12个月账龄","aging_12m_plus":"12个月以上账龄","capacity_hr_day":"日产能(小时)","capacity_ton":"产能(吨)","capacity_tph":"产能(吨每小时)",
    "area_sqm":"面积(平方米)","design_life_years":"设计寿命(年)","useful_life_years":"可用寿命(年)","lead_time_days":"采购提前期(天)","setup_time_min":"准备时间(分钟)",
    "power_kw":"功率(千瓦)","gas_m3":"燃气(立方米)","steam_tons":"蒸汽(吨)","water_tons":"水(吨)","runtime_hours":"运行小时","interval_hours":"间隔小时","interval_days":"间隔天数",
    "column_name_cn":"列中文名","table_name_cn":"表中文名","dws_table":"汇总表","group_by_columns":"分组列","filter_column":"筛选列","calc_method":"计算方法",
    "atomic_metric_names":"原子指标名称","derived_metric_name":"派生指标名称","safety_stock":"安全库存","is_compliant":"是否合规","is_handled":"是否处理",
    "ppe_required":"所需防护装备","action_required":"需采取措施","control_needed":"所需控制","cooperation_since":"合作起始","legal_rep":"法定代表人",
    "parts_replaced":"更换零件","responsible_dept_id":"责任部门ID","party_id":"往来单位ID","obj_desc":"对象说明","obj_id":"对象ID","work_desc":"工作说明",
    "total_life":"总寿命","used_life":"已用寿命","valid_from":"生效起","valid_to":"有效至","valid_until":"有效期至","assigned_tech":"指派技工","tech_cost_amount":"技术成本金额",
    "pc_id":"利润中心ID","pc_code":"利润中心编码","pc_name":"利润中心名称","wc_id":"工作中心ID","wc_code":"工作中心编码","wc_name":"工作中心名称","wc_type":"工作中心类型",
    "cost_center_id":"成本中心ID","fatality_count":"死亡人数","injury_count":"受伤人数","finding_count":"发现数","attendee_count":"参与人数","property_loss":"财产损失",
    "dim_equipment_id":"设备ID","dim_supplier_id":"供应商ID","formula":"公式","handled_by":"处理人","inspection_result":"检验结果","issued_by":"签发人",
    "method":"方法","payment_method":"付款方式","payment_terms":"付款条款","pr_date":"采购申请日期","pr_id":"采购申请ID","pr_no":"采购申请单号","reported_by":"上报人","timestamp":"时间戳"
}

def tr_table(name):
    lc = name.lower()
    if lc in TABLE_PHRASE: return TABLE_PHRASE[lc]
    m = re.match(r"(dws|dwd|dim|fact|agg|ads|ods)_(.+)", lc)
    if not m: return None
    pfx, rest = m.group(1), m.group(2)
    rest = re.sub(r"_(daily|monthly|yearly|weekly)$", "", rest)
    body = tr_col(rest)
    if not body: return None
    suf = PFX.get(pfx, "")
    tail = ""
    if re.search(r"_daily$", lc): tail = "日"
    elif re.search(r"_monthly$", lc): tail = "月"
    elif re.search(r"_yearly$", lc): tail = "年"
    return body + tail + suf

def main():
    ir = json.load(open(IR, encoding="utf-8"))
    # 1) 收集 IR 内已有的权威列名翻译作精确覆盖
    exact = {}
    for o in ir.get("objects", []):
        for a in o.get("attrs", []):
            if norm(a.get("cn"), a.get("col")):
                exact.setdefault(a["col"].lower(), a["cn"])
    obj_fixed = attr_fixed = 0
    for o in ir.get("objects", []):
        # 对象 cn
        if not norm(o.get("cn"), o.get("name")):
            t = tr_table(o.get("name", ""))
            if t: o["cn"] = t; obj_fixed += 1
        # 字段 cn
        for a in o.get("attrs", []):
            if norm(a.get("cn"), a.get("col")): continue
            lc = a["col"].lower()
            cn = exact.get(lc) or tr_col(a["col"])
            if cn: a["cn"] = cn; attr_fixed += 1
    json.dump(ir, open(IR, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"补中文名:对象 +{obj_fixed},字段 +{attr_fixed}")

if __name__ == "__main__":
    main()
