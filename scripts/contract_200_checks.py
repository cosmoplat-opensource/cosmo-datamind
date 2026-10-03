#!/usr/bin/env python3
"""Post-generation semantic validation for the contract-aligned 200-table database (generator v2).

The v1 generator only validated per-row formats; an independent audit then found
that joined facts contradicted each other (shipment lines vs. order lines,
sensors vs. equipment, departments vs. employees), header totals did not match
their lines, 614 columns held placeholder strings and every fact table repeated
the same measure sequence.  Each audit item is a check here; the generator
refuses to publish a database that fails any of them.  Also usable standalone:

    python3 scripts/contract_200_checks.py path/to/contract_200.db

Standard library only.
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
from typing import Any

import contract_200_semantics as S

# ───────── 跨实体一致性:同一业务事实经两条路径取到的属性必须一致(不一致行数须为 0) ─────────
CROSS_ENTITY = {
    "shipment_line_product_matches_order_line": "SELECT COUNT(*) FROM fact_shipment_line sl JOIN fact_sales_order_line ol ON sl.sales_order_line_id=ol.sales_order_line_id WHERE sl.prod_id<>ol.prod_id",
    "shipment_line_belongs_to_shipment_order": "SELECT COUNT(*) FROM fact_shipment_line sl JOIN fact_shipment s ON sl.shipment_id=s.shipment_id JOIN fact_delivery d ON s.delivery_id=d.delivery_id JOIN fact_sales_order_line ol ON sl.sales_order_line_id=ol.sales_order_line_id WHERE ol.order_id<>d.order_id",
    "shipment_customer_matches_order": "SELECT COUNT(*) FROM fact_shipment s JOIN fact_delivery d ON s.delivery_id=d.delivery_id JOIN fact_sales_order o ON d.order_id=o.order_id WHERE s.cust_id<>o.cust_id",
    "order_line_product_matches_order": "SELECT COUNT(*) FROM fact_sales_order_line l JOIN fact_sales_order o ON l.order_id=o.order_id WHERE l.prod_id<>o.prod_id",
    "complaint_customer_matches_order": "SELECT COUNT(*) FROM fact_customer_complaint c JOIN fact_sales_order o ON c.order_id=o.order_id WHERE c.cust_id<>o.cust_id OR c.prod_id<>o.prod_id",
    "price_adjustment_product_matches_pricing": "SELECT COUNT(*) FROM fact_price_adjustment a JOIN dim_pricing p ON a.pricing_id=p.pricing_id WHERE a.prod_id<>p.product_id",
    "production_children_product_line_match_order": "SELECT (SELECT COUNT(*) FROM fact_inspection_lot i JOIN fact_production_order p ON i.order_id=p.order_id WHERE i.prod_id<>p.prod_id)"
        " + (SELECT COUNT(*) FROM fact_scrap_event s JOIN fact_production_order p ON s.order_id=p.order_id WHERE s.prod_id<>p.prod_id)"
        " + (SELECT COUNT(*) FROM fact_work_order_operation w JOIN fact_production_order p ON w.order_id=p.order_id WHERE w.line_id<>p.line_id)"
        " + (SELECT COUNT(*) FROM fact_wip_snapshot w JOIN fact_production_order p ON w.order_id=p.order_id WHERE w.line_id<>p.line_id)",
    "operation_in_order_product_routing": "SELECT COUNT(*) FROM fact_work_order_operation w JOIN fact_production_order p ON w.order_id=p.order_id JOIN dim_operation op ON w.operation_id=op.operation_id JOIN dim_routing r ON op.routing_id=r.routing_id WHERE r.product_id<>p.prod_id",
    "parameter_equipment_on_order_line": "SELECT COUNT(*) FROM fact_process_parameter_record r JOIN fact_production_order p ON r.order_id=p.order_id JOIN dim_equipment e ON r.equipment_id=e.equipment_id WHERE e.line_id<>p.line_id",
    "parameter_fits_equipment_type": "SELECT COUNT(*) FROM fact_process_parameter_record r JOIN dim_equipment e ON r.equipment_id=e.equipment_id JOIN dim_process_parameter pp ON r.process_parameter_id=pp.process_parameter_id WHERE pp.type_id<>e.type_id",
    "sensor_mounted_on_equipment": "SELECT COUNT(*) FROM (SELECT equipment_id, sensor_id FROM fact_predictive_alert UNION ALL SELECT equipment_id, sensor_id FROM fact_condition_monitoring) a JOIN dim_sensor s ON a.sensor_id=s.sensor_id WHERE s.equipment_id<>a.equipment_id",
    "spare_part_fits_equipment_type": "SELECT COUNT(*) FROM fact_spare_part_issue i JOIN dim_spare_part sp ON i.part_id=sp.part_id JOIN dim_equipment e ON i.equipment_id=e.equipment_id WHERE sp.equipment_type_id<>e.type_id",
    "asn_line_belongs_to_asn_po": "SELECT COUNT(*) FROM fact_asn_line l JOIN fact_advance_shipping_notice a ON l.advance_shipping_notice_id=a.advance_shipping_notice_id JOIN fact_po_line pl ON l.po_line_id=pl.line_id WHERE pl.po_id<>a.po_id OR l.material_id<>pl.material_id",
    "asn_supplier_matches_po": "SELECT COUNT(*) FROM fact_advance_shipping_notice a JOIN fact_purchase_order p ON a.po_id=p.po_id WHERE a.supplier_id<>p.supplier_id",
    "material_supplied_by_party": "SELECT (SELECT COUNT(*) FROM fact_supplier_contract_line l JOIN fact_supplier_contract c ON l.supplier_contract_id=c.supplier_contract_id JOIN dim_material m ON l.material_id=m.material_id WHERE m.supplier_id<>c.supplier_id)"
        " + (SELECT COUNT(*) FROM fact_supplier_quality_issue q JOIN dim_material m ON q.material_id=m.material_id WHERE m.supplier_id<>q.supplier_id)"
        " + (SELECT COUNT(*) FROM fact_supplier_claim q JOIN dim_material m ON q.material_id=m.material_id WHERE m.supplier_id<>q.supplier_id)",
    "storage_location_in_warehouse": "SELECT COUNT(*) FROM fact_inventory_movement m JOIN dim_storage_location l ON m.location_id=l.location_id WHERE l.warehouse_id<>m.warehouse_id",
    "employee_department_consistent": "SELECT (SELECT COUNT(*) FROM fact_employee_assignment a JOIN dim_employee e ON a.emp_id=e.emp_id WHERE a.dept_id<>e.dept_id)"
        " + (SELECT COUNT(*) FROM fact_employee_assignment a JOIN dim_job_position j ON a.job_position_id=j.job_position_id WHERE j.dept_id<>a.dept_id)"
        " + (SELECT COUNT(*) FROM fact_overtime o JOIN dim_employee e ON o.emp_id=e.emp_id WHERE o.dept_id<>e.dept_id)"
        " + (SELECT COUNT(*) FROM fact_kpi_snapshot k JOIN dim_employee e ON k.emp_id=e.emp_id WHERE k.dept_id<>e.dept_id)",
    "inspection_characteristic_of_lot_product": "SELECT COUNT(*) FROM fact_inspection_result r JOIN fact_inspection_lot il ON r.inspection_lot_id=il.inspection_lot_id JOIN dim_quality_characteristic qc ON r.quality_characteristic_id=qc.quality_characteristic_id WHERE qc.prod_id<>il.prod_id",
    "material_in_order_product_bom": "SELECT COUNT(*) FROM (SELECT order_id, material_id FROM fact_work_order_material UNION ALL SELECT order_id, material_id FROM fact_lot_trace) w JOIN fact_production_order p ON w.order_id=p.order_id WHERE NOT EXISTS (SELECT 1 FROM dim_bom b JOIN dim_bom_line bl ON b.bom_id=bl.bom_id WHERE b.product_id=p.prod_id AND bl.material_id=w.material_id)",
    "shift_matches_line_workshop": "SELECT (SELECT COUNT(*) FROM fact_shift_report f JOIN dim_production_line l ON f.line_id=l.line_id JOIN dim_shift s ON f.shift_id=s.shift_id WHERE s.workshop_id<>l.workshop_id)"
        " + (SELECT COUNT(*) FROM fact_capacity_plan f JOIN dim_production_line l ON f.line_id=l.line_id JOIN dim_shift s ON f.shift_id=s.shift_id WHERE s.workshop_id<>l.workshop_id)"
        " + (SELECT COUNT(*) FROM fact_labor_booking f JOIN fact_production_order p ON f.order_id=p.order_id JOIN dim_production_line l ON p.line_id=l.line_id JOIN dim_shift s ON f.shift_id=s.shift_id WHERE s.workshop_id<>l.workshop_id)",
    "gl_first_line_account_matches_header": "SELECT COUNT(*) FROM fact_gl_entry_line l JOIN fact_gl_entry h ON l.gl_entry_id=h.gl_entry_id WHERE l.line_no=1 AND l.account_id<>h.account_id",
    "cash_flow_single_counterparty": "SELECT COUNT(*) FROM fact_cash_flow WHERE (cust_id IS NULL)=(supplier_id IS NULL)",
    "transfer_roles_distinct": "SELECT (SELECT COUNT(*) FROM fact_transfer_order WHERE source_warehouse_id=target_warehouse_id) + (SELECT COUNT(*) FROM fact_fixed_asset_transfer WHERE source_factory_id=target_factory_id)",
    "risk_level_matches_score": "SELECT COUNT(*) FROM (SELECT risk_level_id, severity*likelihood s FROM fact_hazard_observation UNION ALL SELECT risk_level_id, severity*likelihood FROM fact_safety_action) x JOIN dim_risk_level r ON x.risk_level_id=r.level_id WHERE (SELECT COUNT(*) FROM dim_risk_level r2 WHERE r2.level_id < r.level_id) <> (CASE WHEN x.s<=4 THEN 0 WHEN x.s<=9 THEN 1 WHEN x.s<=15 THEN 2 ELSE 3 END)",
}

# ───────── 单据头与明细 ─────────
HEADER_LINES = [
    ("fact_shipment", "fact_shipment_line", "shipment_id", True),
    ("fact_cycle_count", "fact_cycle_count_line", "cycle_count_id", False),
    ("fact_transfer_order", "fact_transfer_order_line", "transfer_order_id", True),
    ("fact_supplier_contract", "fact_supplier_contract_line", "supplier_contract_id", True),
    ("fact_advance_shipping_notice", "fact_asn_line", "advance_shipping_notice_id", True),
]

ALLOWED_ACRONYMS = {"ID", "ETL", "KPI", "PO", "SKU", "BOM", "ERP", "MES", "WMS", "QMS", "EAM", "CRM", "SRM", "HRM", "APS",
                    "EHS", "DQ", "MDM", "FIN", "CLM", "BI", "PLC", "SPC", "OEE", "SHA", "ASN", "WIP", "GL", "CC", "PC", "IT"}
PLACEHOLDER = re.compile(r"^[A-Za-z_]+:[a-z_]+:\d{4}$")


def _one(con: sqlite3.Connection, sql: str) -> Any:
    return con.execute(sql).fetchone()[0]


def _cols(con: sqlite3.Connection, t: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]


def run(con: sqlite3.Connection, new_tables: dict[str, Any], known: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回 {检查名: {"value", "expected", "pass"}};new_tables 为 {表名: TableSpec}。"""
    out: dict[str, dict[str, Any]] = {}

    def put(name: str, value: Any, ok: bool, expected: Any = 0) -> None:
        out[name] = {"value": value, "expected": expected, "pass": bool(ok)}

    facts = sorted(t for t, s in new_tables.items() if s.kind == "fact")
    dims = sorted(t for t, s in new_tables.items() if s.kind == "dim")

    # 1 跨实体一致性
    for name, sql in CROSS_ENTITY.items():
        v = _one(con, sql)
        put(f"cross_entity.{name}", v, v == 0)

    # 2 单据头与明细
    for h, l, k, money in HEADER_LINES:
        put(f"header_lines.{h}.headers_without_lines", _one(con, f'SELECT COUNT(*) FROM "{h}" WHERE {k} NOT IN (SELECT {k} FROM "{l}")'),
            _one(con, f'SELECT COUNT(*) FROM "{h}" WHERE {k} NOT IN (SELECT {k} FROM "{l}")') == 0)
        v = _one(con, f'SELECT COUNT(*) FROM "{h}" h JOIN (SELECT {k}, SUM(quantity) q FROM "{l}" GROUP BY {k}) x USING({k}) WHERE ABS(h.quantity-x.q)>0.001')
        put(f"header_lines.{h}.quantity_equals_line_sum", v, v == 0)
        if money:
            v = _one(con, f'SELECT COUNT(*) FROM "{h}" h JOIN (SELECT {k}, SUM(amount) a FROM "{l}" GROUP BY {k}) x USING({k}) WHERE ABS(h.amount-x.a)>0.011')
            put(f"header_lines.{h}.amount_equals_line_sum", v, v == 0)
            v = _one(con, f'SELECT COUNT(*) FROM "{l}" l JOIN "{h}" h USING({k}) WHERE l.currency_code<>h.currency_code')
            put(f"header_lines.{h}.line_currency_equals_header", v, v == 0)
        v = _one(con, f'SELECT COUNT(*) FROM "{l}" l JOIN "{h}" h USING({k}) WHERE l.business_date<h.business_date')
        put(f"header_lines.{h}.line_not_before_header", v, v == 0)
    for l, k in [(t, S.FACT_SEMANTICS[t]["line_of"][0]) for t in facts if S.FACT_SEMANTICS[t].get("line_of")]:
        v = _one(con, f'SELECT COUNT(*) FROM (SELECT {k}, COUNT(*) n, MIN(line_no) a, MAX(line_no) b, COUNT(DISTINCT line_no) d FROM "{l}" GROUP BY {k}) WHERE a<>1 OR b<>n OR d<>n')
        put(f"line_numbers.{l}.sequential_unique", v, v == 0)
    v = _one(con, "SELECT COUNT(*) FROM (SELECT gl_entry_id FROM fact_gl_entry_line GROUP BY gl_entry_id HAVING ABS(SUM(debit_amount)-SUM(credit_amount))>0.01)")
    put("header_lines.gl_entry_balanced", v, v == 0)
    v = _one(con, "SELECT COUNT(*) FROM (SELECT order_id, SUM(quantity) q, SUM(amount) a FROM fact_sales_order_line GROUP BY order_id) x JOIN fact_sales_order o USING(order_id) WHERE ABS(x.q-o.quantity)>0.001 OR ABS(x.a-o.amount)>0.011")
    put("header_lines.sales_order_lines_split_header", v, v == 0)
    v = _one(con, "SELECT COUNT(*) FROM (SELECT invoice_id, SUM(amount) a FROM fact_tax_invoice_line GROUP BY invoice_id) x JOIN fact_invoice i USING(invoice_id) WHERE ABS(x.a-i.amount_no_tax)>0.011")
    put("header_lines.tax_invoice_lines_split_invoice", v, v == 0)

    # 3 占位串与恒定列
    ph = []
    const, total = [], 0
    for t in facts + dims:
        for c in _cols(con, t):
            total += 1
            sample = [r[0] for r in con.execute(f'SELECT "{c}" FROM "{t}" WHERE "{c}" IS NOT NULL LIMIT 50')]
            if any(isinstance(x, str) and PLACEHOLDER.match(x) for x in sample):
                ph.append(f"{t}.{c}")
            if _one(con, f'SELECT COUNT(DISTINCT "{c}") FROM "{t}"') <= 1 and _one(con, f'SELECT COUNT(*) FROM "{t}"') > 1:
                const.append(f"{t}.{c}")
    put("values.placeholder_columns", len(ph), not ph)
    # 设计上恒定的列:单租户、每表固定的来源系统/计量单位/币种/移动类型、单一会计科目表与生成批次、
    # 扁平维表的层级字段、主数据的固定说明性取值、非应税业务税额为 0、未抽中逻辑删除的表
    allowed_const = {"tenant_code", "source_system", "is_deleted", "approval_status", "hierarchy_level", "parent_code", "category_code",
                     "type_code", "status", "effective_to", "unit_code", "currency_code", "exchange_rate", "movement_type", "coa_id",
                     "dataset_release_id", "detected_at", "resolved_at", "generator_version", "random_seed", "resolution_status",
                     "tax_rate", "tax_amount", "tax_code_id", "enterprise_id", "hazard_type", "stock_status", "target_value",
                     "period_type", "tolerance", "setup_minutes", "country_code", "calibration_required", "reporting_standard",
                     "timezone_name", "rate_source", "reference_standard", "tax_type", "legal_basis", "accounting_scope",
                     "installment_count", "applies_to", "incoterm_version", "usage_scope", "measurement_method", "precision_scale",
                     "acceptance_rule", "responsible_role", "market_scope", "depreciation_method", "residual_rate",
                     "capitalization_threshold", "gl_account_code", "medium_type", "proficiency_levels", "renewal_required",
                     "collection_method", "control_method", "reaction_plan", "tolerance_value", "remarks",
                     "routing_version"}                       # 原表工艺路线版本全为 V1.0,沿用
    bad_const = [c for c in const if c.split(".", 1)[1] not in allowed_const]
    put("values.unexpected_constant_columns", len(bad_const), not bad_const)
    put("values.constant_column_ratio", round(len(const) / max(1, total), 4), True, "仅报告")
    out["values.unexpected_constant_columns"]["detail"] = bad_const[:30]
    out["values.placeholder_columns"]["detail"] = ph[:30]

    # 4 度量不在各表间重复,引用不是机械轮转
    seqs: dict[tuple, list[str]] = {}
    for t in facts:
        cs = _cols(con, t)
        for m in ("quantity", "amount"):
            if m in cs:
                key = (m, tuple(r[0] for r in con.execute(f'SELECT "{m}" FROM "{t}" ORDER BY rowid LIMIT 20')))
                seqs.setdefault(key, []).append(t)
    dup = [ts for ts in seqs.values() if len(ts) > 1]
    put("values.measure_sequences_repeated_across_tables", len(dup), not dup)
    uniform = []
    for t, s in new_tables.items():
        if s.kind != "fact" or s.rows < 100:
            continue
        line_key = (S.FACT_SEMANTICS.get(t, {}).get("line_of") or (None,))[0]
        for ref in s.refs:
            if ref.column == line_key:
                continue                                    # 明细按表头分配是设计如此(如每张订单两条明细)
            counts = [r[0] for r in con.execute(f'SELECT COUNT(*) FROM "{t}" WHERE "{ref.column}" IS NOT NULL GROUP BY "{ref.column}"')]
            if len(counts) >= 5 and statistics.mean(counts) > 0 and statistics.pstdev(counts) / statistics.mean(counts) < 0.1:
                uniform.append(f"{t}.{ref.column}")
    put("values.round_robin_reference_columns", len(uniform), len(uniform) <= 3, "<=3")
    out["values.round_robin_reference_columns"]["detail"] = uniform[:20]

    # 5 单位、币种与公式
    bad_unit = 0
    for t in facts:
        sem = S.FACT_SEMANTICS[t]
        if "Q" not in sem["groups"]:
            continue
        if sem.get("unit") == "BY_ENERGY":
            bad_unit += _one(con, f'SELECT COUNT(*) FROM "{t}" f JOIN dim_energy_type e ON f.energy_type_id=e.energy_type_id WHERE f.unit_code<>e.measurement_unit')
        else:
            bad_unit += _one(con, f'SELECT COUNT(*) FROM "{t}" WHERE unit_code<>\'{sem.get("unit", "EA")}\'')
    put("semantics.unit_matches_table_meaning", bad_unit, bad_unit == 0)
    bad_fx = sum(_one(con, f'SELECT COUNT(*) FROM "{t}" f JOIN dim_currency c ON f.currency_code=c.code WHERE ABS(f.exchange_rate-c.rate_to_cny)>1e-9 OR ABS(f.local_amount-ROUND(f.amount*f.exchange_rate,2))>0.011')
                 for t in facts if "M" in S.FACT_SEMANTICS[t]["groups"])
    put("semantics.local_amount_uses_currency_rate", bad_fx, bad_fx == 0)
    bad_amt = sum(_one(con, f'SELECT COUNT(*) FROM "{t}" WHERE ABS(amount-quantity*unit_price) > 0.02 + 0.0001*amount')
                  for t in facts if {"Q", "M"} <= set(S.FACT_SEMANTICS[t]["groups"]) and t not in ("fact_sales_order_line", "fact_shipment", "fact_supplier_contract",
                                                                                                   "fact_transfer_order", "fact_advance_shipping_notice"))
    put("semantics.amount_equals_quantity_times_price", bad_amt, bad_amt == 0)
    bad_dec = sum(_one(con, f'SELECT COUNT(*) FROM "{t}" WHERE qualified_quantity+rejected_quantity>actual_quantity+1e-6')
                  for t in facts if "Q" in S.FACT_SEMANTICS[t]["groups"])
    put("semantics.quality_decomposition", bad_dec, bad_dec == 0)
    bad_prob = _one(con, "SELECT COUNT(*) FROM fact_lead WHERE probability <> (CASE opportunity_stage WHEN '线索' THEN 0.1 WHEN '需求确认' THEN 0.25 WHEN '方案报价' THEN 0.45 WHEN '商务谈判' THEN 0.7 WHEN '赢单' THEN 1.0 ELSE 0.0 END)")
    put("semantics.probability_matches_stage", bad_prob, bad_prob == 0)
    bad_res = _one(con, "SELECT COUNT(*) FROM fact_inspection_result WHERE (measured_value BETWEEN lower_limit AND upper_limit) <> (result_code='合格')")
    put("semantics.inspection_result_matches_limits", bad_res, bad_res == 0)
    bad_risk = _one(con, "SELECT COUNT(*) FROM (SELECT risk_score, severity, likelihood FROM fact_hazard_observation UNION ALL SELECT risk_score, severity, likelihood FROM fact_safety_action) WHERE risk_score<>severity*likelihood")
    put("semantics.risk_score_formula", bad_risk, bad_risk == 0)
    bad_exc = sum(_one(con, f'SELECT COUNT(*) FROM "{t}" WHERE limit_value>0 AND exceed_flag<>(emission_value>limit_value)') for t in ("fact_environment_measurement", "fact_emission_event"))
    put("semantics.exceed_flag_matches_limit", bad_exc, bad_exc == 0)
    bad_meter = _one(con, "SELECT COUNT(*) FROM fact_energy_meter_reading WHERE ABS((meter_end-meter_start)-quantity)>0.11")
    put("semantics.meter_delta_equals_quantity", bad_meter, bad_meter == 0)
    bad_shift = _one(con, "SELECT COUNT(*) FROM dim_shift WHERE shift_start NOT GLOB '[0-2][0-9]:[0-5][0-9]' OR shift_end NOT GLOB '[0-2][0-9]:[0-5][0-9]'")
    put("semantics.shift_times_are_clock_times", bad_shift, bad_shift == 0)
    per_ws = [r[0] for r in con.execute("SELECT COUNT(*) FROM dim_shift GROUP BY workshop_id")]
    put("semantics.each_workshop_has_three_shifts", per_ws, bool(per_ws) and all(n == 3 for n in per_ws), "3 each")

    # 6 日期逻辑
    bad_dates = 0
    for t in dims:
        cs = _cols(con, t)
        bad_dates += _one(con, f'SELECT COUNT(*) FROM "{t}" WHERE date(effective_to)<date(effective_from) OR datetime(updated_at)<datetime(created_at) OR datetime(approved_at)<datetime(created_at)')
        if "planned_start_date" in cs:
            bad_dates += _one(con, f'SELECT COUNT(*) FROM "{t}" WHERE date(planned_end_date)<date(planned_start_date)')
        if t != "dim_calendar":
            bad_dates += _one(con, f'SELECT COUNT(*) FROM "{t}" WHERE datetime(created_at) >= datetime(\'2025-01-01\')')
    for t in facts:
        cs = _cols(con, t)
        bad_dates += _one(con, f'SELECT COUNT(*) FROM "{t}" WHERE date(posting_date)<date(business_date) OR datetime(updated_at)<datetime(created_at) OR (approved_at IS NOT NULL AND datetime(approved_at)<datetime(created_at))')
        for a, b in (("start_time", "end_time"), ("manufacture_date", "expiry_date"), ("requested_date", "promised_date"),
                     ("requested_delivery_date", "promised_delivery_date"), ("business_date", "closed_date"), ("business_date", "due_date"),
                     ("valid_from", "expiry_date"), ("business_date", "expected_close_date")):
            if a in cs and b in cs:
                bad_dates += _one(con, f'SELECT COUNT(*) FROM "{t}" WHERE "{b}" IS NOT NULL AND julianday("{b}")<julianday("{a}")')
    put("dates.order_violations", bad_dates, bad_dates == 0)
    out_of_cal = sum(_one(con, f'SELECT COUNT(*) FROM "{t}" WHERE business_date NOT IN (SELECT calendar_date FROM dim_calendar)') for t in facts)
    put("dates.business_date_in_calendar", out_of_cal, out_of_cal == 0)

    # 7 元数据
    bad_cn = []
    for t, c, cn in con.execute("SELECT table_name, column_name, column_name_cn FROM dim_column_metadata"):
        if t in new_tables:
            tokens = re.findall(r"[A-Za-z]{2,}", cn or "")
            if [x for x in tokens if x.upper() not in ALLOWED_ACRONYMS] or not cn:
                bad_cn.append(f"{t}.{c}={cn}")
    put("metadata.untranslated_column_names", len(bad_cn), not bad_cn)
    out["metadata.untranslated_column_names"]["detail"] = bad_cn[:20]
    descs = [r[0] for r in con.execute("SELECT table_desc FROM dim_table_metadata WHERE table_name IN (%s)" % ",".join("'%s'" % t for t in new_tables))]
    put("metadata.distinct_table_descriptions", len(set(descs)), len(set(descs)) == len(new_tables), len(new_tables))
    bad_line = _one(con, "SELECT COUNT(*) FROM dim_column_metadata WHERE column_name='line_no' AND column_name_cn<>'行号' AND table_name IN (%s)" % ",".join("'%s'" % t for t in new_tables))
    put("metadata.line_no_named_row_number", bad_line, bad_line == 0)
    bad_ref = 0
    for t, s in new_tables.items():
        for ref in s.refs:
            cn = _one(con, f"SELECT column_name_cn FROM dim_column_metadata WHERE table_name='{t}' AND column_name='{ref.column}'")
            pcn = _one(con, f"SELECT table_name_cn FROM dim_table_metadata WHERE table_name='{ref.parent_table}'")
            if pcn and pcn not in (cn or ""):
                bad_ref += 1
    put("metadata.reference_column_names_mention_parent", bad_ref, bad_ref == 0)

    # 8 验证案例的自述与登记的已知问题
    cases = list(con.execute("SELECT code, case_type, detection_rule, expected_count, tolerance_value FROM dim_validation_case"))
    mismatched = []
    for code, ctype, rule, expected, tol in cases:
        try:
            actual = _one(con, rule)
        except sqlite3.Error as exc:
            mismatched.append(f"{code}:{exc}")
            continue
        if abs((actual or 0) - (expected or 0)) > (tol or 0):
            mismatched.append(f"{code}:{ctype} 期望 {expected} 实际 {actual}")
    put("cases.claims_match_data", len(mismatched), not mismatched)
    out["cases.claims_match_data"]["detail"] = mismatched[:20]
    res = _one(con, "SELECT COUNT(*) FROM fact_validation_case_result r JOIN dim_validation_case c ON r.validation_case_id=c.validation_case_id WHERE CAST(r.actual_value AS REAL)<>c.expected_count OR r.resolution_status<>'PASSED'")
    put("cases.results_recorded", res, res == 0)
    put("cases.one_result_per_case", _one(con, "SELECT COUNT(*) FROM fact_validation_case_result"), _one(con, "SELECT COUNT(*) FROM fact_validation_case_result") == len(cases), len(cases))
    return out


def main() -> int:
    import importlib.util
    from pathlib import Path
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("gen", here / "build_contract_200_db.py")
    assert spec is not None and spec.loader is not None
    gen = importlib.util.module_from_spec(spec)
    sys.modules["gen"] = gen
    spec.loader.exec_module(gen)
    con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    result = run(con, {s.name: s for s in gen.table_specs()})
    failed = {k: v for k, v in result.items() if not v["pass"]}
    print(json.dumps({"checks": len(result), "failed": failed}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
