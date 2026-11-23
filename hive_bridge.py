"""HiveServer2 live access through the Hive JDBC driver (DR-163).

Apache Hive and the Spark Thrift Server both speak the HiveServer2 protocol. DataMind talks to them through
``drivers/HiveJdbcBridge.java`` run by a local JDK, so no Python Thrift/SASL stack is needed. Deployment supplies:

- ``DATAMIND_HIVE_JDBC_CLASSPATH``: the Hive JDBC driver (e.g. the ``hive-jdbc-<version>-standalone.jar``);
- ``DATAMIND_JAVA`` (optional): the ``java`` executable, JDK 11+; ``PATH`` is used otherwise.

The bridge is compiled once into a cache directory when ``javac`` is available and launched from source otherwise.
Requests go over stdin, so the password never appears in a process listing. Error text is reduced to a stage and
a cause; driver messages can contain addresses and account names, which are removed before they reach a response.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import observability

BRIDGE_SOURCE = Path(__file__).resolve().parent / "drivers" / "HiveJdbcBridge.java"
TIMEOUT_S = 75
_COMPILE_LOCK = threading.Lock()

ENGINES = ("hive", "spark sql")          # database product names accepted as HiveServer2 endpoints


class HiveUnavailable(RuntimeError):
    """The bridge cannot run here (driver or JDK missing), or the endpoint failed; the message is user-facing."""


def _java():
    java = os.environ.get("DATAMIND_JAVA") or shutil.which("java")
    if not java:
        raise HiveUnavailable("未找到 Java 运行时:Hive 实连需要 JDK 11 及以上(设置 DATAMIND_JAVA 或加入 PATH)")
    return java


def _classpath():
    cp = (os.environ.get("DATAMIND_HIVE_JDBC_CLASSPATH") or "").strip()
    if not cp:
        raise HiveUnavailable("未配置 Hive JDBC 驱动:设置 DATAMIND_HIVE_JDBC_CLASSPATH 指向 hive-jdbc standalone jar 后重启服务")
    missing = [p for p in cp.split(os.pathsep) if p and "*" not in p and not os.path.exists(p)]
    if missing:
        raise HiveUnavailable("DATAMIND_HIVE_JDBC_CLASSPATH 中的驱动文件不存在,请检查路径")
    return cp


def _compiled(java):
    """Directory with the compiled bridge, or None to launch the source file directly."""
    javac = shutil.which("javac", path=str(Path(java).parent)) or shutil.which("javac")
    if not javac:
        return None
    digest = hashlib.sha256(BRIDGE_SOURCE.read_bytes()).hexdigest()[:16]
    out = Path(tempfile.gettempdir()) / "datamind-hive-bridge" / digest
    if (out / "HiveJdbcBridge.class").exists():
        return out
    with _COMPILE_LOCK:
        if (out / "HiveJdbcBridge.class").exists():
            return out
        staging = Path(tempfile.mkdtemp(prefix="hive-bridge-"))
        proc = subprocess.run([javac, "-encoding", "UTF-8", "-d", str(staging), str(BRIDGE_SOURCE)],
                              capture_output=True, timeout=120)
        if proc.returncode:
            shutil.rmtree(staging, ignore_errors=True)
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            staging.rename(out)
        except OSError:                        # another worker finished first
            shutil.rmtree(staging, ignore_errors=True)
    return out


_ADDRESS = re.compile(r"jdbc:[^\s'\"]+|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b|\b[\w.-]+\.(?:invalid|local|internal|lan|corp)\b(?::\d+)?")


def _clean(text, *secrets):
    text = _ADDRESS.sub("<地址>", str(text or ""))
    for s in secrets:
        if s and len(str(s)) >= 2:
            text = text.replace(str(s), "<已隐藏>")
    return re.sub(r"\s+", " ", text).strip()[:200]


def explain(result, user="", password=""):
    """Turn a bridge failure into a message that names the stage and the likely remedy."""
    stage, message = result.get("stage"), str(result.get("message") or "")
    if "OutOfMemoryError" in message:
        return ("Hive/Spark 服务端执行查询时内存不足(OutOfMemoryError):需由集群管理员重启 Thrift Server 或调大 "
                "driver 内存;表清单仍可浏览")
    if stage == "driver":
        return "Hive JDBC 驱动无法加载:请确认 DATAMIND_HIVE_JDBC_CLASSPATH 指向 standalone jar"
    if stage == "connect":
        detail = _clean(message, user, password)
        return "无法连接 Hive 服务(请检查网络或 VPN、端口、数据库名与账号)" + (f":{detail}" if detail else "")
    return f"Hive 执行失败({stage}):{_clean(message, user, password)}"


def call(mode, url, user="", password="", sql="", limit=500, runner=None):
    """Run one bridge request and return the parsed JSON (``ok`` is True) or raise HiveUnavailable."""
    java, cp = _java(), _classpath()
    compiled = _compiled(java)
    cmd = ([java, "-cp", os.pathsep.join([cp, str(compiled)]), "HiveJdbcBridge"] if compiled
           else [java, "-cp", cp, str(BRIDGE_SOURCE)])
    request = "\n".join([mode, url, user or "", password or "", str(int(limit))]) + "\n" + (sql or "")
    started = time.monotonic()
    try:
        proc = (runner or subprocess.run)(cmd, input=request.encode("utf-8"), capture_output=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        observability.count("hive_bridge", mode=mode, outcome="timeout")
        raise HiveUnavailable(f"Hive 请求超过 {TIMEOUT_S} 秒未返回(连接或查询过慢)") from None
    observability.observe("hive_bridge_ms", (time.monotonic() - started) * 1000, mode=mode)
    lines = [ln for ln in (proc.stdout or b"").decode("utf-8", "replace").splitlines() if ln.startswith("{")]
    if not lines:
        observability.count("hive_bridge", mode=mode, outcome="no_output")
        raise HiveUnavailable("Hive 桥接进程没有返回结果:请确认 Java 为 JDK 11 及以上且驱动可用")
    result = json.loads(lines[-1])
    observability.count("hive_bridge", mode=mode, outcome="ok" if result.get("ok") else str(result.get("stage") or "error"))
    if not result.get("ok"):
        raise HiveUnavailable(explain(result, user, password))
    return result


def is_hiveserver2(product):
    """Apache Hive and the Spark Thrift Server both identify as HiveServer2 endpoints."""
    name = str(product or "").lower()
    return any(engine in name for engine in ENGINES)


def query(url, user, password, sql, limit=500, runner=None):
    """→ {"columns": [...], "rows": [[...]], "truncated": bool, "engine": "Spark SQL 3.2.4"}"""
    out = call("query", url, user, password, sql, limit, runner)
    return {"columns": out.get("columns") or [], "rows": out.get("rows") or [], "truncated": bool(out.get("truncated")),
            "engine": " ".join(x for x in (out.get("product"), out.get("version")) if x)}


def tables(url, user, password, limit=500, runner=None):
    """Tables of the connection's database with column counts, read from JDBC metadata only."""
    out = call("tables", url, user, password, "", limit, runner)
    return [{"name": t["name"], "rows": None, "cols": None if t.get("cols") is None else int(t["cols"])}
            for t in out.get("tables") or []]
