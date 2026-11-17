#!/usr/bin/env python3
"""Business semantics for the contract-aligned 200-table synthetic database (generator v2).

v1 filled every new table from one template: references were picked by row
number column by column (so a shipment line pointed at an order line of a
different order), measures were the same row-number sequence in every fact
table, and unmatched attribute columns received ``table:column:0001``
placeholders.  This module replaces that with:

* per-table attribute groups that fit the table's business meaning;
* value generators that read the record's own context (referenced rows,
  business date, quantities) instead of the row number;
* reference derivation along business chains (detail lines inherit their
  header's order and product, sensors resolve their equipment, departments
  come from the employee, materials come from the product's BOM);
* line allocation so each header owns its lines with sequential line numbers.

Only the standard library is used.
"""

from __future__ import annotations

import math
import random
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Callable

RATES = {"CNY": 1.0, "USD": 7.12, "EUR": 7.78, "JPY": 0.048}
USERS = [f"U{n:04d}" for n in range(1, 13)]
SOURCE_BY_DOMAIN = {
    "销售": "CRM", "采购": "SRM", "库存": "WMS", "生产": "MES", "质量": "QMS", "设备": "EAM", "能源": "EMS",
    "财务": "FIN", "资产": "FIN", "项目": "PMS", "环境": "EHS", "安全": "EHS", "人力": "HRM", "计划": "APS",
    "绩效": "BI", "治理": "DQ", "主数据": "MDM", "法务": "CLM",
}
DATE_START = date(2025, 1, 1)
DATE_SPAN = 545  # inclusive offset; calendar covers 2025-01-01 .. 2026-07-01

# 计量单位(维表 dim_unit_of_measure 的编码)与名称
UNITS = [("EA", "个"), ("KG", "千克"), ("TON", "吨"), ("HOUR", "小时"), ("KWH", "千瓦时"), ("M3", "立方米"),
         ("METER", "米"), ("PCT", "百分比"), ("SET", "套"), ("MIN", "分钟")]
CURRENCIES = [("CNY", "人民币", "¥", "156", 2), ("USD", "美元", "$", "840", 2), ("EUR", "欧元", "€", "978", 2), ("JPY", "日元", "¥", "392", 0)]
REASONS = [("QUALITY", "质量原因"), ("DELIVERY", "交付原因"), ("COST", "成本原因"), ("EQUIPMENT", "设备原因"), ("SAFETY", "安全原因"),
           ("ENVIRONMENT", "环境原因"), ("CUSTOMER", "客户原因"), ("SUPPLIER", "供应商原因"), ("PROCESS", "工艺原因"),
           ("MATERIAL", "物料原因"), ("SYSTEM", "系统原因"), ("OTHER", "常规业务(无特定原因)")]
SHIFTS = [("DAY", "白班", "08:00", "16:00", 30), ("SWING", "中班", "16:00", "24:00", 30), ("NIGHT", "夜班", "00:00", "08:00", 45)]
ENERGY_TYPES = [("ELEC", "电力", "KWH", 0.5703), ("WATER", "自来水", "M3", 0.0), ("NGAS", "天然气", "M3", 2.162), ("STEAM", "蒸汽", "TON", 110.0),
                ("CAIR", "压缩空气", "M3", 0.0), ("DIESEL", "柴油", "KG", 3.096), ("PV", "光伏发电", "KWH", 0.0), ("CHW", "冷冻水", "M3", 0.0),
                ("HOTW", "热水", "TON", 0.0), ("N2", "氮气", "M3", 0.0)]
EMISSION_TYPES = [("SO2", "二氧化硫", "mg/m³", 50.0), ("NOX", "氮氧化物", "mg/m³", 100.0), ("PM", "颗粒物", "mg/m³", 20.0), ("VOCS", "挥发性有机物", "mg/m³", 60.0),
                  ("COD", "化学需氧量", "mg/L", 500.0), ("NH3N", "氨氮", "mg/L", 45.0), ("CO2", "二氧化碳", "t", 0.0), ("NOISE", "厂界噪声", "dB(A)", 65.0),
                  ("TP", "总磷", "mg/L", 8.0), ("HW", "危险废物", "t", 0.0)]
TAX_CODES = [("VAT13", "增值税13%", 0.13), ("VAT9", "增值税9%", 0.09), ("VAT6", "增值税6%", 0.06), ("VAT3", "小规模3%", 0.03),
             ("EXP0", "出口零税率", 0.0), ("EXEMPT", "免税", 0.0)]
PAYMENT_TERMS = [("NET30", "月结30天", 30, 0.0), ("NET60", "月结60天", 60, 0.0), ("NET90", "月结90天", 90, 0.0), ("COD", "货到付款", 0, 0.0),
                 ("PREPAY", "预付款", 0, 0.0), ("2/10N30", "10天内付款折2%", 30, 0.02), ("LC90", "90天信用证", 90, 0.0), ("TT45", "电汇45天", 45, 0.0)]
INCOTERMS = [("EXW", "工厂交货"), ("FCA", "货交承运人"), ("FOB", "船上交货"), ("CFR", "成本加运费"), ("CIF", "成本保险加运费"),
             ("DAP", "目的地交货"), ("DPU", "卸货地交货"), ("DDP", "完税后交货")]
CONTRACT_TYPES = [("FRAME", "框架采购合同", 365), ("SPOT", "单次采购合同", 90), ("CONSIGN", "寄售协议", 365), ("SERVICE", "服务合同", 180),
                  ("TOLL", "来料加工合同", 180), ("QUALITY", "质量协议", 730), ("NDA", "保密协议", 1095), ("LEASE", "租赁合同", 365)]

STAGES = ["线索", "需求确认", "方案报价", "商务谈判", "赢单", "输单"]
STAGE_PROB = {"线索": 0.1, "需求确认": 0.25, "方案报价": 0.45, "商务谈判": 0.7, "赢单": 1.0, "输单": 0.0}
REGION_BY_PROVINCE = {"山东": "华北", "北京": "华北", "天津": "华北", "河北": "华北", "上海": "华东", "江苏": "华东", "浙江": "华东", "安徽": "华东",
                      "福建": "华南", "广东": "华南", "广西": "华南", "四川": "西南", "重庆": "西南", "湖北": "华中", "湖南": "华中", "河南": "华中",
                      "辽宁": "东北", "吉林": "东北", "黑龙江": "东北", "陕西": "西北"}


def day(offset: int) -> date:
    return DATE_START + timedelta(days=max(0, min(DATE_SPAN, offset)))


def iso(d: date | datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S") if isinstance(d, datetime) else d.isoformat()


def as_date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def lognorm(rng: random.Random, median: float, spread: float = 0.45) -> float:
    return median * math.exp(rng.gauss(0.0, spread))


class Context:
    """Lookups over base tables and already-seeded new tables, loaded lazily."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self._rows: dict[str, dict[Any, dict[str, Any]]] = {}
        self._ship_deliveries: list[Any] = []

    def rows(self, table: str, key: str) -> dict[Any, dict[str, Any]]:
        cache_key = f"{table}.{key}"
        if cache_key not in self._rows:
            cur = self.con.execute(f'SELECT * FROM "{table}"')
            names = [d[0] for d in cur.description]
            self._rows[cache_key] = {r[names.index(key)]: dict(zip(names, r)) for r in cur.fetchall()}
        return self._rows[cache_key]

    def invalidate(self, table: str) -> None:
        for k in [k for k in self._rows if k.startswith(table + ".")]:
            del self._rows[k]

    def ids(self, table: str, key: str) -> list[Any]:
        return sorted(self.rows(table, key))

    def group(self, table: str, key: str, by: str) -> dict[Any, list[Any]]:
        out: dict[Any, list[Any]] = {}
        for k, r in self.rows(table, key).items():
            out.setdefault(r[by], []).append(k)
        return out

    # 业务链上的常用推导
    def bom_materials(self, prod_id: Any) -> list[Any]:
        boms = [b for b, r in self.rows("dim_bom", "bom_id").items() if r["product_id"] == prod_id]
        return sorted({r["material_id"] for r in self.rows("dim_bom_line", "bom_line_id").values() if r["bom_id"] in boms})

    def routing_operations(self, prod_id: Any) -> list[Any]:
        routings = [k for k, r in self.rows("dim_routing", "routing_id").items() if r["product_id"] == prod_id]
        return sorted(k for k, r in self.rows("dim_operation", "operation_id").items() if r["routing_id"] in routings)

    def shifts_of_workshop(self, workshop_id: Any) -> list[Any]:
        return sorted(k for k, r in self.rows("dim_shift", "shift_id").items() if r["workshop_id"] == workshop_id)

    def line_workshop(self, line_id: Any) -> Any:
        return self.rows("dim_production_line", "line_id")[line_id]["workshop_id"]


# ───────────────────────── 事实表的列分组 ─────────────────────────
FACT_CORE: list[tuple[str, str]] = [
    ("document_no", "TEXT NOT NULL UNIQUE"),
    ("business_date", "DATE NOT NULL"),
    ("event_time", "DATETIME NOT NULL"),
    ("posting_date", "DATE NOT NULL"),
    ("status", "TEXT NOT NULL CHECK(status IN ('OPEN','IN_PROGRESS','APPROVED','CLOSED','CANCELLED'))"),
    ("priority", "INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 5)"),
    ("reason_code", "TEXT NOT NULL"),
    ("source_system", "TEXT NOT NULL"),
    ("source_record_id", "TEXT NOT NULL"),
    ("etl_batch_id", "TEXT NOT NULL"),
    ("trace_id", "TEXT NOT NULL"),
    ("tenant_code", "TEXT NOT NULL"),
    ("created_at", "DATETIME NOT NULL"),
    ("updated_at", "DATETIME NOT NULL"),
    ("created_by", "TEXT NOT NULL"),
    ("updated_by", "TEXT NOT NULL"),
    ("approved_by", "TEXT"),
    ("approved_at", "DATETIME"),
    ("source_document_no", "TEXT"),
    ("row_version", "INTEGER NOT NULL CHECK(row_version >= 1)"),
    ("data_quality_score", "REAL NOT NULL CHECK(data_quality_score BETWEEN 0 AND 1)"),
    ("is_deleted", "INTEGER NOT NULL CHECK(is_deleted IN (0,1))"),
    ("remarks", "TEXT"),
]
LINE_NO = [("line_no", "INTEGER NOT NULL CHECK(line_no >= 1)")]
FACT_QTY = [
    ("unit_code", "TEXT NOT NULL"),
    ("quantity", "REAL NOT NULL CHECK(quantity >= 0)"),
    ("base_quantity", "REAL NOT NULL CHECK(base_quantity >= 0)"),
    ("planned_quantity", "REAL NOT NULL CHECK(planned_quantity >= 0)"),
    ("actual_quantity", "REAL NOT NULL CHECK(actual_quantity >= 0)"),
    ("qualified_quantity", "REAL NOT NULL CHECK(qualified_quantity >= 0)"),
    ("rejected_quantity", "REAL NOT NULL CHECK(rejected_quantity >= 0)"),
]
FACT_MONEY = [
    ("currency_code", "TEXT NOT NULL"),
    ("exchange_rate", "REAL NOT NULL CHECK(exchange_rate > 0)"),
    ("unit_price", "REAL NOT NULL CHECK(unit_price >= 0)"),
    ("amount", "REAL NOT NULL CHECK(amount >= 0)"),
    ("local_amount", "REAL NOT NULL CHECK(local_amount >= 0)"),
    ("tax_amount", "REAL NOT NULL CHECK(tax_amount >= 0)"),
    ("cost_amount", "REAL NOT NULL CHECK(cost_amount >= 0)"),
    ("budget_amount", "REAL NOT NULL CHECK(budget_amount >= 0)"),
]
FACT_DURATION = [("duration_minutes", "REAL NOT NULL CHECK(duration_minutes >= 0)")]
FACT_SCORE = [("rate_value", "REAL NOT NULL CHECK(rate_value BETWEEN 0 AND 1)"), ("score_value", "REAL NOT NULL CHECK(score_value BETWEEN 0 AND 100)")]

# 业务属性组:每张表按含义挑选,值由 fact_attr 按记录上下文生成
ATTR_GROUPS: dict[str, list[tuple[str, str]]] = {
    "opportunity": [("lead_source", "TEXT"), ("opportunity_stage", "TEXT"), ("probability", "REAL"), ("expected_close_date", "DATE"),
                    ("campaign_code", "TEXT"), ("sales_region", "TEXT"), ("next_action", "TEXT"), ("contact_channel", "TEXT")],
    "order_ref": [("customer_po_no", "TEXT"), ("contract_no", "TEXT"), ("requested_delivery_date", "DATE"), ("promised_delivery_date", "DATE"),
                  ("delivery_terms", "TEXT"), ("sales_region", "TEXT"), ("price_list_code", "TEXT"), ("margin_rate", "REAL")],
    "forecast": [("forecast_bucket", "TEXT"), ("sales_region", "TEXT"), ("forecast_method", "TEXT"), ("confidence_level", "REAL"),
                 ("plan_version", "TEXT"), ("planner_code", "TEXT")],
    "pricing": [("price_list_code", "TEXT"), ("old_unit_price", "REAL"), ("new_unit_price", "REAL"), ("adjust_rate", "REAL"),
                ("effective_date", "DATE"), ("approval_no", "TEXT"), ("sales_region", "TEXT")],
    "logistics": [("carrier_tracking_no", "TEXT"), ("vehicle_no", "TEXT"), ("ship_from", "TEXT"), ("ship_to_city", "TEXT"),
                  ("gross_weight_kg", "REAL"), ("package_count", "INTEGER"), ("signed_date", "DATE"), ("freight_amount", "REAL")],
    "procurement": [("requisition_no", "TEXT"), ("buyer_code", "TEXT"), ("supplier_quote_no", "TEXT"), ("requested_date", "DATE"),
                    ("promised_date", "DATE"), ("received_date", "DATE"), ("payment_term_code", "TEXT"), ("incoterm_code", "TEXT"),
                    ("sourcing_method", "TEXT"), ("lead_time_days", "INTEGER"), ("quality_agreement_no", "TEXT")],
    "claim": [("claim_type", "TEXT"), ("claim_resolution", "TEXT"), ("claim_amount_basis", "TEXT"), ("supplier_response_days", "INTEGER")],
    "inventory": [("lot_no", "TEXT"), ("batch_no", "TEXT"), ("bin_code", "TEXT"), ("stock_status", "TEXT"), ("movement_type", "TEXT"),
                  ("manufacture_date", "DATE"), ("expiry_date", "DATE"), ("available_quantity", "REAL"), ("reserved_quantity", "REAL"),
                  ("valuation_type", "TEXT"), ("handled_by_code", "TEXT")],
    "count": [("bin_code", "TEXT"), ("book_quantity", "REAL"), ("counted_quantity", "REAL"), ("variance_quantity", "REAL"),
              ("count_method", "TEXT"), ("recount_required", "INTEGER")],
    "production": [("order_no", "TEXT"), ("operation_seq", "INTEGER"), ("process_stage", "TEXT"), ("shift_code", "TEXT"),
                   ("start_time", "DATETIME"), ("end_time", "DATETIME"), ("work_center_code", "TEXT"), ("batch_no", "TEXT"),
                   ("routing_version", "TEXT"), ("setup_minutes", "REAL"), ("run_minutes", "REAL"), ("cycle_time_seconds", "REAL"),
                   ("yield_rate", "REAL"), ("scrap_quantity", "REAL")],
    "material_use": [("order_no", "TEXT"), ("batch_no", "TEXT"), ("lot_no", "TEXT"), ("bom_qty_per_ton", "REAL"),
                     ("standard_quantity", "REAL"), ("usage_variance_rate", "REAL"), ("issue_type", "TEXT"), ("issue_warehouse", "TEXT")],
    "quality": [("inspection_no", "TEXT"), ("sample_size", "INTEGER"), ("characteristic_code", "TEXT"), ("measured_value", "REAL"),
                ("nominal_value", "REAL"), ("lower_limit", "REAL"), ("upper_limit", "REAL"), ("result_code", "TEXT"),
                ("defect_code", "TEXT"), ("severity", "INTEGER"), ("disposition", "TEXT"), ("root_cause_code", "TEXT"),
                ("containment_action", "TEXT"), ("verification_result", "TEXT"), ("inspector_code", "TEXT")],
    "quality_case": [("case_no", "TEXT"), ("defect_code", "TEXT"), ("severity", "INTEGER"), ("disposition", "TEXT"),
                     ("root_cause_code", "TEXT"), ("containment_action", "TEXT"), ("due_date", "DATE"), ("closed_date", "DATE"),
                     ("verification_result", "TEXT")],
    "equipment": [("event_code", "TEXT"), ("failure_mode", "TEXT"), ("maintenance_type", "TEXT"), ("downtime_minutes", "REAL"),
                  ("health_index", "REAL"), ("remaining_life_hours", "REAL"), ("operator_code", "TEXT")],
    "sensor": [("sensor_code", "TEXT"), ("reading_value", "REAL"), ("reading_unit", "TEXT"), ("lower_threshold", "REAL"),
               ("upper_threshold", "REAL"), ("alarm_code", "TEXT"), ("health_index", "REAL"), ("remaining_life_hours", "REAL")],
    "meter": [("meter_code", "TEXT"), ("meter_start", "REAL"), ("meter_end", "REAL"), ("reading_unit", "TEXT"),
              ("collection_method", "TEXT"), ("emission_factor", "REAL"), ("carbon_emission_kg", "REAL")],
    "finance": [("voucher_no", "TEXT"), ("fiscal_year", "INTEGER"), ("fiscal_period", "INTEGER"), ("debit_amount", "REAL"),
                ("credit_amount", "REAL"), ("account_code", "TEXT"), ("cost_center_code", "TEXT"), ("profit_center_code", "TEXT"),
                ("tax_rate", "REAL"), ("due_date", "DATE"), ("clearing_document", "TEXT"), ("reference_document", "TEXT"),
                ("summary_text", "TEXT")],
    "safety": [("observation_no", "TEXT"), ("hazard_type", "TEXT"), ("severity", "INTEGER"), ("likelihood", "INTEGER"),
               ("risk_score", "REAL"), ("control_measure", "TEXT"), ("location_code", "TEXT"), ("action_due_date", "DATE"),
               ("closed_date", "DATE"), ("permit_no", "TEXT")],
    "environment": [("monitoring_point", "TEXT"), ("emission_value", "REAL"), ("limit_value", "REAL"), ("measurement_unit", "TEXT"),
                    ("exceed_flag", "INTEGER"), ("regulatory_clause", "TEXT"), ("evidence_reference", "TEXT"), ("sampling_method", "TEXT")],
    "hr": [("employee_no", "TEXT"), ("work_date", "DATE"), ("shift_code", "TEXT"), ("clock_in", "TEXT"), ("clock_out", "TEXT"),
           ("regular_hours", "REAL"), ("overtime_hours", "REAL"), ("absence_type", "TEXT"), ("attendance_result", "TEXT"),
           ("supervisor_code", "TEXT")],
    "hr_record": [("employee_no", "TEXT"), ("position_code", "TEXT"), ("skill_level", "INTEGER"), ("assessment_score", "REAL"),
                  ("assessor_code", "TEXT"), ("valid_from", "DATE"), ("expiry_date", "DATE"), ("certificate_no", "TEXT"),
                  ("assessment_method", "TEXT")],
    "planning": [("plan_version", "TEXT"), ("planning_period", "TEXT"), ("scenario_code", "TEXT"), ("demand_quantity", "REAL"),
                 ("supply_quantity", "REAL"), ("capacity_hours", "REAL"), ("utilization_rate", "REAL"), ("backlog_quantity", "REAL"),
                 ("safety_stock_quantity", "REAL"), ("forecast_accuracy", "REAL"), ("exception_code", "TEXT"), ("review_status", "TEXT"),
                 ("planner_code", "TEXT"), ("freeze_horizon_days", "INTEGER")],
    "kpi": [("kpi_code", "TEXT"), ("kpi_name", "TEXT"), ("target_value", "REAL"), ("actual_value", "REAL"), ("achievement_rate", "REAL"),
            ("period_type", "TEXT"), ("planning_period", "TEXT"), ("evaluator_code", "TEXT")],
    "governance": [("rule_code", "TEXT"), ("check_name", "TEXT"), ("severity", "INTEGER"), ("detected_at", "DATETIME"),
                   ("resolved_at", "DATETIME"), ("expected_value", "TEXT"), ("actual_value", "TEXT"), ("exception_count", "INTEGER"),
                   ("owner_team", "TEXT"), ("resolution_status", "TEXT"), ("generator_version", "TEXT"), ("random_seed", "INTEGER"),
                   ("evidence_path", "TEXT"), ("validation_query", "TEXT")],
    "capex": [("project_phase", "TEXT"), ("cost_element", "TEXT"), ("capitalized_flag", "INTEGER"), ("asset_value_added", "REAL"),
              ("invoice_ref", "TEXT"), ("fiscal_year", "INTEGER"), ("fiscal_period", "INTEGER")],
    "asset_transfer": [("transfer_reason", "TEXT"), ("net_book_value", "REAL"), ("approval_no", "TEXT"), ("handover_date", "DATE"),
                       ("receiver_code", "TEXT")],
    "contact": [("contact_channel", "TEXT"), ("contact_topic", "TEXT"), ("contact_result", "TEXT"), ("follow_up_date", "DATE"),
                ("satisfaction_score", "REAL"), ("sales_region", "TEXT")],
    "complaint": [("complaint_channel", "TEXT"), ("complaint_type", "TEXT"), ("defect_code", "TEXT"), ("severity", "INTEGER"),
                  ("response_due_date", "DATE"), ("closed_date", "DATE"), ("compensation_amount", "REAL"), ("satisfaction_score", "REAL")],
    "calibration": [("gauge_code", "TEXT"), ("calibration_type", "TEXT"), ("standard_value", "REAL"), ("measured_value", "REAL"),
                    ("error_value", "REAL"), ("tolerance", "REAL"), ("result_code", "TEXT"), ("next_due_date", "DATE"),
                    ("certificate_no", "TEXT")],
    "maintenance": [("task_type", "TEXT"), ("failure_mode", "TEXT"), ("labor_hours", "REAL"), ("start_time", "DATETIME"),
                    ("end_time", "DATETIME"), ("result_code", "TEXT"), ("operator_code", "TEXT")],
    "spare": [("part_code", "TEXT"), ("issue_type", "TEXT"), ("stock_before", "REAL"), ("stock_after", "REAL"),
              ("requester_code", "TEXT"), ("maintenance_type", "TEXT")],
    "tooling": [("tool_code", "TEXT"), ("used_cycles", "INTEGER"), ("remaining_life", "INTEGER"), ("wear_rate", "REAL"),
                ("operator_code", "TEXT"), ("start_time", "DATETIME"), ("end_time", "DATETIME")],
    "training": [("employee_no", "TEXT"), ("training_no", "TEXT"), ("sign_in_time", "TEXT"), ("attendance_result", "TEXT"),
                 ("exam_score", "REAL"), ("passed_flag", "INTEGER"), ("credit_hours", "REAL")],
}

# ───────────────────────── 每张新事实表的语义配置 ─────────────────────────
# groups:Q 数量组、M 金额组、D 时长、S 比率与评分;attrs:业务属性组;unit:计量单位编码(数量组);
# line_of:明细表所属的表头(列, 表头表);money_unit_from:单价来源
FACT_SEMANTICS: dict[str, dict[str, Any]] = {
    "fact_lead": {"groups": "MS", "attrs": ["opportunity"], "price": 5000},
    "fact_customer_contact": {"groups": "DS", "attrs": ["contact"]},
    "fact_sales_contract_line": {"groups": "QM", "attrs": ["order_ref"], "unit": "TON", "line_of": ("contract_id", "fact_contract")},
    "fact_sales_order_line": {"groups": "QM", "attrs": ["order_ref"], "unit": "TON", "line_of": ("order_id", "fact_sales_order")},
    "fact_shipment": {"groups": "QM", "attrs": ["logistics", "order_ref"], "unit": "TON"},
    "fact_shipment_line": {"groups": "QM", "attrs": ["inventory"], "unit": "TON", "line_of": ("shipment_id", "fact_shipment")},
    "fact_sales_forecast": {"groups": "QMS", "attrs": ["forecast"], "unit": "TON"},
    "fact_price_adjustment": {"groups": "M", "attrs": ["pricing"]},
    "fact_supplier_contract": {"groups": "QM", "attrs": ["procurement"], "unit": "TON"},
    "fact_supplier_contract_line": {"groups": "QM", "attrs": ["procurement"], "unit": "TON", "line_of": ("supplier_contract_id", "fact_supplier_contract")},
    "fact_advance_shipping_notice": {"groups": "QM", "attrs": ["procurement", "logistics"], "unit": "TON"},
    "fact_asn_line": {"groups": "QM", "attrs": ["inventory"], "unit": "TON", "line_of": ("advance_shipping_notice_id", "fact_advance_shipping_notice")},
    "fact_supplier_claim": {"groups": "QM", "attrs": ["claim", "procurement"], "unit": "TON"},
    "fact_inventory_movement": {"groups": "QM", "attrs": ["inventory"], "unit": "TON"},
    "fact_inventory_snapshot": {"groups": "QM", "attrs": ["inventory"], "unit": "TON"},
    "fact_cycle_count": {"groups": "QD", "attrs": ["count"], "unit": "TON"},
    "fact_cycle_count_line": {"groups": "QM", "attrs": ["count", "inventory"], "unit": "TON", "line_of": ("cycle_count_id", "fact_cycle_count")},
    "fact_transfer_order": {"groups": "QM", "attrs": ["logistics", "inventory"], "unit": "TON"},
    "fact_transfer_order_line": {"groups": "QM", "attrs": ["inventory"], "unit": "TON", "line_of": ("transfer_order_id", "fact_transfer_order")},
    "fact_lot_trace": {"groups": "Q", "attrs": ["inventory", "material_use"], "unit": "TON"},
    "fact_batch_quality_hold": {"groups": "QD", "attrs": ["inventory", "quality_case"], "unit": "TON"},
    "fact_work_order_operation": {"groups": "QDS", "attrs": ["production"], "unit": "TON"},
    "fact_work_order_material": {"groups": "QM", "attrs": ["material_use"], "unit": "TON"},
    "fact_shift_report": {"groups": "QDS", "attrs": ["production", "hr"], "unit": "TON"},
    "fact_scrap_event": {"groups": "QM", "attrs": ["production", "quality_case"], "unit": "KG"},
    "fact_rework_order": {"groups": "QMD", "attrs": ["production", "quality_case"], "unit": "TON"},
    "fact_process_parameter_record": {"groups": "S", "attrs": ["production", "quality"]},
    "fact_labor_booking": {"groups": "QD", "attrs": ["production", "hr"], "unit": "HOUR"},
    "fact_tool_usage": {"groups": "QD", "attrs": ["tooling", "production"], "unit": "EA"},
    "fact_wip_snapshot": {"groups": "QM", "attrs": ["production", "inventory"], "unit": "TON"},
    "fact_legacy_material_issue": {"groups": "QM", "attrs": ["material_use", "inventory"], "unit": "TON"},
    "fact_inspection_lot": {"groups": "QS", "attrs": ["quality", "inventory"], "unit": "TON"},
    "fact_inspection_result": {"groups": "QS", "attrs": ["quality"], "unit": "EA", "line_of": ("inspection_lot_id", "fact_inspection_lot")},
    "fact_nonconformance": {"groups": "QM", "attrs": ["quality_case", "quality"], "unit": "TON"},
    "fact_corrective_action": {"groups": "DMS", "attrs": ["quality_case"]},
    "fact_customer_complaint": {"groups": "QM", "attrs": ["complaint", "quality_case"], "unit": "TON"},
    "fact_supplier_quality_issue": {"groups": "QMS", "attrs": ["quality_case", "claim"], "unit": "TON"},
    "fact_gauge_calibration": {"groups": "DS", "attrs": ["calibration"]},
    "fact_equipment_event": {"groups": "DS", "attrs": ["equipment", "maintenance"]},
    "fact_predictive_alert": {"groups": "S", "attrs": ["sensor", "equipment"]},
    "fact_condition_monitoring": {"groups": "S", "attrs": ["sensor", "equipment"]},
    "fact_spare_part_issue": {"groups": "QM", "attrs": ["spare", "maintenance"], "unit": "EA"},
    "fact_maintenance_task": {"groups": "MD", "attrs": ["maintenance", "equipment"]},
    "fact_energy_meter_reading": {"groups": "QM", "attrs": ["meter"], "unit": "BY_ENERGY"},
    "fact_gl_entry": {"groups": "M", "attrs": ["finance"]},
    "fact_gl_entry_line": {"groups": "M", "attrs": ["finance"], "line_of": ("gl_entry_id", "fact_gl_entry")},
    "fact_cost_allocation": {"groups": "MS", "attrs": ["finance"]},
    "fact_cash_flow": {"groups": "M", "attrs": ["finance"]},
    "fact_tax_invoice_line": {"groups": "M", "attrs": ["finance"], "line_of": ("invoice_id", "fact_invoice")},
    "fact_fixed_asset_transfer": {"groups": "M", "attrs": ["asset_transfer", "finance"]},
    "fact_capex_project_cost": {"groups": "M", "attrs": ["capex", "finance"]},
    "fact_environment_measurement": {"groups": "S", "attrs": ["environment"]},
    "fact_emission_event": {"groups": "QMD", "attrs": ["environment", "safety"], "unit": "KG"},
    "fact_hazard_observation": {"groups": "S", "attrs": ["safety"]},
    "fact_safety_action": {"groups": "MDS", "attrs": ["safety"]},
    "fact_training_attendance": {"groups": "DS", "attrs": ["training"]},
    "fact_employee_assignment": {"groups": "S", "attrs": ["hr_record"]},
    "fact_attendance": {"groups": "D", "attrs": ["hr"]},
    "fact_overtime": {"groups": "MD", "attrs": ["hr"]},
    "fact_skill_assessment": {"groups": "S", "attrs": ["hr_record"]},
    "fact_certification_record": {"groups": "M", "attrs": ["hr_record"]},
    "fact_demand_plan": {"groups": "QS", "attrs": ["planning"], "unit": "TON"},
    "fact_supply_plan": {"groups": "QS", "attrs": ["planning"], "unit": "TON"},
    "fact_capacity_plan": {"groups": "QDS", "attrs": ["planning"], "unit": "HOUR"},
    "fact_kpi_snapshot": {"groups": "S", "attrs": ["kpi"]},
    "fact_data_quality_issue": {"groups": "", "attrs": ["governance"]},
    "fact_validation_case_result": {"groups": "", "attrs": ["governance"]},
}

# 有明细的表头:表头的数量与金额由明细汇总
HEADER_OF = {sem["line_of"][1]: (t, sem["line_of"][0]) for t, sem in FACT_SEMANTICS.items() if sem.get("line_of")}


def fact_columns(table: str) -> list[tuple[str, str]]:
    sem = FACT_SEMANTICS[table]
    cols = list(FACT_CORE)
    if sem.get("line_of"):
        cols[1:1] = LINE_NO
    g = sem["groups"]
    if "Q" in g:
        cols += FACT_QTY
    if "M" in g:
        cols += FACT_MONEY
    if "D" in g:
        cols += FACT_DURATION
    if "S" in g:
        cols += FACT_SCORE
    if not sem.get("upstream"):
        cols = [c for c in cols if c[0] != "source_document_no"]   # 没有上游单据的表不设来源单据号
    seen = {c for c, _ in cols}
    for grp in sem["attrs"]:
        for c, t in ATTR_GROUPS[grp]:
            if c not in seen:
                cols.append((c, t))
                seen.add(c)
    drop = set(sem.get("drop", ()))
    return [c for c in cols if c[0] not in drop]


# ───────────────────────── 新维表的属性 ─────────────────────────
DIM_COMMON: list[tuple[str, str]] = [
    ("code", "TEXT NOT NULL UNIQUE"), ("name", "TEXT NOT NULL"), ("name_en", "TEXT"), ("short_name", "TEXT"),
    ("description", "TEXT NOT NULL"),
    ("status", "TEXT NOT NULL CHECK(status IN ('ACTIVE','INACTIVE','DRAFT','CLOSED'))"),
    ("parent_code", "TEXT"), ("category_code", "TEXT"), ("type_code", "TEXT"),
    ("hierarchy_level", "INTEGER NOT NULL CHECK(hierarchy_level >= 0)"), ("sort_order", "INTEGER NOT NULL CHECK(sort_order >= 0)"),
    ("effective_from", "DATE NOT NULL"), ("effective_to", "DATE NOT NULL"), ("external_code", "TEXT"), ("legacy_code", "TEXT"),
    ("source_system", "TEXT NOT NULL"), ("source_record_id", "TEXT NOT NULL"), ("tenant_code", "TEXT NOT NULL"),
    ("approval_status", "TEXT NOT NULL"), ("approved_at", "DATETIME"), ("created_at", "DATETIME NOT NULL"),
    ("updated_at", "DATETIME NOT NULL"), ("created_by", "TEXT NOT NULL"), ("updated_by", "TEXT NOT NULL"),
    ("version_no", "INTEGER NOT NULL CHECK(version_no >= 1)"),
    ("data_quality_score", "REAL NOT NULL CHECK(data_quality_score BETWEEN 0 AND 1)"),
    ("is_deleted", "INTEGER NOT NULL CHECK(is_deleted IN (0,1))"), ("remarks", "TEXT"),
]

DIM_ATTRS: dict[str, list[tuple[str, str]]] = {
    "dim_calendar": [("calendar_date", "DATE"), ("calendar_year", "INTEGER"), ("calendar_quarter", "INTEGER"), ("calendar_month", "INTEGER"),
                     ("calendar_week", "INTEGER"), ("day_of_month", "INTEGER"), ("day_of_week", "INTEGER"), ("is_workday", "INTEGER"),
                     ("is_holiday", "INTEGER"), ("holiday_name", "TEXT"), ("fiscal_year", "INTEGER"), ("fiscal_period", "TEXT"),
                     ("week_start_date", "DATE"), ("month_end_flag", "INTEGER")],
    "dim_shift": [("shift_start", "TEXT"), ("shift_end", "TEXT"), ("break_minutes", "INTEGER"), ("paid_hours", "REAL"),
                  ("crosses_midnight", "INTEGER"), ("timezone_name", "TEXT"), ("night_allowance_rate", "REAL"), ("team_size", "INTEGER")],
    "dim_currency": [("symbol", "TEXT"), ("numeric_code", "TEXT"), ("decimal_places", "INTEGER"), ("country_code", "TEXT"),
                     ("rate_to_cny", "REAL"), ("rate_source", "TEXT"), ("is_functional", "INTEGER")],
    "dim_unit_of_measure": [("dimension_type", "TEXT"), ("base_unit_code", "TEXT"), ("conversion_factor", "REAL"), ("symbol", "TEXT"),
                            ("decimal_places", "INTEGER"), ("allow_fraction", "INTEGER"), ("reference_standard", "TEXT")],
    "dim_tax_code": [("tax_rate", "REAL"), ("tax_type", "TEXT"), ("invoice_type", "TEXT"), ("deductible_flag", "INTEGER"),
                     ("legal_basis", "TEXT"), ("country_code", "TEXT"), ("accounting_scope", "TEXT")],
    "dim_payment_term": [("net_days", "INTEGER"), ("discount_days", "INTEGER"), ("discount_rate", "REAL"), ("payment_method", "TEXT"),
                         ("settlement_method", "TEXT"), ("installment_count", "INTEGER"), ("applies_to", "TEXT")],
    "dim_incoterm": [("incoterm_version", "TEXT"), ("risk_transfer_point", "TEXT"), ("freight_paid_by", "TEXT"),
                     ("insurance_paid_by", "TEXT"), ("export_clearance_by", "TEXT"), ("import_clearance_by", "TEXT"), ("transport_mode", "TEXT")],
    "dim_reason_code": [("reason_group", "TEXT"), ("severity_level", "INTEGER"), ("requires_approval", "INTEGER"),
                        ("allow_manual_entry", "INTEGER"), ("usage_scope", "TEXT"), ("abbreviation", "TEXT"), ("display_value", "TEXT")],
    "dim_quality_characteristic": [("characteristic_type", "TEXT"), ("measurement_method", "TEXT"), ("nominal_value", "REAL"),
                                   ("lower_spec_limit", "REAL"), ("upper_spec_limit", "REAL"), ("unit_symbol", "TEXT"),
                                   ("precision_scale", "INTEGER"), ("sample_frequency", "INTEGER"), ("inspection_level", "TEXT"),
                                   ("criticality", "TEXT"), ("calibration_required", "INTEGER"), ("control_plan_code", "TEXT")],
    "dim_inspection_method": [("method_type", "TEXT"), ("instrument_type", "TEXT"), ("reference_standard", "TEXT"),
                              ("sample_size", "INTEGER"), ("duration_minutes", "INTEGER"), ("destructive_flag", "INTEGER"),
                              ("calibration_required", "INTEGER"), ("acceptance_rule", "TEXT")],
    "dim_customer_segment": [("segment_level", "INTEGER"), ("customer_size", "TEXT"), ("annual_potential", "REAL"),
                             ("target_margin", "REAL"), ("service_level", "TEXT"), ("credit_policy", "TEXT"), ("account_strategy", "TEXT")],
    "dim_sales_channel": [("channel_type", "TEXT"), ("commission_rate", "REAL"), ("responsible_role", "TEXT"),
                          ("service_level", "TEXT"), ("online_flag", "INTEGER"), ("market_scope", "TEXT")],
    "dim_market_segment": [("industry_code", "TEXT"), ("market_scope", "TEXT"), ("sales_region", "TEXT"), ("annual_potential", "REAL"),
                           ("growth_target", "REAL"), ("price_tier", "TEXT"), ("competitor_count", "INTEGER")],
    "dim_supplier_site": [("site_type", "TEXT"), ("country_code", "TEXT"), ("province", "TEXT"), ("city", "TEXT"),
                          ("lead_time_days", "INTEGER"), ("minimum_order_qty", "REAL"), ("delivery_rating", "REAL"),
                          ("quality_rating", "REAL"), ("capacity_rating", "REAL"), ("approved_scope", "TEXT")],
    "dim_contract_type": [("default_term_days", "INTEGER"), ("legal_template_no", "TEXT"), ("approval_level", "TEXT"),
                          ("requires_legal_review", "INTEGER"), ("renewal_type", "TEXT"), ("applies_to", "TEXT")],
    "dim_project": [("project_type", "TEXT"), ("investment_class", "TEXT"), ("planned_start_date", "DATE"), ("planned_end_date", "DATE"),
                    ("budget_ceiling", "REAL"), ("project_stage", "TEXT"), ("funding_source", "TEXT"), ("approval_level", "TEXT"),
                    ("cost_object_code", "TEXT")],
    "dim_asset_class": [("asset_category", "TEXT"), ("depreciation_method", "TEXT"), ("useful_life_years", "INTEGER"),
                        ("residual_rate", "REAL"), ("capitalization_threshold", "REAL"), ("gl_account_code", "TEXT")],
    "dim_energy_type": [("medium_type", "TEXT"), ("measurement_unit", "TEXT"), ("standard_coal_factor", "REAL"),
                        ("emission_factor", "REAL"), ("scope_category", "TEXT"), ("reporting_standard", "TEXT"),
                        ("meter_type", "TEXT"), ("renewable_flag", "INTEGER")],
    "dim_emission_type": [("pollutant_category", "TEXT"), ("measurement_unit", "TEXT"), ("regulatory_limit", "REAL"),
                          ("monitoring_frequency", "TEXT"), ("reporting_standard", "TEXT"), ("calculation_method", "TEXT"),
                          ("medium_type", "TEXT")],
    "dim_skill": [("skill_family", "TEXT"), ("proficiency_levels", "INTEGER"), ("assessment_method", "TEXT"),
                  ("training_hours", "REAL"), ("critical_skill", "INTEGER"), ("renewal_months", "INTEGER")],
    "dim_certification": [("certificate_type", "TEXT"), ("issuing_authority", "TEXT"), ("validity_months", "INTEGER"),
                          ("renewal_required", "INTEGER"), ("mandatory_for_role", "TEXT"), ("exam_fee", "REAL")],
    "dim_job_position": [("job_family", "TEXT"), ("job_grade", "TEXT"), ("minimum_experience_years", "INTEGER"),
                         ("education_requirement", "TEXT"), ("critical_position", "INTEGER"), ("headcount_plan", "INTEGER"),
                         ("replacement_lead_days", "INTEGER")],
    "dim_process_parameter": [("parameter_group", "TEXT"), ("parameter_unit", "TEXT"), ("nominal_value", "REAL"),
                              ("lower_control_limit", "REAL"), ("upper_control_limit", "REAL"), ("lower_spec_limit", "REAL"),
                              ("upper_spec_limit", "REAL"), ("sampling_interval_seconds", "INTEGER"), ("collection_method", "TEXT"),
                              ("control_method", "TEXT"), ("reaction_plan", "TEXT"), ("process_stage", "TEXT")],
    "dim_dataset_release": [("release_name", "TEXT"), ("generator_version", "TEXT"), ("random_seed", "INTEGER"), ("base_sha256", "TEXT"),
                            ("table_count", "INTEGER"), ("column_count", "INTEGER"), ("release_date", "DATE"), ("owner_team", "TEXT")],
    "dim_validation_case": [("case_type", "TEXT"), ("target_table", "TEXT"), ("target_column", "TEXT"), ("expected_behavior", "TEXT"),
                            ("is_intentional", "INTEGER"), ("detection_rule", "TEXT"), ("expected_count", "INTEGER"),
                            ("tolerance_value", "REAL"), ("owner_team", "TEXT"), ("remediation_hint", "TEXT"), ("origin", "TEXT")],
}


def pick(rng: random.Random, seq: list[Any]) -> Any:
    return seq[rng.randrange(len(seq))]


def allocate_lines(n_lines: int, headers: list[Any], rng: random.Random, max_per_header: int = 6) -> list[Any]:
    """把 n_lines 条明细分给表头:每个表头至少 1 条(明细不够时只用部分表头),其余随机分,按表头顺序排列。"""
    hs = list(headers)
    rng.shuffle(hs)
    hs = sorted(hs[: min(len(hs), n_lines)])
    max_per_header = max(max_per_header, -(-n_lines // max(1, len(hs))) + 2)
    counts = {h: 1 for h in hs}
    rest = n_lines - len(hs)
    while rest > 0:
        h = pick(rng, hs)
        if counts[h] < max_per_header:
            counts[h] += 1
            rest -= 1
    return [h for h in hs for _ in range(counts[h])]


__all__ = [n for n in dir() if not n.startswith("_")] + ["Callable"]


# 每张表不适用的属性(恒为空或与表含义无关)与有上游单据的表
_DROP = {
    "fact_shift_report": ["order_no", "operation_seq", "routing_version", "employee_no", "batch_no", "setup_minutes"],
    "fact_scrap_event": ["operation_seq", "routing_version", "setup_minutes", "run_minutes", "cycle_time_seconds"],
    "fact_rework_order": ["operation_seq", "routing_version", "cycle_time_seconds"],
    "fact_process_parameter_record": ["operation_seq", "routing_version", "yield_rate", "scrap_quantity", "setup_minutes",
                                      "run_minutes", "cycle_time_seconds", "sample_size"],
    "fact_labor_booking": ["operation_seq", "routing_version", "cycle_time_seconds", "absence_type", "yield_rate"],
    "fact_tool_usage": ["operation_seq", "routing_version", "setup_minutes", "scrap_quantity", "yield_rate"],
    "fact_wip_snapshot": ["operation_seq", "routing_version", "setup_minutes", "run_minutes", "cycle_time_seconds", "start_time", "end_time"],
    "fact_overtime": ["regular_hours", "clock_in", "clock_out", "absence_type"],
    "fact_condition_monitoring": ["alarm_code"],
    "fact_spare_part_issue": ["labor_hours", "start_time", "end_time", "failure_mode"],
    "fact_skill_assessment": ["position_code", "certificate_no"],
    "fact_certification_record": ["position_code"],
    "fact_employee_assignment": ["certificate_no", "skill_level", "assessment_score"],
    "fact_gauge_calibration": ["certificate_no_"],
    "fact_inspection_lot": ["defect_code_"],
    "fact_maintenance_task": ["downtime_minutes_"],
}
_UPSTREAM = {"fact_scrap_event", "fact_inspection_lot", "fact_work_order_operation", "fact_work_order_material", "fact_lot_trace",
             "fact_wip_snapshot", "fact_tool_usage", "fact_rework_order", "fact_labor_booking", "fact_process_parameter_record",
             "fact_legacy_material_issue", "fact_advance_shipping_notice", "fact_sales_order_line", "fact_customer_complaint",
             "fact_shipment", "fact_maintenance_task", "fact_sales_contract_line", "fact_supplier_claim", "fact_supplier_quality_issue",
             "fact_shipment_line", "fact_asn_line", "fact_inventory_movement", "fact_spare_part_issue", "fact_supplier_contract",
             "fact_supplier_contract_line", "fact_transfer_order", "fact_transfer_order_line"}
# 没有业务含义的通用列:非税单据的税率、计划表中的产能列、无预算可比的单据的预算金额
for _t in ("fact_gl_entry", "fact_gl_entry_line", "fact_cash_flow", "fact_cost_allocation", "fact_fixed_asset_transfer"):
    _DROP.setdefault(_t, []).append("tax_rate")
for _t in ("fact_demand_plan", "fact_supply_plan"):
    _DROP.setdefault(_t, []).extend(["capacity_hours", "utilization_rate"])
_DROP.setdefault("fact_supply_plan", []).append("forecast_accuracy")
for _t in ("fact_gl_entry", "fact_gl_entry_line", "fact_cash_flow", "fact_tax_invoice_line", "fact_cost_allocation", "fact_fixed_asset_transfer",
           "fact_overtime", "fact_certification_record", "fact_corrective_action", "fact_safety_action", "fact_maintenance_task", "fact_lead",
           "fact_price_adjustment", "fact_customer_complaint", "fact_supplier_claim", "fact_supplier_quality_issue", "fact_nonconformance",
           "fact_emission_event", "fact_rework_order", "fact_scrap_event", "fact_energy_meter_reading"):
    _DROP.setdefault(_t, []).append("budget_amount")
# 按物料汇总(不对应单一批次)的表没有生产日期与失效日期;复核结论只在纠正措施等处置环节记录
for _t in ("fact_cycle_count_line", "fact_inventory_snapshot", "fact_transfer_order", "fact_transfer_order_line"):
    _DROP.setdefault(_t, []).extend(["manufacture_date", "expiry_date"])
for _t in ("fact_inspection_lot", "fact_inspection_result", "fact_nonconformance", "fact_process_parameter_record"):
    _DROP.setdefault(_t, []).append("verification_result")
_DROP.setdefault("fact_sales_contract_line", []).append("customer_po_no")
_DROP.setdefault("fact_scrap_event", []).extend(["containment_action", "due_date", "verification_result"])
for _t, _cols in _DROP.items():
    FACT_SEMANTICS[_t]["drop"] = _cols
for _t in _UPSTREAM:
    FACT_SEMANTICS[_t]["upstream"] = True
