#!/usr/bin/env python3
"""Summary tables (DWS_* and agg_metric_monthly) for the 108-table demo database (DR-076).

Every summary is recomputed from the simulated detail tables, so layers reconcile by
construction; the grain and the definition of each measure are written into the metadata.
Standard library only.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import demo108_master as M
from demo108_sim import Sim, dt

GRAINS = {
    "DWS_IN_SALES_DAILY": "按业务员×日:当日接单数;当日末未交付完的订单数",
    "DWS_AFTER_SALES_DAILY": "按业务员×日:当日退货次数与退货数量(吨)",
    "DWS_CASH_DAILY": "按业务员×日:当日末已发货未开票的数量与金额(不含税);当日开票的平均开票天数;当日回款中银承占比(%)",
    "DWS_KPI_DAILY": "按业务员×日:截至当日的月累计销量/毛利是否达到按日折算的目标;部门(区域)指标为该部门达标人数",
    "DWS_MARKET_DAILY": "按客户×日:年度市场容量、近 365 天份额、客户状态(存量/新客/休眠/流失/风险,各为 0/1)、当日成交价相对指导价偏离(%)",
    "DWS_ORG_EXPENSE_DAILY": "按业务员×日:当日报销的销售费用;部门月度费用预算按天与人数折算",
    "DWS_ORG_PERFORMANCE_DAILY": "按业务员×日:当日接单销量、金额(不含税)、实际与标准毛利;当日末应收账款余额(含税)",
    "DWS_ORG_TARGET_DAILY": "按业务员×日:月度销量、毛利、回款目标与发货计划按当月天数折算",
    "DWS_PRE_SALES_DAILY": "按业务员×日:当日新建商机数与预估量、当日丢单数、当日搁置商机数、当日新建重要商机数、当日关闭商机的平均周期(天)、当日送样量与次数",
    "DWS_RISK_DAILY": "按业务员×产品×日(只保留有风险的组合):在库超过 90 天的成品(归属该产品近一年销量最大的业务员)、当日负毛利订单量、当日末超期应收",
    "DWS_PRODUCTION_DAILY": "按产线×日×产品:计划与实际产量、成材率(产出/投料)、优等品率、工单数、投料与损耗、运行与停机时长、OEE、利用率、用电与单耗、计划达成率、检验批合格率",
    "DWS_EQUIPMENT_DAILY": "按设备×日(所在产线当日有运行或停机):运行时长、本设备停机时长与次数、报警数、维护工单新建与完成数、产线 OEE",
    "DWS_SAFETY_DAILY": "按车间×日:事故数(不含未遂)、未遂数、风险评估数(危险源所在车间)、高风险评估数(L3 及以上)、作业许可数、培训场次(按组织车间归集)、当日审计得分、当日环境监测合规率(%)",
    "DWS_FINANCE_DAILY": "按成本中心×月末日:预算与实际、销售费用、产品成本(材料/人工/制造费用)、折旧",
    "DWS_PROCUREMENT_DAILY": "按供应商×日(只保留有业务的日子):下单数与金额、收货批次与数量、来料检验合格率(%)、新增应付(含税)、付款金额",
    "agg_metric_monthly": "按业务员×月:销量、销售额、毛利、发货量、费用、回款、月末应收余额、商机、送样、订单、退货与目标",
}


def _d(x: Any) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def build(s: Sim) -> None:
    t, m = s.t, s.m
    days = M.days()
    reps = [e for e in m.get("dim_employee") if e["dept_id"] <= 6]
    rep_ids = [e["emp_id"] for e in reps]
    emp = {e["emp_id"]: e for e in m.get("dim_employee")}
    dept = {d["dept_id"]: d for d in m.get("dim_department")}
    region = {r["region_id"]: r["region_name"] for r in m.get("dim_region")}
    hire = {e["emp_id"]: _d(e["hire_date"]) for e in reps}
    leave = {s.rep_transfer["emp_id"]: s.rep_transfer["date"]}

    def active(e: int, d: date) -> bool:
        return hire[e] <= d and (e not in leave or d <= leave[e])

    orders = t["fact_sales_order"]
    odate = {o["order_id"]: _d(o["order_date"]) for o in orders}
    order_of_delivery = {dl["delivery_id"]: dl for dl in t["fact_delivery"]}
    # 订单交付完成日(未完成为 None)
    done_day: dict[int, date | None] = {}
    deliv_by_order = defaultdict(list)
    for dl in t["fact_delivery"]:
        deliv_by_order[dl["order_id"]].append(dl)
    for o in orders:
        ds = deliv_by_order[o["order_id"]]
        delivered = sum(x["quantity"] for x in ds)
        done_day[o["order_id"]] = max(_d(x["delivery_date"]) for x in ds) if ds and delivered >= o["quantity"] - 1e-6 else None

    by = defaultdict(lambda: defaultdict(float))       # (表, 业务员, 日) → 指标
    for o in orders:
        k = (o["emp_id"], odate[o["order_id"]])
        by[k]["order_count"] += 1
        by[k]["sales_quantity"] += o["quantity"]
        by[k]["sales_amount"] += o["amount"]
        by[k]["gp_a"] += o["gross_profit_actual"]
        by[k]["gp_s"] += o["gross_profit_standard"]
        start, end = odate[o["order_id"]], done_day[o["order_id"]] or (M.END + timedelta(days=1))
        d = start
        while d < end and d <= M.END:
            by[(o["emp_id"], d)]["undelivered"] += 1
            d += timedelta(days=1)
    for r in t["fact_return"]:
        o = orders[r["order_id"] - 1]
        k = (o["emp_id"], _d(r["return_date"]))
        by[k]["return_count"] += 1
        by[k]["return_quantity"] += r["quantity"]
    inv_by_delivery = {i["delivery_id"]: i for i in t["fact_invoice"] if i["amount_no_tax"] > 0}   # 红字发票不算开票
    for dl in t["fact_delivery"]:
        o = orders[dl["order_id"] - 1]
        inv = inv_by_delivery.get(dl["delivery_id"])
        d, end = _d(dl["delivery_date"]), (_d(inv["invoice_date"]) if inv else M.END + timedelta(days=1))
        while d < end and d <= M.END:
            by[(o["emp_id"], d)]["git_qty"] += dl["quantity"]
            by[(o["emp_id"], d)]["git_amt"] += dl["amount"]
            d += timedelta(days=1)
    for inv in t["fact_invoice"]:
        if inv["amount_no_tax"] < 0:
            continue
        o = orders[order_of_delivery[inv["delivery_id"]]["order_id"] - 1]
        k = (o["emp_id"], _d(inv["invoice_date"]))
        by[k]["inv_n"] += 1
        by[k]["inv_days"] += inv["days_to_invoice"]
    ar_daily = defaultdict(float)
    overdue = defaultdict(lambda: [0.0, 0])
    for a in t["fact_ar_aging"]:
        o = orders[order_of_delivery[t["fact_invoice"][a["invoice_id"] - 1]["delivery_id"]]["order_id"] - 1]
        idate = _d(t["fact_invoice"][a["invoice_id"] - 1]["invoice_date"])
        pays = sorted(a["_payments"], key=lambda p: p["date"])
        if a["_credit_note"]:                                       # 红字发票当即冲减原发票,自身不形成余额
            continue
        settle = sorted([(p["date"], p["amount"]) for p in pays] + list(a["_credits"]))
        bal = a["_total"]
        d, pi = idate, 0
        while d <= M.END:
            while pi < len(settle) and settle[pi][0] <= d:
                bal -= settle[pi][1]
                pi += 1
            if bal <= 0.005:
                break
            ar_daily[(a["emp_id"], d)] += bal
            if d > a["_due"]:
                ov = overdue[(a["emp_id"], o["prod_id"], d)]
                ov[0] += bal
                ov[1] += 1
            d += timedelta(days=1)
        for p in pays:
            k = (a["emp_id"], p["date"])
            by[k]["pay_total"] += p["amount"]
            by[k]["pay_bank"] += p["amount"] if p["bank"] else 0.0
    for x in t["fact_expense"]:
        by[(x["emp_id"], _d(x["expense_date"]))]["expense"] += x["amount"]
    for op in t["fact_opportunity"]:
        k = (op["emp_id"], _d(op["create_date"]))
        by[k]["opp_n"] += 1
        by[k]["opp_q"] += op["estimated_quantity"]
        by[k]["opp_imp"] += op["is_important"]
        if op["status"] == "stalled":
            by[k]["opp_stalled"] += 1
        if op["close_date"]:
            kc = (op["emp_id"], _d(op["close_date"]))
            by[kc]["opp_closed"] += 1
            by[kc]["opp_cycle"] += (_d(op["close_date"]) - _d(op["create_date"])).days
            if op["status"] == "lost":
                by[kc]["opp_lost"] += 1
    for sm in t["fact_sample"]:
        k = (sm["emp_id"], _d(sm["sample_date"]))
        by[k]["sample_n"] += 1
        by[k]["sample_q"] += sm["quantity"]
    target = {(x["emp_id"], x["year"], x["month"]): x for x in t["fact_sales_target"]}
    plan = {(x["emp_id"], x["year"], x["month"]): x for x in t["fact_sales_plan"]}
    bud = {(x["dept_id"], x["year"], x["month"]): x["budget_amount"] for x in t["fact_budget_expense"]}
    dim_days = defaultdict(int)
    for d in days:
        dim_days[(d.year, d.month)] += 1

    def reps_in(dept_id: int, d: date) -> int:
        return max(1, sum(1 for e in rep_ids if emp[e]["dept_id"] == dept_id and active(e, d)))

    mtd = defaultdict(lambda: [0.0, 0.0])
    for d in days:
        for e in rep_ids:
            if not active(e, d):
                continue
            b = by[(e, d)]
            dp = emp[e]["dept_id"]
            nm, dn, rn = emp[e]["emp_name"], dept[dp]["dept_name"], region[dept[dp]["region_id"]]
            key = (e, d.year, d.month)
            acc = mtd[key]
            acc[0] += b["sales_quantity"]
            acc[1] += b["gp_a"]
            tg = target.get(key)
            dm = dim_days[(d.year, d.month)]
            t["DWS_IN_SALES_DAILY"].append({"date": d.isoformat(), "dim_emp_id": e, "order_count": int(b["order_count"]),
                                            "undelivered_order_count": int(b["undelivered"]), "emp_name": nm})
            t["DWS_AFTER_SALES_DAILY"].append({"date": d.isoformat(), "dim_emp_id": e, "return_count": int(b["return_count"]),
                                               "return_quantity": round(b["return_quantity"], 4), "emp_name": nm})
            t["DWS_CASH_DAILY"].append({"date": d.isoformat(), "dim_emp_id": e, "goods_in_transit_qty": round(b["git_qty"], 4),
                                        "goods_in_transit_amt": round(b["git_amt"], 2),
                                        "avg_invoice_days": round(b["inv_days"] / b["inv_n"], 2) if b["inv_n"] else None,
                                        "bank_acceptance_ratio": round(b["pay_bank"] / b["pay_total"] * 100, 2) if b["pay_total"] else None,
                                        "emp_name": nm})
            t["DWS_ORG_PERFORMANCE_DAILY"].append({"date": d.isoformat(), "dim_emp_id": e, "dim_dept_id": dp, "dim_region_id": dept[dp]["region_id"],
                                                   "sales_quantity": round(b["sales_quantity"], 2), "sales_amount": round(b["sales_amount"], 2),
                                                   "gross_profit_actual": round(b["gp_a"], 2), "gross_profit_standard": round(b["gp_s"], 2),
                                                   "accounts_receivable": round(ar_daily[(e, d)], 2), "emp_name": nm, "dept_name": dn, "region_name": rn})
            t["DWS_ORG_EXPENSE_DAILY"].append({"date": d.isoformat(), "dim_emp_id": e, "dim_dept_id": dp, "dim_region_id": dept[dp]["region_id"],
                                               "sales_expense": round(b["expense"], 2),
                                               "expense_budget": round(bud.get((dp, d.year, d.month), 0.0) / dm / reps_in(dp, d), 2),
                                               "emp_name": nm, "dept_name": dn, "region_name": rn})
            pl = plan.get(key)
            t["DWS_ORG_TARGET_DAILY"].append({"date": d.isoformat(), "dim_emp_id": e, "dim_dept_id": dp,
                                              "sales_quantity_target": _split(tg["sales_quantity_target"], dm, d.day) if tg else 0.0,
                                              "profit_target": _split(tg["profit_target"], dm, d.day) if tg else 0.0,
                                              "payment_target": _split(tg["payment_target"], dm, d.day) if tg else 0.0,
                                              "sales_plan_quantity": _split(pl["planned_quantity"], dm, d.day) if pl else 0.0,
                                              "emp_name": nm, "dept_name": dn})
            t["DWS_PRE_SALES_DAILY"].append({"date": d.isoformat(), "dim_emp_id": e, "opp_estimated_quantity": round(b["opp_q"], 2),
                                             "opp_count": int(b["opp_n"]), "lost_opp_count": int(b["opp_lost"]), "stalled_opp_count": int(b["opp_stalled"]),
                                             "important_opp_count": int(b["opp_imp"]),
                                             "opp_cycle": round(b["opp_cycle"] / b["opp_closed"], 2) if b["opp_closed"] else None,
                                             "sample_quantity": round(b["sample_q"], 4), "sample_count": int(b["sample_n"]), "emp_name": nm})
            frac = d.day / dm
            hit_q = int(tg is not None and acc[0] >= tg["sales_quantity_target"] * frac)
            hit_p = int(tg is not None and acc[1] >= tg["profit_target"] * frac)
            by[(dp, d)]["dept_hit_q"] += hit_q
            by[(dp, d)]["dept_hit_p"] += hit_p
            t["DWS_KPI_DAILY"].append({"date": d.isoformat(), "dim_emp_id": e, "dim_dept_id": dp, "kpi_dept_count": 0, "kpi_region_count": 0,
                                       "kpi_person_count": hit_q, "kpt_dept_count": 0, "kpt_region_count": 0, "kpt_person_count": hit_p,
                                       "emp_name": nm, "dept_name": dn})
    for r in t["DWS_KPI_DAILY"]:
        b = by[(r["dim_dept_id"], _d(r["date"]))]
        r["kpi_dept_count"] = r["kpi_region_count"] = int(b["dept_hit_q"])      # 销售部门与区域一一对应
        r["kpt_dept_count"] = r["kpt_region_count"] = int(b["dept_hit_p"])

    # 客户 × 日
    cust_orders = defaultdict(list)
    for o in orders:
        cust_orders[o["cust_id"]].append(o)
    cust_overdue_day = defaultdict(set)
    for a in t["fact_ar_aging"]:
        if a["_credit_note"]:
            continue
        settle = sorted([(p["date"], p["amount"]) for p in a["_payments"]] + list(a["_credits"]))
        bal, pi = a["_total"], 0
        d = a["_due"] + timedelta(days=61)
        while d <= M.END:
            while pi < len(settle) and settle[pi][0] <= d:
                bal -= settle[pi][1]
                pi += 1
            if bal <= 0.005:
                break
            cust_overdue_day[a["cust_id"]].add(d)
            d += timedelta(days=1)
    for c in t["dim_customer"]:
        cid = c["cust_id"]
        os_ = sorted(cust_orders[cid], key=lambda o: o["order_date"])
        first = _d(c["first_deal_date"]) if c["first_deal_date"] else None   # 首次成交日取客户主数据(老客户早于上线)
        by_day = defaultdict(list)
        for o in os_:
            by_day[_d(o["order_date"])].append(o)
        last = (M.START - timedelta(days=20)) if first and first < M.START else None   # 老客户上线前有成交
        vol: list[tuple[date, float]] = []
        for d in days:
            for o in by_day.get(d, []):
                last = d
                vol.append((d, o["quantity"]))
            v365 = sum(q for dd, q in vol if dd > d - timedelta(days=365))
            gap = (d - last).days if last else None
            new = int(first is not None and first <= d < first + timedelta(days=90))
            lost = int(gap is not None and gap > 180)
            dormant = int(gap is not None and 90 < gap <= 180)
            existing = int(last is not None and not lost and not dormant and not new)
            risk = int(d in cust_overdue_day[cid])
            todays = by_day.get(d, [])
            dev = (sum((o["unit_price"] - o["guide_price"]) / o["guide_price"] * 100 for o in todays) / len(todays)) if todays else None
            t["DWS_MARKET_DAILY"].append({"date": d.isoformat(), "dim_cust_id": cid, "dim_industry": c["industry"],
                                          "market_capacity": c["market_capacity"], "market_share": round(v365 / c["market_capacity"] * 100, 2),
                                          "existing_customer_count": existing, "new_customer_count": new, "dormant_customer_count": dormant,
                                          "lost_customer_count": lost, "risk_customer_count": risk,
                                          "guide_price_deviation": round(dev, 2) if dev is not None else None, "cust_name": c["cust_name"]})

    # 风险:超期库存 / 负毛利 / 超期应收
    owner = {}
    vol_rep = defaultdict(float)
    for o in orders:
        if _d(o["order_date"]) > M.END - timedelta(days=365):
            vol_rep[(o["prod_id"], o["emp_id"])] += o["quantity"]
    for pid in s.prod:
        cand = [(v, e) for (p, e), v in vol_rep.items() if p == pid]
        owner[pid] = max(cand)[1] if cand else rep_ids[0]
    moves = defaultdict(list)
    for pid, batch, wdate, when, q in s.fg_moves:
        moves[(pid, batch)].append((when, q, wdate))
    old_stock = defaultdict(float)
    old_amt = defaultdict(float)
    bcost = {b["batch"]: round(b["unit_cost"], 2) for bs in s.fg.values() for b in bs}
    for (pid, batch), ev in moves.items():
        ev.sort()
        wdate = ev[0][2]
        bal, i = 0.0, 0
        d = max(wdate + timedelta(days=91), M.START)
        while d <= M.END:
            while i < len(ev) and ev[i][0] <= d:
                bal += ev[i][1]
                i += 1
            if bal > 0.005:
                old_stock[(pid, d)] += bal
                old_amt[(pid, d)] += bal * bcost[batch]
            elif i >= len(ev):
                break
            d += timedelta(days=1)
    unit_cost = {}
    for pid, batches in s.fg.items():
        unit_cost[pid] = (sum(b["unit_cost"] for b in batches) / len(batches)) if batches else s.prod[pid]["standard_cost"]
    neg = defaultdict(float)
    for o in orders:
        if o["is_negative_margin"]:
            neg[(o["emp_id"], o["prod_id"], _d(o["order_date"]))] += o["quantity"]
    keys = set(neg) | set(overdue) | {(owner[p], p, d) for (p, d) in old_stock}
    for e, pid, d in sorted(keys, key=lambda k: (k[2], k[0], k[1])):
        if not active(e, d):
            continue
        inv_q = old_stock.get((pid, d), 0.0) if owner[pid] == e else 0.0
        ov = overdue.get((e, pid, d), [0.0, 0])
        t["DWS_RISK_DAILY"].append({"date": d.isoformat(), "dim_emp_id": e, "dim_prod_id": pid, "overdue_inventory_qty": round(inv_q, 4),
                                    "overdue_inventory_amt": round(old_amt.get((pid, d), 0.0), 2) if inv_q else 0.0, "negative_margin_qty": round(neg.get((e, pid, d), 0.0), 2),
                                    "overdue_ar_amt": round(ov[0], 2), "overdue_ar_count": int(ov[1]), "emp_name": emp[e]["emp_name"],
                                    "prod_name": s.prod[pid]["prod_name"]})

    production_daily(s)
    equipment_daily(s)
    safety_daily(s)
    finance_monthly(s)
    procurement_daily(s)
    monthly(s, rep_ids, emp, target, active)


def _split(total: float, days_in_month: int, day: int) -> float:
    """月度目标按天折算;尾差放在月末最后一天,使日合计等于月度目标。"""
    base = round(total / days_in_month, 2)
    return round(total - base * (days_in_month - 1), 2) if day == days_in_month else base


def production_daily(s: Sim) -> None:
    t = s.t
    lines = s.lines
    ws_name = {w["workshop_id"]: w["workshop_name"] for w in s.m.get("dim_workshop")}
    cons = defaultdict(lambda: [0.0, 0.0])
    for c in t["fact_material_consumption"]:
        cons[c["order_id"]][0] += c["actual_qty"]
        cons[c["order_id"]][1] += c["scrap_qty"]
    insp = defaultdict(list)
    for q in t["fact_quality_inspection"]:
        insp[q["order_id"]].append(q["result"])
    fgr = {}
    for out in t["fact_production_output"]:
        fgr[out["order_id"]] = out["first_grade_rate"]
    acc_all: dict[tuple[int, date, int], dict[str, float]] = {}

    def acc_of(key: tuple[int, date, int]) -> dict[str, float]:
        return acc_all.setdefault(key, {"plan": 0.0, "act": 0.0, "inp": 0.0, "scrap": 0.0, "n": 0, "fg": 0.0, "ok": 0, "lots": 0,
                                        "run": 0.0, "down": 0.0, "kwh": 0.0, "made": 0.0})
    # 产出类(完工日)
    for o in t["fact_production_order"]:
        if o["status"] != "已完成":
            continue
        d = _d(o["end_time"])
        inp, scrap = cons[o["order_id"]]
        acc = acc_of((o["line_id"], d, o["prod_id"]))
        acc["plan"] += o["planned_quantity"]
        acc["act"] += o["actual_quantity"]
        acc["inp"] += inp
        acc["scrap"] += scrap
        acc["n"] += 1
        acc["fg"] += fgr.get(o["order_id"], 0.0) * o["actual_quantity"]
        acc["lots"] += 1
        acc["ok"] += int(all(r != "不合格" for r in insp[o["order_id"]]))
    # 时长与能耗类(日历日):产线当日运行、停机与用电按各产品当日运行时长分摊;当日没有在产产品的(检修、待产)记到上一个生产的产品
    last_prod = {lid: s.line_products[lid][0] for lid in lines}
    for d in M.days():
        for lid in lines:
            ld = t["_line_day"][(lid, d)]
            win = ld.get("win_p") or {}
            tot = sum(win.values())
            shares = {p_: w / tot for p_, w in win.items()} if tot > 0 else {last_prod[lid]: 1.0}
            for p_, sh in shares.items():
                acc = acc_of((lid, d, p_))
                acc["run"] += ld["run"] * sh
                acc["down"] += ld.get("down", 0.0) * sh
                acc["kwh"] += ld.get("kwh", 0.0) * sh
                acc["made"] += (ld.get("qty_p") or {}).get(p_, 0.0)
            if win:
                last_prod[lid] = max(win, key=lambda k: (win[k], k))
    for (lid, d, pid), a in sorted(acc_all.items(), key=lambda x: (x[0][1], x[0][0], x[0][2])):
        ld = t["_line_day"][(lid, d)]
        t["DWS_PRODUCTION_DAILY"].append({"date": d.isoformat(), "dim_line_id": lid, "dim_workshop_id": lines[lid]["workshop_id"], "dim_prod_id": pid,
                                          "planned_quantity": round(a["plan"], 2), "actual_quantity": round(a["act"], 2),
                                          "yield_rate": round(a["act"] / a["inp"] * 100, 2) if a["inp"] else None,
                                          "first_grade_rate": round(a["fg"] / a["act"], 2) if a["act"] else None, "order_count": int(a["n"]),
                                          "material_input_qty": round(a["inp"], 4), "material_scrap_qty": round(a["scrap"], 4),
                                          "material_scrap_rate": round(a["scrap"] / a["inp"] * 100, 2) if a["inp"] else None,
                                          "actual_run_hours": round(a["run"], 4), "downtime_hours": round(a["down"], 4),
                                          "oee": round(ld.get("oee", 0.0) * 100, 2), "utilization_rate": round(ld["run"] / 24 * 100, 2),
                                          "electricity_kwh": round(a["kwh"], 4),
                                          "unit_energy_consumption": round(a["kwh"] / a["made"], 2) if a["made"] >= 0.5 else None,
                                          "plan_achievement_rate": round(a["act"] / a["plan"] * 100, 2) if a["plan"] else None,
                                          "quality_pass_rate": round(a["ok"] / a["lots"] * 100, 2) if a["lots"] else None,
                                          "line_name": lines[lid]["line_name"], "workshop_name": ws_name[lines[lid]["workshop_id"]],
                                          "prod_name": s.prod[pid]["prod_name"]})


def equipment_daily(s: Sim) -> None:
    t = s.t
    down = defaultdict(lambda: [0.0, 0])
    for dn in t["fact_downtime"]:
        st, en = datetime.fromisoformat(dn["start_time"]), datetime.fromisoformat(dn["end_time"])
        d = st.date()
        while d <= en.date() and d <= M.END:
            h = max(0.0, (min(en, dt(d) + timedelta(days=1)) - max(st, dt(d))).total_seconds() / 3600)
            if h > 0:
                down[(dn["equipment_id"], d)][0] += h
                if d == st.date():
                    down[(dn["equipment_id"], d)][1] += 1
            d += timedelta(days=1)
    alarms = defaultdict(int)
    for a in t["fact_alarm"]:
        alarms[(a["equipment_id"], _d(a["alarm_time"]))] += 1
    wo_new, wo_done = defaultdict(int), defaultdict(int)
    for w in t["fact_maintenance_wo"]:
        wo_new[(w["equipment_id"], _d(w["wo_date"]))] += 1
        if w["status"] == "completed":
            wo_done[(w["equipment_id"], _d(w["end_time"]))] += 1
    lname = {lid: l["line_name"] for lid, l in s.lines.items()}
    for d in M.days():
        for e in s.m.get("dim_equipment"):
            ld = t["_line_day"][(e["line_id"], d)]
            k = (e["equipment_id"], d)
            if ld["run"] <= 0 and ld.get("down", 0) <= 0 and ld.get("planned_down", 0) <= 0 and not (alarms[k] or wo_new[k] or wo_done[k]):
                continue
            own_down = down[k][0]
            t["DWS_EQUIPMENT_DAILY"].append({"date": d.isoformat(), "dim_equipment_id": e["equipment_id"], "dim_line_id": e["line_id"],
                                             "actual_run_hours": round(ld["run"], 2), "downtime_hours": round(min(24.0, own_down), 2),
                                             "downtime_count": down[k][1], "alarm_count": alarms[k], "maint_wo_count": wo_new[k],
                                             "maint_completed_count": wo_done[k], "oee": round(ld.get("oee", 0.0) * 100, 2),
                                             "equipment_name": e["equipment_name"], "line_name": lname[e["line_id"]]})


def safety_daily(s: Sim) -> None:
    t = s.t
    c = defaultdict(lambda: defaultdict(float))
    for i in t["fact_incident"]:
        k = (i["workshop_id"], _d(i["incident_date"]))
        c[k]["near" if i["severity"] == "near_miss" else "inc"] += 1
    for r in t["fact_risk_assessment"]:
        k = (M.HAZARD_WORKSHOP[r["hazard_id"]], _d(r["assess_date"]))
        c[k]["ra"] += 1
        c[k]["hr"] += int(r["risk_level"] in ("L3", "L4"))
    eq_ws = {e["equipment_name"]: s.lines[e["line_id"]]["workshop_id"] for e in s.m.get("dim_equipment")}
    for p in t["fact_work_permit"]:
        c[(eq_ws[p["work_location"]], _d(p["valid_from"]))]["permit"] += 1
    for tr in t["fact_safety_training"]:
        c[((tr["training_id"] - 1) % 5 + 1, _d(tr["training_date"]))]["train"] += 1
    audit = {(a["workshop_id"], _d(a["audit_date"])): a["score"] for a in t["fact_safety_audit"]}
    env = defaultdict(list)
    for r in t["fact_env_record"]:
        env[_d(r["record_date"])].append(r["is_compliant"])
    ws_fac = {w[0]: w[2] for w in M.WORKSHOPS}
    names = {w["workshop_id"]: w["workshop_name"] for w in s.m.get("dim_workshop")}
    for d in M.days():
        for ws in range(1, 6):
            k = (ws, d)
            e = env.get(d) if ws_fac[ws] == 1 else None                      # 环境监测点在青岛一厂
            t["DWS_SAFETY_DAILY"].append({"date": d.isoformat(), "dim_workshop_id": ws, "incident_count": int(c[k]["inc"]),
                                          "near_miss_count": int(c[k]["near"]), "risk_assessment_count": int(c[k]["ra"]),
                                          "high_risk_count": int(c[k]["hr"]), "permit_count": int(c[k]["permit"]),
                                          "training_count": int(c[k]["train"]), "audit_score": audit.get(k),
                                          "env_compliant_rate": round(sum(e) / len(e) * 100, 2) if e else None, "workshop_name": names[ws]})


def finance_monthly(s: Sim) -> None:
    t = s.t
    exp = defaultdict(float)
    for x in t["fact_expense"]:
        exp[(x["dept_id"], x["expense_date"][:7])] += x["amount"]
    sales_cc = {8: (1, 2, 4), 9: (3, 5, 6)}                      # 南方:华东/华南/西南;北方:华北/华中/西北(与预算归集一致)
    line_cc = {lid: l["workshop_id"] for lid, l in s.lines.items()}
    batch_line = {o["batch_no"]: o["line_id"] for o in t["fact_production_order"]}
    pc = defaultdict(lambda: [0.0, 0.0, 0.0])
    for c in t["fact_product_cost"]:
        a = pc[(line_cc[batch_line[c["batch_no"]]], c["period"])]
        a[0] += c["material_cost"]
        a[1] += c["labor_cost"]
        a[2] += c["overhead_cost"]
    dep = defaultdict(float)
    asset_cc = {a["asset_id"]: a["cost_center_id"] for a in t["dim_fixed_asset"]}
    for r in t["fact_depreciation"]:
        dep[(asset_cc[r["asset_id"]], r["period"])] += r["dep_amount"]
    names = {c["cc_id"]: c["cc_name"] for c in s.m.get("dim_cost_center")}
    for b in t["fact_budget"]:
        per = f"{b['year']}-{b['month']:02d}"
        last = (date(b["year"] + b["month"] // 12, b["month"] % 12 + 1, 1) - timedelta(days=1))
        cc = b["cc_id"]
        mat, lab, ovh = pc.get((cc, per), [0.0, 0.0, 0.0])
        t["DWS_FINANCE_DAILY"].append({"date": min(last, M.END).isoformat(), "dim_cc_id": cc, "budget_amount": b["budget_amount"],
                                       "actual_amount": b["actual_amount"],
                                       "expense_amount": round(sum(exp[(dpt, per)] for dpt in sales_cc.get(cc, ())), 2),
                                       "material_cost": round(mat, 2), "labor_cost": round(lab, 2), "overhead_cost": round(ovh, 2),
                                       "total_product_cost": round(mat + lab + ovh, 2), "dep_amount": round(dep[(cc, per)], 2), "cc_name": names[cc]})


def procurement_daily(s: Sim) -> None:
    t = s.t
    acc = defaultdict(lambda: defaultdict(float))
    po_sup = {p["po_id"]: p["supplier_id"] for p in t["fact_purchase_order"]}
    for p in t["fact_purchase_order"]:
        if p["status"] == "draft":
            continue
        k = (p["supplier_id"], _d(p["po_date"]))
        acc[k]["po_n"] += 1
        acc[k]["po_amt"] += p["total_amount"]
    for g in t["fact_goods_receipt"]:
        k = (po_sup[g["po_id"]], _d(g["receipt_date"]))
        acc[k]["gr_n"] += 1
        acc[k]["gr_q"] += g["quantity"] if g["inspection_result"] == "pass" else 0.0
        acc[k]["pass"] += int(g["inspection_result"] == "pass")
    gr_date = {g["gr_id"]: _d(g["receipt_date"]) for g in t["fact_goods_receipt"]}
    for ap in t["fact_ap"]:
        acc[(ap["supplier_id"], gr_date[ap["gr_id"]])]["ap"] += ap["amount"]
    ap_sup = {ap["ap_id"]: ap["supplier_id"] for ap in t["fact_ap"]}
    for p in t["fact_payment_out"]:
        acc[(ap_sup[p["ap_id"]], _d(p["payment_date"]))]["pay"] += p["amount"]
    names = {x["supplier_id"]: x["supplier_name"] for x in s.m.get("dim_supplier")}
    for (sid, d), a in sorted(acc.items(), key=lambda x: (x[0][1], x[0][0])):
        if d > M.END:
            continue
        t["DWS_PROCUREMENT_DAILY"].append({"date": d.isoformat(), "dim_supplier_id": sid, "po_count": int(a["po_n"]), "po_amount": round(a["po_amt"], 2),
                                           "gr_count": int(a["gr_n"]), "gr_quantity": round(a["gr_q"], 3),
                                           "inspection_pass_rate": round(a["pass"] / a["gr_n"] * 100, 2) if a["gr_n"] else None,
                                           "ap_amount": round(a["ap"], 2), "payment_amount": round(a["pay"], 2), "supplier_name": names[sid]})


def monthly(s: Sim, rep_ids: list[int], emp: dict[int, Any], target: dict[Any, Any], active: Any) -> None:
    t = s.t
    agg = defaultdict(lambda: defaultdict(float))
    for r in t["DWS_ORG_PERFORMANCE_DAILY"]:
        k = (r["dim_emp_id"], r["date"][:7])
        agg[k]["q"] += r["sales_quantity"]
        agg[k]["a"] += r["sales_amount"]
        agg[k]["gpa"] += r["gross_profit_actual"]
        agg[k]["gps"] += r["gross_profit_standard"]
        agg[k]["ar"] = r["accounts_receivable"]                        # 月末(最后一天)余额
    for r in t["DWS_IN_SALES_DAILY"]:
        k = (r["dim_emp_id"], r["date"][:7])
        agg[k]["orders"] += r["order_count"]
        agg[k]["undelivered"] = r["undelivered_order_count"]
    for r in t["DWS_AFTER_SALES_DAILY"]:
        k = (r["dim_emp_id"], r["date"][:7])
        agg[k]["ret_n"] += r["return_count"]
        agg[k]["ret_q"] += r["return_quantity"]
    for r in t["DWS_ORG_EXPENSE_DAILY"]:
        agg[(r["dim_emp_id"], r["date"][:7])]["exp"] += r["sales_expense"]
    for r in t["DWS_PRE_SALES_DAILY"]:
        k = (r["dim_emp_id"], r["date"][:7])
        agg[k]["opp_n"] += r["opp_count"]
        agg[k]["opp_q"] += r["opp_estimated_quantity"]
        agg[k]["smp_n"] += r["sample_count"]
        agg[k]["smp_q"] += r["sample_quantity"]
    orders = t["fact_sales_order"]
    for dl in t["fact_delivery"]:
        o = orders[dl["order_id"] - 1]
        agg[(o["emp_id"], dl["delivery_date"][:7])]["deliv"] += dl["quantity"]
    for a in t["fact_ar_aging"]:
        for p in a["_payments"]:
            agg[(a["emp_id"], p["date"].isoformat()[:7])]["paid"] += p["amount"]
    for op in t["fact_opportunity"]:
        if op["status"] == "won" and op["close_date"]:
            agg[(op["emp_id"], op["close_date"][:7])]["won"] += 1
    for sm in t["fact_sample"]:
        k = (sm["emp_id"], sm["sample_date"][:7])
        agg[k]["fb"] += sm["is_feedback"]
        agg[k]["succ"] += sm["is_success"]
    for e in rep_ids:
        for (y, mth) in sorted({(d.year, d.month) for d in M.days()}):
            per = f"{y}-{mth:02d}"
            if not any(active(e, d) for d in M.days() if (d.year, d.month) == (y, mth)):
                continue
            a = agg[(e, per)]
            tg = target.get((e, y, mth))
            t["agg_metric_monthly"].append({"row_id": len(t["agg_metric_monthly"]) + 1, "emp_id": e, "year": y, "month": mth,
                                            "sales_quantity": round(a["q"], 2), "sales_amount": round(a["a"], 2),
                                            "gross_profit_actual": round(a["gpa"], 2), "gross_profit_standard": round(a["gps"], 2),
                                            "delivery_quantity": round(a["deliv"], 2), "expense_total": round(a["exp"], 2),
                                            "payment_total": round(a["paid"], 2), "ar_balance": round(a["ar"], 2), "opp_count": int(a["opp_n"]),
                                            "opp_quantity": round(a["opp_q"], 2), "opp_won_count": int(a["won"]), "sample_count": int(a["smp_n"]),
                                            "sample_quantity": round(a["smp_q"], 2), "sample_success_count": int(a["succ"]),
                                            "sample_feedback_count": int(a["fb"]), "order_count": int(a["orders"]),
                                            "undelivered_order_count": int(a["undelivered"]), "return_count": int(a["ret_n"]),
                                            "return_quantity": round(a["ret_q"], 4),
                                            "quantity_target": tg["sales_quantity_target"] if tg else None,
                                            "profit_target": tg["profit_target"] if tg else None, "emp_name": emp[e]["emp_name"]})
