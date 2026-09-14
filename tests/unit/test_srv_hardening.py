# -*- coding: utf-8 -*-
"""HTTP 慢速攻击缓解的配置校验(DR-048)。

来源:2026-08-14 明鉴漏洞扫描报告在 `http://<host>:8092/` 报出唯一中危——
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

    def test_all_env_accessors_and_description(self, monkeypatch):
        values = {
            "DATAMIND_MAX_CONN": ("32", H.conn_cap, 32),
            "DATAMIND_HDR_BUDGET": ("7", H.hdr_budget, 7),
            "DATAMIND_BODY_BUDGET": ("600", H.body_budget, 600),
            "DATAMIND_BODY_INIT": ("25", H.body_init, 25),
            "DATAMIND_BODY_MIN_RATE": ("900", H.body_min_rate, 900),
        }
        for name, (value, getter, expected) in values.items():
            monkeypatch.setenv(name, value)
            assert getter() == expected
        assert "并发上限 32" in H.describe()
        monkeypatch.setenv("DATAMIND_MAX_CONN", "999999")
        assert H.conn_cap() == H.DEFAULT_MAX_CONN


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


class TestBodyMinRate:
    """慢速攻击的第二种变体(R-U-Dead-Yet):声明大 Content-Length 后每秒只滴 1 字节正文。

    只给正文一个「宽预算」挡不住它——300s 的预算意味着连接可被占 300s,
    比扫描器观测到的 186s 还长。报告建议里的「以及**频率**」正是指这个:
    要按**最低传输速率**判定,而不是只看总时长(等价 Apache mod_reqtimeout 的 MinRate)。
    """

    def test_min_rate_configured(self):
        r = H.DEFAULT_BODY_MIN_RATE
        assert r and 100 <= r <= 100000, f"最低速率 {r} B/s 不合理"

    def test_trickle_dies_near_initial_budget(self):
        """每次只滴 1 字节:换来的时间微乎其微,应在初始预算附近被判失败。"""
        import io, time
        data = io.BytesIO(b"x" * 10000)
        r = H._DeadlineReader(data)
        r.start(0.2, min_rate=500)          # 初始 0.2s,每 500 字节换 1s
        t0 = time.monotonic()
        try:
            for _ in range(10000):
                r.read(1)                    # 1 字节仅换来 1/500 s
                time.sleep(0.005)
            raise AssertionError("滴入式正文未被判失败")
        except TimeoutError:
            assert time.monotonic() - t0 < 1.0, "拖得过久才失败,速率闸没起作用"

    def test_bulk_upload_keeps_earning_time(self):
        """正常上传:一次读到大块数据,换来的时间足以覆盖后续传输,不能误杀。"""
        import io
        r = H._DeadlineReader(io.BytesIO(b"x" * 1_000_000))
        r.start(0.2, min_rate=500)
        for _ in range(20):
            r.read(65536)                    # 每次 64KB → 换来 131s
        assert True                          # 未抛超时即通过

    def test_timeout_forces_socket_shutdown(self):
        """光抛异常不够:Python 3.10+ 里 socket.timeout 就是 TimeoutError,
        会被 Werkzeug 的 handle() 捕获后仅记一行日志,连接并不会立刻断
        (实测正文攻击在 20s 触发了超时,客户端却直到 70s 仍在发)。
        所以判超时的同时必须**主动关闭连接**。"""
        import io

        closed = []

        class _Sock:
            def shutdown(self, how): closed.append(("shutdown", how))
            def close(self): closed.append(("close", None))

        r = H._DeadlineReader(io.BytesIO(b"x" * 100), on_timeout=_Sock())
        r.start(0.05, min_rate=500)
        import time as _t
        _t.sleep(0.1)
        try:
            r.read(1)
        except TimeoutError:
            pass
        assert closed, "超时后未关闭连接 —— 客户端会以为还连着,攻击照样占用资源"

    def test_enforced_during_a_blocking_read(self):
        """判定不能只夹在读的前后。

        实测:客户端声明 Content-Length=100000 后每秒滴 1 字节,服务端一次
        `read(100000)` 会长时间阻塞——前后夹检查的写法永远等不到那次检查,
        连接因此存活 70s+。故**读阻塞期间**必须有看门狗按时把连接掐掉。
        """
        import time as _t

        killed = []

        class _Sock:
            def shutdown(self, how): killed.append(how)
            def close(self): killed.append("close")

        class _SlowStream:
            def read(self, n=-1):
                _t.sleep(0.6)          # 模拟「数据在滴、读迟迟不返回」
                return b"x"

        r = H._DeadlineReader(_SlowStream(), on_timeout=_Sock())
        r.start(0.15, min_rate=500)
        try:
            r.read(100000)
        except TimeoutError:
            pass
        assert killed, "阻塞读期间未被掐断"

    def test_watchdog_idle_when_not_reading(self):
        """**不在读**的时候不得开火。

        响应阶段(尤其 SSE 长流)服务端不再读请求,若看门狗仍挂着,
        20s 后就会把正在推流的连接杀掉——实测这会让 SSE 直接
        `Response ended prematurely`。看门狗只在读期间有效。
        """
        import io
        import time as _t

        killed = []

        class _Sock:
            def shutdown(self, how): killed.append(how)
            def close(self): killed.append("close")

        r = H._DeadlineReader(io.BytesIO(b"x" * 10), on_timeout=_Sock())
        r.start(0.15, min_rate=500)
        _t.sleep(0.5)                  # 期间一次读都不发生(等价于正在发响应)
        assert not killed, "空闲期误杀:会打断 SSE 长流"

    def test_extension_capped(self):
        """速率信用不能无限累积,否则「快而不停」的连接可长期占用。"""
        import io, time
        r = H._DeadlineReader(io.BytesIO(b"x" * 1_000_000))
        r.start(0.2, min_rate=500, ceiling=0.5)
        r.read(500000)                       # 本可换来 1000s
        time.sleep(0.6)
        try:
            r.read(1)
            raise AssertionError("超过绝对上限仍放行")
        except TimeoutError:
            pass

    def test_readinto_attribute_forwarding_and_watchdog_branches(self, monkeypatch):
        import io

        raw = io.BytesIO(b"abc")
        reader = H._DeadlineReader(raw)
        assert reader.readinto(bytearray(2)) == 2
        assert reader.seekable() is True
        reader._fire()  # 未启动时为空操作

        reader.start(10)
        rearmed = []
        monkeypatch.setattr(H.time, "monotonic", lambda: reader._deadline - 1)
        monkeypatch.setattr(reader, "_arm", lambda: rearmed.append(True))
        reader._fire()
        assert rearmed == [True]

    def test_kill_tolerates_broken_socket_methods(self):
        class BrokenSocket:
            def shutdown(self, _how):
                raise RuntimeError("broken")

            def close(self):
                raise RuntimeError("broken")

        H._DeadlineReader(b"", on_timeout=None)._kill()
        H._DeadlineReader(b"", on_timeout=BrokenSocket())._kill()


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

    def test_handler_reject_accept_setup_parse_and_timeout_paths(self, monkeypatch):
        from werkzeug.serving import WSGIRequestHandler

        calls = []
        monkeypatch.setattr(WSGIRequestHandler, "handle", lambda self: calls.append("handle"))
        cls = H.build_handler(timeout=9, max_conn=2)

        class Conn:
            def __init__(self, fail=False):
                self.closed = 0
                self.fail = fail

            def close(self):
                self.closed += 1
                if self.fail:
                    raise OSError("closed")

        class Sem:
            def __init__(self, acquire=True, bad_release=False):
                self.acquire_result = acquire
                self.bad_release = bad_release
                self.released = 0

            def acquire(self, blocking=False):
                assert blocking is False
                return self.acquire_result

            def release(self):
                self.released += 1
                if self.bad_release:
                    raise ValueError("over release")

        rejected = object.__new__(cls)
        rejected.connection = Conn(fail=True)
        cls._sem = Sem(acquire=False)
        rejected.handle()
        assert rejected.connection.closed == 1

        accepted = object.__new__(cls)
        accepted.connection = Conn()
        cls._sem = Sem(acquire=True, bad_release=True)
        accepted.handle()
        assert calls == ["handle"]
        assert cls._sem.released == 1

        import io
        monkeypatch.setattr(
            WSGIRequestHandler,
            "setup",
            lambda self: (setattr(self, "rfile", io.BytesIO(b"")), setattr(self, "connection", Conn())),
        )
        setup_obj = object.__new__(cls)
        setup_obj.setup()
        assert isinstance(setup_obj.rfile, H._DeadlineReader)

        starts = []

        class Reader:
            def start(self, *args, **kwargs):
                starts.append((args, kwargs))

        monkeypatch.setattr(WSGIRequestHandler, "parse_request", lambda self: "parsed")
        parse_obj = object.__new__(cls)
        parse_obj.rfile = Reader()
        assert parse_obj.parse_request() == "parsed"
        assert starts == [((cls._body_init,), {"min_rate": cls._body_rate,
                                               "ceiling": cls._body_budget})]

        def timeout(_self):
            raise TimeoutError

        monkeypatch.setattr(WSGIRequestHandler, "handle_one_request", timeout)
        timeout_obj = object.__new__(cls)
        timeout_obj.connection = Conn(fail=True)
        timeout_obj.rfile = Reader()
        timeout_obj.handle_one_request()
        assert timeout_obj.close_connection is True


class TestSSECompatibility:
    def test_timeout_leaves_room_for_streaming(self):
        """系统有 4 个 SSE 长流端点。超时是**单次 socket 读写**的上限,
        不是整条流的寿命上限;但仍需足够宽,避免事件间隔稍长就误杀。"""
        assert H.DEFAULT_REQ_TIMEOUT >= 20, "超时过短,SSE 事件间隔稍大即被误断"
