#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dao_core —— 数据裁决本体师(Data-Adjudicated Ontologist)的单一裁决核(DR-035)。

背景:此前裁决逻辑有**两份漂移实现**——
  - `quick_build.py`:θ=60 内联、父键精确 100% 唯一、无 MIN_DISTINCT、命名校验用 key_name_ok;
  - `../ontology-engine/engine/relation_discovery.py`:MIN_OVERLAP=60.0、0.95 近似唯一、
    MIN_DISTINCT=3、命名校验用 name_score、排除「子键自身即主键」。
两份各自演化,门槛已经不一致(quick_build 少了 MIN_DISTINCT 与 PK-作子键排除,更易假阳)。

本模块把裁决的**决策逻辑**抽为单一事实源:纯函数、**与数据访问方式无关**
(只吃「已抽取的信号」——子键 distinct 集合、父键 distinct 集合、父键是否唯一、列名),
因此 quick_build 的 SQL 路径(180k 行大表,SQL DISTINCT)与引擎的内存路径(dict of lists)
可以喂各自抽取的信号、共用同一套判定。

裁决口径(与 DR-002/DR-011 一致):
  取值重叠率 = |子键distinct ∩ 父键distinct| / |子键distinct|
  verified   = 重叠≥θ ∧ 父键唯一 ∧ 列名有据 ∧ 子键 distinct≥下限 ∧ 子键非主键
  candidate  = 重叠≥θ 但列名无据(疑自增键值域巧合,送审)/ 或弱重叠(≥weak_floor)
  drop/gap   = 其余
LLM 不得自评 verified;verified 只由数据见证产生。
"""
import re

# ── 单一事实源常量(两引擎此前各自定义,值已漂移)──
MIN_OVERLAP = 60.0        # θ:判 verified 的取值重叠率(%)
MIN_DISTINCT = 3          # 子键去重值下限(太少不可信);quick_build 历史无此校验
UNIQUE_PK_RATIO = 0.95    # 父键近似唯一阈值(distinct/非空行数)
WEAK_FLOOR = 20.0         # 弱重叠 candidate 下限

_GENERIC_KEY = {"id", "code", "no", "key", "num", "number"}
_ABBR_PREFIX_RE = re.compile(r"^[a-z]{2,3}_")
_KEY_SUF_RE = re.compile(r"_?(id|code|key|no|num)$", re.I)


def clean(values):
    """非空值集合 + 非空计数(用于唯一度)。与 relation_discovery._clean 同口径。"""
    vals = [str(v).strip() for v in values if v is not None and str(v).strip() != ""]
    return set(vals), len(vals)


def unique_ratio(values):
    s, n = clean(values)
    return (len(s) / n) if n else 0.0


def is_pk_like(values, ratio=UNIQUE_PK_RATIO):
    """父键候选/主键判定:非空值近似唯一。"""
    return unique_ratio(values) >= ratio


def overlap_pct(child_distinct, parent_distinct):
    """子键 distinct 落在父键值域里的比例(%),**不四舍五入**(保留原始精度,
    边界比较 `>=θ` 与 quick_build 的内联算法逐值一致;显示时调用方自行 round)。
    入参可为集合或可迭代值序列。返回 -1.0 表示子键为空(无从判定)。"""
    cset = child_distinct if isinstance(child_distinct, set) else clean(child_distinct)[0]
    pset = parent_distinct if isinstance(parent_distinct, set) else clean(parent_distinct)[0]
    if not cset:
        return -1.0
    return 100.0 * len(cset & pset) / len(cset)


def key_stem(c):
    """剥尾部 id/code/key/no/num 后缀取词根(quick_build.key_stem 口径)。"""
    return _KEY_SUF_RE.sub("", (c or "").lower()).strip("_")


def _core(col):
    """列名核心词:剥尾部 id/code + 剥 2-3 字母表缩写前缀;泛称或过短视为无核心词
    (relation_discovery._core 口径,用于前缀式列名 we_mo_number↔pm_mo_number 佐证)。"""
    c = re.sub(r"(_?id|_?code)$", "", (col or "").lower()).strip("_")
    c = _ABBR_PREFIX_RE.sub("", c)
    return c if len(c) >= 3 and c not in _GENERIC_KEY else ""


def key_name_ok(child_key, parent_key):
    """子键/父键**词根相容**(值域重叠之外的第二道校验,DR-033)。
    两键词根相等或一方为另一方前缀(限长≥3)。稠密自增代理键之间值域天然 100% 重合,
    仅凭重叠会造假关系。

    **支持复合键**(逗号分隔,如 "c1,c2" ↔ "p1,p2"):逐列判定,列数不等即否,
    全列通过才相容——收编 server._key_name_ok 的复合分支,单一事实源。"""
    cs = [x.strip() for x in str(child_key or "").split(",")]
    ps = [x.strip() for x in str(parent_key or "").split(",")]
    if len(cs) != len(ps):
        return False
    for a, b in zip(cs, ps):
        sa, sb = key_stem(a), key_stem(b)
        if not sa or not sb or sa == sb:
            continue
        if min(len(sa), len(sb)) >= 3 and (sa.startswith(sb) or sb.startswith(sa)):
            continue
        return False
    return True


def name_score(child_col, parent_table, parent_col, synonyms=None):
    """列名一致性打分(relation_discovery 口径,比 key_name_ok 更富:
    2=子父键同名 / 子列名含父表名;1=去后缀相关 / 前缀缩写核心词一致 / 同义词;0=无关。
    比 key_name_ok 多考虑「父表名」与「异名同义」两个信号。"""
    cc, pt, pc = child_col.lower(), parent_table.lower(), parent_col.lower()
    if cc == pc or pt in cc:
        return 2
    stem = re.sub(r"(_?id|_?code)$", "", cc).strip("_")
    if stem and (stem in pt or pt in stem or stem in pc):
        return 1
    if _core(cc) and _core(cc) == _core(pc):
        return 1
    if synonyms:
        cs = synonyms.get(cc) or set()
        ps = synonyms.get(pc) or set()
        if cs and ps and (cs & ps):
            return 1
    return 0


# ── DR-036:自引用/角色键 ──
# 角色键=语义上「指向某实体」的列名。目标含 "self" 者可指向本表(层级自引用);
# 其余为属类词(genus),匹配父表名。仅这些已知模式获得放开待遇,不广泛放宽——
# 自引用仍需过数据裁决(重叠≥θ∧父键唯一),名校验只是补上「值域重叠之外」那一环。
_ROLE_KEYS = {
    "reports_to": ("self", "employee", "staff", "person", "user", "emp"),
    "manager": ("self", "employee", "staff", "person", "user", "emp"),
    "supervisor": ("self", "employee", "staff", "person", "user"),
    "mgr": ("self", "employee", "staff", "person", "user"),
    "boss": ("self", "employee", "staff", "person"),
    "parent": ("self",),
    "predecessor": ("self",),
    "successor": ("self",),
    "prior": ("self",),
    "prev": ("self",),
    "next": ("self",),
}


def _snake(col):
    """驼峰归一:ReportsTo→reports_to、ManagerId→manager_id(角色键常无下划线)。"""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", col or "")
    return s.lower()


def role_targets(col):
    """列名的语义角色目标(DR-036)。返回目标提示元组(可含 'self');非角色键返回 ()。
    先驼峰归一 + 剥尾部 id/code,再直接命中或按 <role>_ 前缀/_<role> 后缀命中。"""
    base = re.sub(r"(_?id|_?code)$", "", _snake(col)).strip("_")
    if base in _ROLE_KEYS:
        return _ROLE_KEYS[base]
    for role, targets in _ROLE_KEYS.items():
        if base.startswith(role + "_") or base.endswith("_" + role):
            return targets
    return ()


def name_ok(child_col, parent_table, parent_key, child_table=None):
    """统一命名相容判定(DR-036 扩展):
    1) 先走 key_name_ok(两键词根)—— 非角色键与此完全等价,既有行为不变;
    2) 角色键补两条路径:self→父表即子表(层级自引用);genus→属类词是父表名子串。"""
    if key_name_ok(child_col, parent_key):
        return True
    rt = role_targets(child_col)
    if rt:
        pt = (parent_table or "").lower()
        if "self" in rt and child_table and pt == child_table.lower():
            return True
        if any(g != "self" and g in pt for g in rt):
            return True
    return False


# ── DR-037:包含方向测试 ──
# 真 N:1 外键中,「多侧」(子)的值域应 ⊂「一侧」(父,唯一)的值域。
# 仅凭「子值域落在父值域」不足以定向——若子列自身也唯一(常是被误当子键的主键),
# 方向可能是反的。用两侧唯一度定向:唯一的一侧才是父。
def fk_direction(a_unique, b_unique):
    """由两列唯一度推断外键方向:
    'a->b' = A 是子(多侧)、B 是父(唯一);'b->a' 反之;
    'ambiguous' = 皆唯一(1:1,方向不定);'none' = 皆不唯一(非 N:1,疑多对多)。"""
    if a_unique and not b_unique:
        return "b->a"
    if b_unique and not a_unique:
        return "a->b"
    if a_unique and b_unique:
        return "ambiguous"
    return "none"


def should_reverse(child_unique, parent_unique):
    """记录的边为 child→parent;若 child 自身唯一而 parent 不唯一,则方向反了
    (真方向 parent→child)。双唯一(1:1)不强制反,保留原样待人审。"""
    return bool(child_unique) and not bool(parent_unique)


# ── DR-038:基数自适应 θ ──
# 「高基数子键命中 X% 重叠」比「5 值枚举命中 X%」证据强得多(偶合概率随基数急降)。
# 故 θ 应随子键 distinct 上升而下调:低基数维持严阈(防枚举偶合),高基数适度放宽(保召回)。
# 经反幻觉评测台验证:distinct≥50 降到 50 → 高基数部分重叠真 FK 召回 0.8→1.0,泄漏率不变。
# 边界:再高也不低于地板 50,避免把稀疏偶合稀释成「真」。
_ADAPT_HIGH = 50          # 高基数拐点:distinct≥此值起放宽
_ADAPT_FLOOR = 50.0       # θ 下限


def adaptive_theta(child_distinct, base=MIN_OVERLAP):
    """基数自适应重叠阈:distinct < 拐点 → base(严);≥拐点 → 地板(宽)。
    刻意用阶跃而非连续函数——拐点与地板都可由评测台标定、可回归,不引入难解释的曲线。"""
    return _ADAPT_FLOOR if child_distinct >= _ADAPT_HIGH else base


def classify(*, overlap, parent_unique, name_ok, child_distinct,
             child_is_pk=False, theta=MIN_OVERLAP, min_distinct=MIN_DISTINCT,
             weak_floor=WEAK_FLOOR, exclude_pk_child=True):
    """统一裁决决策(数据无关,吃已抽取信号)。返回 {status, reason, name_mismatch?}。

    status ∈ {verified, candidate, drop}。
    - child_is_pk + exclude_pk_child:子键自身唯一(疑为主键)→ 不作一对多的多侧(drop)。
    - child_distinct < min_distinct → 去重值太少不可信(drop)。
    - overlap≥θ ∧ parent_unique ∧ name_ok → verified。
    - overlap≥θ ∧ parent_unique ∧ ¬name_ok → candidate(疑自增键值域巧合,送审)。
    - weak_floor ≤ overlap < θ → candidate(弱重叠,送审)。
    - 其余 → drop。

    兼容模式(复现 quick_build 历史行为):min_distinct=1, exclude_pk_child=False。
    """
    if child_is_pk and exclude_pk_child:
        return {"status": "drop", "reason": "子键自身唯一(疑为主键),不作为一对多关系的多侧"}
    if child_distinct < min_distinct:
        return {"status": "drop", "reason": f"子键去重值 {child_distinct} < 下限 {min_distinct},不可信"}
    if overlap >= theta and parent_unique:
        if name_ok:
            return {"status": "verified", "reason": f"重叠 {overlap:.0f}%·父键唯一·列名有据"}
        return {"status": "candidate", "name_mismatch": True,
                "reason": f"重叠 {overlap:.0f}% 但列名词根不一致,疑为自增键值域巧合,送审"}
    if overlap >= weak_floor:
        return {"status": "candidate", "reason": f"弱重叠 {overlap:.0f}%,送审"}
    return {"status": "drop", "reason": f"重叠 {overlap:.0f}% 不足"}
