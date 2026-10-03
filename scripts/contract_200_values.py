#!/usr/bin/env python3
"""Row generation for the contract-aligned 200-table synthetic database (generator v2).

References are derived along business chains, measures are drawn per table
from the record's own context, and every attribute column has a generator
that reads the record (no placeholder strings).  Standard library only.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta
from typing import Any

import contract_200_semantics as S

PARENT_DATE_COLUMNS = ("business_date", "order_date", "po_date", "delivery_date", "invoice_date", "wo_date",
                       "training_date", "start_date", "posting_date", "hire_date", "install_date", "cooperation_since",
                       "purchase_date")


# ───────────────────────── 引用推导 ─────────────────────────

def _zipf_pick(rng: random.Random, seq: list[Any], skew: float = 1.1) -> Any:
    """偏态挑选:少数父记录被频繁引用,多数偶尔被引用(真实业务的长尾分布)。"""
    weights = [1.0 / ((i + 1) ** skew) for i in range(len(seq))]
    order = list(seq)
    random.Random(len(seq) * 7919).shuffle(order)          # 固定打乱,热门父记录不总是 id 最小的那几个
    return rng.choices(order, weights=weights, k=1)[0]


def derive_refs(table: str, refs: list[Any], n: int, ctx: S.Context, rng: random.Random) -> list[dict[str, Any]]:
    """按业务链给 n 行推导引用列。refs 为 TableSpec.refs(column, parent_table, parent_column, nullable)。"""
    sem = S.FACT_SEMANTICS.get(table, {})
    rows: list[dict[str, Any]] = [{} for _ in range(n)]
    header = sem.get("line_of")

    def default(i: int, ref: Any) -> Any:
        return _zipf_pick(rng, ctx.ids(ref.parent_table, ref.parent_column))

    # 明细表:先分配表头,其余引用沿表头推导
    if header:
        col, htable = header
        hkey = "order_id" if htable == "fact_sales_order" else col
        if table == "fact_sales_order_line":
            orders = sorted(ctx.ids("fact_sales_order", "order_id"))
            chosen = sorted(rng.sample(orders, n // 2))
            assign = [o for o in chosen for _ in range(2)]
        elif table == "fact_gl_entry_line":
            heads = sorted(ctx.ids("fact_gl_entry", "gl_entry_id"))
            assign = [h for h in heads for _ in range(2)][:n]
        elif table == "fact_tax_invoice_line":
            invs = sorted(ctx.ids("fact_invoice", "invoice_id"))
            chosen = sorted(rng.sample(invs, n // 2))
            assign = [o for o in chosen for _ in range(2)]
        elif table == "fact_shipment_line":
            # 每张发运单的明细 = 其交付对应订单的订单明细
            ship = ctx.rows("fact_shipment", "shipment_id")
            lines_by_order = ctx.group("fact_sales_order_line", "sales_order_line_id", "order_id")
            assign, so_lines = [], []
            for sid in sorted(ship):
                oid = ctx.rows("fact_delivery", "delivery_id")[ship[sid]["delivery_id"]]["order_id"]
                for ol in sorted(lines_by_order.get(oid, [])):
                    assign.append(sid)
                    so_lines.append(ol)
            assign, so_lines = assign[:n], so_lines[:n]
            rows = [{} for _ in assign]
            for i, (_sid, ol) in enumerate(zip(assign, so_lines)):
                rows[i]["sales_order_line_id"] = ol
                rows[i]["prod_id"] = ctx.rows("fact_sales_order_line", "sales_order_line_id")[ol]["prod_id"]
        elif table == "fact_asn_line":
            asn = ctx.rows("fact_advance_shipping_notice", "advance_shipping_notice_id")
            po_lines = ctx.group("fact_po_line", "line_id", "po_id")
            assign = S.allocate_lines(n, sorted(asn), rng, max_per_header=4)
            rows = [{} for _ in assign]
            used: dict[Any, int] = {}
            for i, aid in enumerate(assign):
                cand = sorted(po_lines[asn[aid]["po_id"]])
                k = used.get(aid, 0)
                used[aid] = k + 1
                pl = cand[k % len(cand)]
                rows[i]["po_line_id"] = pl
                rows[i]["material_id"] = ctx.rows("fact_po_line", "line_id")[pl]["material_id"]
        else:
            heads = sorted(ctx.ids(htable, hkey))
            assign = S.allocate_lines(n, heads, rng)
        if table not in ("fact_shipment_line", "fact_asn_line"):
            rows = [{} for _ in assign]
        line_no: dict[Any, int] = {}
        for i, h in enumerate(assign):
            rows[i][col] = h
            line_no[h] = line_no.get(h, 0) + 1
            rows[i]["_line_no"] = line_no[h]
            rows[i]["_header"] = h
        if table in ("fact_sales_order_line", "fact_tax_invoice_line"):
            # 两条明细恰好拆分表头:订单的数量与金额 / 发票的不含税金额
            for i in range(0, len(rows) - 1, 2):
                share = round(rng.uniform(0.3, 0.7), 4)
                if table == "fact_sales_order_line":
                    o = ctx.rows("fact_sales_order", "order_id")[rows[i]["order_id"]]
                    q, a = float(o["quantity"]), float(o["amount"])
                else:
                    inv = ctx.rows("fact_invoice", "invoice_id")[rows[i]["invoice_id"]]
                    q, a = 1.0, float(inv["amount_no_tax"])
                q1 = round(q * share, 6)
                if table == "fact_sales_order_line":
                    price = float(o["unit_price"])            # 明细金额 = 数量 × 单价,两行合计恰为订单金额
                    a1 = round(q1 * price, 2)
                else:
                    a1 = round(a * share, 2)
                rows[i].update({"_q": q1, "_amount": a1})
                rows[i + 1].update({"_q": round(q - q1, 6), "_amount": round(a - a1, 2)})

    for i, row in enumerate(rows):
        for ref in refs:
            if ref.column in row:
                continue
            row[ref.column] = _derive_one(table, ref, row, ctx, rng, i) if ref.column in _CHAIN.get(table, ()) \
                else default(i, ref)
        _post_derive(table, row, ctx, rng, i)
    return rows


# 沿业务链推导的列:表 → 列
_CHAIN = {
    "fact_sales_order_line": ("prod_id",),
    "fact_shipment": ("delivery_id", "cust_id", "warehouse_id"),
    "fact_tax_invoice_line": ("tax_code_id",),
    "fact_customer_complaint": ("cust_id", "prod_id"),
    "fact_price_adjustment": ("prod_id",),
    "fact_supplier_contract": ("supplier_id",),
    "fact_supplier_contract_line": ("material_id",),
    "fact_advance_shipping_notice": ("supplier_id", "po_id"),
    "fact_supplier_claim": ("supplier_id",),
    "fact_supplier_quality_issue": ("supplier_id",),
    "fact_inventory_movement": ("warehouse_id",),
    "fact_lot_trace": ("material_id",),
    "fact_work_order_operation": ("operation_id", "line_id"),
    "fact_work_order_material": ("material_id",),
    "fact_shift_report": ("shift_id",),
    "fact_scrap_event": ("prod_id",),
    "fact_process_parameter_record": ("equipment_id", "process_parameter_id"),
    "fact_labor_booking": ("shift_id",),
    "fact_wip_snapshot": ("line_id",),
    "fact_inspection_lot": ("prod_id",),
    "fact_inspection_result": ("quality_characteristic_id",),
    "fact_predictive_alert": ("equipment_id",),
    "fact_condition_monitoring": ("equipment_id",),
    "fact_spare_part_issue": ("equipment_id", "part_id"),
    "fact_gl_entry_line": ("account_id",),
    "fact_capacity_plan": ("shift_id",),
    "fact_hazard_observation": ("risk_level_id",),
    "fact_safety_action": ("risk_level_id",),
    "fact_employee_assignment": ("dept_id", "job_position_id"),
    "fact_overtime": ("dept_id",),
    "fact_kpi_snapshot": ("dept_id",),
    "fact_attendance": ("shift_id",),
}


def _risk_level(severity: int, likelihood: int, ctx: S.Context) -> Any:
    levels = ctx.ids("dim_risk_level", "level_id")
    score = severity * likelihood
    idx = 0 if score <= 4 else 1 if score <= 9 else 2 if score <= 15 else 3
    return levels[min(idx, len(levels) - 1)]


def _derive_one(table: str, ref: Any, row: dict[str, Any], ctx: S.Context, rng: random.Random, i: int) -> Any:
    c = ref.column
    if table == "fact_sales_order_line" and c == "prod_id":
        return ctx.rows("fact_sales_order", "order_id")[row["order_id"]]["prod_id"]
    if table == "fact_shipment" and c == "delivery_id":
        return _shipment_delivery(ctx, rng, i)
    if table == "fact_tax_invoice_line" and c == "tax_code_id":
        return next(k for k, r in ctx.rows("dim_tax_code", "tax_code_id").items() if r["code"] == "VAT13")
    if table == "fact_shipment":
        if "delivery_id" not in row:
            row["delivery_id"] = _shipment_delivery(ctx, rng, i)
        order = ctx.rows("fact_sales_order", "order_id")[ctx.rows("fact_delivery", "delivery_id")[row["delivery_id"]]["order_id"]]
        if c == "cust_id":
            return order["cust_id"]
        return _zipf_pick(rng, ctx.ids("dim_warehouse", "warehouse_id"))
    if table == "fact_customer_complaint":
        if "order_id" not in row:
            row["order_id"] = _zipf_pick(rng, ctx.ids("fact_sales_order", "order_id"))
        return ctx.rows("fact_sales_order", "order_id")[row["order_id"]][c]
    if table == "fact_price_adjustment" and c == "prod_id":
        if "pricing_id" not in row:
            row["pricing_id"] = _zipf_pick(rng, ctx.ids("dim_pricing", "pricing_id"))
        return ctx.rows("dim_pricing", "pricing_id")[row["pricing_id"]]["product_id"]
    if c == "supplier_id" and table in ("fact_supplier_contract",):
        with_mat = sorted({r["supplier_id"] for r in ctx.rows("dim_material", "material_id").values()})
        return _zipf_pick(rng, with_mat)
    if table == "fact_supplier_contract_line":
        sup = ctx.rows("fact_supplier_contract", "supplier_contract_id")[row["supplier_contract_id"]]["supplier_id"]
        return pick_or(rng, [m for m, r in ctx.rows("dim_material", "material_id").items() if r["supplier_id"] == sup],
                       ctx.ids("dim_material", "material_id"))
    if table == "fact_advance_shipping_notice":
        if c == "po_id":
            return _zipf_pick(rng, sorted(ctx.group("fact_po_line", "line_id", "po_id")))
        if "po_id" not in row:
            row["po_id"] = _zipf_pick(rng, sorted(ctx.group("fact_po_line", "line_id", "po_id")))
        return ctx.rows("fact_purchase_order", "po_id")[row["po_id"]]["supplier_id"]
    if table in ("fact_supplier_claim", "fact_supplier_quality_issue") and c == "supplier_id":
        if "material_id" not in row:
            row["material_id"] = _zipf_pick(rng, ctx.ids("dim_material", "material_id"))
        return ctx.rows("dim_material", "material_id")[row["material_id"]]["supplier_id"]
    if table == "fact_inventory_movement" and c == "warehouse_id":
        if "location_id" not in row:
            row["location_id"] = _zipf_pick(rng, ctx.ids("dim_storage_location", "location_id"))
        return ctx.rows("dim_storage_location", "location_id")[row["location_id"]]["warehouse_id"]
    if c in ("prod_id", "line_id") and "order_id" in row and table in (
            "fact_scrap_event", "fact_inspection_lot", "fact_wip_snapshot", "fact_work_order_operation"):
        return ctx.rows("fact_production_order", "order_id")[row["order_id"]][c]
    if c == "material_id" and table in ("fact_lot_trace", "fact_work_order_material"):
        _ensure(row, "order_id", lambda: _zipf_pick(rng, ctx.ids("fact_production_order", "order_id")))
        prod = ctx.rows("fact_production_order", "order_id")[row["order_id"]]["prod_id"]
        return pick_or(rng, ctx.bom_materials(prod), ctx.ids("dim_material", "material_id"))
    if table == "fact_work_order_operation" and c == "operation_id":
        _ensure(row, "order_id", lambda: _zipf_pick(rng, ctx.ids("fact_production_order", "order_id")))
        prod = ctx.rows("fact_production_order", "order_id")[row["order_id"]]["prod_id"]
        return pick_or(rng, ctx.routing_operations(prod), ctx.ids("dim_operation", "operation_id"))
    if table == "fact_process_parameter_record":
        _ensure(row, "order_id", lambda: _zipf_pick(rng, ctx.ids("fact_production_order", "order_id")))
        line = ctx.rows("fact_production_order", "order_id")[row["order_id"]]["line_id"]
        if c == "equipment_id":
            return pick_or(rng, [e for e, r in ctx.rows("dim_equipment", "equipment_id").items() if r["line_id"] == line],
                           ctx.ids("dim_equipment", "equipment_id"))
        _ensure(row, "equipment_id", lambda: _derive_one(table, type("R", (), {"column": "equipment_id"})(), row, ctx, rng, i))
        etype = ctx.rows("dim_equipment", "equipment_id")[row["equipment_id"]]["type_id"]
        return pick_or(rng, [p for p, r in ctx.rows("dim_process_parameter", "process_parameter_id").items() if r["type_id"] == etype],
                       ctx.ids("dim_process_parameter", "process_parameter_id"))
    if c == "shift_id":
        if table in ("fact_shift_report", "fact_capacity_plan"):
            _ensure(row, "line_id", lambda: _zipf_pick(rng, ctx.ids("dim_production_line", "line_id")))
            ws = ctx.line_workshop(row["line_id"])
        elif table == "fact_labor_booking":
            _ensure(row, "order_id", lambda: _zipf_pick(rng, ctx.ids("fact_production_order", "order_id")))
            ws = ctx.line_workshop(ctx.rows("fact_production_order", "order_id")[row["order_id"]]["line_id"])
        else:
            ws = pick(rng, sorted({r["workshop_id"] for r in ctx.rows("dim_shift", "shift_id").values()}))
        return pick_or(rng, ctx.shifts_of_workshop(ws), ctx.ids("dim_shift", "shift_id"))
    if table == "fact_inspection_result" and c == "quality_characteristic_id":
        prod = ctx.rows("fact_inspection_lot", "inspection_lot_id")[row["inspection_lot_id"]]["prod_id"]
        return pick_or(rng, [q for q, r in ctx.rows("dim_quality_characteristic", "quality_characteristic_id").items()
                             if r["prod_id"] == prod], ctx.ids("dim_quality_characteristic", "quality_characteristic_id"))
    if c == "equipment_id" and table in ("fact_predictive_alert", "fact_condition_monitoring"):
        _ensure(row, "sensor_id", lambda: _zipf_pick(rng, ctx.ids("dim_sensor", "sensor_id")))
        return ctx.rows("dim_sensor", "sensor_id")[row["sensor_id"]]["equipment_id"]
    if table == "fact_spare_part_issue" and c == "equipment_id":
        typed = {r["type_id"] for r in ctx.rows("dim_equipment", "equipment_id").values()}
        _ensure(row, "part_id", lambda: _zipf_pick(rng, [p for p, r in ctx.rows("dim_spare_part", "part_id").items()
                                                         if r["equipment_type_id"] in typed] or ctx.ids("dim_spare_part", "part_id")))
        etype = ctx.rows("dim_spare_part", "part_id")[row["part_id"]]["equipment_type_id"]
        return pick_or(rng, [e for e, r in ctx.rows("dim_equipment", "equipment_id").items() if r["type_id"] == etype],
                       ctx.ids("dim_equipment", "equipment_id"))
    if table == "fact_spare_part_issue" and c == "part_id":
        etype = ctx.rows("dim_equipment", "equipment_id")[row["equipment_id"]]["type_id"]
        return pick_or(rng, [p for p, r in ctx.rows("dim_spare_part", "part_id").items() if r["equipment_type_id"] == etype],
                       ctx.ids("dim_spare_part", "part_id"))
    if table == "fact_gl_entry_line" and c == "account_id":
        if row.get("_line_no") == 1:
            return ctx.rows("fact_gl_entry", "gl_entry_id")[row["gl_entry_id"]]["account_id"]
        return _zipf_pick(rng, ctx.ids("dim_account", "account_id"))
    if c == "risk_level_id":
        sev, lik = rng.randint(1, 5), rng.randint(1, 5)
        row["_severity"], row["_likelihood"] = sev, lik
        return _risk_level(sev, lik, ctx)
    if c in ("dept_id", "job_position_id") and "emp_id" in row:
        dept = ctx.rows("dim_employee", "emp_id")[row["emp_id"]]["dept_id"]
        if c == "dept_id":
            return dept
        return pick_or(rng, [j for j, r in ctx.rows("dim_job_position", "job_position_id").items() if r["dept_id"] == dept],
                       ctx.ids("dim_job_position", "job_position_id"))
    return _zipf_pick(rng, ctx.ids(ref.parent_table, ref.parent_column))


def _shipment_delivery(ctx: S.Context, rng: random.Random, i: int) -> Any:
    """发运单取「订单有明细」的交付,每单一次,保证发运明细能沿订单明细推导。"""
    if not ctx._ship_deliveries:
        with_lines = set(ctx.group("fact_sales_order_line", "sales_order_line_id", "order_id"))
        dels = sorted(d for d, r in ctx.rows("fact_delivery", "delivery_id").items() if r["order_id"] in with_lines)
        seen, chosen = set(), []
        for d in dels:
            o = ctx.rows("fact_delivery", "delivery_id")[d]["order_id"]
            if o not in seen:
                seen.add(o)
                chosen.append(d)
        random.Random(17).shuffle(chosen)
        ctx._ship_deliveries = chosen
    return ctx._ship_deliveries[i % len(ctx._ship_deliveries)]


def _ensure(row: dict[str, Any], key: str, make: Any) -> None:
    if key not in row:
        row[key] = make()


def pick(rng: random.Random, seq: list[Any]) -> Any:
    return seq[rng.randrange(len(seq))]


def pick_or(rng: random.Random, seq: list[Any], fallback: list[Any]) -> Any:
    return pick(rng, seq) if seq else pick(rng, fallback)


def _post_derive(table: str, row: dict[str, Any], ctx: S.Context, rng: random.Random, i: int) -> None:
    if table == "fact_legacy_material_issue":
        prod = ctx.rows("fact_production_order", "order_id")[row["order_id"]]["prod_id"]
        row["_material_id"] = pick_or(rng, ctx.bom_materials(prod), ctx.ids("dim_material", "material_id"))
    if table == "fact_cash_flow":
        if i % 2:
            row["supplier_id"] = None
        else:
            row["cust_id"] = None
    for a, b in (("source_warehouse_id", "target_warehouse_id"), ("source_factory_id", "target_factory_id")):
        if a in row and b in row and row[a] == row[b]:
            pool = ctx.ids("dim_warehouse", "warehouse_id") if "warehouse" in a else ctx.ids("dim_factory", "factory_id")
            row[b] = pool[(pool.index(row[b]) + 1) % len(pool)]


# ───────────────────────── 日期 ─────────────────────────

def parent_dates(table: str, row: dict[str, Any], refs: list[Any], ctx: S.Context) -> list[date]:
    out = []
    for ref in refs:
        v = row.get(ref.column)
        if v is None:
            continue
        rec = ctx.rows(ref.parent_table, ref.parent_column).get(v) or {}
        for c in PARENT_DATE_COLUMNS:
            d = S.as_date(rec.get(c))
            if d:
                out.append(d)
                break
    return out


# ───────────────────────── 维表行 ─────────────────────────

def dim_record(spec: Any, idx: int, refs: dict[str, Any], ctx: S.Context, rng: random.Random,
               extra: dict[str, Any] | None = None) -> dict[str, Any]:
    t = spec.name
    code, name = _dim_identity(t, idx, spec)
    created = datetime(2024, 1, 1) + timedelta(days=rng.randrange(0, 360), hours=rng.randrange(8, 18))
    inactive = t not in ("dim_calendar", "dim_currency", "dim_unit_of_measure", "dim_dataset_release", "dim_validation_case") \
        and spec.rows >= 10 and rng.random() < 0.08
    eff_from = date(2024, 1, 1) + timedelta(days=rng.randrange(0, 365))
    rec: dict[str, Any] = {
        "code": code, "name": name, "name_en": _dim_name_en(t, idx, code), "short_name": name[:6],
        "description": f"{spec.cn_name}:{name}",
        "status": "INACTIVE" if inactive else "ACTIVE", "parent_code": None,
        "category_code": spec.domain, "type_code": t.removeprefix("dim_").upper(),
        "hierarchy_level": 1, "sort_order": idx,
        "effective_from": eff_from.isoformat(),
        "effective_to": (date(2026, 3, 31) if inactive else date(9999, 12, 31)).isoformat(),
        "external_code": f"{S.SOURCE_BY_DOMAIN.get(spec.domain, 'MDM')}-{code}",
        "legacy_code": None if rng.random() < 0.4 else f"OLD{rng.randrange(10000, 99999)}",
        "source_system": S.SOURCE_BY_DOMAIN.get(spec.domain, "MDM"), "source_record_id": f"{t}:{idx}",
        "tenant_code": "DEMO_TENANT", "approval_status": "APPROVED",
        "approved_at": S.iso(created + timedelta(hours=rng.randrange(1, 72))),
        "created_at": S.iso(created), "updated_at": S.iso(created + timedelta(days=rng.randrange(0, 200))),
        "created_by": pick(rng, S.USERS), "updated_by": pick(rng, S.USERS),
        "version_no": 1 + (rng.random() < 0.25) + (rng.random() < 0.05),
        "data_quality_score": round(rng.uniform(0.88, 1.0), 3), "is_deleted": 0,
        "remarks": None,
    }
    rec.update(refs)
    rec.update(extra or {})
    for col, _typ in S.DIM_ATTRS[t]:
        rec[col] = dim_attr(t, col, idx, rec, ctx, rng)
    return rec


def _dim_identity(t: str, idx: int, spec: Any) -> tuple[str, str]:
    lists = {
        "dim_currency": [(c[0], c[1]) for c in S.CURRENCIES], "dim_unit_of_measure": S.UNITS,
        "dim_reason_code": S.REASONS, "dim_tax_code": [(c[0], c[1]) for c in S.TAX_CODES],
        "dim_payment_term": [(c[0], c[1]) for c in S.PAYMENT_TERMS], "dim_incoterm": S.INCOTERMS,
        "dim_contract_type": [(c[0], c[1]) for c in S.CONTRACT_TYPES],
        "dim_energy_type": [(c[0], c[1]) for c in S.ENERGY_TYPES], "dim_emission_type": [(c[0], c[1]) for c in S.EMISSION_TYPES],
    }
    if t in lists:
        return lists[t][idx - 1]
    if t == "dim_calendar":
        d = S.DATE_START + timedelta(days=idx - 1)
        return d.strftime("%Y%m%d"), d.strftime("%Y年%m月%d日")
    if t == "dim_shift":
        ws = (idx - 1) // 3 + 1
        sh = S.SHIFTS[(idx - 1) % 3]
        return f"WS{ws}-{sh[0]}", f"{ws}号车间{sh[1]}"
    names = {
        "dim_quality_characteristic": ["熔体流动速率", "拉伸强度", "弯曲模量", "缺口冲击强度", "热变形温度", "灰分", "水分", "色差 ΔE",
                                       "密度", "阻燃等级", "断裂伸长率", "维卡软化点"],
        "dim_inspection_method": ["熔指仪法", "万能试验机拉伸", "悬臂梁冲击", "热变形测试仪", "马弗炉灼烧", "卡尔费休水分", "色差仪",
                                  "密度天平", "垂直燃烧", "目视检查", "尺寸三坐标", "红外光谱"],
        "dim_customer_segment": ["战略客户", "重点客户", "成长客户", "一般客户", "新客户", "流失预警客户", "海外客户", "渠道客户",
                                 "项目型客户", "零散客户"],
        "dim_sales_channel": ["直销", "经销商", "电商平台", "大客户部", "海外代理", "招投标", "OEM 合作", "门店"],
        "dim_market_segment": ["家电", "汽车零部件", "电子电气", "包装", "建材", "医疗器械", "新能源", "轨道交通", "日用品", "管材",
                               "纺织", "农业"],
        "dim_asset_class": ["房屋建筑", "生产设备", "检测设备", "运输工具", "办公设备", "电子设备", "动力设备", "环保设施", "仓储设施",
                            "软件", "模具", "其他固定资产"],
        "dim_skill": ["挤出操作", "注塑调机", "配色", "设备点检", "叉车驾驶", "电工", "焊接", "质量检验", "统计过程控制", "精益改善",
                      "安全管理", "化学品管理", "仓储管理", "计划排程", "成本核算", "数据分析", "PLC 编程", "液压维修", "模具维修", "危化品运输"],
        "dim_certification": ["特种设备操作证", "电工证", "焊工证", "叉车证", "危化品安全证", "内审员证", "安全管理员证", "压力容器证",
                              "登高作业证", "有限空间作业证", "消防设施操作员", "计量检定员", "环境管理体系内审员", "6σ 绿带", "6σ 黑带", "PMP"],
        "dim_job_position": ["操作工", "班组长", "工艺工程师", "设备工程师", "质量工程师", "检验员", "计划员", "仓管员", "采购员", "销售代表",
                             "客服专员", "会计", "人事专员", "安全员", "环保专员", "IT 工程师", "研发工程师", "车间主任", "物流专员", "数据分析师"],
        "dim_process_parameter": ["料筒温度一区", "料筒温度二区", "料筒温度三区", "螺杆转速", "喂料速度", "熔体压力", "熔体温度", "真空度",
                                  "冷却水温", "切粒转速", "牵引速度", "主机电流", "侧喂转速", "模头温度", "干燥温度"],
    }
    if t in names:
        lst = names[t]
        base = lst[(idx - 1) % len(lst)]
        suffix = "" if idx <= len(lst) else f"-{(idx - 1) // len(lst) + 1}"
        return f"{t.removeprefix('dim_').upper()[:8]}-{idx:03d}", base + suffix
    if t == "dim_supplier_site":
        return f"SITE-{idx:03d}", f"供应商地点{idx:02d}"
    if t == "dim_project":
        kinds = ["产线扩建", "节能改造", "数字化车间", "环保设施升级", "新品试制", "仓库自动化", "质量实验室建设", "光伏屋顶", "安全隐患整改", "能源计量升级"]
        return f"PRJ-2025-{idx:03d}", f"{kinds[(idx - 1) % len(kinds)]}项目{(idx - 1) // len(kinds) + 1}期"
    if t == "dim_dataset_release":
        return "REL-CV200-V2", "合同验证库 v2"
    if t == "dim_validation_case":
        return f"VC-{idx:03d}", f"验证案例{idx}"
    return f"{t.removeprefix('dim_').upper()}-{idx:04d}", f"{spec.cn_name}{idx}"


def _dim_name_en(t: str, idx: int, code: str) -> str:
    return f"{t.removeprefix('dim_').replace('_', ' ').title()} {code}"


def dim_attr(t: str, col: str, idx: int, rec: dict[str, Any], ctx: S.Context, rng: random.Random) -> Any:
    if t == "dim_calendar":
        d = S.DATE_START + timedelta(days=idx - 1)
        hol = _holiday(d)
        return {"calendar_date": d.isoformat(), "calendar_year": d.year, "calendar_quarter": (d.month - 1) // 3 + 1,
                "calendar_month": d.month, "calendar_week": int(d.strftime("%V")), "day_of_month": d.day,
                "day_of_week": d.isoweekday(), "is_workday": int(d.isoweekday() <= 5 and not hol),
                "is_holiday": int(bool(hol)), "holiday_name": hol, "fiscal_year": d.year, "fiscal_period": f"{d.year}-{d.month:02d}",
                "week_start_date": (d - timedelta(days=d.isoweekday() - 1)).isoformat(),
                "month_end_flag": int((d + timedelta(days=1)).day == 1)}[col]
    if t == "dim_shift":
        sh = S.SHIFTS[(idx - 1) % 3]
        start, end = sh[2], sh[3]
        span = 8.0
        return {"shift_start": start, "shift_end": end, "break_minutes": sh[4], "paid_hours": round(span - sh[4] / 60, 2),
                "crosses_midnight": int(sh[0] == "SWING"), "timezone_name": "Asia/Shanghai",
                "night_allowance_rate": 0.3 if sh[0] == "NIGHT" else 0.15 if sh[0] == "SWING" else 0.0,
                "team_size": rng.randint(8, 20)}[col]
    if t == "dim_currency":
        c = S.CURRENCIES[idx - 1]
        return {"symbol": c[2], "numeric_code": c[3], "decimal_places": c[4],
                "country_code": {"CNY": "CN", "USD": "US", "EUR": "EU", "JPY": "JP"}[c[0]], "rate_to_cny": S.RATES[c[0]],
                "rate_source": "中国人民银行中间价", "is_functional": int(c[0] == "CNY")}[col]
    if t == "dim_unit_of_measure":
        u = S.UNITS[idx - 1][0]
        dim = {"EA": "数量", "SET": "数量", "KG": "质量", "TON": "质量", "HOUR": "时间", "MIN": "时间", "KWH": "能量", "M3": "体积",
               "METER": "长度", "PCT": "比例"}[u]
        base = {"KG": "KG", "TON": "KG", "HOUR": "MIN", "MIN": "MIN", "EA": "EA", "SET": "EA", "KWH": "KWH", "M3": "M3", "METER": "METER", "PCT": "PCT"}[u]
        factor = {"TON": 1000.0, "HOUR": 60.0}.get(u, 1.0)
        return {"dimension_type": dim, "base_unit_code": base, "conversion_factor": factor,
                "symbol": {"EA": "个", "SET": "套", "KG": "kg", "TON": "t", "HOUR": "h", "MIN": "min", "KWH": "kWh", "M3": "m³", "METER": "m", "PCT": "%"}[u],
                "decimal_places": 0 if u in ("EA", "SET") else 3, "allow_fraction": int(u not in ("EA", "SET")),
                "reference_standard": "GB 3100-1993"}[col]
    if t == "dim_tax_code":
        c = S.TAX_CODES[idx - 1]
        return {"tax_rate": c[2], "tax_type": "增值税", "invoice_type": "增值税专用发票" if c[2] >= 0.06 else "增值税普通发票",
                "deductible_flag": int(c[2] >= 0.06), "legal_basis": "增值税暂行条例", "country_code": "CN",
                "accounting_scope": "销项与进项"}[col]
    if t == "dim_payment_term":
        c = S.PAYMENT_TERMS[idx - 1]
        return {"net_days": c[2], "discount_days": 10 if c[3] else 0, "discount_rate": c[3],
                "payment_method": "信用证" if c[0].startswith("LC") else "电汇" if c[0].startswith("TT") else "银行转账",
                "settlement_method": "预付" if c[0] == "PREPAY" else "到期结算", "installment_count": 1,
                "applies_to": "采购与销售"}[col]
    if t == "dim_incoterm":
        c = S.INCOTERMS[idx - 1][0]
        seller_main = c in ("CFR", "CIF", "DAP", "DPU", "DDP")
        return {"incoterm_version": "Incoterms 2020", "risk_transfer_point": "目的地" if c in ("DAP", "DPU", "DDP") else "装运地",
                "freight_paid_by": "卖方" if seller_main else "买方", "insurance_paid_by": "卖方" if c == "CIF" else "买方",
                "export_clearance_by": "买方" if c == "EXW" else "卖方", "import_clearance_by": "卖方" if c == "DDP" else "买方",
                "transport_mode": "海运" if c in ("FOB", "CFR", "CIF") else "任意方式"}[col]
    if t == "dim_reason_code":
        grp = ["内部", "外部"][int(S.REASONS[idx - 1][0] in ("CUSTOMER", "SUPPLIER", "ENVIRONMENT"))]
        return {"reason_group": grp, "severity_level": 1 + (idx % 3), "requires_approval": int(idx % 3 == 0),
                "allow_manual_entry": int(S.REASONS[idx - 1][0] != "SYSTEM"), "usage_scope": "全业务",
                "abbreviation": S.REASONS[idx - 1][0][:3], "display_value": S.REASONS[idx - 1][1]}[col]
    if t == "dim_quality_characteristic":
        spec_by = {"熔体流动速率": (12.0, "g/10min", 2.0), "拉伸强度": (55.0, "MPa", 5.0), "弯曲模量": (2400.0, "MPa", 200.0),
                   "缺口冲击强度": (8.0, "kJ/m2", 2.0), "热变形温度": (110.0, "℃", 8.0), "灰分": (0.5, "%", 0.3), "水分": (0.08, "%", 0.05),
                   "色差 ΔE": (0.5, "", 0.5), "密度": (1.05, "g/cm3", 0.03), "阻燃等级": (0.0, "级", 0.0), "断裂伸长率": (30.0, "%", 10.0),
                   "维卡软化点": (95.0, "℃", 6.0)}
        base = rec["name"].split("-")[0]
        nom, unit, tol = spec_by.get(base, (10.0, "", 1.0))
        return {"characteristic_type": "计数型" if base in ("阻燃等级",) else "计量型", "measurement_method": "实验室检测",
                "nominal_value": nom, "lower_spec_limit": round(nom - tol, 3), "upper_spec_limit": round(nom + tol, 3),
                "unit_symbol": unit, "precision_scale": 2, "sample_frequency": rng.choice([1, 2, 4]),
                "inspection_level": rng.choice(["Ⅰ", "Ⅱ", "Ⅲ"]), "criticality": rng.choice(["关键", "重要", "一般"]),
                "calibration_required": 1, "control_plan_code": f"CP-{idx:03d}"}[col]
    if t == "dim_inspection_method":
        destructive = idx in (2, 3, 4, 5, 9)
        return {"method_type": "破坏性" if destructive else "非破坏性", "instrument_type": rec["name"].rstrip("法"),
                "reference_standard": rng.choice(["GB/T 1040", "GB/T 1043", "GB/T 3682", "ISO 527", "ISO 179", "企业标准"]),
                "sample_size": rng.choice([3, 5, 10]), "duration_minutes": rng.choice([10, 20, 30, 60]),
                "destructive_flag": int(destructive), "calibration_required": 1, "acceptance_rule": "全部样本在规格限内"}[col]
    if t == "dim_customer_segment":
        return {"segment_level": 1 + (idx - 1) // 4, "customer_size": ["大型", "中型", "小型"][(idx - 1) % 3],
                "annual_potential": round(lognorm(rng, 2e7), -3), "target_margin": round(rng.uniform(0.08, 0.25), 3),
                "service_level": ["白金", "金", "银", "标准"][(idx - 1) % 4], "credit_policy": ["月结90天", "月结60天", "月结30天", "款到发货"][(idx - 1) % 4],
                "account_strategy": rec["name"] + "经营策略"}[col]
    if t == "dim_sales_channel":
        return {"channel_type": "线上" if "电商" in rec["name"] else "线下", "commission_rate": round(rng.uniform(0.0, 0.06), 3),
                "responsible_role": "渠道经理", "service_level": rng.choice(["标准", "增强"]), "online_flag": int("电商" in rec["name"]),
                "market_scope": rng.choice(["国内", "海外", "国内外"])}[col]
    if t == "dim_market_segment":
        return {"industry_code": f"IND-{idx:02d}", "market_scope": "国内", "sales_region": pick(rng, sorted(set(S.REGION_BY_PROVINCE.values()))),
                "annual_potential": round(lognorm(rng, 5e8), -4), "growth_target": round(rng.uniform(0.02, 0.18), 3),
                "price_tier": rng.choice(["高端", "中端", "经济"]), "competitor_count": rng.randint(3, 15)}[col]
    if t == "dim_supplier_site":
        sup = ctx.rows("dim_supplier", "supplier_id").get(rec.get("supplier_id")) or {}
        return {"site_type": rng.choice(["生产基地", "仓库", "办事处"]), "country_code": "CN", "province": sup.get("province"),
                "city": sup.get("city"), "lead_time_days": rng.randint(3, 30), "minimum_order_qty": float(rng.choice([1, 5, 10, 20])),
                "delivery_rating": round(rng.uniform(3.0, 5.0), 1), "quality_rating": round(rng.uniform(3.0, 5.0), 1),
                "capacity_rating": round(rng.uniform(3.0, 5.0), 1), "approved_scope": rng.choice(["树脂", "助剂", "色母", "包材"])}[col]
    if t == "dim_contract_type":
        c = S.CONTRACT_TYPES[idx - 1]
        return {"default_term_days": c[2], "legal_template_no": f"LT-{c[0]}", "approval_level": "总经理" if c[2] >= 365 else "部门经理",
                "requires_legal_review": int(c[0] not in ("SPOT",)), "renewal_type": "自动续约" if c[2] >= 365 else "到期终止",
                "applies_to": "供应商" if c[0] in ("FRAME", "SPOT", "CONSIGN", "TOLL") else "通用"}[col]
    if t == "dim_project":
        start = date(2025, 1, 1) + timedelta(days=rng.randrange(0, 330))
        rec.setdefault("_start", start)
        start = rec["_start"]
        return {"project_type": rec["name"][:4], "investment_class": rng.choice(["技改", "基建", "环保", "研发"]),
                "planned_start_date": start.isoformat(), "planned_end_date": (start + timedelta(days=rng.randrange(60, 420))).isoformat(),
                "budget_ceiling": round(lognorm(rng, 3e6), -3), "project_stage": rng.choice(["立项", "实施", "验收", "关闭"]),
                "funding_source": rng.choice(["自有资金", "专项补贴", "银行贷款"]), "approval_level": rng.choice(["董事会", "总经理"]),
                "cost_object_code": f"CO-{rec['code']}"}[col]
    if t == "dim_asset_class":
        life = {"房屋建筑": 20, "运输工具": 4, "电子设备": 3, "软件": 5, "办公设备": 5}.get(rec["name"], 10)
        return {"asset_category": rec["name"], "depreciation_method": "年限平均法", "useful_life_years": life, "residual_rate": 0.05,
                "capitalization_threshold": 5000.0, "gl_account_code": "1601"}[col]
    if t == "dim_energy_type":
        e = S.ENERGY_TYPES[idx - 1]
        return {"medium_type": e[1], "measurement_unit": e[2],
                "standard_coal_factor": {"ELEC": 0.1229, "NGAS": 1.33, "STEAM": 0.1286, "DIESEL": 1.4571}.get(e[0], 0.0),
                "emission_factor": e[3], "scope_category": "范围二" if e[0] in ("ELEC", "STEAM", "HOTW", "CHW") else "范围一",
                "reporting_standard": "GB/T 32150", "meter_type": "智能电表" if e[2] == "KWH" else "流量计",
                "renewable_flag": int(e[0] == "PV")}[col]
    if t == "dim_emission_type":
        e = S.EMISSION_TYPES[idx - 1]
        return {"pollutant_category": "废气" if e[2] == "mg/m3" else "废水" if e[2] == "mg/L" else "其他", "measurement_unit": e[2],
                "regulatory_limit": e[3], "monitoring_frequency": rng.choice(["实时", "每日", "每月", "每季"]),
                "reporting_standard": "排污许可证", "calculation_method": "实测法" if e[3] else "核算法", "medium_type": "环境"}[col]
    if t == "dim_skill":
        return {"skill_family": rng.choice(["生产操作", "设备维护", "质量", "管理", "信息化"]), "proficiency_levels": 5,
                "assessment_method": rng.choice(["实操考核", "笔试", "答辩"]), "training_hours": float(rng.choice([8, 16, 24, 40])),
                "critical_skill": int(rng.random() < 0.3), "renewal_months": rng.choice([12, 24, 36])}[col]
    if t == "dim_certification":
        months = rng.choice([24, 36, 48, 72])
        return {"certificate_type": "操作资格" if "证" in rec["name"] else "专业资质", "issuing_authority": rng.choice(["应急管理局", "市场监管局", "行业协会", "认证机构"]),
                "validity_months": months, "renewal_required": 1, "mandatory_for_role": rng.choice(["操作工", "电工", "安全员", "质量工程师"]),
                "exam_fee": float(rng.choice([300, 500, 800, 1500]))}[col]
    if t == "dim_job_position":
        return {"job_family": rng.choice(["生产", "技术", "管理", "职能"]), "job_grade": f"P{rng.randint(3, 9)}",
                "minimum_experience_years": rng.randint(0, 8), "education_requirement": rng.choice(["中专", "大专", "本科", "硕士"]),
                "critical_position": int(rng.random() < 0.25), "headcount_plan": rng.randint(1, 30), "replacement_lead_days": rng.choice([30, 60, 90])}[col]
    if t == "dim_process_parameter":
        base = rec["name"].split("-")[0]
        unit, nom = ("℃", float(rng.randint(180, 260))) if "温" in base else ("rpm", float(rng.randint(200, 600))) if "转速" in base \
            else ("MPa", round(rng.uniform(5, 15), 1)) if "压力" in base else ("kPa", -80.0) if "真空" in base \
            else ("A", float(rng.randint(150, 400))) if "电流" in base else ("kg/h", float(rng.randint(300, 1200))) if "速度" in base else ("", 10.0)
        w = abs(nom) * 0.03 + 1
        return {"parameter_group": "温度" if unit == "℃" else "速度" if unit in ("rpm", "kg/h") else "压力" if "Pa" in unit else "电气",
                "parameter_unit": unit, "nominal_value": nom, "lower_control_limit": round(nom - w, 2), "upper_control_limit": round(nom + w, 2),
                "lower_spec_limit": round(nom - 2 * w, 2), "upper_spec_limit": round(nom + 2 * w, 2),
                "sampling_interval_seconds": rng.choice([1, 5, 10, 60]), "collection_method": "PLC 采集",
                "control_method": "SPC 控制图", "reaction_plan": "超控制限停机复核", "process_stage": rng.choice(["混料", "挤出", "冷却", "切粒", "干燥"])}[col]
    if t == "dim_dataset_release":
        return rec.get("_release", {}).get(col)
    if t == "dim_validation_case":
        return rec.get("_case", {}).get(col)
    raise KeyError(f"no generator for {t}.{col}")


def lognorm(rng: random.Random, median: float, spread: float = 0.45) -> float:
    return S.lognorm(rng, median, spread)


_HOLIDAYS = {
    (2025, 1, 1): "元旦", (2025, 1, 28): "春节", (2025, 1, 29): "春节", (2025, 1, 30): "春节", (2025, 1, 31): "春节",
    (2025, 2, 1): "春节", (2025, 2, 2): "春节", (2025, 2, 3): "春节", (2025, 2, 4): "春节", (2025, 4, 4): "清明节",
    (2025, 5, 1): "劳动节", (2025, 5, 2): "劳动节", (2025, 5, 5): "劳动节", (2025, 5, 31): "端午节", (2025, 6, 2): "端午节",
    (2025, 10, 1): "国庆节", (2025, 10, 2): "国庆节", (2025, 10, 3): "国庆节", (2025, 10, 6): "中秋节", (2025, 10, 7): "国庆节",
    (2025, 10, 8): "国庆节", (2026, 1, 1): "元旦", (2026, 2, 16): "春节", (2026, 2, 17): "春节", (2026, 2, 18): "春节",
    (2026, 2, 19): "春节", (2026, 2, 20): "春节", (2026, 4, 5): "清明节", (2026, 5, 1): "劳动节", (2026, 6, 19): "端午节",
}


def _holiday(d: date) -> str | None:
    return _HOLIDAYS.get((d.year, d.month, d.day))


# ───────────────────────── 事实表行 ─────────────────────────

def fact_record(spec: Any, idx: int, row: dict[str, Any], ctx: S.Context, rng: random.Random, seed: int,
                scale: dict[str, float]) -> dict[str, Any]:
    t = spec.name
    sem = S.FACT_SEMANTICS[t]
    rec: dict[str, Any] = {spec.pk: idx, **{k: v for k, v in row.items() if not k.startswith("_")}}
    rec["_row"] = row
    pdates = parent_dates(t, row, spec.refs, ctx)
    bday = S.day(rng.randrange(0, S.DATE_SPAN + 1))
    if pdates:
        bday = max(bday, max(pdates) + timedelta(days=rng.randrange(0, 15)))
    bday = min(bday, S.day(S.DATE_SPAN))
    if pdates and bday < max(pdates):
        bday = max(pdates)
    ev = datetime.combine(bday, datetime.min.time()) + timedelta(hours=rng.randrange(7, 20), minutes=rng.randrange(60))
    status = _status(rng, bday)
    rec.update({
        "document_no": f"{_doc_prefix(t)}{bday.strftime('%Y%m%d')}{idx:05d}",
        "business_date": bday.isoformat(), "event_time": S.iso(ev),
        "posting_date": (bday + timedelta(days=rng.choice([0, 0, 0, 1, 1, 2, 3]))).isoformat(),
        "status": status, "priority": rng.choices([1, 2, 3, 4, 5], weights=[5, 20, 50, 20, 5])[0],
        "reason_code": _reason_for(t, rng),
        "source_system": S.SOURCE_BY_DOMAIN.get(spec.domain, "ERP"), "source_record_id": f"{t}:{idx}",
        "etl_batch_id": f"BATCH-{bday.strftime('%Y%m%d')}", "trace_id": hashlib.sha256(f"{t}:{idx}:{seed}".encode()).hexdigest()[:32],
        "tenant_code": "DEMO_TENANT", "created_at": S.iso(ev), "updated_at": S.iso(ev + timedelta(minutes=rng.randrange(1, 2880))),
        "created_by": pick(rng, S.USERS), "updated_by": pick(rng, S.USERS),
        "approved_by": pick(rng, S.USERS) if status in ("APPROVED", "CLOSED") else None,
        "approved_at": S.iso(ev + timedelta(hours=rng.randrange(1, 48))) if status in ("APPROVED", "CLOSED") else None,
        "source_document_no": _source_doc(t, row, ctx),
        "row_version": 1 + (rng.random() < 0.3) + (rng.random() < 0.08),
        "data_quality_score": round(rng.uniform(0.9, 1.0), 3), "is_deleted": 0,
        "remarks": rng.choice(["加急", "客户指定批次", "补录", "已电话确认", "跨月处理"]) if rng.random() < 0.04 else None,
    })
    if sem.get("line_of"):
        rec["line_no"] = row["_line_no"]
    g = sem["groups"]
    if "Q" in g:
        _quantities(t, rec, row, ctx, rng, scale)
    if "M" in g:
        _money(t, rec, row, ctx, rng, scale)
    if "D" in g:
        rec["duration_minutes"] = round(S.lognorm(rng, scale.get("duration", 90.0), 0.6), 1)
    if "S" in g:
        rec["rate_value"] = round(min(0.999, max(0.3, rng.gauss(scale.get("rate", 0.9), 0.05))), 4)
        rec["score_value"] = round(min(100.0, max(40.0, rng.gauss(scale.get("score", 82.0), 8.0))), 1)
    for col, _typ in S.fact_columns(t):
        if col not in rec:
            rec[col] = fact_attr(t, col, rec, row, ctx, rng)
    rec.pop("_row", None)
    return rec


def _doc_prefix(t: str) -> str:
    return "".join(w[0] for w in t.removeprefix("fact_").split("_")).upper() + "-"


def _status(rng: random.Random, bday: date) -> str:
    age = (S.day(S.DATE_SPAN) - bday).days
    if age > 120:
        return rng.choices(["CLOSED", "APPROVED", "CANCELLED"], weights=[80, 17, 3])[0]
    if age > 30:
        return rng.choices(["CLOSED", "APPROVED", "IN_PROGRESS", "CANCELLED"], weights=[45, 30, 22, 3])[0]
    return rng.choices(["OPEN", "IN_PROGRESS", "APPROVED", "CANCELLED"], weights=[40, 35, 22, 3])[0]


_REASON_BY_TABLE = {
    "fact_scrap_event": "QUALITY", "fact_rework_order": "QUALITY", "fact_nonconformance": "QUALITY", "fact_customer_complaint": "CUSTOMER",
    "fact_supplier_claim": "SUPPLIER", "fact_supplier_quality_issue": "SUPPLIER", "fact_equipment_event": "EQUIPMENT",
    "fact_predictive_alert": "EQUIPMENT", "fact_hazard_observation": "SAFETY", "fact_safety_action": "SAFETY",
    "fact_emission_event": "ENVIRONMENT", "fact_environment_measurement": "ENVIRONMENT", "fact_data_quality_issue": "SYSTEM",
    "fact_validation_case_result": "SYSTEM", "fact_price_adjustment": "COST", "fact_cost_allocation": "COST",
}


def _reason_for(t: str, rng: random.Random) -> str:
    if t in _REASON_BY_TABLE and rng.random() < 0.8:
        return _REASON_BY_TABLE[t]
    return rng.choices([r[0] for r in S.REASONS], weights=[8, 10, 6, 6, 3, 2, 8, 6, 8, 8, 4, 31])[0]


def _source_doc(t: str, row: dict[str, Any], ctx: S.Context) -> str | None:
    if "order_id" in row and t in ("fact_scrap_event", "fact_inspection_lot", "fact_work_order_operation", "fact_work_order_material",
                                   "fact_lot_trace", "fact_wip_snapshot", "fact_tool_usage", "fact_rework_order", "fact_labor_booking",
                                   "fact_process_parameter_record", "fact_legacy_material_issue"):
        return ctx.rows("fact_production_order", "order_id")[row["order_id"]]["order_no"]
    if "po_id" in row:
        return ctx.rows("fact_purchase_order", "po_id")[row["po_id"]]["po_no"]
    if "order_id" in row and t in ("fact_sales_order_line", "fact_customer_complaint"):
        return f"SO{row['order_id']:06d}"
    if "delivery_id" in row:
        return f"DL{row['delivery_id']:06d}"
    if "wo_id" in row:
        return ctx.rows("fact_maintenance_wo", "wo_id")[row["wo_id"]]["wo_no"]
    if "contract_id" in row:
        return ctx.rows("fact_contract", "contract_id")[row["contract_id"]]["contract_no"]
    return None


def _unit_for(t: str, row: dict[str, Any], ctx: S.Context) -> str:
    sem = S.FACT_SEMANTICS[t]
    unit = sem.get("unit", "EA")
    if unit == "BY_ENERGY":
        code = ctx.rows("dim_energy_type", "energy_type_id")[row["energy_type_id"]]["code"]
        return next(e[2] for e in S.ENERGY_TYPES if e[0] == code)
    return unit


def _quantities(t: str, rec: dict[str, Any], row: dict[str, Any], ctx: S.Context, rng: random.Random, scale: dict[str, float]) -> None:
    rec["unit_code"] = _unit_for(t, row, ctx)
    q = None
    if t == "fact_sales_order_line":
        o = ctx.rows("fact_sales_order", "order_id")[row["order_id"]]
        q = row["_q"] if row.get("_q") is not None else round(float(o["quantity"]) * 0.5, 4)
    elif t == "fact_shipment_line":
        ol = ctx.rows("fact_sales_order_line", "sales_order_line_id")[row["sales_order_line_id"]]
        q = float(ol["quantity"])
    elif t == "fact_asn_line":
        pl = ctx.rows("fact_po_line", "line_id")[row["po_line_id"]]
        q = round(float(pl["quantity"]) * rng.uniform(0.4, 1.0), 3)
    if q is None:
        q = round(S.lognorm(rng, scale.get("qty", 20.0), 0.5), 3)
    planned = round(q * rng.uniform(1.0, 1.08), 3)
    actual = round(q, 3)
    rej = round(actual * max(0.0, rng.gauss(scale.get("reject", 0.015), 0.01)), 3)
    rej = min(rej, actual)
    rec.update({"quantity": q, "base_quantity": round(q * (1000 if rec["unit_code"] == "TON" else 1), 3),
                "planned_quantity": planned, "actual_quantity": actual, "rejected_quantity": rej,
                "qualified_quantity": round(actual - rej, 3)})


def _money(t: str, rec: dict[str, Any], row: dict[str, Any], ctx: S.Context, rng: random.Random, scale: dict[str, float]) -> None:
    cur = "CNY"
    if t in ("fact_sales_contract_line", "fact_lead", "fact_price_adjustment", "fact_supplier_contract", "fact_supplier_claim"):
        cur = rng.choices(["CNY", "USD", "EUR", "JPY"], weights=[85, 9, 4, 2])[0]
    sem = S.FACT_SEMANTICS[t]
    if sem.get("line_of") and sem["line_of"][1].startswith("fact_") and sem["line_of"][1] in S.FACT_SEMANTICS:
        cur = ctx.rows(sem["line_of"][1], sem["line_of"][0])[row["_header"]].get("currency_code") or "CNY"   # 明细与表头同币种
    if t == "fact_advance_shipping_notice" or t == "fact_asn_line":
        po = row.get("po_id") or ctx.rows("fact_po_line", "line_id")[row["po_line_id"]]["po_id"]
        cur = str(ctx.rows("fact_purchase_order", "po_id")[po].get("currency") or "CNY").upper()
        cur = cur if cur in S.RATES else "CNY"
    rate = S.RATES[cur]
    price_cny = None
    prod = row.get("prod_id")
    mat = row.get("material_id")
    if t == "fact_sales_order_line":
        o = ctx.rows("fact_sales_order", "order_id")[row["order_id"]]
        price_cny = float(o["unit_price"])
    elif t == "fact_shipment_line":
        price_cny = float(ctx.rows("fact_sales_order_line", "sales_order_line_id")[row["sales_order_line_id"]]["unit_price"])
    elif t == "fact_asn_line":
        price_cny = float(ctx.rows("fact_po_line", "line_id")[row["po_line_id"]]["unit_price"]) * rate
    elif prod is not None and t not in ("fact_inspection_lot",):
        price_cny = float(ctx.rows("dim_product", "prod_id")[prod].get("guide_price") or 0) or None
    elif mat is not None:
        price_cny = float(ctx.rows("dim_material", "material_id")[mat].get("unit_price") or 0) or None
    if price_cny is None:
        price_cny = S.lognorm(rng, scale.get("price", 800.0), 0.5)
    exact = t in ("fact_sales_order_line", "fact_shipment_line", "fact_asn_line")
    unit_price = round(price_cny / rate, 4) if exact else round(price_cny / rate * rng.uniform(0.96, 1.04), 4)
    q = rec.get("quantity")
    amount = round((q if q is not None else 1.0) * unit_price, 2) if q is not None else round(S.lognorm(rng, scale.get("amount", 20000.0), 0.7), 2)
    if q is None:
        unit_price = amount
    if row.get("_amount") is not None:                       # 拆分表头得到的明细金额
        amount = row["_amount"]
    if t == "fact_gl_entry_line":
        amount = float(ctx.rows("fact_gl_entry", "gl_entry_id")[row["gl_entry_id"]]["amount"])   # 借贷两行同额
    tax_rate = 0.13 if t not in ("fact_overtime", "fact_certification_record", "fact_safety_action", "fact_maintenance_task") else 0.0
    rec.update({"currency_code": cur, "exchange_rate": rate, "unit_price": unit_price, "amount": amount,
                "local_amount": round(amount * rate, 2), "tax_amount": round(amount * tax_rate, 2),
                "cost_amount": round(amount * rng.uniform(0.6, 0.88), 2), "budget_amount": round(amount * rng.uniform(1.0, 1.15), 2)})


def _supplier_of(t: str, row: dict[str, Any], ctx: S.Context) -> int:
    if row.get("supplier_id") is not None:
        return int(row["supplier_id"])
    if row.get("supplier_contract_id") is not None:
        return int(ctx.rows("fact_supplier_contract", "supplier_contract_id")[row["supplier_contract_id"]]["supplier_id"])
    if row.get("po_id") is not None:
        return int(ctx.rows("fact_purchase_order", "po_id")[row["po_id"]]["supplier_id"])
    return 0


def _emp_no(emp_id: Any) -> str:
    return f"E{int(emp_id):04d}"


def fact_attr(t: str, col: str, rec: dict[str, Any], row: dict[str, Any], ctx: S.Context, rng: random.Random) -> Any:
    bday = S.as_date(rec["business_date"])
    assert bday is not None
    q = rec.get("quantity") or 0.0
    open_status = rec["status"] in ("OPEN", "IN_PROGRESS")
    cust = ctx.rows("dim_customer", "cust_id").get(row.get("cust_id")) if row.get("cust_id") is not None else None
    region = S.REGION_BY_PROVINCE.get(str((cust or {}).get("province") or "")[:2], "华东") if cust else pick(rng, sorted(set(S.REGION_BY_PROVINCE.values())))
    order = ctx.rows("fact_production_order", "order_id").get(row.get("order_id")) if "order_id" in row and t not in (
        "fact_sales_order_line", "fact_customer_complaint") else None
    emp = row.get("emp_id")
    simple: dict[str, Any] = {
        # 商机与订单
        "lead_source": lambda: rng.choice(["展会", "官网", "老客户推荐", "电话营销", "招投标", "行业协会"]),
        "opportunity_stage": lambda: rec.setdefault("_stage", rng.choices(S.STAGES, weights=[20, 20, 18, 14, 16, 12])[0]),
        "probability": lambda: S.STAGE_PROB[fact_attr(t, "opportunity_stage", rec, row, ctx, rng)],
        "expected_close_date": lambda: (bday + timedelta(days=rng.randrange(15, 120))).isoformat(),
        "campaign_code": lambda: f"CMP-{bday.year}Q{(bday.month - 1) // 3 + 1}-{rng.randint(1, 4):02d}",
        "sales_region": lambda: region,
        "next_action": lambda: rng.choice(["寄样", "技术交流", "报价", "商务谈判", "签约", "回访"]),
        "contact_channel": lambda: rng.choice(["电话", "拜访", "微信", "邮件", "展会"]),
        "customer_po_no": lambda: f"CPO-{row.get('cust_id', 0)}-{bday.strftime('%y%m')}{rng.randint(100, 999)}",
        "contract_no": lambda: ctx.rows("fact_contract", "contract_id")[row["contract_id"]]["contract_no"] if row.get("contract_id") else f"SC-{bday.year}-{rng.randint(1, 400):04d}",
        "requested_delivery_date": lambda: (bday + timedelta(days=7)).isoformat(),
        "promised_delivery_date": lambda: (bday + timedelta(days=rng.randrange(7, 15))).isoformat(),
        "delivery_terms": lambda: rng.choice(["送货上门", "客户自提", "第三方物流"]),
        "price_list_code": lambda: rng.choice(["PL-2025-STD", "PL-2025-KA", "PL-2026-STD"]) if bday.year == 2025 else "PL-2026-STD",
        "margin_rate": lambda: round((rec["amount"] - rec["cost_amount"]) / rec["amount"], 4) if rec.get("amount") else round(rng.uniform(0.1, 0.3), 4),
        "forecast_bucket": lambda: (bday.replace(day=1) + timedelta(days=32)).strftime("%Y-%m"),
        "forecast_method": lambda: rng.choice(["移动平均", "指数平滑", "销售人员判断", "季节性模型"]),
        "confidence_level": lambda: round(rng.uniform(0.6, 0.95), 2),
        "plan_version": lambda: f"V{bday.strftime('%Y%m')}-{rng.randint(1, 3)}",
        "planner_code": lambda: _emp_no(rng.randint(1, 25)),
        "old_unit_price": lambda: round(float(ctx.rows("dim_pricing", "pricing_id")[row["pricing_id"]]["unit_price"]), 4),
        "new_unit_price": lambda: round(fact_attr(t, "old_unit_price", rec, row, ctx, rng) * (1 + fact_attr(t, "adjust_rate", rec, row, ctx, rng)), 4),
        "adjust_rate": lambda: rec.setdefault("_adj", round(rng.uniform(-0.08, 0.1), 4)),
        "effective_date": lambda: (bday + timedelta(days=rng.randrange(1, 30))).isoformat(),
        "approval_no": lambda: f"AP-{bday.strftime('%Y%m')}-{rng.randint(1, 999):03d}",
        # 物流
        "carrier_tracking_no": lambda: f"TRK{rng.randint(10**9, 10**10 - 1)}",
        "vehicle_no": lambda: f"鲁B{rng.choice('ABCDEFGHJK')}{rng.randint(10000, 99999)}",
        "ship_from": lambda: ctx.rows("dim_warehouse", "warehouse_id")[row["warehouse_id"]]["warehouse_name"] if row.get("warehouse_id")
        else ctx.rows("dim_warehouse", "warehouse_id")[row.get("source_warehouse_id") or 1]["warehouse_name"] if row.get("source_warehouse_id") else "供应商仓",
        "ship_to_city": lambda: (cust or {}).get("city") or rng.choice(["青岛", "济南", "上海", "苏州", "广州"]),
        "gross_weight_kg": lambda: round(q * (1000 if rec.get("unit_code") == "TON" else 1) * rng.uniform(1.01, 1.05), 1),
        "package_count": lambda: max(1, int(q * (40 if rec.get("unit_code") == "TON" else 1))),
        "freight_amount": lambda: round(max(50.0, q * (1000 if rec.get("unit_code") == "TON" else 1) * rng.uniform(0.08, 0.2)), 2),
        "handled_by_code": lambda: _emp_no(rng.randint(1, 25)),
        "inspector_code": lambda: _emp_no(rng.randint(1, 25)),
        "summary_text": lambda: {"fact_cash_flow": "收付款", "fact_cost_allocation": "成本分摊", "fact_gl_entry": "记账",
                                 "fact_gl_entry_line": "记账", "fact_capex_project_cost": "工程付款", "fact_fixed_asset_transfer": "资产调拨",
                                 "fact_tax_invoice_line": "开票"}.get(t, "业务") + f"({bday.strftime('%Y年%m月')})",
        "signed_date": lambda: None if open_status else (bday + timedelta(days=rng.randrange(1, 6))).isoformat(),
        # 采购
        "requisition_no": lambda: f"PR-{bday.strftime('%Y%m')}-{rng.randint(1, 999):03d}",
        "buyer_code": lambda: _emp_no(rng.randint(1, 25)),
        "supplier_quote_no": lambda: f"SQ-{row.get('supplier_id') or 0}-{bday.strftime('%y%m%d')}",
        "requested_date": lambda: bday.isoformat(),
        "promised_date": lambda: (bday + timedelta(days=rec.setdefault("_lead", rng.randint(5, 25)))).isoformat(),
        "received_date": lambda: None if open_status else (bday + timedelta(days=rec.setdefault("_lead", rng.randint(5, 25)) + rng.randint(-2, 5))).isoformat(),
        "payment_term_code": lambda: rng.choices([p[0] for p in S.PAYMENT_TERMS], weights=[40, 20, 8, 5, 7, 8, 5, 7])[0],
        "incoterm_code": lambda: rng.choices([i[0] for i in S.INCOTERMS], weights=[30, 10, 15, 5, 10, 15, 3, 12])[0],
        "sourcing_method": lambda: rng.choice(["招标", "询比价", "单一来源", "框架协议"]),
        "lead_time_days": lambda: rec.setdefault("_lead", rng.randint(5, 25)),
        "quality_agreement_no": lambda: f"QA-S{_supplier_of(t, row, ctx):03d}-{bday.year}",
        "claim_type": lambda: rng.choice(["质量索赔", "交期索赔", "数量差异"]),
        "claim_resolution": lambda: None if open_status else rng.choice(["扣款", "退货", "换货", "协商关闭"]),
        "claim_amount_basis": lambda: rng.choice(["按批次货值", "按损失核算", "按合同违约条款"]),
        "supplier_response_days": lambda: rng.randint(1, 15),
        # 库存
        "lot_no": lambda: f"L{bday.strftime('%y%m%d')}-{row.get('material_id') or row.get('prod_id') or 0:03d}",
        "batch_no": lambda: order["batch_no"] if order else f"B{bday.strftime('%y%m%d')}{rng.randint(1, 99):02d}",
        "bin_code": lambda: ctx.rows("dim_storage_location", "location_id")[row["location_id"]]["location_code"] if row.get("location_id")
        else f"W{row.get('warehouse_id') or 1}-{rng.choice('ABCD')}{rng.randint(1, 20):02d}",
        "stock_status": lambda: rng.choices(["可用", "质检中", "冻结"], weights=[85, 10, 5])[0] if t != "fact_batch_quality_hold" else "冻结",
        "movement_type": lambda: {"fact_inventory_movement": rng.choice(["101 采购收货", "201 生产领用", "261 工单发料", "301 仓间调拨", "601 销售发货"]),
                                  "fact_transfer_order": "301 仓间调拨", "fact_transfer_order_line": "301 仓间调拨",
                                  "fact_shipment_line": "601 销售发货", "fact_asn_line": "101 采购收货",
                                  "fact_legacy_material_issue": "261 工单发料", "fact_cycle_count_line": "701 盘点调整"}.get(t, "999 其他"),
        "manufacture_date": lambda: (bday - timedelta(days=rng.randrange(1, 60))).isoformat(),
        "expiry_date": lambda: (bday + timedelta(days=rng.choice([180, 365, 730]))).isoformat(),
        "available_quantity": lambda: rec.get("qualified_quantity", q),
        "reserved_quantity": lambda: round(min(rec.get("qualified_quantity", q), q * rng.uniform(0, 0.3)), 3),
        "valuation_type": lambda: rng.choice(["标准价", "移动平均价"]),
        "book_quantity": lambda: rec.setdefault("_book", round(q, 3)),
        "counted_quantity": lambda: rec.setdefault("_counted", round(rec.setdefault("_book", round(q, 3)) * rng.choice([1, 1, 1, 0.98, 1.01]), 3)),
        "variance_quantity": lambda: round(fact_attr(t, "counted_quantity", rec, row, ctx, rng) - rec.setdefault("_book", round(q, 3)), 3),
        "count_method": lambda: rng.choice(["ABC 循环盘点", "全盘", "抽盘"]),
        "recount_required": lambda: int(abs(fact_attr(t, "variance_quantity", rec, row, ctx, rng)) > 0.01),
        # 生产
        "order_no": lambda: order["order_no"] if order else None,
        "operation_seq": lambda: ctx.rows("dim_operation", "operation_id")[row["operation_id"]]["seq_no"] if row.get("operation_id") else None,
        "process_stage": lambda: ctx.rows("dim_operation", "operation_id")[row["operation_id"]]["op_name"] if row.get("operation_id")
        else rng.choice(["混料", "挤出", "冷却", "切粒", "包装"]),
        "shift_code": lambda: ctx.rows("dim_shift", "shift_id")[row["shift_id"]]["code"] if row.get("shift_id") else _shift_by_hour(rec, ctx),
        "start_time": lambda: rec.setdefault("_start", rec["event_time"]),
        "end_time": lambda: S.iso(datetime.fromisoformat(rec.setdefault("_start", rec["event_time"])) + timedelta(minutes=rec.get("duration_minutes") or rng.randint(30, 480))),
        "work_center_code": lambda: _work_center(row, ctx, rng),
        "routing_version": lambda: _routing_version(row, ctx),
        "setup_minutes": lambda: float(ctx.rows("dim_operation", "operation_id")[row["operation_id"]]["setup_time_min"]) if row.get("operation_id") else round(rng.uniform(10, 45), 1),
        "run_minutes": lambda: round(max(0.0, (rec.get("duration_minutes") or 120.0) - fact_attr(t, "setup_minutes", rec, row, ctx, rng)), 1),
        "cycle_time_seconds": lambda: round(float(ctx.rows("dim_operation", "operation_id")[row["operation_id"]]["standard_cycle_min"]) * 60, 1) if row.get("operation_id") else round(rng.uniform(20, 120), 1),
        "yield_rate": lambda: round(rec["qualified_quantity"] / rec["actual_quantity"], 4) if rec.get("actual_quantity") else None,
        "scrap_quantity": lambda: rec.get("rejected_quantity"),
        "bom_qty_per_ton": lambda: _bom_qty(row, ctx),
        "standard_quantity": lambda: round((_bom_qty(row, ctx) or 0.1) * float((order or {}).get("actual_quantity") or 1), 3),
        "usage_variance_rate": lambda: round((q - fact_attr(t, "standard_quantity", rec, row, ctx, rng)) / max(fact_attr(t, "standard_quantity", rec, row, ctx, rng), 0.001), 4),
        "issue_type": lambda: rng.choice(["按单领料", "倒冲", "补料"]),
        "issue_warehouse": lambda: ctx.rows("dim_warehouse", "warehouse_id")[rng.choice(ctx.ids("dim_warehouse", "warehouse_id"))]["warehouse_name"],
        # 质量
        "inspection_no": lambda: f"QC{bday.strftime('%y%m%d')}{rec[next(iter(k for k in rec if k.endswith('_id')))] :05d}",
        "sample_size": lambda: rng.choice([3, 5, 8, 13]),
        "characteristic_code": lambda: _char(row, ctx).get("code"),
        "nominal_value": lambda: _char(row, ctx).get("nominal_value"),
        "lower_limit": lambda: _char(row, ctx).get("lower_spec_limit"),
        "upper_limit": lambda: _char(row, ctx).get("upper_spec_limit"),
        "measured_value": lambda: rec.setdefault("_measured", _measure(row, ctx, rng)),
        "result_code": lambda: rng.choices(["完成", "待复查", "转委外", "未修复"], weights=[80, 10, 6, 4])[0]
        if t in ("fact_equipment_event", "fact_maintenance_task") else _result(rec, row, ctx, rng),
        "defect_code": lambda: None if _result(rec, row, ctx, rng) == "合格" and t in ("fact_inspection_result", "fact_inspection_lot", "fact_process_parameter_record")
        else rng.choice(["D01 黑点", "D02 色差", "D03 力学性能不足", "D04 水分超标", "D05 尺寸超差", "D06 包装破损"]),
        "severity": lambda: row.get("_severity") or rng.choices([1, 2, 3, 4, 5], weights=[30, 30, 20, 15, 5])[0],
        "disposition": lambda: "放行" if _result(rec, row, ctx, rng) == "合格" and t in ("fact_inspection_result", "fact_inspection_lot", "fact_process_parameter_record")
        else rng.choice(["返工", "报废", "让步接收", "退货"]),
        "root_cause_code": lambda: None if open_status else rng.choice(["人", "机", "料", "法", "环", "测"]),
        "containment_action": lambda: rng.choice(["隔离在库品", "暂停发货", "加严检验", "通知客户", "无需围堵"]),
        "verification_result": lambda: None if open_status else rng.choices(["有效", "部分有效", "无效"], weights=[80, 15, 5])[0],
        "case_no": lambda: f"{_doc_prefix(t)}C{bday.strftime('%y%m')}{rec[next(iter(k for k in rec if k.endswith('_id')))] :05d}",
        "due_date": lambda: (bday + timedelta(days=rng.choice([7, 14, 30]))).isoformat(),
        "closed_date": lambda: None if open_status else (bday + timedelta(days=rng.randrange(1, 14))).isoformat(),
        # 设备
        "event_code": lambda: rng.choice(["EV-STOP", "EV-START", "EV-FAULT", "EV-SETUP", "EV-PM"]),
        "failure_mode": lambda: rng.choice(["电机过热", "轴承磨损", "密封泄漏", "传感器故障", "螺杆磨损", "加热圈损坏", "无故障"]),
        "maintenance_type": lambda: rng.choice(["预防性维护", "故障维修", "点检", "改善性维修"]),
        "downtime_minutes": lambda: round(min(rec["duration_minutes"], S.lognorm(rng, 40, 0.8)), 1) if rec.get("duration_minutes")
        else (0.0 if rng.random() < 0.85 else round(S.lognorm(rng, 30, 0.8), 1)),
        "health_index": lambda: round(rng.uniform(0.55, 0.99), 3),
        "remaining_life_hours": lambda: round(S.lognorm(rng, 3000, 0.6), 0),
        "operator_code": lambda: _emp_no(emp) if emp else _emp_no(rng.randint(1, 25)),
        "sensor_code": lambda: ctx.rows("dim_sensor", "sensor_id")[row["sensor_id"]]["sensor_code"],
        "reading_unit": lambda: ctx.rows("dim_sensor", "sensor_id")[row["sensor_id"]]["unit"] if row.get("sensor_id") else rec.get("unit_code"),
        "lower_threshold": lambda: rec.setdefault("_lo", round(rng.uniform(20, 40), 1)),
        "upper_threshold": lambda: round(rec.setdefault("_lo", round(rng.uniform(20, 40), 1)) + rng.uniform(30, 60), 1),
        "reading_value": lambda: round(rng.uniform(fact_attr(t, "lower_threshold", rec, row, ctx, rng) * 0.9, fact_attr(t, "upper_threshold", rec, row, ctx, rng) * (1.15 if t == "fact_predictive_alert" else 0.98)), 2),
        "alarm_code": lambda: (f"AL-{rng.randint(100, 999)}" if t == "fact_predictive_alert" else None),
        "meter_code": lambda: f"M-{row.get('equipment_id', 0):03d}-{ctx.rows('dim_energy_type', 'energy_type_id')[row['energy_type_id']]['code']}",
        "meter_start": lambda: rec.setdefault("_m0", round(rng.uniform(1e4, 9e5), 1)),
        "meter_end": lambda: round(rec.setdefault("_m0", round(rng.uniform(1e4, 9e5), 1)) + q, 1),
        "collection_method": lambda: rng.choice(["自动采集", "人工抄表"]),
        "emission_factor": lambda: next(e[3] for e in S.ENERGY_TYPES if e[0] == ctx.rows("dim_energy_type", "energy_type_id")[row["energy_type_id"]]["code"]),
        "carbon_emission_kg": lambda: round(q * fact_attr(t, "emission_factor", rec, row, ctx, rng), 3),
        # 财务
        "voucher_no": lambda: f"记-{bday.strftime('%Y%m')}-{rec[next(iter(k for k in rec if k.endswith('_id')))] :05d}",
        "fiscal_year": lambda: bday.year,
        "fiscal_period": lambda: bday.month,
        "debit_amount": lambda: rec.get("amount") if t != "fact_gl_entry_line" or row.get("_line_no") == 1 else 0.0,
        "credit_amount": lambda: rec.get("amount") if t != "fact_gl_entry_line" or row.get("_line_no") != 1 else 0.0,
        "account_code": lambda: ctx.rows("dim_account", "account_id")[row["account_id"]]["account_code"] if row.get("account_id")
        else ctx.rows("dim_account", "account_id")[rng.choice(ctx.ids("dim_account", "account_id"))]["account_code"],
        "cost_center_code": lambda: ctx.rows("dim_cost_center", "cc_id")[row.get("cc_id") or row.get("source_cc_id") or rng.choice(ctx.ids("dim_cost_center", "cc_id"))]["cc_code"],
        "profit_center_code": lambda: ctx.rows("dim_profit_center", "pc_id")[row.get("target_pc_id") or rng.choice(ctx.ids("dim_profit_center", "pc_id"))]["pc_code"],
        "tax_rate": lambda: next(c[2] for c in S.TAX_CODES if c[0] == ctx.rows("dim_tax_code", "tax_code_id")[row["tax_code_id"]]["code"]) if row.get("tax_code_id") else 0.13,
        "clearing_document": lambda: None if open_status else f"CL-{bday.strftime('%Y%m')}-{rng.randint(1, 9999):04d}",
        "reference_document": lambda: rec.get("source_document_no") or {"fact_cash_flow": "收付款单", "fact_cost_allocation": "分摊批次",
                                       "fact_gl_entry": "业务单据", "fact_gl_entry_line": "业务单据", "fact_capex_project_cost": "项目结算单",
                                       "fact_fixed_asset_transfer": "资产调拨单", "fact_tax_invoice_line": "销售发票"}.get(t, "单据") + f"-{bday.strftime('%Y%m')}-{rng.randint(1, 9999):04d}",
        # 安全与环境
        "observation_no": lambda: f"OB{bday.strftime('%y%m%d')}{rec[next(iter(k for k in rec if k.endswith('_id')))] :04d}",
        "hazard_type": lambda: ctx.rows("dim_hazard", "hazard_id")[row["hazard_id"]]["hazard_category"] if row.get("hazard_id") else "环境",
        "likelihood": lambda: row.get("_likelihood") or rng.randint(1, 5),
        "risk_score": lambda: float(fact_attr(t, "severity", rec, row, ctx, rng) * fact_attr(t, "likelihood", rec, row, ctx, rng)),
        "control_measure": lambda: rng.choice(["工程控制", "管理控制", "个体防护", "消除替代"]),
        "location_code": lambda: f"WS{row['workshop_id']}" if row.get("workshop_id") else (ctx.rows("dim_hazard", "hazard_id")[row["hazard_id"]]["location"]
                                 if row.get("hazard_id") else f"F{row.get('factory_id') or 1}"),
        "action_due_date": lambda: (bday + timedelta(days=rng.choice([7, 14, 30]))).isoformat(),
        "permit_no": lambda: rng.choice([None, f"PTW-{bday.strftime('%y%m')}-{rng.randint(1, 300):03d}"]),
        "monitoring_point": lambda: f"F{row.get('factory_id') or 1}-{rng.choice(['DA001 排气筒', 'DW001 总排口', 'ZS01 厂界'])}",
        "measurement_unit": lambda: ctx.rows("dim_emission_type", "emission_type_id")[row["emission_type_id"]]["measurement_unit"],
        "limit_value": lambda: ctx.rows("dim_emission_type", "emission_type_id")[row["emission_type_id"]]["regulatory_limit"],
        "emission_value": lambda: rec.setdefault("_ev", round((fact_attr(t, "limit_value", rec, row, ctx, rng) or 10.0) * rng.uniform(0.2, 1.08), 3)),
        "exceed_flag": lambda: int(bool(fact_attr(t, "limit_value", rec, row, ctx, rng)) and fact_attr(t, "emission_value", rec, row, ctx, rng) > fact_attr(t, "limit_value", rec, row, ctx, rng)),
        "regulatory_clause": lambda: rng.choice(["GB 16297-1996", "GB 8978-1996", "GB 12348-2008", "排污许可证 第三条"]),
        "evidence_reference": lambda: f"在线监测-{bday.strftime('%Y%m%d')}",
        "sampling_method": lambda: rng.choice(["在线监测", "手工采样", "委托检测"]),
        # 人力
        "employee_no": lambda: _emp_no(emp) if emp else None,
        "work_date": lambda: bday.isoformat(),
        "clock_in": lambda: _clock(rec, row, ctx, rng, True),
        "clock_out": lambda: _clock(rec, row, ctx, rng, False),
        "regular_hours": lambda: _shift_paid(row, ctx),
        "overtime_hours": lambda: round(max(0.0, (rec.get("duration_minutes") or 0) / 60.0), 2) if t == "fact_overtime" else round(rng.choice([0, 0, 0, 0.5, 1, 2]), 2),
        "absence_type": lambda: rng.choices([None, "事假", "病假", "年假"], weights=[90, 4, 3, 3])[0],
        "attendance_result": lambda: rng.choices(["正常", "迟到", "早退", "缺勤"], weights=[92, 4, 2, 2])[0],
        "supervisor_code": lambda: _emp_no(rng.randint(1, 25)),
        "position_code": lambda: ctx.rows("dim_job_position", "job_position_id")[row["job_position_id"]]["code"] if row.get("job_position_id") else None,
        "skill_level": lambda: rng.randint(1, 5),
        "assessment_score": lambda: rec.get("score_value") if rec.get("score_value") is not None else round(rng.uniform(60, 98), 1),
        "assessor_code": lambda: _emp_no(rng.randint(1, 25)),
        "valid_from": lambda: bday.isoformat(),
        "certificate_no": lambda: f"CERT-{bday.year}-{rng.randint(10000, 99999)}" if t == "fact_certification_record"
        else f"JL-{bday.year}-{rng.randint(10000, 99999)}" if t == "fact_gauge_calibration" else None,
        "assessment_method": lambda: rng.choice(["实操考核", "笔试", "答辩"]),
        # 计划与绩效
        "planning_period": lambda: bday.strftime("%Y-%m"),
        "scenario_code": lambda: rng.choice(["BASE", "UPSIDE", "DOWNSIDE"]),
        "demand_quantity": lambda: rec.get("planned_quantity"),
        "supply_quantity": lambda: rec.get("actual_quantity"),
        "capacity_hours": lambda: round((rec.get("duration_minutes") or rng.uniform(400, 1400)) / 60.0, 2),
        "utilization_rate": lambda: rec.get("rate_value"),
        "backlog_quantity": lambda: round(max((rec.get("planned_quantity") or 0) - (rec.get("actual_quantity") or 0), 0), 3),
        "safety_stock_quantity": lambda: round(q * 0.2, 3),
        "forecast_accuracy": lambda: round(rng.uniform(0.7, 0.97), 3),
        "exception_code": lambda: rng.choices([None, "CAP_SHORT", "MAT_SHORT", "DEMAND_SPIKE"], weights=[80, 8, 7, 5])[0],
        "review_status": lambda: "已评审" if not open_status else "待评审",
        "freeze_horizon_days": lambda: rng.choice([7, 14]),
        "kpi_code": lambda: rec.setdefault("_kpi", rng.choice(["KPI-OTD", "KPI-YIELD", "KPI-OEE", "KPI-SALES", "KPI-COST"])),
        "kpi_name": lambda: {"KPI-OTD": "准时交付率", "KPI-YIELD": "一次合格率", "KPI-OEE": "设备综合效率", "KPI-SALES": "销售额达成", "KPI-COST": "成本控制"}[fact_attr(t, "kpi_code", rec, row, ctx, rng)],
        "target_value": lambda: rec.setdefault("_target", 0.95),
        "actual_value": lambda: rec.get("rate_value") if t == "fact_kpi_snapshot" else None,
        "achievement_rate": lambda: round((rec.get("rate_value") or 0) / 0.95, 4),
        "period_type": lambda: "月度",
        "evaluator_code": lambda: _emp_no(rng.randint(1, 25)),
        # 资产与项目
        "project_phase": lambda: rng.choice(["设计", "采购", "施工", "调试"]),
        "cost_element": lambda: rng.choice(["设备购置", "安装工程", "建筑工程", "其他费用"]),
        "capitalized_flag": lambda: int(not open_status),
        "asset_value_added": lambda: rec.get("amount") if not open_status else 0.0,
        "invoice_ref": lambda: f"INV-{bday.strftime('%Y%m')}-{rng.randint(1, 9999):04d}",
        "transfer_reason": lambda: rng.choice(["产能调整", "闲置调拨", "新产线配套"]),
        "net_book_value": lambda: round(float(ctx.rows("dim_fixed_asset", "asset_id")[row["asset_id"]]["net_book_value"] or 0), 2),
        "handover_date": lambda: None if open_status else (bday + timedelta(days=rng.randrange(1, 10))).isoformat(),
        "receiver_code": lambda: _emp_no(rng.randint(1, 25)),
        # 客户接触与投诉
        "contact_topic": lambda: rng.choice(["新品推介", "价格沟通", "交付跟进", "质量反馈", "回款催收"]),
        "contact_result": lambda: rng.choice(["有意向", "待跟进", "无需求", "已成交"]),
        "follow_up_date": lambda: (bday + timedelta(days=rng.randrange(3, 30))).isoformat(),
        "satisfaction_score": lambda: round(rng.uniform(3.0, 5.0), 1),
        "complaint_channel": lambda: rng.choice(["电话", "邮件", "客户拜访", "售后系统"]),
        "complaint_type": lambda: rng.choice(["质量问题", "交付延迟", "包装破损", "数量短缺"]),
        "response_due_date": lambda: (bday + timedelta(days=2)).isoformat(),
        "compensation_amount": lambda: round(rec.get("amount", 0) * rng.choice([0, 0, 0.02, 0.05]), 2),
        # 计量校准、维修、备件、工装、培训
        "gauge_code": lambda: f"GG-{row.get('equipment_id', 0):03d}-{rng.randint(1, 5)}",
        "calibration_type": lambda: rng.choice(["内校", "外校"]),
        "standard_value": lambda: _char(row, ctx).get("nominal_value"),
        "error_value": lambda: rec.setdefault("_err", round(rng.gauss(0, 0.02), 4)),
        "tolerance": lambda: 0.05,
        "next_due_date": lambda: (bday + timedelta(days=365)).isoformat(),
        "task_type": lambda: rng.choice(["拆检", "更换备件", "调整", "清洁润滑"]),
        "labor_hours": lambda: round((rec.get("duration_minutes") or 60) / 60.0, 2),
        "part_code": lambda: ctx.rows("dim_spare_part", "part_id")[row["part_id"]]["part_code"],
        "stock_before": lambda: rec.setdefault("_sb", float(rng.randint(5, 60))),
        "stock_after": lambda: round(rec.setdefault("_sb", float(rng.randint(5, 60))) - q, 3),
        "requester_code": lambda: _emp_no(rng.randint(1, 25)),
        "tool_code": lambda: ctx.rows("dim_tool", "tool_id")[row["tool_id"]]["tool_code"],
        "used_cycles": lambda: rec.setdefault("_uc", rng.randint(100, 5000)),
        "remaining_life": lambda: max(0, int(ctx.rows("dim_tool", "tool_id")[row["tool_id"]]["total_life"] or 0) - int(ctx.rows("dim_tool", "tool_id")[row["tool_id"]]["used_life"] or 0)),
        "wear_rate": lambda: round(rng.uniform(0.001, 0.02), 4),
        "training_no": lambda: ctx.rows("fact_safety_training", "training_id")[row["training_id"]]["training_no"],
        "sign_in_time": lambda: f"{rng.randint(8, 13):02d}:{rng.choice(['00', '15', '30', '45'])}",
        "exam_score": lambda: rec.get("score_value"),
        "passed_flag": lambda: int((rec.get("score_value") or 0) >= 60),
        "credit_hours": lambda: float(ctx.rows("fact_safety_training", "training_id")[row["training_id"]]["duration_hours"] or 2),
    }
    if t in ("fact_data_quality_issue", "fact_validation_case_result") and col in GOV_COLUMNS:
        return (row.get("_gov") or {}).get(col)
    if col in simple:
        return simple[col]()
    raise KeyError(f"no generator for {t}.{col}")


GOV_COLUMNS = {c for c, _ in S.ATTR_GROUPS["governance"]}


def _shift_by_hour(rec: dict[str, Any], ctx: S.Context) -> str:
    h = int(rec["event_time"][11:13])
    code = "DAY" if 8 <= h < 16 else "SWING" if 16 <= h else "NIGHT"
    return code


def _shift_paid(row: dict[str, Any], ctx: S.Context) -> float:
    if row.get("shift_id"):
        return float(ctx.rows("dim_shift", "shift_id")[row["shift_id"]]["paid_hours"])
    return 7.5


def _clock(rec: dict[str, Any], row: dict[str, Any], ctx: S.Context, rng: random.Random, start: bool) -> str:
    if row.get("shift_id"):
        sh = ctx.rows("dim_shift", "shift_id")[row["shift_id"]]
        base = sh["shift_start"] if start else sh["shift_end"]
    else:
        base = "08:00" if start else "16:00"
    h, m = int(base[:2]) % 24, int(base[3:5])
    m += rng.randint(-10, 5) if start else rng.randint(0, 20)
    h, m = (h + m // 60) % 24, m % 60
    return f"{h:02d}:{m:02d}"


def _work_center(row: dict[str, Any], ctx: S.Context, rng: random.Random) -> str | None:
    if row.get("operation_id"):
        wc = ctx.rows("dim_operation", "operation_id")[row["operation_id"]]["wc_id"]
        return ctx.rows("dim_work_center", "wc_id")[wc]["wc_code"]
    line = row.get("line_id")
    if line is None and row.get("order_id") in ctx.rows("fact_production_order", "order_id"):
        line = ctx.rows("fact_production_order", "order_id")[row["order_id"]]["line_id"]
    wcs = [r["wc_code"] for r in ctx.rows("dim_work_center", "wc_id").values() if r["line_id"] == line]
    return pick(rng, wcs) if wcs else None


def _routing_version(row: dict[str, Any], ctx: S.Context) -> str | None:
    if row.get("operation_id"):
        rid = ctx.rows("dim_operation", "operation_id")[row["operation_id"]]["routing_id"]
        return ctx.rows("dim_routing", "routing_id")[rid]["version"]
    return None


def _bom_qty(row: dict[str, Any], ctx: S.Context) -> float | None:
    mat = row.get("material_id", row.get("_material_id"))
    if row.get("order_id") not in ctx.rows("fact_production_order", "order_id") or mat is None:
        return None
    prod = ctx.rows("fact_production_order", "order_id")[row["order_id"]]["prod_id"]
    boms = [b for b, r in ctx.rows("dim_bom", "bom_id").items() if r["product_id"] == prod]
    for r in ctx.rows("dim_bom_line", "bom_line_id").values():
        if r["bom_id"] in boms and r["material_id"] == mat:
            return float(r["qty_per_ton"])
    return None


def _char(row: dict[str, Any], ctx: S.Context) -> dict[str, Any]:
    if row.get("process_parameter_id") is not None:
        p = ctx.rows("dim_process_parameter", "process_parameter_id")[row["process_parameter_id"]]
        return {"code": p["code"], "nominal_value": p["nominal_value"], "lower_spec_limit": p["lower_spec_limit"],
                "upper_spec_limit": p["upper_spec_limit"]}
    if row.get("inspection_lot_id") is not None and row.get("quality_characteristic_id") is None:
        row = {**row, "prod_id": ctx.rows("fact_inspection_lot", "inspection_lot_id")[row["inspection_lot_id"]]["prod_id"], "order_id": 0}
    qc = row.get("quality_characteristic_id")
    if qc is None and row.get("prod_id") is not None and "order_id" in row:
        own = sorted(q for q, r in ctx.rows("dim_quality_characteristic", "quality_characteristic_id").items() if r["prod_id"] == row["prod_id"])
        qc = own[0] if own else None                        # 检验批按产品的首要质量特性判定
    if qc is None:
        return {}
    return ctx.rows("dim_quality_characteristic", "quality_characteristic_id")[qc]


def _measure(row: dict[str, Any], ctx: S.Context, rng: random.Random) -> float | None:
    c = _char(row, ctx)
    if not c:
        return round(rng.uniform(10, 100), 3)
    nom, lo, hi = c["nominal_value"], c["lower_spec_limit"], c["upper_spec_limit"]
    sd = max((hi - lo) / 6.0, 1e-3)
    return round(rng.gauss(nom, sd * 1.1), 3)


def _result(rec: dict[str, Any], row: dict[str, Any], ctx: S.Context, rng: random.Random) -> str:
    if "_result" in rec:
        return rec["_result"]
    c = _char(row, ctx)
    if c and rec.get("_measured", rec.get("measured_value")) is not None:
        v = rec.get("_measured", rec.get("measured_value"))
        ok = c["lower_spec_limit"] <= v <= c["upper_spec_limit"]
    else:
        ok = (rec.get("rejected_quantity") or 0) <= (rec.get("actual_quantity") or 1) * 0.02
    rec["_result"] = "合格" if ok else "不合格"
    return rec["_result"]
