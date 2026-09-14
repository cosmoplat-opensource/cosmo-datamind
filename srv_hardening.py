#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP 慢速攻击(Slow HTTP DoS / Slowloris)缓解 —— DR-048。

2026-08-14 的漏洞扫描在本服务 `:8092` 上报出唯一中危:CVE-2007-6750,CVSS 5.3,
POC 记录一条只发了半截请求头的连接被保持 **186 秒**。成因是内置开发服务器
(Werkzeug)对请求处理器**不设任何 socket 超时**,且线程数无上限——
慢速客户端可以极低成本占满连接与线程。

报告给的两条修复建议在此逐条落地:

  ① 「设置适当的超时时间,规定请求头和请求体发送的时间以及频率」
     → 给请求处理器设 socket 超时。socketserver 只在 `timeout` 非 None 时才
       `settimeout()`,所以这个类属性就是开关本身。慢速发头/发体者会在
       `rfile.readline()` 上超时,连接被关闭。

  ② 「增加 MaxClient(MaxRequestWorkers):增加最大连接数」
     → 其实质是**并发要有明确边界**。这里用信号量给在处理的连接数封顶:
       超出即刻关闭,不排队、不占线程,使攻击无法把资源吃到崩溃。

**与 SSE 的关系**:本超时是**单次 socket 读写**的上限,不是整条流的寿命上限。
系统有 4 个 SSE 端点,流期间服务端只在有事件时写;客户端正常读取时写操作不阻塞,
故长流不受影响。默认值取 30s,比正常请求(毫秒级)宽两个数量级,又远低于 186s。
"""
import os
import threading
import time

DEFAULT_REQ_TIMEOUT = 30      # 秒:单次 socket 读写上限(挡「完全不发数据」的挂起连接)
DEFAULT_MAX_CONN = 256        # 同时在处理的连接数上限
DEFAULT_HDR_BUDGET = 10       # 秒:**整个请求头**必须发完的总时限 —— 真正克制 Slowloris 的一条
DEFAULT_BODY_BUDGET = 300     # 秒:请求体的**绝对**上限(信用再多也不得超过)
DEFAULT_BODY_INIT = 20        # 秒:请求体的初始预算(等价 mod_reqtimeout 的 body=20)
DEFAULT_BODY_MIN_RATE = 500   # 字节/秒:每收到这么多字节,截止时间才延长 1 秒(MinRate=500)


def _env_int(name, default, lo, hi):
    """读环境变量;缺失/非法/越界一律回落默认——**不允许**退化成「无限制」。"""
    try:
        v = int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default
    return v if lo <= v <= hi else default


def req_timeout():
    return _env_int("DATAMIND_REQ_TIMEOUT", DEFAULT_REQ_TIMEOUT, 5, 60)


def conn_cap():
    return _env_int("DATAMIND_MAX_CONN", DEFAULT_MAX_CONN, 16, 4096)


def hdr_budget():
    return _env_int("DATAMIND_HDR_BUDGET", DEFAULT_HDR_BUDGET, 3, 30)


def body_budget():
    return _env_int("DATAMIND_BODY_BUDGET", DEFAULT_BODY_BUDGET, 30, 3600)


def body_init():
    return _env_int("DATAMIND_BODY_INIT", DEFAULT_BODY_INIT, 5, 120)


def body_min_rate():
    return _env_int("DATAMIND_BODY_MIN_RATE", DEFAULT_BODY_MIN_RATE, 100, 100000)


class _DeadlineReader:
    """给读操作加**跨多次读的总时限**的透明包装。

    为什么不能只靠 socket 超时:`settimeout` 约束的是**单次**读的等待,
    慢速客户端每隔几秒滴入一个字节就能无限续命(实测每 5s 补一个头,
    连接存活 75s+ 不断)。总时限则不管来多少次数据,超过就判失败。
    """

    def __init__(self, raw, on_timeout=None):
        self._raw = raw
        self._deadline = None
        self._min_rate = None
        self._ceiling = None
        # 判定超时后要主动关闭的连接。仅抛异常不够:Python 3.10+ 里 socket.timeout
        # 即 TimeoutError,会被 Werkzeug 的 handle() 捕获后只记一行日志,
        # 连接不会立刻断(实测正文攻击 20s 触发超时、客户端 70s 仍在发)。
        self._sock = on_timeout
        self._timer = None

    def start(self, budget, min_rate=None, ceiling=None):
        """budget:初始预算。min_rate:每秒至少要收到的字节数——
        收到 n 字节即把截止时间延后 n/min_rate 秒。滴入式攻击换来的时间微乎其微,
        正常上传则源源不断地「挣」到时间。ceiling:信用再多也不得超过的绝对上限。"""
        now = time.monotonic()
        self._deadline = now + budget
        self._min_rate = min_rate
        self._ceiling = (now + ceiling) if ceiling else None

    def stop(self):
        self._deadline = self._min_rate = self._ceiling = None
        self._disarm()

    # ── 看门狗:只在「一次读正在阻塞」期间生效 ────────────────────────
    # 前后夹检查有致命盲区:一次 `read(N)` 若长时间阻塞(客户端按 Content-Length
    # 慢慢滴),那次检查根本轮不到执行 —— 实测连接因此存活 70s+。
    # 但也**不能常挂**:响应阶段不再读请求,常挂会把正在推流的 SSE 掐断
    # (实测 20s 后 Response ended prematurely)。故读前挂、读返回即撤。
    def _arm(self):
        self._disarm()
        if self._deadline is None or self._sock is None:
            return
        delay = max(0.05, self._deadline - time.monotonic())
        self._timer = threading.Timer(delay, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _disarm(self):
        t = getattr(self, "_timer", None)
        if t is not None:
            t.cancel()
            self._timer = None

    def _fire(self):
        # 到点复核:期间若因收到数据而延长了截止时间,就顺延重挂,不误杀。
        if self._deadline is None:
            return
        if time.monotonic() >= self._deadline - 0.01:
            self._kill()
        else:
            self._arm()

    def _check(self):
        if self._deadline is None:
            return
        now = time.monotonic()
        if now > self._deadline or (self._ceiling and now > self._ceiling):
            self._kill()
            raise TimeoutError("请求发送过慢(慢速攻击防护)")

    def _kill(self):
        """把连接就地断掉,让攻击方立刻失去这个占位。"""
        sock = self._sock
        if sock is None:
            return
        try:
            import socket as _s
            sock.shutdown(_s.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    def _credit(self, n):
        """按实际收到的字节数兑换时间;不得越过绝对上限。"""
        if self._min_rate and n and self._deadline is not None:
            self._deadline += n / self._min_rate
            if self._ceiling:
                self._deadline = min(self._deadline, self._ceiling)

    def readline(self, *a, **kw):
        self._check()
        self._arm()                      # 只在这次读阻塞期间挂看门狗
        try:
            d = self._raw.readline(*a, **kw)
        finally:
            self._disarm()               # 读返回即撤:响应阶段(SSE)不再受其约束
        self._credit(len(d)); self._check(); return d

    def read(self, *a, **kw):
        self._check()
        self._arm()                      # 只在这次读阻塞期间挂看门狗
        try:
            d = self._raw.read(*a, **kw)
        finally:
            self._disarm()               # 读返回即撤:响应阶段(SSE)不再受其约束
        self._credit(len(d)); self._check(); return d

    def readinto(self, b):
        self._check()
        self._arm()
        try:
            n = self._raw.readinto(b)
        finally:
            self._disarm()
        self._credit(n or 0); self._check(); return n

    def __getattr__(self, n):
        return getattr(self._raw, n)


def build_handler(timeout=None, max_conn=None):
    """产出一个带超时与并发上限的 WSGI 请求处理器类。

    返回类而非实例:`app.run(request_handler=...)` / `run_simple` 要的是类。
    """
    from werkzeug.serving import WSGIRequestHandler

    t = timeout if timeout is not None else req_timeout()
    c = max_conn if max_conn is not None else conn_cap()
    hb, bb = hdr_budget(), body_budget()
    bi, br = body_init(), body_min_rate()

    class HardenedWSGIRequestHandler(WSGIRequestHandler):
        # socketserver.StreamRequestHandler.setup() 据此调用 settimeout();
        # 为 None 时不设超时——那正是本漏洞的成因。
        timeout = t
        _hdr_budget = hb
        _body_budget = bb
        _body_init = bi
        _body_rate = br
        _sem = threading.BoundedSemaphore(c)

        def handle(self):
            # 并发已满:立刻放弃这条连接,不排队、不占线程。
            if not self._sem.acquire(blocking=False):
                try:
                    self.connection.close()
                except OSError:
                    pass
                return
            try:
                super().handle()
            finally:
                try:
                    self._sem.release()
                except ValueError:
                    pass          # 释放多于获取:不让计数错乱把服务打崩

        def setup(self):
            super().setup()
            # 把读流换成带总时限的包装:请求行/头阶段受 hdr 预算约束,
            # 正文阶段放宽到 body 预算(上传是合法的慢)。
            self.rfile = _DeadlineReader(self.rfile, on_timeout=self.connection)

        def parse_request(self):
            # 「请求头必须在 N 秒内发完」—— 滴入数据也不能续命,这条才真正克制 Slowloris。
            # Deadline already includes the request line; never reset it here.
            try:
                return super().parse_request()
            finally:
                # 头读完:正文改按**速率**判定——初始预算 + 按字节兑换时间 + 绝对上限
                self.rfile.start(self._body_init, min_rate=self._body_rate,
                                 ceiling=self._body_budget)

        def handle_one_request(self):
            self.rfile.start(self._hdr_budget)
            try:
                super().handle_one_request()
            except TimeoutError:
                # 超时即判定为慢速攻击,关连接。不打完整堆栈,避免被刷屏当成 DoS 放大器。
                self.close_connection = True
                try:
                    self.connection.close()
                except OSError:
                    pass

    return HardenedWSGIRequestHandler


def describe():
    """启动横幅用:把生效的防护参数如实打印,便于运维核对。"""
    return (f"慢速攻击防护:请求头时限 {hdr_budget()}s · 正文初始 {body_init()}s/上限 {body_budget()}s · "
            f"正文速率下限 {body_min_rate()}B/s · socket 超时 {req_timeout()}s · 并发上限 {conn_cap()}")
