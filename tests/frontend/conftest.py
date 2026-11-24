"""Shared isolated site and Chromium for the browser acceptance suite."""
import sys
from threading import Thread

import pytest


def pytest_collection_modifyitems(items):
    if sys.platform != "linux":
        return
    skip = pytest.mark.skip(reason="Linux WebKit 构建页已移出验收范围")
    for item in items:
        params = item.callspec.params if hasattr(item, "callspec") else {}
        device = params.get("device", ())
        webkit = params.get("scope_browser") == "webkit" or device[:1] == ("webkit",)
        if item.path.name == "build_viewport_browser.py" or (params.get("key") == "build" and webkit):
            item.add_marker(skip)
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
