#!/usr/bin/env python3
"""Post-generation semantic validation for the contract-aligned 200-table database (generator v3).

v1 only validated per-row formats; the v2 audit found joined facts contradicting each other,
so v2 added cross-entity, header/line and format checks.  The second audit found that rows were
still sampled independently (child dates outside parent windows, quantities not derived from
parents, no running state), so v3 derives the synthesized tables from the events of the
108-table base and ``EVENT_CHECKS`` reconciles them with that base: shipments with deliveries
and produced batches, vouchers with documents, meter readings with daily consumption, stock
snapshots with movements, labour and operations with production orders, attendance with the
calendar.  The generator refuses to publish a database that fails any check.  Standalone:

    python3 scripts/contract_200_checks.py path/to/contract_200.db

Standard library only.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from typing import Any

import contract_200_semantics as S

# ───────── 跨实体一致性:同一业务事实经两条路径取到的属性必须一致(不一致行数须为 0) ─────────
CROSS_ENTITY = {
    "shipment_line_product_matches_order_line": "SELECT COUNT(*) FROM fact_shipment_line sl JOIN fact_sales_order_line ol ON sl.sales_order_line_id=ol.sales_order_line_id WHERE sl.prod_id<>ol.prod_id",
    "shipment_line_belongs_to_shipment_order": "SELECT COUNT(*) FROM fact_shipment_line sl JOIN fact_shipment s ON sl.shipment_id=s.shipment_id JOIN fact_delivery d ON s.delivery_id=d.delivery_id JOIN fact_sales_order_line ol ON sl.sales_order_line_id=ol.sales_order_line_id WHERE ol.order_id<>d.order_id",
    "shipment_customer_matches_order": "SELECT COUNT(*) FROM fact_shipment s JOIN fact_delivery d ON s.delivery_id=d.delivery_id JOIN fact_sales_order o ON d.order_id=o.order_id WHERE s.cust_id<>o.cust_id",
    "order_line_region_and_contract_match_customer": "SELECT COUNT(*) FROM fact_sales_order_line l JOIN fact_sales_order o USING(order_id) "
        "WHERE l.contract_no IS NOT NULL AND l.contract_no NOT IN (SELECT contract_no FROM fact_contract c WHERE c.party_id=o.cust_id AND c.contract_type='sales')",
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
    "gauge_sensor_on_equipment": "SELECT COUNT(*) FROM fact_gauge_calibration g JOIN dim_sensor s ON g.sensor_id=s.sensor_id WHERE s.equipment_id<>g.equipment_id",
    "hazard_observed_in_its_workshop": "SELECT COUNT(*) FROM fact_hazard_observation o JOIN dim_hazard h ON o.hazard_id=h.hazard_id JOIN dim_workshop w ON o.workshop_id=w.workshop_id WHERE instr(h.location, w.workshop_name)=0",
    "labor_booked_by_team_leader_of_line": "SELECT COUNT(*) FROM fact_labor_booking b JOIN fact_production_order p ON b.order_id=p.order_id WHERE b.emp_id NOT IN (SELECT leader_id FROM dim_team t WHERE t.line_id=p.line_id)",
    "tool_on_order_line": "SELECT COUNT(*) FROM fact_tool_usage u JOIN fact_production_order p ON u.order_id=p.order_id JOIN dim_tool t ON u.tool_id=t.tool_id WHERE t.line_id<>p.line_id",
    "shipment_warehouse_is_product_fg_store": "SELECT COUNT(*) FROM fact_shipment s JOIN dim_warehouse w ON s.warehouse_id=w.warehouse_id WHERE w.warehouse_type<>'finished'",
    "inventory_flows_in_raw_stores": "SELECT COUNT(*) FROM fact_inventory_movement m JOIN dim_warehouse w ON m.warehouse_id=w.warehouse_id WHERE w.warehouse_type<>'raw'",
    "cash_flow_at_most_one_counterparty": "SELECT COUNT(*) FROM fact_cash_flow WHERE cust_id IS NOT NULL AND supplier_id IS NOT NULL",
    "transfer_roles_distinct": "SELECT (SELECT COUNT(*) FROM fact_transfer_order WHERE source_warehouse_id=target_warehouse_id) + (SELECT COUNT(*) FROM fact_fixed_asset_transfer WHERE source_factory_id=target_factory_id)",
    "risk_level_matches_score": "SELECT COUNT(*) FROM (SELECT risk_level_id, severity*likelihood s FROM fact_hazard_observation UNION ALL SELECT risk_level_id, severity*likelihood FROM fact_safety_action) x JOIN dim_risk_level r ON x.risk_level_id=r.level_id WHERE (SELECT COUNT(*) FROM dim_risk_level r2 WHERE r2.level_id < r.level_id) <> (CASE WHEN x.s<=4 THEN 0 WHEN x.s<=9 THEN 1 WHEN x.s<=15 THEN 2 ELSE 3 END)",
}


# ───────── v3:由 108 表业务事件推导的表,逐项与基础库勾稽(违反数须为 0) ─────────
_SIGNED = "CASE WHEN movement_type IN ('101 采购收货','301 仓间调拨入') THEN quantity ELSE -quantity END"
EVENT_CHECKS = {
    # 销售:发运 = 交付;发运明细 = 交付消耗的成品批次(批次在发货前完工,累计发出不超过产量)
    "shipment_one_per_delivery": "SELECT (SELECT COUNT(*) FROM fact_delivery) - (SELECT COUNT(DISTINCT delivery_id) FROM fact_shipment) + (SELECT COUNT(*) - COUNT(DISTINCT delivery_id) FROM fact_shipment)",
    "shipment_freight_by_weight": "SELECT COUNT(*) FROM fact_shipment WHERE freight_amount <> CASE WHEN delivery_terms='客户自提' THEN 0.0 ELSE ROUND(MAX(200.0, quantity*150.0),2) END",
    "shipment_matches_delivery": "SELECT COUNT(*) FROM fact_shipment s JOIN fact_delivery d USING(delivery_id) WHERE ABS(s.quantity-d.quantity)>0.001 OR ABS(s.amount-d.amount)>0.05 OR s.business_date<>d.delivery_date",
    "shipped_batch_made_before_shipment": "SELECT COUNT(*) FROM fact_shipment_line l LEFT JOIN fact_production_order p ON p.batch_no=l.batch_no "
        "WHERE (p.order_id IS NULL OR p.status<>'已完成' OR date(p.end_time)>=l.business_date OR p.prod_id<>l.prod_id)",
    "shipped_batch_not_over_produced": "SELECT COUNT(*) FROM (SELECT batch_no, SUM(quantity) q FROM fact_shipment_line GROUP BY batch_no) x JOIN fact_production_order p USING(batch_no) WHERE x.q>p.actual_quantity+0.001",
    "rejected_batch_shipped_after_rework": "SELECT COUNT(*) FROM fact_shipment_line l JOIN fact_rework_order w ON w.batch_no=l.batch_no WHERE w.end_time IS NULL OR l.business_date<=date(w.end_time)",
    "order_lines_dated_with_order": "SELECT COUNT(*) FROM fact_sales_order_line l JOIN fact_sales_order o USING(order_id) WHERE l.business_date<>o.order_date",
    "forecast_made_before_period": "SELECT COUNT(*) FROM fact_sales_forecast WHERE strftime('%Y-%m', business_date)>=forecast_bucket",
    "complaint_precedes_quality_return": "SELECT COUNT(*) FROM fact_return r JOIN fact_sales_order o USING(order_id) WHERE (r.reason LIKE '产品质量%' OR r.reason LIKE '颜色%') "
        "AND NOT EXISTS (SELECT 1 FROM fact_customer_complaint c WHERE c.order_id=r.order_id AND c.complaint_type='质量问题' AND c.business_date<=r.return_date)",
    # 采购:到货预告对应收货;索赔对应来料不合格
    "asn_line_equals_receipt": "SELECT ABS((SELECT ROUND(SUM(quantity),3) FROM fact_asn_line) - (SELECT ROUND(SUM(quantity),3) FROM fact_goods_receipt)) > 0.01",
    "asn_sent_before_receipt": "SELECT COUNT(*) FROM fact_advance_shipping_notice WHERE business_date>received_date",
    "claim_per_failed_receipt": "SELECT COUNT(*) FROM fact_supplier_claim c WHERE NOT EXISTS (SELECT 1 FROM fact_goods_receipt g WHERE g.gr_no=c.source_document_no AND g.inspection_result='fail' AND g.receipt_date<=c.business_date)",
    "supplier_contract_line_actuals": "SELECT COUNT(*) FROM fact_supplier_contract_line l JOIN fact_supplier_contract c USING(supplier_contract_id) "
        "WHERE ABS(l.actual_quantity-(SELECT SUM(pl.quantity) FROM fact_po_line pl JOIN fact_purchase_order po USING(po_id) WHERE po.supplier_id=c.supplier_id AND pl.material_id=l.material_id))>0.01",
    # 库存:月末快照 − 截至当日的移动累计 = 期初(每个仓库 × 物料各月相同);调拨明细 = 调拨入库移动
    "snapshot_reconciles_with_movements": "SELECT COUNT(*) FROM (SELECT warehouse_id, material_id, MAX(o)-MIN(o) spread FROM (SELECT s.warehouse_id, s.material_id, "
        "s.quantity - COALESCE((SELECT SUM(" + _SIGNED + ") FROM fact_inventory_movement m WHERE m.warehouse_id=s.warehouse_id AND m.material_id=s.material_id "
        "AND m.business_date<=s.business_date), 0) o FROM fact_inventory_snapshot s) GROUP BY 1,2) WHERE spread>0.01",
    "snapshot_never_negative": "SELECT COUNT(*) FROM fact_inventory_snapshot WHERE quantity<0",
    "transfer_lines_equal_transfer_in": "SELECT ABS((SELECT ROUND(SUM(quantity),3) FROM fact_transfer_order_line) - (SELECT ROUND(SUM(quantity),3) FROM fact_inventory_movement WHERE movement_type='301 仓间调拨入')) > 0.01",
    "issues_equal_consumption": "SELECT ABS((SELECT ROUND(SUM(quantity),3) FROM fact_inventory_movement WHERE movement_type='261 工单发料') - (SELECT ROUND(SUM(actual_qty),3) FROM fact_material_consumption)) > 0.05",
    "count_book_equals_snapshot": "SELECT COUNT(*) FROM fact_cycle_count_line l JOIN fact_cycle_count c USING(cycle_count_id) JOIN fact_inventory_snapshot s "
        "ON s.material_id=l.material_id AND s.warehouse_id=c.warehouse_id AND s.business_date=c.business_date WHERE ABS(l.book_quantity-s.quantity)>0.001",
    # 生产:工序、报工、工艺参数在工单起止之内;发料 = 投料;工时合计 = 运行时长
    "operations_within_order_window": "SELECT COUNT(*) FROM fact_work_order_operation w JOIN fact_production_order p USING(order_id) "
        "WHERE w.start_time<p.start_time OR (w.end_time IS NOT NULL AND p.end_time IS NOT NULL AND w.end_time>datetime(p.end_time,'+1 minute'))",
    "operation_output_equals_order": "SELECT COUNT(*) FROM fact_work_order_operation w JOIN fact_production_order p USING(order_id) WHERE ABS(w.actual_quantity-COALESCE(p.actual_quantity,0))>0.001",
    "issued_material_equals_consumption": "SELECT COUNT(*) FROM (SELECT order_id, material_id, SUM(quantity) q FROM fact_work_order_material GROUP BY 1,2) w "
        "JOIN (SELECT order_id, material_id, SUM(actual_qty) q FROM fact_material_consumption GROUP BY 1,2) c USING(order_id, material_id) WHERE ABS(w.q-c.q)>0.001",
    "labor_within_order_window": "SELECT COUNT(*) FROM fact_labor_booking b JOIN fact_production_order p USING(order_id) WHERE b.start_time<p.start_time OR (p.end_time IS NOT NULL AND b.end_time>p.end_time)",
    "labor_hours_equal_runtime": "SELECT COUNT(*) FROM (SELECT order_id, SUM(quantity) h FROM fact_labor_booking GROUP BY 1) b JOIN fact_production_order p USING(order_id) "
        "WHERE p.status='已完成' AND ABS(b.h-p.runtime_hours)>0.02*(SELECT COUNT(*) FROM fact_labor_booking x WHERE x.order_id=p.order_id)",
    "parameters_within_order_window": "SELECT COUNT(*) FROM fact_process_parameter_record r JOIN fact_production_order p USING(order_id) WHERE r.event_time<p.start_time OR r.event_time>p.end_time",
    "shift_output_equals_production": "SELECT COUNT(*) FROM (SELECT line_id, SUM(quantity) q FROM fact_shift_report GROUP BY 1) s "
        "JOIN (SELECT line_id, SUM(COALESCE(actual_quantity,0)) q FROM fact_production_order GROUP BY 1) p USING(line_id) WHERE ABS(s.q-p.q)>1.0",
    "capacity_not_exceeded": "SELECT COUNT(*) FROM fact_capacity_plan WHERE quantity>capacity_hours+0.01 OR utilization_rate>1",
    "wip_only_orders_running_at_month_end": "SELECT COUNT(*) FROM fact_wip_snapshot w JOIN fact_production_order p USING(order_id) "
        "WHERE p.start_time>w.business_date||' 23:59:59' OR (p.end_time IS NOT NULL AND p.end_time<=w.business_date||' 23:59:59')",
    "tool_life_not_exceeded": "SELECT COUNT(*) FROM fact_tool_usage u JOIN dim_tool t USING(tool_id) WHERE u.remaining_life<0 OR u.remaining_life>t.total_life",
    "scrap_equals_consumption_loss": "SELECT COUNT(*) FROM fact_scrap_event e WHERE ABS(e.quantity/1000-(SELECT SUM(scrap_qty) FROM fact_material_consumption c WHERE c.order_id=e.order_id))>0.001",
    "rework_after_failed_inspection": "SELECT COUNT(*) FROM fact_rework_order w JOIN fact_production_order p USING(order_id) WHERE w.start_time<p.end_time "
        "OR NOT EXISTS (SELECT 1 FROM fact_quality_inspection q WHERE q.order_id=w.order_id AND q.result='不合格')",
    # 质量:检验批 = 完工工单;检验结果 = 108 表质检记录;不合格记录 = 非合格检验项
    "inspection_lot_per_completed_order": "SELECT (SELECT COUNT(*) FROM fact_production_order WHERE status='已完成') - (SELECT COUNT(DISTINCT order_id) FROM fact_inspection_lot)",
    "inspection_results_equal_base": "SELECT (SELECT COUNT(*) FROM fact_quality_inspection q JOIN fact_production_order p USING(order_id) WHERE p.status='已完成') - (SELECT COUNT(*) FROM fact_inspection_result)",
    "lot_verdict_matches_results": "SELECT COUNT(*) FROM fact_inspection_lot i WHERE i.result_code <> (SELECT CASE WHEN SUM(r.result_code='不合格')>0 THEN '不合格' WHEN SUM(r.result_code='让步接收')>0 THEN '让步接收' ELSE '合格' END FROM fact_inspection_result r WHERE r.inspection_lot_id=i.inspection_lot_id)",
    "nonconformance_per_failed_item": "SELECT (SELECT COUNT(*) FROM fact_inspection_result WHERE result_code<>'合格') - (SELECT COUNT(*) FROM fact_nonconformance)",
    "corrective_action_after_nonconformance": "SELECT COUNT(*) FROM fact_corrective_action a JOIN fact_nonconformance n USING(nonconformance_id) WHERE a.business_date<n.business_date OR a.defect_code<>n.defect_code",
    "hold_per_flagged_batch": "SELECT COUNT(*) FROM fact_inspection_lot i WHERE i.result_code<>'合格' AND NOT EXISTS (SELECT 1 FROM fact_batch_quality_hold h WHERE h.batch_no=i.batch_no)",
    "calibration_within_tolerance_flag": "SELECT COUNT(*) FROM fact_gauge_calibration WHERE (ABS(error_value)<=tolerance)<>(result_code='合格')",
    # 设备与能源
    "equipment_event_per_downtime": "SELECT (SELECT COUNT(*) FROM fact_downtime) - (SELECT COUNT(*) FROM fact_equipment_event)",
    "monitoring_mirrors_iot": "SELECT (SELECT COUNT(*) FROM fact_iot_data) - (SELECT COUNT(*) FROM fact_condition_monitoring)",
    "alert_precedes_breakdown": "SELECT COUNT(*) FROM fact_predictive_alert a WHERE NOT EXISTS (SELECT 1 FROM fact_downtime d WHERE d.equipment_id=a.equipment_id AND d.reason_code=1 "
        "AND d.start_time>a.event_time AND julianday(d.start_time)-julianday(a.event_time)<=5.01)",
    "maintenance_tasks_within_work_order": "SELECT COUNT(*) FROM fact_maintenance_task t JOIN fact_maintenance_wo w USING(wo_id) WHERE t.start_time<w.start_time OR (w.end_time IS NOT NULL AND t.end_time>w.end_time)",
    "spare_stock_continuous": "SELECT COUNT(*) FROM (SELECT stock_before, LAG(stock_after) OVER (PARTITION BY part_id ORDER BY event_time, spare_part_issue_id) prev, "
        "sp.min_stock, sp.max_stock FROM fact_spare_part_issue i JOIN dim_spare_part sp USING(part_id)) WHERE prev IS NOT NULL AND stock_before<>prev AND stock_before<>max_stock",
    "spare_end_stock_matches_master": "SELECT COUNT(*) FROM dim_spare_part sp WHERE sp.current_stock<>(SELECT stock_after FROM fact_spare_part_issue i WHERE i.part_id=sp.part_id ORDER BY event_time DESC, spare_part_issue_id DESC LIMIT 1)",
    "spare_issue_equals_replaced_parts": "SELECT COUNT(*) FROM fact_spare_part_issue WHERE ABS(stock_before-quantity-stock_after)>0.001 OR stock_after<0",
    "meter_readings_continuous": "SELECT COUNT(*) FROM (SELECT meter_start, LAG(meter_end) OVER (PARTITION BY meter_code ORDER BY event_time) prev FROM fact_energy_meter_reading) WHERE prev IS NOT NULL AND ABS(meter_start-prev)>0.01",
    "electricity_equals_consumption": "SELECT ABS((SELECT ROUND(SUM(quantity),1) FROM fact_energy_meter_reading r JOIN dim_energy_type e USING(energy_type_id) WHERE e.code='ELEC') - (SELECT ROUND(SUM(electricity_kwh),1) FROM fact_energy_consumption)) > 1",
    # 财务:凭证借贷平衡,且与单据金额一致;资金流水 = 收款 + 付款
    # 收入净额 = 蓝字发票 − 红字发票;应收贷方 = 回款 + 红字冲销;应付贷方 = 应付 + 工程款;原材料借贷差 = 合格收货 − 生产领料(均按批次入库单价)
    "gl_revenue_equals_invoices": "SELECT ABS((SELECT ROUND(SUM(l.credit_amount-l.debit_amount),2) FROM fact_gl_entry_line l WHERE l.account_code='6001') - (SELECT ROUND(SUM(amount_no_tax),2) FROM fact_invoice)) > 1",
    "gl_receivable_clears_with_payments": "SELECT ABS((SELECT ROUND(SUM(l.credit_amount),2) FROM fact_gl_entry_line l WHERE l.account_code='1122') - (SELECT ROUND(SUM(amount),2) FROM fact_payment) - (SELECT ROUND(-SUM(total_amount),2) FROM fact_invoice WHERE total_amount<0)) > 1",
    "gl_payable_equals_ap": "SELECT ABS((SELECT ROUND(SUM(l.credit_amount),2) FROM fact_gl_entry_line l WHERE l.account_code='2202') - (SELECT ROUND(SUM(amount),2) FROM fact_ap) - (SELECT ROUND(SUM(amount),2) FROM fact_capex_project_cost)) > 1",
    "gl_raw_material_equals_stock_ledger": "SELECT ABS((SELECT ROUND(SUM(l.debit_amount-l.credit_amount),2) FROM fact_gl_entry_line l JOIN fact_gl_entry h USING(gl_entry_id) "
        "WHERE l.account_code='1403' AND h.summary_text NOT LIKE '期初余额%') - "
        "(SELECT ROUND(SUM(CASE movement_type WHEN '101 采购收货' THEN amount WHEN '261 工单发料' THEN -amount ELSE 0 END),2) FROM fact_inventory_movement)) > 5",
    "gl_overhead_cleared_monthly": "SELECT COUNT(*) FROM (SELECT strftime('%Y-%m', business_date) m, SUM(debit_amount-credit_amount) b FROM fact_gl_entry_line WHERE account_code='5101' GROUP BY 1) WHERE ABS(b)>1",
    "gl_voucher_refs_resolve": "SELECT (SELECT COUNT(*) FROM fact_tax_invoice_line WHERE voucher_no NOT IN (SELECT voucher_no FROM fact_gl_entry)) + (SELECT COUNT(*) FROM fact_cash_flow WHERE voucher_no NOT IN (SELECT voucher_no FROM fact_gl_entry))",
    "gl_header_equals_line_debits": "SELECT COUNT(*) FROM fact_gl_entry h JOIN (SELECT gl_entry_id, SUM(debit_amount) d FROM fact_gl_entry_line GROUP BY 1) x USING(gl_entry_id) WHERE ABS(h.amount-x.d)>0.011",
    # 资金流水 = 总账银行存款发生额(不含期初);银行存款任何一天不为负
    "cash_flow_equals_bank_ledger": "SELECT ABS((SELECT ROUND(SUM(debit_amount-credit_amount),2) FROM fact_cash_flow) - (SELECT ROUND(SUM(l.debit_amount-l.credit_amount),2) "
        "FROM fact_gl_entry_line l JOIN fact_gl_entry h USING(gl_entry_id) WHERE l.account_code='1002' AND h.summary_text NOT LIKE '期初余额%')) > 1",
    "bank_balance_never_negative": "SELECT COUNT(*) FROM (SELECT business_date, SUM(SUM(debit_amount-credit_amount)) OVER (ORDER BY business_date) b FROM fact_gl_entry_line WHERE account_code='1002' GROUP BY business_date) WHERE b<0",
    "fixed_assets_ledger_equals_register": "SELECT ABS((SELECT ROUND(SUM(l.debit_amount-l.credit_amount),0) FROM fact_gl_entry_line l JOIN fact_gl_entry h USING(gl_entry_id) WHERE l.account_code='1601' AND h.summary_text LIKE '期初余额%') "
        "- (SELECT ROUND(SUM(original_value),0) FROM dim_fixed_asset WHERE purchase_date<'2025-01-01')) > 1",
    "allocation_shares_sum_to_one": "SELECT COUNT(*) FROM (SELECT source_cc_id, business_date, SUM(rate_value) r FROM fact_cost_allocation GROUP BY 1,2) WHERE ABS(r-1)>0.001",
    "capex_within_project_window": "SELECT COUNT(*) FROM fact_capex_project_cost c JOIN dim_project p USING(project_id) WHERE c.business_date<p.planned_start_date OR c.business_date>p.planned_end_date",
    # 安全、环境与人力
    "measurements_average_to_record": "SELECT COUNT(*) FROM (SELECT business_date, emission_type_id, AVG(emission_value) a FROM fact_environment_measurement GROUP BY 1,2) m JOIN dim_emission_type t USING(emission_type_id) "
        "WHERE NOT EXISTS (SELECT 1 FROM fact_env_record r WHERE r.record_date=m.business_date AND ABS(r.measured_value-m.a)<0.01)",
    "emission_event_per_noncompliance": "SELECT (SELECT COUNT(*) FROM fact_env_record WHERE is_compliant=0) - (SELECT COUNT(*) FROM fact_emission_event)",
    "action_for_high_risk_observation": "SELECT COUNT(*) FROM fact_hazard_observation o WHERE o.severity*o.likelihood>=6 AND o.business_date<'2026-06-30' "
        "AND NOT EXISTS (SELECT 1 FROM fact_safety_action a WHERE a.observation_no=o.observation_no)",
    "training_attendance_equals_headcount": "SELECT COUNT(*) FROM fact_safety_training t WHERE t.attendee_count<>(SELECT COUNT(*) FROM fact_training_attendance a WHERE a.training_id=t.training_id)",
    "trainee_employed_at_training": "SELECT COUNT(*) FROM fact_training_attendance a JOIN dim_employee e USING(emp_id) JOIN fact_safety_training t USING(training_id) WHERE e.hire_date>t.training_date",
    # 行政白班只在工作日;轮班的班组长按四班三运转在岗(含周末);报工人当天有同班次考勤
    "attendance_on_workdays_once": "SELECT (SELECT COUNT(*) FROM fact_attendance a JOIN dim_calendar c ON c.calendar_date=a.business_date WHERE c.is_workday=0 AND a.shift_id IS NULL) "
        "+ (SELECT COUNT(*) FROM (SELECT emp_id, business_date FROM fact_attendance GROUP BY 1,2 HAVING COUNT(*)>1))",
    "attendance_within_employment": "SELECT COUNT(*) FROM fact_attendance a JOIN dim_employee e USING(emp_id) WHERE a.business_date<e.hire_date OR (e.status='inactive' AND a.business_date>'2026-03-31')",
    "labor_booker_on_shift": "SELECT COUNT(*) FROM fact_labor_booking b WHERE NOT EXISTS (SELECT 1 FROM fact_attendance a WHERE a.emp_id=b.emp_id AND a.work_date=b.work_date AND a.shift_id=b.shift_id)",
    "overtime_from_attendance": "SELECT COUNT(*) FROM fact_overtime o WHERE NOT EXISTS (SELECT 1 FROM fact_attendance a WHERE a.emp_id=o.emp_id AND a.business_date=o.business_date AND a.overtime_hours=o.overtime_hours)",
    "assignment_per_employee": "SELECT (SELECT COUNT(*) FROM dim_employee) - (SELECT COUNT(DISTINCT emp_id) FROM fact_employee_assignment)",
    # 第二轮审计:人员按岗位、订单行分配、表头继承、合格记录无处置、批号与库位可解析、单位一致
    "person_codes_match_role": "SELECT " + " + ".join(f"(SELECT COUNT(*) FROM {t} x JOIN dim_employee e ON x.{c}=printf('E%04d', e.emp_id) WHERE e.dept_id NOT IN ({d}))"
        for t, c, d in (("fact_inspection_result", "inspector_code", "8"), ("fact_asn_line", "handled_by_code", "11"), ("fact_inventory_movement", "handled_by_code", "11"),
                        ("fact_advance_shipping_notice", "buyer_code", "10"), ("fact_supplier_claim", "buyer_code", "10"), ("fact_capacity_plan", "planner_code", "15"),
                        ("fact_demand_plan", "planner_code", "15"), ("fact_equipment_event", "operator_code", "9"), ("fact_labor_booking", "supervisor_code", "7"))),
    "shipped_not_above_order_line": "SELECT COUNT(*) FROM fact_sales_order_line ol JOIN (SELECT sales_order_line_id, SUM(quantity) q FROM fact_shipment_line GROUP BY 1) x USING(sales_order_line_id) WHERE x.q>ol.quantity+0.001 OR ABS(x.q-ol.actual_quantity)>0.001",
    "shipment_inherits_order_line": "SELECT COUNT(*) FROM fact_shipment s JOIN fact_shipment_line sl ON sl.shipment_id=s.shipment_id AND sl.line_no=1 "
        "JOIN fact_sales_order_line ol ON ol.sales_order_line_id=sl.sales_order_line_id "
        "WHERE s.customer_po_no IS NOT ol.customer_po_no OR s.price_list_code IS NOT ol.price_list_code OR s.sales_region IS NOT ol.sales_region "
        "OR s.contract_no IS NOT ol.contract_no OR s.promised_delivery_date IS NOT ol.promised_delivery_date",
    "shipment_margin_consistent": "SELECT COUNT(*) FROM fact_shipment WHERE amount>0 AND ABS(margin_rate-(amount-cost_amount)/amount)>0.0002",
    "pass_records_have_no_case_fields": "SELECT (SELECT COUNT(*) FROM fact_inspection_result WHERE result_code='合格' AND (root_cause_code IS NOT NULL OR containment_action IS NOT NULL OR disposition IS NOT NULL OR defect_code IS NOT NULL))"
        " + (SELECT COUNT(*) FROM fact_process_parameter_record WHERE result_code='合格' AND (root_cause_code IS NOT NULL OR containment_action IS NOT NULL OR defect_code IS NOT NULL))",
    "corrective_action_inherits_case": "SELECT COUNT(*) FROM fact_corrective_action a JOIN fact_nonconformance n USING(nonconformance_id) WHERE a.root_cause_code IS NOT n.root_cause_code OR a.containment_action IS NOT n.containment_action",
    "lot_header_matches_verdict": "SELECT COUNT(*) FROM fact_inspection_lot WHERE (measured_value BETWEEN lower_limit AND upper_limit) <> (result_code='合格')",
    "movement_lots_traceable": "SELECT COUNT(*) FROM fact_inventory_movement m WHERE m.movement_type='101 采购收货' AND m.lot_no NOT IN (SELECT batch_no FROM fact_asn_line)",
    "issue_lots_from_consumption": "SELECT COUNT(*) FROM fact_inventory_movement m WHERE m.movement_type='261 工单发料' AND m.lot_no NOT IN (SELECT batch_no FROM fact_material_consumption)",
    "bins_exist": "SELECT " + " + ".join(f"(SELECT COUNT(*) FROM {t} WHERE bin_code IS NOT NULL AND bin_code NOT IN (SELECT location_code FROM dim_storage_location))"
        for t in ("fact_inventory_movement", "fact_shipment_line", "fact_asn_line", "fact_cycle_count_line", "fact_batch_quality_hold", "fact_inspection_lot")),
    "usage_variance_consistent": "SELECT COUNT(*) FROM fact_lot_trace t JOIN (SELECT order_id, material_id, SUM(quantity) q, MAX(standard_quantity) s FROM fact_work_order_material GROUP BY 1,2) w "
        "USING(order_id, material_id) WHERE ABS(t.standard_quantity-w.s)>0.001",
    "scrap_cost_in_tonnes": "SELECT COUNT(*) FROM fact_scrap_event WHERE ABS(cost_amount-amount)>0.01",
    "monitoring_fault_only_when_out_of_range": "SELECT COUNT(*) FROM fact_condition_monitoring WHERE (reading_value>upper_threshold OR reading_value<lower_threshold) <> (failure_mode<>'无故障')",
    "env_samples_exceed_only_in_noncompliant_months": "SELECT COUNT(*) FROM fact_environment_measurement m WHERE m.exceed_flag=1 AND NOT EXISTS (SELECT 1 FROM fact_emission_event e "
        "WHERE e.emission_type_id=m.emission_type_id AND e.business_date=m.business_date)",
    "task_by_assigned_tech": "SELECT COUNT(*) FROM fact_maintenance_task t JOIN fact_maintenance_wo w USING(wo_id) JOIN dim_employee e ON e.emp_id=t.emp_id WHERE e.emp_name<>w.assigned_tech",
    "task_hours_equal_record": "SELECT COUNT(*) FROM (SELECT wo_id, SUM(labor_hours) h FROM fact_maintenance_task GROUP BY 1) t JOIN fact_maintenance_record r USING(wo_id) WHERE ABS(t.h-r.labor_hours)>0.02",
    "training_score_mean_matches": "SELECT COUNT(*) FROM fact_safety_training s JOIN (SELECT training_id, AVG(exam_score) a FROM fact_training_attendance GROUP BY 1) x USING(training_id) WHERE ABS(x.a-s.score_avg)>0.2",
    "forecast_segment_matches_industry": "SELECT COUNT(*) FROM fact_sales_forecast f JOIN dim_customer c USING(cust_id) JOIN dim_market_segment m USING(market_segment_id) "
        "WHERE m.name<>CASE c.industry WHEN '汽车' THEN '汽车零部件' ELSE c.industry END",
    "emission_standard_by_medium": "SELECT COUNT(*) FROM fact_environment_measurement f JOIN dim_emission_type t USING(emission_type_id) "
        "WHERE (t.measurement_unit='mg/L' AND f.regulatory_clause<>'GB 8978-1996') OR (t.measurement_unit='mg/m³' AND f.regulatory_clause<>'GB 16297-1996') OR (t.code='NOISE' AND f.regulatory_clause<>'GB 12348-2008')",
    "capex_phases_in_order": "SELECT COUNT(*) FROM fact_capex_project_cost a JOIN fact_capex_project_cost b ON a.project_id=b.project_id AND a.business_date<b.business_date "
        "WHERE (CASE a.project_phase WHEN '设计' THEN 1 WHEN '采购' THEN 2 WHEN '施工' THEN 3 ELSE 4 END) > (CASE b.project_phase WHEN '设计' THEN 1 WHEN '采购' THEN 2 WHEN '施工' THEN 3 ELSE 4 END)",
    "capex_fully_capitalized": "SELECT COUNT(*) FROM fact_capex_project_cost c WHERE capitalized_flag=1 AND ABS(asset_value_added-(SELECT SUM(amount) FROM fact_capex_project_cost x WHERE x.project_id=c.project_id))>0.01",
    "kpi_rates_bounded": "SELECT COUNT(*) FROM fact_kpi_snapshot WHERE kpi_code IN ('KPI-OTD','KPI-QPASS','KPI-YIELD','KPI-OEE') AND actual_value>1",
    "sqi_matches_claim": "SELECT COUNT(*) FROM fact_supplier_quality_issue q JOIN fact_supplier_claim c USING(source_document_no) WHERE q.claim_resolution IS NOT c.claim_resolution OR q.claim_amount_basis<>c.claim_amount_basis OR q.supplier_response_days<>c.supplier_response_days",
    # 时间窗:事件不晚于观测期末
    "no_events_after_window": "SELECT " + " + ".join(f"(SELECT COUNT(*) FROM {t} WHERE business_date>'2026-06-30')" for t in (
        "fact_shipment", "fact_shipment_line", "fact_inventory_movement", "fact_work_order_operation", "fact_labor_booking", "fact_equipment_event",
        "fact_attendance", "fact_gl_entry", "fact_cash_flow", "fact_energy_meter_reading", "fact_hazard_observation", "fact_predictive_alert")),
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

    for name, sql in EVENT_CHECKS.items():
        v = _one(con, sql)
        put(f"events.{name}", v, v == 0)

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
    # v3 的事实行由业务事件推导,同一张表内取值恒定可能正是业务事实(如原料都收在一厂原料仓),只报告不拦截;
    # 全空的列仍拦截(说明该列没有任何业务来源)
    put("values.constant_columns_report", len(bad_const), True, "仅报告")
    out["values.constant_columns_report"]["detail"] = bad_const[:60]
    out["values.placeholder_columns"]["detail"] = ph[:30]
    empty = [f"{t}.{c}" for t in facts + dims for c in _cols(con, t)
             if _one(con, f'SELECT COUNT("{c}") FROM "{t}"') == 0 and c not in ("remarks", "parent_code", "approved_by", "approved_at")]
    put("values.all_null_columns", len(empty), not empty)
    out["values.all_null_columns"]["detail"] = empty[:30]

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
