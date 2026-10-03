#!/usr/bin/env python3
"""Build and validate the contract-aligned 200-table synthetic validation database (generator v2).

The source database is opened read-only and never modified.  The destination is
built in a temporary file, validated, and atomically moved into place only when
all hard checks pass.

v2 (after an independent audit of v1): references are derived along business
chains, header totals come from their lines, every column has a meaningful
value, measures and units follow each table's meaning, known defects of the
108-table base are kept and registered as validation cases instead of being
masked, and ``contract_200_checks`` runs the audit as post-generation checks.
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
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contract_200_checks as CHK  # noqa: E402
import contract_200_metadata as META  # noqa: E402
import contract_200_semantics as SEM  # noqa: E402
import contract_200_values as VAL  # noqa: E402

GENERATOR_VERSION = "2.0.0"
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
        S("dim_shift", "生产班次", "主数据", "dim", "time", 15, [R("workshop_id", "dim_workshop", "workshop_id")]),
        S("dim_currency", "币种", "财务", "dim", "finance", 4),
        S("dim_unit_of_measure", "计量单位", "主数据", "dim", "code", 10),
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



def _tables_with_column(con: sqlite3.Connection, column: str) -> list[str]:
    return [table for table in user_tables(con) if column in {row[1] for row in table_columns(con, table)}]


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


# ───────────────────────── 原 108 表:只登记已知问题,不改数据 ─────────────────────────
# v1 把孤儿引用轮流挂到现有记录、把子单据日期改成父单据日期、删除汇总层重复粒度,数据看起来干净,
# 问题却被掩盖。v2 原样保留,逐项登记为验证案例(数量在生成时按底库实测,校验时复算比对)。
BASE_ISSUES: list[tuple[str, str, str, str, str]] = [
    ("BASE_ORPHAN_RETURN_ORDER", "fact_return", "order_id", "退货引用的销售订单不存在(声明外键,原库即有)",
     "SELECT COUNT(*) FROM fact_return WHERE order_id IS NOT NULL AND order_id NOT IN (SELECT order_id FROM fact_sales_order)"),
    ("BASE_ORPHAN_INVOICE_DELIVERY", "fact_invoice", "delivery_id", "发票引用的交付单不存在",
     "SELECT COUNT(*) FROM fact_invoice WHERE delivery_id IS NOT NULL AND delivery_id NOT IN (SELECT delivery_id FROM fact_delivery)"),
    ("BASE_ORPHAN_AR_INVOICE", "fact_ar_aging", "invoice_id", "应收账龄引用的发票不存在",
     "SELECT COUNT(*) FROM fact_ar_aging WHERE invoice_id IS NOT NULL AND invoice_id NOT IN (SELECT invoice_id FROM fact_invoice)"),
    ("BASE_ORPHAN_CONSUMPTION_ORDER", "fact_material_consumption", "order_id", "物料消耗引用的生产工单不存在(约八成)",
     "SELECT COUNT(*) FROM fact_material_consumption WHERE order_id IS NOT NULL AND order_id NOT IN (SELECT order_id FROM fact_production_order)"),
    ("BASE_RETURN_OVER_ORDER_QTY", "fact_return", "quantity", "退货数量超过订单数量",
     "SELECT COUNT(*) FROM fact_return r JOIN fact_sales_order o ON r.order_id=o.order_id WHERE r.quantity>o.quantity"),
    ("BASE_RUNTIME_EXCEEDS_SPAN", "fact_production_order", "runtime_hours", "工单运行时长超过开始到结束的时间跨度",
     "SELECT COUNT(*) FROM fact_production_order WHERE runtime_hours > (julianday(end_time)-julianday(start_time))*24 + 0.1"),
    ("BASE_CONSUMPTION_NOT_IN_BOM", "fact_material_consumption", "material_id", "消耗物料不在工单产品 BOM 中",
     "SELECT COUNT(*) FROM fact_material_consumption c JOIN fact_production_order p ON c.order_id=p.order_id WHERE NOT EXISTS (SELECT 1 FROM dim_bom b JOIN dim_bom_line bl ON b.bom_id=bl.bom_id WHERE b.product_id=p.prod_id AND bl.material_id=c.material_id)"),
    ("BASE_DWS_EMP_NAME_MISMATCH", "DWS_IN_SALES_DAILY", "emp_name", "汇总层员工姓名与员工主数据不一致",
     "SELECT COUNT(*) FROM DWS_IN_SALES_DAILY a JOIN dim_employee e ON a.dim_emp_id=e.emp_id WHERE a.emp_name<>e.emp_name"),
    ("BASE_AGG_EMP_NAME_MISMATCH", "agg_metric_monthly", "emp_name", "月度指标汇总的员工姓名与主数据不一致",
     "SELECT COUNT(*) FROM agg_metric_monthly a JOIN dim_employee e ON a.emp_id=e.emp_id WHERE a.emp_name<>e.emp_name"),
    ("BASE_DWS_EQUIPMENT_DUP_GRAIN", "DWS_EQUIPMENT_DAILY", "date,dim_equipment_id,dim_line_id", "设备日汇总同一粒度重复行",
     "SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM DWS_EQUIPMENT_DAILY GROUP BY date, dim_equipment_id, dim_line_id HAVING n>1)"),
    ("BASE_DWS_SAFETY_DUP_GRAIN", "DWS_SAFETY_DAILY", "date,dim_workshop_id", "安全日汇总同一粒度重复行",
     "SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM DWS_SAFETY_DAILY GROUP BY date, dim_workshop_id HAVING n>1)"),
    ("BASE_DOWNTIME_REASON_STORES_ID", "fact_downtime", "reason_code", "停机原因码列存的是原因 id,不是原因编码",
     "SELECT COUNT(*) FROM fact_downtime WHERE reason_code NOT IN (SELECT reason_code FROM dim_downtime_reason) AND reason_code IN (SELECT reason_id FROM dim_downtime_reason)"),
    ("BASE_MATERIAL_ID_COMMENT_PRODUCT", "fact_material_consumption", "material_id", "列名为物料 id,注释却说关联产品表",
     "SELECT COUNT(*) FROM dim_column_metadata WHERE table_name='fact_material_consumption' AND column_name='material_id' AND column_desc LIKE '%dim_product%'"),
    ("BASE_ALARM_LAYER_GAP", "DWS_EQUIPMENT_DAILY", "alarm_count", "设备日汇总报警数合计与报警明细条数之差",
     "SELECT (SELECT SUM(alarm_count) FROM DWS_EQUIPMENT_DAILY) - (SELECT COUNT(*) FROM fact_alarm)"),
    ("BASE_PRODUCTION_LAYER_GAP", "DWS_PRODUCTION_DAILY", "actual_quantity", "生产日汇总产量与工单实际产量之差(取整)",
     "SELECT CAST(ROUND((SELECT SUM(actual_quantity) FROM fact_production_order) - (SELECT SUM(actual_quantity) FROM DWS_PRODUCTION_DAILY)) AS INTEGER)"),
]

# 新表里刻意构造并登记的挑战与脏数据
SOFT_DELETE_TABLES = ("fact_lead", "fact_customer_contact", "fact_inventory_movement", "fact_attendance", "fact_condition_monitoring")
LEGACY_UNRESOLVED_RATE = 0.05


def _column_defs(spec: TableSpec) -> list[tuple[str, str]]:
    columns: list[tuple[str, str]] = [(spec.pk, "INTEGER PRIMARY KEY")]
    for ref in spec.refs:
        columns.append((ref.column, "INTEGER" + ("" if ref.nullable else " NOT NULL")))
    if spec.kind == "dim":
        columns += SEM.DIM_COMMON + SEM.DIM_ATTRS[spec.name]
    else:
        columns += SEM.fact_columns(spec.name)
    for extra in spec.extras:
        columns.append((extra.name, extra.sql_type + ("" if extra.nullable else " NOT NULL")))
    names = [n for n, _ in columns]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise AssertionError(f"{spec.name}: duplicate columns {sorted(dup)}")
    return columns


def create_table(con: sqlite3.Connection, spec: TableSpec) -> None:
    defs = [f"{qident(name)} {sql_type}" for name, sql_type in _column_defs(spec)]
    names = {n for n, _ in _column_defs(spec)}
    if spec.kind == "dim":
        defs.append("CHECK(date(effective_to) >= date(effective_from))")
    else:
        defs += ["CHECK(date(posting_date) >= date(business_date))", "CHECK(datetime(updated_at) >= datetime(created_at))"]
        if "qualified_quantity" in names:
            defs.append("CHECK(qualified_quantity + rejected_quantity <= actual_quantity + 0.000001)")
            defs.append("FOREIGN KEY(unit_code) REFERENCES dim_unit_of_measure(code)")
        if "currency_code" in names:
            defs.append("FOREIGN KEY(currency_code) REFERENCES dim_currency(code)")
        defs.append("FOREIGN KEY(reason_code) REFERENCES dim_reason_code(code)")
        defs.append("FOREIGN KEY(business_date) REFERENCES dim_calendar(calendar_date)")
        if "payment_term_code" in names:
            defs.append("FOREIGN KEY(payment_term_code) REFERENCES dim_payment_term(code)")
            defs.append("FOREIGN KEY(incoterm_code) REFERENCES dim_incoterm(code)")
    for ref in spec.refs:
        if ref.declared:
            defs.append(f"FOREIGN KEY({qident(ref.column)}) REFERENCES {qident(ref.parent_table)}({qident(ref.parent_column)}) "
                        "ON UPDATE RESTRICT ON DELETE RESTRICT")
    con.execute(f"CREATE TABLE {qident(spec.name)} (\n  " + ",\n  ".join(defs) + "\n)")
    if spec.name == "dim_calendar":
        con.execute("CREATE UNIQUE INDEX idx_dim_calendar_calendar_date ON dim_calendar(calendar_date)")
    for ref in spec.refs:
        con.execute(f"CREATE INDEX {qident('idx_' + spec.name + '_' + ref.column)} ON {qident(spec.name)}({qident(ref.column)})")
    if spec.kind == "fact":
        con.execute(f"CREATE INDEX {qident('idx_' + spec.name + '_business_date')} ON {qident(spec.name)}(business_date)")
        con.execute(f"CREATE INDEX {qident('idx_' + spec.name + '_status')} ON {qident(spec.name)}(status)")


def _insert(con: sqlite3.Connection, spec: TableSpec, records: list[dict[str, Any]]) -> None:
    columns = [n for n, _ in _column_defs(spec)]
    con.executemany(
        f"INSERT INTO {qident(spec.name)} ({','.join(qident(c) for c in columns)}) VALUES ({','.join('?' for _ in columns)})",
        [[r.get(c) for c in columns] for r in records])


def _dim_refs(spec: TableSpec, idx: int, ctx: SEM.Context, rng: random.Random) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ref in spec.refs:
        ids = ctx.ids(ref.parent_table, ref.parent_column)
        if spec.name == "dim_shift":
            out[ref.column] = ids[(idx - 1) // 3]                 # 每个车间三个班次
        elif spec.name in ("dim_quality_characteristic", "dim_job_position", "dim_process_parameter", "dim_supplier_site"):
            out[ref.column] = ids[(idx - 1) % len(ids)]            # 覆盖每个产品/部门/设备类型/供应商
        else:
            out[ref.column] = VAL._zipf_pick(rng, ids)
    return out


def seed_dimension(con: sqlite3.Connection, spec: TableSpec, ctx: SEM.Context, rng: random.Random,
                   extras: list[dict[str, Any]] | None = None) -> None:
    records = []
    for idx in range(1, spec.rows + 1):
        rec = VAL.dim_record(spec, idx, _dim_refs(spec, idx, ctx, rng), ctx, rng, extras[idx - 1] if extras else None)
        rec[spec.pk] = idx
        records.append(rec)
    _insert(con, spec, records)
    ctx.invalidate(spec.name)


def seed_fact(con: sqlite3.Connection, spec: TableSpec, ctx: SEM.Context, seed: int,
              gov_rows: list[dict[str, Any]] | None = None) -> None:
    rng = random.Random(f"{seed}:{spec.name}")
    scale = {"qty": SEM.lognorm(rng, 20.0, 0.8), "price": SEM.lognorm(rng, 800.0, 0.8), "amount": SEM.lognorm(rng, 20000.0, 0.6),
             "duration": SEM.lognorm(rng, 90.0, 0.5), "rate": rng.uniform(0.82, 0.96), "score": rng.uniform(72, 90),
             "reject": rng.uniform(0.005, 0.03)}
    if gov_rows is not None:
        rows = [{"_gov": g, **{k: v for k, v in g.items() if k.endswith("_id")}} for g in gov_rows]
    else:
        rows = VAL.derive_refs(spec.name, spec.refs, spec.rows, ctx, rng)
    records = [VAL.fact_record(spec, i + 1, row, ctx, rng, seed, scale) for i, row in enumerate(rows)]
    if spec.name == "fact_legacy_material_issue":
        for rec, row in zip(records, rows):
            rec["mat_ref"] = ctx.rows("dim_material", "material_id")[row["_material_id"]]["material_code"]
    _insert(con, spec, records)
    ctx.invalidate(spec.name)


def aggregate_headers(con: sqlite3.Connection, ctx: SEM.Context) -> dict[str, int]:
    """表头的数量与金额由明细汇总;单价为加权平均价。"""
    done = {}
    for header, (line, key) in SEM.HEADER_OF.items():
        if header not in SEM.FACT_SEMANTICS or header == "fact_inspection_lot":
            continue                                          # 原表表头(销售订单、发票、合同)不改,明细按表头拆分;
                                                              # 检验批数量是批量(吨),检验结果是样本个数,不汇总
        cols = {r[1] for r in con.execute(f'PRAGMA table_info("{header}")')}
        if header == "fact_gl_entry":
            con.execute("UPDATE fact_gl_entry SET debit_amount=amount, credit_amount=amount")
            done[header] = 0
            continue
        sets = []
        if "quantity" in cols:
            for c in ("quantity", "base_quantity", "planned_quantity", "actual_quantity", "qualified_quantity", "rejected_quantity"):
                sets.append(f"{c}=(SELECT ROUND(SUM(l.{c}),3) FROM {qident(line)} l WHERE l.{key}={qident(header)}.{key})")
        if "amount" in cols:
            for c in ("amount", "tax_amount", "cost_amount", "budget_amount"):
                sets.append(f"{c}=(SELECT ROUND(SUM(l.{c}),2) FROM {qident(line)} l WHERE l.{key}={qident(header)}.{key})")
        con.execute(f"UPDATE {qident(header)} SET {', '.join(sets)} WHERE {key} IN (SELECT {key} FROM {qident(line)})")
        if "amount" in cols:
            con.execute(f"UPDATE {qident(header)} SET local_amount=ROUND(amount*exchange_rate,2), "
                        f"unit_price=CASE WHEN quantity>0 THEN ROUND(amount/quantity,4) ELSE unit_price END")
        if "available_quantity" in cols:
            con.execute(f"UPDATE {qident(header)} SET available_quantity=qualified_quantity, reserved_quantity=MIN(reserved_quantity, qualified_quantity)")
        if "book_quantity" in cols:
            con.execute(f"UPDATE {qident(header)} SET book_quantity=(SELECT ROUND(SUM(book_quantity),3) FROM {qident(line)} l WHERE l.{key}={qident(header)}.{key}), "
                        f"counted_quantity=(SELECT ROUND(SUM(counted_quantity),3) FROM {qident(line)} l WHERE l.{key}={qident(header)}.{key})")
            con.execute(f"UPDATE {qident(header)} SET variance_quantity=ROUND(counted_quantity-book_quantity,3), recount_required=ABS(counted_quantity-book_quantity)>0.01")
        if "gross_weight_kg" in cols:
            con.execute(f"UPDATE {qident(header)} SET gross_weight_kg=ROUND(quantity*1000*1.03,1), package_count=MAX(1, CAST(quantity*40 AS INTEGER))")
        done[header] = con.execute(f"SELECT COUNT(*) FROM {qident(header)}").fetchone()[0]
        ctx.invalidate(header)
    return done


def inject_dirty_data(con: sqlite3.Connection, seed: int) -> dict[str, int]:
    """刻意构造并登记的脏数据:叶子事实表少量逻辑删除;遗留领料记录 5% 物料编码在物料主数据中找不到。"""
    rng = random.Random(f"{seed}:dirty")
    out = {}
    for t in SOFT_DELETE_TABLES:
        ids = [r[0] for r in con.execute(f"SELECT rowid FROM {qident(t)} ORDER BY rowid")]
        pick = sorted(rng.sample(ids, max(1, int(len(ids) * 0.02))))
        con.executemany(f"UPDATE {qident(t)} SET is_deleted=1, status='CANCELLED' WHERE rowid=?", [(i,) for i in pick])
        out[t] = len(pick)
    ids = [r[0] for r in con.execute("SELECT rowid FROM fact_legacy_material_issue ORDER BY rowid")]
    pick = sorted(rng.sample(ids, max(1, int(len(ids) * LEGACY_UNRESOLVED_RATE))))
    con.executemany("UPDATE fact_legacy_material_issue SET mat_ref=? WHERE rowid=?", [(f"LEGACY-{9000 + i}", r) for i, r in enumerate(pick)])
    out["fact_legacy_material_issue.unresolved"] = len(pick)
    return out


def _new_ref_orphan_sql(specs: list[TableSpec]) -> str:
    parts = [f"(SELECT COUNT(*) FROM {qident(s.name)} WHERE {qident(r.column)} IS NOT NULL AND {qident(r.column)} NOT IN "
             f"(SELECT {qident(r.parent_column)} FROM {qident(r.parent_table)}))" for s in specs for r in s.refs]
    return "SELECT " + " + ".join(parts)


def build_cases(con: sqlite3.Connection, specs: list[TableSpec], dirty: dict[str, int]) -> list[dict[str, Any]]:
    """验证案例:每条带可执行的检测 SQL,预期数量由生成时实测并写入,校验时复算比对。"""
    soft_sql = "SELECT " + " + ".join(f"(SELECT COUNT(*) FROM {t} WHERE is_deleted=1)" for t in SOFT_DELETE_TABLES)
    rows = [
        ("MISSING_DECLARED_FK", "fact_legacy_material_issue", "mat_ref", "物料编码引用物料主数据,但没有声明外键",
         "SELECT COUNT(*) FROM fact_legacy_material_issue WHERE mat_ref IN (SELECT material_code FROM dim_material)", "challenge"),
        ("IRREGULAR_KEY_NAME", "fact_legacy_material_issue", "mat_ref", "引用列名(mat_ref)与父表键名(material_code)不同",
         "SELECT COUNT(DISTINCT mat_ref) FROM fact_legacy_material_issue WHERE mat_ref IN (SELECT material_code FROM dim_material)", "challenge"),
        ("LEGACY_UNRESOLVED_CODE", "fact_legacy_material_issue", "mat_ref", "约 5% 的遗留物料编码在主数据中找不到(孤儿,刻意构造)",
         "SELECT COUNT(*) FROM fact_legacy_material_issue WHERE mat_ref NOT IN (SELECT material_code FROM dim_material)", "dirty_data"),
        ("SOFT_DELETED_ROWS", ",".join(SOFT_DELETE_TABLES), "is_deleted", "叶子事实表约 2% 的行逻辑删除(刻意构造)", soft_sql, "dirty_data"),
        ("INACTIVE_MASTER_DATA", "dim_*", "status", "部分新维表记录已停用(失效日期为 2026-03-31)",
         "SELECT " + " + ".join(f"(SELECT COUNT(*) FROM {s.name} WHERE status='INACTIVE')" for s in specs if s.kind == "dim"), "dirty_data"),
        ("COMPOSITE_KEY", "fact_shipment_line", "shipment_id,line_no", "发运单号 + 行号唯一标识一条发运明细",
         "SELECT COUNT(*) FROM (SELECT shipment_id, line_no FROM fact_shipment_line GROUP BY 1,2 HAVING COUNT(*)>1)", "challenge"),
        ("DUAL_WAREHOUSE_ROLE", "fact_transfer_order", "source_warehouse_id,target_warehouse_id", "来源仓与目标仓是同一父表的两个角色,不相同",
         "SELECT COUNT(*) FROM fact_transfer_order WHERE source_warehouse_id=target_warehouse_id", "challenge"),
        ("LOW_CARDINALITY_CODE", "dim_reason_code", "code", "原因码只有 12 个取值,被几乎所有事实表引用",
         "SELECT COUNT(*) FROM dim_reason_code", "challenge"),
        ("TEMPORAL_ORDER", "fact_shipment", "business_date,posting_date", "过账日期不早于业务日期",
         "SELECT COUNT(*) FROM fact_shipment WHERE posting_date<business_date", "challenge"),
        ("AMOUNT_RECONCILIATION", "fact_sales_order_line", "quantity,unit_price,amount", "明细金额 = 数量 × 单价(容差 0.05)",
         "SELECT COUNT(*) FROM fact_sales_order_line WHERE ABS(amount-quantity*unit_price)>0.05", "challenge"),
        ("HEADER_LINE_RECONCILIATION", "fact_shipment", "quantity,amount", "发运单数量与金额 = 其明细合计",
         "SELECT COUNT(*) FROM fact_shipment h JOIN (SELECT shipment_id, SUM(quantity) q, SUM(amount) a FROM fact_shipment_line GROUP BY 1) x USING(shipment_id) WHERE ABS(h.quantity-x.q)>0.001 OR ABS(h.amount-x.a)>0.011", "challenge"),
        ("QUALITY_DECOMPOSITION", "fact_inspection_result", "actual_quantity,qualified_quantity,rejected_quantity", "合格 + 不合格不超过实际数量",
         "SELECT COUNT(*) FROM fact_inspection_result WHERE qualified_quantity+rejected_quantity>actual_quantity+0.000001", "challenge"),
        ("TRACEABILITY", "fact_lot_trace", "trace_id", "每条记录有唯一追溯标识",
         "SELECT COUNT(*)-COUNT(DISTINCT trace_id) FROM fact_lot_trace", "challenge"),
        ("METADATA_COMPLETENESS", "dim_column_metadata", "column_desc", "每个物理列都有元数据",
         "SELECT COUNT(*) FROM sqlite_master m, pragma_table_info(m.name) p WHERE m.type='table' AND m.name NOT LIKE 'sqlite_%' "
         "AND NOT EXISTS (SELECT 1 FROM dim_column_metadata c WHERE c.table_name=m.name AND c.column_name=p.name)", "challenge"),
        ("NEW_REFERENCE_INTEGRITY", "new tables", "*", "新表的引用(含未声明的)全部能在父表找到", _new_ref_orphan_sql(specs), "challenge"),
    ] + [(code, tbl, col, desc, sql, "base_known_issue") for code, tbl, col, desc, sql in BASE_ISSUES]
    counting = {"MISSING_DECLARED_FK", "IRREGULAR_KEY_NAME", "LOW_CARDINALITY_CODE"}   # 这几条是计数,其余挑战案例断言违反数为 0
    out = []
    for code, tbl, col, desc, sql, origin in rows:
        count = con.execute(sql).fetchone()[0] or 0
        out.append({"case_type": code, "target_table": tbl, "target_column": col, "expected_behavior": desc,
                    "is_intentional": int(origin != "base_known_issue"), "detection_rule": sql, "expected_count": int(count),
                    "tolerance_value": 0.0, "owner_team": "数据治理" if origin == "base_known_issue" else "本体评测",
                    "remediation_hint": {"base_known_issue": "原 108 表的问题,如实保留并登记;修复须在源库进行",
                                         "dirty_data": "刻意构造的脏数据,评测智能体是否识别并合理处理",
                                         "challenge": "刻意构造的建模挑战,数据须满足描述"}[origin],
                    "origin": {"base_known_issue": "原108表", "dirty_data": "刻意构造", "challenge": "刻意构造"}[origin],
                    "asserts_zero": int(origin == "challenge" and code not in counting)})
        if out[-1]["asserts_zero"] and count:
            raise AssertionError(f"validation case {code} asserts no violations but found {count}: {desc}")
    return out


def refresh_metadata(con: sqlite3.Connection, specs: list[TableSpec]) -> None:
    by_name = {s.name: s for s in specs}
    table_cn = {r[0]: r[1] for r in con.execute("SELECT table_name, table_name_cn FROM dim_table_metadata")}
    table_cn.update({s.name: s.cn_name for s in specs})
    table_cn.setdefault("dim_workshop", "车间")
    next_tid = con.execute("SELECT COALESCE(MAX(table_id),0)+1 FROM dim_table_metadata").fetchone()[0]
    existing = {r[0] for r in con.execute("SELECT table_name FROM dim_table_metadata")}
    for table in user_tables(con):
        if table in existing:
            continue
        spec = by_name.get(table)
        header = SEM.FACT_SEMANTICS.get(table, {}).get("line_of")
        desc = META.table_description(spec, table_cn, table_cn.get(header[1]) if header else None) if spec else f"原108表数据库中的{table}表。"
        category = "维度表" if table.startswith("dim_") else ("事实表" if table.startswith("fact_") else "汇总表")
        con.execute("INSERT INTO dim_table_metadata(table_id,table_name,table_name_cn,table_desc,table_category,table_level) VALUES(?,?,?,?,?,?)",
                    (next_tid, table, table_cn.get(table, table), desc, category, "contract_validation" if spec else "core"))
        next_tid += 1
    existing_cols = {(r[0], r[1]) for r in con.execute("SELECT table_name,column_name FROM dim_column_metadata")}
    next_cid = con.execute("SELECT COALESCE(MAX(column_id),0)+1 FROM dim_column_metadata").fetchone()[0]
    for table in user_tables(con):
        spec = by_name.get(table)
        refs = {r.column: r for r in spec.refs} if spec else {}
        for ordinal, info in enumerate(table_columns(con, table), start=1):
            column = info[1]
            if (table, column) in existing_cols:
                continue
            if spec:
                cn = "主键" if column == spec.pk else META.column_cn(table, column, refs, table_cn)
                if column == spec.pk:
                    cn = f"{spec.cn_name}ID"
                desc = f"{cn};物理类型为 {info[2] or 'TEXT'}"
                if column in refs:
                    r = refs[column]
                    desc += f";引用{table_cn.get(r.parent_table, r.parent_table)}({r.parent_table}.{r.parent_column})"
                desc += "。合成数据。"
            else:
                cn, desc = column, f"{column};物理类型为 {info[2] or 'TEXT'}。"
            con.execute("INSERT INTO dim_column_metadata(column_id,table_name,column_name,column_name_cn,column_desc,ordinal) VALUES(?,?,?,?,?,?)",
                        (next_cid, table, column, cn, desc, ordinal))
            next_cid += 1


def validate_database(con: sqlite3.Connection, specs: list[TableSpec], extra: dict[str, Any], seed: int) -> dict[str, Any]:
    expected_new = {spec.name for spec in specs}
    tables = user_tables(con)
    columns = sum(len(table_columns(con, table)) for table in tables)
    total_rows = sum(con.execute(f"SELECT COUNT(*) FROM {qident(table)}").fetchone()[0] for table in tables)
    fks = list(con.execute("PRAGMA foreign_key_check"))
    fk_by_table: dict[str, int] = {}
    for row in fks:
        fk_by_table[row[0]] = fk_by_table.get(row[0], 0) + 1
    registered_orphans = {c["target_table"]: c["expected_count"] for c in extra["cases"] if c["case_type"].startswith("BASE_ORPHAN_")}
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    table_meta = {row[0] for row in con.execute("SELECT table_name FROM dim_table_metadata")}
    col_meta = {(row[0], row[1]) for row in con.execute("SELECT table_name,column_name FROM dim_column_metadata")}
    physical_cols = {(table, info[1]) for table in tables for info in table_columns(con, table)}
    semantic = CHK.run(con, {s.name: s for s in specs})
    plaintext_scan = scan_delivery_plaintext(con)
    hard_checks = {
        "table_count_exact_200": len(tables) == TARGET_TABLES,
        "column_count_at_least_5000": columns >= TARGET_COLUMNS,
        "integrity_check_ok": integrity == "ok",
        "declared_fk_violations_only_registered_base_orphans": fk_by_table == {t: n for t, n in registered_orphans.items() if n},
        "all_new_tables_present": expected_new.issubset(tables),
        "table_metadata_complete": set(tables) == table_meta,
        "column_metadata_complete": physical_cols == col_meta,
        "all_tables_nonempty": all(con.execute(f"SELECT EXISTS(SELECT 1 FROM {qident(t)} LIMIT 1)").fetchone()[0] for t in tables),
        "business_plaintext_scan_zero": sum(plaintext_scan.values()) == 0,
        "semantic_checks_pass": all(v["pass"] for v in semantic.values()),
    }
    return {
        "ok": all(hard_checks.values()), "generator_version": GENERATOR_VERSION, "seed": seed,
        "tables": len(tables), "columns": columns, "rows": total_rows, "new_tables": len(expected_new),
        "new_fact_tables": sum(1 for s in specs if s.kind == "fact"), "new_dimension_tables": sum(1 for s in specs if s.kind == "dim"),
        "declared_foreign_keys": sum(len(list(con.execute(f"PRAGMA foreign_key_list({qident(t)})"))) for t in tables),
        "indexes": con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'").fetchone()[0],
        "integrity_check": integrity, "foreign_key_violations": fk_by_table,
        "metadata": {"table_records": len(table_meta), "column_records": len(col_meta)},
        "deidentification": extra["deidentification"], "seeded_base_empty_tables": extra["seeded"],
        "header_aggregation": extra["headers"], "dirty_data": extra["dirty"],
        "validation_cases": {c["case_type"]: c["expected_count"] for c in extra["cases"]},
        "semantic_checks": semantic, "business_plaintext_scan": plaintext_scan, "hard_checks": hard_checks,
    }


def build(base: Path, output: Path, report: Path, dictionary: Path, force: bool, seed: int) -> dict[str, Any]:
    base, output = base.resolve(), output.resolve()
    if not base.is_file():
        raise FileNotFoundError(f"base database not found: {base}")
    if output == base:
        raise ValueError("output must not overwrite the 108-table source database")
    if output.exists() and not force:
        raise FileExistsError(f"output exists; use --force to replace the generated destination: {output}")
    for p in (output, report, dictionary):
        p.parent.mkdir(parents=True, exist_ok=True)
    base_hash = sha256_file(base)
    specs = table_specs()
    by_name = {s.name: s for s in specs}
    tmp_handle = tempfile.NamedTemporaryFile(prefix="contract_200_", suffix=".db", dir=output.parent, delete=False)
    tmp_path = Path(tmp_handle.name)
    tmp_handle.close()
    dest = None
    try:
        source = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
        dest = sqlite3.connect(tmp_path)
        source.backup(dest)
        source.close()
        dest.execute("PRAGMA foreign_keys=OFF")
        dest.execute("PRAGMA journal_mode=DELETE")
        deidentification = deidentify_base_plaintext(dest)
        seeded = seed_empty_base_tables(dest)
        dest.commit()
        collisions = set(user_tables(dest)) & set(by_name)
        if collisions:
            raise AssertionError(f"new tables collide with the base database: {sorted(collisions)}")
        for spec in specs:
            create_table(dest, spec)
        ctx = SEM.Context(dest)
        rng = random.Random(seed)
        governance = ("dim_dataset_release", "dim_validation_case", "fact_data_quality_issue", "fact_validation_case_result")
        for spec in specs:
            if spec.kind == "dim" and spec.name not in governance:
                seed_dimension(dest, spec, ctx, rng)
        for spec in specs:
            if spec.kind == "fact" and spec.name not in governance:
                seed_fact(dest, spec, ctx, seed)
        headers = aggregate_headers(dest, ctx)
        dirty = inject_dirty_data(dest, seed)
        refresh_metadata(dest, specs)                       # 案例 METADATA_COMPLETENESS 需要元数据先就位
        cases = build_cases(dest, specs, dirty)
        rel = by_name["dim_dataset_release"]
        rel.rows = 1
        seed_dimension(dest, rel, ctx, rng, [{"_release": {
            "release_name": "contract-validation-200-v2", "generator_version": GENERATOR_VERSION, "random_seed": seed,
            "base_sha256": base_hash, "table_count": TARGET_TABLES, "column_count": None, "release_date": "2026-10-03",
            "owner_team": "本体评测"}}])
        vc = by_name["dim_validation_case"]
        vc.rows = len(cases)
        seed_dimension(dest, vc, ctx, rng, [{"_case": c} for c in cases])
        dest.execute("UPDATE dim_dataset_release SET column_count=(SELECT SUM(n) FROM (SELECT COUNT(*) n FROM sqlite_master m, pragma_table_info(m.name) WHERE m.type='table' AND m.name NOT LIKE 'sqlite_%'))")
        ctx.invalidate("dim_dataset_release")
        ctx.invalidate("dim_validation_case")
        # 治理事实表:每个案例一条执行结果;每个登记的已知问题与脏数据一条数据质量问题
        case_ids = {r[1]: r[0] for r in dest.execute("SELECT validation_case_id, case_type FROM dim_validation_case")}
        results = []
        for c in cases:
            actual = dest.execute(c["detection_rule"]).fetchone()[0] or 0
            results.append({"dataset_release_id": 1, "validation_case_id": case_ids[c["case_type"]], "rule_code": c["case_type"],
                            "check_name": c["expected_behavior"], "severity": 3 if c["origin"] == "原108表" else 2,
                            "detected_at": "2026-10-03 09:00:00", "resolved_at": "2026-10-03 09:05:00",
                            "expected_value": str(c["expected_count"]), "actual_value": str(actual), "exception_count": int(actual),
                            "owner_team": c["owner_team"], "resolution_status": "PASSED" if actual == c["expected_count"] else "FAILED",
                            "generator_version": GENERATOR_VERSION, "random_seed": seed,
                            "evidence_path": f"validation_report.json#validation_cases.{c['case_type']}",
                            "validation_query": c["detection_rule"]})
        issues = [r | {"resolution_status": "OPEN" if c["origin"] == "原108表" else "ACCEPTED"}
                  for r, c in zip(results, cases) if c["origin"] != "刻意构造" or c["case_type"] in ("LEGACY_UNRESOLVED_CODE", "SOFT_DELETED_ROWS", "INACTIVE_MASTER_DATA")]
        for spec_name, gov in (("fact_validation_case_result", results), ("fact_data_quality_issue", issues)):
            spec = by_name[spec_name]
            spec.rows = len(gov)
            seed_fact(dest, spec, ctx, seed, gov_rows=gov)
        refresh_metadata(dest, specs)
        dest.commit()
        validation = validate_database(dest, specs, {"deidentification": deidentification, "seeded": seeded, "headers": headers,
                                                      "dirty": dirty, "cases": cases}, seed)
        if not validation["ok"]:
            failed = {k: v for k, v in validation["semantic_checks"].items() if not v["pass"]}
            raise RuntimeError("generated database failed validation: " + json.dumps(
                {"hard_checks": validation["hard_checks"], "failed_semantic_checks": failed,
                 "foreign_key_violations": validation["foreign_key_violations"]}, ensure_ascii=False, default=str))
        write_dictionary(dest, dictionary)
        dest.execute("VACUUM")
        dest.close()
        dest = None
        if output.exists():
            output.unlink()
        os.replace(tmp_path, output)
        validation.update({"database_path": str(output), "database_bytes": output.stat().st_size, "database_sha256": sha256_file(output),
                           "base_database_path": str(base), "base_database_sha256": base_hash, "dictionary_path": str(dictionary)})
        report.write_text(json.dumps(validation, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return validation
    except Exception:
        if dest is not None:
            try:
                dest.close()
            except Exception:
                pass
        if tmp_path.exists():
            if os.environ.get("CONTRACT200_KEEP_FAILED"):      # 调试:保留未通过校验的库
                print(f"kept failed build: {tmp_path}", file=sys.stderr)
            else:
                tmp_path.unlink()
        raise


def scan_delivery_plaintext(con: sqlite3.Connection) -> dict[str, int]:
    """Scan text values for direct identifiers and named-party residues."""
    patterns = {
        "mainland_mobile": re.compile(r"(?<![A-Za-z0-9])1[3-9][0-9]{9}(?![A-Za-z0-9])"),
        "cn_identity_number": re.compile(
            r"(?<![A-Za-z0-9])[0-9]{17}[0-9Xx](?![A-Za-z0-9])"
        ),
        "long_numeric_account": re.compile(
            r"(?<![A-Za-z0-9])[0-9]{16,19}(?![A-Za-z0-9])"
        ),
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
                    if pattern.search(value):
                        counts[name] += 1
                if any(token in value for token in banned_tokens):
                    counts["named_party_residue"] += 1
    return counts


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
