#!/usr/bin/env python3
"""Build and validate the contract-aligned 200-table synthetic validation database.

The source database is opened read-only and never modified.  The destination is
built in a temporary file, validated, and atomically moved into place only when
all hard checks pass.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


GENERATOR_VERSION = "1.0.0"
DEFAULT_SEED = 20260828
TARGET_TABLES = 200
TARGET_COLUMNS = 5000


@dataclass(frozen=True)
class Ref:
    column: str
    parent_table: str
    parent_column: str
    nullable: bool = False
    declared: bool = True


@dataclass(frozen=True)
class ExtraColumn:
    name: str
    sql_type: str
    nullable: bool = True


@dataclass
class TableSpec:
    name: str
    cn_name: str
    domain: str
    kind: str
    profile: str
    rows: int
    refs: list[Ref] = field(default_factory=list)
    extras: list[ExtraColumn] = field(default_factory=list)

    @property
    def pk(self) -> str:
        stem = self.name.removeprefix("dim_").removeprefix("fact_")
        return f"{stem}_id"


DIM_COMMON: list[tuple[str, str]] = [
    ("code", "TEXT NOT NULL UNIQUE"),
    ("name", "TEXT NOT NULL"),
    ("name_en", "TEXT"),
    ("short_name", "TEXT"),
    ("description", "TEXT NOT NULL"),
    ("status", "TEXT NOT NULL CHECK(status IN ('ACTIVE','INACTIVE','DRAFT','CLOSED'))"),
    ("parent_code", "TEXT"),
    ("category_code", "TEXT"),
    ("type_code", "TEXT"),
    ("hierarchy_level", "INTEGER NOT NULL CHECK(hierarchy_level >= 0)"),
    ("sort_order", "INTEGER NOT NULL CHECK(sort_order >= 0)"),
    ("effective_from", "DATE NOT NULL"),
    ("effective_to", "DATE NOT NULL"),
    ("external_code", "TEXT"),
    ("legacy_code", "TEXT"),
    ("source_system", "TEXT NOT NULL"),
    ("source_record_id", "TEXT NOT NULL"),
    ("tenant_code", "TEXT NOT NULL"),
    ("approval_status", "TEXT NOT NULL"),
    ("approved_at", "DATETIME"),
    ("created_at", "DATETIME NOT NULL"),
    ("updated_at", "DATETIME NOT NULL"),
    ("created_by", "TEXT NOT NULL"),
    ("updated_by", "TEXT NOT NULL"),
    ("version_no", "INTEGER NOT NULL CHECK(version_no >= 1)"),
    ("data_quality_score", "REAL NOT NULL CHECK(data_quality_score BETWEEN 0 AND 1)"),
    ("is_deleted", "INTEGER NOT NULL CHECK(is_deleted IN (0,1))"),
    ("remarks", "TEXT"),
]


FACT_COMMON: list[tuple[str, str]] = [
    ("document_no", "TEXT NOT NULL UNIQUE"),
    ("line_no", "INTEGER NOT NULL CHECK(line_no >= 1)"),
    ("business_date", "DATE NOT NULL"),
    ("event_time", "DATETIME NOT NULL"),
    ("posting_date", "DATE NOT NULL"),
    ("status", "TEXT NOT NULL CHECK(status IN ('OPEN','IN_PROGRESS','APPROVED','CLOSED','CANCELLED'))"),
    ("priority", "INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 5)"),
    ("currency_code", "TEXT NOT NULL"),
    ("unit_code", "TEXT NOT NULL"),
    ("quantity", "REAL NOT NULL CHECK(quantity >= 0)"),
    ("base_quantity", "REAL NOT NULL CHECK(base_quantity >= 0)"),
    ("planned_quantity", "REAL NOT NULL CHECK(planned_quantity >= 0)"),
    ("actual_quantity", "REAL NOT NULL CHECK(actual_quantity >= 0)"),
    ("qualified_quantity", "REAL NOT NULL CHECK(qualified_quantity >= 0)"),
    ("rejected_quantity", "REAL NOT NULL CHECK(rejected_quantity >= 0)"),
    ("unit_price", "REAL NOT NULL CHECK(unit_price >= 0)"),
    ("amount", "REAL NOT NULL CHECK(amount >= 0)"),
    ("tax_amount", "REAL NOT NULL CHECK(tax_amount >= 0)"),
    ("cost_amount", "REAL NOT NULL CHECK(cost_amount >= 0)"),
    ("budget_amount", "REAL NOT NULL CHECK(budget_amount >= 0)"),
    ("duration_minutes", "REAL NOT NULL CHECK(duration_minutes >= 0)"),
    ("rate_value", "REAL NOT NULL CHECK(rate_value BETWEEN 0 AND 1)"),
    ("score_value", "REAL NOT NULL CHECK(score_value BETWEEN 0 AND 100)"),
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
    ("row_version", "INTEGER NOT NULL CHECK(row_version >= 1)"),
    ("data_quality_score", "REAL NOT NULL CHECK(data_quality_score BETWEEN 0 AND 1)"),
    ("is_deleted", "INTEGER NOT NULL CHECK(is_deleted IN (0,1))"),
    ("remarks", "TEXT"),
]


DIM_PROFILES: dict[str, list[tuple[str, str]]] = {
    "time": [
        ("calendar_date", "DATE"), ("calendar_year", "INTEGER"), ("calendar_quarter", "INTEGER"),
        ("calendar_month", "INTEGER"), ("calendar_week", "INTEGER"), ("day_of_month", "INTEGER"),
        ("day_of_week", "INTEGER"), ("is_workday", "INTEGER"), ("is_holiday", "INTEGER"),
        ("shift_start", "TEXT"), ("shift_end", "TEXT"), ("break_minutes", "INTEGER"),
        ("timezone_name", "TEXT"), ("fiscal_period", "TEXT"),
    ],
    "finance": [
        ("symbol", "TEXT"), ("numeric_code", "TEXT"), ("decimal_places", "INTEGER"),
        ("tax_rate", "REAL"), ("settlement_days", "INTEGER"), ("credit_days", "INTEGER"),
        ("discount_rate", "REAL"), ("exchange_rate_basis", "TEXT"), ("country_code", "TEXT"),
        ("invoice_type", "TEXT"), ("payment_method", "TEXT"), ("settlement_method", "TEXT"),
        ("accounting_scope", "TEXT"), ("legal_basis", "TEXT"),
    ],
    "code": [
        ("canonical_value", "TEXT"), ("display_value", "TEXT"), ("abbreviation", "TEXT"),
        ("synonym_1", "TEXT"), ("synonym_2", "TEXT"), ("conversion_factor", "REAL"),
        ("base_unit_code", "TEXT"), ("validation_pattern", "TEXT"), ("value_domain", "TEXT"),
        ("allow_manual_entry", "INTEGER"), ("severity_level", "INTEGER"), ("usage_scope", "TEXT"),
        ("reference_standard", "TEXT"), ("semantic_version", "TEXT"),
    ],
    "quality": [
        ("characteristic_type", "TEXT"), ("measurement_method", "TEXT"), ("nominal_value", "REAL"),
        ("lower_spec_limit", "REAL"), ("upper_spec_limit", "REAL"), ("precision_scale", "INTEGER"),
        ("sample_frequency", "INTEGER"), ("sample_unit", "TEXT"), ("inspection_level", "TEXT"),
        ("acceptance_rule", "TEXT"), ("instrument_type", "TEXT"), ("calibration_required", "INTEGER"),
        ("control_plan_code", "TEXT"), ("criticality", "TEXT"),
    ],
    "market": [
        ("segment_level", "INTEGER"), ("channel_type", "TEXT"), ("market_scope", "TEXT"),
        ("customer_size", "TEXT"), ("industry_code", "TEXT"), ("sales_region", "TEXT"),
        ("price_tier", "TEXT"), ("service_level", "TEXT"), ("credit_policy", "TEXT"),
        ("target_margin", "REAL"), ("annual_potential", "REAL"), ("growth_target", "REAL"),
        ("account_strategy", "TEXT"), ("responsible_role", "TEXT"),
    ],
    "supplier": [
        ("site_code", "TEXT"), ("country_code", "TEXT"), ("province", "TEXT"), ("city", "TEXT"),
        ("address_line", "TEXT"), ("postal_code", "TEXT"), ("contact_name", "TEXT"),
        ("contact_phone", "TEXT"), ("lead_time_days", "INTEGER"), ("minimum_order_qty", "REAL"),
        ("delivery_rating", "REAL"), ("quality_rating", "REAL"), ("capacity_rating", "REAL"),
        ("approved_scope", "TEXT"),
    ],
    "asset": [
        ("project_type", "TEXT"), ("asset_category", "TEXT"), ("investment_class", "TEXT"),
        ("planned_start_date", "DATE"), ("planned_end_date", "DATE"), ("budget_ceiling", "REAL"),
        ("depreciation_method", "TEXT"), ("useful_life_years", "INTEGER"), ("capitalization_rule", "TEXT"),
        ("responsible_department", "TEXT"), ("project_stage", "TEXT"), ("funding_source", "TEXT"),
        ("approval_level", "TEXT"), ("cost_object_code", "TEXT"),
    ],
    "sustainability": [
        ("medium_type", "TEXT"), ("measurement_unit", "TEXT"), ("conversion_factor", "REAL"),
        ("emission_factor", "REAL"), ("lower_threshold", "REAL"), ("upper_threshold", "REAL"),
        ("regulatory_limit", "REAL"), ("monitoring_frequency", "INTEGER"), ("scope_category", "TEXT"),
        ("reporting_standard", "TEXT"), ("calculation_method", "TEXT"), ("meter_type", "TEXT"),
        ("responsible_role", "TEXT"), ("evidence_requirement", "TEXT"),
    ],
    "hr": [
        ("skill_family", "TEXT"), ("proficiency_level", "INTEGER"), ("certificate_type", "TEXT"),
        ("issuing_authority", "TEXT"), ("validity_months", "INTEGER"), ("renewal_required", "INTEGER"),
        ("job_family", "TEXT"), ("job_grade", "TEXT"), ("minimum_experience_years", "INTEGER"),
        ("education_requirement", "TEXT"), ("training_hours", "REAL"), ("assessment_method", "TEXT"),
        ("critical_position", "INTEGER"), ("replacement_lead_days", "INTEGER"),
    ],
    "manufacturing": [
        ("parameter_group", "TEXT"), ("parameter_unit", "TEXT"), ("nominal_value", "REAL"),
        ("lower_control_limit", "REAL"), ("upper_control_limit", "REAL"), ("lower_spec_limit", "REAL"),
        ("upper_spec_limit", "REAL"), ("sampling_interval_seconds", "INTEGER"), ("collection_method", "TEXT"),
        ("control_method", "TEXT"), ("reaction_plan", "TEXT"), ("equipment_scope", "TEXT"),
        ("product_scope", "TEXT"), ("process_stage", "TEXT"),
    ],
    "governance": [
        ("release_name", "TEXT"), ("generator_version", "TEXT"), ("random_seed", "INTEGER"),
        ("base_sha256", "TEXT"), ("case_type", "TEXT"), ("target_table", "TEXT"),
        ("target_column", "TEXT"), ("expected_behavior", "TEXT"), ("is_intentional", "INTEGER"),
        ("detection_rule", "TEXT"), ("expected_count", "INTEGER"), ("tolerance_value", "REAL"),
        ("owner_team", "TEXT"), ("remediation_hint", "TEXT"),
    ],
}


FACT_PROFILES: dict[str, list[tuple[str, str]]] = {
    "sales": [
        ("lead_source", "TEXT"), ("opportunity_stage", "TEXT"), ("probability", "REAL"),
        ("expected_close_date", "DATE"), ("customer_po_no", "TEXT"), ("contract_no", "TEXT"),
        ("requested_delivery_date", "DATE"), ("promised_delivery_date", "DATE"),
        ("sales_region", "TEXT"), ("campaign_code", "TEXT"), ("forecast_bucket", "TEXT"),
        ("delivery_terms", "TEXT"), ("price_list_code", "TEXT"), ("margin_rate", "REAL"),
    ],
    "procurement": [
        ("requisition_no", "TEXT"), ("quotation_no", "TEXT"), ("buyer_code", "TEXT"),
        ("supplier_quote_no", "TEXT"), ("requested_date", "DATE"), ("promised_date", "DATE"),
        ("received_date", "DATE"), ("payment_term_code", "TEXT"), ("incoterm_code", "TEXT"),
        ("sourcing_method", "TEXT"), ("lead_time_days", "INTEGER"), ("quality_agreement_no", "TEXT"),
        ("claim_type", "TEXT"), ("claim_resolution", "TEXT"),
    ],
    "inventory": [
        ("lot_no", "TEXT"), ("batch_no", "TEXT"), ("serial_no", "TEXT"),
        ("movement_type", "TEXT"), ("bin_code", "TEXT"), ("stock_status", "TEXT"),
        ("reservation_no", "TEXT"), ("pallet_no", "TEXT"), ("container_no", "TEXT"),
        ("expiry_date", "DATE"), ("manufacture_date", "DATE"), ("available_quantity", "REAL"),
        ("reserved_quantity", "REAL"), ("valuation_type", "TEXT"),
    ],
    "production": [
        ("order_no", "TEXT"), ("operation_seq", "INTEGER"), ("process_stage", "TEXT"),
        ("shift_code", "TEXT"), ("start_time", "DATETIME"), ("end_time", "DATETIME"),
        ("work_center_code", "TEXT"), ("batch_no", "TEXT"), ("routing_version", "TEXT"),
        ("setup_minutes", "REAL"), ("run_minutes", "REAL"), ("cycle_time_seconds", "REAL"),
        ("yield_rate", "REAL"), ("scrap_quantity", "REAL"),
    ],
    "quality": [
        ("inspection_no", "TEXT"), ("sample_size", "INTEGER"), ("characteristic_code", "TEXT"),
        ("measured_value", "REAL"), ("nominal_value", "REAL"), ("lower_limit", "REAL"),
        ("upper_limit", "REAL"), ("result_code", "TEXT"), ("defect_code", "TEXT"),
        ("severity", "INTEGER"), ("disposition", "TEXT"), ("root_cause_code", "TEXT"),
        ("containment_action", "TEXT"), ("verification_result", "TEXT"),
    ],
    "equipment": [
        ("event_code", "TEXT"), ("alarm_code", "TEXT"), ("sensor_code", "TEXT"),
        ("reading_value", "REAL"), ("reading_unit", "TEXT"), ("lower_threshold", "REAL"),
        ("upper_threshold", "REAL"), ("maintenance_type", "TEXT"), ("failure_mode", "TEXT"),
        ("downtime_minutes", "REAL"), ("meter_start", "REAL"), ("meter_end", "REAL"),
        ("health_index", "REAL"), ("remaining_life_hours", "REAL"),
    ],
    "finance": [
        ("voucher_no", "TEXT"), ("fiscal_year", "INTEGER"), ("fiscal_period", "INTEGER"),
        ("debit_amount", "REAL"), ("credit_amount", "REAL"), ("account_code", "TEXT"),
        ("cost_center_code", "TEXT"), ("profit_center_code", "TEXT"), ("exchange_rate", "REAL"),
        ("local_amount", "REAL"), ("tax_rate", "REAL"), ("due_date", "DATE"),
        ("clearing_document", "TEXT"), ("reference_document", "TEXT"),
    ],
    "safety": [
        ("observation_no", "TEXT"), ("hazard_type", "TEXT"), ("severity", "INTEGER"),
        ("likelihood", "INTEGER"), ("risk_score", "REAL"), ("control_measure", "TEXT"),
        ("permit_no", "TEXT"), ("location_code", "TEXT"), ("action_due_date", "DATE"),
        ("closed_date", "DATE"), ("emission_value", "REAL"), ("limit_value", "REAL"),
        ("regulatory_clause", "TEXT"), ("evidence_reference", "TEXT"),
    ],
    "hr": [
        ("employee_no", "TEXT"), ("work_date", "DATE"), ("shift_code", "TEXT"),
        ("clock_in", "TEXT"), ("clock_out", "TEXT"), ("regular_hours", "REAL"),
        ("overtime_hours", "REAL"), ("absence_type", "TEXT"), ("position_code", "TEXT"),
        ("skill_level", "INTEGER"), ("assessment_score", "REAL"), ("expiry_date", "DATE"),
        ("supervisor_code", "TEXT"), ("attendance_result", "TEXT"),
    ],
    "planning": [
        ("plan_version", "TEXT"), ("planning_period", "TEXT"), ("scenario_code", "TEXT"),
        ("demand_quantity", "REAL"), ("supply_quantity", "REAL"), ("capacity_hours", "REAL"),
        ("utilization_rate", "REAL"), ("backlog_quantity", "REAL"), ("safety_stock_quantity", "REAL"),
        ("forecast_accuracy", "REAL"), ("exception_code", "TEXT"), ("review_status", "TEXT"),
        ("planner_code", "TEXT"), ("freeze_horizon_days", "INTEGER"),
    ],
    "governance": [
        ("rule_code", "TEXT"), ("check_name", "TEXT"), ("severity", "INTEGER"),
        ("detected_at", "DATETIME"), ("resolved_at", "DATETIME"), ("expected_value", "TEXT"),
        ("actual_value", "TEXT"), ("exception_count", "INTEGER"), ("owner_team", "TEXT"),
        ("resolution_status", "TEXT"), ("generator_version", "TEXT"), ("random_seed", "INTEGER"),
        ("evidence_path", "TEXT"), ("validation_query", "TEXT"),
    ],
}


def R(column: str, table: str, parent_column: str, nullable: bool = False, declared: bool = True) -> Ref:
    return Ref(column, table, parent_column, nullable, declared)


def S(
    name: str,
    cn_name: str,
    domain: str,
    kind: str,
    profile: str,
    rows: int,
    refs: Iterable[Ref] = (),
    extras: Iterable[ExtraColumn] = (),
) -> TableSpec:
    return TableSpec(name, cn_name, domain, kind, profile, rows, list(refs), list(extras))


def table_specs() -> list[TableSpec]:
    """Return 92 new tables in referentially safe creation/seed order."""
    dims = [
        S("dim_calendar", "日历", "主数据", "dim", "time", 547),
        S("dim_shift", "生产班次", "主数据", "dim", "time", 3, [R("factory_id", "dim_factory", "factory_id")]),
        S("dim_currency", "币种", "财务", "dim", "finance", 4),
        S("dim_unit_of_measure", "计量单位", "主数据", "dim", "code", 8),
        S("dim_tax_code", "税码", "财务", "dim", "finance", 6),
        S("dim_payment_term", "付款条件", "财务", "dim", "finance", 8),
        S("dim_incoterm", "贸易术语", "采购", "dim", "finance", 8),
        S("dim_reason_code", "原因码", "主数据", "dim", "code", 12),
        S("dim_quality_characteristic", "质量特性", "质量", "dim", "quality", 24, [R("prod_id", "dim_product", "prod_id")]),
        S("dim_inspection_method", "检验方法", "质量", "dim", "quality", 12),
        S("dim_customer_segment", "客户分群", "销售", "dim", "market", 10),
        S("dim_sales_channel", "销售渠道", "销售", "dim", "market", 8),
        S("dim_market_segment", "市场细分", "销售", "dim", "market", 12),
        S("dim_supplier_site", "供应商地点", "采购", "dim", "supplier", 24, [R("supplier_id", "dim_supplier", "supplier_id")]),
        S("dim_contract_type", "合同类型", "法务", "dim", "finance", 8),
        S("dim_project", "项目", "项目", "dim", "asset", 20, [R("enterprise_id", "dim_enterprise", "enterprise_id"), R("owner_emp_id", "dim_employee", "emp_id")]),
        S("dim_asset_class", "资产类别", "资产", "dim", "asset", 12),
        S("dim_energy_type", "能源类型", "能源", "dim", "sustainability", 10),
        S("dim_emission_type", "排放物类型", "环境", "dim", "sustainability", 10),
        S("dim_skill", "岗位技能", "人力", "dim", "hr", 20),
        S("dim_certification", "资质证书", "人力", "dim", "hr", 16),
        S("dim_job_position", "岗位", "人力", "dim", "hr", 20, [R("dept_id", "dim_department", "dept_id")]),
        S("dim_process_parameter", "工艺参数", "生产", "dim", "manufacturing", 30, [R("type_id", "dim_equipment_type", "type_id")]),
        S("dim_dataset_release", "数据集版本", "治理", "dim", "governance", 1),
        S("dim_validation_case", "验证案例", "治理", "dim", "governance", 12, [R("dataset_release_id", "dim_dataset_release", "dataset_release_id")]),
    ]

    facts = [
        # Sales and CRM
        S("fact_lead", "销售线索", "销售", "fact", "sales", 240, [R("cust_id", "dim_customer", "cust_id"), R("emp_id", "dim_employee", "emp_id"), R("sales_channel_id", "dim_sales_channel", "sales_channel_id")]),
        S("fact_customer_contact", "客户接触记录", "销售", "fact", "sales", 320, [R("cust_id", "dim_customer", "cust_id"), R("emp_id", "dim_employee", "emp_id")]),
        S("fact_sales_contract_line", "销售合同明细", "销售", "fact", "sales", 300, [R("contract_id", "fact_contract", "contract_id"), R("prod_id", "dim_product", "prod_id")]),
        S("fact_sales_order_line", "销售订单明细", "销售", "fact", "sales", 480, [R("order_id", "fact_sales_order", "order_id"), R("prod_id", "dim_product", "prod_id")]),
        S("fact_shipment", "发运单", "销售", "fact", "sales", 220, [R("delivery_id", "fact_delivery", "delivery_id"), R("cust_id", "dim_customer", "cust_id"), R("warehouse_id", "dim_warehouse", "warehouse_id"), R("carrier_id", "dim_carrier", "carrier_id")]),
        S("fact_shipment_line", "发运明细", "销售", "fact", "sales", 440, [R("shipment_id", "fact_shipment", "shipment_id"), R("sales_order_line_id", "fact_sales_order_line", "sales_order_line_id"), R("prod_id", "dim_product", "prod_id")]),
        S("fact_sales_forecast", "销售预测", "销售", "fact", "sales", 240, [R("cust_id", "dim_customer", "cust_id"), R("prod_id", "dim_product", "prod_id"), R("market_segment_id", "dim_market_segment", "market_segment_id")]),
        S("fact_price_adjustment", "价格调整", "销售", "fact", "sales", 180, [R("cust_id", "dim_customer", "cust_id"), R("prod_id", "dim_product", "prod_id"), R("pricing_id", "dim_pricing", "pricing_id")]),
        # Procurement
        S("fact_supplier_contract", "供应商合同", "采购", "fact", "procurement", 140, [R("supplier_id", "dim_supplier", "supplier_id"), R("contract_type_id", "dim_contract_type", "contract_type_id")]),
        S("fact_supplier_contract_line", "供应商合同明细", "采购", "fact", "procurement", 300, [R("supplier_contract_id", "fact_supplier_contract", "supplier_contract_id"), R("material_id", "dim_material", "material_id")]),
        S("fact_advance_shipping_notice", "到货预告", "采购", "fact", "procurement", 180, [R("supplier_id", "dim_supplier", "supplier_id"), R("po_id", "fact_purchase_order", "po_id"), R("warehouse_id", "dim_warehouse", "warehouse_id")]),
        S("fact_asn_line", "到货预告明细", "采购", "fact", "procurement", 360, [R("advance_shipping_notice_id", "fact_advance_shipping_notice", "advance_shipping_notice_id"), R("material_id", "dim_material", "material_id"), R("po_line_id", "fact_po_line", "line_id")]),
        S("fact_supplier_claim", "供应商索赔", "采购", "fact", "procurement", 160, [R("supplier_id", "dim_supplier", "supplier_id"), R("material_id", "dim_material", "material_id")]),
        # Inventory and logistics
        S("fact_inventory_movement", "库存移动", "库存", "fact", "inventory", 420, [R("material_id", "dim_material", "material_id"), R("warehouse_id", "dim_warehouse", "warehouse_id"), R("location_id", "dim_storage_location", "location_id")]),
        S("fact_inventory_snapshot", "库存快照", "库存", "fact", "inventory", 300, [R("material_id", "dim_material", "material_id"), R("warehouse_id", "dim_warehouse", "warehouse_id")]),
        S("fact_cycle_count", "循环盘点单", "库存", "fact", "inventory", 120, [R("warehouse_id", "dim_warehouse", "warehouse_id"), R("emp_id", "dim_employee", "emp_id")]),
        S("fact_cycle_count_line", "循环盘点明细", "库存", "fact", "inventory", 300, [R("cycle_count_id", "fact_cycle_count", "cycle_count_id"), R("material_id", "dim_material", "material_id")]),
        S("fact_transfer_order", "库存调拨单", "库存", "fact", "inventory", 140, [R("source_warehouse_id", "dim_warehouse", "warehouse_id"), R("target_warehouse_id", "dim_warehouse", "warehouse_id")]),
        S("fact_transfer_order_line", "库存调拨明细", "库存", "fact", "inventory", 320, [R("transfer_order_id", "fact_transfer_order", "transfer_order_id"), R("material_id", "dim_material", "material_id")]),
        S("fact_lot_trace", "批次追溯", "库存", "fact", "inventory", 280, [R("material_id", "dim_material", "material_id"), R("order_id", "fact_production_order", "order_id")]),
        S("fact_batch_quality_hold", "批次质量冻结", "库存", "fact", "inventory", 120, [R("material_id", "dim_material", "material_id"), R("emp_id", "dim_employee", "emp_id")]),
        # Production
        S("fact_work_order_operation", "工单工序执行", "生产", "fact", "production", 420, [R("order_id", "fact_production_order", "order_id"), R("operation_id", "dim_operation", "operation_id"), R("line_id", "dim_production_line", "line_id")]),
        S("fact_work_order_material", "工单物料投用", "生产", "fact", "production", 420, [R("order_id", "fact_production_order", "order_id"), R("material_id", "dim_material", "material_id")]),
        S("fact_shift_report", "班次生产报告", "生产", "fact", "production", 260, [R("line_id", "dim_production_line", "line_id"), R("shift_id", "dim_shift", "shift_id")]),
        S("fact_scrap_event", "报废事件", "生产", "fact", "production", 180, [R("order_id", "fact_production_order", "order_id"), R("prod_id", "dim_product", "prod_id")]),
        S("fact_rework_order", "返工单", "生产", "fact", "production", 140, [R("order_id", "fact_production_order", "order_id"), R("emp_id", "dim_employee", "emp_id")]),
        S("fact_process_parameter_record", "工艺参数记录", "生产", "fact", "production", 480, [R("order_id", "fact_production_order", "order_id"), R("equipment_id", "dim_equipment", "equipment_id"), R("process_parameter_id", "dim_process_parameter", "process_parameter_id")]),
        S("fact_labor_booking", "生产工时报工", "生产", "fact", "production", 320, [R("order_id", "fact_production_order", "order_id"), R("emp_id", "dim_employee", "emp_id"), R("shift_id", "dim_shift", "shift_id")]),
        S("fact_tool_usage", "工装使用记录", "生产", "fact", "production", 260, [R("order_id", "fact_production_order", "order_id"), R("tool_id", "dim_tool", "tool_id")]),
        S("fact_wip_snapshot", "在制品快照", "生产", "fact", "production", 300, [R("order_id", "fact_production_order", "order_id"), R("line_id", "dim_production_line", "line_id")]),
        S("fact_legacy_material_issue", "遗留系统领料记录", "生产", "fact", "production", 180, [R("order_id", "fact_production_order", "order_id")], [ExtraColumn("mat_ref", "TEXT", False)]),
        # Quality
        S("fact_inspection_lot", "检验批", "质量", "fact", "quality", 220, [R("order_id", "fact_production_order", "order_id"), R("prod_id", "dim_product", "prod_id")]),
        S("fact_inspection_result", "检验结果", "质量", "fact", "quality", 440, [R("inspection_lot_id", "fact_inspection_lot", "inspection_lot_id"), R("quality_characteristic_id", "dim_quality_characteristic", "quality_characteristic_id"), R("inspection_method_id", "dim_inspection_method", "inspection_method_id")]),
        S("fact_nonconformance", "不合格记录", "质量", "fact", "quality", 160, [R("inspection_lot_id", "fact_inspection_lot", "inspection_lot_id"), R("reason_code_id", "dim_reason_code", "reason_code_id")]),
        S("fact_corrective_action", "纠正措施", "质量", "fact", "quality", 160, [R("nonconformance_id", "fact_nonconformance", "nonconformance_id"), R("emp_id", "dim_employee", "emp_id")]),
        S("fact_customer_complaint", "客户投诉", "质量", "fact", "quality", 140, [R("cust_id", "dim_customer", "cust_id"), R("prod_id", "dim_product", "prod_id"), R("order_id", "fact_sales_order", "order_id")]),
        S("fact_supplier_quality_issue", "供应商质量问题", "质量", "fact", "quality", 150, [R("supplier_id", "dim_supplier", "supplier_id"), R("material_id", "dim_material", "material_id")]),
        S("fact_gauge_calibration", "量具校准", "质量", "fact", "quality", 120, [R("equipment_id", "dim_equipment", "equipment_id"), R("quality_characteristic_id", "dim_quality_characteristic", "quality_characteristic_id")]),
        # Equipment and energy
        S("fact_equipment_event", "设备事件", "设备", "fact", "equipment", 360, [R("equipment_id", "dim_equipment", "equipment_id"), R("reason_code_id", "dim_reason_code", "reason_code_id")]),
        S("fact_predictive_alert", "预测性维护告警", "设备", "fact", "equipment", 240, [R("equipment_id", "dim_equipment", "equipment_id"), R("sensor_id", "dim_sensor", "sensor_id")]),
        S("fact_condition_monitoring", "设备状态监测", "设备", "fact", "equipment", 480, [R("equipment_id", "dim_equipment", "equipment_id"), R("sensor_id", "dim_sensor", "sensor_id")]),
        S("fact_spare_part_issue", "备件领用", "设备", "fact", "equipment", 220, [R("equipment_id", "dim_equipment", "equipment_id"), R("part_id", "dim_spare_part", "part_id")]),
        S("fact_maintenance_task", "维修任务", "设备", "fact", "equipment", 220, [R("wo_id", "fact_maintenance_wo", "wo_id"), R("emp_id", "dim_employee", "emp_id")]),
        S("fact_energy_meter_reading", "能源计量读数", "能源", "fact", "equipment", 360, [R("equipment_id", "dim_equipment", "equipment_id"), R("energy_type_id", "dim_energy_type", "energy_type_id")]),
        # Finance
        S("fact_gl_entry", "总账凭证", "财务", "fact", "finance", 220, [R("account_id", "dim_account", "account_id"), R("cc_id", "dim_cost_center", "cc_id"), R("coa_id", "dim_coa", "coa_id")]),
        S("fact_gl_entry_line", "总账凭证明细", "财务", "fact", "finance", 440, [R("gl_entry_id", "fact_gl_entry", "gl_entry_id"), R("account_id", "dim_account", "account_id")]),
        S("fact_cost_allocation", "成本分摊", "财务", "fact", "finance", 260, [R("source_cc_id", "dim_cost_center", "cc_id"), R("target_pc_id", "dim_profit_center", "pc_id")]),
        S("fact_cash_flow", "现金流记录", "财务", "fact", "finance", 260, [R("account_id", "dim_account", "account_id"), R("cust_id", "dim_customer", "cust_id", nullable=True), R("supplier_id", "dim_supplier", "supplier_id", nullable=True)]),
        S("fact_tax_invoice_line", "税务发票明细", "财务", "fact", "finance", 360, [R("invoice_id", "fact_invoice", "invoice_id"), R("tax_code_id", "dim_tax_code", "tax_code_id")]),
        S("fact_fixed_asset_transfer", "固定资产调拨", "资产", "fact", "finance", 140, [R("asset_id", "dim_fixed_asset", "asset_id"), R("source_factory_id", "dim_factory", "factory_id"), R("target_factory_id", "dim_factory", "factory_id")]),
        S("fact_capex_project_cost", "资本项目成本", "项目", "fact", "finance", 220, [R("project_id", "dim_project", "project_id"), R("asset_id", "dim_fixed_asset", "asset_id")]),
        # Safety and environment
        S("fact_environment_measurement", "环境监测记录", "环境", "fact", "safety", 300, [R("factory_id", "dim_factory", "factory_id"), R("emission_type_id", "dim_emission_type", "emission_type_id")]),
        S("fact_emission_event", "排放事件", "环境", "fact", "safety", 180, [R("factory_id", "dim_factory", "factory_id"), R("emission_type_id", "dim_emission_type", "emission_type_id")]),
        S("fact_hazard_observation", "危险源观察", "安全", "fact", "safety", 220, [R("hazard_id", "dim_hazard", "hazard_id"), R("emp_id", "dim_employee", "emp_id"), R("workshop_id", "dim_workshop", "workshop_id"), R("risk_level_id", "dim_risk_level", "level_id")]),
        S("fact_safety_action", "安全整改措施", "安全", "fact", "safety", 180, [R("hazard_id", "dim_hazard", "hazard_id"), R("emp_id", "dim_employee", "emp_id"), R("risk_level_id", "dim_risk_level", "level_id")]),
        S("fact_training_attendance", "安全培训签到", "安全", "fact", "safety", 260, [R("training_id", "fact_safety_training", "training_id"), R("emp_id", "dim_employee", "emp_id")]),
        # Human resources
        S("fact_employee_assignment", "员工任职记录", "人力", "fact", "hr", 180, [R("emp_id", "dim_employee", "emp_id"), R("dept_id", "dim_department", "dept_id"), R("job_position_id", "dim_job_position", "job_position_id")]),
        S("fact_attendance", "考勤记录", "人力", "fact", "hr", 360, [R("emp_id", "dim_employee", "emp_id"), R("shift_id", "dim_shift", "shift_id")]),
        S("fact_overtime", "加班记录", "人力", "fact", "hr", 220, [R("emp_id", "dim_employee", "emp_id"), R("dept_id", "dim_department", "dept_id")]),
        S("fact_skill_assessment", "技能评估", "人力", "fact", "hr", 220, [R("emp_id", "dim_employee", "emp_id"), R("skill_id", "dim_skill", "skill_id")]),
        S("fact_certification_record", "员工资质记录", "人力", "fact", "hr", 180, [R("emp_id", "dim_employee", "emp_id"), R("certification_id", "dim_certification", "certification_id")]),
        # Planning and governance
        S("fact_demand_plan", "需求计划", "计划", "fact", "planning", 240, [R("prod_id", "dim_product", "prod_id"), R("cust_id", "dim_customer", "cust_id")]),
        S("fact_supply_plan", "供应计划", "计划", "fact", "planning", 240, [R("prod_id", "dim_product", "prod_id"), R("factory_id", "dim_factory", "factory_id")]),
        S("fact_capacity_plan", "产能计划", "计划", "fact", "planning", 220, [R("line_id", "dim_production_line", "line_id"), R("shift_id", "dim_shift", "shift_id")]),
        S("fact_kpi_snapshot", "绩效指标快照", "绩效", "fact", "planning", 260, [R("emp_id", "dim_employee", "emp_id"), R("dept_id", "dim_department", "dept_id")]),
        S("fact_data_quality_issue", "数据质量问题", "治理", "fact", "governance", 160, [R("dataset_release_id", "dim_dataset_release", "dataset_release_id"), R("validation_case_id", "dim_validation_case", "validation_case_id")]),
        S("fact_validation_case_result", "验证案例执行结果", "治理", "fact", "governance", 120, [R("dataset_release_id", "dim_dataset_release", "dataset_release_id"), R("validation_case_id", "dim_validation_case", "validation_case_id")]),
    ]
    specs = dims + facts
    if len(specs) != 92:
        raise AssertionError(f"expected 92 new tables, got {len(specs)}")
    names = [s.name for s in specs]
    if len(set(names)) != len(names):
        raise AssertionError("duplicate new table names")
    return specs


def qident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def user_tables(con: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def table_columns(con: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return list(con.execute(f"PRAGMA table_info({qident(table)})"))


def parent_values(con: sqlite3.Connection, table: str, column: str) -> list[Any]:
    return [row[0] for row in con.execute(f"SELECT {qident(column)} FROM {qident(table)} WHERE {qident(column)} IS NOT NULL ORDER BY {qident(column)}")]


PARENT_DATE_CANDIDATES = (
    "business_date",
    "order_date",
    "po_date",
    "delivery_date",
    "invoice_date",
    "wo_date",
    "training_date",
    "start_date",
    "posting_date",
)


def parent_date_column(con: sqlite3.Connection, table: str) -> str | None:
    columns = {row[1] for row in table_columns(con, table)}
    return next((name for name in PARENT_DATE_CANDIDATES if name in columns), None)


def parent_dates(
    con: sqlite3.Connection,
    table: str,
    key_column: str,
) -> dict[Any, date]:
    date_column = parent_date_column(con, table)
    if date_column is None:
        return {}
    values: dict[Any, date] = {}
    for key, raw_date in con.execute(
        f"SELECT {qident(key_column)}, {qident(date_column)} FROM {qident(table)} "
        f"WHERE {qident(date_column)} IS NOT NULL"
    ):
        try:
            values[key] = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            continue
    return values


def _choose_parent_by_date(
    con: sqlite3.Connection,
    parent_table: str,
    parent_key: str,
    parent_date: str,
    child_date_value: str,
    ordinal: int,
) -> Any:
    rows = list(
        con.execute(
            f"SELECT {qident(parent_key)}, {qident(parent_date)} FROM {qident(parent_table)} "
            f"WHERE date({qident(parent_date)}) <= date(?) ORDER BY date({qident(parent_date)}), {qident(parent_key)}",
            (child_date_value,),
        )
    )
    if not rows:
        rows = list(con.execute(f"SELECT {qident(parent_key)}, {qident(parent_date)} FROM {qident(parent_table)} ORDER BY {qident(parent_key)}"))
    return rows[ordinal % len(rows)][0]


def repair_base_foreign_keys(con: sqlite3.Connection) -> dict[str, int]:
    """Repair four known invalid FK chains in the copied synthetic base."""
    repairs: dict[str, int] = {}
    date_rules = [
        ("fact_return", "order_id", "return_date", "fact_sales_order", "order_id", "order_date"),
        ("fact_invoice", "delivery_id", "invoice_date", "fact_delivery", "delivery_id", "delivery_date"),
        ("fact_ar_aging", "invoice_id", "due_date", "fact_invoice", "invoice_id", "invoice_date"),
    ]
    for child, child_fk, child_date, parent, parent_pk, parent_date in date_rules:
        bad = list(
            con.execute(
                f"SELECT rowid, {qident(child_date)} FROM {qident(child)} WHERE {qident(child_fk)} IS NOT NULL "
                f"AND {qident(child_fk)} NOT IN (SELECT {qident(parent_pk)} FROM {qident(parent)}) ORDER BY rowid"
            )
        )
        for ordinal, (rowid, when) in enumerate(bad):
            new_key = _choose_parent_by_date(con, parent, parent_pk, parent_date, when, ordinal)
            con.execute(f"UPDATE {qident(child)} SET {qident(child_fk)}=? WHERE rowid=?", (new_key, rowid))
        repairs[f"{child}.{child_fk}->{parent}.{parent_pk}"] = len(bad)

    child, child_fk, parent, parent_pk = "fact_material_consumption", "order_id", "fact_production_order", "order_id"
    keys = parent_values(con, parent, parent_pk)
    bad = list(
        con.execute(
            f"SELECT rowid FROM {qident(child)} WHERE {qident(child_fk)} IS NOT NULL "
            f"AND {qident(child_fk)} NOT IN (SELECT {qident(parent_pk)} FROM {qident(parent)}) ORDER BY rowid"
        )
    )
    for ordinal, (rowid,) in enumerate(bad):
        con.execute(f"UPDATE {qident(child)} SET {qident(child_fk)}=? WHERE rowid=?", (keys[ordinal % len(keys)], rowid))
    repairs[f"{child}.{child_fk}->{parent}.{parent_pk}"] = len(bad)
    return repairs


def _tables_with_column(con: sqlite3.Connection, column: str) -> list[str]:
    return [
        table
        for table in user_tables(con)
        if column in {row[1] for row in table_columns(con, table)}
    ]


def deidentify_base_plaintext(con: sqlite3.Connection) -> dict[str, Any]:
    """Tokenize party, person and direct-identifier text in the copied base.

    The source database is synthetic, but it deliberately uses realistic public
    company and person names.  Contract D-1 disallows business plaintext, so the
    delivery copy replaces those realistic labels with stable synthetic tokens.
    Keys, dates, measures and business distributions are left unchanged.
    """
    families = {
        "emp_name": "合成员工",
        "cust_name": "合成客户",
        "supplier_name": "合成供应商",
        "carrier_name": "合成承运商",
        "enterprise_name": "合成企业",
        "contact_person": "合成联系人",
        "opp_name": "合成商机",
    }
    replacements: dict[str, str] = {}
    family_sizes: dict[str, int] = {}
    changes_before = con.total_changes
    for column, prefix in families.items():
        tables = _tables_with_column(con, column)
        values = sorted(
            {
                str(row[0])
                for table in tables
                for row in con.execute(
                    f"SELECT DISTINCT {qident(column)} FROM {qident(table)} "
                    f"WHERE {qident(column)} IS NOT NULL AND TRIM(CAST({qident(column)} AS TEXT))<>''"
                )
            }
        )
        mapping = {value: f"{prefix}{idx:04d}" for idx, value in enumerate(values, start=1)}
        replacements.update(mapping)
        family_sizes[column] = len(mapping)
        for table in tables:
            for old, new in mapping.items():
                con.execute(
                    f"UPDATE {qident(table)} SET {qident(column)}=? WHERE {qident(column)}=?",
                    (new, old),
                )

    direct_columns = {
        "contact_phone": "SYN-PHONE",
        "bank_account": "SYN-ACCOUNT",
        "credit_code": "SYN-CREDIT",
        "legal_rep": "合成负责人",
        "address": "合成地址",
    }
    direct_counts: dict[str, int] = {}
    for column, prefix in direct_columns.items():
        count = 0
        for table in _tables_with_column(con, column):
            rows = list(
                con.execute(
                    f"SELECT rowid FROM {qident(table)} "
                    f"WHERE {qident(column)} IS NOT NULL ORDER BY rowid"
                )
            )
            for ordinal, (rowid,) in enumerate(rows, start=1):
                con.execute(
                    f"UPDATE {qident(table)} SET {qident(column)}=? WHERE rowid=?",
                    (f"{prefix}-{table}-{ordinal:04d}", rowid),
                )
            count += len(rows)
        direct_counts[column] = count

    # Metadata descriptions and denormalized labels can contain the same names
    # inside longer text.  Replace those occurrences as well, longest first.
    text_columns = [
        (table, info[1])
        for table in user_tables(con)
        for info in table_columns(con, table)
        if any(token in (info[2] or "").upper() for token in ("TEXT", "CHAR", "CLOB"))
    ]
    for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        for table, column in text_columns:
            con.execute(
                f"UPDATE {qident(table)} SET {qident(column)}=REPLACE({qident(column)}, ?, ?) "
                f"WHERE INSTR({qident(column)}, ?)>0",
                (old, new, old),
            )
    return {
        "method": "deterministic_tokenization",
        "name_token_counts": family_sizes,
        "direct_identifier_counts": direct_counts,
        "updated_cells": con.total_changes - changes_before,
    }


def repair_base_temporal_rules(con: sqlite3.Connection) -> dict[str, int]:
    """Repair event-order violations after the copied base foreign keys are resolved."""
    rules = [
        (
            "fact_delivery",
            "delivery_date",
            "fact_sales_order",
            "order_id",
            "order_id",
            "order_date",
        ),
        (
            "fact_return",
            "return_date",
            "fact_sales_order",
            "order_id",
            "order_id",
            "order_date",
        ),
        (
            "fact_invoice",
            "invoice_date",
            "fact_delivery",
            "delivery_id",
            "delivery_id",
            "delivery_date",
        ),
        (
            "fact_ar_aging",
            "due_date",
            "fact_invoice",
            "invoice_id",
            "invoice_id",
            "invoice_date",
        ),
    ]
    repairs: dict[str, int] = {}
    for child, child_date, parent, child_fk, parent_pk, parent_date in rules:
        predicate = (
            f"EXISTS (SELECT 1 FROM {qident(parent)} parent "
            f"WHERE parent.{qident(parent_pk)}={qident(child)}.{qident(child_fk)} "
            f"AND date({qident(child)}.{qident(child_date)})<date(parent.{qident(parent_date)}))"
        )
        count = con.execute(
            f"SELECT COUNT(*) FROM {qident(child)} WHERE {predicate}"
        ).fetchone()[0]
        con.execute(
            f"UPDATE {qident(child)} SET {qident(child_date)}=("
            f"SELECT parent.{qident(parent_date)} FROM {qident(parent)} parent "
            f"WHERE parent.{qident(parent_pk)}={qident(child)}.{qident(child_fk)}) "
            f"WHERE {predicate}"
        )
        repairs[f"{child}.{child_date}>={parent}.{parent_date}"] = count
    return repairs


def seed_empty_base_tables(con: sqlite3.Connection) -> dict[str, int]:
    """Fill the one empty base fact table with explicitly marked synthetic records."""
    seeded: dict[str, int] = {}
    existing = con.execute("SELECT COUNT(*) FROM fact_product_cost").fetchone()[0]
    if existing:
        seeded["fact_product_cost"] = 0
        return seeded
    product_ids = parent_values(con, "dim_product", "prod_id")
    rows: list[tuple[Any, ...]] = []
    for idx in range(1, 181):
        material_cost = round(800 + (idx % 45) * 17.5, 2)
        labor_cost = round(180 + (idx % 20) * 6.25, 2)
        overhead_cost = round(120 + (idx % 16) * 4.75, 2)
        total_cost = round(material_cost + labor_cost + overhead_cost, 2)
        output_qty = round(8 + (idx % 28) * 0.75, 3)
        unit_cost = round(total_cost / output_qty, 4)
        period_date = date(2025, 1, 1) + timedelta(days=31 * ((idx - 1) % 18))
        rows.append(
            (
                idx,
                product_ids[(idx - 1) % len(product_ids)],
                f"SYN-COST-{idx:06d}",
                period_date.strftime("%Y-%m"),
                material_cost,
                labor_cost,
                overhead_cost,
                total_cost,
                output_qty,
                unit_cost,
            )
        )
    con.executemany(
        "INSERT INTO fact_product_cost("
        "cost_id,product_id,batch_no,period,material_cost,labor_cost,overhead_cost,"
        "total_cost,output_qty,unit_cost) VALUES(?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    seeded["fact_product_cost"] = len(rows)
    return seeded


def _column_defs(spec: TableSpec) -> list[tuple[str, str]]:
    columns: list[tuple[str, str]] = [(spec.pk, "INTEGER PRIMARY KEY")]
    for ref in spec.refs:
        null_sql = "" if ref.nullable else " NOT NULL"
        columns.append((ref.column, "INTEGER" + null_sql))
    common = DIM_COMMON if spec.kind == "dim" else FACT_COMMON
    profiles = DIM_PROFILES if spec.kind == "dim" else FACT_PROFILES
    columns.extend(common)
    columns.extend(profiles[spec.profile])
    for extra in spec.extras:
        columns.append((extra.name, extra.sql_type + ("" if extra.nullable else " NOT NULL")))
    seen: set[str] = set()
    duplicates: set[str] = set()
    for name, _ in columns:
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    if duplicates:
        raise AssertionError(f"{spec.name}: duplicate columns {sorted(duplicates)}")
    return columns


def create_table(con: sqlite3.Connection, spec: TableSpec) -> None:
    defs = [f"{qident(name)} {sql_type}" for name, sql_type in _column_defs(spec)]
    if spec.kind == "dim":
        defs.append("CHECK(date(effective_to) >= date(effective_from))")
    else:
        defs.extend(
            [
                "CHECK(date(posting_date) >= date(business_date))",
                "CHECK(datetime(updated_at) >= datetime(created_at))",
                "CHECK(qualified_quantity + rejected_quantity <= actual_quantity + 0.000001)",
            ]
        )
        defs.append("FOREIGN KEY(currency_code) REFERENCES dim_currency(code)")
        defs.append("FOREIGN KEY(unit_code) REFERENCES dim_unit_of_measure(code)")
        defs.append("FOREIGN KEY(reason_code) REFERENCES dim_reason_code(code)")
        defs.append("FOREIGN KEY(business_date) REFERENCES dim_calendar(calendar_date)")
        if spec.profile == "procurement":
            defs.append("FOREIGN KEY(payment_term_code) REFERENCES dim_payment_term(code)")
            defs.append("FOREIGN KEY(incoterm_code) REFERENCES dim_incoterm(code)")
    for ref in spec.refs:
        if ref.declared:
            defs.append(
                f"FOREIGN KEY({qident(ref.column)}) REFERENCES {qident(ref.parent_table)}({qident(ref.parent_column)}) "
                "ON UPDATE RESTRICT ON DELETE RESTRICT"
            )
    con.execute(f"CREATE TABLE {qident(spec.name)} (\n  " + ",\n  ".join(defs) + "\n)")
    if spec.name == "dim_calendar":
        con.execute("CREATE UNIQUE INDEX idx_dim_calendar_calendar_date ON dim_calendar(calendar_date)")
    for ref in spec.refs:
        con.execute(f"CREATE INDEX {qident('idx_' + spec.name + '_' + ref.column)} ON {qident(spec.name)}({qident(ref.column)})")
    if spec.kind == "fact":
        con.execute(f"CREATE INDEX {qident('idx_' + spec.name + '_business_date')} ON {qident(spec.name)}(business_date)")
        con.execute(f"CREATE INDEX {qident('idx_' + spec.name + '_status')} ON {qident(spec.name)}(status)")


def _iso_day(rng: random.Random, offset_max: int = 545) -> str:
    return (date(2025, 1, 1) + timedelta(days=rng.randrange(offset_max + 1))).isoformat()


def _generic_value(name: str, sql_type: str, row_no: int, spec: TableSpec, rng: random.Random) -> Any:
    lower = name.lower()
    if lower.endswith("_date") or lower in {"calendar_date", "due_date", "expiry_date", "manufacture_date", "work_date", "closed_date"}:
        return _iso_day(rng)
    if lower.endswith("_at") or lower.endswith("_time") or lower in {"detected_at", "resolved_at"}:
        return f"{_iso_day(rng)} {8 + row_no % 10:02d}:{row_no % 60:02d}:00"
    if lower.startswith("is_") or lower.endswith("_required") or lower in {"allow_manual_entry", "critical_position", "calibration_required", "renewal_required"}:
        return row_no % 2
    if any(token in lower for token in ("rate", "score", "probability", "factor", "accuracy", "margin", "utilization", "health_index")):
        return round(0.5 + (row_no % 45) / 100, 4)
    if any(token in lower for token in ("amount", "cost", "price", "value", "limit", "ceiling", "potential")):
        return round(100 + row_no * 3.17, 4)
    if any(token in lower for token in ("quantity", "hours", "minutes", "life_", "threshold")):
        return round(1 + (row_no % 80) * 0.75, 4)
    if "INTEGER" in sql_type.upper() or any(token in lower for token in ("count", "level", "year", "period", "days", "order", "seed", "size", "scale", "frequency")):
        return int(1 + row_no % 30)
    if "REAL" in sql_type.upper() or "DECIMAL" in sql_type.upper():
        return round(1 + row_no * 0.37, 4)
    return f"{spec.name}:{name}:{row_no:04d}"


def _special_dimension_codes(spec: TableSpec) -> list[str] | None:
    values = {
        "dim_shift": ["DAY", "SWING", "NIGHT"],
        "dim_currency": ["CNY", "USD", "EUR", "JPY"],
        "dim_unit_of_measure": ["EA", "KG", "TON", "HOUR", "KWH", "CNY", "PCT", "METER"],
        "dim_reason_code": ["QUALITY", "DELIVERY", "COST", "EQUIPMENT", "SAFETY", "ENVIRONMENT", "CUSTOMER", "SUPPLIER", "PROCESS", "MATERIAL", "SYSTEM", "OTHER"],
    }
    return values.get(spec.name)


def seed_dimension(
    con: sqlite3.Connection,
    spec: TableSpec,
    rng: random.Random,
    base_hash: str,
    seed: int,
) -> None:
    columns = [name for name, _ in _column_defs(spec)]
    parent_cache = {ref.column: parent_values(con, ref.parent_table, ref.parent_column) for ref in spec.refs}
    codes = _special_dimension_codes(spec)
    rows: list[list[Any]] = []
    start = date(2025, 1, 1)
    for idx in range(1, spec.rows + 1):
        record: dict[str, Any] = {spec.pk: idx}
        for ref in spec.refs:
            values = parent_cache[ref.column]
            record[ref.column] = values[(idx - 1) % len(values)] if values else None
        code = codes[idx - 1] if codes else f"{spec.name.removeprefix('dim_').upper()}-{idx:04d}"
        if spec.name == "dim_calendar":
            day = start + timedelta(days=idx - 1)
            code = day.strftime("%Y%m%d")
        record.update(
            {
                "code": code,
                "name": f"合成{spec.cn_name}{idx}",
                "name_en": f"Synthetic {spec.name} {idx}",
                "short_name": f"{spec.cn_name}{idx}",
                "description": f"合同评测用合成{spec.cn_name}主数据，第{idx}条。",
                "status": "ACTIVE",
                "parent_code": None,
                "category_code": spec.domain.upper(),
                "type_code": spec.profile.upper(),
                "hierarchy_level": 1,
                "sort_order": idx,
                "effective_from": "2025-01-01",
                "effective_to": "2099-12-31",
                "external_code": f"EXT-{code}",
                "legacy_code": f"LEG-{idx:04d}",
                "source_system": "SYNTHETIC_VALIDATION",
                "source_record_id": f"{spec.name}:{idx}",
                "tenant_code": "DEMO_TENANT",
                "approval_status": "APPROVED",
                "approved_at": "2026-08-28 09:00:00",
                "created_at": "2026-08-28 08:00:00",
                "updated_at": "2026-08-28 09:00:00",
                "created_by": "contract_data_generator",
                "updated_by": "contract_data_generator",
                "version_no": 1,
                "data_quality_score": 1.0,
                "is_deleted": 0,
                "remarks": "明确标记为合成数据，不对应真实个人或企业交易。",
            }
        )
        for name, sql_type in DIM_PROFILES[spec.profile]:
            record[name] = _generic_value(name, sql_type, idx, spec, rng)
        if spec.name == "dim_calendar":
            day = start + timedelta(days=idx - 1)
            record.update(
                {
                    "calendar_date": day.isoformat(),
                    "calendar_year": day.year,
                    "calendar_quarter": (day.month - 1) // 3 + 1,
                    "calendar_month": day.month,
                    "calendar_week": int(day.strftime("%V")),
                    "day_of_month": day.day,
                    "day_of_week": day.isoweekday(),
                    "is_workday": int(day.isoweekday() <= 5),
                    "is_holiday": int(day.isoweekday() > 5),
                    "fiscal_period": f"{day.year}-{day.month:02d}",
                }
            )
        elif spec.name == "dim_dataset_release":
            record.update(
                {
                    "release_name": "contract-validation-200-v1",
                    "generator_version": GENERATOR_VERSION,
                    "random_seed": seed,
                    "base_sha256": base_hash,
                    "case_type": "DATASET_RELEASE",
                    "target_table": "*",
                    "target_column": "*",
                    "expected_behavior": "200 tables, at least 5000 columns, deterministic synthetic data",
                    "is_intentional": 1,
                    "detection_rule": "validate_database",
                    "expected_count": TARGET_TABLES,
                    "tolerance_value": 0.0,
                    "owner_team": "data-governance",
                    "remediation_hint": "Regenerate from the same base hash and seed.",
                }
            )
        elif spec.name == "dim_validation_case":
            case_rows = [
                ("MISSING_DECLARED_FK", "fact_legacy_material_issue", "mat_ref", "Values resolve to dim_material.material_code while the schema intentionally omits the FK."),
                ("IRREGULAR_KEY_NAME", "fact_legacy_material_issue", "mat_ref", "Relationship discovery should detect an irregular material reference name."),
                ("LAYER_RECONCILIATION", "DWS_PRODUCTION_DAILY", "actual_quantity", "Layer totals are a documented challenge case and must not be silently treated as identical."),
                ("COMPOSITE_KEY", "fact_shipment_line", "shipment_id,line_no", "Line identity is represented by header plus line number."),
                ("DUAL_WAREHOUSE_ROLE", "fact_transfer_order", "source_warehouse_id,target_warehouse_id", "Both role-specific references must remain directionally distinct."),
                ("LOW_CARDINALITY_CODE", "dim_reason_code", "code", "Low-cardinality codes require semantic confirmation."),
                ("TEMPORAL_ORDER", "fact_shipment", "business_date,posting_date", "Posting date must not precede business date."),
                ("AMOUNT_RECONCILIATION", "fact_sales_order_line", "quantity,unit_price,amount", "Amount must equal quantity multiplied by unit price within rounding tolerance."),
                ("QUALITY_DECOMPOSITION", "fact_inspection_result", "actual_quantity,qualified_quantity,rejected_quantity", "Qualified and rejected quantities must not exceed actual quantity."),
                ("TRACEABILITY", "fact_lot_trace", "trace_id", "Every record must have a stable trace identifier."),
                ("METADATA_COMPLETENESS", "dim_column_metadata", "column_desc", "All physical columns must have metadata records."),
                ("REFERENCE_INTEGRITY", "*", "*", "All declared foreign keys must pass PRAGMA foreign_key_check."),
            ]
            case_type, target_table, target_column, behavior = case_rows[idx - 1]
            record.update(
                {
                    "release_name": "contract-validation-200-v1",
                    "generator_version": GENERATOR_VERSION,
                    "random_seed": seed,
                    "base_sha256": base_hash,
                    "case_type": case_type,
                    "target_table": target_table,
                    "target_column": target_column,
                    "expected_behavior": behavior,
                    "is_intentional": 1,
                    "detection_rule": case_type.lower(),
                    "expected_count": 0,
                    "tolerance_value": 0.0001,
                    "owner_team": "ontology-evaluation",
                    "remediation_hint": "Review the validation case manifest before changing the data.",
                }
            )
        rows.append([record.get(column) for column in columns])
    placeholders = ",".join("?" for _ in columns)
    con.executemany(
        f"INSERT INTO {qident(spec.name)} ({','.join(qident(c) for c in columns)}) VALUES ({placeholders})",
        rows,
    )


def seed_fact(con: sqlite3.Connection, spec: TableSpec, rng: random.Random, seed: int) -> None:
    defs = _column_defs(spec)
    columns = [name for name, _ in defs]
    parent_cache = {ref.column: parent_values(con, ref.parent_table, ref.parent_column) for ref in spec.refs}
    parent_date_cache = {
        ref.column: parent_dates(con, ref.parent_table, ref.parent_column)
        for ref in spec.refs
        if ref.parent_table.startswith("fact_")
    }
    currencies = parent_values(con, "dim_currency", "code")
    units = parent_values(con, "dim_unit_of_measure", "code")
    reasons = parent_values(con, "dim_reason_code", "code")
    payment_terms = parent_values(con, "dim_payment_term", "code") if spec.profile == "procurement" else []
    incoterms = parent_values(con, "dim_incoterm", "code") if spec.profile == "procurement" else []
    material_codes = parent_values(con, "dim_material", "material_code") if spec.name == "fact_legacy_material_issue" else []
    rows: list[list[Any]] = []
    for idx in range(1, spec.rows + 1):
        record: dict[str, Any] = {spec.pk: idx}
        for ref in spec.refs:
            values = parent_cache[ref.column]
            record[ref.column] = values[(idx * 7 + len(ref.column)) % len(values)] if values else None
        if spec.name == "fact_gl_entry_line":
            values = parent_cache["gl_entry_id"]
            record["gl_entry_id"] = values[((idx - 1) // 2) % len(values)]
        for source_column, target_column in (
            ("source_warehouse_id", "target_warehouse_id"),
            ("source_factory_id", "target_factory_id"),
        ):
            if source_column not in record or target_column not in record:
                continue
            if record[source_column] == record[target_column]:
                values = parent_cache[target_column]
                current = values.index(record[target_column])
                record[target_column] = values[(current + 1) % len(values)]
        if spec.name == "fact_cash_flow":
            if idx % 2:
                record["supplier_id"] = None
            else:
                record["cust_id"] = None

        measure_idx = (idx + 1) // 2 if spec.name == "fact_gl_entry_line" else idx
        business_day = date(2025, 1, 1) + timedelta(days=(measure_idx * 11 + len(spec.name)) % 546)
        referenced_dates = [
            dates[record[column]]
            for column, dates in parent_date_cache.items()
            if record.get(column) in dates
        ]
        if referenced_dates:
            business_day = max(business_day, max(referenced_dates))
        posting_day = business_day + timedelta(days=idx % 4)
        event_time = datetime.combine(business_day, datetime.min.time()) + timedelta(hours=8 + idx % 10, minutes=idx % 60)
        quantity = round(1.0 + (measure_idx % 40) * 0.75, 4)
        planned = round(quantity * (1.02 + (measure_idx % 4) * 0.01), 4)
        actual = round(quantity * (0.96 + (measure_idx % 5) * 0.01), 4)
        rejected = round(actual * ((measure_idx % 4) / 100), 4)
        qualified = round(actual - rejected, 4)
        unit_price = round(50 + (measure_idx % 120) * 3.25, 4)
        amount = round(quantity * unit_price, 2)
        record.update(
            {
                "document_no": f"{spec.name.upper()}-{idx:06d}",
                "line_no": ((idx - 1) % 2 + 1) if spec.name == "fact_gl_entry_line" else idx % 10 + 1,
                "business_date": business_day.isoformat(),
                "event_time": event_time.strftime("%Y-%m-%d %H:%M:%S"),
                "posting_date": posting_day.isoformat(),
                "status": ("OPEN", "IN_PROGRESS", "APPROVED", "CLOSED")[idx % 4],
                "priority": idx % 5 + 1,
                "currency_code": currencies[idx % len(currencies)],
                "unit_code": units[idx % len(units)],
                "quantity": quantity,
                "base_quantity": quantity,
                "planned_quantity": planned,
                "actual_quantity": actual,
                "qualified_quantity": qualified,
                "rejected_quantity": rejected,
                "unit_price": unit_price,
                "amount": amount,
                "tax_amount": round(amount * 0.13, 2),
                "cost_amount": round(amount * (0.62 + (idx % 8) / 100), 2),
                "budget_amount": round(amount * 1.08, 2),
                "duration_minutes": round(15 + idx % 360, 2),
                "rate_value": round(0.70 + (idx % 25) / 100, 4),
                "score_value": round(70 + idx % 30, 2),
                "reason_code": reasons[idx % len(reasons)],
                "source_system": "SYNTHETIC_VALIDATION",
                "source_record_id": f"{spec.name}:{idx}",
                "etl_batch_id": f"BATCH-{business_day.strftime('%Y%m')}",
                "trace_id": hashlib.sha256(f"{spec.name}:{idx}:{seed}".encode()).hexdigest()[:32],
                "tenant_code": "DEMO_TENANT",
                "created_at": event_time.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": (event_time + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
                "created_by": "contract_data_generator",
                "updated_by": "contract_data_generator",
                "row_version": 1,
                "data_quality_score": 1.0,
                "is_deleted": 0,
                "remarks": "合成业务记录，仅用于合同技术验证。",
            }
        )
        for name, sql_type in FACT_PROFILES[spec.profile]:
            record[name] = _generic_value(name, sql_type, idx, spec, rng)
        # Keep profile-level temporal and measure fields internally consistent.
        if spec.profile == "sales":
            record["probability"] = round(0.35 + (idx % 60) / 100, 4)
            record["margin_rate"] = round((amount - record["cost_amount"]) / amount, 4)
            record["requested_delivery_date"] = (business_day + timedelta(days=7)).isoformat()
            record["promised_delivery_date"] = (business_day + timedelta(days=10)).isoformat()
            record["expected_close_date"] = (business_day + timedelta(days=15)).isoformat()
        elif spec.profile == "procurement":
            record["requested_date"] = business_day.isoformat()
            record["promised_date"] = (business_day + timedelta(days=7)).isoformat()
            record["received_date"] = (business_day + timedelta(days=idx % 9)).isoformat()
            record["lead_time_days"] = 7
            record["payment_term_code"] = payment_terms[idx % len(payment_terms)]
            record["incoterm_code"] = incoterms[idx % len(incoterms)]
        elif spec.profile == "inventory":
            record["manufacture_date"] = (business_day - timedelta(days=30)).isoformat()
            record["expiry_date"] = (business_day + timedelta(days=365)).isoformat()
            record["available_quantity"] = qualified
            record["reserved_quantity"] = rejected
        elif spec.profile == "production":
            record["start_time"] = event_time.strftime("%Y-%m-%d %H:%M:%S")
            record["end_time"] = (event_time + timedelta(minutes=record["duration_minutes"])).strftime("%Y-%m-%d %H:%M:%S")
            record["yield_rate"] = round(qualified / actual if actual else 0, 4)
            record["scrap_quantity"] = rejected
        elif spec.profile == "quality":
            nominal = round(50 + idx % 10, 3)
            record.update({"nominal_value": nominal, "lower_limit": nominal - 1, "upper_limit": nominal + 1, "measured_value": nominal + ((idx % 5) - 2) * 0.2})
        elif spec.profile == "equipment":
            reading = round(40 + idx % 20, 3)
            record.update({"reading_value": reading, "lower_threshold": 35.0, "upper_threshold": 65.0, "meter_start": idx * 10.0, "meter_end": idx * 10.0 + quantity, "health_index": record["rate_value"]})
        elif spec.profile == "finance":
            record.update({"fiscal_year": business_day.year, "fiscal_period": business_day.month, "debit_amount": amount, "credit_amount": amount, "local_amount": amount, "exchange_rate": 1.0, "tax_rate": 0.13, "due_date": (business_day + timedelta(days=30)).isoformat()})
            if spec.name == "fact_gl_entry_line":
                is_debit = record["line_no"] == 1
                record["debit_amount"] = amount if is_debit else 0.0
                record["credit_amount"] = 0.0 if is_debit else amount
                record["local_amount"] = amount if is_debit else -amount
        elif spec.profile == "safety":
            severity, likelihood = idx % 5 + 1, (idx * 3) % 5 + 1
            record.update({"severity": severity, "likelihood": likelihood, "risk_score": severity * likelihood, "action_due_date": (business_day + timedelta(days=14)).isoformat(), "closed_date": (business_day + timedelta(days=10)).isoformat(), "emission_value": quantity, "limit_value": quantity * 1.2})
        elif spec.profile == "hr":
            record.update({"work_date": business_day.isoformat(), "regular_hours": 8.0, "overtime_hours": round(idx % 4 * 0.5, 2), "assessment_score": record["score_value"], "expiry_date": (business_day + timedelta(days=365)).isoformat()})
        elif spec.profile == "planning":
            record.update({"demand_quantity": planned, "supply_quantity": actual, "capacity_hours": record["duration_minutes"] / 60, "utilization_rate": record["rate_value"], "backlog_quantity": max(planned - actual, 0), "safety_stock_quantity": quantity * 0.2, "forecast_accuracy": record["rate_value"]})
        elif spec.profile == "governance":
            record.update({"detected_at": record["event_time"], "resolved_at": record["updated_at"], "exception_count": 0, "generator_version": GENERATOR_VERSION, "random_seed": seed})
        if spec.name == "fact_legacy_material_issue":
            record["mat_ref"] = material_codes[(idx * 5) % len(material_codes)]
        rows.append([record.get(column) for column in columns])
    placeholders = ",".join("?" for _ in columns)
    con.executemany(
        f"INSERT INTO {qident(spec.name)} ({','.join(qident(c) for c in columns)}) VALUES ({placeholders})",
        rows,
    )


TOKEN_CN = {
    "id": "标识", "code": "编码", "name": "名称", "date": "日期", "time": "时间",
    "status": "状态", "amount": "金额", "quantity": "数量", "price": "价格", "rate": "比率",
    "score": "评分", "source": "来源", "target": "目标", "created": "创建", "updated": "更新",
    "employee": "员工", "customer": "客户", "supplier": "供应商", "material": "物料", "product": "产品",
    "equipment": "设备", "warehouse": "仓库", "factory": "工厂", "order": "订单", "line": "明细",
    "business": "业务", "document": "单据", "reason": "原因", "quality": "质量", "inspection": "检验",
    "actual": "实际", "planned": "计划", "qualified": "合格", "rejected": "不合格", "unit": "单位",
    "data": "数据", "trace": "追溯", "version": "版本", "description": "说明", "remarks": "备注",
}


def column_cn(name: str) -> str:
    return "".join(TOKEN_CN.get(token, token.upper()) for token in name.split("_"))


def refresh_metadata(con: sqlite3.Connection, specs_by_name: dict[str, TableSpec]) -> None:
    tables = user_tables(con)
    existing_tables = {row[0] for row in con.execute("SELECT table_name FROM dim_table_metadata")}
    next_table_id = con.execute("SELECT COALESCE(MAX(table_id),0)+1 FROM dim_table_metadata").fetchone()[0]
    for table in tables:
        if table in existing_tables:
            continue
        spec = specs_by_name.get(table)
        cn = spec.cn_name if spec else table
        desc = (
            f"合同评测用合成{cn}数据表；数据由确定性生成器产生，不对应真实业务记录。"
            if spec
            else f"原108表数据库中的{table}表。"
        )
        category = "维度表" if table.startswith("dim_") else ("事实表" if table.startswith("fact_") else "汇总表")
        con.execute(
            "INSERT INTO dim_table_metadata(table_id,table_name,table_name_cn,table_desc,table_category,table_level) VALUES(?,?,?,?,?,?)",
            (next_table_id, table, cn, desc, category, "contract_validation" if spec else "core"),
        )
        next_table_id += 1

    existing_cols = {(row[0], row[1]) for row in con.execute("SELECT table_name,column_name FROM dim_column_metadata")}
    next_col_id = con.execute("SELECT COALESCE(MAX(column_id),0)+1 FROM dim_column_metadata").fetchone()[0]
    for table in tables:
        for ordinal, info in enumerate(table_columns(con, table), start=1):
            column = info[1]
            if (table, column) in existing_cols:
                continue
            type_name = info[2] or "TEXT"
            desc = f"{column_cn(column)}；物理类型为{type_name}。"
            if table in specs_by_name:
                desc += "合同评测用合成字段。"
            con.execute(
                "INSERT INTO dim_column_metadata(column_id,table_name,column_name,column_name_cn,column_desc,ordinal) VALUES(?,?,?,?,?,?)",
                (next_col_id, table, column, column_cn(column), desc, ordinal),
            )
            next_col_id += 1


def logical_checks(
    con: sqlite3.Connection,
    specs: list[TableSpec],
) -> dict[str, int]:
    checks: dict[str, int] = {}
    new_fact_tables = sorted(spec.name for spec in specs if spec.kind == "fact")
    checks["new_fact_negative_measures"] = sum(
        con.execute(
            f"SELECT COUNT(*) FROM {qident(table)} WHERE quantity<0 OR base_quantity<0 OR planned_quantity<0 "
            "OR actual_quantity<0 OR qualified_quantity<0 OR rejected_quantity<0 OR amount<0 OR cost_amount<0"
        ).fetchone()[0]
        for table in new_fact_tables
    )
    checks["new_fact_bad_dates"] = sum(
        con.execute(f"SELECT COUNT(*) FROM {qident(table)} WHERE date(posting_date)<date(business_date) OR datetime(updated_at)<datetime(created_at)").fetchone()[0]
        for table in new_fact_tables
    )
    checks["new_fact_bad_quantity_decomposition"] = sum(
        con.execute(f"SELECT COUNT(*) FROM {qident(table)} WHERE qualified_quantity + rejected_quantity > actual_quantity + 0.000001").fetchone()[0]
        for table in new_fact_tables
    )
    checks["new_fact_bad_amount_formula"] = sum(
        con.execute(f"SELECT COUNT(*) FROM {qident(table)} WHERE ABS(amount - ROUND(quantity*unit_price,2)) > 0.011").fetchone()[0]
        for table in new_fact_tables
    )
    checks["legacy_material_unresolved"] = con.execute(
        "SELECT COUNT(*) FROM fact_legacy_material_issue l LEFT JOIN dim_material m ON l.mat_ref=m.material_code WHERE m.material_id IS NULL"
    ).fetchone()[0]
    checks["fact_parent_temporal_violations"] = 0
    for spec in specs:
        if spec.kind != "fact":
            continue
        for ref in spec.refs:
            if not ref.parent_table.startswith("fact_"):
                continue
            date_column = parent_date_column(con, ref.parent_table)
            if date_column is None:
                continue
            checks["fact_parent_temporal_violations"] += con.execute(
                f"SELECT COUNT(*) FROM {qident(spec.name)} child "
                f"JOIN {qident(ref.parent_table)} parent "
                f"ON child.{qident(ref.column)}=parent.{qident(ref.parent_column)} "
                f"WHERE date(child.business_date)<date(parent.{qident(date_column)})"
            ).fetchone()[0]
    checks["same_source_target_location"] = con.execute(
        "SELECT COUNT(*) FROM fact_transfer_order WHERE source_warehouse_id=target_warehouse_id"
    ).fetchone()[0] + con.execute(
        "SELECT COUNT(*) FROM fact_fixed_asset_transfer WHERE source_factory_id=target_factory_id"
    ).fetchone()[0]
    checks["cash_flow_invalid_counterparty"] = con.execute(
        "SELECT COUNT(*) FROM fact_cash_flow "
        "WHERE (cust_id IS NULL AND supplier_id IS NULL) OR (cust_id IS NOT NULL AND supplier_id IS NOT NULL)"
    ).fetchone()[0]
    checks["unbalanced_gl_entries"] = con.execute(
        "SELECT COUNT(*) FROM ("
        "SELECT gl_entry_id FROM fact_gl_entry_line GROUP BY gl_entry_id "
        "HAVING ABS(SUM(debit_amount)-SUM(credit_amount))>0.01)"
    ).fetchone()[0]
    profile_tables = {
        profile: [spec.name for spec in specs if spec.kind == "fact" and spec.profile == profile]
        for profile in FACT_PROFILES
    }
    checks["sales_date_sequence_violations"] = sum(
        con.execute(
            f"SELECT COUNT(*) FROM {qident(table)} "
            "WHERE date(promised_delivery_date)<date(requested_delivery_date) "
            "OR date(expected_close_date)<date(business_date)"
        ).fetchone()[0]
        for table in profile_tables["sales"]
    )
    checks["inventory_date_sequence_violations"] = sum(
        con.execute(
            f"SELECT COUNT(*) FROM {qident(table)} WHERE date(expiry_date)<=date(manufacture_date)"
        ).fetchone()[0]
        for table in profile_tables["inventory"]
    )
    checks["production_time_sequence_violations"] = sum(
        con.execute(
            f"SELECT COUNT(*) FROM {qident(table)} WHERE datetime(end_time)<datetime(start_time)"
        ).fetchone()[0]
        for table in profile_tables["production"]
    )
    checks["quality_measurement_bound_violations"] = sum(
        con.execute(
            f"SELECT COUNT(*) FROM {qident(table)} "
            "WHERE measured_value<lower_limit OR measured_value>upper_limit"
        ).fetchone()[0]
        for table in profile_tables["quality"]
    )
    checks["equipment_meter_sequence_violations"] = sum(
        con.execute(
            f"SELECT COUNT(*) FROM {qident(table)} WHERE meter_end<meter_start"
        ).fetchone()[0]
        for table in profile_tables["equipment"]
    )
    checks["finance_due_date_violations"] = sum(
        con.execute(
            f"SELECT COUNT(*) FROM {qident(table)} WHERE date(due_date)<date(business_date)"
        ).fetchone()[0]
        for table in profile_tables["finance"]
    )
    checks["safety_action_date_violations"] = sum(
        con.execute(
            f"SELECT COUNT(*) FROM {qident(table)} "
            "WHERE date(closed_date)<date(business_date) OR date(action_due_date)<date(closed_date)"
        ).fetchone()[0]
        for table in profile_tables["safety"]
    )
    checks["hr_expiry_date_violations"] = sum(
        con.execute(
            f"SELECT COUNT(*) FROM {qident(table)} WHERE date(expiry_date)<date(work_date)"
        ).fetchone()[0]
        for table in profile_tables["hr"]
    )
    checks["planning_balance_violations"] = sum(
        con.execute(
            f"SELECT COUNT(*) FROM {qident(table)} "
            "WHERE ABS(backlog_quantity-MAX(demand_quantity-supply_quantity,0))>0.0001"
        ).fetchone()[0]
        for table in profile_tables["planning"]
    )
    checks["product_cost_total_formula_violations"] = con.execute(
        "SELECT COUNT(*) FROM fact_product_cost "
        "WHERE ABS(total_cost-(material_cost+labor_cost+overhead_cost))>0.011"
    ).fetchone()[0]
    checks["product_cost_unit_formula_violations"] = con.execute(
        "SELECT COUNT(*) FROM fact_product_cost "
        "WHERE output_qty<=0 OR ABS(unit_cost-(1.0*total_cost/output_qty))>0.00011"
    ).fetchone()[0]
    checks["product_cost_unresolved_product"] = con.execute(
        "SELECT COUNT(*) FROM fact_product_cost c "
        "LEFT JOIN dim_product p ON c.product_id=p.prod_id WHERE p.prod_id IS NULL"
    ).fetchone()[0]
    temporal_sql = {
        "delivery_before_order": "SELECT COUNT(*) FROM fact_delivery d JOIN fact_sales_order o ON d.order_id=o.order_id WHERE date(d.delivery_date)<date(o.order_date)",
        "return_before_order": "SELECT COUNT(*) FROM fact_return r JOIN fact_sales_order o ON r.order_id=o.order_id WHERE date(r.return_date)<date(o.order_date)",
        "invoice_before_delivery": "SELECT COUNT(*) FROM fact_invoice i JOIN fact_delivery d ON i.delivery_id=d.delivery_id WHERE date(i.invoice_date)<date(d.delivery_date)",
        "ar_due_before_invoice": "SELECT COUNT(*) FROM fact_ar_aging a JOIN fact_invoice i ON a.invoice_id=i.invoice_id WHERE date(a.due_date)<date(i.invoice_date)",
    }
    for name, sql in temporal_sql.items():
        checks[name] = con.execute(sql).fetchone()[0]
    return checks


def scan_delivery_plaintext(con: sqlite3.Connection) -> dict[str, int]:
    """Scan text values for direct identifiers and named-party residues."""
    patterns = {
        "mainland_mobile": re.compile(r"1[3-9][0-9]{9}"),
        "cn_identity_number": re.compile(r"[0-9]{17}[0-9Xx]"),
        "long_numeric_account": re.compile(r"[0-9]{16,19}"),
        "email_address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        "private_ipv4": re.compile(
            r"(?:10(?:\.[0-9]{1,3}){3}|192\.168(?:\.[0-9]{1,3}){2}|"
            r"172\.(?:1[6-9]|2[0-9]|3[01])(?:\.[0-9]{1,3}){2})"
        ),
    }
    banned_tokens = (
        "卡奥斯",
        "海尔",
        "顺丰",
        "德邦",
        "中远",
        "上海大众",
        "比亚迪",
        "美的集团",
        "格力电器",
        "华为技术",
        "联想集团",
        "中石化",
        "万华化学",
        "巴斯夫",
    )
    counts = {name: 0 for name in patterns}
    counts["named_party_residue"] = 0
    for table in user_tables(con):
        for info in table_columns(con, table):
            column, sql_type = info[1], (info[2] or "").upper()
            if not any(token in sql_type for token in ("TEXT", "CHAR", "CLOB")):
                continue
            for (raw_value,) in con.execute(
                f"SELECT {qident(column)} FROM {qident(table)} "
                f"WHERE {qident(column)} IS NOT NULL"
            ):
                value = str(raw_value).strip()
                for name, pattern in patterns.items():
                    if pattern.fullmatch(value):
                        counts[name] += 1
                if any(token in value for token in banned_tokens):
                    counts["named_party_residue"] += 1
    return counts


def validate_database(
    con: sqlite3.Connection,
    specs: list[TableSpec],
    deidentification: dict[str, Any],
    fk_repairs: dict[str, int],
    temporal_repairs: dict[str, int],
    seeded_base_tables: dict[str, int],
    seed: int,
) -> dict[str, Any]:
    expected_new = {spec.name for spec in specs}
    tables = user_tables(con)
    columns = sum(len(table_columns(con, table)) for table in tables)
    total_rows = sum(con.execute(f"SELECT COUNT(*) FROM {qident(table)}").fetchone()[0] for table in tables)
    fks = list(con.execute("PRAGMA foreign_key_check"))
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    quick = con.execute("PRAGMA quick_check").fetchone()[0]
    table_meta = {row[0] for row in con.execute("SELECT table_name FROM dim_table_metadata")}
    col_meta = {(row[0], row[1]) for row in con.execute("SELECT table_name,column_name FROM dim_column_metadata")}
    physical_cols = {(table, info[1]) for table in tables for info in table_columns(con, table)}
    new_fact_tables = sorted(table for table in expected_new if table.startswith("fact_"))
    logic = logical_checks(con, specs)
    plaintext_scan = scan_delivery_plaintext(con)
    hard_checks = {
        "table_count_exact_200": len(tables) == TARGET_TABLES,
        "column_count_at_least_5000": columns >= TARGET_COLUMNS,
        "integrity_check_ok": integrity == "ok",
        "quick_check_ok": quick == "ok",
        "declared_fk_violations_zero": len(fks) == 0,
        "all_new_tables_present": expected_new.issubset(tables),
        "table_metadata_complete": set(tables) == table_meta,
        "column_metadata_complete": physical_cols == col_meta,
        "all_tables_nonempty": all(
            con.execute(f"SELECT EXISTS(SELECT 1 FROM {qident(table)} LIMIT 1)").fetchone()[0]
            for table in tables
        ),
        "business_plaintext_scan_zero": sum(plaintext_scan.values()) == 0,
        "logical_violations_zero": sum(logic.values()) == 0,
    }
    return {
        "ok": all(hard_checks.values()),
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "tables": len(tables),
        "columns": columns,
        "rows": total_rows,
        "new_tables": len(expected_new),
        "new_fact_tables": len(new_fact_tables),
        "new_dimension_tables": len(expected_new) - len(new_fact_tables),
        "declared_foreign_keys": sum(len(list(con.execute(f"PRAGMA foreign_key_list({qident(table)})"))) for table in tables),
        "indexes": con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'").fetchone()[0],
        "integrity_check": integrity,
        "quick_check": quick,
        "foreign_key_violations": len(fks),
        "metadata": {
            "table_records": len(table_meta),
            "column_records": len(col_meta),
            "table_coverage": round(len(set(tables) & table_meta) / len(tables), 6),
            "column_coverage": round(len(physical_cols & col_meta) / len(physical_cols), 6),
        },
        "base_fk_repairs": fk_repairs,
        "base_temporal_repairs": temporal_repairs,
        "deidentification": deidentification,
        "seeded_base_empty_tables": seeded_base_tables,
        "logical_checks": logic,
        "business_plaintext_scan": plaintext_scan,
        "validation_cases": con.execute("SELECT COUNT(*) FROM dim_validation_case").fetchone()[0],
        "hard_checks": hard_checks,
    }


def write_dictionary(con: sqlite3.Connection, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    table_meta = {
        row[0]: row
        for row in con.execute("SELECT table_name,table_name_cn,table_desc,table_category,table_level FROM dim_table_metadata")
    }
    col_meta = {
        (row[0], row[1]): row
        for row in con.execute("SELECT table_name,column_name,column_name_cn,column_desc,ordinal FROM dim_column_metadata")
    }
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["table_name", "table_name_cn", "table_category", "table_desc", "column_name", "column_name_cn", "data_type", "not_null", "primary_key", "column_desc", "ordinal"])
        for table in user_tables(con):
            tm = table_meta[table]
            for info in table_columns(con, table):
                cm = col_meta[(table, info[1])]
                writer.writerow([table, tm[1], tm[3], tm[2], info[1], cm[2], info[2], info[3], info[5], cm[3], cm[4]])


def build(base: Path, output: Path, report: Path, dictionary: Path, force: bool, seed: int) -> dict[str, Any]:
    base = base.resolve()
    output = output.resolve()
    if not base.is_file():
        raise FileNotFoundError(f"base database not found: {base}")
    if output == base:
        raise ValueError("output must not overwrite the 108-table source database")
    if output.exists() and not force:
        raise FileExistsError(f"output exists; use --force to replace the generated destination: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    dictionary.parent.mkdir(parents=True, exist_ok=True)
    base_hash = sha256_file(base)
    rng = random.Random(seed)
    specs = table_specs()
    tmp_handle = tempfile.NamedTemporaryFile(prefix="contract_200_", suffix=".db", dir=output.parent, delete=False)
    tmp_path = Path(tmp_handle.name)
    tmp_handle.close()
    try:
        source = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
        dest = sqlite3.connect(tmp_path)
        source.backup(dest)
        source.close()
        dest.row_factory = sqlite3.Row
        dest.execute("PRAGMA foreign_keys=OFF")
        dest.execute("PRAGMA journal_mode=DELETE")
        dest.execute("PRAGMA synchronous=FULL")
        deidentification = deidentify_base_plaintext(dest)
        fk_repairs = repair_base_foreign_keys(dest)
        temporal_repairs = repair_base_temporal_rules(dest)
        seeded_base_tables = seed_empty_base_tables(dest)
        dest.commit()
        dest.execute("PRAGMA foreign_keys=ON")
        existing = set(user_tables(dest))
        collisions = existing & {spec.name for spec in specs}
        if collisions:
            raise AssertionError(f"new tables collide with the base database: {sorted(collisions)}")
        for spec in specs:
            create_table(dest, spec)
        for spec in specs:
            if spec.kind == "dim":
                seed_dimension(dest, spec, rng, base_hash, seed)
            else:
                seed_fact(dest, spec, rng, seed)
        refresh_metadata(dest, {spec.name: spec for spec in specs})
        dest.commit()
        validation = validate_database(
            dest,
            specs,
            deidentification,
            fk_repairs,
            temporal_repairs,
            seeded_base_tables,
            seed,
        )
        if not validation["ok"]:
            failure_detail = {
                "hard_checks": validation["hard_checks"],
                "logical_checks": validation["logical_checks"],
            }
            raise RuntimeError(
                "generated database failed validation: "
                + json.dumps(failure_detail, ensure_ascii=False)
            )
        write_dictionary(dest, dictionary)
        dest.execute("VACUUM")
        dest.close()
        if output.exists():
            output.unlink()
        os.replace(tmp_path, output)
        validation["database_path"] = str(output)
        validation["database_bytes"] = output.stat().st_size
        validation["database_sha256"] = sha256_file(output)
        validation["base_database_path"] = str(base)
        validation["base_database_sha256"] = base_hash
        validation["dictionary_path"] = str(dictionary)
        report.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return validation
    except Exception:
        try:
            dest.close()
        except Exception:
            pass
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True, help="read-only 108-table SQLite source")
    parser.add_argument("--output", type=Path, required=True, help="new 200-table SQLite database")
    parser.add_argument("--report", type=Path, required=True, help="JSON validation report")
    parser.add_argument("--dictionary", type=Path, required=True, help="CSV data dictionary")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--force", action="store_true", help="replace an existing generated destination")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build(args.base, args.output, args.report, args.dictionary, args.force, args.seed)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
