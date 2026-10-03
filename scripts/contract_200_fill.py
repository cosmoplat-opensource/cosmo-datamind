#!/usr/bin/env python3
"""Context-bound defaults for the generic columns of event-derived rows (generator v3, DR-076).

The event functions in ``contract_200_events`` set each table's business values; the generic
attribute groups (people, regions, terms, bins, batch dates, quality case fields, voucher
references...) used to fall back to v2's independent random draws, which the second v3 audit
found contradicting the rest of the row (inspectors from the sales department, a random region per
order line, root causes on passed inspections).  ``Filler.value`` derives them from the row's own
references instead; it returns ``MISSING`` for columns it does not own, which then keep v2's
generators (free-text attributes with no relation to other tables).  Standard library only.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import contract_200_semantics as S
import demo108_master as M

MISSING = object()
PASS = "合格"
# 质量特性 → 不合格时的根因(人机料法环测)与缺陷描述
ROOT_CAUSE = {"拉伸强度": "料", "缺口冲击强度": "料", "熔体流动速率": "法", "密度": "机", "灰分": "机", "阻燃等级(1.6mm)": "料"}
EMISSION_RULES = {  # 介质 → (监测点, 执行标准)
    "COD": ("DW001 废水总排口", "GB 8978-1996"), "NH3N": ("DW001 废水总排口", "GB 8978-1996"),
    "VOCS": ("DA001 挤出废气排气筒", "GB 16297-1996"), "PM": ("DA002 配料粉尘排气筒", "GB 16297-1996"),
    "NOISE": ("ZS01 厂界东侧", "GB 12348-2008"),
}
TERMS_CODE = {30: "NET30", 45: "TT45", 60: "NET60", 90: "NET90"}


def _h(*parts: Any) -> int:
    return int(hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def emp_no(emp_id: Any) -> str | None:
    return f"E{int(emp_id):04d}" if emp_id is not None else None


class Filler:
    def __init__(self, ctx: S.Context):
        self.ctx = ctx
        emps = ctx.rows("dim_employee", "emp_id")
        self.roles = M.employee_roles(list(emps))
        self.by_role: dict[str, list[int]] = defaultdict(list)
        for eid, role in sorted(self.roles.items()):
            self.by_role[role].append(eid)
        self.tech_factory = {eid: (1 if k < 6 else 2 if k < 11 else 3) for k, eid in enumerate(self.by_role["维修技师"])}
        self.leaders_of_line: dict[int, list[int]] = defaultdict(list)
        for t in sorted(ctx.rows("dim_team", "team_id").values(), key=lambda x: x["team_id"]):
            self.leaders_of_line[t["line_id"]].append(t["leader_id"])
        self._cache: dict[str, Any] = {}

    # ───────── 基础推导 ─────────
    def pick(self, role: str, *key: Any) -> int:
        pool = self.by_role[role]
        return pool[_h(role, *key) % len(pool)]

    def line_factory(self, line_id: int) -> int:
        ws = self.ctx.rows("dim_production_line", "line_id")[line_id]["workshop_id"]
        return int(M.WORKSHOPS[ws - 1][2])

    def order(self, order_id: Any) -> dict[str, Any] | None:
        return self.ctx.rows("fact_production_order", "order_id").get(order_id)

    def tech_of_factory(self, fac: int, *key: Any) -> int:
        pool = [e for e, f in self.tech_factory.items() if f == fac]
        return pool[_h("tech", *key) % len(pool)]

    def equipment_factory(self, eq_id: int) -> int:
        return self.line_factory(self.ctx.rows("dim_equipment", "equipment_id")[eq_id]["line_id"])

    def cust_region(self, cust_id: Any) -> str | None:
        if "region" not in self._cache:
            regions = {r["region_id"]: r["region_name"] for r in self.ctx.rows("dim_region", "region_id").values()}
            depts = self.ctx.rows("dim_department", "dept_id")
            emps = self.ctx.rows("dim_employee", "emp_id")
            first: dict[int, int] = {}
            for o in sorted(self.ctx.rows("fact_sales_order", "order_id").values(), key=lambda o: o["order_id"]):
                first.setdefault(o["cust_id"], o["emp_id"])
            self._cache["region"] = {c: regions.get(depts[emps[e]["dept_id"]]["region_id"]) for c, e in first.items()}
        return self._cache["region"].get(cust_id)

    def sales_contract(self, cust_id: Any, day: date) -> str | None:
        for c in self.ctx.rows("fact_contract", "contract_id").values():
            if c["contract_type"] == "sales" and c["party_id"] == cust_id and c["start_date"] <= day.isoformat() <= (c["end_date"] or "9999"):
                return str(c["contract_no"])
        return None

    def supplier_terms(self, sid: Any) -> str:
        days = M.SUPPLIERS[int(sid) - 1][7]
        return TERMS_CODE.get(days, "NET30")

    def raw_wh_of_factory(self, fac: int) -> int:
        return {1: 1, 2: 4, 3: 6}[fac]

    def bin(self, wh: Any, *key: Any) -> str | None:
        if wh is None:
            return None
        codes = sorted(l["location_code"] for l in self.ctx.rows("dim_storage_location", "location_id").values() if l["warehouse_id"] == wh)
        return codes[_h("bin", wh, *key) % len(codes)] if codes else None

    def fg_wh(self, pid: int) -> int:
        ws = M.PRODUCTS[pid - 1][6][0]
        return {1: 2, 2: 5, 3: 7}[M.WORKSHOPS[ws - 1][2]]

    def batch_dates(self, batch: str | None) -> tuple[date, date] | None:
        if not batch:
            return None
        if "batch" not in self._cache:
            m: dict[str, tuple[date, date]] = {}
            for o in self.ctx.rows("fact_production_order", "order_id").values():
                if o["end_time"]:
                    d = date.fromisoformat(o["end_time"][:10])
                    m[o["batch_no"]] = (d, d + timedelta(days=730))
            for g in self.ctx.rows("fact_goods_receipt", "gr_id").values():
                d = date.fromisoformat(g["receipt_date"])
                lot = f"-{d.strftime('%y%m%d')}-{g['gr_id']}"
                m[lot] = (d - timedelta(days=5), d + timedelta(days=365))
            self._cache["batch"] = m
        if batch.startswith("RM") and not batch.endswith("INIT"):
            return self._cache["batch"].get(batch[4:])
        return self._cache["batch"].get(batch)

    def vehicles(self, qty_t: float, *key: Any) -> str:
        n = max(1, math.ceil((qty_t or 0) / 32.0))                  # 单车核载约 32 吨
        plates = [f"鲁B{'ACDEFGHJKL'[_h('v', i, *key) % 10]}{10000 + _h('p', i, *key) % 89999}" for i in range(n)]
        return ",".join(plates)

    # ───────── 列取值 ─────────
    def value(self, t: str, col: str, rec: dict[str, Any], row: dict[str, Any]) -> Any:
        ctx = self.ctx
        key = (t, rec.get(next(iter(rec))))
        bday = rec["business_date"] if isinstance(rec["business_date"], date) else date.fromisoformat(str(rec["business_date"]))
        o = self.order(row.get("order_id")) if t not in ("fact_sales_order_line", "fact_customer_complaint") else None
        result = rec.get("result_code")
        failed = result is not None and result != PASS and t in ("fact_inspection_result", "fact_inspection_lot", "fact_process_parameter_record")
        # 人员:按岗位取人(同一业务对象取同一人)
        if col == "inspector_code":
            return emp_no(self.pick("质检员", rec.get("batch_no") or key))
        if col == "buyer_code":
            sid = row.get("supplier_id") or (ctx.rows("fact_supplier_contract", "supplier_contract_id").get(row.get("supplier_contract_id")) or {}).get("supplier_id")
            return emp_no(self.pick("采购员", sid))
        if col == "handled_by_code":
            return emp_no(self.pick("仓管员", row.get("warehouse_id") or row.get("target_warehouse_id") or key))
        if col == "planner_code":
            return emp_no(self.pick("计划员", rec.get("planning_period") or key))
        if col == "operator_code":
            if row.get("equipment_id"):
                return emp_no(self.tech_of_factory(self.equipment_factory(row["equipment_id"]), key))
            if o:
                return emp_no(self.leaders_of_line[o["line_id"]][_h(key) % 4])
            return emp_no(row.get("emp_id"))
        if col == "supervisor_code":
            line = row.get("line_id") or (o or {}).get("line_id")
            if line:
                ws = ctx.rows("dim_production_line", "line_id")[line]["workshop_id"]
                return emp_no(self.by_role["生产主管"][(ws - 1) % len(self.by_role["生产主管"])])
            emp = ctx.rows("dim_employee", "emp_id").get(row.get("emp_id"))
            if emp:                                                   # 部门负责人:部门内建档最早者
                return emp_no(min(e["emp_id"] for e in ctx.rows("dim_employee", "emp_id").values() if e["dept_id"] == emp["dept_id"]))
            return None
        if col in ("assessor_code", "evaluator_code"):
            emp = ctx.rows("dim_employee", "emp_id").get(row.get("emp_id"))
            if emp and emp["dept_id"] in (7, 8, 9):
                return emp_no(self.by_role["设备工程师"][0] if emp["dept_id"] == 9 else self.by_role["质量工程师"][0] if emp["dept_id"] == 8 else self.by_role["生产主管"][0])
            return emp_no(self.by_role["总经理"][0])
        if col == "receiver_code":
            return emp_no(self.by_role["设备工程师"][_h(key) % len(self.by_role["设备工程师"])])
        # 销售:区域、合同、客户订单号、价格表随客户与业务日期
        cust = row.get("cust_id")
        if col == "sales_region" and cust is not None:
            return self.cust_region(cust)
        if col == "contract_no" and cust is not None:
            return self.sales_contract(cust, bday)
        if col == "customer_po_no" and cust is not None:
            return f"CPO-{cust:03d}-{row.get('order_id') or bday.strftime('%y%m')}"
        if col == "price_list_code":
            return f"PL-{bday.year}-STD"
        # 采购:条款按供应商固定(继承框架合同),到厂价(DAP)
        sid = row.get("supplier_id")
        if sid is None and row.get("supplier_contract_id"):
            sid = ctx.rows("fact_supplier_contract", "supplier_contract_id")[row["supplier_contract_id"]]["supplier_id"]
        if sid is not None:
            if col == "payment_term_code":
                return self.supplier_terms(sid)
            if col == "incoterm_code":
                return "DAP"
            if col == "sourcing_method":
                return "框架协议"
            if col == "quality_agreement_no":
                return f"QA-S{int(sid):03d}-{bday.year}"
            if col == "supplier_quote_no":
                return f"SQ-S{int(sid):03d}-{bday.year}"
            if col == "lead_time_days":
                mats = [m for m in ctx.rows("dim_material", "material_id").values() if m["supplier_id"] == sid]
                return max((m["lead_time_days"] for m in mats), default=7)
        if col == "requisition_no" and row.get("po_id"):
            po = ctx.rows("fact_purchase_order", "po_id")[row["po_id"]]
            return "PR" + po["po_no"][2:]
        # 物流
        if col == "ship_from":
            if t == "fact_advance_shipping_notice" and sid is not None:
                sup = ctx.rows("dim_supplier", "supplier_id")[sid]
                return f"{sup['province']}{sup['city']}"
            wh = row.get("warehouse_id") or row.get("source_warehouse_id")
            return ctx.rows("dim_warehouse", "warehouse_id")[wh]["warehouse_name"] if wh else None
        if col == "ship_to_city":
            if t == "fact_advance_shipping_notice":
                return "青岛"
            if row.get("target_warehouse_id"):
                return {4: "青岛", 6: "合肥"}.get(row["target_warehouse_id"], "青岛")
            c = ctx.rows("dim_customer", "cust_id").get(cust)
            return c["city"] if c else None
        if col == "vehicle_no":
            return self.vehicles(rec.get("quantity") or 0.0, key)
        if col == "carrier_tracking_no":
            return f"YD{bday.strftime('%y%m%d')}{_h('trk', *key) % 1000000:06d}"
        # 库存:库位、批次日期、计价
        if col == "bin_code":
            wh = row.get("warehouse_id") or row.get("target_warehouse_id")
            if wh is None and o:
                wh = self.fg_wh(o["prod_id"]) if t in ("fact_inspection_lot", "fact_batch_quality_hold", "fact_scrap_event", "fact_rework_order") \
                    else self.raw_wh_of_factory(self.line_factory(o["line_id"]))
            if wh is None and row.get("prod_id"):
                wh = self.fg_wh(row["prod_id"])
            if wh is None and row.get("material_id"):
                wh = 1
            return self.bin(wh, row.get("material_id") or row.get("prod_id") or 0)
        if col in ("manufacture_date", "expiry_date"):
            bd = self.batch_dates(rec.get("batch_no") or rec.get("lot_no"))
            if bd is None:
                return None
            return (bd[0] if col == "manufacture_date" else bd[1]).isoformat()
        if col == "valuation_type":
            return "移动加权平均"
        if col == "issue_warehouse" and o:
            return ctx.rows("dim_warehouse", "warehouse_id")[self.raw_wh_of_factory(self.line_factory(o["line_id"]))]["warehouse_name"]
        if col == "issue_type":
            return "完工倒冲"
        if col == "bom_qty_per_ton" and o and row.get("material_id", row.get("_material_id")) is not None:
            return _bom_t(ctx, o["prod_id"], int(row.get("material_id", row.get("_material_id")) or 0))
        if col == "standard_quantity" and o and row.get("material_id", row.get("_material_id")) is not None:
            per = _bom_t(ctx, o["prod_id"], int(row.get("material_id", row.get("_material_id")) or 0))
            return round(per / 1000.0 * float(o["actual_quantity"] or o["planned_quantity"]), 4) if per is not None else None   # 吨
        if col == "usage_variance_rate" and o:
            std = self.value(t, "standard_quantity", rec, row)
            return round((rec["quantity"] - std) / std, 4) if std else None
        # 生产
        if col == "work_center_code":
            line = row.get("line_id") or (o or {}).get("line_id")
            if line:
                return f"WC{line:02d}02"                              # 挤出造粒工位
        if col == "shift_code" and rec.get("start_time"):
            h = int(str(rec["start_time"])[11:13])
            return "DAY" if 8 <= h < 16 else "SWING" if h >= 16 else "NIGHT"
        # 质量:合格记录没有缺陷、处置、根因与围堵;不合格按特性定根因
        if col in ("defect_code", "disposition", "root_cause_code", "containment_action", "verification_result") and \
                t in ("fact_inspection_result", "fact_inspection_lot", "fact_process_parameter_record"):
            if not failed:
                return None
            if col == "root_cause_code":
                return ROOT_CAUSE.get(str(rec.get("characteristic_code") or "").split("-")[0], "法") if t != "fact_process_parameter_record" else "法"
            if col == "containment_action":
                return "隔离该批次,冻结库存" if t != "fact_process_parameter_record" else "调整工艺并加严首检"
            if col == "verification_result":
                return None
        if col == "severity" and t in ("fact_inspection_result", "fact_inspection_lot", "fact_process_parameter_record"):
            return None if not failed else (3 if result == "不合格" else 1)
        if col == "sample_size" and t == "fact_process_parameter_record":
            return 1
        # 环境:监测点与执行标准按介质
        if t in ("fact_environment_measurement", "fact_emission_event") and row.get("emission_type_id"):
            code = ctx.rows("dim_emission_type", "emission_type_id")[row["emission_type_id"]]["code"]
            point, std = EMISSION_RULES.get(code, ("厂区", "排污许可证"))
            if col == "monitoring_point":
                return point
            if col == "regulatory_clause":
                return std
            if col == "sampling_method":
                return "手工采样"
            if col == "evidence_reference":
                return f"月度监测报告-{bday.strftime('%Y%m')}"
        # 财务
        if col == "tax_rate":
            return row.get("_tax_rate")
        if col in ("fiscal_year", "fiscal_period"):
            return bday.year if col == "fiscal_year" else bday.month
        # 通用
        if col == "plan_version" and rec.get("planning_period"):
            return f"{rec['planning_period']}-V1"
        if col == "scenario_code":
            return "BASE"
        if col == "review_status":
            return "已评审"
        if col == "freeze_horizon_days":
            return 7
        if col == "exception_code" and t == "fact_capacity_plan":
            return "高负荷" if (rec.get("utilization_rate") or 0) >= 0.9 else None
        if col == "cost_center_code" and t == "fact_cash_flow":
            if cust is None:
                return None                                     # 支付货款不归成本中心
            return "CC008" if self.cust_region(cust) in ("华东", "华南", "西南") else "CC009"
        if col == "exception_code":
            return None if (rec.get("backlog_quantity") or 0) <= 0 else "DEMAND_GAP"
        return MISSING


def _bom_t(ctx: S.Context, prod_id: int, mat_id: int) -> float | None:
    """BOM 单耗,千克原料/吨产品(与 108 表 dim_bom_line.qty_per_ton 同单位)。"""
    boms = {b for b, r in ctx.rows("dim_bom", "bom_id").items() if r["product_id"] == prod_id}
    for r in ctx.rows("dim_bom_line", "bom_line_id").values():
        if r["bom_id"] in boms and r["material_id"] == mat_id:
            return round(float(r["qty_per_ton"]), 4)
    return None
