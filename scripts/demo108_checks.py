#!/usr/bin/env python3
"""Business-rule checks for the 108-table demo database (DR-076).

Each check is a query that counts violations (must be 0) or a bounded statistic.  They cover
the problems three independent audits found in the original data: chains that do not
reconcile (order → delivery → invoice → receivable → payment, PO → receipt → payable →
payment), child events outside their parent's window, wrong VAT and cost conventions,
recipes and units, master data contradicting itself, and summary tables that do not add up
to their details.  Usable standalone:  python3 scripts/demo108_checks.py <db>.  Standard library only.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any

END = "2026-06-30"
TOL = 0.011

ZERO_CHECKS: dict[str, str] = {
    # ── 销售链 ──
    "sales.amount_equals_qty_times_price": "SELECT COUNT(*) FROM fact_sales_order WHERE ABS(amount-ROUND(quantity*unit_price,2))>0.011",
    "sales.gross_profit_formula": "SELECT COUNT(*) FROM fact_sales_order WHERE ABS(gross_profit_actual-(amount-actual_cost_amount))>0.011 OR ABS(gross_profit_standard-(amount-standard_cost_amount))>0.011 OR is_negative_margin<>(gross_profit_actual<0)",
    "sales.order_rep_is_sales_and_employed": "SELECT COUNT(*) FROM fact_sales_order o JOIN dim_employee e ON o.emp_id=e.emp_id JOIN dim_department d ON e.dept_id=d.dept_id WHERE d.region_id IS NULL OR o.order_date<e.hire_date",
    "sales.delivery_after_order": "SELECT COUNT(*) FROM fact_delivery d JOIN fact_sales_order o USING(order_id) WHERE d.delivery_date<=o.order_date",
    "sales.delivery_amount_uses_order_price": "SELECT COUNT(*) FROM fact_delivery d JOIN fact_sales_order o USING(order_id) WHERE ABS(d.amount-ROUND(d.quantity*o.unit_price,2))>0.011",
    "sales.delivered_not_above_ordered": "SELECT COUNT(*) FROM (SELECT order_id, SUM(quantity) q FROM fact_delivery GROUP BY order_id) x JOIN fact_sales_order o USING(order_id) WHERE x.q>o.quantity+1e-6",
    "sales.status_matches_deliveries": "SELECT COUNT(*) FROM fact_sales_order o LEFT JOIN (SELECT order_id, SUM(quantity) q FROM fact_delivery GROUP BY order_id) x USING(order_id) WHERE (o.status IN ('delivered','returned')) <> (COALESCE(x.q,0)>=o.quantity-1e-6)",
    "sales.invoice_one_per_delivery": "SELECT COUNT(*) FROM (SELECT delivery_id FROM fact_invoice WHERE amount_no_tax>0 GROUP BY delivery_id HAVING COUNT(*)>1)",
    # 退货开红字发票:按订单,红字发票不含税合计 = −退货金额;红字发票不早于原发票
    "sales.credit_notes_equal_returns": "SELECT COUNT(*) FROM (SELECT o.order_id, COALESCE((SELECT SUM(r.amount) FROM fact_return r WHERE r.order_id=o.order_id),0) ra, "
        "COALESCE((SELECT SUM(i.amount_no_tax) FROM fact_invoice i JOIN fact_delivery d USING(delivery_id) WHERE d.order_id=o.order_id AND i.amount_no_tax<0),0) ca "
        "FROM fact_sales_order o) WHERE ABS(ra+ca)>0.011",
    "sales.credit_note_after_invoice": "SELECT COUNT(*) FROM fact_invoice c JOIN fact_invoice i ON i.delivery_id=c.delivery_id AND i.amount_no_tax>0 WHERE c.amount_no_tax<0 AND c.invoice_date<i.invoice_date",
    "sales.returned_orders_have_no_receivable": "SELECT COUNT(*) FROM fact_sales_order o JOIN fact_delivery d USING(order_id) JOIN fact_invoice i USING(delivery_id) JOIN fact_ar_aging a USING(invoice_id) WHERE o.status='returned' AND a.balance_amount>0.01",
    "sales.invoice_vat_13pct": "SELECT COUNT(*) FROM fact_invoice WHERE ABS(tax_amount-ROUND(amount_no_tax*0.13,2))>0.011 OR ABS(total_amount-(amount_no_tax+tax_amount))>0.011",
    "sales.invoice_net_equals_delivery": "SELECT COUNT(*) FROM fact_invoice i JOIN fact_delivery d USING(delivery_id) WHERE i.amount_no_tax>0 AND ABS(i.amount_no_tax-d.amount)>0.011 OR i.invoice_date<d.delivery_date OR i.days_to_invoice<>CAST(julianday(i.invoice_date)-julianday(d.delivery_date) AS INTEGER)",
    "sales.ar_one_per_invoice": "SELECT (SELECT COUNT(*) FROM fact_invoice)-(SELECT COUNT(DISTINCT invoice_id) FROM fact_ar_aging)",
    # 应收余额 = 发票价税合计 − 回款 + 同一交付的红字发票(负数);红字发票自身余额为 0
    "sales.ar_balance_equals_total_minus_payments": "SELECT COUNT(*) FROM fact_ar_aging a JOIN fact_invoice i USING(invoice_id) LEFT JOIN (SELECT ar_id, SUM(amount) p FROM fact_payment GROUP BY ar_id) x USING(ar_id) "
        "WHERE (i.total_amount>0 AND ABS(a.balance_amount-(i.total_amount-COALESCE(x.p,0)+COALESCE((SELECT SUM(c.total_amount) FROM fact_invoice c WHERE c.delivery_id=i.delivery_id AND c.total_amount<0),0)))>0.02) "
        "OR (i.total_amount<0 AND a.balance_amount<>0)",
    "sales.customer_risk_status_matches_overdue": "SELECT COUNT(*) FROM dim_customer c WHERE (c.status='risk') <> EXISTS (SELECT 1 FROM fact_ar_aging a WHERE a.cust_id=c.cust_id AND a.balance_amount>0.005 AND julianday('2026-06-30')-julianday(a.due_date)>60)",
    "sales.customer_risk_status_matches_dws": "SELECT COUNT(*) FROM dim_customer c LEFT JOIN DWS_MARKET_DAILY m ON m.dim_cust_id=c.cust_id AND m.date='2026-06-30' WHERE (c.status='risk')<>(COALESCE(m.risk_customer_count,0)=1)",
    "sales.credit_within_limit_or_frozen": "SELECT COUNT(*) FROM dim_customer_credit cc JOIN dim_customer c USING(cust_id) WHERE cc.used_credit>cc.credit_limit AND c.credit_status<>'frozen'",
    "sales.first_deal_not_after_first_order": "SELECT COUNT(*) FROM dim_customer c WHERE c.first_deal_date>(SELECT MIN(order_date) FROM fact_sales_order o WHERE o.cust_id=c.cust_id)",
    "sales.guide_price_above_standard_cost": "SELECT COUNT(*) FROM dim_product WHERE guide_price<standard_cost*1.05",
    # 成品:任何时点累计发货不超过此前已放行的完工量(不设期初成品库存)
    "fg.shipped_not_before_made": "SELECT COUNT(*) FROM fact_delivery d JOIN fact_sales_order o USING(order_id) WHERE "
        "(SELECT SUM(d2.quantity) FROM fact_delivery d2 JOIN fact_sales_order o2 USING(order_id) WHERE o2.prod_id=o.prod_id AND d2.delivery_date<=d.delivery_date) "
        "> COALESCE((SELECT SUM(p.actual_quantity) FROM fact_production_order p WHERE p.prod_id=o.prod_id AND p.status='已完成' AND date(p.end_time)<d.delivery_date),0)+0.01",
    "sales.ar_buckets_sum_to_balance": "SELECT COUNT(*) FROM fact_ar_aging WHERE ABS(balance_amount-(aging_1m+aging_2m+aging_3m+aging_4m+aging_5m+aging_6m+aging_6_12m+aging_12m_plus))>0.011",
    "sales.ar_overdue_flag": "SELECT COUNT(*) FROM fact_ar_aging WHERE is_overdue<>(balance_amount>0 AND due_date<'2026-06-30')",
    "sales.payment_after_invoice_within_window": "SELECT COUNT(*) FROM fact_payment p JOIN fact_ar_aging a USING(ar_id) JOIN fact_invoice i USING(invoice_id) WHERE p.payment_date<i.invoice_date OR p.payment_date>'2026-06-30' OR p.amount<=0",
    "sales.payment_type_matches_flag": "SELECT COUNT(*) FROM fact_payment WHERE (payment_type='银承')<>(is_bank_accepted=1)",
    "sales.return_after_delivery_within_delivered": "SELECT COUNT(*) FROM fact_return r JOIN fact_sales_order o USING(order_id) WHERE r.return_date<=(SELECT MIN(delivery_date) FROM fact_delivery d WHERE d.order_id=r.order_id) OR r.quantity>(SELECT SUM(quantity) FROM fact_delivery d WHERE d.order_id=r.order_id AND d.delivery_date<r.return_date)+1e-6 OR ABS(r.amount-ROUND(r.quantity*o.unit_price,2))>0.011",
    # 末次成交 = 窗口内最后一张订单;首次成交:老客户早于系统上线,新客户 = 首张订单
    "sales.customer_deal_dates": "SELECT COUNT(*) FROM dim_customer c LEFT JOIN (SELECT cust_id, MIN(order_date) f, MAX(order_date) l FROM fact_sales_order GROUP BY cust_id) x USING(cust_id) "
        "WHERE c.last_deal_date IS NOT x.l OR (c.first_deal_date>='2025-01-01' AND c.first_deal_date IS NOT x.f)",
    "sales.customer_status_matches_activity": "SELECT COUNT(*) FROM dim_customer WHERE (status='lost')<>(julianday('2026-06-30')-julianday(last_deal_date)>180) OR (status='dormant')<>(julianday('2026-06-30')-julianday(last_deal_date) BETWEEN 91 AND 180)",
    "sales.credit_used_equals_ar": "SELECT COUNT(*) FROM dim_customer_credit c LEFT JOIN (SELECT cust_id, SUM(balance_amount) b FROM fact_ar_aging GROUP BY cust_id) x USING(cust_id) WHERE ABS(c.used_credit-COALESCE(x.b,0))>0.05",
    "sales.won_opportunity_has_order": "SELECT COUNT(*) FROM fact_opportunity op LEFT JOIN fact_sales_order o ON op.converted_order_id=o.order_id WHERE op.status='won' AND (o.order_id IS NULL OR o.cust_id<>op.cust_id OR o.order_date<>op.close_date)",
    "sales.opportunity_dates": "SELECT COUNT(*) FROM fact_opportunity WHERE (status IN ('won','lost'))<>(close_date IS NOT NULL) OR close_date<create_date OR (status<>'won' AND converted_order_id IS NOT NULL)",
    "sales.sample_feedback": "SELECT COUNT(*) FROM fact_sample WHERE feedback_date<sample_date OR (is_success=1 AND is_feedback=0) OR (is_feedback=1)<>(feedback_result IS NOT NULL)",
    "sales.quotation_total": "SELECT COUNT(*) FROM fact_quotation WHERE ABS(total_amount-ROUND(quantity*unit_price,2))>0.011 OR valid_until<quotation_date",
    "sales.no_orders_after_rep_left": "SELECT COUNT(*) FROM fact_sales_order o JOIN dim_employee e ON o.emp_id=e.emp_id WHERE e.status='inactive' AND o.order_date>'2026-03-31'",
    # ── 采购链 ──
    "proc.one_material_per_po": "SELECT COUNT(*) FROM (SELECT po_id FROM fact_po_line GROUP BY po_id HAVING COUNT(DISTINCT material_id)>1)",
    "proc.po_total_equals_lines": "SELECT COUNT(*) FROM fact_purchase_order p JOIN (SELECT po_id, SUM(amount) a FROM fact_po_line GROUP BY po_id) x USING(po_id) WHERE ABS(p.total_amount-x.a)>0.011",
    "proc.po_line_amount": "SELECT COUNT(*) FROM fact_po_line WHERE ABS(amount-ROUND(quantity*unit_price,2))>0.011 OR received_qty>quantity+1e-6",
    "proc.receipt_after_po": "SELECT COUNT(*) FROM fact_goods_receipt g JOIN fact_purchase_order p USING(po_id) WHERE g.receipt_date<p.po_date OR p.status='draft'",
    "proc.received_equals_passed_receipts": "SELECT COUNT(*) FROM (SELECT po_id, SUM(quantity) q FROM fact_goods_receipt WHERE inspection_result='pass' GROUP BY po_id) x JOIN (SELECT po_id, SUM(received_qty) r FROM fact_po_line GROUP BY po_id) y USING(po_id) WHERE ABS(x.q-y.r)>0.001",
    "proc.po_status_matches_receipts": "SELECT COUNT(*) FROM fact_purchase_order p JOIN (SELECT po_id, MIN(received_qty>=quantity-1e-6) is_full FROM fact_po_line GROUP BY po_id) x USING(po_id) WHERE (p.status IN ('received','closed'))<>(x.is_full=1) AND p.status<>'draft'",
    "proc.ap_only_for_passed_receipts": "SELECT COUNT(*) FROM fact_ap a JOIN fact_goods_receipt g USING(gr_id) WHERE g.inspection_result<>'pass'",
    "proc.ap_supplier_and_amount": "SELECT COUNT(*) FROM fact_ap a JOIN fact_goods_receipt g USING(gr_id) JOIN fact_purchase_order p ON g.po_id=p.po_id WHERE a.supplier_id<>p.supplier_id",
    "proc.ap_paid_equals_payments": "SELECT COUNT(*) FROM fact_ap a LEFT JOIN (SELECT ap_id, SUM(amount) s FROM fact_payment_out GROUP BY ap_id) x USING(ap_id) WHERE ABS(a.paid_amount-COALESCE(x.s,0))>0.011 OR a.paid_amount>a.amount+0.011 OR (a.status='closed')<>(a.paid_amount>=a.amount-0.011)",
    "proc.payment_after_receipt": "SELECT COUNT(*) FROM fact_payment_out p JOIN fact_ap a USING(ap_id) JOIN fact_goods_receipt g USING(gr_id) WHERE p.payment_date<g.receipt_date OR p.payment_date>'2026-06-30'",
    # ── 生产 ──
    "prod.line_can_make_product": "SELECT COUNT(*) FROM fact_production_order o JOIN dim_production_line l USING(line_id) JOIN dim_product p USING(prod_id) WHERE INSTR(l.product_type, p.prod_name)=0",
    "prod.order_time_window": "SELECT COUNT(*) FROM fact_production_order WHERE status='已完成' AND (end_time<start_time OR runtime_hours>(julianday(end_time)-julianday(start_time))*24+0.01 OR start_time<order_date)",
    "prod.status_fields": "SELECT COUNT(*) FROM fact_production_order WHERE (status='已完成' AND (end_time IS NULL OR actual_quantity IS NULL)) OR (status='生产中' AND (end_time IS NOT NULL OR start_time IS NULL)) OR (status='待生产' AND start_time IS NOT NULL)",
    "prod.capacity_respected": "SELECT COUNT(*) FROM fact_production_order o JOIN dim_production_line l USING(line_id) WHERE o.status='已完成' AND o.actual_quantity/o.runtime_hours>l.capacity_tph*1.0001",
    "prod.consumption_in_bom": "SELECT COUNT(*) FROM fact_material_consumption c JOIN fact_production_order o USING(order_id) WHERE NOT EXISTS (SELECT 1 FROM dim_bom b JOIN dim_bom_line bl ON b.bom_id=bl.bom_id WHERE b.product_id=o.prod_id AND bl.material_id=c.material_id)",
    "prod.consumption_matches_bom_and_output": "SELECT COUNT(*) FROM fact_material_consumption c JOIN fact_production_order o USING(order_id) JOIN dim_bom b ON b.product_id=o.prod_id JOIN dim_bom_line bl ON bl.bom_id=b.bom_id AND bl.material_id=c.material_id WHERE ABS(c.planned_qty-bl.qty_per_ton/1000.0*o.actual_quantity)>0.001 OR ABS(c.scrap_qty-(c.actual_qty-c.planned_qty))>0.0002 OR c.scrap_qty<0",
    "prod.bom_sums_to_one_ton": "SELECT COUNT(*) FROM (SELECT bom_id, SUM(qty_per_ton) s FROM dim_bom_line GROUP BY bom_id) WHERE ABS(s-1000)>0.5",
    "prod.yield_is_output_over_input": "SELECT COUNT(*) FROM fact_production_order o JOIN (SELECT order_id, SUM(actual_qty) i FROM fact_material_consumption GROUP BY order_id) x USING(order_id) WHERE ABS(o.yield_rate-ROUND(o.actual_quantity/x.i*100,2))>0.011",
    "prod.output_equals_order": "SELECT COUNT(*) FROM fact_production_order o LEFT JOIN (SELECT order_id, SUM(quantity) q, MIN(output_date) d FROM fact_production_output GROUP BY order_id) x USING(order_id) WHERE o.status='已完成' AND (ABS(COALESCE(x.q,0)-o.actual_quantity)>0.011 OR x.d<>substr(o.end_time,1,10))",
    "prod.inspection_for_completed_orders": "SELECT COUNT(*) FROM fact_production_order o WHERE o.status='已完成' AND NOT EXISTS (SELECT 1 FROM fact_quality_inspection q WHERE q.order_id=o.order_id)",
    "prod.inspection_date_and_batch": "SELECT COUNT(*) FROM fact_quality_inspection q JOIN fact_production_order o USING(order_id) WHERE q.batch_no<>o.batch_no OR q.inspection_date<>substr(o.end_time,1,10)",
    "prod.cost_formula": "SELECT COUNT(*) FROM fact_product_cost WHERE ABS(total_cost-(material_cost+labor_cost+overhead_cost))>0.011 OR ABS(unit_cost-total_cost/output_qty)>0.001",
    "prod.cost_output_matches_order": "SELECT COUNT(*) FROM fact_product_cost c JOIN fact_production_order o ON c.batch_no=o.batch_no WHERE ABS(c.output_qty-o.actual_quantity)>0.011 OR c.product_id<>o.prod_id",
    "prod.unit_cost_near_standard": "SELECT COUNT(*) FROM fact_product_cost c JOIN dim_product p ON c.product_id=p.prod_id WHERE c.unit_cost NOT BETWEEN p.standard_cost*0.85 AND p.standard_cost*1.25",
    "prod.inventory_cost": "SELECT COUNT(*) FROM fact_inventory WHERE ABS(total_cost-ROUND(quantity*unit_cost,2))>0.02 OR storage_days<>CAST(julianday('2026-06-30')-julianday(warehouse_date) AS INTEGER) OR quantity<=0",
    # ── 设备、维护、资产 ──
    "eq.downtime_on_equipment_line": "SELECT COUNT(*) FROM fact_downtime d JOIN dim_equipment e USING(equipment_id) WHERE e.line_id<>d.line_id OR ABS(d.duration_hours-(julianday(d.end_time)-julianday(d.start_time))*24)>0.02",
    "eq.runtime_bounds": "SELECT COUNT(*) FROM fact_equipment_runtime WHERE actual_run_hours+downtime_hours>24.01 OR plan_run_hours<actual_run_hours-0.01 OR oee NOT BETWEEN 0 AND 100",
    "eq.alarm_sensor_equipment": "SELECT COUNT(*) FROM fact_alarm a JOIN dim_sensor s USING(sensor_id) WHERE a.equipment_id<>s.equipment_id OR (a.is_handled=1 AND a.handled_time<a.alarm_time)",
    "eq.iot_unit_is_sensor_unit": "SELECT COUNT(*) FROM fact_iot_data d JOIN dim_sensor s USING(sensor_id) WHERE d.unit<>s.unit",
    "eq.wo_dates": "SELECT COUNT(*) FROM fact_maintenance_wo WHERE start_time<wo_date OR end_time<start_time OR (status='completed')<>(end_time IS NOT NULL)",
    "eq.wo_record_pairs": "SELECT (SELECT COUNT(*) FROM fact_maintenance_wo w WHERE status='completed' AND NOT EXISTS (SELECT 1 FROM fact_maintenance_record r WHERE r.wo_id=w.wo_id)) + (SELECT COUNT(*) FROM fact_maintenance_record r JOIN fact_maintenance_wo w USING(wo_id) WHERE r.equipment_id<>w.equipment_id OR r.maint_date<>substr(w.end_time,1,10))",
    "eq.wo_plan_equipment": "SELECT COUNT(*) FROM fact_maintenance_wo w JOIN fact_maintenance_plan p USING(plan_id) WHERE w.equipment_id<>p.equipment_id OR w.maint_type<>p.maint_type",
    "eq.spare_stock_bounds": "SELECT COUNT(*) FROM dim_spare_part WHERE current_stock<0 OR current_stock>max_stock",
    "eq.tool_life": "SELECT COUNT(*) FROM dim_tool WHERE used_life>total_life OR used_life<0",
    "eq.install_after_factory_established": "SELECT COUNT(*) FROM dim_equipment e JOIN dim_production_line l USING(line_id) JOIN dim_fixed_asset a ON a.equipment_id=e.equipment_id WHERE a.purchase_date<>e.install_date",
    "asset.depreciation_consistent": "SELECT COUNT(*) FROM fact_depreciation d JOIN dim_fixed_asset a USING(asset_id) WHERE ABS(d.net_book_value-(a.original_value-d.accumulated_dep))>0.011 OR d.accumulated_dep>a.original_value*0.95+0.01 OR d.period<substr(a.purchase_date,1,7)",
    "asset.master_matches_last_period": "SELECT COUNT(*) FROM dim_fixed_asset a JOIN (SELECT asset_id, MAX(period) p FROM fact_depreciation GROUP BY asset_id) x USING(asset_id) JOIN fact_depreciation d ON d.asset_id=a.asset_id AND d.period=x.p WHERE ABS(d.accumulated_dep-a.accumulated_dep)>0.011",
    "equip.runtime_plus_downtime_within_day": "SELECT (SELECT COUNT(*) FROM fact_equipment_runtime WHERE actual_run_hours+downtime_hours>24.01) + (SELECT COUNT(*) FROM DWS_EQUIPMENT_DAILY WHERE actual_run_hours+downtime_hours>24.01)",
    "equip.tech_not_double_booked": "SELECT COUNT(*) FROM fact_maintenance_wo a JOIN fact_maintenance_wo b ON a.assigned_tech=b.assigned_tech AND a.wo_id<b.wo_id "
        "AND a.start_time IS NOT NULL AND b.start_time IS NOT NULL AND a.start_time<COALESCE(b.end_time,'9999') AND b.start_time<COALESCE(a.end_time,'9999')",
    "equip.overhaul_longer_than_monthly": "SELECT (SELECT AVG(julianday(end_time)-julianday(start_time)) FROM fact_maintenance_wo WHERE maint_type='季度大修' AND end_time IS NOT NULL) "
        "<= (SELECT AVG(julianday(end_time)-julianday(start_time)) FROM fact_maintenance_wo w JOIN dim_equipment e USING(equipment_id) WHERE maint_type='月度检修' AND e.type_id=1 AND end_time IS NOT NULL)",
    "equip.power_covers_consumption": "SELECT COUNT(*) FROM (SELECT r.line_id, SUM(e.electricity_kwh)/SUM(r.actual_run_hours) kwh_h FROM fact_equipment_runtime r JOIN fact_energy_consumption e "
        "ON e.line_id=r.line_id AND e.record_date=r.record_date GROUP BY 1) x WHERE x.kwh_h > (SELECT SUM(power_kw) FROM dim_equipment q WHERE q.line_id=x.line_id)",
    # ── 安全与环境 ──
    "safety.near_miss_no_loss": "SELECT COUNT(*) FROM fact_incident WHERE severity='near_miss' AND (property_loss<>0 OR injury_count<>0)",
    "safety.incident_line_in_workshop": "SELECT COUNT(*) FROM fact_incident i JOIN dim_production_line l USING(line_id) WHERE l.workshop_id<>i.workshop_id",
    "safety.investigation_dates": "SELECT COUNT(*) FROM fact_incident_inv v JOIN fact_incident i USING(incident_id) WHERE v.start_date<i.incident_date OR v.end_date<v.start_date",
    "safety.risk_matrix": "SELECT COUNT(*) FROM (SELECT risk_level, (CASE likelihood WHEN 'rare' THEN 1 WHEN 'unlikely' THEN 2 WHEN 'possible' THEN 3 WHEN 'likely' THEN 4 ELSE 5 END)*(CASE consequence WHEN 'negligible' THEN 1 WHEN 'minor' THEN 2 WHEN 'moderate' THEN 3 WHEN 'major' THEN 4 ELSE 5 END) s FROM fact_risk_assessment) WHERE risk_level<>(CASE WHEN s<=4 THEN 'L1' WHEN s<=9 THEN 'L2' WHEN s<=15 THEN 'L3' ELSE 'L4' END)",
    "safety.permit_window": "SELECT COUNT(*) FROM fact_work_permit WHERE valid_to<valid_from OR (status='closed' AND valid_to>'2026-06-30 23:59:59')",
    "safety.training_attendance": "SELECT COUNT(*) FROM fact_safety_training WHERE attendee_count>(SELECT COUNT(*) FROM dim_employee)",
    "env.compliance_flag_and_unit": "SELECT COUNT(*) FROM fact_env_record WHERE is_compliant<>(measured_value<=limit_value) OR (emission_type IN ('COD','氨氮') AND unit<>'mg/L') OR (emission_type IN ('VOCs','颗粒物') AND unit<>'mg/m³')",
    # ── 主数据 ──
    "master.factory_before_equipment": "SELECT COUNT(*) FROM dim_factory WHERE established_date>='2025-01-01'",
    "master.functional_departments_exist": "SELECT CASE WHEN (SELECT COUNT(*) FROM dim_department WHERE region_id IS NULL)>=8 THEN 0 ELSE 1 END",
    "master.cost_center_dept_exists": "SELECT COUNT(*) FROM dim_cost_center c LEFT JOIN dim_department d USING(dept_id) WHERE d.dept_id IS NULL",
    "master.team_leader_on_production": "SELECT COUNT(*) FROM dim_team t JOIN dim_employee e ON t.leader_id=e.emp_id WHERE e.dept_id<>7",
    "master.pricing_periods": "SELECT COUNT(*) FROM dim_pricing WHERE expired_date<effective_date",
    # ── 汇总层与明细对账 ──
    "dws.performance_sales": "SELECT COUNT(*) FROM (SELECT o.emp_id, o.order_date, SUM(o.amount) a, SUM(o.quantity) q FROM fact_sales_order o GROUP BY 1,2) x JOIN DWS_ORG_PERFORMANCE_DAILY d ON d.dim_emp_id=x.emp_id AND d.date=x.order_date WHERE ABS(d.sales_amount-x.a)>0.05 OR ABS(d.sales_quantity-x.q)>0.011",
    "dws.performance_covers_all_orders": "SELECT ABS((SELECT ROUND(SUM(amount),0) FROM fact_sales_order)-(SELECT ROUND(SUM(sales_amount),0) FROM DWS_ORG_PERFORMANCE_DAILY))>1",
    "dws.in_sales_order_count": "SELECT (SELECT COUNT(*) FROM fact_sales_order)-(SELECT SUM(order_count) FROM DWS_IN_SALES_DAILY)",
    "dws.after_sales_returns": "SELECT ABS((SELECT ROUND(SUM(quantity),2) FROM fact_return)-(SELECT ROUND(SUM(return_quantity),2) FROM DWS_AFTER_SALES_DAILY))>0.05"
        " OR ABS((SELECT ROUND(SUM(quantity),2) FROM fact_return)-(SELECT ROUND(SUM(return_quantity),2) FROM agg_metric_monthly))>0.011",
    "dws.target_split_sums_to_month": "SELECT COUNT(*) FROM (SELECT dim_emp_id, substr(date,1,7) m, SUM(sales_quantity_target) s FROM DWS_ORG_TARGET_DAILY GROUP BY 1,2) x "
        "JOIN fact_sales_target t ON t.emp_id=x.dim_emp_id AND printf('%04d-%02d', t.year, t.month)=x.m WHERE ABS(x.s-t.sales_quantity_target)>0.011",
    "dws.production_energy_and_hours": "SELECT ABS((SELECT ROUND(SUM(electricity_kwh),0) FROM fact_energy_consumption)-(SELECT ROUND(SUM(electricity_kwh),0) FROM DWS_PRODUCTION_DAILY))"
        "+ABS((SELECT ROUND(SUM(actual_run_hours),0) FROM fact_equipment_runtime)-(SELECT ROUND(SUM(actual_run_hours),0) FROM DWS_PRODUCTION_DAILY))"
        "+ABS((SELECT ROUND(SUM(downtime_hours),0) FROM fact_equipment_runtime)-(SELECT ROUND(SUM(downtime_hours),0) FROM DWS_PRODUCTION_DAILY)) > 2",
    "dws.production_actual": "SELECT ABS((SELECT ROUND(SUM(actual_quantity),1) FROM fact_production_order WHERE status='已完成')-(SELECT ROUND(SUM(actual_quantity),1) FROM DWS_PRODUCTION_DAILY))>0.5",
    "dws.equipment_alarms": "SELECT (SELECT COUNT(*) FROM fact_alarm WHERE alarm_time<='2026-06-30 23:59:59')-(SELECT SUM(alarm_count) FROM DWS_EQUIPMENT_DAILY)",
    "dws.safety_counts": "SELECT ABS((SELECT COUNT(*) FROM fact_incident WHERE severity<>'near_miss')-(SELECT SUM(incident_count) FROM DWS_SAFETY_DAILY)) + ABS((SELECT COUNT(*) FROM fact_work_permit)-(SELECT SUM(permit_count) FROM DWS_SAFETY_DAILY))",
    "dws.finance_budget_and_dep": "SELECT ABS((SELECT ROUND(SUM(budget_amount),0) FROM fact_budget)-(SELECT ROUND(SUM(budget_amount),0) FROM DWS_FINANCE_DAILY))+ABS((SELECT ROUND(SUM(dep_amount),0) FROM fact_depreciation)-(SELECT ROUND(SUM(dep_amount),0) FROM DWS_FINANCE_DAILY))",
    "dws.procurement_po_amount": "SELECT ABS((SELECT ROUND(SUM(total_amount),0) FROM fact_purchase_order WHERE status<>'draft' AND po_date<='2026-06-30')-(SELECT ROUND(SUM(po_amount),0) FROM DWS_PROCUREMENT_DAILY))>1",
    "dws.monthly_agg_sales": "SELECT COUNT(*) FROM agg_metric_monthly a JOIN (SELECT emp_id, CAST(substr(order_date,1,4) AS INTEGER) y, CAST(substr(order_date,6,2) AS INTEGER) m, SUM(amount) s FROM fact_sales_order GROUP BY 1,2,3) x ON a.emp_id=x.emp_id AND a.year=x.y AND a.month=x.m WHERE ABS(a.sales_amount-x.s)>0.05",
    "dws.market_status_one_hot": "SELECT COUNT(*) FROM DWS_MARKET_DAILY WHERE existing_customer_count+new_customer_count+dormant_customer_count+lost_customer_count>1",
    "dws.kpi_dept_counts": "SELECT COUNT(*) FROM (SELECT date, dim_dept_id, SUM(kpi_person_count) s, MAX(kpi_dept_count) k FROM DWS_KPI_DAILY GROUP BY 1,2) WHERE s<>k",
    "dws.names_match_master": "SELECT (SELECT COUNT(*) FROM DWS_IN_SALES_DAILY d JOIN dim_employee e ON d.dim_emp_id=e.emp_id WHERE d.emp_name<>e.emp_name)+(SELECT COUNT(*) FROM agg_metric_monthly a JOIN dim_employee e USING(emp_id) WHERE a.emp_name<>e.emp_name)",
    # ── 时间窗口 ──
    "time.no_events_after_window": "SELECT (SELECT COUNT(*) FROM fact_sales_order WHERE order_date>'2026-06-30')+(SELECT COUNT(*) FROM fact_delivery WHERE delivery_date>'2026-06-30')+(SELECT COUNT(*) FROM fact_invoice WHERE invoice_date>'2026-06-30')+(SELECT COUNT(*) FROM fact_goods_receipt WHERE receipt_date>'2026-06-30')+(SELECT COUNT(*) FROM fact_production_output WHERE output_date>'2026-06-30')"
        "+(SELECT COUNT(*) FROM fact_downtime WHERE start_time>'2026-06-30 23:59:59')+(SELECT COUNT(*) FROM fact_alarm WHERE alarm_time>'2026-06-30 23:59:59')"
        "+(SELECT COUNT(*) FROM fact_iot_data WHERE timestamp>'2026-06-30 23:59:59')+(SELECT COUNT(*) FROM fact_maintenance_wo WHERE wo_date>'2026-06-30')",
    # 检验结论与记录值一致:在标准范围内为合格,超出为让步接收或不合格(阻燃等级为 V-0 才合格)
    "quality.result_matches_recorded_value": "SELECT COUNT(*) FROM (SELECT result, CASE WHEN property_name LIKE '阻燃%' THEN actual_value='V-0' "
        "ELSE CAST(actual_value AS REAL) BETWEEN CAST(substr(standard_value,1,instr(standard_value,'~')-1) AS REAL) "
        "AND CAST(substr(standard_value,instr(standard_value,'~')+1) AS REAL) END ok FROM fact_quality_inspection) WHERE ok <> (result='合格')",
}


def run(con: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, sql in ZERO_CHECKS.items():
        v = con.execute(sql).fetchone()[0] or 0
        out[name] = {"value": v, "expected": 0, "pass": v == 0}
    # 投料批次可追溯:期初批次或某张合格收货单(同一原料)
    gr = {g[0]: g[1] for g in con.execute("SELECT g.gr_id, l.material_id FROM fact_goods_receipt g JOIN fact_po_line l ON l.po_id=g.po_id WHERE g.inspection_result='pass'")}
    bad = 0
    for _mid, batch in con.execute("SELECT material_id, batch_no FROM fact_material_consumption"):
        if batch.endswith("-INIT"):
            continue
        try:
            gid = int(batch.rsplit("-", 1)[1])
        except ValueError:
            bad += 1
            continue
        bad += int(gid not in gr)
    out["prod.batch_traceable_to_receipt"] = {"value": bad, "expected": 0, "pass": bad == 0}
    # 产线日停机 = 停机事件(不含计划保养)按日切分合计
    from datetime import datetime, timedelta
    ev: dict[tuple[int, str], float] = {}
    for lid, st, en in con.execute("SELECT line_id, start_time, end_time FROM fact_downtime WHERE reason_code<>3"):
        a, b = datetime.fromisoformat(st), datetime.fromisoformat(en)
        d0 = datetime(a.year, a.month, a.day)
        while d0 < b:
            d1 = d0 + timedelta(days=1)
            h = (min(b, d1) - max(a, d0)).total_seconds() / 3600
            if h > 0 and d0.date().isoformat() <= END:
                ev[(lid, d0.date().isoformat())] = ev.get((lid, d0.date().isoformat()), 0.0) + h
            d0 = d1
    rt = {(lid, d): h for lid, d, h in con.execute("SELECT line_id, record_date, downtime_hours FROM fact_equipment_runtime")}
    bad_dt = sum(1 for k in set(ev) | set(rt) if abs(ev.get(k, 0.0) - rt.get(k, 0.0)) > 0.02)
    out["equip.runtime_downtime_equals_events"] = {"value": bad_dt, "expected": 0, "pass": bad_dt == 0}
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    out["integrity.declared_foreign_keys"] = {"value": len(fk), "expected": 0, "pass": not fk}
    empty = [t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'") if not con.execute(f'SELECT 1 FROM "{t}" LIMIT 1').fetchone()]
    out["integrity.no_empty_tables"] = {"value": empty, "expected": [], "pass": not empty}
    all_null = []
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for c in con.execute(f'PRAGMA table_info("{t}")').fetchall():
            if con.execute(f'SELECT COUNT("{c[1]}") FROM "{t}"').fetchone()[0] == 0:
                all_null.append(f"{t}.{c[1]}")
    # 一级会计科目没有上级;派生指标配置沿用原指标目录,未配置筛选列
    all_null = [c for c in all_null if c not in ("dim_account.parent_id", "dim_derived_metric_config.filter_column")]
    out["integrity.no_all_null_columns"] = {"value": all_null, "expected": [], "pass": not all_null}
    meta = con.execute("SELECT COUNT(*) FROM dim_column_metadata WHERE column_name_cn IS NULL OR column_name_cn='' OR column_name_cn=column_name").fetchone()[0]
    total = sum(len(con.execute(f'PRAGMA table_info("{t}")').fetchall()) for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
    rows = con.execute("SELECT COUNT(*) FROM dim_column_metadata").fetchone()[0]
    out["metadata.every_column_named"] = {"value": {"missing_cn": meta, "rows": rows, "columns": total}, "expected": "0 / 全覆盖", "pass": meta == 0 and rows == total}
    # 负荷:各车间产能利用率在合理区间
    util = con.execute("""SELECT l.workshop_id, SUM(r.actual_run_hours)/(COUNT(DISTINCT l.line_id)*546*24.0)
                          FROM fact_equipment_runtime r JOIN dim_production_line l USING(line_id) GROUP BY 1""").fetchall()
    out["realism.workshop_utilization"] = {"value": {w: round(u, 2) for w, u in util}, "expected": "0.45~0.92",
                                           "pass": all(0.45 <= u <= 0.92 for _w, u in util)}
    lead = con.execute("SELECT AVG(julianday(d.delivery_date)-julianday(o.order_date)) FROM fact_delivery d JOIN fact_sales_order o USING(order_id)").fetchone()[0]
    out["realism.order_to_delivery_days"] = {"value": round(lead, 1), "expected": "5~30", "pass": 5 <= lead <= 30}
    margin = con.execute("SELECT SUM(gross_profit_actual)/SUM(amount) FROM fact_sales_order").fetchone()[0]
    out["realism.gross_margin"] = {"value": round(margin, 3), "expected": "0.08~0.30", "pass": 0.08 <= margin <= 0.30}
    dso = con.execute("SELECT AVG(julianday(p.payment_date)-julianday(i.invoice_date)) FROM fact_payment p JOIN fact_ar_aging a USING(ar_id) JOIN fact_invoice i USING(invoice_id)").fetchone()[0]
    out["realism.days_to_collect"] = {"value": round(dso, 1), "expected": "30~110", "pass": 30 <= dso <= 110}
    return out


def main() -> int:
    con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    r = run(con)
    failed = {k: v for k, v in r.items() if not v["pass"]}
    print(json.dumps({"checks": len(r), "failed": failed}, ensure_ascii=False, indent=2, default=str))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
