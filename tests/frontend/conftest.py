"""Shared isolated site and Chromium for the browser acceptance suite."""
from threading import Thread

import pytest
from werkzeug.serving import make_server


@pytest.fixture(scope="module")
def site():
    # tests/conftest.py 在导入应用前准备临时工作目录、示例库与离线引擎。
    from server import app

    with make_server("127.0.0.1", 0, app, threaded=True) as http:
        thread = Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{http.server_port}"
        finally:
            http.shutdown()
            thread.join()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    from tests.browser_runtime import browser_executable

    with sync_playwright() as pw, pw.chromium.launch(executable_path=browser_executable()) as chromium:
        yield chromium
