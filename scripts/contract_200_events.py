#!/usr/bin/env python3
"""Event-driven rows for the synthesized fact tables of the 200-table database (generator v3, DR-076).

v2 sampled each synthesized table on its own (dates "any day not before the parent",
quantities and categorical fields drawn independently, skewed parent picks reused everywhere),
so joined facts were consistent but business-wise impossible.  v3 derives every row from the
events of the 108-table base (itself simulated end to end): shipments from deliveries and the
FIFO batches they consumed, operations and labour inside each production order's window,
inspection lots and nonconformances from the quality results, meter readings accumulated from
daily consumption, vouchers from documents by double-entry rules, attendance from the shift
roster.  Each function returns rows carrying references and business values; columns it does
not set are filled by ``contract_200_values.fact_attr``.  Standard library only.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Callable

import contract_200_fill as FILL
import contract_200_semantics as S
import demo108_master as M

END = date(2026, 6, 30)
WEND = datetime(2026, 6, 30, 23, 59, 59)                     # 观测期末时刻
GO_LIVE = date(2025, 1, 1)                                    # 系统上线日,早于此日的记录在上线时迁移
POSITION_OF = {"生产主管": "车间主任"}                         # 建档岗位 → 岗位主数据名称(其余同名)
KEY_PARAMS = ("熔体温度", "熔体压力", "螺杆转速", "主机电流", "真空度")   # 挤出机巡检记录的关键工艺参数
H = timedelta(hours=1)


def d_(x: Any) -> date:
    return x if isinstance(x, date) and not isinstance(x, datetime) else date.fromisoformat(str(x)[:10])


def t_(x: Any) -> datetime:
    return x if isinstance(x, datetime) else datetime.fromisoformat(str(x))


def at(day: date, hour: float) -> datetime:
    return datetime(day.year, day.month, day.day) + timedelta(hours=hour)


def status_for(day: date, done: bool = True, cancelled: bool = False) -> str:
    if cancelled:
        return "CANCELLED"
    if not done:
        return "IN_PROGRESS"
    return "CLOSED" if (END - day).days > 30 else "APPROVED"


def _num(val: Any) -> float:
    """检验记录值转数值;阻燃等级 V-0 记 0,V-1 记 1。"""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0 if val == "V-0" else 1.0


def ev(day: date | datetime, status: str | None = None, **kw: Any) -> dict[str, Any]:
    """一行事件:业务日期/时间与状态,外加引用和业务字段。"""
    tm = day if isinstance(day, datetime) else None
    dd = day.date() if isinstance(day, datetime) else day
    row = {"_date": dd, "_status": status or status_for(dd)}
    if tm is not None:
        row["_time"] = tm
    row.update(kw)
    return row


class Events:
    # 惰性缓存(首次使用时计算)
    _fifo: dict[int, list[tuple[str, float, date]]]
    _lso: dict[tuple[int, date, str], dict[str, float]]
    _mflows: list[tuple[date, int, int, float, str, str, str, float]]
    _transfer_need: dict[tuple[date, int, int], float]
    _snap: dict[tuple[int, int, date], float]
    _series: dict[int, list[tuple[datetime, float]]]
    _vouchers: list[tuple[date, str, list[tuple[str, float, float]], str]]
    _lot_mat: dict[int, int]
    _lot_price: dict[int, float]
    _voucher_of: dict[tuple[date, str], str]
    _capex: list[dict[str, Any]]
    _plans: dict[str, list[dict[str, Any]]]
    _line_alloc: dict[int, list[tuple[int, float]]]
    _line_parts: dict[int, list[float]]
    _bcost: dict[str, float]
    _ship_parts: dict[int, list[tuple[int, str, float, date]]]
    def __init__(self, ctx: S.Context, seed: int):
        self.ctx = ctx
        self.con = ctx.con
        self.rng = random.Random(f"{seed}:events")
        self.q = lambda sql, *a: self.con.execute(sql, a).fetchall()
        self.F = FILL.Filler(ctx)

    def rows(self, table: str, key: str) -> dict[Any, dict[str, Any]]:
        return self.ctx.rows(table, key)

    # ───────── 共用推导 ─────────
    def fifo_allocations(self) -> dict[int, list[tuple[str, float, date]]]:
        """按 108 表的产出、检验与发货回放成品 FIFO:每张发货单消耗了哪些批次。"""
        if hasattr(self, "_fifo"):
            return self._fifo
        ok = {}
        for oid, res in self.q("SELECT order_id, GROUP_CONCAT(result) FROM fact_quality_inspection GROUP BY order_id"):
            ok[oid] = "不合格" not in (res or "")
        batches: dict[int, list[list[Any]]] = defaultdict(list)    # 产品 → [[批次, 余量, 可发货日, 完工日]](按完工先后)
        for oid, pid, batch, qty, end, rt in self.q("SELECT order_id, prod_id, batch_no, actual_quantity, end_time, runtime_hours FROM fact_production_order "
                                                    "WHERE status='已完成' ORDER BY end_time, order_id"):
            # 不合格批次返工后放行(与 108 表仿真一致),放行前不参与分配
            avail = d_(end) if ok.get(oid, True) else M.rework_window(t_(end), rt)[2]
            batches[pid].append([batch, qty, avail, d_(end)])
        alloc: dict[int, list[tuple[str, float, date]]] = {}
        for did, oid, dd_, qty in self.q("SELECT delivery_id, order_id, delivery_date, quantity FROM fact_delivery ORDER BY delivery_date, delivery_id"):
            pid, dd = self.rows("fact_sales_order", "order_id")[oid]["prod_id"], d_(dd_)
            need, got = qty, []
            for b in batches[pid]:
                if b[1] <= 1e-9 or b[2] >= dd:
                    continue
                take = min(b[1], need)
                b[1] -= take
                got.append((b[0], round(take, 4), b[3]))
                need -= take
                if need <= 1e-9:
                    break
            assert need <= 1e-6, f"成品 FIFO 回放出现缺口(与 108 表发货不一致): 交付 {did} 缺 {need}"   # 108 表不设期初成品库存
            alloc[did] = got
        self._fifo = alloc
        return alloc

    def crew(self, line_id: int, when: datetime) -> tuple[str, int]:
        """四班三运转:每线甲乙丙丁四个班组,每天三班在岗、一班轮休,班次逐日倒转(夜→中→白→休)。返回(班次, 当班班组长)。"""
        h = when.hour
        code = "DAY" if 8 <= h < 16 else "SWING" if h >= 16 else "NIGHT"
        j = {"DAY": 0, "SWING": 1, "NIGHT": 2}[code]
        k = (when.date() - GO_LIVE).days
        return code, self.F.leaders_of_line[line_id][(k + j) % 4]

    def team_shift(self, line_id: int, team_k: int, day: date) -> str | None:
        """班组 team_k(0~3)当天的班次;轮休返回 None。"""
        j = (team_k - (day - GO_LIVE).days) % 4
        return None if j == 3 else ("DAY", "SWING", "NIGHT")[j]

    def employees(self, role_dept: int | None = None) -> list[int]:
        rows = self.rows("dim_employee", "emp_id").values()
        return sorted(e["emp_id"] for e in rows if role_dept is None or e["dept_id"] == role_dept)

    # ───────── 销售与客户 ─────────
    def lead_days(self, cid: int) -> int:
        """客户要求交期:按客户固定的 10~20 天(与 108 表仿真的交期区间一致)。"""
        return 10 + (cid * 7) % 11

    def line_allocation(self) -> dict[int, list[tuple[int, float]]]:
        """交付按订单行顺序逐行分配(第 1 行发满再发第 2 行):delivery_id → [(行号, 数量)]。"""
        if hasattr(self, "_line_alloc"):
            return self._line_alloc
        parts = self.line_parts()
        alloc: dict[int, list[tuple[int, float]]] = {}
        left = {oid: list(ps) for oid, ps in parts.items()}
        for dl in sorted(self.rows("fact_delivery", "delivery_id").values(), key=lambda x: (x["delivery_date"], x["delivery_id"])):
            need, got = dl["quantity"], []
            rem = left[dl["order_id"]]
            for k in range(len(rem)):
                if need <= 1e-9:
                    break
                take = min(rem[k], need)
                if take > 1e-9:
                    got.append((k + 1, round(take, 4)))
                    rem[k] = round(rem[k] - take, 6)
                    need -= take
            if need > 1e-6:                                    # 尾差计入最后一行
                n, q = got[-1] if got else (len(rem), 0.0)
                got = got[:-1] + [(n, round(q + need, 4))] if got else [(n, round(need, 4))]
            alloc[dl["delivery_id"]] = got
        self._line_alloc = alloc
        return alloc

    def line_parts(self) -> dict[int, list[float]]:
        if hasattr(self, "_line_parts"):
            return self._line_parts
        out = {}
        for o in sorted(self.rows("fact_sales_order", "order_id").values(), key=lambda x: x["order_id"]):
            split = (o["order_id"] * 2654435761) % 100 < 15 and o["quantity"] >= 10      # 约 15% 的订单分两次要求交货
            out[o["order_id"]] = [round(o["quantity"] * 0.5, 4), round(o["quantity"] - round(o["quantity"] * 0.5, 4), 4)] if split else [o["quantity"]]
        self._line_parts = out
        return out

    def batch_cost(self) -> dict[str, float]:
        if not hasattr(self, "_bcost"):
            self._bcost = {r[0]: float(r[1]) for r in self.q("SELECT batch_no, unit_cost FROM fact_product_cost")}
        return self._bcost

    def fact_sales_order_line(self) -> list[dict[str, Any]]:
        """订单明细:每张订单一行,约 15% 按两次要求交期拆成两行(数量与金额之和等于表头);
        实发量为分配到该行的交付合计,发满才关闭;成本按实际发出批次。"""
        out = []
        shipped: dict[tuple[int, int], float] = defaultdict(float)
        dl_order = {d["delivery_id"]: d["order_id"] for d in self.rows("fact_delivery", "delivery_id").values()}
        for did, ps in self.line_allocation().items():
            for n, q in ps:
                shipped[(dl_order[did], n)] += q
        cost_of_line: dict[tuple[int, int], float] = defaultdict(float)
        bcost = self.batch_cost()
        for did, sp in self.ship_parts().items():
            for lno, batch, bq, _made in sp:
                cost_of_line[(dl_order[did], lno)] += bq * bcost.get(batch, 0.0)
        std = {p: r["standard_cost"] for p, r in self.rows("dim_product", "prod_id").items()}
        for o in self.rows("fact_sales_order", "order_id").values():
            od = d_(o["order_date"])
            parts = self.line_parts()[o["order_id"]]
            amt1 = round(parts[0] * o["unit_price"], 2)
            amts = [amt1, round(o["amount"] - amt1, 2)] if len(parts) == 2 else [o["amount"]]
            for n, (q, a) in enumerate(zip(parts, amts), start=1):
                req = od + timedelta(days=self.lead_days(o["cust_id"]) + 7 * (n - 1))
                sq = round(min(q, shipped[(o["order_id"], n)]), 4)
                cost = round(cost_of_line[(o["order_id"], n)] + max(0.0, q - sq) * std[o["prod_id"]], 2)   # 未发部分按标准成本
                status = ("CLOSED" if sq >= q - 1e-4 and od < END - timedelta(days=30)
                          else "APPROVED" if sq >= q - 1e-4 else "IN_PROGRESS" if sq > 0 else "OPEN")
                out.append(ev(od, status, order_id=o["order_id"], prod_id=o["prod_id"], _line_no=n,
                              unit_code="TON", quantity=q, base_quantity=round(q * 1000, 3), planned_quantity=q, actual_quantity=sq,
                              qualified_quantity=sq, rejected_quantity=0.0, currency_code="CNY", exchange_rate=1.0,
                              unit_price=o["unit_price"], amount=a, _tax_rate=0.13, cost_amount=cost, budget_amount=round(q * o["guide_price"], 2),
                              requested_delivery_date=req.isoformat(), promised_delivery_date=req.isoformat(),
                              customer_po_no=f"CPO-{o['cust_id']:03d}-{o['order_id']}", price_list_code=f"PL-{od.year}-STD",
                              sales_region=self.F.cust_region(o["cust_id"]), contract_no=self.F.sales_contract(o["cust_id"], od),
                              margin_rate=round((a - cost) / a, 4) if a else None, delivery_terms="送货上门",
                              source_document_no=f"SO{o['order_id']:06d}"))
        return out

    def fact_shipment(self) -> list[dict[str, Any]]:
        """发运单 = 交付单;合同号、客户订单号、交期、价格表继承订单首行。"""
        out = []
        carriers = sorted(self.rows("dim_carrier", "carrier_id"))
        road = [c for c, r in self.rows("dim_carrier", "carrier_id").items() if r["transport_mode"] == "road"] or carriers
        lines = {(l["order_id"], l["line_no"]): l for l in self.rows("fact_sales_order_line", "sales_order_line_id").values()}
        alloc = self.line_allocation()
        for dl in self.rows("fact_delivery", "delivery_id").values():
            o = self.rows("fact_sales_order", "order_id")[dl["order_id"]]
            dd = d_(dl["delivery_date"])
            wh = self.fg_warehouse(o["prod_id"])
            pickup = (dl["delivery_id"] * 40503) % 100 < 12
            ol = lines[(o["order_id"], alloc[dl["delivery_id"]][0][0])]
            out.append(ev(at(dd, 8 + (dl["delivery_id"] * 37) % 8), status_for(dd), delivery_id=dl["delivery_id"], cust_id=o["cust_id"],
                          warehouse_id=wh, carrier_id=road[dl["delivery_id"] % len(road)], unit_code="TON", currency_code="CNY",
                          exchange_rate=1.0, _tax_rate=0.13, delivery_terms="客户自提" if pickup else "送货上门",
                          contract_no=ol["contract_no"], customer_po_no=ol["customer_po_no"], price_list_code=ol["price_list_code"],
                          requested_delivery_date=ol["requested_delivery_date"], promised_delivery_date=ol["promised_delivery_date"],
                          sales_region=ol["sales_region"],
                          signed_date=(dd + timedelta(days=1 + dl["delivery_id"] % 3)).isoformat() if dd + timedelta(days=3) <= END else None,
                          source_document_no=f"DL{dl['delivery_id']:06d}", _pickup=pickup))
        return out

    def fg_warehouse(self, pid: int) -> int:
        ws = M.PRODUCTS[pid - 1][6][0]                          # 与 108 表仿真一致:产品所属车间 → 工厂 → 成品仓
        return {1: 2, 2: 5, 3: 7}[M.WORKSHOPS[ws - 1][2]]

    def ship_parts(self) -> dict[int, list[tuple[int, str, float, date]]]:
        """交付 → [(订单行号, 成品批次, 数量, 完工日)]:FIFO 批次按订单行分配顺序切分。"""
        if hasattr(self, "_ship_parts"):
            return self._ship_parts
        out: dict[int, list[tuple[int, str, float, date]]] = {}
        for did, batches in self.fifo_allocations().items():
            lines = [list(x) for x in self.line_allocation()[did]]
            res, li = [], 0
            for batch, q, made in batches:
                left = q
                while left > 1e-9 and li < len(lines):
                    take = min(left, lines[li][1])
                    if take > 1e-9:
                        res.append((int(lines[li][0]), batch, round(take, 4), made))
                    left -= take
                    lines[li][1] -= take
                    if lines[li][1] <= 1e-9:
                        li += 1
                if left > 1e-6:                                # 舍入尾差
                    n = res[-1][0] if res else int(lines[-1][0])
                    res.append((n, batch, round(left, 4), made))
            out[did] = res
        self._ship_parts = out
        return out

    def fact_shipment_line(self) -> list[dict[str, Any]]:
        """发运明细 = 发货消耗的成品批次(FIFO 回放)× 订单行;数量合计等于发货数量,成本按批次单位成本。"""
        ship_of = {s["delivery_id"]: s for s in self.rows("fact_shipment", "shipment_id").values()}
        lines = {(l["order_id"], l["line_no"]): l for l in self.rows("fact_sales_order_line", "sales_order_line_id").values()}
        bcost = self.batch_cost()
        out = []
        for did, parts in self.ship_parts().items():
            sh = ship_of[did]
            o = self.rows("fact_sales_order", "order_id")[self.rows("fact_delivery", "delivery_id")[did]["order_id"]]
            sd = d_(sh["business_date"])
            for n, (lno, batch, q, made) in enumerate(parts, start=1):
                ol = lines[(o["order_id"], lno)]
                out.append(ev(sd, sh["status"], shipment_id=sh["shipment_id"], sales_order_line_id=ol["sales_order_line_id"], prod_id=o["prod_id"],
                              _line_no=n, unit_code="TON", quantity=q, base_quantity=round(q * 1000, 3), planned_quantity=q, actual_quantity=q,
                              qualified_quantity=q, rejected_quantity=0.0, currency_code="CNY", exchange_rate=1.0, unit_price=o["unit_price"],
                              amount=round(q * o["unit_price"], 2), cost_amount=round(q * bcost.get(batch, 0.0), 2),
                              budget_amount=round(q * o["guide_price"], 2), _tax_rate=0.13, batch_no=batch, lot_no=batch,
                              manufacture_date=made.isoformat(), expiry_date=(made + timedelta(days=730)).isoformat(),
                              stock_status="可用", movement_type="601 销售发货", available_quantity=q, reserved_quantity=0.0,
                              bin_code=self.fg_bin(sh["warehouse_id"], o["prod_id"]), source_document_no=f"DL{did:06d}"))
        return out

    def fg_bin(self, wh: int, pid: int) -> str:
        codes = sorted(l["location_code"] for l in self.rows("dim_storage_location", "location_id").values() if l["warehouse_id"] == wh)
        return codes[pid % len(codes)]

    def fact_sales_contract_line(self) -> list[dict[str, Any]]:
        """销售框架合同明细:合同约定额按客户 2025 年产品结构分到各产品,约定价为 2025 年成交均价;
        实际量为合同期内(终止合同截至终止日)的订单量,观测期截至 2026-06-30。"""
        out = []
        orders = defaultdict(list)
        for o in self.rows("fact_sales_order", "order_id").values():
            orders[o["cust_id"]].append(o)
        for c in self.rows("fact_contract", "contract_id").values():
            if c["contract_type"] != "sales":
                continue
            st, en = d_(c["start_date"]), d_(c["end_date"])
            mix = defaultdict(lambda: [0.0, 0.0])
            act = defaultdict(float)
            for o in orders[c["party_id"]]:
                od = d_(o["order_date"])
                if od.year == 2025:
                    mix[o["prod_id"]][0] += o["quantity"]
                    mix[o["prod_id"]][1] += o["amount"]
                if st <= od <= en:
                    act[o["prod_id"]] += o["quantity"]
            tot = sum(v[1] for v in mix.values()) or 1.0
            for n, (pid, (q, a)) in enumerate(sorted(mix.items(), key=lambda x: -x[1][1]), start=1):
                price = round(a / q, 2)
                qty = round(c["total_amount"] * a / tot / price, 1)
                out.append(ev(st, "APPROVED" if c["status"] == "active" else "CLOSED", contract_id=c["contract_id"], prod_id=pid, _line_no=n,
                              unit_code="TON", quantity=qty, base_quantity=round(qty * 1000, 3), planned_quantity=qty,
                              actual_quantity=round(act[pid], 3), qualified_quantity=round(act[pid], 3), rejected_quantity=0.0, currency_code="CNY",
                              exchange_rate=1.0, unit_price=price, amount=round(qty * price, 2), _tax_rate=0.13, price_list_code="PL-2025-STD",
                              requested_delivery_date=c["end_date"], promised_delivery_date=c["end_date"],
                              contract_no=c["contract_no"], customer_po_no=None, delivery_terms="送货上门", source_document_no=c["contract_no"]))
        return out

    def fact_lead(self) -> list[dict[str, Any]]:
        """线索:每个商机之前有一条线索(转化),另有未转化线索;阶段与概率一致。"""
        out = []
        for op in self.rows("fact_opportunity", "opp_id").values():
            cd = d_(op["create_date"]) - timedelta(days=self.rng.randint(5, 30))
            stage = {"won": "赢单", "lost": "输单", "stalled": "商务谈判", "open": "方案报价"}[op["status"]]
            out.append(ev(max(cd, GO_LIVE), "CLOSED" if op["status"] in ("won", "lost") else "IN_PROGRESS", cust_id=op["cust_id"],
                          emp_id=op["emp_id"], sales_channel_id=None, opportunity_stage=stage, probability=S.STAGE_PROB[stage],
                          expected_close_date=(op["close_date"] or (d_(op["create_date"]) + timedelta(days=90)).isoformat()),
                          currency_code="CNY", exchange_rate=1.0, amount=op["estimated_amount"], unit_price=op["estimated_amount"], _tax_rate=0.0))
        for _ in range(len(out)):
            cid = self.rng.choice(sorted(self.rows("dim_customer", "cust_id")))
            day = date(2025, 1, 1) + timedelta(days=self.rng.randrange(540))
            stage = self.rng.choice(["线索", "需求确认", "输单"])
            rep = self.rep_for(cid, day)
            if stage != "输单" and day < END - timedelta(days=90):
                stage = "输单"                                    # 超过三个月未推进的线索按输单关闭
            out.append(ev(day, "CLOSED" if stage == "输单" else "OPEN", cust_id=cid, emp_id=rep, opportunity_stage=stage,
                          probability=S.STAGE_PROB[stage], currency_code="CNY", exchange_rate=1.0,
                          expected_close_date=(day + timedelta(days=90)).isoformat(),
                          amount=round(self.rng.uniform(1e5, 2e6), -3), _tax_rate=0.0))
        nxt = {"线索": "技术交流", "需求确认": "寄样", "方案报价": "报价跟进", "商务谈判": "商务谈判"}
        seg = {r["name"]: k for k, r in self.rows("dim_customer_segment", "customer_segment_id").items()}
        for r in out:
            r["next_action"] = nxt.get(r["opportunity_stage"])          # 已关闭(赢单、输单)的没有下一步
            r["customer_segment_id"] = self.cust_segment(r["cust_id"], seg)
            r["unit_price"] = r["amount"]
            r["sales_channel_id"] = r.get("sales_channel_id") or 1 + (r["cust_id"] % 4)
            r["rate_value"] = r["probability"]
            r["score_value"] = round(r["probability"] * 100, 1)
        return out

    def cust_segment(self, cid: int, seg: dict[str, int]) -> int:
        """客户分群:年需求 ≥ 200 吨/月的为战略客户,首单在上线后的为新客户,其余为重点客户。"""
        c = self.rows("dim_customer", "cust_id")[cid]
        if c["first_deal_date"] and c["first_deal_date"] >= "2025-01-01":
            return seg["新客户"]
        vol = sum(o["quantity"] for o in self.rows("fact_sales_order", "order_id").values() if o["cust_id"] == cid) / 18
        return seg["战略客户"] if vol >= 200 else seg["重点客户"]

    def rep_for(self, cid: int, day: date) -> int:
        orders = [o for o in self.rows("fact_sales_order", "order_id").values() if o["cust_id"] == cid]
        if not orders:
            return self.employees(1)[0]
        best = min(orders, key=lambda o: abs((d_(o["order_date"]) - day).days))
        return best["emp_id"]

    def fact_customer_contact(self) -> list[dict[str, Any]]:
        """客户拜访与沟通:每个业务员对名下客户约每两周一次,订单前后更频繁。"""
        out = []
        rep_of = {}
        for o in sorted(self.rows("fact_sales_order", "order_id").values(), key=lambda o: o["order_date"]):
            rep_of[o["cust_id"]] = o["emp_id"]
        for cid, _rep in sorted(rep_of.items()):
            day = date(2025, 1, 6)
            while day <= END:
                day += timedelta(days=self.rng.randint(10, 20))
                if day > END:
                    break
                r = self.rep_for(cid, day)
                sat = round(min(5.0, max(2.5, self.rng.gauss(4.2, 0.4))), 1)
                out.append(ev(at(day, self.rng.uniform(9, 17)), "CLOSED", cust_id=cid, emp_id=r,
                              duration_minutes=float(self.rng.choice([15, 30, 45, 60, 90, 120])), satisfaction_score=sat,
                              rate_value=round(sat / 5, 3), score_value=round(sat * 20, 1)))
        return out

    def fact_sales_forecast(self) -> list[dict[str, Any]]:
        """月度滚动预测:每月初预测下月客户×产品销量,误差 ±25%。"""
        actual = defaultdict(float)
        for o in self.rows("fact_sales_order", "order_id").values():
            actual[(o["cust_id"], o["prod_id"], o["order_date"][:7])] += o["quantity"]
        seg = {r["name"]: k for k, r in self.rows("dim_market_segment", "market_segment_id").items()}
        ind_seg = {"汽车": "汽车零部件", "家电": "家电", "电子电气": "电子电气", "电动工具": "电动工具", "建材": "建材"}
        custs = self.rows("dim_customer", "cust_id")
        out = []
        months = sorted({k[2] for k in actual})
        for (cid, pid, ym), q in sorted(actual.items()):
            y, m = int(ym[:4]), int(ym[5:])
            made = date(y, m, 1) - timedelta(days=self.rng.randint(5, 12))
            if made < date(2025, 1, 1) or ym not in months:
                continue
            f = round(q * self.rng.uniform(0.75, 1.25), 2)
            price = self.rows("dim_product", "prod_id")[pid]["guide_price"]
            out.append(ev(made, "CLOSED" if made < END - timedelta(days=30) else "APPROVED", cust_id=cid, prod_id=pid,
                          market_segment_id=seg[ind_seg[custs[cid]["industry"]]], unit_code="TON", quantity=f, base_quantity=round(f * 1000, 3),
                          planned_quantity=f, actual_quantity=round(q, 3), qualified_quantity=round(q, 3), rejected_quantity=0.0,
                          currency_code="CNY", exchange_rate=1.0, unit_price=price, amount=round(f * price, 2), _tax_rate=0.0,
                          forecast_bucket=ym, rate_value=round(min(1.0, 1 - abs(f - q) / q), 4) if q else 0.0))
        return out

    def fact_price_adjustment(self) -> list[dict[str, Any]]:
        """2026 年价目表生效:每个在买客户 × 产品一条调价,原价为 2025 年最后成交价,新价为 2026 年首单价。"""
        out = []
        first26, last25 = {}, {}
        for o in sorted(self.rows("fact_sales_order", "order_id").values(), key=lambda o: o["order_date"]):
            k = (o["cust_id"], o["prod_id"])
            if o["order_date"] < "2026-01-01":
                last25[k] = o
            elif k not in first26:
                first26[k] = o
        pricing = {(p["product_id"], p["effective_date"][:4]): p for p in self.rows("dim_pricing", "pricing_id").values()}
        for k, new in sorted(first26.items()):
            old = last25.get(k)
            if not old:
                continue
            pr = pricing[(k[1], "2026")]
            day = date(2025, 12, 15) + timedelta(days=self.rng.randint(0, 10))
            rate = round(new["unit_price"] / old["unit_price"] - 1, 4)
            out.append(ev(day, "CLOSED", cust_id=k[0], prod_id=k[1], pricing_id=pr["pricing_id"], currency_code="CNY", exchange_rate=1.0,
                          unit_price=new["unit_price"], amount=new["unit_price"], _tax_rate=0.0, old_unit_price=old["unit_price"],
                          new_unit_price=new["unit_price"], adjust_rate=rate, effective_date="2026-01-01", price_list_code="PL-2026-STD"))
        return out

    # ───────── 采购 ─────────
    def fact_supplier_contract(self) -> list[dict[str, Any]]:
        out = []
        types = {r["code"]: k for k, r in self.rows("dim_contract_type", "contract_type_id").items()}
        for c in self.rows("fact_contract", "contract_id").values():
            if c["contract_type"] != "purchase" or not self.q("SELECT 1 FROM fact_purchase_order WHERE supplier_id=? LIMIT 1", c["party_id"]):
                continue
            out.append(ev(d_(c["start_date"]), "APPROVED", supplier_id=c["party_id"], contract_type_id=types["FRAME"], unit_code="TON",
                          currency_code="CNY", exchange_rate=1.0, _tax_rate=0.13, _contract=c["contract_id"],
                          contract_no=c["contract_no"], source_document_no=c["contract_no"]))
        return out

    def fact_supplier_contract_line(self) -> list[dict[str, Any]]:
        """采购框架合同明细:合同约定额按该供应商 2025 年各物料采购额分配,约定价为 2025 年采购均价;实际量为合同期内的采购订单量。"""
        out = []
        c_of = {r["contract_no"]: r for r in self.rows("fact_contract", "contract_id").values()}
        mix: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
        act: dict[tuple[int, int], float] = defaultdict(float)
        for pl in self.rows("fact_po_line", "line_id").values():
            po = self.rows("fact_purchase_order", "po_id")[pl["po_id"]]
            if po["po_date"] < "2026-01-01":
                mix[(po["supplier_id"], pl["material_id"])][0] += pl["quantity"]
                mix[(po["supplier_id"], pl["material_id"])][1] += pl["amount"]
            act[(po["supplier_id"], pl["material_id"])] += pl["quantity"]
        for h in self.rows("fact_supplier_contract", "supplier_contract_id").values():
            c = c_of[h["source_document_no"]]
            mats = sorted((m, v) for (s_, m), v in mix.items() if s_ == h["supplier_id"])
            tot = sum(v[1] for _m, v in mats) or 1.0
            for n, (mid, (q, a)) in enumerate(mats, start=1):
                price = round(a / q, 2)
                qty = round(c["total_amount"] * a / tot / price, 1)
                out.append(ev(d_(h["business_date"]), h["status"], supplier_contract_id=h["supplier_contract_id"], material_id=mid, _line_no=n,
                              unit_code="TON", quantity=qty, base_quantity=round(qty * 1000, 3), planned_quantity=qty,
                              actual_quantity=round(act[(h["supplier_id"], mid)], 3), qualified_quantity=round(act[(h["supplier_id"], mid)], 3),
                              rejected_quantity=0.0, currency_code="CNY", exchange_rate=1.0, unit_price=price, amount=round(qty * price, 2),
                              _tax_rate=0.13, requested_date=c["end_date"], promised_date=c["end_date"], source_document_no=h["source_document_no"]))
        return out

    def fact_advance_shipping_notice(self) -> list[dict[str, Any]]:
        """到货预告:供应商在每批到货前 1~2 天发出;一张预告对应一个采购订单当天的到货。"""
        out = []
        groups = defaultdict(list)
        for g in self.rows("fact_goods_receipt", "gr_id").values():
            groups[(g["po_id"], g["receipt_date"])].append(g)
        for (po_id, rd), gs in sorted(groups.items(), key=lambda x: x[0][1]):
            po = self.rows("fact_purchase_order", "po_id")[po_id]
            sent = d_(rd) - timedelta(days=0 if po["po_no"].endswith("R") else self.rng.randint(1, 2))
            out.append(ev(sent, "CLOSED" if d_(rd) < END - timedelta(days=30) else "APPROVED", supplier_id=po["supplier_id"], po_id=po_id,
                          warehouse_id=1, supplier_site_id=self.supplier_site(po["supplier_id"]), unit_code="TON", currency_code="CNY",
                          exchange_rate=1.0, _tax_rate=0.13, freight_amount=0.0, requested_date=po["delivery_date"], promised_date=po["delivery_date"], received_date=rd, signed_date=rd, _grs=[g["gr_id"] for g in gs],
                          source_document_no=po["po_no"]))
        return out

    def fact_asn_line(self) -> list[dict[str, Any]]:
        out = []
        asns = self.rows("fact_advance_shipping_notice", "advance_shipping_notice_id")
        gr_line = {}
        for g in self.rows("fact_goods_receipt", "gr_id").values():
            gr_line[g["gr_id"]] = g
        line_of = {}
        for pl in self.rows("fact_po_line", "line_id").values():
            line_of.setdefault(pl["po_id"], []).append(pl)
        for a in asns.values():
            grs = [g for g in gr_line.values() if g["po_id"] == a["po_id"] and g["receipt_date"] == a["received_date"]]
            for n, g in enumerate(sorted(grs, key=lambda g: g["gr_id"]), start=1):
                pl = self.receipt_po_line(g, line_of[g["po_id"]])
                q = g["quantity"]
                out.append(ev(d_(a["business_date"]), a["status"], advance_shipping_notice_id=a["advance_shipping_notice_id"],
                              material_id=pl["material_id"], po_line_id=pl["line_id"], _line_no=n, unit_code="TON", quantity=q,
                              base_quantity=round(q * 1000, 3), planned_quantity=q, actual_quantity=q,
                              qualified_quantity=q if g["inspection_result"] == "pass" else 0.0,
                              rejected_quantity=0.0 if g["inspection_result"] == "pass" else q, currency_code="CNY", exchange_rate=1.0,
                              unit_price=pl["unit_price"], amount=round(q * pl["unit_price"], 2), _tax_rate=0.13,
                              lot_no=f"SUP{a['supplier_id']:02d}-{a['received_date'].replace('-', '')}-{g['gr_id']}",
                              batch_no=f"RM{pl['material_id']:02d}-{a['received_date'][2:].replace('-', '')}-{g['gr_id']}",
                              movement_type="101 采购收货", stock_status="可用" if g["inspection_result"] == "pass" else "冻结",
                              source_document_no=a["source_document_no"]))
        return out

    def supplier_site(self, sid: int) -> int:
        """供应商的发货基地:该供应商编号最小的地点。"""
        return min(k for k, r in self.rows("dim_supplier_site", "supplier_site_id").items() if r["supplier_id"] == sid)

    def receipt_po_line(self, g: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, Any]:
        """收货单对应的采购订单行:原料批次号 RM{物料}-{收货日}-{收货单} 记录了物料;未投用的批次按数量最接近的行。"""
        if not hasattr(self, "_lot_mat"):
            self._lot_mat = {}
            for (b,) in self.q("SELECT DISTINCT batch_no FROM fact_material_consumption WHERE batch_no NOT LIKE '%-INIT'"):
                self._lot_mat[int(b.rsplit("-", 1)[1])] = int(b[2:4])
        mid = self._lot_mat.get(g["gr_id"])
        if mid is not None:
            for pl in lines:
                if pl["material_id"] == mid:
                    return pl
        return min(lines, key=lambda pl: abs(pl["quantity"] - g["quantity"]))

    def fact_supplier_claim(self) -> list[dict[str, Any]]:
        """来料检验不合格的批次向供应商索赔(按批次货值)。"""
        out = []
        lines = defaultdict(list)
        for pl in self.rows("fact_po_line", "line_id").values():
            lines[pl["po_id"]].append(pl)
        for g in self.rows("fact_goods_receipt", "gr_id").values():
            if g["inspection_result"] != "fail":
                continue
            po = self.rows("fact_purchase_order", "po_id")[g["po_id"]]
            pl = min(lines[g["po_id"]], key=lambda x: abs(x["quantity"] - g["quantity"]))
            day = d_(g["receipt_date"]) + timedelta(days=self.rng.randint(1, 3))
            if day > END:
                continue
            q = g["quantity"]
            out.append(ev(day, status_for(day), supplier_id=po["supplier_id"], material_id=pl["material_id"], unit_code="TON", quantity=q,
                          base_quantity=round(q * 1000, 3), planned_quantity=q, actual_quantity=q, qualified_quantity=0.0, rejected_quantity=q,
                          currency_code="CNY", exchange_rate=1.0, unit_price=round(pl["unit_price"] * 0.1, 4),
                          amount=round(q * round(pl["unit_price"] * 0.1, 4), 2), _tax_rate=0.0, claim_type="质量索赔", claim_amount_basis="按批次货值的10%",
                          source_document_no=g["gr_no"], supplier_response_days=1 + g["gr_id"] % 5,
                          claim_resolution=None if day > END - timedelta(days=20) else "整批退货补货并扣款"))
        return out

    # ───────── 生产 ─────────
    def completed_orders(self) -> list[dict[str, Any]]:
        return sorted((o for o in self.rows("fact_production_order", "order_id").values() if o["start_time"]),
                      key=lambda o: o["start_time"])

    def fact_work_order_operation(self) -> list[dict[str, Any]]:
        """工序执行:按工单产品的工艺路线,在工单开始与结束之间依次排开;产出数量等于工单产量。"""
        out = []
        ops_by_routing = defaultdict(list)
        for op in self.rows("dim_operation", "operation_id").values():
            ops_by_routing[op["routing_id"]].append(op)
        routing_of = {r["product_id"]: r["routing_id"] for r in self.rows("dim_routing", "routing_id").values()}
        for o in self.completed_orders():
            ops = sorted(ops_by_routing[routing_of[o["prod_id"]]], key=lambda x: x["seq_no"])
            st = t_(o["start_time"])
            en = t_(o["end_time"]) if o["end_time"] else WEND
            span = (en - st).total_seconds() / 60
            weights = [max(5.0, op["standard_cycle_min"] or 5.0) for op in ops]
            cursor = st
            done = o["status"] == "已完成"
            q = o["actual_quantity"] or 0.0
            for op, w in zip(ops, weights):
                dur = span * w / sum(weights)
                end_op = cursor + timedelta(minutes=dur)
                fin = end_op <= WEND and done
                rej = round(q * 0.004, 3) if op["op_name"] == "挤出造粒" else 0.0
                out.append(ev(cursor, "CLOSED" if fin else "IN_PROGRESS", order_id=o["order_id"], operation_id=op["operation_id"],
                              line_id=o["line_id"], unit_code="TON", quantity=q, base_quantity=round(q * 1000, 3), planned_quantity=o["planned_quantity"],
                              actual_quantity=q, rejected_quantity=rej, qualified_quantity=round(q - rej, 3), duration_minutes=round(dur, 1),
                              start_time=S.iso(cursor), end_time=S.iso(end_op) if fin else None, order_no=o["order_no"], batch_no=o["batch_no"],
                              operation_seq=op["seq_no"], process_stage=op["op_name"], setup_minutes=float(op["setup_time_min"]),
                              run_minutes=round(max(0.0, dur - float(op["setup_time_min"])), 1),
                              yield_rate=round((q - rej) / q, 4) if q else None, scrap_quantity=rej, rate_value=round((q - rej) / q, 4) if q else 0.0))
                cursor = end_op
        return out

    def fact_work_order_material(self) -> list[dict[str, Any]]:
        """工单发料:完工时按投料记录倒冲(发料量 = 实际投料),原料批次与 108 表投料记录一致,按批次入库单价计价。"""
        out = []
        orders = self.rows("fact_production_order", "order_id")
        for c in self.q("SELECT order_id, material_id, batch_no, planned_qty, actual_qty FROM fact_material_consumption ORDER BY consumption_id"):
            oid, mid, batch, planned, actual = c
            o = orders[oid]
            en = t_(o["end_time"])
            price = self.lot_price(batch, mid)
            out.append(ev(en, "CLOSED" if en.date() < END - timedelta(days=30) else "APPROVED",
                          order_id=oid, material_id=mid, unit_code="TON", quantity=actual, base_quantity=round(actual * 1000, 3),
                          planned_quantity=planned, actual_quantity=actual, qualified_quantity=actual, rejected_quantity=0.0,
                          currency_code="CNY", exchange_rate=1.0, unit_price=price, amount=round(actual * price, 2), cost_amount=round(actual * price, 2),
                          _tax_rate=0.0, order_no=o["order_no"], batch_no=o["batch_no"],
                          lot_no=batch, standard_quantity=planned, usage_variance_rate=round((actual - planned) / planned, 4) if planned else 0.0,
                          issue_type="完工倒冲", source_document_no=o["order_no"]))
        return out

    def fact_lot_trace(self) -> list[dict[str, Any]]:
        """批次追溯:成品批次 ← 投入的原料批次(来自投料记录)。"""
        out = []
        orders = self.rows("fact_production_order", "order_id")
        for oid, mid, batch, actual in self.q("SELECT order_id, material_id, batch_no, actual_qty FROM fact_material_consumption ORDER BY consumption_id"):
            o = orders[oid]
            if o["status"] != "已完成":
                continue
            day = d_(o["end_time"])
            out.append(ev(day, status_for(day), material_id=mid, order_id=oid, unit_code="TON", quantity=actual,
                          base_quantity=round(actual * 1000, 3), planned_quantity=actual, actual_quantity=actual, qualified_quantity=actual,
                          rejected_quantity=0.0, lot_no=batch, batch_no=o["batch_no"], order_no=o["order_no"], movement_type="261 工单发料",
                          stock_status="已消耗", manufacture_date=day.isoformat(), expiry_date=(day + timedelta(days=730)).isoformat(),
                          available_quantity=0.0, reserved_quantity=0.0))
        return out

    def shift_windows(self, day: date) -> list[tuple[str, datetime, datetime]]:
        return [("DAY", at(day, 8), at(day, 16)), ("SWING", at(day, 16), at(day, 24)), ("NIGHT", at(day, 0), at(day, 8))]

    def line_shift_output(self) -> dict[tuple[int, date, str], dict[str, float]]:
        """产线 × 日 × 班次的产量与运行时长:按工单运行区间与班次时段重叠分摊。"""
        if hasattr(self, "_lso"):
            return self._lso
        acc: dict[tuple[int, date, str], dict[str, float]] = defaultdict(lambda: {"qty": 0.0, "run": 0.0, "orders": 0.0})
        for o in self.completed_orders():
            st = t_(o["start_time"])
            en = t_(o["end_time"]) if o["end_time"] else WEND
            span = (en - st).total_seconds() / 3600
            if span <= 0:
                continue
            q = o["actual_quantity"] or 0.0
            day = st.date()
            while day <= en.date() and day <= END:
                for code, s0, s1 in self.shift_windows(day):
                    h = max(0.0, (min(en, s1) - max(st, s0)).total_seconds() / 3600)
                    if h > 0:
                        a = acc[(o["line_id"], day, code)]
                        a["qty"] += q * h / span
                        a["run"] += (o["runtime_hours"] or 0) * h / span
                        a["orders"] += 1
                day += timedelta(days=1)
        self._lso = acc
        return acc

    def shift_id(self, line_id: int, code: str) -> int:
        ws = self.rows("dim_production_line", "line_id")[line_id]["workshop_id"]
        return next(k for k, s in self.rows("dim_shift", "shift_id").items() if s["workshop_id"] == ws and s["code"].endswith(code))

    def fact_shift_report(self) -> list[dict[str, Any]]:
        out = []
        for (lid, day, code), a in sorted(self.line_shift_output().items(), key=lambda x: (x[0][1], x[0][0], x[0][2])):
            if a["qty"] < 0.05:
                continue
            sid = self.shift_id(lid, code)
            s0 = {"DAY": 8, "SWING": 16, "NIGHT": 0}[code]
            q = round(a["qty"], 3)
            rej = round(q * 0.003, 3)
            out.append(ev(at(day, s0 + 7.75), "CLOSED" if day < END - timedelta(days=30) else "APPROVED", line_id=lid, shift_id=sid,
                          unit_code="TON", quantity=q, base_quantity=round(q * 1000, 3), planned_quantity=q, actual_quantity=q,
                          rejected_quantity=rej, qualified_quantity=round(q - rej, 3), duration_minutes=round(a["run"] * 60, 1),
                          start_time=S.iso(at(day, s0)), end_time=S.iso(at(day, s0 + 8)), shift_code=self.rows("dim_shift", "shift_id")[sid]["code"],
                          rate_value=round(min(1.0, a["run"] / 8.0), 4), process_stage="挤出造粒", yield_rate=round((q - rej) / q, 4) if q else None,
                          scrap_quantity=rej, work_date=day.isoformat(), regular_hours=self.rows("dim_shift", "shift_id")[sid]["paid_hours"], overtime_hours=0.0,
                          clock_in=f"{s0:02d}:00", clock_out=f"{(s0 + 8) % 24:02d}:00", attendance_result="正常"))
        return out

    def fact_labor_booking(self) -> list[dict[str, Any]]:
        """工时报工:当班班组长(四班三运转)为本班运行的工单报工,工时 = 该工单在本班的运行时长,合计等于工单运行时长。"""
        out = []
        for o in self.completed_orders():
            st = t_(o["start_time"])
            en = t_(o["end_time"]) if o["end_time"] else WEND
            span = (en - st).total_seconds() / 3600
            day = st.date()
            while day <= en.date() and day <= END:
                for code, s0, s1 in self.shift_windows(day):
                    h = max(0.0, (min(en, s1) - max(st, s0)).total_seconds() / 3600)
                    if h <= 0:
                        continue
                    run = (o["runtime_hours"] or 0) * h / span
                    _c, leader = self.crew(o["line_id"], s0)
                    sid = self.shift_id(o["line_id"], code)
                    out.append(ev(min(en, s1), "CLOSED" if day < END - timedelta(days=30) else "APPROVED", order_id=o["order_id"],
                                  emp_id=leader, shift_id=sid, unit_code="HOUR",
                                  quantity=round(run, 2), base_quantity=round(run * 60, 1), planned_quantity=round(h, 2), actual_quantity=round(run, 2),
                                  qualified_quantity=round(run, 2), rejected_quantity=0.0, duration_minutes=round(run * 60, 1),
                                  start_time=S.iso(max(st, s0)), end_time=S.iso(min(en, s1)), order_no=o["order_no"], batch_no=o["batch_no"],
                                  process_stage="挤出造粒", shift_code=self.rows("dim_shift", "shift_id")[sid]["code"], work_date=day.isoformat(),
                                  regular_hours=round(h, 2), overtime_hours=0.0, clock_in=s0.strftime("%H:%M"),
                                  clock_out=s1.strftime("%H:%M") if s1.hour else "24:00", attendance_result="正常", setup_minutes=0.0,
                                  run_minutes=round(run * 60, 1), scrap_quantity=0.0, employee_no=FILL.emp_no(leader),
                                  supervisor_code=FILL.emp_no(self.F.by_role["生产主管"][(self.rows("dim_production_line", "line_id")[o["line_id"]]["workshop_id"] - 1) % 4])))
                day += timedelta(days=1)
        return out

    def fact_tool_usage(self) -> list[dict[str, Any]]:
        """工装使用:工单在本线运行时使用的工装;使用次数按运行时长,剩余寿命随使用递减。"""
        out = []
        tools = defaultdict(list)
        for tl in self.rows("dim_tool", "tool_id").values():
            tools[tl["line_id"]].append(tl)
        per_cycle = {"螺纹元件组": 30, "切粒刀组": 6, "过滤网组": 1}
        used = defaultdict(int)
        for o in self.completed_orders():
            if o["status"] != "已完成":
                continue
            for tl in sorted(tools[o["line_id"]], key=lambda x: x["tool_id"]):
                cyc = per_cycle[tl["tool_type"]]
                used[tl["tool_id"]] = (used[tl["tool_id"]] + cyc) % tl["total_life"]
                st, en = t_(o["start_time"]), t_(o["end_time"])
                out.append(ev(en, status_for(en.date()), order_id=o["order_id"], tool_id=tl["tool_id"], unit_code="EA", quantity=1.0,
                              base_quantity=1.0, planned_quantity=1.0, actual_quantity=1.0, qualified_quantity=1.0, rejected_quantity=0.0,
                              duration_minutes=round((o["runtime_hours"] or 0) * 60, 1), tool_code=tl["tool_code"], used_cycles=cyc,
                              remaining_life=tl["total_life"] - used[tl["tool_id"]], start_time=S.iso(st), end_time=S.iso(en),
                              order_no=o["order_no"], batch_no=o["batch_no"], process_stage="挤出造粒",
                              run_minutes=round((o["runtime_hours"] or 0) * 60, 1)))
        return out

    def fact_wip_snapshot(self) -> list[dict[str, Any]]:
        """月末在制:月末时刻正在生产的工单,完工比例按已运行时间。"""
        out = []
        for o in self.completed_orders():
            st = t_(o["start_time"])
            en = t_(o["end_time"]) if o["end_time"] else at(END + timedelta(days=30), 0)
            day = date(st.year, st.month, 1)
            while day <= END:
                me = (date(day.year + day.month // 12, day.month % 12 + 1, 1) - timedelta(days=1))
                cut = at(me, 24)
                if st < cut < en:
                    frac = (cut - st) / (en - st)
                    q = round((o["actual_quantity"] if o["status"] == "已完成" else o["planned_quantity"]) * frac, 3)
                    cost = self.rows("dim_product", "prod_id")[o["prod_id"]]["standard_cost"]
                    out.append(ev(me, "CLOSED" if me < END else "APPROVED", order_id=o["order_id"], line_id=o["line_id"], unit_code="TON",
                                  quantity=q, base_quantity=round(q * 1000, 3), planned_quantity=o["planned_quantity"], actual_quantity=q,
                                  qualified_quantity=q, rejected_quantity=0.0, currency_code="CNY", exchange_rate=1.0, unit_price=cost,
                                  amount=round(q * cost, 2), _tax_rate=0.0, order_no=o["order_no"], batch_no=o["batch_no"],
                                  process_stage="挤出造粒", stock_status="在制", movement_type="在制盘存"))
                day = date(day.year + day.month // 12, day.month % 12 + 1, 1)
        return out

    def fact_scrap_event(self) -> list[dict[str, Any]]:
        """报废:每张有投料损耗(开机料、过渡料)的完工工单一条,数量为损耗合计(千克),按产品标准成本计报废损失。"""
        out = []
        orders = self.rows("fact_production_order", "order_id")
        scrap = defaultdict(float)
        for oid, s_ in self.q("SELECT order_id, SUM(scrap_qty) FROM fact_material_consumption GROUP BY order_id"):
            scrap[oid] = s_
        for oid, q in sorted(scrap.items()):
            o = orders[oid]
            if o["status"] != "已完成" or q <= 0:
                continue
            cost = self.rows("dim_product", "prod_id")[o["prod_id"]]["standard_cost"]
            kg = round(q * 1000, 1)
            day = d_(o["end_time"])
            amt = round(kg * cost / 1000, 2)
            out.append(ev(t_(o["end_time"]), status_for(day), order_id=oid, prod_id=o["prod_id"], unit_code="KG", quantity=kg, base_quantity=kg,
                          planned_quantity=kg, actual_quantity=kg, qualified_quantity=0.0, rejected_quantity=kg, currency_code="CNY",
                          exchange_rate=1.0, unit_price=round(cost / 1000, 4), amount=amt, cost_amount=amt, budget_amount=amt, _tax_rate=0.0,
                          order_no=o["order_no"], batch_no=o["batch_no"], scrap_quantity=kg, defect_code="开机料与过渡料",
                          disposition="报废", process_stage="挤出造粒", root_cause_code="法", containment_action=None, due_date=None,
                          closed_date=day.isoformat(), verification_result=None))
        return out

    def fact_rework_order(self) -> list[dict[str, Any]]:
        """返工:检验有不合格项的批次在原产线返工,时间窗与 108 表放行日一致(M.rework_window),由当班班组长执行。"""
        out = []
        orders = self.rows("fact_production_order", "order_id")
        failed = defaultdict(list)
        for oid, prop in self.q("SELECT order_id, property_name FROM fact_quality_inspection WHERE result='不合格' ORDER BY inspection_id"):
            failed[oid].append(prop)
        leaders = defaultdict(list)
        for tm in sorted(self.rows("dim_team", "team_id").values(), key=lambda x: x["team_id"]):
            leaders[tm["line_id"]].append(tm["leader_id"])
        for oid, props in sorted(failed.items()):
            o = orders[oid]
            st, en, _rel = M.rework_window(t_(o["end_time"]), o["runtime_hours"])
            if st.date() > END:
                continue
            q = o["actual_quantity"]
            hours = (en - st).total_seconds() / 3600
            cost = self.rows("dim_product", "prod_id")[o["prod_id"]]["standard_cost"]
            done = en <= WEND
            out.append(ev(st, "CLOSED" if done and en.date() < END - timedelta(days=30) else ("APPROVED" if done else "IN_PROGRESS"),
                          order_id=oid, emp_id=self.crew(o["line_id"], st)[1], unit_code="TON", quantity=q, base_quantity=round(q * 1000, 3),
                          planned_quantity=q, actual_quantity=q, qualified_quantity=q if done else 0.0, rejected_quantity=0.0, currency_code="CNY",
                          exchange_rate=1.0, unit_price=round(cost * 0.12, 2), amount=round(q * cost * 0.12, 2), _tax_rate=0.0,
                          duration_minutes=round(hours * 60, 1), start_time=S.iso(st), end_time=S.iso(en) if done else None,
                          order_no=o["order_no"], batch_no=o["batch_no"], defect_code=f"{props[0]}不合格", disposition="返工",
                          process_stage="掺混调整后重新挤出", root_cause_code=FILL.ROOT_CAUSE.get(props[0], "法"),
                          containment_action="隔离该批次,冻结库存", yield_rate=1.0 if done else None, scrap_quantity=0.0,
                          due_date=en.date().isoformat(), closed_date=en.date().isoformat() if done else None,
                          verification_result="复检合格" if done else None))
        return out

    def fact_process_parameter_record(self) -> list[dict[str, Any]]:
        """工艺参数:每张完工工单开机 1 小时后首检、此后每 24 小时记录挤出机 5 项关键参数(按本车间树脂的设定),
        开机首检时各辅机记录一项参数;读数多数落在控制限内,偶有超规格。"""
        out = []
        plan = {r["code"]: r for r in self.dim_plans()["dim_process_parameter"]}
        rows = {r["code"]: k for k, r in self.rows("dim_process_parameter", "process_parameter_id").items()}
        dims = self.rows("dim_process_parameter", "process_parameter_id")
        eq_by_line = defaultdict(list)
        for e in self.rows("dim_equipment", "equipment_id").values():
            eq_by_line[e["line_id"]].append(e)

        def record(o: dict[str, Any], eq: dict[str, Any], code: str, tm: datetime) -> None:
            p = dims[rows[code]]
            lo, hi = p["lower_control_limit"], p["upper_control_limit"]
            v = self.rng.gauss((lo + hi) / 2, (hi - lo) / 6.5)
            if self.rng.random() < 0.004:                       # 偶发工艺波动,超出规格限
                v = p["upper_spec_limit"] + (hi - lo) * self.rng.uniform(0.05, 0.3)
            ok = p["lower_spec_limit"] <= v <= p["upper_spec_limit"]
            out.append(ev(tm, "CLOSED" if tm.date() < END - timedelta(days=30) else "APPROVED", order_id=o["order_id"],
                          equipment_id=eq["equipment_id"], process_parameter_id=p["process_parameter_id"],
                          measured_value=round(v, 3), nominal_value=p["nominal_value"], lower_limit=p["lower_spec_limit"],
                          upper_limit=p["upper_spec_limit"], characteristic_code=p["code"], result_code="合格" if ok else "不合格",
                          defect_code=None if ok else f"{plan[code]['_base']}超规格", disposition=None if ok else "调整工艺",
                          order_no=o["order_no"], batch_no=o["batch_no"], process_stage=p["process_stage"], rate_value=1.0 if ok else 0.0))
        for o in self.completed_orders():
            if o["status"] != "已完成":
                continue
            st, en = t_(o["start_time"]), t_(o["end_time"])
            ws = self.rows("dim_production_line", "line_id")[o["line_id"]]["workshop_id"]
            eqs = {e["type_id"]: e for e in eq_by_line[o["line_id"]]}
            key = [c for c, r in plan.items() if r["type_id"] == 1 and r["_base"] in KEY_PARAMS
                   and (r["_ws"] == ws or r["_prod"] == o["prod_id"])]
            for t_id, e in sorted(eqs.items()):
                if t_id != 1:
                    first = min(c for c, r in plan.items() if r["type_id"] == t_id)
                    record(o, e, first, st + 1 * H)
            tm = st + 1 * H
            while tm < en:
                for c in key:
                    record(o, eqs[1], c, tm)
                tm += 24 * H
        return out

    def fact_legacy_material_issue(self) -> list[dict[str, Any]]:
        """遗留系统领料:2025 年上半年一厂 1、2 号线与 4 号线仍在旧系统过账发料(物料编码按旧码 mat_ref 记录)。"""
        out = []
        orders = self.rows("fact_production_order", "order_id")
        mats = self.rows("dim_material", "material_id")
        for _cid, oid, mid, batch, actual in self.q("SELECT consumption_id, order_id, material_id, batch_no, actual_qty FROM fact_material_consumption ORDER BY consumption_id"):
            o = orders[oid]
            en = t_(o["end_time"])
            if en.date() >= date(2025, 7, 1) or o["line_id"] not in (1, 2, 4):
                continue
            price = self.lot_price(batch, mid)
            out.append(ev(en, "CLOSED", order_id=oid, unit_code="TON", quantity=actual, base_quantity=round(actual * 1000, 3),
                          planned_quantity=actual, actual_quantity=actual, qualified_quantity=actual, rejected_quantity=0.0, currency_code="CNY",
                          exchange_rate=1.0, unit_price=price, amount=round(actual * price, 2), cost_amount=round(actual * price, 2), _tax_rate=0.0,
                          mat_ref=mats[mid]["material_code"], lot_no=batch, order_no=o["order_no"], batch_no=o["batch_no"],
                          movement_type="261 工单发料", issue_type="完工倒冲", _material_id=mid))
        return out

    # ───────── 库存 ─────────
    RAW_WH = {1: 1, 2: 4, 3: 6}                                 # 工厂 → 原料仓

    def lot_price(self, lot: str, mid: int) -> float:
        """原料批次的入库单价:收货批次取采购订单行单价,期初批次取物料主数据单价(与 108 表成本计算一致)。"""
        if not hasattr(self, "_lot_price"):
            line_of_po = {pl["po_id"]: pl for pl in self.rows("fact_po_line", "line_id").values()}
            self._lot_price = {g["gr_id"]: line_of_po[g["po_id"]]["unit_price"] for g in self.rows("fact_goods_receipt", "gr_id").values()}
        if lot.endswith("-INIT"):
            return float(self.rows("dim_material", "material_id")[mid]["unit_price"])
        return float(self._lot_price[int(lot.rsplit("-", 1)[1])])

    def order_factory(self, o: dict[str, Any]) -> int:
        return M.WORKSHOPS[self.rows("dim_production_line", "line_id")[o["line_id"]]["workshop_id"] - 1][2]

    def material_flows(self) -> list[tuple[date, int, int, float, str, str, str, float]]:
        """原料流水 (日期, 仓库, 物料, 数量变动, 移动类型, 单据, 原料批次, 批次单价)。
        108 表仿真中原料统一收在一厂原料仓;二厂、合肥的工单在投料当天(完工日)从一厂原料仓按单调拨到本厂原料仓再发料。"""
        if hasattr(self, "_mflows"):
            return self._mflows
        flows: list[tuple[date, int, int, float, str, str, str, float]] = []
        lines = defaultdict(list)
        for pl in self.rows("fact_po_line", "line_id").values():
            lines[pl["po_id"]].append(pl)
        for mid, q in self.q("SELECT material_id, SUM(actual_qty) FROM fact_material_consumption WHERE batch_no LIKE '%-INIT' GROUP BY material_id"):
            lot = f"RM{mid:02d}-INIT"
            flows.append((date(2025, 1, 1), 1, mid, q, "期初", "INIT", lot, self.lot_price(lot, mid)))
        for g in self.rows("fact_goods_receipt", "gr_id").values():
            if g["inspection_result"] != "pass":
                continue
            pl = self.receipt_po_line(g, lines[g["po_id"]])
            lot = f"RM{pl['material_id']:02d}-{d_(g['receipt_date']).strftime('%y%m%d')}-{g['gr_id']}"
            flows.append((d_(g["receipt_date"]), 1, pl["material_id"], g["quantity"], "101 采购收货", g["gr_no"], lot, float(pl["unit_price"])))
        orders = self.rows("fact_production_order", "order_id")
        need = defaultdict(float)
        moved: dict[tuple[date, int, int, str], float] = defaultdict(float)
        for oid, mid, lot, actual in self.q("SELECT order_id, material_id, batch_no, actual_qty FROM fact_material_consumption ORDER BY consumption_id"):
            o = orders[oid]
            day, fac = d_(o["end_time"]), self.order_factory(o)          # 108 表仿真在完工日按配方扣料
            wh = self.RAW_WH[fac]
            if fac != 1:
                need[(day, wh, mid)] += actual
                moved[(day, wh, mid, lot)] += actual
            flows.append((day, wh, mid, -actual, "261 工单发料", o["order_no"], lot, self.lot_price(lot, mid)))
        self._transfer_need = need
        for (day, wh, mid, lot), q in moved.items():
            doc = f"TO{day.strftime('%Y%m%d')}-W{wh}"
            flows.append((day, 1, mid, -q, "301 仓间调拨出", doc, lot, self.lot_price(lot, mid)))
            flows.append((day, wh, mid, q, "301 仓间调拨入", doc, lot, self.lot_price(lot, mid)))
        flows.sort(key=lambda x: (x[0], -x[3]))                # 同一天先入后出
        self._mflows = flows
        return flows

    def fact_inventory_movement(self) -> list[dict[str, Any]]:
        """库存移动:原料收货、仓间调拨与工单发料(来自 108 表收货与投料),逐笔与月末快照勾稽。"""
        out = []
        locs = defaultdict(list)
        for l in self.rows("dim_storage_location", "location_id").values():
            locs[l["warehouse_id"]].append(l["location_id"])
        codes = {l["location_id"]: l["location_code"] for l in self.rows("dim_storage_location", "location_id").values()}
        for day, wh, mid, q, kind, doc, lot, price in self.material_flows():
            if kind == "期初":
                continue
            loc = sorted(locs[wh])[(mid - 1) % len(locs[wh])]
            amt = round(abs(q) * price, 2)
            out.append(ev(day, status_for(day), material_id=mid, warehouse_id=wh, location_id=loc, unit_code="TON", quantity=round(abs(q), 4),
                          base_quantity=round(abs(q) * 1000, 3), planned_quantity=round(abs(q), 4), actual_quantity=round(abs(q), 4),
                          qualified_quantity=round(abs(q), 4), rejected_quantity=0.0, currency_code="CNY", exchange_rate=1.0,
                          unit_price=price, amount=amt, cost_amount=amt, _tax_rate=0.0, lot_no=lot, batch_no=lot, bin_code=codes[loc],
                          movement_type=kind, source_document_no=doc, stock_status="可用", available_quantity=round(abs(q), 4),
                          reserved_quantity=0.0, _sign=1 if q > 0 else -1))
        return out

    def fact_inventory_snapshot(self) -> list[dict[str, Any]]:
        """月末原料库存快照:由期初 + 收货 − 投料逐日累计得到,与库存移动一致。"""
        out = []
        bal = defaultdict(float)
        flows = self.material_flows()
        mats = self.rows("dim_material", "material_id")
        months = []
        d = date(2025, 1, 31)
        while d <= END:
            months.append(d)
            d = date(d.year + d.month // 12, d.month % 12 + 1, 1)
            d = date(d.year + d.month // 12, d.month % 12 + 1, 1) - timedelta(days=1)
        i = 0
        for me in months:
            while i < len(flows) and flows[i][0] <= me:
                bal[(flows[i][1], flows[i][2])] += flows[i][3]
                i += 1
            for wh, mid in sorted({(f[1], f[2]) for f in flows}):
                q = round(bal[(wh, mid)], 3)
                assert q > -0.01, f"原料库存为负: 仓库{wh} 物料{mid} {me} {q}"
                q = max(0.0, q)
                out.append(ev(me, "CLOSED" if me < END else "APPROVED", material_id=mid, warehouse_id=wh, unit_code="TON", quantity=q,
                              base_quantity=round(q * 1000, 3), planned_quantity=mats[mid]["safety_stock"], actual_quantity=q,
                              qualified_quantity=q, rejected_quantity=0.0, currency_code="CNY", exchange_rate=1.0,
                              unit_price=mats[mid]["unit_price"], amount=round(q * mats[mid]["unit_price"], 2), _tax_rate=0.0,
                              movement_type="月末盘存", stock_status="可用", available_quantity=q, reserved_quantity=0.0))
        self._snap = {(r["warehouse_id"], r["material_id"], r["_date"]): r["quantity"] for r in out}
        return out

    def fact_cycle_count(self) -> list[dict[str, Any]]:
        out = []
        keepers = self.employees(11)
        for me in sorted({r["_date"] for r in self.fact_inventory_snapshot()}):
            if me.month % 3:
                continue
            out.append(ev(me, "CLOSED" if me < END else "APPROVED", warehouse_id=1, emp_id=self.rng.choice(keepers), unit_code="TON",
                          duration_minutes=float(self.rng.choice([240, 300, 360])), count_method="ABC 循环盘点"))
        return out

    def fact_cycle_count_line(self) -> list[dict[str, Any]]:
        """盘点明细:账面量取盘点日月末快照,实盘量有小幅差异。"""
        out = []
        if not hasattr(self, "_snap"):
            self.fact_inventory_snapshot()
        snap = self._snap
        mats = self.rows("dim_material", "material_id")
        for cc in self.rows("fact_cycle_count", "cycle_count_id").values():
            day = d_(cc["business_date"])
            for n, mid in enumerate(sorted(mats), start=1):
                book = snap.get((1, mid, day), 0.0)
                counted = round(book * (1 + self.rng.choice([0, 0, 0, 0, -0.002, 0.001])), 3)
                var = round(counted - book, 3)
                price = mats[mid]["unit_price"]
                out.append(ev(day, cc["status"], cycle_count_id=cc["cycle_count_id"], material_id=mid, _line_no=n, unit_code="TON",
                              quantity=counted, base_quantity=round(counted * 1000, 3), planned_quantity=book, actual_quantity=counted,
                              qualified_quantity=counted, rejected_quantity=0.0, currency_code="CNY", exchange_rate=1.0, unit_price=price,
                              amount=round(counted * price, 2), _tax_rate=0.0, book_quantity=book, counted_quantity=counted,
                              variance_quantity=var, recount_required=int(abs(var) > 0.01), movement_type="701 盘点调整",
                              stock_status="可用", available_quantity=counted, reserved_quantity=0.0, count_method="ABC 循环盘点"))
        return out

    def fact_transfer_order(self) -> list[dict[str, Any]]:
        """调拨:二厂、合肥工厂的工单在投料当天,从一厂原料仓按单调拨到本厂原料仓(量 = 当天投料)。"""
        self.material_flows()
        out = []
        for day, wh in sorted({(k[0], k[1]) for k in self._transfer_need}):
            out.append(ev(at(day, 7.5), status_for(day), source_warehouse_id=1, target_warehouse_id=wh, unit_code="TON", currency_code="CNY",
                          exchange_rate=1.0, _tax_rate=0.0, movement_type="301 仓间调拨", stock_status="已收货",
                          source_document_no=f"TO{day.strftime('%Y%m%d')}-W{wh}"))
        return out

    def fact_transfer_order_line(self) -> list[dict[str, Any]]:
        out = []
        self.material_flows()
        need = self._transfer_need
        mats = self.rows("dim_material", "material_id")
        for h in self.rows("fact_transfer_order", "transfer_order_id").values():
            day = d_(h["business_date"])
            items = sorted((mid, q) for (w, wh, mid), q in need.items() if w == day and wh == h["target_warehouse_id"])
            for n, (mid, q) in enumerate(items, start=1):
                q = round(q, 4)
                out.append(ev(day, h["status"], transfer_order_id=h["transfer_order_id"], material_id=mid, _line_no=n, unit_code="TON",
                              quantity=q, base_quantity=round(q * 1000, 3), planned_quantity=q, actual_quantity=q, qualified_quantity=q,
                              rejected_quantity=0.0, currency_code="CNY", exchange_rate=1.0, unit_price=mats[mid]["unit_price"],
                              amount=round(q * mats[mid]["unit_price"], 2), _tax_rate=0.0, movement_type="301 仓间调拨", stock_status="已收货",
                              available_quantity=q, reserved_quantity=q, source_document_no=f"TO{day.strftime('%Y%m%d')}-W{h['target_warehouse_id']}"))
        return out

    def fact_batch_quality_hold(self) -> list[dict[str, Any]]:
        """质量冻结:有不合格项的成品批次完工即冻结,返工复检后放行(放行日与 108 表一致);让步接收的当天评审放行。"""
        out = []
        res = defaultdict(list)
        for oid, prop, r in self.q("SELECT order_id, property_name, result FROM fact_quality_inspection ORDER BY inspection_id"):
            res[oid].append((prop, r))
        orders = self.rows("fact_production_order", "order_id")
        for oid, items in sorted(res.items()):
            bad = [p for p, r in items if r == "不合格"]
            soft = [p for p, r in items if r == "让步接收"]
            if not bad and not soft:
                continue
            o = orders[oid]
            day = d_(o["end_time"])
            hard = bool(bad)
            release = M.rework_window(t_(o["end_time"]), o["runtime_hours"])[2] if hard else day
            q = o["actual_quantity"]
            prop = (bad or soft)[0]
            out.append(ev(t_(o["end_time"]), "CLOSED" if release <= END else "IN_PROGRESS", prod_id=o["prod_id"],
                          emp_id=self.F.pick("质量工程师", o["batch_no"]), unit_code="TON",
                          quantity=q, base_quantity=round(q * 1000, 3), planned_quantity=q, actual_quantity=q, qualified_quantity=0.0 if hard else q,
                          rejected_quantity=q if hard else 0.0, duration_minutes=float((release - day).days * 1440 if hard else 240), batch_no=o["batch_no"],
                          lot_no=o["batch_no"], stock_status="冻结", defect_code=f"{prop}{'不合格' if hard else '让步接收'}",
                          disposition="返工" if hard else "让步接收", closed_date=release.isoformat() if release <= END else None,
                          due_date=release.isoformat(), movement_type="质量冻结", root_cause_code=FILL.ROOT_CAUSE.get(prop, "法"),
                          containment_action="隔离该批次,冻结库存", verification_result=("复检合格" if hard else "评审同意让步") if release <= END else None))
        return out

    def char_id(self, pid: int, prop: str) -> int:
        return next(k for k, r in self.rows("dim_quality_characteristic", "quality_characteristic_id").items()
                    if r["prod_id"] == pid and r["name"].split("-")[0] == prop)

    def fact_inspection_lot(self) -> list[dict[str, Any]]:
        """检验批 = 完工工单;批结论取最差项,表头特性取决定结论的那一项(全部合格取第一项)。"""
        out = []
        res = defaultdict(list)
        for prop, oid, val, r in self.q("SELECT property_name, order_id, actual_value, result FROM fact_quality_inspection ORDER BY inspection_id"):
            res[oid].append((prop, val, r))
        rank = {"不合格": 2, "让步接收": 1, "合格": 0}
        for o in self.rows("fact_production_order", "order_id").values():
            if o["status"] != "已完成":
                continue
            items = res[o["order_id"]]
            worst = max(items, key=lambda x: rank[x[2]])
            verdict = worst[2]
            ch = self.rows("dim_quality_characteristic", "quality_characteristic_id")[self.char_id(o["prod_id"], worst[0])]
            q = o["actual_quantity"]
            day = d_(o["end_time"])
            out.append(ev(t_(o["end_time"]), status_for(day), order_id=o["order_id"], prod_id=o["prod_id"], unit_code="TON", quantity=q,
                          base_quantity=round(q * 1000, 3), planned_quantity=o["planned_quantity"], actual_quantity=q,
                          qualified_quantity=0.0 if verdict == "不合格" else q, rejected_quantity=q if verdict == "不合格" else 0.0,
                          result_code=verdict, characteristic_code=ch["code"], measured_value=_num(worst[1]),
                          nominal_value=ch["nominal_value"], lower_limit=ch["lower_spec_limit"], upper_limit=ch["upper_spec_limit"],
                          inspection_no=f"QC{o['batch_no']}", batch_no=o["batch_no"], lot_no=o["batch_no"], sample_size=len(items) * 5,
                          stock_status="可用" if verdict != "不合格" else "冻结",
                          rate_value=round(sum(r == "合格" for _p, _v, r in items) / len(items), 4) if items else 1.0, _verdict=verdict))
        return out

    def fact_inspection_result(self) -> list[dict[str, Any]]:
        """检验结果 = 108 表质检记录逐项映射到质量特性,判定一致。"""
        out = []
        lot_of = {r["order_id"]: r for r in self.rows("fact_inspection_lot", "inspection_lot_id").values()}
        methods = {r["name"]: k for k, r in self.rows("dim_inspection_method", "inspection_method_id").items()}
        meth_for = {"拉伸强度": "万能试验机拉伸", "缺口冲击强度": "悬臂梁冲击", "熔体流动速率": "熔指仪法", "密度": "密度天平",
                    "灰分": "马弗炉灼烧", "阻燃等级(1.6mm)": "垂直燃烧"}
        n_by_lot = defaultdict(int)
        for _qid, oid, prop, _std, val, res, day in self.q("SELECT inspection_id, order_id, property_name, standard_value, actual_value, result, inspection_date FROM fact_quality_inspection ORDER BY inspection_id"):
            lot = lot_of.get(oid)
            if not lot:
                continue
            n_by_lot[oid] += 1
            pid = self.rows("fact_production_order", "order_id")[oid]["prod_id"]
            ch = self.rows("dim_quality_characteristic", "quality_characteristic_id")[self.char_id(pid, prop)]
            v = _num(val)
            out.append(ev(d_(day), lot["status"], inspection_lot_id=lot["inspection_lot_id"],
                          quality_characteristic_id=ch["quality_characteristic_id"], inspection_method_id=methods[meth_for[prop]],
                          _line_no=n_by_lot[oid], unit_code="EA", quantity=5.0, base_quantity=5.0, planned_quantity=5.0, actual_quantity=5.0,
                          qualified_quantity=5.0 if res != "不合格" else 0.0, rejected_quantity=0.0 if res != "不合格" else 5.0,
                          measured_value=v, nominal_value=ch["nominal_value"], lower_limit=ch["lower_spec_limit"], upper_limit=ch["upper_spec_limit"],
                          characteristic_code=ch["code"], result_code=res, sample_size=5, inspection_no=f"QC{lot['batch_no']}-{n_by_lot[oid]}",
                          defect_code=None if res == "合格" else f"{prop}{res}", disposition=None if res == "合格" else {"让步接收": "让步接收", "不合格": "返工"}[res],
                          inspector_code=FILL.emp_no(self.F.pick("质检员", lot["batch_no"])), rate_value=1.0 if res == "合格" else 0.0))
        return out

    def fact_nonconformance(self) -> list[dict[str, Any]]:
        """不合格记录:每个不合格或让步接收的检验项一条。"""
        out = []
        reasons = {r["code"]: k for k, r in self.rows("dim_reason_code", "reason_code_id").items()}
        for r in self.rows("fact_inspection_result", "inspection_result_id").values():
            if r["result_code"] == "合格":
                continue
            lot = self.rows("fact_inspection_lot", "inspection_lot_id")[r["inspection_lot_id"]]
            day = d_(r["business_date"])
            q = lot["actual_quantity"] if r["result_code"] == "不合格" else 0.0
            cost = self.rows("dim_product", "prod_id")[lot["prod_id"]]["standard_cost"]
            out.append(ev(day, status_for(day), inspection_lot_id=lot["inspection_lot_id"], reason_code_id=reasons["QUALITY"], unit_code="TON",
                          quantity=lot["actual_quantity"], base_quantity=round(lot["actual_quantity"] * 1000, 3), planned_quantity=lot["actual_quantity"],
                          actual_quantity=lot["actual_quantity"], qualified_quantity=round(lot["actual_quantity"] - q, 3), rejected_quantity=q,
                          currency_code="CNY", exchange_rate=1.0, unit_price=round(cost * 0.12, 2) if q else 0.0,
                          amount=round(lot["actual_quantity"] * round(cost * 0.12, 2), 2) if q else 0.0, _tax_rate=0.0,   # 返工损失估算;让步接收无损失
                          defect_code=r["defect_code"], disposition=r["disposition"], characteristic_code=r["characteristic_code"],
                          measured_value=r["measured_value"], nominal_value=r["nominal_value"], lower_limit=r["lower_limit"],
                          upper_limit=r["upper_limit"], result_code=r["result_code"], inspection_no=r["inspection_no"],
                          root_cause_code=FILL.ROOT_CAUSE.get(str(r["defect_code"]).replace("不合格", "").replace("让步接收", ""), "法"),
                          containment_action="隔离该批次,冻结库存" if r["result_code"] == "不合格" else "评审后让步放行",
                          inspector_code=r["inspector_code"], verification_result=None,
                          reason_code="QUALITY", _severity=3 if r["result_code"] == "不合格" else 1))
        return out

    def fact_corrective_action(self) -> list[dict[str, Any]]:
        """纠正措施:每条不合格记录一条(让步接收的做简单原因分析),缺陷与不合格记录一致。"""
        out = []
        for n in self.rows("fact_nonconformance", "nonconformance_id").values():
            day = d_(n["business_date"]) + timedelta(days=self.rng.randint(1, 3))
            if day > END:
                continue
            due = day + timedelta(days=14 if n["result_code"] == "不合格" else 7)
            closed = due - timedelta(days=self.rng.randint(0, 5)) if due < END else None
            out.append(ev(day, "CLOSED" if closed else "IN_PROGRESS", nonconformance_id=n["nonconformance_id"],
                          emp_id=self.F.pick("质量工程师", n["nonconformance_id"]),
                          duration_minutes=float(self.rng.choice([60, 120, 240])), currency_code="CNY", exchange_rate=1.0,
                          amount=float(self.rng.choice([0, 500, 1200, 3000])), _tax_rate=0.0, defect_code=n["defect_code"],
                          disposition=n["disposition"], due_date=due.isoformat(), closed_date=closed.isoformat() if closed else None,
                          root_cause_code=n["root_cause_code"], containment_action=n["containment_action"],
                          verification_result=("有效" if self.rng.random() < 0.9 else "部分有效") if closed else None,
                          rate_value=1.0 if closed else 0.0))
        for r in out:
            r["unit_price"] = r["amount"]
        return out

    def fact_customer_complaint(self) -> list[dict[str, Any]]:
        """客户投诉:每笔退货前客户先投诉(按退货原因分类);另有实际晚于承诺交期的交付引起的交付投诉(不退货)。"""
        out = []
        kind = {"产品质量": "质量问题", "颜色": "质量问题", "包装": "包装破损", "规格": "发货差错", "客户订单": "订单变更"}
        for r in self.rows("fact_return", "return_id").values():
            o = self.rows("fact_sales_order", "order_id")[r["order_id"]]
            ctype = next(v for k, v in kind.items() if r["reason"].startswith(k))
            day = d_(r["return_date"]) - timedelta(days=1 + r["return_id"] % 4)
            out.append(ev(day, status_for(day), cust_id=o["cust_id"], prod_id=o["prod_id"], order_id=o["order_id"], unit_code="TON",
                          quantity=r["quantity"], base_quantity=round(r["quantity"] * 1000, 3), planned_quantity=r["quantity"],
                          actual_quantity=r["quantity"], qualified_quantity=0.0, rejected_quantity=r["quantity"], currency_code="CNY",
                          exchange_rate=1.0, unit_price=o["unit_price"], amount=r["amount"], _tax_rate=0.0, complaint_type=ctype,
                          defect_code=r["reason"], disposition="退货", compensation_amount=0.0, closed_date=r["return_date"],
                          response_due_date=(day + timedelta(days=2)).isoformat()))
        promised = {}
        for l in self.rows("fact_sales_order_line", "sales_order_line_id").values():
            promised.setdefault(l["order_id"], l["promised_delivery_date"])
        late = [dl for dl in self.rows("fact_delivery", "delivery_id").values()
                if dl["delivery_date"] > promised[dl["order_id"]] and (d_(dl["delivery_date"]) - d_(promised[dl["order_id"]])).days >= 5]
        for dl in sorted(late, key=lambda x: x["delivery_id"])[::6]:         # 晚交 5 天以上的约六分之一客户会投诉
            o = self.rows("fact_sales_order", "order_id")[dl["order_id"]]
            day = d_(dl["delivery_date"]) - timedelta(days=2)
            out.append(ev(day, status_for(day), cust_id=o["cust_id"], prod_id=o["prod_id"], order_id=o["order_id"], unit_code="TON",
                          quantity=0.0, base_quantity=0.0, planned_quantity=0.0, actual_quantity=0.0, qualified_quantity=0.0,
                          rejected_quantity=0.0, currency_code="CNY", exchange_rate=1.0, unit_price=o["unit_price"], amount=0.0, _tax_rate=0.0,
                          complaint_type="交付延迟", defect_code=None, disposition="催交", compensation_amount=0.0,
                          response_due_date=(day + timedelta(days=2)).isoformat(), closed_date=dl["delivery_date"]))
        return out

    def fact_supplier_quality_issue(self) -> list[dict[str, Any]]:
        """供应商质量问题:每次来料检验不合格一条,索赔类字段与对应索赔单一致。"""
        out = []
        for c in self.rows("fact_supplier_claim", "supplier_claim_id").values():
            day = d_(c["business_date"]) - timedelta(days=1)
            out.append(ev(day, c["status"], supplier_id=c["supplier_id"], material_id=c["material_id"], unit_code="TON", quantity=c["quantity"],
                          base_quantity=c["base_quantity"], planned_quantity=c["quantity"], actual_quantity=c["quantity"], qualified_quantity=0.0,
                          rejected_quantity=c["quantity"], currency_code="CNY", exchange_rate=1.0, unit_price=c["unit_price"],
                          amount=c["amount"], _tax_rate=0.0, defect_code="来料检验不合格", disposition="退货",
                          claim_type=c["claim_type"], claim_resolution=c["claim_resolution"], claim_amount_basis=c["claim_amount_basis"],
                          supplier_response_days=c["supplier_response_days"], root_cause_code="料", containment_action="整批拒收,退回供应商",
                          rate_value=0.0, source_document_no=c["source_document_no"]))
        return out

    def fact_gauge_calibration(self) -> list[dict[str, Any]]:
        """在线仪表校准:每只压力、温度传感器每半年校准一次,误差在允差内为合格。"""
        out = []
        for s_ in self.rows("dim_sensor", "sensor_id").values():
            if s_["sensor_type"] not in ("温度", "压力"):
                continue
            for k in range(3):
                day = date(2025, 3, 1) + timedelta(days=182 * k + (s_["sensor_id"] % 60))
                if day > END:
                    continue
                tol = round(max(float(s_["accuracy"]) * 2, 0.05), 4)
                err = round(self.rng.gauss(0, tol / 2.5), 4)
                ok = abs(err) <= tol
                std = 250.0 if s_["sensor_type"] == "温度" else 8.0
                out.append(ev(day, status_for(day), equipment_id=s_["equipment_id"], sensor_id=s_["sensor_id"],
                              duration_minutes=float(self.rng.choice([30, 45, 60])), gauge_code=s_["sensor_code"], standard_value=std,
                              measured_value=round(std + err, 4), error_value=err, tolerance=tol, result_code="合格" if ok else "不合格",
                              next_due_date=(day + timedelta(days=182)).isoformat(), calibration_type="内校",
                              certificate_no=f"JL-{day.year}-{s_['sensor_id']:04d}{k}", rate_value=1.0 if ok else 0.0))
        return out

    # ───────── 设备与能源 ─────────
    def fact_equipment_event(self) -> list[dict[str, Any]]:
        """设备事件:每次停机记一条停机事件(含原因与时长)。"""
        out = []
        reasons = {r["code"]: k for k, r in self.rows("dim_reason_code", "reason_code_id").items()}
        mapping = {1: "EQUIPMENT", 2: "PROCESS", 3: "EQUIPMENT", 4: "MATERIAL", 5: "QUALITY", 6: "ENVIRONMENT", 7: "OTHER", 8: "PROCESS"}
        kind = {1: ("EV-FAULT", "故障维修"), 2: ("EV-SETUP", "换产"), 3: ("EV-PM", "预防性维护"), 4: ("EV-STOP", "待料"),
                5: ("EV-STOP", "工艺调整"), 6: ("EV-STOP", "停电"), 7: ("EV-STOP", "人员调配"), 8: ("EV-STOP", "滤网更换")}
        for dn in self.rows("fact_downtime", "downtime_id").values():
            st = t_(dn["start_time"])
            ek, mt = kind[dn["reason_code"]]
            out.append(ev(st, status_for(st.date()), equipment_id=dn["equipment_id"], reason_code_id=reasons[mapping[dn["reason_code"]]],
                          reason_code=mapping[dn["reason_code"]], duration_minutes=round(dn["duration_hours"] * 60, 1), event_code=ek,
                          maintenance_type=mt, failure_mode=dn["description"] if dn["reason_code"] == 1 else "无故障",
                          downtime_minutes=round(dn["duration_hours"] * 60, 1), start_time=dn["start_time"], end_time=dn["end_time"],
                          result_code="完成", rate_value=1.0))
        return out

    def sensor_series(self) -> dict[int, list[tuple[datetime, float]]]:
        if hasattr(self, "_series"):
            return self._series
        s = defaultdict(list)
        for sid, ts, v in self.q("SELECT sensor_id, timestamp, value FROM fact_iot_data ORDER BY timestamp"):
            s[sid].append((t_(ts), v))
        self._series = s
        return s

    def fact_condition_monitoring(self) -> list[dict[str, Any]]:
        """状态监测 = 传感器采集数据(108 表 IoT)按设备落表,阈值取传感器的正常区间与报警限。"""
        out = []
        sensors = self.rows("dim_sensor", "sensor_id")
        for sid, series in sorted(self.sensor_series().items()):
            s_ = sensors[sid]
            lo, alarm = self.sensor_limits(s_)
            for ts, v in series:
                low = v < lo
                over = v > alarm
                hi_ = round(max(0.0, min(1.0, 1 - max(0.0, v - lo) / max(alarm - lo, 1e-6) * 0.4 - (0.3 if low else 0.0))), 3)
                out.append(ev(ts, "CLOSED", equipment_id=s_["equipment_id"], sensor_id=sid, reading_value=v, reading_unit=s_["unit"],
                              lower_threshold=lo, upper_threshold=alarm, sensor_code=s_["sensor_code"], health_index=hi_,
                              event_code="EV-ALARM" if over else "EV-LOW" if low else "EV-RUN",
                              failure_mode="读数超报警限" if over else "读数低于正常区间" if low else "无故障",
                              maintenance_type="状态监测", downtime_minutes=0.0, remaining_life_hours=None if (over or low) else round(hi_ * 4000, 0),
                              rate_value=0.0 if (over or low) else 1.0))
        return out

    def sensor_limits(self, s_: dict[str, Any]) -> tuple[float, float]:
        """传感器的正常区间下限与报警上限,与 108 表生成传感器数据时的取值一致(M.sensor_range)。"""
        eq = self.rows("dim_equipment", "equipment_id")[s_["equipment_id"]]
        ws = self.rows("dim_production_line", "line_id")[eq["line_id"]]["workshop_id"]
        lo, _hi, alarm = M.sensor_range(eq["type_id"], s_["sensor_name"].split("-")[0], ws)
        return lo, alarm

    def fact_predictive_alert(self) -> list[dict[str, Any]]:
        """预测性告警:设备故障停机前 1~5 天,该设备某只传感器出现趋势偏移而告警。"""
        out = []
        sensors_by_eq = defaultdict(list)
        for s_ in self.rows("dim_sensor", "sensor_id").values():
            sensors_by_eq[s_["equipment_id"]].append(s_)
        for dn in self.rows("fact_downtime", "downtime_id").values():
            if dn["reason_code"] != 1 or self.rng.random() > 0.55:
                continue
            st = t_(dn["start_time"]) - timedelta(days=self.rng.uniform(1, 5))
            if st.date() < GO_LIVE:
                continue
            s_ = self.rng.choice(sensors_by_eq[dn["equipment_id"]])
            lo, alarm = self.sensor_limits(s_)
            v = round(alarm * self.rng.uniform(0.9, 0.98) if alarm > 0 else alarm * 1.05, 3)
            out.append(ev(st, "CLOSED", equipment_id=dn["equipment_id"], sensor_id=s_["sensor_id"], reading_value=v, reading_unit=s_["unit"],
                          lower_threshold=lo, upper_threshold=alarm, sensor_code=s_["sensor_code"], alarm_code=f"PA-{s_['sensor_code']}",
                          health_index=round(self.rng.uniform(0.45, 0.65), 3), remaining_life_hours=round((t_(dn["start_time"]) - st).total_seconds() / 3600, 1),
                          failure_mode="趋势偏移预警", event_code="EV-WARN", maintenance_type="预测性维护", downtime_minutes=0.0, rate_value=0.0))
        return out

    def fact_spare_part_issue(self) -> list[dict[str, Any]]:
        """备件领用 = 维修记录更换的备件;领用前后库存按备件逐笔滚动(低于下限即补货到上限)。"""
        out = []
        parts = {p["part_name"]: p for p in self.rows("dim_spare_part", "part_id").values()}
        stock = {p["part_id"]: p["max_stock"] for p in parts.values()}
        techs = self.employees(9)
        wo_start = {w["wo_id"]: w["start_time"] or "" for w in self.rows("fact_maintenance_wo", "wo_id").values()}
        for r in sorted(self.rows("fact_maintenance_record", "record_id").values(), key=lambda r: (wo_start[r["wo_id"]], r["record_id"])):
            if not r["parts_replaced"]:
                continue
            wo = self.rows("fact_maintenance_wo", "wo_id")[r["wo_id"]]
            for item in r["parts_replaced"].split("、"):
                name, n = item.rsplit("×", 1)
                p = parts[name]
                n = int(n)
                if stock[p["part_id"]] - n < p["min_stock"]:
                    stock[p["part_id"]] = p["max_stock"]
                before = stock[p["part_id"]]
                stock[p["part_id"]] -= n
                day = d_(r["maint_date"])
                out.append(ev(t_(wo["start_time"]), status_for(day), equipment_id=r["equipment_id"], part_id=p["part_id"], unit_code="EA",
                              quantity=float(n), base_quantity=float(n), planned_quantity=float(n), actual_quantity=float(n),
                              qualified_quantity=float(n), rejected_quantity=0.0, currency_code="CNY", exchange_rate=1.0,
                              unit_price=p["unit_price"], amount=round(n * p["unit_price"], 2), _tax_rate=0.0, part_code=p["part_code"],
                              stock_before=float(before), stock_after=float(stock[p["part_id"]]), maintenance_type=wo["maint_type"],
                              requester_code=f"E{self.rng.choice(techs):04d}", issue_type="维修领用", task_type="更换备件",
                              source_document_no=wo["wo_no"]))
        return out

    def fact_maintenance_task(self) -> list[dict[str, Any]]:
        """维修任务:每张已开工的工单按类型拆成 2~3 个任务,由工单派工技师执行,时间在工单起止之内,人·时合计等于维修记录工时。"""
        out = []
        name_to_id = {e["emp_name"]: k for k, e in self.rows("dim_employee", "emp_id").items()}
        labor = {r["wo_id"]: r["labor_hours"] for r in self.rows("fact_maintenance_record", "record_id").values()}
        steps = {"故障维修": ["故障诊断", "拆检更换", "试机验证"], "月度检修": ["清洁紧固", "润滑点检"], "季度大修": ["拆检测量", "更换易损件", "试机验证"]}
        for wo in self.rows("fact_maintenance_wo", "wo_id").values():
            if not wo["start_time"]:
                continue
            st = t_(wo["start_time"])
            en = t_(wo["end_time"]) if wo["end_time"] else min(st + 4 * H, datetime(2026, 6, 30, 23, 59))
            names = steps.get(wo["maint_type"], ["检修"])
            span = (en - st) / len(names)
            done = wo["status"] == "completed"
            total = labor.get(wo["wo_id"]) or (en - st).total_seconds() / 3600   # 人·时,与维修记录一致(计划检修两人作业)
            for k, name in enumerate(names):
                s0, s1 = st + span * k, st + span * (k + 1)
                hours = (s1 - s0).total_seconds() / 3600
                lh = round(total / len(names), 2) if k < len(names) - 1 else round(total - round(total / len(names), 2) * (len(names) - 1), 2)
                out.append(ev(s0, "CLOSED" if done else "IN_PROGRESS", wo_id=wo["wo_id"], emp_id=name_to_id[wo["assigned_tech"]],
                              duration_minutes=round(hours * 60, 1), currency_code="CNY", exchange_rate=1.0, amount=round(lh * 120, 2),
                              unit_price=120.0, _tax_rate=0.0, task_type=name, labor_hours=lh, start_time=S.iso(s0),
                              end_time=S.iso(s1) if done else None, result_code="完成" if done else None, maintenance_type=wo["maint_type"],
                              failure_mode="见工单" if wo["maint_type"] == "故障维修" else "无故障"))
        return out

    def fact_energy_meter_reading(self) -> list[dict[str, Any]]:
        """能源计量读数:每条产线电、水、天然气、蒸汽表每日抄表,读数累计,差值等于当日能耗记录(天然气、蒸汽有用量的日子才抄)。"""
        out = []
        types = {r["code"]: k for k, r in self.rows("dim_energy_type", "energy_type_id").items()}
        extruder = {e["line_id"]: e["equipment_id"] for e in self.rows("dim_equipment", "equipment_id").values() if e["type_id"] == 1}
        meter = defaultdict(lambda: 10000.0)
        prices = {"ELEC": 0.72, "WATER": 4.2, "NGAS": 3.6, "STEAM": 260.0}
        for lid, day, kwh, water, gas, steam in self.q("SELECT line_id, record_date, electricity_kwh, water_tons, gas_m3, steam_tons FROM fact_energy_consumption ORDER BY record_date, line_id"):
            for code, val, unit in (("ELEC", kwh, "KWH"), ("WATER", water, "M3"), ("NGAS", gas, "M3"), ("STEAM", steam, "TON")):
                if code in ("NGAS", "STEAM") and not val:
                    continue
                start = meter[(lid, code)]
                meter[(lid, code)] = round(start + val, 2)
                out.append(ev(at(d_(day), 23.98), "CLOSED", equipment_id=extruder[lid], energy_type_id=types[code], unit_code=unit,
                              quantity=round(val, 2), base_quantity=round(val, 2), planned_quantity=round(val, 2), actual_quantity=round(val, 2),
                              qualified_quantity=round(val, 2), rejected_quantity=0.0, currency_code="CNY", exchange_rate=1.0,
                              unit_price=prices[code], amount=round(val * prices[code], 2), _tax_rate=0.13 if code == "ELEC" else 0.09,
                              meter_code=f"M-L{lid:02d}-{code}", meter_start=round(start, 2), meter_end=meter[(lid, code)],
                              collection_method="自动采集"))
        return out

    # ───────── 财务:按单据与复式记账规则生成 ─────────
    def vouchers(self) -> list[tuple[date, str, list[tuple[str, float, float]], str]]:
        """(日期, 业务类型, [(科目编码, 借, 贷)], 凭证号) — 每天每类业务一张汇总记账凭证,借贷平衡。
        销售开票与红字冲销、回款(电汇入银行存款,银行承兑汇票入应收票据)、采购入库、付款、费用报销、
        生产领料(按原料批次入库单价)、直接人工与制造费用转入生产成本、完工入库、销售出库结转成本(按发出批次单位成本)、
        月末制造费用归集、次月工资发放、计提折旧、在建工程。"""
        if hasattr(self, "_vouchers"):
            return self._vouchers
        daily: dict[tuple[date, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for i in self.rows("fact_invoice", "invoice_id").values():
            if i["amount_no_tax"] >= 0:
                k = (d_(i["invoice_date"]), "销售开票")
                daily[k]["1122D"] += i["total_amount"]
                daily[k]["6001C"] += i["amount_no_tax"]
                daily[k]["2221C"] += i["tax_amount"]
            else:
                k = (d_(i["invoice_date"]), "销售退回")
                daily[k]["6001D"] += -i["amount_no_tax"]
                daily[k]["2221D"] += -i["tax_amount"]
                daily[k]["1122C"] += -i["total_amount"]
        for p in self.rows("fact_payment", "payment_id").values():
            k = (d_(p["payment_date"]), "客户回款")
            daily[k]["1121D" if p["is_bank_accepted"] else "1002D"] += p["amount"]
            daily[k]["1122C"] += p["amount"]
            if p["is_bank_accepted"]:                             # 银行承兑汇票 6 个月到期托收入账
                due = d_(p["payment_date"]) + timedelta(days=182)
                if due <= END:
                    daily[(due, "票据到期托收")]["1002D"] += p["amount"]
                    daily[(due, "票据到期托收")]["1121C"] += p["amount"]
        for ap in self.rows("fact_ap", "ap_id").values():
            gr = self.rows("fact_goods_receipt", "gr_id")[ap["gr_id"]]
            k = (d_(gr["receipt_date"]), "采购入库")
            net = round(gr["quantity"] * self.lot_price(f"RM-{gr['gr_id']}", 0), 2)
            daily[k]["1403D"] += net
            daily[k]["2221D"] += round(ap["amount"] - net, 2)
            daily[k]["2202C"] += ap["amount"]
        for p in self.rows("fact_payment_out", "payment_id").values():
            k = (d_(p["payment_date"]), "支付货款")
            daily[k]["2202D"] += p["amount"]
            daily[k]["1002C"] += p["amount"]
        for x in self.rows("fact_expense", "expense_id").values():
            k = (d_(x["expense_date"]), "费用报销")
            daily[k]["6601D"] += x["amount"]
            daily[k]["1002C"] += x["amount"]
        orders = {o["order_id"]: o for o in self.rows("fact_production_order", "order_id").values()}
        for oid, mid, lot, actual in self.q("SELECT order_id, material_id, batch_no, actual_qty FROM fact_material_consumption"):
            k = (d_(orders[oid]["end_time"]), "生产领料")
            amt = round(actual * self.lot_price(lot, mid), 2)
            daily[k]["5001D"] += amt
            daily[k]["1403C"] += amt
        done_day = {o["batch_no"]: d_(o["end_time"]) for o in orders.values() if o["status"] == "已完成"}
        month_labor: dict[str, float] = defaultdict(float)
        month_oh: dict[str, float] = defaultdict(float)
        for c in self.rows("fact_product_cost", "cost_id").values():
            day = done_day[c["batch_no"]]
            k = (day, "完工入库")
            daily[k]["5001D"] += c["labor_cost"] + c["overhead_cost"]
            daily[k]["2211C"] += c["labor_cost"]                 # 直接人工计入应付职工薪酬
            daily[k]["5101C"] += c["overhead_cost"]              # 制造费用按工单转入生产成本
            daily[k]["1405D"] += c["total_cost"]
            daily[k]["5001C"] += c["total_cost"]
            month_labor[day.strftime("%Y-%m")] += c["labor_cost"]
            month_oh[day.strftime("%Y-%m")] += c["overhead_cost"]
        bcost = self.batch_cost()
        for did, parts in self.ship_parts().items():
            dd = d_(self.rows("fact_delivery", "delivery_id")[did]["delivery_date"])
            cogs = round(sum(q * bcost.get(b, 0.0) for _n, b, q, _m in parts), 2)
            k = (dd, "销售出库结转成本")
            daily[k]["6401D"] += cogs
            daily[k]["1405C"] += cogs
        month_dep: dict[str, float] = defaultdict(float)
        for r in self.rows("fact_depreciation", "dep_id").values():
            y, m = int(r["period"][:4]), int(r["period"][5:])
            last = date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)
            k = (min(last, END), "计提折旧")
            daily[k]["5101D"] += r["dep_amount"]
            daily[k]["1602C"] += r["dep_amount"]
            month_dep[r["period"]] += r["dep_amount"]
        for per, oh in month_oh.items():                          # 月末归集折旧以外的制造费用:间接人工四成、水电维修等六成
            y, m = int(per[:4]), int(per[5:])
            last = min(date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1), END)
            rest = round(oh - month_dep.get(per, 0.0), 2)
            if rest > 0:
                k = (last, "制造费用归集")
                daily[k]["5101D"] += rest
                daily[k]["2211C"] += round(rest * 0.4, 2)
                daily[k]["1002C"] += round(rest - round(rest * 0.4, 2), 2)
                month_labor[per] += round(rest * 0.4, 2)
        for per, wage in month_labor.items():                     # 次月 15 日发放上月工资
            y, m = int(per[:4]), int(per[5:])
            pay = date(y + m // 12, m % 12 + 1, 15)
            if pay <= END:
                k = (pay, "发放工资")
                daily[k]["2211D"] += wage
                daily[k]["1002C"] += wage
        for r in self.capex_rows():
            k = (r["_date"], "在建工程")
            daily[k]["1604D"] += r["amount"]
            daily[k]["2202C"] += r["amount"]
            if r["capitalized_flag"]:
                k2 = (r["_date"], "在建工程转固")
                daily[k2]["1601D"] += r["asset_value_added"]
                daily[k2]["1604C"] += r["asset_value_added"]
        # 期初余额(系统上线日):上线前购置资产的原值与累计折旧、期初原料库存,银行存款按全期最低余额留足,差额记实收资本
        opening: dict[str, float] = defaultdict(float)
        for a_ in self.rows("dim_fixed_asset", "asset_id").values():
            pdate = d_(a_["purchase_date"])
            if pdate >= GO_LIVE:
                continue
            used = (2024 - pdate.year) * 12 + (12 - pdate.month)
            opening["1601D"] += a_["original_value"]
            opening["1602C"] += round(min(a_["original_value"] * 0.95, a_["monthly_depreciation"] * used), 2)
        for f in self.material_flows():
            if f[4] == "期初":
                opening["1403D"] += round(f[3] * f[7], 2)
        bank, low = 0.0, 0.0
        for _key, acc in sorted(daily.items()):
            bank += acc.get("1002D", 0.0) - acc.get("1002C", 0.0)
            low = min(low, bank)
        opening["1002D"] += round(-low * 1.1 + 5.0e7, -4)
        opening["4001C"] += round(sum(v for k_, v in opening.items() if k_.endswith("D")) - sum(v for k_, v in opening.items() if k_.endswith("C")), 2)
        daily[(GO_LIVE, "期初余额")] = opening
        out = []
        seq: dict[str, int] = defaultdict(int)
        for (day, kind), acc in sorted(daily.items(), key=lambda x: (x[0][0], x[0][1] != "期初余额", x[0][1])):
            if day > END:
                continue
            lines = []
            for key, v in sorted(acc.items()):
                v = round(v, 2)
                if abs(v) < 0.005:
                    continue
                lines.append((key[:-1], v if key.endswith("D") else 0.0, v if key.endswith("C") else 0.0))
            if not lines:
                continue
            lines.sort(key=lambda x: (x[1] == 0.0, x[0]))      # 先借后贷
            dr = round(sum(x[1] for x in lines), 2)
            cr = round(sum(x[2] for x in lines), 2)
            if abs(dr - cr) > 0.005:                            # 四舍五入差计入最后一行
                code, d0, c0 = lines[-1]
                lines[-1] = (code, d0 + (cr - dr if d0 else 0.0), c0 + (dr - cr if c0 else 0.0))
            seq[day.strftime("%Y%m")] += 1
            out.append((day, kind, lines, f"记-{day.strftime('%Y%m')}-{seq[day.strftime('%Y%m')]:04d}"))
        self._vouchers = out
        self._voucher_of = {(v[0], v[1]): v[3] for v in out}
        return out

    def acct(self, code: str) -> int:
        return next(k for k, r in self.rows("dim_account", "account_id").items() if r["account_code"] == code)

    def fact_gl_entry(self) -> list[dict[str, Any]]:
        out = []
        cc_for = {"期初余额": 7, "票据到期托收": 8, "销售开票": 8, "销售退回": 8, "客户回款": 8, "采购入库": 1, "支付货款": 1, "费用报销": 8, "生产领料": 1, "完工入库": 1,
                  "销售出库结转成本": 8, "计提折旧": 7, "制造费用归集": 1, "发放工资": 1, "在建工程": 7, "在建工程转固": 7}
        for day, kind, lines, vno in self.vouchers():
            main = lines[0]                                     # 凭证头记第一条借方科目
            total = round(sum(x[1] for x in lines), 2)
            out.append(ev(day, "CLOSED" if day < END - timedelta(days=5) else "APPROVED", account_id=self.acct(main[0]), cc_id=cc_for[kind],
                          coa_id=1, currency_code="CNY", exchange_rate=1.0, unit_price=total, amount=total, _tax_rate=0.0,
                          debit_amount=total, credit_amount=total, account_code=main[0], summary_text=f"{kind}({day.isoformat()}汇总)",
                          voucher_no=vno, cost_center_code=self.rows("dim_cost_center", "cc_id")[cc_for[kind]]["cc_code"]))
        return out

    def fact_gl_entry_line(self) -> list[dict[str, Any]]:
        out = []
        for h in self.rows("fact_gl_entry", "gl_entry_id").values():
            day = d_(h["business_date"])
            for n, (code, dr, cr) in enumerate(self.vouchers()[h["gl_entry_id"] - 1][2], start=1):
                amt = round(dr or cr, 2)
                out.append(ev(day, h["status"], gl_entry_id=h["gl_entry_id"], account_id=self.acct(code), _line_no=n, currency_code="CNY",
                              exchange_rate=1.0, unit_price=amt, amount=amt, _tax_rate=0.0, debit_amount=round(dr, 2), credit_amount=round(cr, 2),
                              account_code=code, voucher_no=h["voucher_no"], summary_text=h["summary_text"], cost_center_code=h["cost_center_code"]))
        return out

    def fact_cash_flow(self) -> list[dict[str, Any]]:
        """资金流水(银行存款):电汇回款、票据到期托收为流入,支付货款、费用报销、发放工资与制造费用付款为流出;
        银行承兑汇票回款先记应收票据,到期托收时才进资金流水。凭证号取当日汇总凭证。"""
        out = []
        self.vouchers()
        bank = self.acct("1002")
        for p in self.rows("fact_payment", "payment_id").values():
            if p["is_bank_accepted"]:
                continue
            a = self.rows("fact_ar_aging", "ar_id")[p["ar_id"]]
            day = d_(p["payment_date"])
            out.append(ev(day, "CLOSED" if day < END - timedelta(days=5) else "APPROVED", account_id=bank,
                          cust_id=a["cust_id"], supplier_id=None, currency_code="CNY", exchange_rate=1.0, unit_price=p["amount"], amount=p["amount"],
                          _tax_rate=0.0, debit_amount=p["amount"], credit_amount=0.0, account_code="1002", summary_text="客户回款(电汇)",
                          voucher_no=self._voucher_of[(day, "客户回款")]))
        for p in self.rows("fact_payment_out", "payment_id").values():
            ap = self.rows("fact_ap", "ap_id")[p["ap_id"]]
            day = d_(p["payment_date"])
            out.append(ev(day, "CLOSED" if day < END - timedelta(days=5) else "APPROVED", account_id=bank,
                          cust_id=None, supplier_id=ap["supplier_id"], currency_code="CNY", exchange_rate=1.0, unit_price=p["amount"],
                          amount=p["amount"], _tax_rate=0.0, debit_amount=0.0, credit_amount=p["amount"], account_code="1002", summary_text="支付货款",
                          voucher_no=self._voucher_of[(day, "支付货款")], cost_center_code=None))
        internal = {"费用报销": "报销销售费用", "发放工资": "发放工资", "制造费用归集": "支付水电与维修等制造费用", "票据到期托收": "银行承兑汇票到期托收"}
        for day, kind, lines, vno in self.vouchers():
            if kind not in internal:
                continue
            dr = round(sum(d0 for c, d0, _c in lines if c == "1002"), 2)
            cr = round(sum(c0 for c, _d, c0 in lines if c == "1002"), 2)
            amt = dr or cr
            if amt <= 0:
                continue
            out.append(ev(day, "CLOSED" if day < END - timedelta(days=5) else "APPROVED", account_id=bank, cust_id=None, supplier_id=None,
                          currency_code="CNY", exchange_rate=1.0, unit_price=amt, amount=amt, _tax_rate=0.0, debit_amount=dr, credit_amount=cr,
                          account_code="1002", summary_text=internal[kind], voucher_no=vno))
        out.sort(key=lambda r: r["_date"])
        return out

    def fact_tax_invoice_line(self) -> list[dict[str, Any]]:
        """销项发票明细:每张蓝字发票一行(产品 × 数量 × 不含税单价,增值税 13%),成本为发出批次成本;红字发票不列明细。"""
        out = []
        self.vouchers()
        vat13 = next(k for k, r in self.rows("dim_tax_code", "tax_code_id").items() if r["code"] == "VAT13")
        bcost = self.batch_cost()
        for i in self.rows("fact_invoice", "invoice_id").values():
            if i["amount_no_tax"] < 0:
                continue
            dl = self.rows("fact_delivery", "delivery_id")[i["delivery_id"]]
            o = self.rows("fact_sales_order", "order_id")[dl["order_id"]]
            day = d_(i["invoice_date"])
            cogs = round(sum(q * bcost.get(b, 0.0) for _n, b, q, _m in self.ship_parts()[dl["delivery_id"]]), 2)
            out.append(ev(day, status_for(day), invoice_id=i["invoice_id"], tax_code_id=vat13, _line_no=1,
                          currency_code="CNY", exchange_rate=1.0, unit_price=o["unit_price"], amount=i["amount_no_tax"], _tax_rate=0.13,
                          tax_amount=i["tax_amount"], cost_amount=cogs, account_code="6001", tax_rate=0.13, debit_amount=0.0, credit_amount=i["amount_no_tax"],
                          voucher_no=self._voucher_of.get((day, "销售开票")),
                          summary_text=f"销售{self.rows('dim_product', 'prod_id')[o['prod_id']]['prod_name']} {dl['quantity']} 吨"))
        return out

    def fact_cost_allocation(self) -> list[dict[str, Any]]:
        """成本分摊:质量部、设备部每月费用按各生产成本中心的运行工时分摊,比例合计 100%。"""
        out = []
        run = defaultdict(float)
        for lid, day, hours in self.q("SELECT line_id, record_date, actual_run_hours FROM fact_equipment_runtime"):
            ws = self.rows("dim_production_line", "line_id")[lid]["workshop_id"]
            run[(ws, day[:7])] += hours
        ccs = self.rows("dim_cost_center", "cc_id")
        pcs = {ws: next(k for k, r in self.rows("dim_profit_center", "pc_id").items() if r["bu_id"] == ccs[ws]["bu_id"]) for ws in range(1, 6)}
        budgets = {(b["cc_id"], f"{b['year']}-{b['month']:02d}"): b["actual_amount"] for b in self.rows("fact_budget", "budget_id").values()}
        for (src, per), amt in sorted(budgets.items()):
            if src not in (6, 7):
                continue
            tot = sum(run[(ws, per)] for ws in range(1, 6))
            if not tot:
                continue
            y, m = int(per[:4]), int(per[5:])
            last = min(date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1), END)
            for ws in range(1, 6):
                share = run[(ws, per)] / tot
                a = round(amt * share, 2)
                out.append(ev(last, "CLOSED" if last < END else "APPROVED", source_cc_id=src, target_pc_id=pcs[ws], currency_code="CNY",
                              exchange_rate=1.0, unit_price=a, amount=a, _tax_rate=0.0, rate_value=round(share, 4), debit_amount=a, credit_amount=0.0,
                              voucher_no=f"CO-{per.replace('-', '')}-{src:02d}{ws}",
                              account_code="5101", cost_center_code=self.rows("dim_cost_center", "cc_id")[src]["cc_code"],
                              summary_text=f"{per} {self.rows('dim_cost_center', 'cc_id')[src]['cc_name']}费用按工时分摊至{self.rows('dim_workshop', 'workshop_id')[ws]['workshop_name']}"))
        return out

    def fact_fixed_asset_transfer(self) -> list[dict[str, Any]]:
        """固定资产调拨:少量包装机在工厂间调剂;资产卡片按月变更,观测期内均未体现。"""
        out = []
        packers = [e for e in self.rows("dim_equipment", "equipment_id").values() if e["type_id"] == 6]
        ws_fac = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3}
        for k, e in enumerate(self.rng.sample(packers, 3)):
            src = ws_fac[self.rows("dim_production_line", "line_id")[e["line_id"]]["workshop_id"]]
            tgt = 3 if src != 3 else 2
            done = k == 0                                       # 一台已交接(资产卡片下月起变更,观测期内未体现),两台在审批
            day = END - timedelta(days=self.rng.randint(20, 25) if done else self.rng.randint(5, 40))
            a = self.rows("dim_fixed_asset", "asset_id")[e["equipment_id"]]
            out.append(ev(day, "CLOSED" if done else "OPEN", asset_id=a["asset_id"], source_factory_id=src, target_factory_id=tgt, currency_code="CNY", exchange_rate=1.0,
                          unit_price=a["net_book_value"], amount=a["net_book_value"], _tax_rate=0.0, net_book_value=a["net_book_value"],
                          transfer_reason="产能调整", handover_date=(day + timedelta(days=7)).isoformat() if done else None, debit_amount=a["net_book_value"], credit_amount=a["net_book_value"],
                          account_code="1601"))
        return out

    def capex_rows(self) -> list[dict[str, Any]]:
        """资本项目每月工程款:阶段按时间顺序(设计 → 采购 → 施工 → 调试);项目在观测期内完工的,最后一笔把累计支出全部转固。
        产线类项目挂该线挤出机,厂房、仓库、公用工程类挂所在工厂的对应资产。"""
        if hasattr(self, "_capex"):
            return self._capex
        out: list[dict[str, Any]] = []
        assets = self.rows("dim_fixed_asset", "asset_id")
        cls = {r["name"]: k for k, r in self.rows("dim_asset_class", "asset_class_id").items()}
        cls_of = {"production_equipment": "生产设备", "building": "房屋建筑", "utility": "动力设备"}
        extruders = sorted(e["equipment_id"] for e in self.rows("dim_equipment", "equipment_id").values() if e["type_id"] == 1)
        by_name = {a_["asset_name"]: k for k, a_ in assets.items()}
        fac_names = [f[1] for f in M.FACTORIES]
        for pj in sorted(self.rows("dim_project", "project_id").values(), key=lambda x: x["project_id"]):
            st, en = d_(pj["planned_start_date"]), d_(pj["planned_end_date"])
            fac = fac_names[(pj["project_id"] - 1) % 3]
            kind = pj["project_type"]
            if kind == "仓库自动化":
                asset = by_name[f"{fac}仓库"]
            elif kind in ("质量实验室建设",):
                asset = by_name[f"{fac}主厂房"]
            elif kind in ("环保设施升级", "光伏屋顶", "能源计量升级", "安全隐患整改"):
                asset = by_name[f"{fac}公用工程(配电、空压、冷却水)"]
            else:
                asset = extruders[(pj["project_id"] - 1) % len(extruders)]
            months = max(1, round((en - st).days / 30))
            day, k, spent = st + timedelta(days=10), 0, 0.0
            while day <= min(en, END):
                frac = (day - st).days / max(1, (en - st).days)
                phase = "设计" if frac < 0.15 else "采购" if frac < 0.35 else "施工" if frac < 0.8 else "调试"
                amt = round(pj["budget_ceiling"] / months * (0.85 + (pj["project_id"] * 7 + k) % 4 * 0.05), 2)
                spent = round(spent + amt, 2)
                last = day + timedelta(days=30) > en and en <= END
                out.append(ev(day, status_for(day), project_id=pj["project_id"], asset_id=asset,
                              asset_class_id=cls[cls_of[assets[asset]["asset_type"]]], currency_code="CNY", exchange_rate=1.0,
                              unit_price=amt, amount=amt, _tax_rate=0.13, budget_amount=round(pj["budget_ceiling"] / months, 2),
                              project_phase=phase, cost_element={"设计": "设计咨询费", "采购": "设备购置", "施工": "安装工程", "调试": "调试费"}[phase],
                              capitalized_flag=int(last), asset_value_added=spent if last else 0.0,
                              debit_amount=amt, credit_amount=0.0, account_code="1604",
                              invoice_ref=f"PINV-{pj['code']}-{k + 1:02d}", summary_text=f"{pj['name']}{phase}阶段工程款"))
                day += timedelta(days=30)
                k += 1
        self._capex = out
        return out

    def fact_capex_project_cost(self) -> list[dict[str, Any]]:
        self.vouchers()
        rows = [dict(r) for r in self.capex_rows()]
        for r in rows:
            r["voucher_no"] = self._voucher_of.get((r["_date"], "在建工程"))
        return rows

    def fact_environment_measurement(self) -> list[dict[str, Any]]:
        """环境监测明细:每次月度监测三次采样,均值即 108 表环境监测记录的测值。"""
        out = []
        types = {r["code"]: k for k, r in self.rows("dim_emission_type", "emission_type_id").items()}
        code = {"COD": "COD", "氨氮": "NH3N", "VOCs": "VOCS", "颗粒物": "PM", "噪声": "NOISE"}
        for r in self.rows("fact_env_record", "record_id").values():
            day = d_(r["record_date"])
            v = r["measured_value"]
            # 三次采样均值等于月度测值;测值合规的月份,各次采样也不超过限值
            for _ in range(50):
                devs = [self.rng.uniform(-0.06, 0.06) for _ in range(2)]
                vals = [round(v * (1 + devs[0]), 3), round(v * (1 + devs[1]), 3)]
                vals.append(round(3 * v - sum(vals), 3))
                if not r["is_compliant"] or max(vals) <= r["limit_value"]:
                    break
            else:
                vals = [v, v, v]
            for k, x in enumerate(vals):
                out.append(ev(at(day, 9 + 3 * k), "CLOSED", factory_id=1, emission_type_id=types[code[r["emission_type"]]], emission_value=x,
                              limit_value=r["limit_value"], measurement_unit=r["unit"], exceed_flag=int(x > r["limit_value"]),
                              monitoring_point={"COD": "DW001 总排口", "氨氮": "DW001 总排口", "VOCs": "DA001 排气筒", "颗粒物": "DA001 排气筒",
                                                "噪声": "ZS01 厂界"}[r["emission_type"]], sampling_method="手工采样",
                              rate_value=1.0 if x <= r["limit_value"] else 0.0))
        return out

    def fact_emission_event(self) -> list[dict[str, Any]]:
        """超标排放事件:月度监测不合规即立案,三天内整改复测。"""
        out = []
        types = {r["code"]: k for k, r in self.rows("dim_emission_type", "emission_type_id").items()}
        code = {"COD": "COD", "氨氮": "NH3N", "VOCs": "VOCS", "颗粒物": "PM", "噪声": "NOISE"}
        for r in self.rows("fact_env_record", "record_id").values():
            if r["is_compliant"]:
                continue
            day = d_(r["record_date"])
            out.append(ev(day, status_for(day), factory_id=1, emission_type_id=types[code[r["emission_type"]]], unit_code="KG", quantity=0.0,
                          base_quantity=0.0, planned_quantity=0.0, actual_quantity=0.0, qualified_quantity=0.0, rejected_quantity=0.0,
                          currency_code="CNY", exchange_rate=1.0, unit_price=0.0, amount=0.0, _tax_rate=0.0,
                          duration_minutes=float(self.rng.choice([120, 240, 480])), emission_value=r["measured_value"],
                          limit_value=r["limit_value"], measurement_unit=r["unit"], exceed_flag=1, hazard_type="环境",
                          action_due_date=(day + timedelta(days=3)).isoformat(), closed_date=(day + timedelta(days=self.rng.randint(1, 3))).isoformat()))
        return out

    def fact_hazard_observation(self) -> list[dict[str, Any]]:
        """隐患观察:安全员与班组长每周巡检危险源所在区域,风险等级由严重度 × 可能性确定。"""
        out = []
        observers = self.employees(14) + [t["leader_id"] for t in self.rows("dim_team", "team_id").values()][:12]
        hz_ws = M.HAZARD_WORKSHOP
        base_sev = {"low": 2, "medium": 3, "high": 4}
        for hz in self.rows("dim_hazard", "hazard_id").values():
            day = date(2025, 1, 6) + timedelta(days=hz["hazard_id"] % 7)
            while day <= END:
                sev = max(1, min(5, base_sev[hz["risk_level"]] + self.rng.choice([-1, 0, 0, 1])))
                lik = self.rng.choice([1, 1, 2, 2, 3])
                out.append(ev(at(day, self.rng.uniform(9, 16)), status_for(day), hazard_id=hz["hazard_id"], emp_id=self.rng.choice(observers),
                              workshop_id=hz_ws[hz["hazard_id"]], risk_level_id=self.risk_level(sev * lik), _severity=sev, _likelihood=lik,
                              severity=sev, likelihood=lik, risk_score=float(sev * lik), hazard_type=hz["hazard_category"],
                              location_code=hz["location"], rate_value=round(1 - sev * lik / 25, 4)))
                day += timedelta(days=self.rng.randint(12, 21))
        return out

    def fact_safety_action(self) -> list[dict[str, Any]]:
        """整改措施:观察到风险分值 ≥ 6 的隐患立整改,期限按风险等级(越高越短)。"""
        out = []
        owners = self.employees(14)
        for o in self.rows("fact_hazard_observation", "hazard_observation_id").values():
            score = o["severity"] * o["likelihood"]
            if score < 6:
                continue
            day = d_(o["business_date"]) + timedelta(days=1)
            if day > END:
                continue
            lv = self.rows("dim_risk_level", "level_id")[o["risk_level_id"]]["level_code"]
            due = day + timedelta(days={"L1": 30, "L2": 14, "L3": 7, "L4": 2}[lv])
            closed = day + timedelta(days=self.rng.randint(1, (due - day).days + 2))
            out.append(ev(day, "CLOSED" if closed <= END else "IN_PROGRESS", hazard_id=o["hazard_id"], emp_id=self.rng.choice(owners),
                          risk_level_id=o["risk_level_id"], _severity=o["severity"], _likelihood=o["likelihood"], severity=o["severity"],
                          likelihood=o["likelihood"], risk_score=float(score), duration_minutes=float(self.rng.choice([60, 120, 240])),
                          currency_code="CNY", exchange_rate=1.0, amount=float(self.rng.choice([0, 800, 2000, 6000])), _tax_rate=0.0,
                          action_due_date=due.isoformat(), closed_date=closed.isoformat() if closed <= END else None,
                          observation_no=o["observation_no"], hazard_type=o["hazard_type"], location_code=o["location_code"],
                          control_measure=self.rng.choice(["工程控制", "管理控制", "个体防护"]), rate_value=1.0 if closed <= END else 0.0))
        for r in out:
            r["unit_price"] = r["amount"]
        return out

    def risk_level(self, score: int) -> int:
        code = "L1" if score <= 4 else "L2" if score <= 9 else "L3" if score <= 15 else "L4"
        return next(k for k, r in self.rows("dim_risk_level", "level_id").items() if r["level_code"] == code)

    def fact_training_attendance(self) -> list[dict[str, Any]]:
        """培训签到:每场培训签到人数等于 108 表参训人数,签到在培训当天,同一人同一场一次。"""
        out = []
        emps = sorted(self.rows("dim_employee", "emp_id"))
        for tr in self.rows("fact_safety_training", "training_id").values():
            day = d_(tr["training_date"])
            pool = [e for e in emps if d_(self.rows("dim_employee", "emp_id")[e]["hire_date"]) <= day]
            if tr["training_type"] == "新员工三级安全教育":            # 三级教育只有当期新入职员工参加
                pool = [e for e in pool if (day - d_(self.rows("dim_employee", "emp_id")[e]["hire_date"])).days <= 30]
            ppl = sorted(self.rng.sample(pool, tr["attendee_count"]))
            raw = [self.rng.gauss(0, 6) for _ in ppl]
            shift = tr["score_avg"] - sum(raw) / len(raw)
            for _ in range(20):                                     # 截到 40~100 后再平移,使签到成绩均值等于培训平均分
                scores = [min(100.0, max(40.0, x + shift)) for x in raw]
                gap = tr["score_avg"] - sum(scores) / len(scores)
                if abs(gap) < 0.01:
                    break
                shift += gap
            scores = [round(x, 1) for x in scores]
            for e, score in zip(ppl, scores):
                out.append(ev(at(day, 9), "CLOSED", training_id=tr["training_id"], emp_id=e, duration_minutes=float(tr["duration_hours"] * 60),
                              score_value=score, rate_value=1.0, exam_score=score, passed_flag=int(score >= 60), credit_hours=tr["duration_hours"],
                              sign_in_time=f"0{self.rng.randint(8, 8)}:{self.rng.randint(40, 59)}", training_no=tr["training_no"],
                              employee_no=f"E{e:04d}", attendance_result="正常"))
        return out

    # ───────── 人力 ─────────
    def roster(self) -> dict[int, tuple[str, int | None, int]]:
        """排班:班组长随所在产线四班三运转(含周末);其余员工上行政白班(工作日)。值为(类型, 产线, 班组序号)。"""
        out: dict[int, tuple[str, int | None, int]] = {}
        for line, leaders in self.F.leaders_of_line.items():
            for k, leader in enumerate(leaders):
                out[leader] = ("ROT", line, k)
        for e in self.rows("dim_employee", "emp_id"):
            out.setdefault(e, ("OFFICE", None, 0))
        return out

    def workdays(self) -> list[date]:
        rows = self.rows("dim_calendar", "calendar_id")
        return sorted(d_(r["calendar_date"]) for r in rows.values() if r["is_workday"] and d_(r["calendar_date"]) <= END)

    def fact_attendance(self) -> list[dict[str, Any]]:
        """考勤:轮班的班组长在其班组当班的每一天(含周末与节假日)一条,班次与报工一致;
        其余员工每个工作日一条行政白班(08:30~17:30,午休 1 小时),少量请假、迟到与加班。"""
        out = []
        shifts = self.rows("dim_shift", "shift_id")
        emps = self.rows("dim_employee", "emp_id")
        all_days = [GO_LIVE + timedelta(days=i) for i in range((END - GO_LIVE).days + 1)]
        workdays = set(self.workdays())
        for e, (kind, line, k) in sorted(self.roster().items()):
            emp = emps[e]
            hire = d_(emp["hire_date"])
            leave = date(2026, 3, 31) if emp["status"] == "inactive" else None
            for day in all_days:
                if day < hire or (leave and day > leave):
                    continue
                if kind == "ROT" and line is not None:
                    code = self.team_shift(line, k, day)
                    if code is None:
                        continue
                    sid = self.shift_id(line, code)
                    sh = shifts[sid]
                    s0, e_h = int(sh["shift_start"][:2]), int(sh["shift_end"][:2]) % 24
                    r_in = f"{(s0 - 1) % 24:02d}:{40 + (e + day.day) % 18:02d}"
                    r_out = f"{e_h:02d}:{5 + (e * 3 + day.day) % 20:02d}"
                    out.append(ev(day, "CLOSED" if day < END - timedelta(days=30) else "APPROVED", emp_id=e, shift_id=sid,
                                  duration_minutes=round(sh["paid_hours"] * 60, 1), work_date=day.isoformat(), shift_code=sh["code"],
                                  clock_in=r_in, clock_out=r_out, regular_hours=sh["paid_hours"], overtime_hours=0.0, absence_type=None,
                                  attendance_result="正常", employee_no=FILL.emp_no(e),
                                  supervisor_code=FILL.emp_no(self.F.by_role["生产主管"][(self.rows("dim_production_line", "line_id")[line]["workshop_id"] - 1) % 4])))
                    continue
                if day not in workdays:
                    continue
                absent = self.rng.random() < 0.025
                late = not absent and self.rng.random() < 0.03
                ot = 0.0 if absent else self.rng.choice([0, 0, 0, 0, 0, 0, 1.0, 2.0])
                cin: str | None = None if absent else (f"08:{31 + self.rng.randint(0, 20):02d}" if late else f"08:{self.rng.randint(5, 29):02d}")
                cout: str | None = None if absent else f"{17 + int(ot)}:{30 + self.rng.randint(0, 25):02d}"
                out.append(ev(day, "CLOSED" if day < END - timedelta(days=30) else "APPROVED", emp_id=e, shift_id=None,
                              duration_minutes=0.0 if absent else round((8.0 + ot) * 60, 1), work_date=day.isoformat(),
                              shift_code="行政白班", clock_in=cin, clock_out=cout, regular_hours=0.0 if absent else 8.0,
                              overtime_hours=ot, absence_type=self.rng.choice(["事假", "病假", "年假"]) if absent else None,
                              attendance_result="缺勤" if absent else ("迟到" if late else "正常"), employee_no=FILL.emp_no(e)))
        return out

    def fact_overtime(self) -> list[dict[str, Any]]:
        """加班 = 考勤中有加班工时的记录;加班费 = 加班工时 × 时薪 × 1.5。"""
        out = []
        emps = self.rows("dim_employee", "emp_id")
        for a in self.rows("fact_attendance", "attendance_id").values():
            if not a["overtime_hours"]:
                continue
            day = d_(a["business_date"])
            rate = 45.0 if emps[a["emp_id"]]["dept_id"] == 7 else 60.0
            amt = round(a["overtime_hours"] * rate * 1.5, 2)
            out.append(ev(day, a["status"], emp_id=a["emp_id"], dept_id=emps[a["emp_id"]]["dept_id"], duration_minutes=a["overtime_hours"] * 60,
                          currency_code="CNY", exchange_rate=1.0, unit_price=round(rate * 1.5, 2), amount=amt, _tax_rate=0.0,
                          overtime_hours=a["overtime_hours"], work_date=a["work_date"], shift_code=a["shift_code"], employee_no=a["employee_no"],
                          attendance_result="正常"))
        return out

    def fact_employee_assignment(self) -> list[dict[str, Any]]:
        """任职:每名员工一条现任岗位(按建档岗位),自入职日起;离职员工以离职日结束。"""
        out = []
        plan = {(p["dept_id"], p["code"]): p for p in self.dim_plans()["dim_job_position"]}
        pos = {(d, pp["_role"]): k for k, r in self.rows("dim_job_position", "job_position_id").items() for (d, c), pp in plan.items() if c == r["code"]}
        for e in self.rows("dim_employee", "emp_id").values():
            hire = d_(e["hire_date"])
            jp = pos[(e["dept_id"], POSITION_OF.get(self.F.roles[e["emp_id"]], self.F.roles[e["emp_id"]]))]
            out.append(ev(max(hire, GO_LIVE), "CLOSED" if e["status"] == "inactive" else "APPROVED", emp_id=e["emp_id"],   # 上线前入职的随系统上线迁移
                          dept_id=e["dept_id"], job_position_id=jp, valid_from=hire.isoformat(),
                          expiry_date=("2026-03-31" if e["status"] == "inactive" else None), employee_no=FILL.emp_no(e["emp_id"]),
                          position_code=self.rows("dim_job_position", "job_position_id")[jp]["code"],
                          assessor_code=FILL.emp_no(self.F.by_role["人事专员"][0]), rate_value=1.0, score_value=100.0))
        return out

    def fact_skill_assessment(self) -> list[dict[str, Any]]:
        """技能评估:一线与技术岗位每年 4 月按岗位核心技能评估一次;技能等级由得分决定,评估人为本专业负责人。"""
        out = []
        skill = {r["name"]: k for k, r in self.rows("dim_skill", "skill_id").items()}
        core = {"班组长": "挤出操作", "维修技师": "设备点检", "设备工程师": "PLC 编程", "质检员": "质量检验", "质量工程师": "统计过程控制",
                "生产主管": "精益改善", "仓管员": "仓储管理", "计划员": "计划排程", "安全工程师": "安全管理", "研发工程师": "配方开发"}
        assessor = {"班组长": "生产主管", "生产主管": "总经理", "维修技师": "设备工程师", "设备工程师": "总经理", "质检员": "质量工程师",
                    "质量工程师": "总经理", "仓管员": "总经理", "计划员": "总经理", "安全工程师": "总经理", "研发工程师": "总经理"}
        for e in self.rows("dim_employee", "emp_id").values():
            role = self.F.roles[e["emp_id"]]
            if role not in core:
                continue
            for y in (2025, 2026):
                day = date(y, 4, 10) + timedelta(days=e["emp_id"] % 20)
                if day > END or day < d_(e["hire_date"]) or (e["status"] == "inactive" and day > date(2026, 3, 31)):
                    continue
                score = round(min(100.0, max(50.0, self.rng.gauss(80, 8))), 1)
                level = 5 if score >= 92 else 4 if score >= 85 else 3 if score >= 75 else 2 if score >= 65 else 1
                out.append(ev(day, "CLOSED", emp_id=e["emp_id"], skill_id=skill[core[role]], score_value=score,
                              rate_value=round(score / 100, 3), assessment_score=score, skill_level=level, employee_no=FILL.emp_no(e["emp_id"]),
                              assessor_code=FILL.emp_no(self.F.pick(assessor[role], e["emp_id"])), assessment_method="实操考核" if role in ("班组长", "维修技师", "质检员") else "答辩",
                              valid_from=day.isoformat(), expiry_date=(day + timedelta(days=365)).isoformat()))
        return out

    def fact_certification_record(self) -> list[dict[str, Any]]:
        """资质证书:按岗位应持证书(班组长持特种设备操作证、维修技师持电工证与特种设备操作证等);有效期与考试费取证书主数据。"""
        out = []
        certs = self.rows("dim_certification", "certification_id")
        need = {"维修技师": ["电工证", "特种设备操作证"], "班组长": ["特种设备操作证"], "仓管员": ["叉车证"], "设备工程师": ["压力容器证"],
                "安全工程师": ["安全管理员证", "危化品安全证", "有限空间作业证"], "质量工程师": ["内审员证"], "质检员": ["计量检定员"]}
        by_name = {c["name"]: k for k, c in certs.items()}
        for e in self.rows("dim_employee", "emp_id").values():
            for name in need.get(self.F.roles[e["emp_id"]], []):
                c = certs[by_name[name]]
                day = max(d_(e["hire_date"]), date(2024, 1, 1)) + timedelta(days=60 + e["emp_id"] % 90)
                if day > END:
                    continue
                out.append(ev(max(day, GO_LIVE), "APPROVED", emp_id=e["emp_id"], certification_id=c["certification_id"], currency_code="CNY", exchange_rate=1.0,
                              unit_price=c["exam_fee"], amount=c["exam_fee"], _tax_rate=0.0, valid_from=day.isoformat(),
                              expiry_date=(day + timedelta(days=int(c["validity_months"]) * 30)).isoformat(),
                              certificate_no=f"CERT-{day.year}-{e['emp_id']:04d}{c['certification_id']:02d}", employee_no=FILL.emp_no(e["emp_id"]),
                              assessor_code=FILL.emp_no(self.F.by_role["人事专员"][0])))
        return out

    def fact_demand_plan(self) -> list[dict[str, Any]]:
        """需求计划(共识需求):以当月销售预测为基础,计划员按在手订单调整 ±5%;供应量为当月实际交付量,缺口为未满足需求。"""
        out = []
        deliv: dict[tuple[int, int, str], float] = defaultdict(float)
        for dl in self.rows("fact_delivery", "delivery_id").values():
            o = self.rows("fact_sales_order", "order_id")[dl["order_id"]]
            deliv[(o["prod_id"], o["cust_id"], dl["delivery_date"][:7])] += dl["quantity"]
        for f in sorted(self.rows("fact_sales_forecast", "sales_forecast_id").values(), key=lambda x: x["sales_forecast_id"]):
            ym = f["forecast_bucket"]
            made = d_(f["business_date"]) + timedelta(days=2)
            plan = round(f["quantity"] * (0.95 + (f["sales_forecast_id"] * 13) % 11 / 100), 2)
            sup = round(deliv[(f["prod_id"], f["cust_id"], ym)], 3)
            out.append(ev(made, "CLOSED" if made < END - timedelta(days=30) else "APPROVED", prod_id=f["prod_id"], cust_id=f["cust_id"], unit_code="TON",
                          quantity=plan, base_quantity=round(plan * 1000, 3), planned_quantity=plan, actual_quantity=f["actual_quantity"],
                          qualified_quantity=f["actual_quantity"], rejected_quantity=0.0, demand_quantity=plan, supply_quantity=sup,
                          backlog_quantity=round(max(0.0, plan - sup), 3), planning_period=ym,
                          forecast_accuracy=round(max(0.0, 1 - abs(plan - f["actual_quantity"]) / f["actual_quantity"]), 4) if f["actual_quantity"] else 0.0,
                          safety_stock_quantity=round(plan * 0.2, 2), rate_value=round(min(1.0, sup / plan), 4) if plan else 0.0))
        return out

    def fact_supply_plan(self) -> list[dict[str, Any]]:
        """供应计划:按产品 × 工厂 × 月,计划量 = 当月需求计划合计;供应量为当月实际完工量。"""
        out = []
        made: dict[tuple[int, int, str], float] = defaultdict(float)
        for o in self.rows("fact_production_order", "order_id").values():
            if o["status"] != "已完成":
                continue
            made[(o["prod_id"], self.order_factory(o), o["end_time"][:7])] += o["actual_quantity"]
        demand: dict[tuple[int, str], float] = defaultdict(float)
        for r in self.rows("fact_demand_plan", "demand_plan_id").values():
            demand[(r["prod_id"], r["planning_period"])] += r["demand_quantity"]
        for (pid, fac, ym), q in sorted(made.items()):
            y, m = int(ym[:4]), int(ym[5:])
            day = date(y, m, 1) - timedelta(days=5)
            if day < GO_LIVE or not demand.get((pid, ym)):
                continue
            plan = round(demand[(pid, ym)], 2)
            out.append(ev(day, "CLOSED" if day < END - timedelta(days=30) else "APPROVED", prod_id=pid, factory_id=fac, unit_code="TON",
                          quantity=plan, base_quantity=round(plan * 1000, 3), planned_quantity=plan, actual_quantity=round(q, 3),
                          qualified_quantity=round(q, 3), rejected_quantity=0.0, demand_quantity=plan, supply_quantity=round(q, 3),
                          backlog_quantity=round(max(0.0, plan - q), 3), planning_period=ym, safety_stock_quantity=round(plan * 0.2, 2),
                          forecast_accuracy=None, rate_value=round(min(1.0, q / plan), 4) if plan else 0.0))
        return out

    def fact_capacity_plan(self) -> list[dict[str, Any]]:
        """产能计划:产线 × 月 × 班次,可用工时 = 班次计薪工时 × 工作天数,需求工时 = 该班实际运行工时。"""
        out = []
        need = defaultdict(float)
        for (lid, day, code), a in self.line_shift_output().items():
            need[(lid, day.strftime("%Y-%m"), code)] += a["run"]
        cal = defaultdict(int)
        for d in self.rows("dim_calendar", "calendar_id").values():
            if d_(d["calendar_date"]) <= END:
                cal[d["calendar_date"][:7]] += 1
        for lid in sorted(self.rows("dim_production_line", "line_id")):
            for ym in sorted(cal):
                for code in ("DAY", "SWING", "NIGHT"):
                    sid = self.shift_id(lid, code)
                    avail = round(8.0 * cal[ym], 1)                # 连续生产:每班 8 机台小时 × 当月日历天数
                    req = round(need[(lid, ym, code)], 2)
                    y, m = int(ym[:4]), int(ym[5:])
                    day = date(y, m, 1) - timedelta(days=5)
                    if day < date(2025, 1, 1):
                        continue
                    out.append(ev(day, "CLOSED" if day < END - timedelta(days=30) else "APPROVED", line_id=lid, shift_id=sid, unit_code="HOUR",
                                  quantity=req, base_quantity=round(req * 60, 1), planned_quantity=avail, actual_quantity=req, qualified_quantity=req,
                                  rejected_quantity=0.0, duration_minutes=round(req * 60, 1), capacity_hours=avail, demand_quantity=avail,
                                  supply_quantity=req, backlog_quantity=0.0, utilization_rate=round(min(1.0, req / avail), 4) if avail else 0.0,
                                  rate_value=round(min(1.0, req / avail), 4) if avail else 0.0, planning_period=ym))
        return out

    def fact_kpi_snapshot(self) -> list[dict[str, Any]]:
        """绩效快照:各部门负责人每月一条本部门 KPI,实际值按指标定义从业务数据计算:
        成材率 = 产出/投料;设备综合效率 = 负荷时长加权 OEE;检验批合格率 = 判定合格的检验批/检验批;
        来料准时率 = 不晚于采购订单交期的收货批次/收货批次;销售额达成率 = 部门接单额/部门销售额目标。"""
        out = []
        heads = {}
        for e in sorted(self.rows("dim_employee", "emp_id").values(), key=lambda e: e["emp_id"]):
            heads.setdefault(e["dept_id"], e["emp_id"])
        kpi_for = {7: ("KPI-YIELD", "成材率", 0.985), 9: ("KPI-OEE", "设备综合效率", 0.75), 8: ("KPI-QPASS", "检验批合格率", 0.95),
                   1: ("KPI-SALES", "销售额达成率", 1.0), 2: ("KPI-SALES", "销售额达成率", 1.0), 10: ("KPI-OTD", "来料准时率", 0.90)}
        m: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
        for day, oee, plan in self.q("SELECT record_date, oee, plan_run_hours FROM fact_equipment_runtime"):
            a = m[day[:7]]["oee"]
            a[0] += oee / 100 * plan
            a[1] += plan
        for oid, end_, act in self.q("SELECT order_id, end_time, actual_quantity FROM fact_production_order WHERE status='已完成'"):
            inp = self.q("SELECT SUM(actual_qty) FROM fact_material_consumption WHERE order_id=?", oid)[0][0] or 0.0
            a = m[end_[:7]]["yield"]
            a[0] += act
            a[1] += inp
        for oid, rs in self.q("SELECT p.end_time, GROUP_CONCAT(q.result) FROM fact_quality_inspection q JOIN fact_production_order p USING(order_id) GROUP BY q.order_id"):
            a = m[oid[:7]]["qpass"]
            a[0] += 0.0 if ("不合格" in rs or "让步接收" in rs) else 1.0
            a[1] += 1
        for rd, due in self.q("SELECT g.receipt_date, p.delivery_date FROM fact_goods_receipt g JOIN fact_purchase_order p USING(po_id)"):
            a = m[rd[:7]]["otd"]
            a[0] += 1.0 if rd <= due else 0.0
            a[1] += 1
        tgt: dict[tuple[int, str], float] = defaultdict(float)
        emps = self.rows("dim_employee", "emp_id")
        for r in self.q("SELECT emp_id, year, month, sales_amount_target FROM fact_sales_target"):
            tgt[(emps[r[0]]["dept_id"], f"{r[1]}-{r[2]:02d}")] += r[3]
        for o in self.rows("fact_sales_order", "order_id").values():
            a = m[o["order_date"][:7]][f"sales{emps[o['emp_id']]['dept_id']}"]
            a[0] += o["amount"]
        for ym in sorted(m):
            y, mo = int(ym[:4]), int(ym[5:])
            day = date(y + mo // 12, mo % 12 + 1, 3)
            if day > END:
                continue
            for dept, (code, name, target) in kpi_for.items():
                key = {"KPI-YIELD": "yield", "KPI-OEE": "oee", "KPI-QPASS": "qpass", "KPI-OTD": "otd"}.get(code, f"sales{dept}")
                num, den = m[ym][key]
                if code == "KPI-SALES":
                    den = tgt[(dept, ym)]
                if not den:
                    continue
                actual = round(num / den, 4)
                out.append(ev(day, "CLOSED", emp_id=heads[dept], dept_id=dept, kpi_code=code, kpi_name=name, target_value=target,
                              actual_value=actual, achievement_rate=round(actual / target, 4), planning_period=ym, period_type="月度",
                              evaluator_code=FILL.emp_no(self.F.by_role["总经理"][0]),
                              rate_value=min(1.0, actual), score_value=round(min(100.0, actual / target * 100), 1)))
        return out

    def dim_plans(self) -> dict[str, list[dict[str, Any]]]:
        """新维表中与业务强相关的几张,每行的引用与属性一起确定(属性彼此一致,并与 108 表主数据一致)。"""
        if hasattr(self, "_plans"):
            return self._plans
        prods = self.rows("dim_product", "prod_id")
        method = {"拉伸强度": "万能试验机拉伸", "缺口冲击强度": "悬臂梁冲击", "熔体流动速率": "熔指仪法", "密度": "密度天平",
                  "灰分": "马弗炉灼烧", "阻燃等级(1.6mm)": "垂直燃烧"}
        qc = []
        for pid in sorted(prods):
            for k, (name, unit, nom, lo, hi) in enumerate(M.quality_spec(pid), start=1):
                qc.append({"prod_id": pid, "code": f"QC-P{pid:02d}-{k}", "name": f"{name}-{prods[pid]['prod_name']}",
                           "characteristic_type": "计数型" if name.startswith("阻燃") else "计量型", "measurement_method": method[name],
                           "nominal_value": round(nom, 3), "lower_spec_limit": round(lo, 3), "upper_spec_limit": round(hi, 3), "unit_symbol": unit,
                           "precision_scale": 3 if name == "密度" else 2, "sample_frequency": 1, "inspection_level": "Ⅱ",
                           "criticality": "关键" if name in ("拉伸强度", "缺口冲击强度", "阻燃等级(1.6mm)") else "重要",
                           "calibration_required": int(not name.startswith("阻燃")), "control_plan_code": f"CP-P{pid:02d}"})
        # 挤出机关键参数按车间所加工的树脂分别设定(熔体温度与 108 表传感器正常区间一致);辅机参数按设备类型
        family = {1: "PP", 2: "PA", 3: "ABS/PC", 4: "阻燃", 5: "PPS/PPA"}
        press = {1: 5.5, 2: 6.5, 3: 7.0, 4: 6.0, 5: 8.0}
        rpm = {1: 450, 2: 400, 3: 380, 4: 350, 5: 350}
        amp = {1: 260, 2: 290, 3: 300, 4: 270, 5: 310}
        vac = {1: -85, 2: -88, 3: -85, 4: -80, 5: -90}
        params: list[tuple[Any, ...]] = []            # 设备类型, 车间, 名称, 单位, 中心值, 控制限半宽, 规格限半宽, 工序, 产品
        for pid, (lo, hi) in sorted(M.PRODUCT_MELT.items()):  # 熔体温度按牌号设定工艺窗口
            params.append((1, None, "熔体温度", "℃", (lo + hi) / 2, (hi - lo) * 0.3, (hi - lo) / 2, "挤出", pid))
        for ws in range(1, 6):
            params += [(1, ws, "熔体压力", "MPa", press[ws], 0.8, 2.0, "挤出"), (1, ws, "螺杆转速", "rpm", rpm[ws], 15, 40, "挤出"),
                       (1, ws, "主机电流", "A", amp[ws], 25, 55, "挤出"), (1, ws, "真空度", "kPa", vac[ws], 3, 8, "挤出")]
        params += [(2, None, "混合温度", "℃", 55, 8, 15, "混料"), (2, None, "混合时间", "s", 300, 20, 60, "混料"),
                   (3, None, "主喂料速率", "kg/h", 1100, 60, 150, "喂料"), (3, None, "喂料精度", "%", 0.0, 0.3, 0.8, "喂料"),
                   (4, None, "切刀转速", "rpm", 950, 60, 150, "切粒"), (4, None, "冷却水温", "℃", 35, 3, 8, "切粒"),
                   (5, None, "干燥温度", "℃", 85, 4, 10, "干燥"), (5, None, "露点温度", "℃", -38, 3, 7, "干燥"),
                   (6, None, "包装重量", "kg", 25.0, 0.05, 0.1, "包装"), (6, None, "包装速度", "包/h", 240, 20, 50, "包装")]
        group = {"℃": "温度", "MPa": "压力", "kPa": "压力", "rpm": "速度", "kg/h": "速度", "包/h": "速度", "A": "电气"}
        prods = self.rows("dim_product", "prod_id")
        pp = [{"type_id": t, "code": (f"PRM-T{t}-P{x[0]:02d}-{n:02d}" if x else f"PRM-T{t}-W{ws}-{n:02d}" if ws else f"PRM-T{t}-{n:02d}"),
               "name": (f"{name}({prods[x[0]]['prod_name']})" if x else f"{name}({family[ws]})" if ws else name),
               "parameter_group": group.get(u, "其他"), "parameter_unit": u,
               "nominal_value": float(c), "lower_control_limit": round(c - cw, 3), "upper_control_limit": round(c + cw, 3),
               "lower_spec_limit": round(c - sw, 3), "upper_spec_limit": round(c + sw, 3), "sampling_interval_seconds": 5 if t == 1 else 60,
               "collection_method": "PLC 采集", "control_method": "SPC 控制图", "reaction_plan": "超控制限复核,超规格限停机调整",
               "process_stage": st, "_ws": ws, "_base": name, "_prod": x[0] if x else None}
              for n, (t, ws, name, u, c, cw, sw, st, *x) in enumerate(params, start=1)]
        emps = self.rows("dim_employee", "emp_id").values()
        head = defaultdict(int)
        for e in emps:
            head[e["dept_id"]] += 1
        regions = ["华东", "华南", "华北", "西南", "华中", "西北"]
        # 岗位 = 部门 × 建档岗位,编制等于在岗人数
        grade = {"销售经理": ("销售", "M1", 5, "本科"), "销售代表": ("销售", "P5", 2, "本科"), "车间主任": ("管理", "M2", 8, "本科"),
                 "班组长": ("生产", "P5", 3, "大专"), "质检员": ("技术", "P4", 1, "大专"), "质量工程师": ("技术", "P6", 3, "本科"),
                 "设备工程师": ("技术", "P6", 3, "本科"), "维修技师": ("生产", "P5", 3, "中专"), "采购员": ("职能", "P5", 2, "本科"),
                 "仓管员": ("职能", "P4", 1, "大专"), "会计": ("职能", "P5", 2, "本科"), "人事专员": ("职能", "P5", 2, "本科"),
                 "安全工程师": ("技术", "P6", 3, "本科"), "计划员": ("职能", "P5", 2, "本科"), "研发工程师": ("技术", "P7", 3, "硕士"),
                 "总经理": ("管理", "M5", 15, "硕士")}
        count: dict[tuple[int, str], int] = defaultdict(int)
        for e in emps:
            role = self.F.roles[e["emp_id"]]
            count[(e["dept_id"], POSITION_OF.get(role, role))] += 1
        jp = []
        for n, ((d, name), hc) in enumerate(sorted(count.items()), start=1):
            fam, grd, yrs, edu = grade[name]
            jp.append({"dept_id": d, "code": f"JOB-{n:03d}", "name": f"{regions[d - 1]}{name}" if d <= 6 else name, "job_family": fam,
                       "job_grade": grd, "minimum_experience_years": yrs, "education_requirement": edu,
                       "critical_position": int(name in ("班组长", "设备工程师", "安全工程师", "车间主任", "维修技师")),
                       "headcount_plan": hc, "replacement_lead_days": 90 if fam in ("技术", "管理") else 30, "_role": name})
        certs = [("特种设备操作证", "市场监管局", 48, 1, "班组长", 500.0), ("电工证", "应急管理局", 72, 1, "维修技师", 300.0),
                 ("焊工证", "应急管理局", 72, 1, "维修技师", 300.0), ("叉车证", "市场监管局", 48, 1, "仓管员", 300.0),
                 ("危化品安全证", "应急管理局", 36, 1, "安全工程师", 800.0), ("内审员证", "认证机构", 36, 1, "质量工程师", 1500.0),
                 ("安全管理员证", "应急管理局", 36, 1, "安全工程师", 800.0), ("压力容器证", "市场监管局", 48, 1, "设备工程师", 500.0),
                 ("登高作业证", "应急管理局", 72, 1, "设备工程师", 300.0), ("有限空间作业证", "应急管理局", 36, 1, "安全工程师", 300.0),
                 ("消防设施操作员", "人社部门", 60, 1, "安全工程师", 500.0), ("计量检定员", "市场监管局", 60, 1, "质检员", 800.0),
                 ("环境管理体系内审员", "认证机构", 36, 1, "安全工程师", 1500.0), ("6σ 绿带", "行业协会", 120, 0, "质量工程师", 3000.0),
                 ("6σ 黑带", "行业协会", 120, 0, "质量工程师", 8000.0), ("PMP", "项目管理协会", 36, 1, "车间主任", 3900.0)]
        cf = [{"code": f"CERT-{n:03d}", "name": name, "certificate_type": "操作资格" if org in ("市场监管局", "应急管理局", "人社部门") else "专业资质",
               "issuing_authority": org, "validity_months": m, "renewal_required": r, "mandatory_for_role": role, "exam_fee": fee}
              for n, (name, org, m, r, role, fee) in enumerate(certs, start=1)]
        kinds = [("产线扩建", "技改", 6.0e6), ("节能改造", "技改", 1.8e6), ("数字化车间", "技改", 3.5e6), ("环保设施升级", "环保", 2.2e6),
                 ("新品试制", "研发", 0.8e6), ("仓库自动化", "基建", 4.5e6), ("质量实验室建设", "基建", 1.6e6), ("光伏屋顶", "环保", 3.0e6),
                 ("安全隐患整改", "技改", 0.6e6), ("能源计量升级", "技改", 0.9e6)]
        pj = []
        for n in range(1, 21):
            name, cls, budget = kinds[(n - 1) % 10]
            start = date(2025, 1, 6) + timedelta(days=24 * (n - 1))
            end = start + timedelta(days=120 + 30 * ((n * 7) % 8))
            stage = "关闭" if end <= END - timedelta(days=60) else "验收" if end <= END else "实施" if start <= END else "立项"
            owner = {"技改": 9, "环保": 14, "研发": 16, "基建": 7}[cls]
            pj.append({"owner_emp_id": min(e["emp_id"] for e in emps if e["dept_id"] == owner), "enterprise_id": 1,
                       "code": f"PRJ-{start.year}-{n:03d}", "name": f"{name}项目{(n - 1) // 10 + 1}期", "project_type": name, "investment_class": cls,
                       "planned_start_date": start.isoformat(), "planned_end_date": end.isoformat(), "budget_ceiling": budget * (1 + 0.25 * ((n - 1) // 10)),
                       "project_stage": stage, "funding_source": "专项补贴" if cls == "环保" else "自有资金",
                       "approval_level": "董事会" if budget >= 3e6 else "总经理", "cost_object_code": f"CO-PRJ-{n:03d}"})
        self._plans = {"dim_quality_characteristic": qc, "dim_process_parameter": pp, "dim_job_position": jp, "dim_certification": cf,
                       "dim_project": pj}
        return self._plans

    def handlers(self) -> dict[str, Callable[[], list[dict[str, Any]]]]:
        return {name: getattr(self, name) for name in S.FACT_SEMANTICS if hasattr(self, name)}
