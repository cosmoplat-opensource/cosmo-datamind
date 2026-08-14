# -*- coding: utf-8 -*-
"""HTTP 慢速攻击缓解的配置校验(DR-048)。

来源:2026-08-14 漏洞扫描报告在 `http://<host>:8092/` 报出唯一中危——
「http 慢速攻击」(Slow HTTP DoS / Slowloris,CVE-2007-6750,CVSS 5.3),
POC 记录连接被保持 **186 秒**。报告给的两条修复建议:①设置请求头/请求体的发送超时;
②提高最大连接数上限(即:并发要有明确边界,不能被慢连接无限占满)。

这里只校验**配置层**(纯函数、离线可跑);真实的「慢速连接会被掐断」由
`test_all.py` 的实连断言与部署验证覆盖。
"""
import srv_hardening as H


class TestTimeout:
    def test_default_timeout_is_bounded_and_sane(self):
        """必须有超时,且要落在「远大于正常请求、又远小于扫描器观测到的 186s」之间。"""
        t = H.DEFAULT_REQ_TIMEOUT
        assert t is not None, "未设置请求超时 —— 慢速客户端可无限占用连接"
        assert 5 <= t <= 60, f"超时 {t}s 不合理(正常请求毫秒级;186s 是漏洞观测值)"

    def test_handler_class_carries_timeout(self):
        """超时要真正挂到请求处理器上:socketserver 仅在 timeout 非 None 时才 settimeout。"""
        cls = H.build_handler(timeout=17, max_conn=8)
        assert cls.timeout == 17

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("DATAMIND_REQ_TIMEOUT", "12")
        assert H.req_timeout() == 12
        monkeypatch.setenv("DATAMIND_REQ_TIMEOUT", "0")
        assert H.req_timeout() == H.DEFAULT_REQ_TIMEOUT, "0/非法值须回落默认,不可退化为无超时"
        monkeypatch.setenv("DATAMIND_REQ_TIMEOUT", "abc")
        assert H.req_timeout() == H.DEFAULT_REQ_TIMEOUT


class TestHeaderDeadline:
    """关键:socket 超时是**每次读**的上限,慢速客户端只要持续滴入数据就永不触发
    ——这正是 Slowloris 的原理(实测每 5s 补一个头,连接存活 75s+ 不断)。
    真正管用的是**请求头阶段的总时限**(等价于 Apache mod_reqtimeout 的 header 预算)。"""

    def test_header_budget_configured(self):
        b = H.DEFAULT_HDR_BUDGET
        assert b and 3 <= b <= 30, f"请求头总时限 {b}s 不合理"

    def test_reader_raises_after_budget_regardless_of_trickle(self):
        """滴入数据不能续命:超过总预算即失败。"""
        import io, time
        r = H._DeadlineReader(io.BytesIO(b"a\r\nb\r\nc\r\n"))
        r.start(0.15)
        time.sleep(0.25)                      # 期间「持续有数据」也无用
        try:
            r.readline()
            raise AssertionError("超出请求头总时限仍放行")
        except TimeoutError:
            pass

    def test_reader_transparent_within_budget(self):
        import io
        r = H._DeadlineReader(io.BytesIO(b"hello\r\nworld\r\n"))
        r.start(30)
        assert r.readline() == b"hello\r\n"
        r.stop()                               # 头读完:正文阶段不再受该预算约束
        assert r.readline() == b"world\r\n"

    def test_body_budget_generous_for_uploads(self):
        """正文预算要远大于头预算——系统有文件上传端点,不能把慢速大文件误杀。"""
        assert H.DEFAULT_BODY_BUDGET >= 120
        assert H.DEFAULT_BODY_BUDGET > H.DEFAULT_HDR_BUDGET * 5


class TestConnectionCap:
    def test_default_cap_is_bounded(self):
        c = H.DEFAULT_MAX_CONN
        assert c and 16 <= c <= 4096, f"并发上限 {c} 不合理"

    def test_cap_rejects_beyond_limit_and_releases(self):
        """超过上限的连接立即放弃,已结束的连接必须归还名额(否则正常用量会把自己饿死)。"""
        cls = H.build_handler(timeout=5, max_conn=2)
        sem = cls._sem
        assert sem.acquire(blocking=False) and sem.acquire(blocking=False)
        assert not sem.acquire(blocking=False), "上限失效:第 3 个连接仍被放行"
        sem.release()
        assert sem.acquire(blocking=False), "名额未归还"


class TestSSECompatibility:
    def test_timeout_leaves_room_for_streaming(self):
        """系统有 4 个 SSE 长流端点。超时是**单次 socket 读写**的上限,
        不是整条流的寿命上限;但仍需足够宽,避免事件间隔稍长就误杀。"""
        assert H.DEFAULT_REQ_TIMEOUT >= 20, "超时过短,SSE 事件间隔稍大即被误断"
