# -*- coding: utf-8 -*-
"""反幻觉评测台的带标签基准(IR-009 / DR-039)。

构造一个合成库 + 一批**带真值标签**的候选关系:真外键 + 植入的假边
(代理键碰撞、枚举巧合、方向反、共享维度键)。喂给裁决器,量化
「verify 真的、拒假的」能力——把 DAO 的反幻觉主张变成可回归的数字。

另附 DR-038 探针case(高基数部分重叠的真 FK / 低基数同名巧合的假边),
用于验证「固定 θ 的两类失效」以及自适应门槛是否真能改善。

纯确定性、离线;不需 LLM——裁决器本身就是防线,评测的正是这道防线。
"""


def build(db_path):
    """建合成基准库,返回 db_path。刻意用取模构造确定性数据(无随机)。"""
    import sqlite3
    con = sqlite3.connect(db_path)
    x = con.executescript
    x("""
    CREATE TABLE customers(customer_id INTEGER PRIMARY KEY, name TEXT);
    CREATE TABLE products(product_id INTEGER PRIMARY KEY, pname TEXT);
    CREATE TABLE orders(order_id INTEGER PRIMARY KEY, customer_id INTEGER, status TEXT, level INTEGER);
    CREATE TABLE order_items(item_id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, qty INTEGER);
    CREATE TABLE widgets(widget_id INTEGER PRIMARY KEY, wname TEXT);
    CREATE TABLE ref_status(status_code TEXT PRIMARY KEY, label TEXT);
    CREATE TABLE storage_bins(bin_id INTEGER PRIMARY KEY, level INTEGER);
    CREATE TABLE order_events(event_id INTEGER PRIMARY KEY, customer_id INTEGER);
    """)
    con.executemany("INSERT INTO customers VALUES(?,?)", [(i, f"c{i}") for i in range(1, 101)])
    con.executemany("INSERT INTO products VALUES(?,?)", [(i, f"p{i}") for i in range(1, 51)])
    # orders.customer_id 全落 customers(多侧,重复);status∈{A,B,C};level∈{1,2,3}(优先级,低基数)
    con.executemany("INSERT INTO orders VALUES(?,?,?,?)",
                    [(i, (i % 100) + 1, "ABC"[i % 3], (i % 3) + 1) for i in range(1, 301)])
    con.executemany("INSERT INTO order_items VALUES(?,?,?,?)",
                    [(i, (i % 300) + 1, (i % 50) + 1, (i % 9) + 1) for i in range(1, 501)])
    con.executemany("INSERT INTO widgets VALUES(?,?)", [(i, f"w{i}") for i in range(1, 101)])
    con.executemany("INSERT INTO ref_status VALUES(?,?)", [(c, c) for c in "ABC"])
    # storage_bins.level∈{1,2,3,4}(存储层级,与 orders.level 语义无关,值域巧合),唯一
    con.executemany("INSERT INTO storage_bins VALUES(?,?)", [(i, i) for i in range(1, 5)])
    # order_events.customer_id:80 个distinct,仅 46 落在 customers(高基数、部分重叠 57.5%)
    con.executemany("INSERT INTO order_events VALUES(?,?)",
                    [(i, (i % 100) + 1 if i <= 46 else 200 + i) for i in range(1, 81)])
    con.commit()
    con.close()
    return db_path


# 带标签候选:is_true_fk = 该关系是否为真外键;cat = 类别(用于分组统计与 DR-038 过滤)
CANDIDATES = [
    # ── 干净真 FK(名匹配,应 verified)──
    {"child_table": "orders", "child_col": "customer_id",
     "parent_table": "customers", "parent_col": "customer_id", "is_true_fk": True, "cat": "clean_true"},
    {"child_table": "order_items", "child_col": "order_id",
     "parent_table": "orders", "parent_col": "order_id", "is_true_fk": True, "cat": "clean_true"},
    {"child_table": "order_items", "child_col": "product_id",
     "parent_table": "products", "parent_col": "product_id", "is_true_fk": True, "cat": "clean_true"},
    # ── 植入假边(应被拒:不得 verified)──
    # 代理键碰撞:customer_id{1..100} 与 widget_id{1..100} 100%重合但语义无关(名不符)
    {"child_table": "customers", "child_col": "customer_id",
     "parent_table": "widgets", "parent_col": "widget_id", "is_true_fk": False, "cat": "surrogate_collision"},
    # 低基数真 FK:orders.status → ref_status.status_code(状态码引用状态维;名词根同 status;应 verified)。
    # 与下方 dr038_lowcard_coincidence 同为低基数(distinct=3),但一真一假、信号完全相同——
    # 这对「孪生案例」正是数据裁决的固有极限:确定性信号无法区分真码表与巧合同名。
    {"child_table": "orders", "child_col": "status",
     "parent_table": "ref_status", "parent_col": "status_code", "is_true_fk": True, "cat": "clean_true"},
    # 方向反:dim 的 PK(customers.customer_id 唯一)被 fact(orders)引用 → 反向应被抑制
    {"child_table": "customers", "child_col": "customer_id",
     "parent_table": "orders", "parent_col": "customer_id", "is_true_fk": False, "cat": "reverse"},
    # ── DR-038 探针 ──
    # 高基数部分重叠的真 FK:order_events.customer_id 高基数但仅 57.5% 落 customers(孤儿/迟到维)
    {"child_table": "order_events", "child_col": "customer_id",
     "parent_table": "customers", "parent_col": "customer_id", "is_true_fk": True, "cat": "dr038_highcard_partial"},
    # 低基数同名巧合的假边:orders.level(优先级1-3)与 storage_bins.level(存储层1-4)同名、100%含入,但无关
    {"child_table": "orders", "child_col": "level",
     "parent_table": "storage_bins", "parent_col": "level", "is_true_fk": False, "cat": "dr038_lowcard_coincidence"},
]
