# -*- coding: utf-8 -*-
"""安全原语的负向测试(源码审计整改配套)。

审计报告把「路径遍历 / SQL 注入 / 命令注入 / SSRF / 凭据落盘」列为中高危,
整改引入了一组集中裁决(_confined / _safe_ident / _safe_argv / _single_statement /
_check_fetch_url / 凭据加密)。这些裁决若只有实现没有回归锁,下一次重构就会悄悄失效 ——
本模块专测**被拒绝的那一半**:正例证明功能没坏,负例才证明防线还在。
"""
import os

import pytest

import server


# ── 路径禁闭 ────────────────────────────────────────────────────────
def test_confined_allows_inside():
    p = server._confined(server.WORK, "edits_demo.json")
    assert p.startswith(os.path.normpath(server.WORK) + os.sep)


@pytest.mark.parametrize("evil", [
    "../../etc/passwd",
    "../outside.json",
    "sub/../../escape.json",
])
def test_confined_blocks_escape(evil):
    with pytest.raises(ValueError):
        server._confined(server.WORK, evil)


def test_confined_blocks_absolute_escape():
    """绝对路径同样要被拦:os.path.join 遇到绝对路径会**丢弃**前面的 base,
    这正是「拼了 base 就以为安全」最常见的翻车方式。"""
    with pytest.raises(ValueError):
        server._confined(server.WORK, "/etc/passwd")


@pytest.mark.parametrize("raw,expect", [
    ("../../etc/passwd", "passwd"),      # 目录部分被丢弃,只留最后一段
    ("..", "__invalid__"),
    ("", "__invalid__"),
    (".ssh", "__invalid__"),             # 隐藏文件:不给写
    ("skill.md", "skill.md"),
])
def test_safe_fname(raw, expect):
    assert server._safe_fname(raw) == expect


# ── SQL 标识符 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("ok", ["dim_customer", "订单金额", "_x1", "COL"])
def test_safe_ident_accepts(ok):
    assert server._safe_ident(ok) == ok


@pytest.mark.parametrize("bad", [
    'a" FROM sqlite_master --',          # 越出引号边界
    "a;DROP TABLE t",
    "1abc",                              # 数字开头
    "a b",
    "a-b",
    "",
    None,
    123,
])
def test_safe_ident_rejects(bad):
    assert server._safe_ident(bad) is None


def test_quote_ident_escapes_embedded_quote():
    """允许任意字符的位置(如中文指标名做列别名)靠成对转义收口,而不是靠白名单。"""
    assert server._quote_ident('毛利"率') == '"毛利""率"'
    assert server._quote_ident('a" , (SELECT 1) "') .count('"') % 2 == 0


# ── 堆叠语句 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("sql", ["SELECT 1", "SELECT 1;", " SELECT ';' AS s ", 'SELECT "a;b"'])
def test_single_statement_accepts(sql):
    assert server._single_statement(sql) is True


@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE t",
    "SELECT 1;SELECT 2",
    "SELECT 'x'; UPDATE t SET a=1",
])
def test_single_statement_rejects_stacked(sql):
    """sql_is_readonly 只看开头,堆叠语句能整个绕过它;外部驱动(pymysql 等)
    在部分配置下确实会执行多语句,故必须单独拦。"""
    assert server._single_statement(sql) is False


# ── 子进程参数 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", [
    "x`whoami`", "a;rm -rf /", "a|b", "a$(id)", "a\nb", "a>b",
])
def test_safe_argv_strips_shell_metachars(raw):
    out = server._safe_argv(raw)
    assert not set(out) & set("`$;&|<>\\\"'\n\r")


def test_safe_argv_never_looks_like_option():
    assert not server._safe_argv("--output=/etc/x").startswith("-")


def test_safe_argv_falls_back_when_empty():
    assert server._safe_argv("   ") == "untitled"
    assert server._safe_argv(None, default="未命名") == "未命名"


# ── SSRF ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://127.0.0.1:11211/_",
    "dict://127.0.0.1:11211/stat",
])
def test_fetch_url_rejects_non_http(url):
    assert server._check_fetch_url(url) is not None


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # 云元数据:SSRF 最高价值目标
    "http://0.0.0.0/",
])
def test_fetch_url_rejects_metadata_and_unspecified(url):
    assert server._check_fetch_url(url) is not None


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8092/api/overview",      # IPv4 回环
    "http://localhost:8092/api/overview",      # 会解析出 IPv6 ::1
])
def test_fetch_url_allows_loopback_by_default(url):
    """本产品的正当用法就是连内网/本机 API(自指向的 API 数据源是自带回归用例),
    默认封死私网只会让人把开关一开了之。严格模式另测。

    localhost 这一例是回归锁:ipaddress 把 IPv6 回环 ::1 归入 is_reserved,
    守卫若直接照搬 is_reserved 就会把 localhost 一并误拦 —— 只测 127.0.0.1 抓不到。
    """
    assert server._check_fetch_url(url) is None


def test_fetch_url_strict_mode_blocks_loopback(monkeypatch):
    monkeypatch.setattr(server, "_STRICT_FETCH", True)
    assert server._check_fetch_url("http://127.0.0.1:8092/api/overview") is not None


# ── 日志伪造(CWE-117)────────────────────────────────────────────
@pytest.mark.parametrize("payload", [
    "127.0.0.1\n2026-01-01 00:00:00 INFO [datamind] 伪造的成功记录",
    "a\r\nFAKE: 已授权",
    "x\x1b[2J",                                   # ESC 序列:可清屏/改色,操纵运维终端
])
def test_log_sanitizer_collapses_injected_lines(payload):
    """写进日志的换行必须被抹掉:否则一条记录能被拆成两条,
    攻击者可以拼出一行以假乱真的日志把审计线索搅浑。"""
    import logging
    rec = logging.LogRecord("datamind", logging.INFO, __file__, 1, "host=%s", (payload,), None)
    assert server._LogSanitizer().filter(rec) is True
    out = rec.getMessage()
    assert "\n" not in out and "\r" not in out and "\x1b" not in out
    assert "␊" in out                              # 留下可见痕迹,不是悄悄吞字


def test_log_sanitizer_keeps_normal_message_intact():
    """正常消息不得被改动 —— 净化只针对控制字符,不能顺手改写正常日志。"""
    import logging
    rec = logging.LogRecord("datamind", logging.INFO, __file__, 1, "端口 %d 就绪", (8092,), None)
    server._LogSanitizer().filter(rec)
    assert rec.getMessage() == "端口 8092 就绪"


# ── 凭据 ───────────────────────────────────────────────────────────
def test_dsn_credentials_are_split_out():
    """DSN 会随连接清单回给前端,内嵌口令必须在登记时就摘掉。"""
    clean, user, pwd = server._split_dsn_creds("mysql://alice:s3cr3t@10.0.0.9:3306/dw")
    assert "s3cr3t" not in clean and "alice" not in clean
    assert (user, pwd) == ("alice", "s3cr3t")


def test_mask_conn_hides_legacy_userinfo():
    """加固前存下的记录仍带 userinfo,回显路径上要兜住。"""
    masked = server._mask_conn({"dsn": "mysql://alice:s3cr3t@h/db"})
    assert "s3cr3t" not in masked["dsn"] and "***@" in masked["dsn"]


def test_secret_roundtrip_and_not_plaintext_on_disk():
    stored = server._enc_secret("s3cr3t-pw")
    assert server._dec_secret(stored) == "s3cr3t-pw"
    if server._fernet() is not None:          # 装了 cryptography:落盘形态不得含明文
        assert "s3cr3t-pw" not in stored


def test_secret_reads_legacy_plaintext():
    """旧版本写下的裸明文仍读得出(不制造"升级即丢凭据"),下次保存自动升级为密文。"""
    assert server._dec_secret("legacy-plain") == "legacy-plain"
