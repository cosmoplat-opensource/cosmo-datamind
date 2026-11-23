"""HiveServer2 live access through the JDBC bridge (DR-163): bridge protocol, error handling and the HTTP surfaces."""
import json
import shutil
import subprocess

import pytest

import hive_bridge
import server


class Done:
    def __init__(self, payload, returncode=0):
        self.stdout = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
        self.returncode = returncode


@pytest.fixture
def driver(tmp_path, monkeypatch):
    jar = tmp_path / "hive-jdbc-standalone.jar"
    jar.write_bytes(b"")
    monkeypatch.setenv("DATAMIND_HIVE_JDBC_CLASSPATH", str(jar))
    monkeypatch.setenv("DATAMIND_JAVA", "/opt/jdk/bin/java")
    monkeypatch.setattr(hive_bridge, "_compiled", lambda java: None)
    return jar


@pytest.mark.skipif(not shutil.which("java"), reason="needs a JDK")
@pytest.mark.parametrize("url,database", [("jdbc:hive2://h:10000/sales", "sales"), ("jdbc:hive2://h:10000", "default"),
                                          ("jdbc:hive2://h:10000/", "default"),
                                          ("jdbc:hive2://h:10000/ods;transportMode=binary?spark.sql.defaultCatalog=paimon", "ods")])
def test_bridge_source_writes_valid_json_and_reads_the_database_from_the_url(url, database):
    # Connection.getSchema() would run SELECT current_database(), a query job; the bridge reads the URL instead
    proc = subprocess.run([shutil.which("java"), str(hive_bridge.BRIDGE_SOURCE)],
                          input=f"selftest\n{url}\n\n\n1\n".encode(), capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-400:]
    assert json.loads(proc.stdout) == {"ok": True, "text": "quote\" backslash\\ tab\t newline\n 控制\u0001 中文",
                                       "database": database}


@pytest.mark.skipif(not shutil.which("javac"), reason="needs javac")
def test_bridge_compiles_without_the_driver_on_the_classpath(tmp_path):
    proc = subprocess.run([shutil.which("javac"), "-encoding", "UTF-8", "-d", str(tmp_path), str(hive_bridge.BRIDGE_SOURCE)],
                          capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-400:]


@pytest.mark.skipif(not shutil.which("javac"), reason="needs javac")
def test_compiled_bridge_is_cached_and_launched_by_class_name(tmp_path, monkeypatch):
    monkeypatch.setattr(hive_bridge.tempfile, "gettempdir", lambda: str(tmp_path))
    first = hive_bridge._compiled(shutil.which("java"))
    assert first and (first / "HiveJdbcBridge.class").exists()
    assert hive_bridge._compiled(shutil.which("java")) == first
    jar = tmp_path / "driver.jar"
    jar.write_bytes(b"")
    monkeypatch.setenv("DATAMIND_HIVE_JDBC_CLASSPATH", str(jar))
    seen = []
    hive_bridge.call("query", "jdbc:hive2://h:10000/d", runner=lambda cmd, **kw: seen.append(cmd) or Done(
        {"ok": True, "columns": [], "rows": []}))
    assert seen[0][-1] == "HiveJdbcBridge" and str(first) in seen[0][2]


def test_missing_driver_file_and_silent_bridge_are_reported(driver, monkeypatch, tmp_path):
    with pytest.raises(hive_bridge.HiveUnavailable, match="没有返回结果"):
        hive_bridge.call("query", "jdbc:hive2://h:10000/d", runner=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b""))
    monkeypatch.setenv("DATAMIND_HIVE_JDBC_CLASSPATH", str(tmp_path / "absent.jar"))
    with pytest.raises(hive_bridge.HiveUnavailable, match="不存在"):
        hive_bridge.call("query", "jdbc:hive2://h:10000/d")


@pytest.mark.parametrize("stage,message,expected", [("driver", "ClassNotFound", "驱动无法加载"),
                                                   ("query", "ParseException near 'FROMM'", "执行失败(query)")])
def test_other_failures_name_their_stage(stage, message, expected):
    assert expected in hive_bridge.explain({"stage": stage, "message": message})


def test_request_goes_over_stdin_and_rows_keep_types(driver):
    seen = {}

    def runner(cmd, **kw):
        seen["cmd"], seen["input"] = cmd, kw["input"].decode()
        return Done({"ok": True, "product": "Spark SQL", "version": "3.2.4", "columns": ["total", "missing"],
                     "rows": [[33, None]], "truncated": False})

    out = hive_bridge.query("jdbc:hive2://warehouse.invalid:10000/fixture", "reader", "s3cret", "SELECT 1", 20, runner)
    assert out == {"columns": ["total", "missing"], "rows": [[33, None]], "truncated": False, "engine": "Spark SQL 3.2.4"}
    assert "s3cret" not in " ".join(seen["cmd"])
    assert seen["input"].split("\n")[:5] == ["query", "jdbc:hive2://warehouse.invalid:10000/fixture", "reader", "s3cret", "20"]
    assert seen["input"].endswith("SELECT 1")


@pytest.mark.parametrize("env,message", [("DATAMIND_HIVE_JDBC_CLASSPATH", "DATAMIND_HIVE_JDBC_CLASSPATH"),
                                         ("DATAMIND_JAVA", "Java")])
def test_missing_driver_or_jdk_is_reported(driver, monkeypatch, env, message):
    monkeypatch.delenv(env)
    if env == "DATAMIND_JAVA":
        monkeypatch.setattr(hive_bridge.shutil, "which", lambda *a, **k: None)
    with pytest.raises(hive_bridge.HiveUnavailable, match=message):
        hive_bridge.query("jdbc:hive2://h:10000/d", "", "", "SELECT 1")


def test_server_out_of_memory_is_explained(driver):
    runner = lambda cmd, **kw: Done({"ok": False, "stage": "query", "error_class": "java.sql.SQLException",  # noqa: E731
                                     "message": "Error running query: java.lang.OutOfMemoryError: Java heap space"})
    with pytest.raises(hive_bridge.HiveUnavailable, match="内存不足.*重启 Thrift Server"):
        hive_bridge.query("jdbc:hive2://h:10000/d", "", "", "SELECT 1", runner=runner)


def test_connect_failure_hides_address_and_account(driver):
    runner = lambda cmd, **kw: Done({"ok": False, "stage": "connect", "error_class": "TTransportException",  # noqa: E731
                                     "message": "Could not open client transport with JDBC Uri: "
                                                "jdbc:hive2://192.0.2.7:10000/fixture user reader at 192.0.2.7:10000"})
    with pytest.raises(hive_bridge.HiveUnavailable) as info:
        hive_bridge.query("jdbc:hive2://192.0.2.7:10000/fixture", "reader", "pw", "SELECT 1", runner=runner)
    text = str(info.value)
    assert text.startswith("无法连接 Hive 服务") and "192.0.2.7" not in text and "reader" not in text and "jdbc:" not in text


def test_timeout_is_reported(driver):
    def runner(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    with pytest.raises(hive_bridge.HiveUnavailable, match="超过"):
        hive_bridge.tables("jdbc:hive2://h:10000/d", "", "", runner=runner)


@pytest.mark.parametrize("product,ok", [("Spark SQL", True), ("Apache Hive", True), ("MySQL", False), ("", False)])
def test_hiveserver2_identity_accepts_hive_and_spark_thrift(product, ok):
    assert hive_bridge.is_hiveserver2(product) is ok


@pytest.fixture
def hive_source(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "connections.json"))
    monkeypatch.setattr(server, "_CONN_SECRETS_F", str(tmp_path / "secrets.json"))
    client = server.app.test_client()
    response = client.post("/api/build/connect", json={"kind": "hive", "name": "现场 Hive",
                                                         "dsn": "jdbc:hive2://warehouse.invalid:10000/fixture",
                                                         "user": "reader", "password": ""})
    assert response.status_code == 200, response.json
    calls = []

    def fake(mode, url, user="", password="", sql="", limit=500, runner=None):
        calls.append((mode, url, user, sql, limit))
        if mode == "tables":
            return {"ok": True, "product": "Spark SQL", "version": "3.2.4",
                    "tables": [{"name": "daily", "cols": 1}, {"name": "slow_table", "cols": None}]}
        if "OOM" in sql:
            raise hive_bridge.HiveUnavailable("Hive/Spark 服务端执行查询时内存不足(OutOfMemoryError)")
        return {"ok": True, "product": "Spark SQL", "version": "3.2.4", "columns": ["total", "missing"],
                "rows": [[33, None]], "truncated": False}

    monkeypatch.setattr(hive_bridge, "call", fake)
    return client, response.json["conn"]["id"], calls


def status(client, source):
    return next(s for s in client.get("/api/build/sources").json["sources"] if s["id"] == source)


@pytest.mark.parametrize("path", ["/api/query", "/api/viz/run"])
def test_workbench_and_chart_return_live_hive_rows(hive_source, path):
    client, source, calls = hive_source
    assert status(client, source).get("live_status") is None
    body = client.post(path, json={"src": source, "sql": "SELECT SUM(v) AS total, CAST(NULL AS INT) AS missing FROM t"}).json
    assert body["live"] is True and body["columns"] == ["total", "missing"]
    assert body["rows"] == [{"total": 33, "missing": None}]
    assert calls[-1][:3] == ("query", "jdbc:hive2://warehouse.invalid:10000/fixture", "reader")
    assert (status(client, source)["live_status"], status(client, source)["engine"]) == ("ok", "Spark SQL 3.2.4")


def test_hive_rejects_writes_before_reaching_the_bridge(hive_source):
    client, source, calls = hive_source
    body = client.post("/api/query", json={"src": source, "sql": "INSERT INTO t VALUES (1)"}).json
    assert body.get("error") and not calls
    assert client.post("/api/query", json={"src": source, "sql": "SELECT 1; DROP TABLE t"}).json.get("error") and not calls


def test_table_catalog_and_preview_use_hive_metadata_and_backticks(hive_source):
    client, source, calls = hive_source
    tables = client.get("/api/conn/tables", query_string={"src": source}).json
    assert tables["live"] is True
    # an unread column count stays unknown (None), never reported as 0
    assert tables["tables"] == [{"name": "daily", "rows": None, "cols": 1}, {"name": "slow_table", "rows": None, "cols": None}]
    preview = client.get("/api/conn/preview", query_string={"src": source, "table": "daily"}).json
    assert preview["live"] is True and calls[-1][3] == "SELECT * FROM `daily` LIMIT 100" and calls[-1][4] == 100


def test_server_failure_is_reported_and_marks_the_connection(hive_source):
    client, source, _ = hive_source
    body = client.post("/api/query", json={"src": source, "sql": "SELECT 'OOM'"})
    assert body.status_code == 400 and "内存不足" in body.json["error"]
    assert status(client, source)["live_status"] == "failed"
