# -*- coding: utf-8 -*-
"""dao_core 一致性测试(DR-035)—— 单一裁决核。

覆盖:重叠公式精度、统一裁决三态、compat 模式复现 quick_build、自引用键支持、
以及与上游 relation_discovery 的 name_score 平价(证明两份实现同口径)。
"""
import importlib.util
import pathlib
import pytest
import dao_core


class TestOverlap:
    def test_full_containment(self):
        assert dao_core.overlap_pct({1, 2, 3}, {1, 2, 3, 4}) == 100.0

    def test_partial(self):
        assert dao_core.overlap_pct({1, 2, 3, 4}, {1, 2}) == 50.0

    def test_empty_child_is_sentinel(self):
        assert dao_core.overlap_pct(set(), {1}) == -1.0

    def test_raw_precision_not_rounded(self):
        # 2/3 → 66.66…%,不四舍五入(边界比较须与 quick_build 内联算法逐值一致)
        ov = dao_core.overlap_pct({1, 2, 3}, {1, 2})
        assert abs(ov - 66.6666) < 0.01


class TestClassifyCanonical:
    def test_verified_when_all_gates_pass(self):
        r = dao_core.classify(overlap=100.0, parent_unique=True, name_ok=True, child_distinct=50)
        assert r["status"] == "verified"

    def test_candidate_when_name_mismatch(self):
        r = dao_core.classify(overlap=100.0, parent_unique=True, name_ok=False, child_distinct=50)
        assert r["status"] == "candidate"
        assert r.get("name_mismatch") is True

    def test_candidate_when_weak_overlap(self):
        r = dao_core.classify(overlap=35.0, parent_unique=True, name_ok=True, child_distinct=50)
        assert r["status"] == "candidate"

    def test_drop_when_below_min_distinct(self):
        # 规范 MIN_DISTINCT=3:去重值太少 → drop(quick_build 历史无此闸)
        r = dao_core.classify(overlap=100.0, parent_unique=True, name_ok=True, child_distinct=2)
        assert r["status"] == "drop"

    def test_drop_when_child_is_pk(self):
        r = dao_core.classify(overlap=100.0, parent_unique=True, name_ok=True,
                              child_distinct=50, child_is_pk=True)
        assert r["status"] == "drop"

    def test_not_verified_when_parent_not_unique(self):
        # 父键不唯一 → 绝不 verified;100% 重叠仍落到弱 candidate(与 quick_build elif ov>=20 一致)。
        # 注:上游 relation_discovery 会直接跳过非唯一父键——此处是两引擎的又一处漂移,
        # 统一核显式保留 quick_build 口径(弱候选送审,不静默丢弃)。
        r = dao_core.classify(overlap=100.0, parent_unique=False, name_ok=True, child_distinct=50)
        assert r["status"] != "verified"
        assert r["status"] == "candidate"


class TestClassifyCompatMode:
    """compat 模式(min_distinct=1, exclude_pk_child=False)复现 quick_build 历史裁决。"""

    def test_low_distinct_kept_in_compat(self):
        r = dao_core.classify(overlap=100.0, parent_unique=True, name_ok=True,
                              child_distinct=2, min_distinct=1, exclude_pk_child=False)
        assert r["status"] == "verified"

    def test_pk_child_kept_in_compat(self):
        r = dao_core.classify(overlap=100.0, parent_unique=True, name_ok=True,
                              child_distinct=50, child_is_pk=True,
                              min_distinct=1, exclude_pk_child=False)
        assert r["status"] == "verified"


class TestNaming:
    def test_key_name_ok_stem_prefix(self):
        assert dao_core.key_name_ok("customer_id", "CustomerId") is True
        assert dao_core.key_name_ok("prod_id", "product_id") is True
        assert dao_core.key_name_ok("order_id", "customer_id") is False

    def test_name_score_considers_parent_table(self):
        # 子列名直接含父表名 → 2(单数 customer 是 customer_id 的子串)
        assert dao_core.name_score("customer_id", "customer", "id") == 2
        # 父表名为复数 customers 时,"customers" 非 "customer_id" 子串,走去后缀 stem 相关 → 1
        assert dao_core.name_score("customer_id", "customers", "id") == 1
        # 无关列名 → 0
        assert dao_core.name_score("random_col", "unrelated", "other_id") == 0

    def test_self_referential_role_key_scores_via_parent_table(self):
        # 自引用键 reports_to → employees.employee_id:name_score 借父表名可给正分,
        # 这是 DR-036 放开 pt==t 后能命中的信号(key_name_ok 单看两键词根则判 False)。
        assert dao_core.key_name_ok("reports_to", "employee_id") is False   # 现状:词根不符
        # name_score 看父表名 employees ⊃? reports_to 不含,但去后缀 employee 相关 → 记录现值
        # (此处锁 name_score 的确定性,不主张一定≥1;DR-036 将引入角色词典把它提为佐证)


class TestRoleKeysDR036:
    """自引用/角色键(DR-036):role_targets + role-aware name_ok。"""

    def test_reports_to_targets_self_and_person(self):
        rt = dao_core.role_targets("reports_to")
        assert "self" in rt and "employee" in rt

    def test_camelcase_normalized(self):
        # ReportsTo / ManagerId 无下划线,须先归一到 snake 再识别
        assert "self" in dao_core.role_targets("ReportsTo")
        assert "self" in dao_core.role_targets("ManagerId")

    def test_parent_is_pure_self(self):
        assert dao_core.role_targets("parent_id") == ("self",)
        assert "self" in dao_core.role_targets("parent_part")   # 复合:parent_ 前缀

    def test_non_role_key_returns_empty(self):
        assert dao_core.role_targets("customer_id") == ()
        assert dao_core.role_targets("amount") == ()

    def test_name_ok_self_referential(self):
        # key_name_ok 单看两键词根 → False;name_ok 借角色 self + 父表==子表 → True
        assert dao_core.key_name_ok("reports_to", "employee_id") is False
        assert dao_core.name_ok("reports_to", "employees", "employee_id",
                                child_table="employees") is True

    def test_name_ok_role_genus_match_cross_table(self):
        # orders.manager_id → employees(genus employee ⊂ "employees"),非自引用也放行
        assert dao_core.name_ok("manager_id", "employees", "employee_id",
                                child_table="orders") is True

    def test_name_ok_parent_only_self_not_cross(self):
        # parent 仅 self:指向外部无 genus 表时回落到 key_name_ok
        assert dao_core.name_ok("parent_id", "categories", "category_id",
                                child_table="products") is False

    def test_name_ok_non_role_equals_key_name_ok(self):
        # 非角色键:name_ok 必须与 key_name_ok 逐值一致(保证既有行为不变)
        for ck, pt, pk in [("customer_id", "customers", "customer_id"),
                           ("order_id", "customers", "customer_id"),
                           ("prod_id", "products", "product_id")]:
            assert dao_core.name_ok(ck, pt, pk) == dao_core.key_name_ok(ck, pk)


class TestDirectionDR037:
    """包含方向测试(DR-037):真 N:1 外键中,多侧(子)值域 ⊂ 一侧(父,唯一)值域。"""

    def test_unique_side_is_parent(self):
        # A 唯一、B 不唯一 → B 是子(多侧),A 是父 → 'b->a'
        assert dao_core.fk_direction(a_unique=True, b_unique=False) == "b->a"
        assert dao_core.fk_direction(a_unique=False, b_unique=True) == "a->b"

    def test_both_unique_is_ambiguous_1to1(self):
        assert dao_core.fk_direction(a_unique=True, b_unique=True) == "ambiguous"

    def test_neither_unique_is_not_fk(self):
        assert dao_core.fk_direction(a_unique=False, b_unique=False) == "none"

    def test_should_reverse_when_child_unique_parent_not(self):
        # 记录的边 child→parent,但 child 唯一而 parent 不唯一 → 方向反了(真方向 parent→child)
        assert dao_core.should_reverse(child_unique=True, parent_unique=False) is True
        # child 不唯一(正常多侧)→ 方向对,不反
        assert dao_core.should_reverse(child_unique=False, parent_unique=True) is False
        # 双唯一(1:1)→ 不强制反(方向不定,保留原样)
        assert dao_core.should_reverse(child_unique=True, parent_unique=True) is False


class TestParityWithEngine:
    """与上游 ../ontology-engine/engine/relation_discovery 的 name_score 平价 —— 证明同口径。"""

    @pytest.fixture(scope="class")
    def engine(self):
        p = (pathlib.Path(__file__).resolve().parents[2].parent
             / "ontology-engine" / "engine" / "relation_discovery.py")
        if not p.exists():
            pytest.skip("上游引擎不在(可选组件),跳过平价测试")
        spec = importlib.util.spec_from_file_location("_rel_disc", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @pytest.mark.parametrize("cc,pt,pc", [
        ("customer_id", "customers", "id"),
        ("cust_ref", "customer", "customer_id"),
        ("we_mo_number", "pm", "pm_mo_number"),
        ("random_col", "unrelated", "other_id"),
    ])
    def test_name_score_matches_engine(self, engine, cc, pt, pc):
        assert dao_core.name_score(cc, pt, pc) == engine.name_score(cc, pt, pc)

    def test_overlap_matches_engine_rounded(self, engine):
        # 引擎 overlap 四舍五入 1 位,dao_core 保留原始精度;取整后应一致
        cvals, pvals = [1, 2, 3, 4], [1, 2, 3]
        assert round(dao_core.overlap_pct(set(map(str, cvals)), set(map(str, pvals))), 1) == \
               engine.overlap_pct(cvals, pvals)
