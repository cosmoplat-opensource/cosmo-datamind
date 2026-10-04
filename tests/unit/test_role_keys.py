"""同一张子表的多个键指向同一父表(角色键,DR-080):superhero 的 eye/hair/skin_colour_id → colour.id。

每对表只保留一条关系,其余经数据裁决成立的角色键记在 evidence.role_keys;
本体约束校验与 JOIN 提示都认可这些键。BIRD 对比实验中 hair_colour_id 的 JOIN 曾被拦下。"""
from __future__ import annotations

import server


def _ir():
    return {"objects": [{"name": "hero", "table": "superhero", "cn": "英雄"},
                        {"name": "colour", "table": "colour", "cn": "颜色"}],
            "relations": [{"source_concept": "hero", "target_concept": "colour", "status": "verified",
                           "evidence": {"child_key": "eye_colour_id", "parent_key": "id",
                                        "role_keys": [{"child_key": "hair_colour_id", "parent_key": "id", "overlap": 100.0}]}}]}


def test_gate_accepts_verified_role_key_join():
    sql = ("SELECT h.name FROM superhero h JOIN colour c1 ON h.eye_colour_id = c1.id "
           "JOIN colour c2 ON h.hair_colour_id = c2.id WHERE c1.colour = 'Blue'")
    ok, why = server._validate_sql_ontology(sql, _ir(), strict=False)
    assert ok, why
    bad = "SELECT h.name FROM superhero h JOIN colour c ON h.skin_colour_id = c.id"
    assert not server._validate_sql_ontology(bad, _ir(), strict=False)[0]      # 未经裁决的角色键仍拦下


def test_join_hints_list_role_keys():
    hints = "\n".join(server._join_hints(_ir(), ["superhero", "colour"]))
    assert "superhero.eye_colour_id = colour.id" in hints and "superhero.hair_colour_id = colour.id" in hints


def test_adjudication_records_other_verified_role_keys(make_sqlite):
    colours = [(i, c) for i, c in enumerate(["Blue", "Brown", "Green", "Red", "Black", "White"], 1)]
    heroes = [(i, f"H{i}", (i % 6) + 1, ((i + 2) % 6) + 1, ((i + 4) % 6) + 1) for i in range(1, 60)]
    db = make_sqlite({"colour": ("id INTEGER PRIMARY KEY, colour TEXT", colours),
                      "superhero": ("id INTEGER PRIMARY KEY, name TEXT, eye_colour_id INTEGER, hair_colour_id INTEGER, skin_colour_id INTEGER", heroes)})
    extracted = {"objects": [{"name": "hero", "table": "superhero"}, {"name": "colour", "table": "colour"}],
                 "relations": [{"source": "hero", "target": "colour", "verb": "有颜色", "child_key": "eye_colour_id",
                                "parent_key": "id"}]}
    ir = server._adjudicate_ir(db, "t", extracted, server._gather_evidence(db))
    r = next(r for r in ir["relations"] if r.get("status") == "verified")
    roles = {x["child_key"] for x in (r.get("evidence") or {}).get("role_keys") or []}
    assert r["evidence"]["child_key"] == "eye_colour_id"
    assert roles == {"hair_colour_id", "skin_colour_id"}
