"""SQL 结果的列和值、已保存看板的图表数量必须能逐项还原。"""
import re

import pytest
import server


@pytest.mark.parametrize("route", ["/api/query", "/api/viz/run"])
@pytest.mark.parametrize("names", [("left_value", "right_value"), ("n", "n"), ("n", "N")])
def test_query_columns_preserve_both_values_or_explain_ambiguity(route, names):
    sql = f'SELECT 11 AS "{names[0]}", 22 AS "{names[1]}"'
    response = server.app.test_client().post(route, json={"sql": sql, "src": "demo"})
    data = response.get_json()
    if names[0].lower() == names[1].lower() and 400 <= response.status_code < 500:
        assert re.search(r"重复|重名|同名|歧义|别名|duplicate|ambig|unique", str(data.get("error")), re.I), data
        return
    assert response.status_code == 200 and "error" not in data
    assert [data["rows"][0][column] for column in data["columns"]] == [11, 22]


def test_generated_labels_preserve_explicit_aliases_and_do_not_depend_on_rows():
    sql = 'SELECT 11 AS n, 22 AS n, 33 AS N_2, 44 AS N'
    client = server.app.test_client()
    for suffix, expected in [("", [[11, 22, 33, 44]]), (" WHERE 0", [])]:
        data = client.post("/api/query", json={"src": "demo", "sql": sql + suffix}).get_json()
        assert data["columns"] == ["n", "n_3", "N_2", "N_4"]
        assert [[r[c] for c in data["columns"]] for r in data["rows"]] == expected


@pytest.mark.parametrize("count", [24, 25])
def test_board_update_preserves_all_charts_or_rejects_without_overwrite(count, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_VIZ_BOARDS_F", str(tmp_path / "boards.json"))
    client = server.app.test_client()
    charts = [{"title": f"图{i}", "sql": f"SELECT {i} AS n", "type": "kpi",
               "src": "demo", "x": "n", "ys": ["n"]} for i in range(count)]
    original = charts[:1]
    created = client.post("/api/viz/save", json={"name": "测试看板", "charts": original})
    assert created.status_code == 200
    bid = created.get_json()["id"]
    saved = client.post("/api/viz/save", json={"id": bid, "name": "测试看板", "charts": charts})
    stored = next(b for b in client.get("/api/viz/boards").get_json() if b["id"] == bid)
    if count > 24 and 400 <= saved.status_code < 500:
        assert saved.get_json().get("error") and stored["charts"] == original
    else:
        assert saved.status_code == 200 and saved.get_json()["ok"]
        assert stored["charts"] == charts
