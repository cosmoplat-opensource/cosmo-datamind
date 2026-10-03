#!/usr/bin/env python3
"""Business-process simulation for the 108-table demo database (DR-076).

Every fact is produced by a simulated process instead of being sampled per table:
sales orders come from customers' demand; production orders come from backlog and stock
targets and are scheduled on capable lines (changeovers, breakdowns, planned maintenance);
materials are consumed per BOM from received lots; finished batches are inspected and
allocated FIFO to deliveries; deliveries are invoiced, collected (per customer payment
behaviour) and occasionally returned; purchases follow MRP (requisition → PO → receipt →
payable → payment).  Running state (stock, receivables, spare parts) is kept so that every
summary can be recomputed from the details.  Standard library only.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import demo108_master as M

H = timedelta(hours=1)


def dt(d: date, hour: float = 0.0) -> datetime:
    return datetime(d.year, d.month, d.day) + timedelta(hours=hour)


def fmt(x: datetime) -> str:
    return x.strftime("%Y-%m-%d %H:%M:%S")


def month_key(d: date) -> tuple[int, int]:
    return d.year, d.month


def add_months(d: date, n: int) -> date:
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, 28))


SEASON = {1: 0.95, 2: 0.62, 3: 1.02, 4: 1.0, 5: 1.0, 6: 1.04, 7: 0.96, 8: 0.97, 9: 1.06, 10: 0.92, 11: 1.1, 12: 1.12}
HOLIDAYS = {date(2025, 1, 1), *[date(2025, 1, 28) + timedelta(days=i) for i in range(8)], date(2025, 4, 4), date(2025, 5, 1),
            date(2025, 5, 2), date(2025, 5, 5), date(2025, 5, 31), date(2025, 6, 2), *[date(2025, 10, 1) + timedelta(days=i) for i in range(8)],
            date(2026, 1, 1), date(2026, 1, 2), *[date(2026, 2, 15) + timedelta(days=i) for i in range(9)], date(2026, 4, 6),
            date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5), date(2026, 6, 19)}
MAKEUP_WORKDAYS = {date(2025, 1, 26), date(2025, 2, 8), date(2025, 4, 27), date(2025, 9, 28), date(2025, 10, 11),
                   date(2026, 1, 4), date(2026, 2, 14), date(2026, 2, 28), date(2026, 5, 9)}


def month_end(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1) - timedelta(days=1)


def is_workday(d: date) -> bool:
    return d in MAKEUP_WORKDAYS or (d.weekday() < 5 and d not in HOLIDAYS)


class Sim:
    def __init__(self, m: M.Master, seed: int, volume_factor: float = 1.9):
        self.m, self.seed = m, seed
        self.rng = random.Random(f"{seed}:sim")
        self.vf = volume_factor
        self.t: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.prod = m.by("dim_product", "prod_id")
        self.mat = m.by("dim_material", "material_id")
        self.lines = m.by("dim_production_line", "line_id")
        self.emps = m.by("dim_employee", "emp_id")
        self.roles: dict[int, str] = m.roles
        self.recipes: dict[int, dict[int, float]] = m.recipes
        self.line_products: dict[int, list[int]] = m.line_products
        self.product_lines = defaultdict(list)
        for lid, ps in self.line_products.items():
            for p in ps:
                self.product_lines[p].append(lid)
        self.eq_by_line = defaultdict(list)
        for e in m.get("dim_equipment"):
            self.eq_by_line[e["line_id"]].append(e)
        self.sensors_by_eq = defaultdict(list)
        for s in m.get("dim_sensor"):
            self.sensors_by_eq[s["equipment_id"]].append(s)
        self.role_ids = defaultdict(list)
        for eid, role in self.roles.items():
            self.role_ids[role].append(eid)

    # ───────── 客户与业务员 ─────────
    def customers(self) -> None:
        rng = self.rng
        reps_by_region = defaultdict(list)
        for e in self.m.get("dim_employee"):
            if self.roles[e["emp_id"]] in ("销售代表", "销售经理") and e["dept_id"] <= 6:
                reps_by_region[e["dept_id"]].append(e["emp_id"])
        leaver = self.m.leaver
        self.cust = {}
        rows = []
        for i, (name, ind, prov, city, region, size, terms, delay, bank, life) in enumerate(M.CUSTOMERS, start=1):
            pool = [r for r in reps_by_region[region] if r != leaver["successor"]]
            rep = pool[(i - 1) % len(pool)]
            prefs = M.CUSTOMER_INDUSTRY_PRODUCTS[ind]
            k = rng.randint(2, min(4, len(prefs)))
            chosen = rng.sample(prefs, k)
            weights = [M.PRODUCT_WEIGHT[p] * rng.uniform(0.6, 1.6) for p in chosen]
            start = M.START if life != "new" else date(2026, 1, 10) + timedelta(days=rng.randrange(0, 40))
            stop = {"dormant": date(2026, 2, 10), "lost": date(2025, 9, 25)}.get(life)
            self.cust[i] = {"rep": rep, "region": region, "size": size * self.vf, "terms": terms, "delay": delay, "bank": bank,
                            "life": life, "start": start, "stop": stop, "products": chosen, "weights": weights,
                            "avg_qty": rng.choice([12, 15, 18, 22, 25, 30]) * (1.4 if size >= 200 else 1.0),
                            "discount": rng.uniform(0.93, 0.98) if size >= 200 else rng.uniform(0.97, 1.02),
                            "consignment": name.startswith("示范智家")}
            rows.append({"cust_id": i, "cust_name": name, "industry": ind, "province": prov, "city": city,
                         "market_capacity": round(size * self.vf * 12 / rng.uniform(0.12, 0.35), -1), "market_share": None,
                         "total_parts": rng.randint(800, 6000), "status": None, "first_deal_date": None, "last_deal_date": None,
                         "credit_status": "normal"})
        self.t["dim_customer"] = rows
        # 离职业务员的客户在离职次日转给接替者
        self.rep_transfer = leaver
        # 产能按市场需求规划:各车间预期负荷约七成,车间内各线保持原有产能比例
        exp = defaultdict(float)
        for c in self.cust.values():
            months = 18 if c["life"] in ("active", "risk") else 12 if c["life"] == "dormant" else 9 if c["life"] == "lost" else 5
            tot = sum(c["weights"])
            for p, w in zip(c["products"], c["weights"]):
                exp[M.PRODUCTS[p - 1][6][0]] += c["size"] * months * w / tot
        hours = 0.9 * 24 * M.DAYS
        for ws in range(1, 6):
            ls = [l for l in self.lines.values() if l["workshop_id"] == ws]
            need = exp[ws] / (0.70 * hours)
            base = sum(l["capacity_tph"] for l in ls)
            for l in ls:
                l["capacity_tph"] = round(max(0.3, round(l["capacity_tph"] / base * need / 0.05) * 0.05), 2)
        # 挤出机主机功率按规划产能配置:满产时整线负载率约八成,单耗与装机功率自洽
        sec = {1: 360, 2: 520, 3: 430, 4: 610}
        for e in self.m.get("dim_equipment"):
            if e["type_id"] == 1:
                l = self.lines[e["line_id"]]
                pl = M.PRODUCTS[self.m.line_products[e["line_id"]][0] - 1][2]
                e["power_kw"] = float(round(l["capacity_tph"] * sec[pl] * 0.85 / 5) * 5)

    def rep_of(self, cust_id: int, d: date) -> int:
        rep = self.cust[cust_id]["rep"]
        if rep == self.rep_transfer["emp_id"] and d > self.rep_transfer["date"]:
            return self.rep_transfer["successor"]
        return rep

    # ───────── 主流程 ─────────
    def run(self) -> None:
        self.customers()
        self.init_state()
        self.plan_opportunities()
        for d in M.days():
            self.day = d
            self.take_orders(d)
            if d.weekday() == 0 or d == M.START:
                self.mrp(d)
            self.receive_materials(d)
            self.plan_production(d)
            self.rush_check(d)
            self.receive_materials(d)
            self.run_production(d)
            self.deliver(d)
            self.invoice(d)
            self.process_returns(d)
            self.collect(d)
            self.pay_suppliers(d)
            self.credit_control(d)
        self.close_period()

    def init_state(self) -> None:
        rng = self.rng
        self.fg: dict[int, list[dict[str, Any]]] = defaultdict(list)       # 成品批次(FIFO)
        self.raw: dict[int, list[dict[str, Any]]] = defaultdict(list)      # 原料批次
        self.cust_ready = True
        rate = self.demand_rate(M.START)
        for mid, mrow in self.mat.items():
            use = sum(r * self.recipes[p].get(mid, 0.0) / 1000.0 for p, r in rate.items())
            qty = round(mrow["safety_stock"] + use * (mrow["lead_time_days"] + 21), 1)
            self.raw[mid].append({"lot": f"RM{mid:02d}-INIT", "qty": qty, "date": M.START, "price": mrow["unit_price"]})
        self.fg_moves: list[tuple[int, str, date, date, float]] = []   # (产品, 批次, 入库日, 发生日, 数量变动)
        # 成品不设期初库存:系统上线时成品库已盘空,首批订单等首批工单完工后发货(发货累计不超过完工累计)
        self.pending_returns: list[dict[str, Any]] = []
        self.tech_jobs: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
        self.pm_count: dict[int, int] = defaultdict(int)
        self.line_free = {lid: dt(M.START, 8) for lid in self.lines}
        self.mtbf = {}
        for lid in self.lines:
            ext = next(e for e in self.eq_by_line[lid] if e["type_id"] == 1)
            age = (M.START - date.fromisoformat(ext["install_date"])).days / 365
            self.mtbf[lid] = 90.0 * max(0.6, 1.45 - 0.07 * age)           # 新线约 120 小时,役龄 12 年约 75 小时
        self.line_last_product: dict[int, int | None] = {lid: None for lid in self.lines}
        self.next_pm = {lid: dt(M.START + timedelta(days=rng.randint(5, 25)), 8) for lid in self.lines}
        self.open_sales: list[dict[str, Any]] = []
        self.wip: dict[int, float] = defaultdict(float)
        self.pending_prod: list[dict[str, Any]] = []
        self.open_po_lines: list[dict[str, Any]] = []
        self.pending_invoices: list[dict[str, Any]] = []
        self.ar_items: list[dict[str, Any]] = []
        self.ap_items: list[dict[str, Any]] = []
        self.frozen: dict[int, date] = {}
        self.spare_stock = {p["part_id"]: p["max_stock"] for p in self.m.get("dim_spare_part")}
        self.tool_use = {t["tool_id"]: 0 for t in self.m.get("dim_tool")}
        self.price_index = 1.0

    def fg_warehouse(self, pid: int) -> int:
        ws = M.PRODUCTS[pid - 1][6][0]
        fac = M.WORKSHOPS[ws - 1][2]
        return {1: 2, 2: 5, 3: 7}[fac]

    def raw_warehouse(self, mid: int) -> int:
        return 1

    # ───────── 售前:商机与送样,赢单会在成交日生成订单 ─────────
    def plan_opportunities(self) -> None:
        rng = self.rng
        self.opp_orders: dict[date, list[dict[str, Any]]] = defaultdict(list)
        oid = 0
        for cid, c in self.cust.items():
            n = rng.randint(2, 6) if c["life"] != "lost" else 1
            for _ in range(n):
                create = c["start"] + timedelta(days=rng.randrange(0, max(1, (M.END - c["start"]).days - 20)))
                if c["stop"] and create > c["stop"]:
                    create = c["stop"] - timedelta(days=rng.randint(30, 120))
                pid = rng.choice(M.CUSTOMER_INDUSTRY_PRODUCTS[self.t["dim_customer"][cid - 1]["industry"]])
                est_q = round(rng.choice([60, 100, 150, 240, 360, 500]) * rng.uniform(0.8, 1.2), 1)
                guide = self.prod[pid]["guide_price"]
                rep = self.rep_of(cid, create)
                oid += 1
                outcome = rng.choices(["won", "lost", "stalled", "open"], weights=[45, 25, 12, 18])[0]
                close = create + timedelta(days=rng.randint(25, 120))
                if outcome in ("won", "lost") and close > M.END:
                    outcome = "open"
                if outcome == "won" and c["stop"] and close > c["stop"]:
                    outcome = "lost"
                opp = {"opp_id": oid, "opp_name": f"{self.t['dim_customer'][cid - 1]['cust_name'][:6]}-{self.prod[pid]['prod_name']}项目",
                       "cust_id": cid, "emp_id": rep, "create_date": create.isoformat(), "estimated_quantity": est_q,
                       "estimated_amount": round(est_q * guide, 2), "status": outcome,
                       "close_date": close.isoformat() if outcome in ("won", "lost") else None,
                       "is_important": int(est_q * guide >= 3_000_000), "converted_order_id": None, "_pid": pid}
                self.t["fact_opportunity"].append(opp)
                if outcome == "won":
                    self.opp_orders[close].append(opp)
                # 送样:商机创建后两周内,赢单的送样成功
                if rng.random() < 0.6:
                    sd = create + timedelta(days=rng.randint(3, 14))
                    fb = sd + timedelta(days=rng.randint(7, 30))
                    has_fb = fb <= M.END and (outcome != "open" or rng.random() < 0.5)
                    success = outcome == "won"
                    self.t["fact_sample"].append({"sample_id": len(self.t["fact_sample"]) + 1, "cust_id": cid, "emp_id": rep,
                                                  "sample_date": sd.isoformat(), "quantity": round(rng.choice([0.025, 0.05, 0.1, 0.2]), 4),
                                                  "feedback_date": fb.isoformat() if has_fb else None,
                                                  "feedback_result": ("success" if success else "fail") if has_fb else None,
                                                  "is_feedback": int(has_fb), "is_success": int(has_fb and success)})

    # ───────── 销售订单 ─────────
    def take_orders(self, d: date) -> None:
        rng = self.rng
        if d.month == 1 and d.day == 1 and d.year == 2026:
            self.price_index = 1.03
        for opp in self.opp_orders.get(d, []):
            if opp["cust_id"] in self.frozen and d >= self.frozen[opp["cust_id"]]:
                opp["status"], opp["close_date"] = "lost", d.isoformat()     # 客户冻结后赢单作废
                continue
            order = self.new_order(d, opp["cust_id"], opp["_pid"], max(5.0, round(opp["estimated_quantity"] / rng.uniform(6, 12), 2)))
            opp["converted_order_id"] = order["order_id"]
        if not is_workday(d):
            return
        for cid, c in self.cust.items():
            if d < c["start"] or (c["stop"] and d > c["stop"]) or (cid in self.frozen and d >= self.frozen[cid]):
                continue
            growth = 1.0 + 0.06 * ((d - M.START).days / 365)
            lam = c["size"] * SEASON[d.month] * growth / c["avg_qty"] / 21.0
            n = self.poisson(lam)
            for _ in range(n):
                pid = rng.choices(c["products"], weights=c["weights"])[0]
                qty = round(max(1.0, c["avg_qty"] * math.exp(rng.gauss(0, 0.45))), 2)
                self.new_order(d, cid, pid, qty)

    def poisson(self, lam: float) -> int:
        k, p, L = 0, 1.0, math.exp(-lam)
        while True:
            p *= self.rng.random()
            if p <= L:
                return k
            k += 1

    def new_order(self, d: date, cid: int, pid: int, qty: float) -> dict[str, Any]:
        rng = self.rng
        c = self.cust[cid]
        guide = round(self.prod[pid]["guide_price"] * self.price_index, 2)
        price = round(guide * c["discount"] * rng.uniform(0.98, 1.02), 2)
        if rng.random() < 0.03:                                # 少数战略/清库订单折让成交(指导价的 85%~95%)
            price = round(guide * rng.uniform(0.85, 0.95), 2)
        order = {"order_id": len(self.t["fact_sales_order"]) + 1, "order_date": d.isoformat(), "cust_id": cid,
                 "emp_id": self.rep_of(cid, d), "prod_id": pid, "quantity": qty, "unit_price": price,
                 "amount": round(qty * price, 2), "guide_price": guide, "status": "undelivered",
                 "_due": d + timedelta(days=rng.randint(7, 20)), "_open": qty, "_delivered": 0.0, "_returned": 0.0}
        self.t["fact_sales_order"].append(order)
        self.open_sales.append(order)
        return order

    # ───────── 采购:MRP → 请购 → 采购订单 ─────────
    def demand_rate(self, d: date) -> dict[int, float]:
        """各产品近 28 天的日均订货量(吨/天);期初用客户规模估算。"""
        since = d - timedelta(days=28)
        rate = defaultdict(float)
        recent = [o for o in self.t["fact_sales_order"] if date.fromisoformat(o["order_date"]) > since]
        if d - M.START < timedelta(days=28):
            for c in self.cust.values():
                tot = sum(c["weights"])
                for p, w in zip(c["products"], c["weights"]):
                    rate[p] += c["size"] * w / tot / 30.0
            return rate
        for o in recent:
            rate[o["prod_id"]] += o["quantity"] / 28.0
        return rate

    def mrp(self, d: date) -> None:
        horizon = defaultdict(float)
        rate = self.demand_rate(d)
        for mid in self.mat:
            cover = self.mat[mid]["lead_time_days"] + 14            # 提前期 + 两周复盘周期
            for pid, r in rate.items():
                horizon[mid] += r * 1.15 * cover * self.recipes[pid].get(mid, 0.0) / 1000.0
        for o in self.pending_prod:                                  # 已排程未投料的工单
            for mid, kg in self.recipes[o["prod_id"]].items():
                horizon[mid] += o["_actual"] * kg / 1000.0
        self.place_pos(d, horizon, rush=False)

    def rush_check(self, d: date) -> None:
        """每日缺料检查:三天内开工的工单所需原料不足时,向供应商下急单(1~2 天到货)。"""
        need = defaultdict(float)
        for o in self.pending_prod:
            if o["_start"].date() <= d + timedelta(days=3):
                for mid, kg in self.recipes[o["prod_id"]].items():
                    need[mid] += o["_actual"] * kg / 1000.0 * (1 + M.SCRAP_RATE + 0.01)
        short = {}
        for mid, q in need.items():
            stock = sum(l["qty"] for l in self.raw[mid])
            arriving = sum(qq for pl in self.open_po_lines if pl["material_id"] == mid for w, qq in pl["_parts"] if w <= d + 2 * timedelta(days=1))
            if stock + arriving < q:
                short[mid] = q - stock - arriving + self.mat[mid]["safety_stock"] * 0.5
        if short:
            self.place_pos(d, short, rush=True)

    def place_pos(self, d: date, horizon: dict[int, float], rush: bool) -> None:
        rng = self.rng
        by_supplier = defaultdict(list)
        for mid, need in horizon.items():
            if rush:
                lot = math.ceil(need) if need < 10 else math.ceil(need / 5.0) * 5.0
                by_supplier[(self.mat[mid]["supplier_id"], mid)].append((mid, float(lot)))
                continue
            stock = sum(l["qty"] for l in self.raw[mid])
            on_order = sum(pl["quantity"] - pl["_received"] for pl in self.open_po_lines if pl["material_id"] == mid)
            gap = need + self.mat[mid]["safety_stock"] - stock - on_order
            if gap > 0:
                lot = max(gap, self.mat[mid]["safety_stock"])
                lot = math.ceil(lot / 5.0) * 5.0 if lot >= 10 else round(lot + 0.5)
                by_supplier[(self.mat[mid]["supplier_id"], mid)].append((mid, float(lot)))
        for (sid, mat_id), items in by_supplier.items():          # 一张采购订单只订一种物料,收货可唯一对应到物料
            if not rush and d + timedelta(days=2) > M.END:      # 观测期最后两天不再下常规采购单
                continue
            pr_date = d
            requester = rng.choice(self.role_ids["计划员"])
            pr = {"pr_id": len(self.t["fact_purchase_requisition"]) + 1, "pr_no": f"PR{d.strftime('%Y%m%d')}{sid:02d}{mat_id:02d}{'R' if rush else ''}",
                  "dept_id": 15, "requester_id": requester, "required_date": (d + timedelta(days=max(self.mat[m]["lead_time_days"] for m, _ in items) + 2)).isoformat(),
                  "pr_date": pr_date.isoformat(), "status": "ordered",
                  "total_amount": round(sum(q * self.mat[m]["unit_price"] * self.price_index for m, q in items), 2)}
            self.t["fact_purchase_requisition"].append(pr)
            po_date = d if rush else d + timedelta(days=rng.randint(1, 2))
            lead = 0 if rush else max(self.mat[m]["lead_time_days"] for m, _ in items)
            po = {"po_id": len(self.t["fact_purchase_order"]) + 1, "po_no": f"PO{po_date.strftime('%Y%m%d')}{sid:02d}{mat_id:02d}{'R' if rush else ''}",
                  "supplier_id": sid, "po_date": po_date.isoformat(), "delivery_date": (po_date + timedelta(days=lead)).isoformat(),
                  "total_amount": 0.0, "currency": "CNY", "payment_terms": f"月结{M.SUPPLIERS[sid - 1][7]}天", "status": "confirmed",
                  "_pr": pr["pr_id"]}
            if po_date > M.END:
                po["status"] = "draft"
            self.t["fact_purchase_order"].append(po)
            for n, (mid, q) in enumerate(items, start=1):
                price = round(self.mat[mid]["unit_price"] * self.price_index * rng.uniform(0.97, 1.03) * (1.04 if rush else 1.0), 2)
                pl = {"line_id": len(self.t["fact_po_line"]) + 1, "po_id": po["po_id"], "line_no": n, "material_id": mid,
                      "quantity": q, "unit_price": price, "amount": round(q * price, 2), "received_qty": 0.0, "_received": 0.0}
                po["total_amount"] = round(po["total_amount"] + pl["amount"], 2)
                self.t["fact_po_line"].append(pl)
                if po["status"] != "draft":
                    self.schedule_receipts(po, pl, po_date, lead, rush)

    def schedule_receipts(self, po: dict[str, Any], pl: dict[str, Any], po_date: date, lead: int, rush: bool = False) -> None:
        rng = self.rng
        sup = M.SUPPLIERS[po["supplier_id"] - 1]
        late = 0 if rush or rng.random() < sup[8] else rng.randint(1, 6)
        first = po_date + timedelta(days=lead + late)
        parts = [pl["quantity"]] if rush or rng.random() > 0.15 else [round(pl["quantity"] * 0.6, 3), round(pl["quantity"] - round(pl["quantity"] * 0.6, 3), 3)]
        pl["_parts"] = [(first + timedelta(days=7 * i), q) for i, q in enumerate(parts)]
        self.open_po_lines.append(pl)

    def receive_materials(self, d: date) -> None:
        rng = self.rng
        for pl in list(self.open_po_lines):
            for _when, q in [p for p in pl["_parts"] if p[0] == d]:
                po = self.t["fact_purchase_order"][pl["po_id"] - 1]
                sup = M.SUPPLIERS[po["supplier_id"] - 1]
                passed = po["po_no"].endswith("R") or rng.random() < sup[9]
                gr = {"gr_id": len(self.t["fact_goods_receipt"]) + 1, "gr_no": f"GR{d.strftime('%Y%m%d')}{len(self.t['fact_goods_receipt']) + 1:04d}",
                      "po_id": pl["po_id"], "receipt_date": d.isoformat(), "quantity": q, "status": "posted" if passed else "inspected",
                      "inspector_id": rng.choice(self.role_ids["质检员"]), "inspection_result": "pass" if passed else "fail",
                      "_material": pl["material_id"], "_price": pl["unit_price"], "_po_line": pl["line_id"]}
                self.t["fact_goods_receipt"].append(gr)
                if passed:
                    pl["_received"] = round(pl["_received"] + q, 3)
                    self.raw[pl["material_id"]].append({"lot": f"RM{pl['material_id']:02d}-{d.strftime('%y%m%d')}-{gr['gr_id']}",
                                                       "qty": q, "date": d, "price": pl["unit_price"]})
                    terms = sup[7]
                    amount = round(q * pl["unit_price"] * (1 + M.VAT), 2)
                    ap = {"ap_id": len(self.t["fact_ap"]) + 1, "supplier_id": po["supplier_id"],
                          "invoice_no": f"SINV{d.strftime('%y%m%d')}{gr['gr_id']:04d}", "gr_id": gr["gr_id"], "amount": amount,
                          "due_date": (month_end(d) + timedelta(days=terms)).isoformat(), "paid_amount": 0.0, "status": "open"}
                    self.t["fact_ap"].append(ap)
                    self.ap_items.append(ap)
                else:
                    # 检验不合格整批退回,供应商一周后补货
                    pl["_parts"].append((d + timedelta(days=7), q))
            pl["_parts"] = [p for p in pl["_parts"] if p[0] != d]
            if pl["_received"] >= pl["quantity"] - 1e-6:
                self.open_po_lines.remove(pl)
        # 采购订单状态在期末统一结算

    # ───────── 生产计划与执行 ─────────
    def plan_production(self, d: date) -> None:
        backlog = defaultdict(float)
        for o in self.open_sales:
            if o["_due"] <= d + timedelta(days=14):
                backlog[o["prod_id"]] += o["_open"]
        for pid in self.prod:
            stock = sum(b["qty"] for b in self.fg[pid] if b["ok"])
            target = backlog[pid] + 20.0                           # 未交订单 + 安全库存
            gap = target - stock - self.wip[pid]
            if gap < 8.0:
                continue
            lot = min(120.0, max(15.0, math.ceil(gap / 5.0) * 5.0))
            line = min(self.product_lines[pid], key=lambda lid: self.line_free[lid])
            rel = max(dt(d, 8), self.line_free[line])
            if rel > dt(M.END, 23) + timedelta(days=10):
                continue
            order = {"order_id": len(self.t["fact_production_order"]) + 1, "order_no": f"WO{d.strftime('%Y%m%d')}{len(self.t['fact_production_order']) + 1:04d}",
                     "order_date": d.isoformat(), "line_id": line, "prod_id": pid,
                     "batch_no": f"B{d.strftime('%y%m%d')}{line:02d}{len(self.t['fact_production_order']) + 1:04d}",
                     "planned_quantity": lot, "actual_quantity": None, "yield_rate": None, "start_time": None, "end_time": None,
                     "runtime_hours": None, "status": "待生产"}
            self.t["fact_production_order"].append(order)
            self.wip[pid] += lot
            self.schedule(order, rel)

    def schedule(self, order: dict[str, Any], rel: datetime) -> None:
        """在产线时间轴上排程:到期的计划保养先做;换产要清机;运行中按小时率出现故障停机。"""
        rng = self.rng
        line = order["line_id"]
        t = rel
        if t >= self.next_pm[line]:
            self.pm_count[line] += 1
            overhaul = self.pm_count[line] % 3 == 0                    # 每三次月度检修中有一次是挤出机季度大修
            hours = float(rng.choice([24, 36, 48])) if overhaul else float(rng.choice([6, 8, 10, 12]))
            self.add_downtime(line, t, hours, 3, planned=True)
            self.maintenance_pm(line, t, hours, overhaul)
            t += hours * H
            self.next_pm[line] = t + timedelta(days=rng.randint(26, 32))
        if self.line_last_product[line] not in (None, order["prod_id"]):
            hours = rng.choice([1.0, 1.5, 2.0, 3.0])
            self.add_downtime(line, t, hours, 2, planned=True)
            t += hours * H
        cap = self.lines[line]["capacity_tph"]
        perf = min(0.97, self.lines[line]["_perf"] * rng.uniform(0.96, 1.03))   # 各线性能基线不同
        actual = round(order["planned_quantity"] * rng.uniform(0.97, 1.0), 2)
        run = actual / (cap * perf)
        start = t
        down_total = 0.0
        cursor = start
        remaining = run
        while remaining > 0:
            gap = rng.expovariate(1 / self.mtbf[line])           # 平均无故障运行时长随设备役龄下降
            if gap >= remaining:
                cursor += remaining * H
                break
            cursor += gap * H
            remaining -= gap
            reason = rng.choices([1, 4, 5, 6, 7, 8], weights=[40, 10, 15, 6, 6, 23])[0]
            hours = round(min(10.0, rng.lognormvariate(0.6, 0.7)), 2)
            self.add_downtime(line, cursor, hours, reason, planned=False)
            if reason == 1:
                self.maintenance_breakdown(line, cursor, hours)
            cursor += hours * H
            down_total += hours
        end = cursor
        for _ in range(self.poisson(run / 55.0)):                   # 运行中的预警(未停机):约每 55 运行小时一次
            when = start + (end - start) * rng.random()
            eq = rng.choice(self.eq_by_line[line])
            self.add_alarm(rng.choice(self.sensors_by_eq[eq["equipment_id"]]), eq["equipment_id"], when, critical=False)
        order.update({"start_time": fmt(start), "end_time": fmt(end), "runtime_hours": round(run, 2),
                      "_start": start, "_end": end, "_actual": actual, "_perf": perf, "_down": down_total})
        self.line_free[line] = end + 0.5 * H
        self.line_last_product[line] = order["prod_id"]
        self.pending_prod.append(order)

    def add_downtime(self, line: int, start: datetime, hours: float, reason: int, planned: bool) -> None:
        rng = self.rng
        if start.date() > M.END:                                  # 观测期之外不记录
            return
        eqs = self.eq_by_line[line]
        eq = eqs[2] if reason in (2, 3, 8) or rng.random() < 0.5 else rng.choice(eqs)
        desc = {1: f"{eq['equipment_name']}故障停机抢修", 2: "换产清机(清理螺杆与料斗)", 3: "按维护计划停机保养", 4: "原料未到位待料",
                5: "质量波动停机调整工艺", 6: "外部供电波动停机", 7: "人员调配不足停机", 8: "熔体压力升高更换滤网"}[reason]
        self.t["fact_downtime"].append({"downtime_id": len(self.t["fact_downtime"]) + 1, "equipment_id": eq["equipment_id"], "line_id": line,
                                        "start_time": fmt(start), "end_time": fmt(start + hours * H), "duration_hours": round(hours, 2),
                                        "reason_code": reason, "description": desc, "_planned": planned})
        if reason == 1:
            sensors = self.sensors_by_eq[eq["equipment_id"]]
            s = rng.choice(sensors)
            self.add_alarm(s, eq["equipment_id"], start - timedelta(minutes=rng.randint(2, 30)), critical=True)

    def add_alarm(self, s: dict[str, Any], eq_id: int, when: datetime, critical: bool) -> None:
        rng = self.rng
        if when.date() > M.END:
            return
        kind = {"温度": "超温", "压力": "超压", "电流": "过电流", "振动": "振动异常", "流量": "喂料波动", "料位": "料位异常",
                "转速": "转速异常", "重量": "称重偏差", "速度": "速度异常"}[s["sensor_type"]]
        handled = when + timedelta(minutes=rng.randint(5, 90))
        is_handled = handled <= dt(M.END, 23)
        self.t["fact_alarm"].append({"alarm_id": len(self.t["fact_alarm"]) + 1, "sensor_id": s["sensor_id"], "equipment_id": eq_id,
                                     "alarm_type": kind, "severity": "critical" if critical else "warning", "alarm_time": fmt(when),
                                     "description": f"{s['sensor_name']}{kind}(报警限 {s['_alarm']}{s['unit']})",
                                     "is_handled": int(is_handled), "handled_time": fmt(handled) if is_handled else None,
                                     "handled_by": self.emps[self.pick_tech(eq_id, when, when + timedelta(minutes=30))]["emp_name"] if is_handled else None})

    def maintenance_pm(self, line: int, start: datetime, hours: float, overhaul: bool) -> None:
        """整线停机检修:挤出机大修占用大部分时间,其余设备依次检修;同一窗口内的工单首尾相接,不重叠派工。"""
        eqs = self.eq_by_line[line]
        ext_share = 0.6 if overhaul else 0.3
        slots = [hours * ext_share if e["type_id"] == 1 else hours * (1 - ext_share) / (len(eqs) - 1) for e in eqs]
        cursor = start
        for e, h in zip(eqs, slots):
            self.new_wo(e, cursor, h, "季度大修" if overhaul and e["type_id"] == 1 else "月度检修", planned=True)
            cursor += h * H

    def pick_tech(self, eq_id: int, start: datetime, end: datetime) -> int:
        """派工:设备所在工厂的维修技师中,优先空闲且累计工时少的。"""
        line = next(e["line_id"] for e in self.m.get("dim_equipment") if e["equipment_id"] == eq_id)
        fac = M.WORKSHOPS[self.lines[line]["workshop_id"] - 1][2]
        pool = [tid for tid, f in self.m.tech_factory.items() if f == fac]
        jobs = self.tech_jobs
        free = [tid for tid in pool if all(e0 <= start or s0 >= end for s0, e0 in jobs[tid])]
        load = {tid: sum((e0 - s0).total_seconds() for s0, e0 in jobs[tid]) for tid in pool}
        tid = min(free or pool, key=lambda x: (load[x], x))
        jobs[tid].append((start, end))
        return tid

    def maintenance_breakdown(self, line: int, start: datetime, hours: float) -> None:
        if start.date() > M.END:
            return
        eq = self.t["fact_downtime"][-1]
        e = next(x for x in self.eq_by_line[line] if x["equipment_id"] == eq["equipment_id"])
        self.new_wo(e, start, hours, "故障维修", planned=False)

    def new_wo(self, eq: dict[str, Any], start: datetime, hours: float, kind: str, planned: bool) -> None:
        rng = self.rng
        wo_date = (start - timedelta(days=rng.randint(1, 5))).date() if planned else start.date()
        end = start + hours * H
        status = "completed" if end.date() <= M.END else ("in_progress" if start.date() <= M.END else "created")
        if wo_date > M.END:
            return
        wo = {"wo_id": len(self.t["fact_maintenance_wo"]) + 1, "wo_no": f"MWO{wo_date.strftime('%Y%m%d')}{len(self.t['fact_maintenance_wo']) + 1:04d}",
              "equipment_id": eq["equipment_id"], "plan_id": None, "maint_type": kind, "priority": "urgent" if not planned else "normal",
              "wo_date": wo_date.isoformat(), "start_time": fmt(start) if start.date() <= M.END else None,
              "end_time": fmt(end) if status == "completed" else None, "status": status,
              "assigned_tech": self.emps[self.pick_tech(eq["equipment_id"], start, end)]["emp_name"], "_hours": hours, "_type": eq["type_id"]}
        self.t["fact_maintenance_wo"].append(wo)
        if status == "completed":
            parts = [p for p in self.m.get("dim_spare_part") if p["equipment_type_id"] == eq["type_id"]]
            used = []
            if parts and (not planned or rng.random() < 0.4):
                p = rng.choice(parts)
                n = 1 if p["unit_price"] > 3000 else rng.randint(1, 4)
                if self.spare_stock[p["part_id"]] - n < p["min_stock"]:
                    self.spare_stock[p["part_id"]] = p["max_stock"]      # 低于下限即补货到上限
                self.spare_stock[p["part_id"]] -= n
                used.append((p, n))
            labor = round(hours * (2 if planned else 1.5), 1)
            cost = round(sum(p["unit_price"] * n for p, n in used) + labor * 120.0, 2)
            self.t["fact_maintenance_record"].append({
                "record_id": len(self.t["fact_maintenance_record"]) + 1, "wo_id": wo["wo_id"], "equipment_id": eq["equipment_id"],
                "maint_date": end.date().isoformat(), "maint_desc": f"{kind}:{eq['equipment_name']}" + ("检查、清洁、紧固、润滑" if planned else "故障排查与修复"),
                "parts_replaced": "、".join(f"{p['part_name']}×{n}" for p, n in used) or None, "labor_hours": labor, "cost": cost,
                "result": "completed"})

    def run_production(self, d: date) -> None:
        rng = self.rng
        for order in list(self.pending_prod):
            if order["_end"].date() != d:
                continue
            self.pending_prod.remove(order)
            pid, actual = order["prod_id"], order["_actual"]
            # 投料:按 BOM 从原料批次 FIFO 扣减,损耗约 1.2%
            total_input, mat_cost = 0.0, 0.0
            for mid, kg in self.recipes[pid].items():
                planned = round(kg / 1000.0 * actual, 4)
                act = round(planned * (1 + max(0.002, rng.gauss(M.SCRAP_RATE, 0.004))), 4)
                lot, cost = self.take_raw(mid, act)
                total_input += act
                mat_cost += cost
                self.t["fact_material_consumption"].append({
                    "consumption_id": len(self.t["fact_material_consumption"]) + 1, "order_id": order["order_id"], "material_id": mid,
                    "batch_no": lot, "planned_qty": planned, "actual_qty": act, "scrap_qty": round(act - planned, 4), "unit": "吨"})
            order["actual_quantity"] = actual
            order["yield_rate"] = round(actual / total_input * 100, 2)
            order["status"] = "已完成"
            self.wip[pid] -= order["planned_quantity"]
            # 成本:材料 + 人工与制造费用(按运行时长)
            pl = self.prod[pid]["prod_line_id"]
            conv = M.CONVERSION_COST[pl] * actual * rng.uniform(0.92, 1.12)
            labor = round(conv * 0.35, 2)
            overhead = round(conv - labor, 2)
            unit_cost = round((mat_cost + labor + overhead) / actual, 4)
            self.t["fact_product_cost"].append({"cost_id": len(self.t["fact_product_cost"]) + 1, "product_id": pid, "batch_no": order["batch_no"],
                                                "period": d.strftime("%Y-%m"), "material_cost": round(mat_cost, 2), "labor_cost": labor,
                                                "overhead_cost": overhead, "total_cost": round(mat_cost + labor + overhead, 2),
                                                "output_qty": actual, "unit_cost": unit_cost})
            # 检验:各项性能指标
            ok = self.inspect(order, d)
            order["_ok"] = ok is not False
            # 产出:按等级拆分
            # 产出等级:全部合格的按优等、一等拆分;让步接收的整批为合格品;不合格的整批为返工品(返工复检后放行)
            fgr = rng.uniform(0.78, 0.95) if ok is True else 0.0
            grades = ([("优等品", round(actual * fgr, 2)), ("一等品", round(actual - round(actual * fgr, 2), 2))] if ok is True
                      else [("合格品", actual)] if ok == "让步接收" else [("返工品", actual)])
            for g, q in grades:
                if q <= 0:
                    continue
                self.t["fact_production_output"].append({"output_id": len(self.t["fact_production_output"]) + 1, "order_id": order["order_id"],
                                                         "output_date": d.isoformat(), "quantity": q, "grade": g,
                                                         "is_first_grade": int(g == "优等品"), "first_grade_rate": round(fgr * 100, 2)})
            # 不合格批次隔离返工,返工完成次日放行后才可发货(返工窗口与 200 表返工单一致,见 M.rework_window)
            avail = d if ok in (True, "让步接收") else M.rework_window(order["_end"], order["runtime_hours"])[2]
            self.fg[pid].append({"batch": order["batch_no"], "qty": actual, "date": avail, "made": d, "unit_cost": unit_cost, "ok": True,
                                 "warehouse": self.fg_warehouse(pid)})
            self.fg_moves.append((pid, order["batch_no"], d, d, actual))
            for tool in self.m.get("dim_tool"):
                if tool["line_id"] == order["line_id"]:
                    self.tool_use[tool["tool_id"]] += 1

    def take_raw(self, mid: int, qty: float) -> tuple[str, float]:
        lots = self.raw[mid]
        main, cost, need = None, 0.0, qty
        while need > 1e-9:
            if not lots:
                # 投料时缺料:当日向供应商下急单并到货检验(真实的请购、采购、收货与应付记录)
                self.place_pos(self.day, {mid: need + self.mat[mid]["safety_stock"] * 0.5}, rush=True)
                self.receive_materials(self.day)
                self.urgent = getattr(self, "urgent", 0) + 1
            lot = lots[0]
            take = min(lot["qty"], need)
            if main is None or take > main[1]:
                main = (lot["lot"], take)
            lot["qty"] -= take
            cost += take * lot["price"]
            need -= take
            if lot["qty"] <= 1e-9:
                lots.pop(0)
        return (main[0] if main else ""), cost

    def inspect(self, order: dict[str, Any], d: date) -> Any:
        rng = self.rng
        spec = M.quality_spec(order["prod_id"])
        inspector = self.emps[rng.choice(self.role_ids["质检员"])]["emp_name"]
        failed, marginal = False, False
        for name, unit, nom, lo, hi in spec:
            if name.startswith("阻燃"):
                val = "V-0" if rng.random() > 0.015 else "V-1"
                res = "合格" if val == "V-0" else "不合格"
                std = "V-0"
            else:
                sd = (hi - lo) / 7.5
                v = round(rng.gauss(nom * 1.01, sd), 3 if unit == "g/cm³" else 2)   # 按记录精度判定,记录值与结论一致
                val = f"{v:.3f}" if unit == "g/cm³" else f"{v:.2f}"
                res = "合格" if lo <= v <= hi else ("让步接收" if lo - sd * 0.5 <= v <= hi + sd * 0.5 else "不合格")
                std = f"{lo:.3f}~{hi:.3f} {unit}" if unit == "g/cm³" else f"{lo:.2f}~{hi:.2f} {unit}"
            failed = failed or res == "不合格"
            marginal = marginal or res == "让步接收"
            self.t["fact_quality_inspection"].append({"inspection_id": len(self.t["fact_quality_inspection"]) + 1, "order_id": order["order_id"],
                                                      "batch_no": order["batch_no"], "inspection_date": d.isoformat(), "property_name": name,
                                                      "standard_value": std, "actual_value": val, "result": res, "inspector": inspector})
        return False if failed else ("让步接收" if marginal else True)

    # ───────── 发货、开票、收款、退货 ─────────
    def deliver(self, d: date) -> None:
        rng = self.rng
        if not is_workday(d) and rng.random() < 0.7:
            return
        for o in sorted(self.open_sales, key=lambda x: (x["_due"], x["order_id"])):
            if o["_due"] > d + timedelta(days=3) or date.fromisoformat(o["order_date"]) >= d:
                continue
            avail = [b for b in self.fg[o["prod_id"]] if b["ok"] and b["qty"] > 1e-6 and b["date"] < d]
            stock = sum(b["qty"] for b in avail)
            if stock < min(o["_open"], 3.0):
                continue
            q = round(min(o["_open"], stock), 2)
            if q < o["_open"] and q < 0.5 * o["quantity"]:
                continue                                          # 不足一半先不发,等补产
            cost = 0.0
            need = q
            for b in avail:
                take = min(b["qty"], need)
                b["qty"] = round(b["qty"] - take, 4)
                self.fg_moves.append((o["prod_id"], b["batch"], b["made"], d, -take))
                cost += take * b["unit_cost"]
                need -= take
                if need <= 1e-9:
                    break
            dl = {"delivery_id": len(self.t["fact_delivery"]) + 1, "order_id": o["order_id"], "delivery_date": d.isoformat(), "quantity": q,
                  "amount": round(q * o["unit_price"], 2), "is_consignment": int(self.cust[o["cust_id"]]["consignment"]),
                  "_cost": cost}
            self.t["fact_delivery"].append(dl)
            o["_open"] = round(o["_open"] - q, 4)
            o["_delivered"] = round(o["_delivered"] + q, 4)
            o.setdefault("_cost", 0.0)
            o["_cost"] += cost
            o["_last_delivery"] = d
            inv_days = rng.randint(15, 40) if dl["is_consignment"] else rng.choice([0, 0, 1, 1, 2, 3, 5])
            self.pending_invoices.append({"delivery": dl, "date": d + timedelta(days=inv_days)})
            if o["_open"] <= 1e-6:
                o["status"] = "delivered"
                self.open_sales.remove(o)
            if rng.random() < 0.018:                              # 退货
                self.plan_return(o, dl, d)

    def plan_return(self, o: dict[str, Any], dl: dict[str, Any], d: date) -> None:
        rng = self.rng
        inv_date = self.pending_invoices[-1]["date"]                # 退货在开票之后,开红字发票冲减
        rd = max(d + timedelta(days=rng.randint(5, 35)), inv_date + timedelta(days=rng.randint(1, 3)))
        if rd > M.END:
            return
        reason = rng.choices(["产品质量问题:冲击强度偏低", "颜色与样板不符", "包装破损受潮", "客户订单变更取消", "规格型号发错"],
                             weights=[35, 25, 15, 15, 10])[0]
        q = round(dl["quantity"] * (1.0 if reason.startswith("客户") and rng.random() < 0.5 else rng.uniform(0.05, 0.4)), 4)
        self.pending_returns.append({"date": rd, "order": o, "delivery": dl, "reason": reason, "quantity": q})

    def process_returns(self, d: date) -> None:
        """退货:开红字发票(负数),冲减原发票的应收;已收款不足以冲减的(客户已付清)不退货,按质量索赔线下处理。"""
        for r in [x for x in self.pending_returns if x["date"] == d]:
            self.pending_returns.remove(r)
            o, dl, q = r["order"], r["delivery"], r["quantity"]
            a = next((x for x in self.ar_items if not x.get("credit_note") and x["inv"]["delivery_id"] == dl["delivery_id"]), None)
            net = round(q * o["unit_price"], 2)
            tax = round(net * M.VAT, 2)
            if a is None or a["total"] - a["paid"] - a["credit"] < net + tax - 0.01:
                continue
            o["_returned"] = round(o["_returned"] + q, 4)
            self.t["fact_return"].append({"return_id": len(self.t["fact_return"]) + 1, "order_id": o["order_id"], "return_date": d.isoformat(),
                                          "quantity": q, "amount": net, "reason": r["reason"], "_delivery": dl["delivery_id"]})
            inv = {"invoice_id": len(self.t["fact_invoice"]) + 1, "delivery_id": dl["delivery_id"], "invoice_date": d.isoformat(),
                   "amount_no_tax": -net, "tax_amount": -tax, "total_amount": -round(net + tax, 2),
                   "days_to_invoice": (d - date.fromisoformat(dl["delivery_date"])).days}
            self.t["fact_invoice"].append(inv)
            a["credit"] = round(a["credit"] + net + tax, 2)
            a["credits"].append((d, round(net + tax, 2)))
            self.ar_items.append({"inv": inv, "cust": a["cust"], "emp": a["emp"], "due": d, "sched": [], "paid": 0.0, "credit": 0.0,
                                  "credits": [], "total": inv["total_amount"], "payments": [], "credit_note": True})

    def invoice(self, d: date) -> None:
        for p in [x for x in self.pending_invoices if x["date"] == d]:
            self.pending_invoices.remove(p)
            dl = p["delivery"]
            o = self.t["fact_sales_order"][dl["order_id"] - 1]
            net = dl["amount"]
            tax = round(net * M.VAT, 2)
            inv = {"invoice_id": len(self.t["fact_invoice"]) + 1, "delivery_id": dl["delivery_id"], "invoice_date": d.isoformat(),
                   "amount_no_tax": net, "tax_amount": tax, "total_amount": round(net + tax, 2),
                   "days_to_invoice": (d - date.fromisoformat(dl["delivery_date"])).days}
            self.t["fact_invoice"].append(inv)
            c = self.cust[o["cust_id"]]
            due = d + timedelta(days=c["terms"])
            delay = max(-5, int(self.rng.gauss(c["delay"], max(3, c["delay"] * 0.6))))
            pay = due + timedelta(days=delay)
            split = self.rng.random() < 0.12
            sched = [(pay, round(inv["total_amount"] * 0.5, 2)), (pay + timedelta(days=self.rng.randint(15, 45)), None)] if split else [(pay, None)]
            self.ar_items.append({"inv": inv, "cust": o["cust_id"], "emp": o["emp_id"], "due": due, "sched": sched, "paid": 0.0, "credit": 0.0,
                                  "credits": [], "total": inv["total_amount"], "payments": []})

    def collect(self, d: date) -> None:
        for a in self.ar_items:
            for _i, (when, amt) in enumerate(a["sched"]):
                if when != d:
                    continue
                left = round(a["total"] - a["paid"] - a["credit"], 2)
                amt = left if amt is None else min(amt, left)
                if amt <= 0:
                    continue
                c = self.cust[a["cust"]]
                bank = self.rng.random() < c["bank"]
                a["paid"] = round(a["paid"] + amt, 2)
                a["payments"].append({"date": d, "amount": amt, "type": "银承" if bank else "电汇", "bank": int(bank)})

    def pay_suppliers(self, d: date) -> None:
        for ap in self.ap_items:
            if ap["status"] == "closed":
                continue
            due = date.fromisoformat(ap["due_date"])
            if d == due + timedelta(days=self.rng.choice([0, 0, 1, 3, 5])) or (d > due + timedelta(days=10) and ap["paid_amount"] < ap["amount"]):
                amt = round(ap["amount"] - ap["paid_amount"], 2)
                if amt <= 0:
                    continue
                self.t["fact_payment_out"].append({"payment_id": len(self.t["fact_payment_out"]) + 1, "ap_id": ap["ap_id"], "payment_date": d.isoformat(),
                                                   "amount": amt, "payment_method": "bank_transfer" if amt < 2_000_000 else "letter_of_credit",
                                                   "bank_account": f"SYN-ACCOUNT-{ap['supplier_id']:04d}"})
                ap["paid_amount"] = round(ap["paid_amount"] + amt, 2)
                ap["status"] = "closed" if ap["paid_amount"] >= ap["amount"] - 0.01 else "partial"

    def credit_control(self, d: date) -> None:
        for a in self.ar_items:
            if a["cust"] in self.frozen or a["total"] - a["paid"] - a["credit"] <= 1:
                continue
            if (d - a["due"]).days > 90 and self.cust[a["cust"]]["life"] == "risk":
                self.frozen[a["cust"]] = d + timedelta(days=1)

    # ───────── 期末结算 ─────────
    def close_period(self) -> None:
        end = M.END
        # 在制:已排程未完工的工单
        for o in self.t["fact_production_order"]:
            if o["status"] == "已完成":
                continue
            if o.get("_start") and o["_start"].date() <= end:
                o["status"] = "生产中"
                done = min(1.0, (dt(end, 24) - o["_start"]) / (o["_end"] - o["_start"]))
                o["actual_quantity"] = round(o["_actual"] * done, 2)
                o["end_time"] = None
                o["runtime_hours"] = round(o["runtime_hours"] * done, 2)
            else:
                o["status"] = "待生产"
                o["start_time"] = o["end_time"] = None
                o["runtime_hours"] = None
                o["actual_quantity"] = None
        # 采购订单状态
        lines_by_po = defaultdict(list)
        for pl in self.t["fact_po_line"]:
            pl["received_qty"] = pl["_received"]
            lines_by_po[pl["po_id"]].append(pl)
        for po in self.t["fact_purchase_order"]:
            if po["status"] == "draft":
                continue
            ls = lines_by_po[po["po_id"]]
            if all(pl["received_qty"] >= pl["quantity"] - 1e-6 for pl in ls):
                grs = [g for g in self.t["fact_goods_receipt"] if g["po_id"] == po["po_id"]]
                aps = [ap for ap in self.t["fact_ap"] if ap["gr_id"] in {g["gr_id"] for g in grs}]
                po["status"] = "closed" if aps and all(ap["status"] == "closed" for ap in aps) else "received"
        # 收款与应收账龄(期末快照)
        for a in self.ar_items:
            inv = a["inv"]
            ar_id = len(self.t["fact_ar_aging"]) + 1
            for p in a["payments"]:
                self.t["fact_payment"].append({"payment_id": len(self.t["fact_payment"]) + 1, "ar_id": ar_id, "payment_date": p["date"].isoformat(),
                                               "amount": p["amount"], "payment_type": p["type"], "is_bank_accepted": p["bank"]})
            bal = 0.0 if a.get("credit_note") else round(a["total"] - a["paid"] - a["credit"], 2)   # 红字发票当即冲减原发票应收
            idate = date.fromisoformat(inv["invoice_date"])
            age_days = (end - idate).days
            buckets = {k: 0.0 for k in ("aging_1m", "aging_2m", "aging_3m", "aging_4m", "aging_5m", "aging_6m", "aging_6_12m", "aging_12m_plus")}
            if bal > 0:
                key = ("aging_1m" if age_days <= 30 else "aging_2m" if age_days <= 60 else "aging_3m" if age_days <= 90 else
                       "aging_4m" if age_days <= 120 else "aging_5m" if age_days <= 150 else "aging_6m" if age_days <= 180 else
                       "aging_6_12m" if age_days <= 365 else "aging_12m_plus")
                buckets[key] = bal
            overdue_days = (end - a["due"]).days
            self.t["fact_ar_aging"].append({"ar_id": ar_id, "cust_id": a["cust"], "emp_id": a["emp"], "invoice_id": inv["invoice_id"],
                                            "due_date": a["due"].isoformat(), "balance_amount": bal, "current_month": age_days // 30 + 1,
                                            **buckets, "is_overdue": int(bal > 0 and overdue_days > 0),
                                            "overdue_months": (overdue_days // 30 + 1) if bal > 0 and overdue_days > 0 else 0,
                                            "_total": a["total"], "_payments": a["payments"], "_due": a["due"], "_credits": a["credits"],
                                            "_credit_note": bool(a.get("credit_note"))})
        self.spare_final = dict(self.spare_stock)
