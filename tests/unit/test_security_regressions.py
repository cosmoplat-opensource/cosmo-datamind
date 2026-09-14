"""Security regressions use temporary files and mocked transport only."""
import io
import ipaddress
import json
import sqlite3
import urllib.request

import pytest

import server
import srv_context
import srv_hardening


@pytest.mark.parametrize("origin", [
    "null", "invalid-origin", "https://localhost", "http://localhost:bad", "http://localhost:0",
    "http://[", "http://user@localhost", "http://localhost/extra",
])
def test_csrf_rejects_opaque_invalid_or_different_scheme_origins(origin):
    response = server.app.test_client().post(
        "/api/conn/api_fetch", json={"id": "absent"}, headers={"Origin": origin},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "http://localhost"}, {"Origin": "http://LOCALHOST:80"},
    {"Referer": "http://localhost/build?step=2"},
])
def test_csrf_retains_same_origin_and_script_clients(headers):
    response = server.app.test_client().post(
        "/api/conn/api_fetch", json={"id": "absent"}, headers=headers,
    )
    assert response.status_code == 404


@pytest.mark.parametrize("method", ["get", "post"])
def test_host_guard_blocks_dns_rebinding_even_with_matching_origin(method):
    client = server.app.test_client()
    response = getattr(client, method)(
        "/api/conn/api_fetch", json={"id": "absent"}, base_url="http://rebind.attacker.example",
        headers={"Origin": "http://rebind.attacker.example"},
    )
    assert response.status_code == 400


def test_proxy_public_origin_is_explicit_and_does_not_trust_forwarded_headers(monkeypatch):
    monkeypatch.setattr(server, "_TRUSTED_HOSTS", {"datamind.example"})
    monkeypatch.setattr(server, "_PUBLIC_ORIGIN", ("https", "datamind.example", 443))
    client = server.app.test_client()
    response = client.post(
        "/api/conn/api_fetch", json={"id": "absent"}, base_url="http://datamind.example",
        headers={"Origin": "https://datamind.example"},
    )
    assert response.status_code == 404
    forged = client.post(
        "/api/conn/api_fetch", json={"id": "absent"}, base_url="http://datamind.example",
        headers={"Origin": "https://attacker.example", "X-Forwarded-Host": "attacker.example",
                 "X-Forwarded-Proto": "https"},
    )
    assert forged.status_code == 403


def test_origin_policy_does_not_trust_wildcard_listen_addresses():
    origin, hosts = server._origin_policy("", "", "0.0.0.0")
    assert origin is None
    assert hosts == {"localhost", "127.0.0.1", "::1"}
    assert server._origin_policy("", "", "::")[1] == hosts


def test_origin_policy_accepts_explicit_proxy_and_internal_hosts():
    origin, hosts = server._origin_policy("https://datamind.example:8443/", "node.internal,[::1]", "127.0.0.2")
    assert origin == ("https", "datamind.example", 8443)
    assert {"node.internal", "datamind.example", "127.0.0.2", "::1"}.issubset(hosts)


@pytest.mark.parametrize("origin", ["null", "invalid", "http://user@host", "https://host/path"])
def test_invalid_public_origin_fails_closed(origin):
    with pytest.raises(ValueError):
        server._origin_policy(origin, "", "127.0.0.1")


def test_readonly_uri_preserves_literal_filename_and_never_opens_other_db(tmp_path):
    ordinary = tmp_path / "source.db"
    literal = tmp_path / "source.db?mode=rw&ignored="
    for path, marker in [(ordinary, "other-db"), (literal, "requested-db")]:
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE marker(value TEXT)")
            connection.execute("INSERT INTO marker VALUES (?)", (marker,))
    connection = srv_context.ro_connect(str(literal))
    try:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("requested-db",)
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO marker VALUES ('write')")
    finally:
        connection.close()


def test_readonly_connection_does_not_fallback_after_uri_open_failure(tmp_path, monkeypatch):
    path = tmp_path / "database.db"
    path.touch()
    calls = []

    def connect(*args, **kwargs):
        calls.append(kwargs)
        raise sqlite3.OperationalError("simulated open failure")

    monkeypatch.setattr(srv_context.sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        srv_context.ro_connect(str(path))
    assert calls == [{"uri": True}]


def test_request_line_is_already_subject_to_absolute_header_deadline(monkeypatch):
    from werkzeug.serving import WSGIRequestHandler

    cls = srv_hardening.build_handler()
    handler = object.__new__(cls)
    handler.rfile = srv_hardening._DeadlineReader(io.BytesIO(b"GET / HTTP/1.1\r\n"))
    deadlines = []

    def read_request_line(self):
        deadlines.append(self.rfile._deadline)
        self.rfile.readline()

    monkeypatch.setattr(WSGIRequestHandler, "handle_one_request", read_request_line)
    handler.handle_one_request()
    assert deadlines[0] is not None


def test_redirect_cannot_bypass_metadata_protection(monkeypatch):
    monkeypatch.setattr(server, "_resolved_ips", lambda host: [ipaddress.ip_address(host)])
    redirect = server._SafeFetchRedirect()
    request = urllib.request.Request("https://public.example/api")
    with pytest.raises(ValueError, match="SSRF"):
        redirect.redirect_request(request, None, 302, "Found", {}, "http://169.254.169.254/latest")


def test_fetch_socket_revalidates_dns_and_never_connects_to_forbidden_address(monkeypatch):
    import socket

    monkeypatch.setattr(server, "_resolved_ips", lambda host: [ipaddress.ip_address("169.254.169.254")])
    calls = []
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="SSRF"):
        server._fetch_socket(("changing.example", 80), timeout=12)
    assert calls == []


def test_fetch_socket_connects_to_validated_numeric_ip_without_second_dns_lookup(monkeypatch):
    import socket

    monkeypatch.setattr(server, "_resolved_ips", lambda host: [ipaddress.ip_address("93.184.216.34")])
    calls = []
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: calls.append((args, kwargs)) or "socket")
    assert server._fetch_socket(("api.example", 443), timeout=12) == "socket"
    assert calls[0][0][0] == ("93.184.216.34", 443)


@pytest.mark.parametrize("url", [
    "http://api.example:bad/data", "http://api.example:65536/data", "http://api.example:0/data",
    "http://user:password@api.example/data", "http://api.example/\nmetadata", None, 7,
])
def test_fetch_url_rejects_malformed_targets_before_resolving(monkeypatch, url):
    resolutions = []
    monkeypatch.setattr(server, "_resolved_ips", lambda host: resolutions.append(host))
    assert server._check_fetch_url(url)
    assert resolutions == []


def test_strict_fetch_rejects_any_non_global_result_in_mixed_dns_answer(monkeypatch):
    monkeypatch.setattr(server, "_STRICT_FETCH", True)
    monkeypatch.setattr(server, "_resolved_ips", lambda host: [
        ipaddress.ip_address("93.184.216.34"), ipaddress.ip_address("100.64.0.1"),
    ])
    assert server._check_fetch_url("https://api.example")


def test_https_pinning_preserves_hostname_for_tls_verification(monkeypatch):
    import ssl

    wrapped = []

    class Socket:
        def setsockopt(self, *args):
            pass

    sock = Socket()
    monkeypatch.setattr(server, "_fetch_socket", lambda *args: sock)
    connection = server._FetchHTTPSConnection("api.example", timeout=12)
    assert connection._context.check_hostname is True
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    monkeypatch.setattr(connection._context, "wrap_socket", lambda socket, server_hostname: wrapped.append(server_hostname) or socket)
    connection.connect()
    assert wrapped == ["api.example"]
    assert connection.sock is sock


def test_api_opener_uses_checked_handlers_and_no_environment_proxy(monkeypatch):
    captured = {}

    class Opener:
        def open(self, req, timeout):
            captured.update(url=req.full_url, timeout=timeout)
            return "response"

    def build(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(server, "_check_fetch_url", lambda url: None)
    monkeypatch.setattr(urllib.request, "build_opener", build)
    assert server._open_api_url("https://api.example/data") == "response"
    handlers = captured["handlers"]
    assert any(isinstance(h, server._SafeFetchRedirect) for h in handlers)
    assert any(isinstance(h, server._FetchHTTPHandler) for h in handlers)
    assert any(isinstance(h, server._FetchHTTPSHandler) for h in handlers)
    assert next(h for h in handlers if isinstance(h, urllib.request.ProxyHandler)).proxies == {}
    assert captured["url"] == "https://api.example/data"


@pytest.mark.parametrize("body", [None, [], ["value"], True, 7, "text"])
def test_json_write_routes_reject_non_object_bodies(body):
    response = server.app.test_client().post(
        "/api/conn/api_fetch", data=json.dumps(body), content_type="application/json",
    )
    assert response.status_code == 400
    assert "JSON 对象" in response.get_json()["error"]


@pytest.fixture
def api_source(monkeypatch, tmp_path):
    conn = {"id": "test-api", "kind": "api", "name": "demo", "url": "https://api.example/data"}
    db = tmp_path / "uploads.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE api_demo(old TEXT)")
        connection.execute("INSERT INTO api_demo VALUES ('preserve-on-error')")
    monkeypatch.setattr(server, "UPLOAD_DB", str(db))
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "conns.json"))
    monkeypatch.setattr(server, "_find_conn", lambda cid: conn)
    monkeypatch.setattr(server, "_load_conns", lambda: [dict(conn)])
    monkeypatch.setattr(server, "_check_fetch_url", lambda url: None)

    def fetch(data):
        monkeypatch.setattr(server, "_open_api_url", lambda *a, **kw: io.BytesIO(json.dumps(data).encode()))
        return server.app.test_client().post("/api/conn/api_fetch", json={"id": conn["id"]})

    return db, fetch


def test_api_column_normalization_preserves_values_and_resolves_collisions(api_source):
    db, fetch = api_source
    response = fetch([{"gross margin": "12", "gross-margin": "13", "A": "upper", "a": "lower", "123": "number"}])
    assert response.status_code == 200
    columns = response.get_json()["columns"]
    assert len(columns) == len(set(c.casefold() for c in columns)) == 5
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT * FROM api_demo").fetchone() == ("12", "13", "upper", "lower", "number")


def test_api_rejects_late_non_object_without_destroying_previous_table(api_source):
    db, fetch = api_source
    response = fetch([{"new": "ok"}] * 20 + [None])
    assert response.status_code == 400
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT old FROM api_demo").fetchone() == ("preserve-on-error",)


def test_api_rolls_back_schema_replacement_when_insert_fails(api_source, monkeypatch):
    db, fetch = api_source
    connect = sqlite3.connect

    class FailingInsert:
        def __init__(self, path):
            self.connection = connect(path)

        def executemany(self, *args):
            raise sqlite3.OperationalError("simulated insert failure")

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(server.sqlite3, "connect", FailingInsert)
    response = fetch([{"new": "value"}])
    assert response.status_code == 502
    with connect(db) as connection:
        assert connection.execute("SELECT old FROM api_demo").fetchone() == ("preserve-on-error",)


@pytest.mark.parametrize("sql", [
    "SELECT 'secret' INTO OUTFILE '/tmp/exfil'",
    "SELECT 'secret' INTO/**/DUMPFILE '/tmp/exfil'",
    "SELECT * INTO new_table FROM existing_table",
    "WITH x AS (SELECT 1) SELECT * INTO new_table FROM x",
    "SELECT 1 /*!50000 INTO OUTFILE '/tmp/exfil' */",
])
def test_select_prefix_is_not_sufficient_for_readonly(sql):
    assert srv_context.sql_is_readonly(sql) is False


@pytest.mark.parametrize("sql", [
    "SELECT 'INTO OUTFILE' AS label",
    'SELECT "into" FROM t',
    "SELECT 1 /* INTO OUTFILE is a comment */",
    "WITH x AS (SELECT 'delete' AS label) SELECT * FROM x",
])
def test_readonly_ignores_literals_identifiers_and_plain_comments(sql):
    assert srv_context.sql_is_readonly(sql) is True


def test_single_statement_cannot_hide_second_statement_in_comment_quote():
    assert server._single_statement("SELECT 1 /* ' */; DROP TABLE t; -- '") is False


@pytest.mark.parametrize("sql", [
    "SELECT 1 # '\n; DROP TABLE t; -- '",
    "SELECT 1 --'\n; DROP TABLE t; -- '",
    "SELECT $tag$ INTO OUTFILE '/tmp/file' $tag$",
])
def test_single_statement_rejects_dialect_ambiguous_comment_or_quote_syntax(sql):
    assert server._single_statement(sql) is False


@pytest.mark.parametrize("sql", ["SELECT 1; -- trailing comment", "SELECT ';' AS s; /* okay */"])
def test_single_statement_allows_trailing_plain_comments(sql):
    assert server._single_statement(sql) is True
