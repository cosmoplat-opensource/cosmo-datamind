# -*- coding: utf-8 -*-
import io

import server


def test_multipart_upload_over_limit_is_rejected_before_route_reads_file(monkeypatch):
    monkeypatch.setitem(server.app.config, "MAX_CONTENT_LENGTH", 256)
    client = server.app.test_client()
    response = client.post(
        "/api/build/upload",
        data={"files": (io.BytesIO(b"x" * 1024), "large.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 413
    assert response.get_json() == {"error": "请求体过大", "max_bytes": 256}


def test_request_limit_env_parser_is_bounded():
    assert server._positive_int_env("__DATAMIND_TEST_MISSING__", 2048) == 2048
    assert server._positive_int_env("__DATAMIND_TEST_MISSING__", 1) == 1024
