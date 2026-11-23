"""Run explicitly with pytest on a field node. See tests/field/README.md."""
from collections import Counter
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import pytest


SQL = ("SELECT SUM(v) AS total, CAST(NULL AS INT) AS missing "
       "FROM (SELECT 11 AS v UNION ALL SELECT 22 AS v) t")


@pytest.fixture(scope="module")
def engine():
    kind = os.environ.get("DATAMIND_FIELD_KIND")
    dsn = os.environ.get("DATAMIND_FIELD_DSN", "")
    uri = urlsplit(dsn.removeprefix("jdbc:"))
    scheme = {"doris": "mysql", "hive": "hive2"}.get(kind)
    if not scheme or uri.scheme != scheme or not uri.hostname or not uri.path.strip("/"):
        pytest.fail("Set the engine and its JDBC URL with a host and database.", pytrace=False)
    if uri.username is not None or uri.password is not None or not os.environ.get("DATAMIND_FIELD_USER"):
        pytest.fail("Provide the field account separately from the JDBC URL.", pytrace=False)
    return kind


@pytest.fixture(scope="module")
def registered(engine):
    from server import app

    if engine == "hive" and os.environ.get("DATAMIND_FIELD_HIVE_CLASSPATH"):
        # DataMind reaches HiveServer2 through the same JDBC driver (hive_bridge, DR-163)
        os.environ.setdefault("DATAMIND_HIVE_JDBC_CLASSPATH", os.environ["DATAMIND_FIELD_HIVE_CLASSPATH"])
    client = app.test_client()
    resp = client.post("/api/build/connect", json={
        "kind": engine, "name": "现场只读测试", "dsn": os.environ["DATAMIND_FIELD_DSN"],
        "user": os.environ["DATAMIND_FIELD_USER"], "password": os.environ.get("DATAMIND_FIELD_PASSWORD", ""),
    })
    if resp.status_code != 200 or not resp.get_json().get("ok"):
        pytest.fail("DataMind could not register the field connection.", pytrace=False)
    return client, resp.get_json()["conn"]["id"]


def live_data(response):
    body = response.get_json(silent=True) or {}
    if response.status_code != 200 or body.get("error") or body.get("live") is not True:
        # Driver error messages can include addresses and account names.
        pytest.fail(f"DataMind did not return live data (HTTP {response.status_code}).", pytrace=False)
    return body


def test_native_identity_and_aggregate(engine):
    if engine == "hive":
        classpath = os.environ.get("DATAMIND_FIELD_HIVE_CLASSPATH")
        if not classpath:
            pytest.fail("Set DATAMIND_FIELD_HIVE_CLASSPATH for the JDBC driver.", pytrace=False)
        program = Path(__file__).with_name("FieldHiveCheck.java")
        try:
            proc = subprocess.run(["java", "-cp", classpath, str(program), SQL],
                                  capture_output=True, timeout=45)
        except subprocess.TimeoutExpired as exc:
            stage = "launcher"
            for line in (exc.stdout or b"").splitlines():
                if line in (b"connect", b"identity", b"aggregate", b"tables"):
                    stage = line.decode("ascii")
            pytest.fail(f"Hive JDBC exceeded the 45 s deadline at {stage}.", pytrace=False)
        if proc.returncode:
            stage = {2: "connect", 3: "identity", 4: "aggregate", 5: "tables"}.get(proc.returncode, "launcher")
            pytest.fail(f"Hive JDBC failed at {stage}.", pytrace=False)
        return

    import pymysql  # type: ignore[import-untyped]

    uri = urlsplit(os.environ["DATAMIND_FIELD_DSN"].removeprefix("jdbc:"))
    stage = "connect"
    try:
        with pymysql.connect(host=uri.hostname, port=uri.port or 9030, database=uri.path[1:],
                             user=os.environ["DATAMIND_FIELD_USER"], password=os.getenv("DATAMIND_FIELD_PASSWORD", ""),
                             connect_timeout=6, read_timeout=20) as db, db.cursor() as cursor:
            stage = "identity"
            cursor.execute("SELECT @@version_comment")
            if "doris" not in str(cursor.fetchone()[0]).lower():
                pytest.fail("The MySQL-protocol endpoint did not identify as Doris.", pytrace=False)
            stage = "aggregate"
            cursor.execute(SQL)
            if cursor.fetchall() != ((33, None),):
                pytest.fail("Unexpected native aggregate or NULL.", pytrace=False)
    except pymysql.MySQLError:
        pytest.fail(f"Doris driver failed at {stage}.", pytrace=False)


@pytest.mark.parametrize("path", ["/api/query", "/api/viz/run"])
def test_workbench_and_chart_return_reference_values(registered, path):
    client, source = registered
    body = live_data(client.post(path, json={"src": source, "sql": SQL}))
    assert body["columns"] == ["total", "missing"]
    assert body["rows"] == [{"total": 33, "missing": None}]


def test_table_catalog(registered):
    client, source = registered
    tables = live_data(client.get("/api/conn/tables", query_string={"src": source}))["tables"]
    fixture = os.environ.get("DATAMIND_FIELD_PREVIEW_TABLE")
    # cols is None when the server could not report it in time; a designated fixture must still have a real count
    if not isinstance(tables, list) or not all(t.get("name") and (t.get("cols") is None or isinstance(t["cols"], int))
                                               for t in tables):
        pytest.fail("Invalid table catalog.", pytrace=False)
    if fixture and not any(t["name"] == fixture and (t["cols"] or 0) > 0 for t in tables):
        pytest.fail("The designated fixture or its column count is missing.", pytrace=False)


def test_preview_designated_fixture(registered):
    table = os.environ.get("DATAMIND_FIELD_PREVIEW_TABLE")
    if not table:
        pytest.skip("No synthetic preview table designated.")
    client, source = registered
    body = live_data(client.get("/api/conn/preview", query_string={"src": source, "table": table}))
    if body.get("columns") != ["v"] or not isinstance(body.get("rows"), list):
        pytest.fail("Unexpected fixture preview shape.", pytrace=False)
    values = [row["v"] for row in body["rows"] if isinstance(row, dict) and "v" in row]
    if len(body["rows"]) != 4 or Counter(values) != Counter([0, 11, 22, None]):
        pytest.fail("The preview did not return the four designated fixture values.", pytrace=False)
