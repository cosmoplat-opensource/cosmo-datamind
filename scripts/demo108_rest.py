#!/usr/bin/env python3
"""Tables derived from the simulated events of the 108-table demo database (DR-076).

Runtime, energy, sensor data, maintenance plans, assets and depreciation, budgets and
expenses, targets and plans, quotations, contracts, supplier evaluation, safety and
environment, and the period-end state of customers, credit, inventory, spare parts and tools.
Every value is computed from the simulation or from master data, never sampled per table.
Standard library only.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import demo108_master as M
from demo108_sim import Sim, dt, fmt, is_workday


def _d(x: str) -> date:
    return date.fromisoformat(x[:10])


def _months() -> list[tuple[int, int]]:
    out, d = [], M.START
    while d <= M.END:
        if (d.year, d.month) not in out:
            out.append((d.year, d.month))
        d += timedelta(days=1)
    return out


def _overlap(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> float:
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds() / 3600.0)


def finalize(s: Sim) -> None:
    m, rng, t = s.m, s.rng, s.t
    prod = s.prod
    # ───────── 销售订单的成本、毛利与状态 ─────────
    cost_by_month = defaultdict(list)
    for c in t["fact_product_cost"]:
        cost_by_month[(c["product_id"], c["period"])].append((c["output_qty"], c["unit_cost"]))
    for o in t["fact_sales_order"]:
        pid = o["prod_id"]
        q = o["quantity"]
        if o.get("_delivered"):
            unit_actual = o["_cost"] / o["_delivered"]                    # 实际发出批次的成本
        else:
            rows = cost_by_month.get((pid, o["order_date"][:7])) or [(1, prod[pid]["standard_cost"])]
            unit_actual = sum(a * b for a, b in rows) / sum(a for a, _ in rows)
        o["tech_cost_amount"] = round(q * prod[pid]["tech_cost"], 2)
        o["standard_cost_amount"] = round(q * prod[pid]["standard_cost"], 2)
        o["actual_cost_amount"] = round(q * unit_actual, 2)
        o["gross_profit_actual"] = round(o["amount"] - o["actual_cost_amount"], 2)
        o["gross_profit_standard"] = round(o["amount"] - o["standard_cost_amount"], 2)
        o["is_negative_margin"] = int(o["gross_profit_actual"] < 0)
        if o["_returned"] >= o["_delivered"] - 1e-6 and o["_delivered"] > 0 and o["status"] == "delivered":
            o["status"] = "returned"

    # ───────── 客户期末状态与信用 ─────────
    by_cust = defaultdict(list)
    for o in t["fact_sales_order"]:
        by_cust[o["cust_id"]].append(o["order_date"])
    ar_open = [a for a in t["fact_ar_aging"] if not a["_credit_note"]]

    def balance(a: dict[str, Any], d: date) -> float:
        if _d(t["fact_invoice"][a["invoice_id"] - 1]["invoice_date"]) > d:
            return 0.0
        paid = sum(p["amount"] for p in a["_payments"] if p["date"] <= d) + sum(x for w, x in a["_credits"] if w <= d)
        return max(0.0, a["_total"] - paid)
    month_ends = [date(y, mm, 1) + timedelta(days=31) for y, mm in _months()]
    month_ends = [date(x.year, x.month, 1) - timedelta(days=1) for x in month_ends][-6:]
    peak = defaultdict(float)
    for me in month_ends:
        cust_tot: dict[int, float] = defaultdict(float)
        for a in ar_open:
            cust_tot[a["cust_id"]] += balance(a, me)
        for cid, v in cust_tot.items():
            peak[cid] = max(peak[cid], v)
    delay_w, amt_w, over_now = defaultdict(float), defaultdict(float), defaultdict(int)
    for a in ar_open:
        cid = a["cust_id"]
        for p in a["_payments"]:
            delay_w[cid] += max(0, (p["date"] - a["_due"]).days) * p["amount"]
            amt_w[cid] += p["amount"]
        if a["balance_amount"] > 0.005:
            over_now[cid] = max(over_now[cid], (M.END - a["_due"]).days)
    vol12 = defaultdict(float)
    for o in t["fact_sales_order"]:
        if _d(o["order_date"]) > M.END - timedelta(days=365):
            vol12[o["cust_id"]] += o["quantity"]
    credit_rows = []
    for c in t["dim_customer"]:
        cid = c["cust_id"]
        dates = sorted(by_cust[cid])
        hist = s.cust[cid]["life"] != "new"                       # 老客户的首次成交早于系统上线
        c["first_deal_date"] = (date(2013 + (cid * 5) % 10, 1 + (cid * 7) % 12, 1 + (cid * 11) % 27).isoformat() if hist
                                else (dates[0] if dates else None))
        c["last_deal_date"] = dates[-1] if dates else None
        gap = (M.END - _d(dates[-1])).days if dates else 999
        c["status"] = "risk" if over_now[cid] > 60 else "lost" if gap > 180 else "dormant" if gap > 90 else "active"
        c["credit_status"] = "frozen" if cid in s.frozen else "normal"
        c["market_share"] = round(min(60.0, vol12[cid] / c["market_capacity"] * 100), 2)
        used = round(sum(a["balance_amount"] for a in ar_open if a["cust_id"] == cid), 2)
        limit = max(500000.0, round(peak[cid] * 1.3, -4))        # 授信额度按近半年月末应收峰值的 1.3 倍核定
        avg_delay = delay_w[cid] / amt_w[cid] if amt_w[cid] else 0.0
        score = max(35, min(98, int(round(96 - avg_delay * 1.2 - over_now[cid] * 0.3 - (25 if c["credit_status"] == "frozen" else 0)))))
        credit_rows.append({"credit_id": cid, "cust_id": cid, "credit_limit": limit, "used_credit": used,
                            "credit_score": score, "credit_level": "AA" if score >= 90 else "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D",
                            "last_review_date": date(2026, 4, 1).isoformat(), "days_overdue_max": max(0, over_now[cid])})
    t["dim_customer_credit"] = credit_rows

    # ───────── 期末成品库存 ─────────
    for pid, batches in s.fg.items():
        for b in batches:
            if b["qty"] > 0.005:
                t["fact_inventory"].append({"inventory_id": len(t["fact_inventory"]) + 1, "prod_id": pid, "batch_no": b["batch"],
                                            "warehouse_date": b["made"].isoformat(), "quantity": round(b["qty"], 4),
                                            "unit_cost": round(b["unit_cost"], 2), "total_cost": round(b["qty"] * round(b["unit_cost"], 2), 2),
                                            "storage_days": (M.END - b["made"]).days})
    # 备件期末库存:按维修开工时间先后回放领用,低于下限即补货到上限(与 200 表备件领用台账同一顺序)
    parts = {p["part_name"]: p for p in m.get("dim_spare_part")}
    spare_end = {p["part_id"]: p["max_stock"] for p in parts.values()}
    wo_start = {w["wo_id"]: w["start_time"] or "" for w in t["fact_maintenance_wo"]}
    for r in sorted(t["fact_maintenance_record"], key=lambda r: (wo_start[r["wo_id"]], r["record_id"])):
        for item in (r["parts_replaced"] or "").split("、"):
            if not item:
                continue
            name, n = item.rsplit("×", 1)
            p = parts[name]
            if spare_end[p["part_id"]] - int(n) < p["min_stock"]:
                spare_end[p["part_id"]] = p["max_stock"]
            spare_end[p["part_id"]] -= int(n)
    for p in m.get("dim_spare_part"):
        p["current_stock"] = spare_end[p["part_id"]]
    for tool in m.get("dim_tool"):
        tool["used_life"] = s.tool_use[tool["tool_id"]] * {"螺纹元件组": 30, "切粒刀组": 6, "过滤网组": 1}[tool["tool_type"]]
        tool["status"] = "scrapped" if tool["used_life"] >= tool["total_life"] else "in_use"
        if tool["status"] == "scrapped":                     # 到寿即更换,换上的新件从零计
            tool["used_life"] = tool["used_life"] % tool["total_life"]
            tool["status"] = "in_use"

    # ───────── 维护计划(工单按计划派生) ─────────
    plans = {}
    for e in m.get("dim_equipment"):
        kinds = [("月度检修", 30, 600)] + ([("季度大修", 90, 1800)] if e["type_id"] == 1 else [])
        for kind, days, hours in kinds:
            pid = len(t["fact_maintenance_plan"]) + 1
            plans[(e["equipment_id"], kind)] = pid
            t["fact_maintenance_plan"].append({"plan_id": pid, "plan_no": f"MP{e['equipment_id']:03d}{'M' if days == 30 else 'Q'}",
                                               "equipment_id": e["equipment_id"], "maint_type": kind, "interval_days": days,
                                               "interval_hours": hours, "next_date": None,
                                               "description": f"{e['equipment_name']}{kind}:清洁、紧固、润滑、易损件检查" + ("、螺纹元件与机筒磨损测量" if days == 90 else "")})
    last_done = {}
    for wo in t["fact_maintenance_wo"]:
        key = (wo["equipment_id"], wo["maint_type"])
        if key in plans:
            wo["plan_id"] = plans[key]
            if wo["status"] == "completed":
                last_done[plans[key]] = max(last_done.get(plans[key], M.START), _d(wo["end_time"]))
    for p in t["fact_maintenance_plan"]:
        base = last_done.get(p["plan_id"], M.START)
        nxt = base + timedelta(days=p["interval_days"])
        while nxt <= M.END:
            nxt += timedelta(days=p["interval_days"])
        p["next_date"] = nxt.isoformat()

    # ───────── 运行台时、OEE 与能耗(产线 × 日) ─────────
    orders = [o for o in t["fact_production_order"] if o.get("_start")]
    downs = defaultdict(list)
    for dn in t["fact_downtime"]:
        downs[dn["line_id"]].append(dn)
    t["_line_day"] = {}
    for lid, _line in s.lines.items():
        lo = [o for o in orders if o["line_id"] == lid]
        for d in M.days():
            d0, d1 = dt(d), dt(d) + timedelta(days=1)
            window, qty, perf_q, qual_q = 0.0, 0.0, 0.0, 0.0
            win_p: dict[int, float] = defaultdict(float)
            qty_p: dict[int, float] = defaultdict(float)
            for o in lo:
                if o["_end"] <= d0 or o["_start"] >= d1:
                    continue
                span = (o["_end"] - o["_start"]).total_seconds() / 3600
                inside = _overlap(o["_start"], o["_end"], d0, d1)
                share = inside / span if span else 0.0
                window += inside
                win_p[o["prod_id"]] += inside
                qty_p[o["prod_id"]] += o["_actual"] * share
                qty += o["_actual"] * share
                perf_q += o["_perf"] * inside
                qual_q += (0.99 if o.get("_ok", True) else 0.90) * inside     # 不合格批次需返工,计入质量损失
            unplanned, changeover, pm, reasons = 0.0, 0.0, 0.0, defaultdict(float)
            for dn in downs[lid]:
                h = _overlap(datetime.fromisoformat(dn["start_time"]), datetime.fromisoformat(dn["end_time"]), d0, d1)
                if h <= 0:
                    continue
                if dn["reason_code"] == 3:
                    pm += h
                    continue
                if dn["reason_code"] == 2:
                    changeover += h
                else:
                    unplanned += h
                reasons[dn["reason_code"]] += h
            run = max(0.0, window - unplanned)                  # 工单窗口内扣除故障等非计划停机即运行时长(停机区间互不重叠)
            down = unplanned + changeover                       # 停机时长含换产,不含计划保养(计划停机不计入负荷时间)
            if run + down + pm <= 0.01:
                t["_line_day"][(lid, d)] = {"run": 0.0, "qty": 0.0, "win_p": {}, "qty_p": {}}
                continue
            plan_raw = run + down
            avail = run / plan_raw if plan_raw else 0.0
            perf = perf_q / window if window else 0.0
            quality = qual_q / window if window else 0.0
            reason = max(reasons, key=lambda k: reasons[k]) if reasons else None
            t["fact_equipment_runtime"].append({"runtime_id": len(t["fact_equipment_runtime"]) + 1, "line_id": lid, "record_date": d.isoformat(),
                                                "plan_run_hours": round(plan_raw, 2), "actual_run_hours": round(run, 2), "downtime_hours": round(down, 2),
                                                "downtime_reason": next(r[2] for r in M.DOWNTIME_REASONS if r[0] == reason) if reason else None,
                                                "oee": round(avail * perf * quality * 100, 2)})
            t["_line_day"][(lid, d)] = {"run": run, "qty": qty, "down": down, "planned_down": pm, "oee": avail * perf * quality,
                                        "plan_hours": round(plan_raw, 2), "avail": avail, "perf": perf, "quality": quality,
                                        "win_p": dict(win_p), "qty_p": dict(qty_p)}
    sec = {1: 360, 2: 520, 3: 430, 4: 610}                        # 吨耗电(kWh/t),按产品线
    for lid, line in s.lines.items():
        pl = s.prod[s.line_products[lid][0]]["prod_line_id"]
        ws = line["workshop_id"]
        for d in M.days():
            ld = t["_line_day"][(lid, d)]
            idle_hours = 24.0 - ld["run"]
            kwh = ld["qty"] * sec[pl] * rng.uniform(0.95, 1.06) + idle_hours * 18.0 + (ld.get("down", 0) + ld.get("planned_down", 0)) * 25.0
            water = ld["qty"] * rng.uniform(0.7, 0.9) + 2.0
            gas = (ld["qty"] * rng.uniform(4.0, 6.0) if ws in (2, 3, 5) else 0.0)   # 除湿干燥用天然气
            steam = round(rng.uniform(2.0, 4.0), 2) if d.month in (11, 12, 1, 2, 3) else 0.0    # 冬季车间采暖
            t["fact_energy_consumption"].append({"energy_id": len(t["fact_energy_consumption"]) + 1, "line_id": lid, "workshop_id": ws,
                                                 "record_date": d.isoformat(), "electricity_kwh": round(kwh, 2), "water_tons": round(water, 2),
                                                 "gas_m3": round(gas, 2), "steam_tons": steam,
                                                 "unit_energy_consumption": round(kwh / ld["qty"], 2) if ld["qty"] >= 0.5 else None})
            ld["kwh"] = kwh

    # ───────── 传感器数据(运行日采样)与报警 ─────────
    running_days = defaultdict(list)
    for (lid, d), ld in t["_line_day"].items():
        if ld["run"] >= 4:
            running_days[lid].append(d)
    for sn in m.get("dim_sensor"):
        eq = next(e for e in m.get("dim_equipment") if e["equipment_id"] == sn["equipment_id"])
        days_ = running_days[eq["line_id"]]
        every = 1 if eq["type_id"] == 1 else 7
        for d in days_[::every]:
            when = dt(d, rng.uniform(1, 23))
            lo, hi, alarm = sn["_lo"], sn["_hi"], sn["_alarm"]
            v = rng.gauss((lo + hi) / 2, (hi - lo) / 6)
            anomaly = rng.random() < 0.004
            if anomaly:
                v = alarm + abs(rng.gauss(0, (hi - lo) * 0.05))
            flag = "good" if rng.random() > 0.01 else "uncertain"
            t["fact_iot_data"].append({"data_id": len(t["fact_iot_data"]) + 1, "sensor_id": sn["sensor_id"], "timestamp": fmt(when),
                                       "value": round(v, 3), "unit": sn["unit"], "quality_flag": flag})
            if anomaly:
                s.add_alarm(sn, eq["equipment_id"], when, critical=False)
    t["fact_alarm"].sort(key=lambda a: a["alarm_time"])
    for i, a in enumerate(t["fact_alarm"], start=1):
        a["alarm_id"] = i

    # ───────── 固定资产与折旧 ─────────
    value_by_type = {1: 6_500_000, 2: 520_000, 3: 380_000, 4: 560_000, 5: 450_000, 6: 420_000}
    cc_of_ws = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
    for e in m.get("dim_equipment"):
        line = s.lines[e["line_id"]]
        purchase = _d(e["install_date"])
        orig = round(value_by_type[e["type_id"]] * (max(0.6, line["capacity_tph"]) / 1.2 if e["type_id"] == 1 else 1.0) * rng.uniform(0.9, 1.1), -2)
        life = e["design_life_years"]                            # 折旧年限取设备设计寿命(挤出机 15 年)
        monthly = round(orig * 0.95 / (life * 12), 2)
        months_used = (M.END.year - purchase.year) * 12 + (M.END.month - purchase.month)
        acc = round(min(orig * 0.95, monthly * months_used), 2)
        aid = e["equipment_id"]
        t["dim_fixed_asset"].append({"asset_id": aid, "asset_code": f"FA{e['equipment_code'][2:]}", "asset_name": e["equipment_name"],
                                     "equipment_id": e["equipment_id"], "asset_type": "production_equipment", "original_value": orig,
                                     "useful_life_years": life, "monthly_depreciation": monthly, "accumulated_dep": acc,
                                     "net_book_value": round(orig - acc, 2), "purchase_date": purchase.isoformat(),
                                     "cost_center_id": cc_of_ws[line["workshop_id"]]})
        for y, mth in _months():
            used = (y - purchase.year) * 12 + (mth - purchase.month)
            if used <= 0:
                continue
            acc_m = round(min(orig * 0.95, monthly * used), 2)
            prev = round(min(orig * 0.95, monthly * (used - 1)), 2)
            dep = round(acc_m - prev, 2)
            if dep <= 0:
                continue
            t["fact_depreciation"].append({"dep_id": len(t["fact_depreciation"]) + 1, "asset_id": aid, "period": f"{y}-{mth:02d}",
                                           "dep_amount": dep, "accumulated_dep": acc_m, "net_book_value": round(orig - acc_m, 2)})

    # 厂房、仓库与公用工程(没有对应设备):按工厂建成日入账,厂房 20 年、公用工程 10 年
    first_ws = {1: 1, 2: 3, 3: 5}
    for fid, fname, _bu, _loc, area, est in M.FACTORIES:
        for name, kind, value, life in ((f"{fname}主厂房", "building", area * 1500.0, 20), (f"{fname}仓库", "building", area * 400.0, 20),
                                        (f"{fname}公用工程(配电、空压、冷却水)", "utility", area * 250.0, 10)):
            aid = len(t["dim_fixed_asset"]) + 1
            orig = round(value, -3)
            monthly = round(orig * 0.95 / (life * 12), 2)
            purchase = est
            used = (M.END.year - purchase.year) * 12 + (M.END.month - purchase.month)
            acc = round(min(orig * 0.95, monthly * used), 2)
            t["dim_fixed_asset"].append({"asset_id": aid, "asset_code": f"FA9{aid:03d}", "asset_name": name, "equipment_id": None,
                                         "asset_type": kind, "original_value": orig, "useful_life_years": life, "monthly_depreciation": monthly,
                                         "accumulated_dep": acc, "net_book_value": round(orig - acc, 2), "purchase_date": purchase.isoformat(),
                                         "cost_center_id": cc_of_ws[first_ws[fid]]})
            for y, mth in _months():
                u = (y - purchase.year) * 12 + (mth - purchase.month)
                acc_m = round(min(orig * 0.95, monthly * u), 2)
                prev = round(min(orig * 0.95, monthly * (u - 1)), 2)
                dep = round(acc_m - prev, 2)
                if dep > 0:
                    t["fact_depreciation"].append({"dep_id": len(t["fact_depreciation"]) + 1, "asset_id": aid, "period": f"{y}-{mth:02d}",
                                                   "dep_amount": dep, "accumulated_dep": acc_m, "net_book_value": round(orig - acc_m, 2)})

    # ───────── 费用、预算 ─────────
    reps = [e for e in m.get("dim_employee") if s.roles[e["emp_id"]] in ("销售代表", "销售经理")]
    for e in reps:
        hire = _d(e["hire_date"])
        leave = s.rep_transfer["date"] if e["emp_id"] == s.rep_transfer["emp_id"] else None
        for d in M.days():
            if d < hire or (leave and d > leave) or not is_workday(d):
                continue
            if rng.random() < 0.22:
                kind = rng.choices(["差旅费", "业务招待费", "小车费", "通讯费", "办公费"], weights=[40, 25, 20, 8, 7])[0]
                amt = {"差旅费": rng.uniform(600, 4500), "业务招待费": rng.uniform(300, 3000), "小车费": rng.uniform(80, 600),
                       "通讯费": rng.uniform(100, 300), "办公费": rng.uniform(50, 500)}[kind]
                t["fact_expense"].append({"expense_id": len(t["fact_expense"]) + 1, "emp_id": e["emp_id"], "dept_id": e["dept_id"],
                                          "expense_date": d.isoformat(), "expense_type": kind, "amount": round(amt, 2)})
    # 与业务量挂钩的销售费用:按业务员当月发货吨数计运输费、按当月开票额计渠道佣金,按月末报销;区域市场推广按季度
    deliv_by = defaultdict(float)
    inv_by = defaultdict(float)
    for dl in t["fact_delivery"]:
        o = t["fact_sales_order"][dl["order_id"] - 1]
        deliv_by[(o["emp_id"], dl["delivery_date"][:7])] += dl["quantity"]
    order_of_dl = {dl["delivery_id"]: dl["order_id"] for dl in t["fact_delivery"]}
    for inv in t["fact_invoice"]:
        o = t["fact_sales_order"][order_of_dl[inv["delivery_id"]] - 1]
        inv_by[(o["emp_id"], inv["invoice_date"][:7])] += inv["amount_no_tax"]
    for e in reps:
        for y, mth in _months():
            last = date(y + mth // 12, mth % 12 + 1, 1) - timedelta(days=1)
            day_ = min(last, M.END)
            while not is_workday(day_):
                day_ -= timedelta(days=1)
            for kind, amt in (("运输费", deliv_by[(e["emp_id"], f"{y}-{mth:02d}")] * rng.uniform(140, 175)),
                              ("渠道佣金", max(0.0, inv_by[(e["emp_id"], f"{y}-{mth:02d}")]) * 0.005)):
                if amt >= 1:
                    t["fact_expense"].append({"expense_id": len(t["fact_expense"]) + 1, "emp_id": e["emp_id"], "dept_id": e["dept_id"],
                                              "expense_date": day_.isoformat(), "expense_type": kind, "amount": round(amt, 2)})
            if mth % 3 == 0 and s.roles[e["emp_id"]] == "销售经理":
                t["fact_expense"].append({"expense_id": len(t["fact_expense"]) + 1, "emp_id": e["emp_id"], "dept_id": e["dept_id"],
                                          "expense_date": day_.isoformat(), "expense_type": "市场推广费", "amount": round(rng.uniform(60000, 180000), 2)})
    t["fact_expense"].sort(key=lambda x: (x["expense_date"], x["emp_id"]))
    for i, x in enumerate(t["fact_expense"], start=1):
        x["expense_id"] = i
    exp_by = defaultdict(float)
    for x in t["fact_expense"]:
        exp_by[(x["dept_id"], x["expense_date"][:7])] += x["amount"]
    for dept in range(1, 7):
        for y, mth in _months():
            actual = exp_by[(dept, f"{y}-{mth:02d}")]
            t["fact_budget_expense"].append({"budget_id": len(t["fact_budget_expense"]) + 1, "dept_id": dept, "year": y, "month": mth,
                                             "budget_amount": round(max(20000.0, actual * rng.uniform(0.95, 1.15)), -2)})
    conv_by = defaultdict(float)
    cc_of_line = {lid: cc_of_ws[l["workshop_id"]] for lid, l in s.lines.items()}
    batch_line = {o["batch_no"]: o["line_id"] for o in t["fact_production_order"]}
    for c in t["fact_product_cost"]:
        conv_by[(cc_of_line[batch_line[c["batch_no"]]], c["period"])] += c["labor_cost"] + c["overhead_cost"]
    maint_by = defaultdict(float)
    for r in t["fact_maintenance_record"]:
        maint_by[r["maint_date"][:7]] += r["cost"]
    sales_cc = {8: (1, 2, 4), 9: (3, 5, 6)}                      # 南方:华东/华南/西南;北方:华北/华中/西北
    for cc in m.get("dim_cost_center"):
        for y, mth in _months():
            per = f"{y}-{mth:02d}"
            if cc["cc_type"] == "production":
                actual = conv_by[(cc["cc_id"], per)]
            elif cc["cc_type"] == "sales":
                actual = sum(exp_by[(dpt, per)] for dpt in sales_cc[cc["cc_id"]])
            elif cc["cc_type"] == "equipment":
                actual = maint_by[per]
            else:
                actual = rng.uniform(80000, 160000) if cc["cc_type"] == "rd" else rng.uniform(60000, 110000)
            t["fact_budget"].append({"budget_id": len(t["fact_budget"]) + 1, "budget_no": f"BG{y}{mth:02d}{cc['cc_id']:02d}", "year": y,
                                     "month": mth, "cc_id": cc["cc_id"], "budget_amount": round(max(10000.0, actual) * rng.uniform(0.96, 1.12), -2),
                                     "actual_amount": round(actual, 2), "budget_type": "expense"})

    # ───────── 销售目标、计划、回款目标与生产计划 ─────────
    sold = defaultdict(float)
    gp = defaultdict(float)
    amt = defaultdict(float)
    paid = defaultdict(float)
    for o in t["fact_sales_order"]:
        k = (o["emp_id"], o["order_date"][:7])
        sold[k] += o["quantity"]
        gp[k] += o["gross_profit_actual"]
        amt[k] += o["amount"]
    for a in t["fact_ar_aging"]:
        for p in a["_payments"]:
            paid[(a["emp_id"], p["date"].isoformat()[:7])] += p["amount"]
    for e in reps:
        hire = _d(e["hire_date"])
        leave = s.rep_transfer["date"] if e["emp_id"] == s.rep_transfer["emp_id"] else None
        hist = [sold[(e["emp_id"], f"{y}-{mm:02d}")] for y, mm in _months()]
        if not any(hist):                                         # 不直接带客户的销售经理不下达个人销量目标
            continue
        base = sorted(hist)[len(hist) // 2] or 60.0
        for i, (y, mth) in enumerate(_months()):
            if date(y, mth, 28) < hire or (leave and date(y, mth, 1) > leave):
                continue
            prev = hist[max(0, i - 3): i] or [base]
            q_t = round(max(20.0, sum(prev) / len(prev)) * 1.08, 1)
            margin = (sum(gp[(e["emp_id"], p)] for p in [f"{y}-{mth:02d}"]) / max(1.0, sold[(e["emp_id"], f"{y}-{mth:02d}")])) or 2200.0
            price = (amt[(e["emp_id"], f"{y}-{mth:02d}")] / max(1.0, sold[(e["emp_id"], f"{y}-{mth:02d}")])) or 15000.0
            t["fact_sales_target"].append({"target_id": len(t["fact_sales_target"]) + 1, "emp_id": e["emp_id"], "year": y, "month": mth,
                                           "sales_quantity_target": q_t, "profit_target": round(q_t * max(1500.0, margin) * 0.98, -2),
                                           "payment_target": round(q_t * price * 1.13 * 0.95, -2), "sales_amount_target": round(q_t * price, -2)})
            t["fact_sales_plan"].append({"plan_id": len(t["fact_sales_plan"]) + 1, "emp_id": e["emp_id"], "year": y, "month": mth,
                                         "planned_quantity": round(q_t * rng.uniform(0.94, 1.0), 1)})
    for e in reps:
        for y in (2025, 2026):
            t["fact_payment_target"].append({"target_id": len(t["fact_payment_target"]) + 1, "emp_id": e["emp_id"], "year": y,
                                             "turnover_rate_target": round(rng.uniform(5.0, 7.5), 2)})
    released = defaultdict(float)                                 # 月度生产计划 = 排程在当月完工的工单计划量(取整)
    for o in t["fact_production_order"]:
        if o.get("_end"):
            released[(o["line_id"], o["_end"].strftime("%Y-%m"))] += o["planned_quantity"]
    made = defaultdict(float)
    for o in t["fact_production_order"]:
        if o["status"] == "已完成":
            made[(o["line_id"], o["end_time"][:7])] += o["actual_quantity"]
    for lid, line in s.lines.items():
        for y, mth in _months():
            per = f"{y}-{mth:02d}"
            days_in = sum(1 for d in M.days() if (d.year, d.month) == (y, mth))
            planned = round(released[(lid, per)] * rng.uniform(0.97, 1.04), -1) or round(line["capacity_tph"] * 24 * days_in * 0.5, -1)
            actual = round(made[(lid, per)], 2)
            t["fact_production_plan"].append({"plan_id": len(t["fact_production_plan"]) + 1, "line_id": lid, "year": y, "month": mth,
                                              "planned_quantity": planned, "actual_quantity": actual,
                                              "plan_achievement": round(actual / planned * 100, 2) if planned else None})

    # ───────── 报价、合同、供应商评估 ─────────
    for o in t["fact_sales_order"]:
        if rng.random() < 0.08:
            qd = _d(o["order_date"]) - timedelta(days=rng.randint(3, 15))
            if qd < M.START:
                continue
            t["fact_quotation"].append({"quotation_id": len(t["fact_quotation"]) + 1, "quotation_no": f"QT{qd.strftime('%Y%m%d')}{len(t['fact_quotation']) + 1:04d}",
                                        "cust_id": o["cust_id"], "emp_id": o["emp_id"], "quotation_date": qd.isoformat(),
                                        "valid_until": (qd + timedelta(days=30)).isoformat(), "product_id": o["prod_id"], "quantity": o["quantity"],
                                        "unit_price": round(o["unit_price"] * rng.uniform(1.0, 1.04), 2),
                                        "total_amount": 0.0, "status": "won"})
    for _ in range(len(t["fact_quotation"]) // 3):
        cid = rng.randint(1, len(t["dim_customer"]))
        c = s.cust[cid]
        qd = M.START + timedelta(days=rng.randrange(M.DAYS))
        pid = rng.choice(c["products"])
        guide = prod[pid]["guide_price"]
        status = "lost" if qd < M.END - timedelta(days=30) else rng.choice(["sent", "draft"])
        t["fact_quotation"].append({"quotation_id": len(t["fact_quotation"]) + 1, "quotation_no": f"QT{qd.strftime('%Y%m%d')}{len(t['fact_quotation']) + 1:04d}",
                                    "cust_id": cid, "emp_id": s.rep_of(cid, qd), "quotation_date": qd.isoformat(),
                                    "valid_until": (qd + timedelta(days=30)).isoformat(), "product_id": pid,
                                    "quantity": round(c["avg_qty"] * rng.uniform(0.6, 2.0), 1), "unit_price": round(guide * rng.uniform(0.98, 1.08), 2),
                                    "total_amount": 0.0, "status": status})
    for q in t["fact_quotation"]:
        q["total_amount"] = round(q["quantity"] * q["unit_price"], 2)
    t["fact_quotation"].sort(key=lambda q: q["quotation_date"])
    for i, q in enumerate(t["fact_quotation"], start=1):
        q["quotation_id"] = i
    po_amt = defaultdict(float)
    for po in t["fact_purchase_order"]:
        po_amt[po["supplier_id"]] += po["total_amount"]
    # 框架合同在年初按预计用量签订,约定额与实际执行有偏差(按供应商、客户各不相同)
    for sid, *_rest in M.SUPPLIERS[:12]:
        if po_amt[sid] <= 0:
            continue
        t["fact_contract"].append({"contract_id": len(t["fact_contract"]) + 1, "contract_no": f"PC{2025}{sid:03d}", "contract_type": "purchase",
                                   "party_id": sid, "start_date": "2025-01-01", "end_date": "2026-12-31",
                                   "total_amount": round(po_amt[sid] / 18 * 24 * (0.85 + (sid * 37) % 40 / 100), -4), "status": "active"})
    top = sorted(s.cust, key=lambda c: -s.cust[c]["size"])[:8]
    sales_amt = defaultdict(float)
    for o in t["fact_sales_order"]:
        sales_amt[o["cust_id"]] += o["amount"]
    for cid in top:
        st = s.cust[cid]["start"]
        status = "active" if s.cust[cid]["life"] in ("active", "risk", "new") else "terminated"
        stop = s.cust[cid]["stop"]
        t["fact_contract"].append({"contract_id": len(t["fact_contract"]) + 1, "contract_no": f"SC2025{cid:03d}", "contract_type": "sales",
                                   "party_id": cid, "start_date": max(st, M.START).isoformat(),
                                   "end_date": (stop + timedelta(days=30)).isoformat() if status == "terminated" and stop else "2026-12-31",
                                   "total_amount": round(sales_amt[cid] / 18 * 24 * (0.85 + (cid * 37) % 40 / 100), -4), "status": status})
    grs = defaultdict(list)
    for g in t["fact_goods_receipt"]:
        grs[t["fact_purchase_order"][g["po_id"] - 1]["supplier_id"]].append(g)
    for sid, *_r in M.SUPPLIERS:
        for y, q in [(2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2)]:
            q0 = date(y, 3 * q - 2, 1)
            q1 = add_q(q0)
            if q1 > M.END + timedelta(days=1):
                continue
            gs = [g for g in grs[sid] if q0 <= _d(g["receipt_date"]) < q1]
            if not gs:
                continue
            ontime = sum(1 for g in gs if _d(g["receipt_date"]) <= _d(t["fact_purchase_order"][g["po_id"] - 1]["delivery_date"])) / len(gs)
            passr = sum(1 for g in gs if g["inspection_result"] == "pass") / len(gs)
            dsc, qsc, ssc = round(60 + 40 * ontime, 2), round(60 + 40 * passr, 2), round(rng.uniform(78, 95), 2)
            tot = round(0.4 * dsc + 0.4 * qsc + 0.2 * ssc, 2)
            t["fact_supplier_eval"].append({"eval_id": len(t["fact_supplier_eval"]) + 1, "supplier_id": sid, "eval_date": (q1 + timedelta(days=5)).isoformat(),
                                            "eval_period": f"{y}-Q{q}", "delivery_score": dsc, "quality_score": qsc, "service_score": ssc,
                                            "total_score": tot, "grade": "AA" if tot >= 92 else "A" if tot >= 85 else "B" if tot >= 75 else "C"})

    # ───────── 安全与环境 ─────────
    safety(s)


def add_q(d: date) -> date:
    return date(d.year + (d.month + 2) // 12, (d.month + 2) % 12 + 1, 1)


def safety(s: Sim) -> None:
    t, rng, m = s.t, s.rng, s.m
    safety_staff = s.role_ids["安全工程师"]
    leaders_by_line = {tm["line_id"]: tm["leader_id"] for tm in m.get("dim_team")}
    lines_by_ws = defaultdict(list)
    for l in m.get("dim_production_line"):
        lines_by_ws[l["workshop_id"]].append(l["line_id"])
    for y, issued in ((2025, date(2025, 1, 8)), (2026, date(2026, 1, 6))):
        pid = len(t["fact_safety_plan"]) + 1
        t["fact_safety_plan"].append({"plan_id": pid, "plan_no": f"SP{y}", "year": y, "plan_type": "annual",
                                      "status": "closed" if y == 2025 else "active", "issued_date": issued.isoformat(),
                                      "issued_by": s.emps[s.role_ids["总经理"][0]]["emp_name"]})
        for desc, val, unit in (("重大事故为零", 0, "起"), ("损工事件率不高于", 0.5, "‰"), ("员工安全培训覆盖率", 100, "%")):
            t["fact_safety_objective"].append({"obj_id": len(t["fact_safety_objective"]) + 1, "plan_id": pid, "obj_desc": desc,
                                               "target_value": val, "unit": unit, "deadline": f"{y}-12-31", "responsible_dept_id": 14})
    # 风险评估:每个危险源每半年一次;可能性 × 后果 → 等级
    L = ["rare", "unlikely", "possible", "likely", "almost_certain"]
    C = ["negligible", "minor", "moderate", "major", "catastrophic"]
    base_lv = {"low": (1, 1), "medium": (2, 2), "high": (2, 3)}
    for hz in m.get("dim_hazard"):
        for k in range(3):
            d = M.START + timedelta(days=180 * k + rng.randint(5, 40))
            li, co = base_lv[hz["risk_level"]]
            li = min(4, max(0, li + rng.choice([-1, 0, 0, 1])))
            co = min(4, max(0, co + rng.choice([0, 0, 1])))
            score = (li + 1) * (co + 1)
            lv = "L1" if score <= 4 else "L2" if score <= 9 else "L3" if score <= 15 else "L4"
            t["fact_risk_assessment"].append({"assessment_id": len(t["fact_risk_assessment"]) + 1, "hazard_id": hz["hazard_id"],
                                              "assessor_id": rng.choice(safety_staff), "assess_date": d.isoformat(), "likelihood": L[li],
                                              "consequence": C[co], "risk_level": lv, "control_needed": int(lv != "L1")})
    # 事故:多为未遂与急救;地点在车间的产线上;报告人为该产线班组长
    for _i in range(32):
        d = M.START + timedelta(days=rng.randrange(M.DAYS))
        ws = rng.randint(1, 5)
        line = rng.choice(lines_by_ws[ws])
        sev = rng.choices(["near_miss", "fa", "mtc", "lti"], weights=[55, 30, 11, 4])[0]
        kind = rng.choice(["灼烫", "机械伤害", "物体打击", "车辆伤害", "高处坠落", "触电"])
        inj = 0 if sev == "near_miss" else 1
        status = "closed" if d < M.END - timedelta(days=30) else ("investigating" if sev in ("mtc", "lti") else "reported")
        iid = len(t["fact_incident"]) + 1
        t["fact_incident"].append({"incident_id": iid, "incident_no": f"IN{d.strftime('%Y%m%d')}{iid:03d}", "incident_type": kind, "severity": sev,
                                   "incident_date": d.isoformat(), "incident_time": f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00",
                                   "workshop_id": ws, "line_id": line, "location_desc": f"{s.lines[line]['line_name']}{rng.choice(['挤出区', '切粒区', '配料区', '包装区'])}",
                                   "description": f"{kind}{'未遂事件' if sev == 'near_miss' else '事件'},已现场处置", "injury_count": inj, "fatality_count": 0,
                                   "property_loss": 0.0 if sev == "near_miss" else round(rng.uniform(2000, 40000), 2),   # 未遂事件没有损失
                                   "reported_by": leaders_by_line[line], "status": status})
        if sev in ("fa", "mtc", "lti") and d < M.END - timedelta(days=3):
            st = d + timedelta(days=rng.randint(0, 2))
            en = st + timedelta(days=rng.randint(3, 14))
            if en > M.END:
                continue
            t["fact_incident_inv"].append({"inv_id": len(t["fact_incident_inv"]) + 1, "incident_id": iid, "investigator_id": rng.choice(safety_staff),
                                           "start_date": st.isoformat(), "end_date": en.isoformat(), "method": rng.choice(["5Why", "鱼骨图", "FTA"]),
                                           "root_cause": rng.choice(["防护装置缺失", "作业规程执行不到位", "设备隐患未及时整改", "个人防护用品佩戴不规范"]),
                                           "contributory_factors": rng.choice(["夜班疲劳", "新员工培训不足", "现场照明不足", "交叉作业沟通不畅"])})
    # 安全审计:每月两次,轮流到各车间
    for y, mth in _months():
        for k in range(2):
            d = date(y, mth, 8 + 12 * k)
            if d > M.END:
                continue
            ws = (len(t["fact_safety_audit"]) % 5) + 1
            score = round(rng.uniform(82, 97), 1)
            t["fact_safety_audit"].append({"audit_id": len(t["fact_safety_audit"]) + 1, "audit_no": f"SA{d.strftime('%Y%m%d')}{ws}",
                                           "audit_type": "专项审计" if k else "月度安全审计", "workshop_id": ws, "auditor_id": rng.choice(safety_staff),
                                           "audit_date": d.isoformat(), "score": score, "finding_count": max(0, int((100 - score) / 3)),
                                           "status": "completed" if d < M.END - timedelta(days=7) else "in_progress"})
    # 培训:每月一至两次,参训人数不超过在职人数
    n_emp = len(m.get("dim_employee"))
    for y, mth in _months():
        for k in range(rng.randint(1, 2)):
            d = date(y, mth, rng.randint(5, 25))
            if d > M.END:
                continue
            kind = rng.choice(["岗位安全复训", "消防安全", "危险化学品", "机械安全", "应急演练", "职业健康"])
            n = rng.randint(12, min(45, n_emp))
            t["fact_safety_training"].append({"training_id": len(t["fact_safety_training"]) + 1, "training_no": f"TR{d.strftime('%Y%m')}{k + 1}",
                                              "training_type": kind, "trainer_id": rng.choice(safety_staff), "training_date": d.isoformat(),
                                              "duration_hours": rng.choice([1.5, 2.0, 3.0, 4.0]), "attendee_count": n, "topic": f"{kind}专题培训",
                                              "score_avg": round(rng.uniform(80, 95), 1)})
    # 新员工入职三级安全教育(厂级、车间级、班组级):每名观测期内入职的员工入职次日一场
    for e in m.get("dim_employee"):
        hd = _d(e["hire_date"])
        if hd < M.START:
            continue
        d = hd + timedelta(days=1)
        t["fact_safety_training"].append({"training_id": len(t["fact_safety_training"]) + 1, "training_no": f"TR{d.strftime('%Y%m')}N{e['emp_id']:03d}",
                                          "training_type": "新员工三级安全教育", "trainer_id": rng.choice(safety_staff), "training_date": d.isoformat(),
                                          "duration_hours": 24.0, "attendee_count": 1, "topic": "新员工三级安全教育(厂级、车间级、班组级)",
                                          "score_avg": round(rng.uniform(85, 95), 1)})
    # 作业许可:维修与保养中的动火、高处、受限空间等
    techs = s.role_ids["维修技师"] + s.role_ids["设备工程师"]
    hz_for = {"动火作业": "1", "高处作业": "7", "受限空间": "10", "临时用电": "8", "动土作业": "4"}
    for wo in [w for w in t["fact_maintenance_wo"] if w["start_time"]]:
        if rng.random() > 0.08:
            continue
        kind = rng.choice(list(hz_for))
        st = datetime.fromisoformat(wo["start_time"]) - timedelta(hours=1)
        en = st + timedelta(hours=wo["_hours"] + 2)
        status = "closed" if en.date() <= M.END else "approved"
        t["fact_work_permit"].append({"permit_id": len(t["fact_work_permit"]) + 1, "permit_no": f"PTW{st.strftime('%Y%m%d')}{len(t['fact_work_permit']) + 1:03d}",
                                      "permit_type": kind, "applicant_id": rng.choice(techs), "valid_from": fmt(st), "valid_to": fmt(en),
                                      "work_location": next(e["equipment_name"] for e in m.get("dim_equipment") if e["equipment_id"] == wo["equipment_id"]),
                                      "work_desc": f"{wo['maint_type']}期间{kind}", "hazard_ids": hz_for[kind],
                                      "ppe_required": {"动火作业": "防火服,护目镜,灭火器", "高处作业": "安全带,安全帽", "受限空间": "气体检测仪,呼吸器,安全绳",
                                                       "临时用电": "绝缘手套,绝缘鞋", "动土作业": "安全帽,反光背心"}[kind], "status": status})
    # 环境监测:每月一次五项指标(纳管排放与厂界噪声)
    limits = [("COD", "mg/L", 500.0, 180.0), ("氨氮", "mg/L", 45.0, 12.0), ("VOCs", "mg/m³", 60.0, 18.0), ("颗粒物", "mg/m³", 20.0, 6.0),
              ("噪声", "dB(A)", 65.0, 57.0)]
    for y, mth in _months():
        d = date(y, mth, 15)
        for name, unit, lim, typical in limits:
            v = round(typical * rng.uniform(0.75, 1.3) if rng.random() > 0.02 else lim * rng.uniform(1.02, 1.15), 2)
            t["fact_env_record"].append({"record_id": len(t["fact_env_record"]) + 1, "record_date": d.isoformat(),
                                         "record_type": "季度监测" if mth in (3, 6, 9, 12) else "月度监测", "emission_type": name,
                                         "measured_value": v, "unit": unit, "limit_value": lim, "is_compliant": int(v <= lim)})
