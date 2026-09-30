#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""观测驱动(ODD):构建与问数的结构化追踪和指标。

本轮排查里几个问题都是靠人工翻日志才发现的:模型抽取超时后回退构建全库、口径校验把已验证的
关系拦掉、问数因关联缺租户条件而计数虚高。这些都应当在运行时就能被看见。本模块提供:

- **追踪**:每次构建 / 问数是一个 run(run_id),按步骤记录事件(步骤名、成败、距上一步耗时、
  少量计数属性),追加写入 ``WORK/obs/events-YYYYMMDD.jsonl``。
- **指标**:进程内计数器与耗时分布(构建次数、各步骤耗时、兜底次数、口径拦截按原因分类等),
  由 ``/api/obs/metrics`` 读出。

隐私:属性只收标量;问句、SQL、口令等敏感键不落盘(只记长度与指纹),字符串截断并去除 IP 与主机。
只用标准库;写盘失败不影响业务(观测永远不能让主流程失败)。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager

import srv_context

_LOCK = threading.Lock()
_COUNTERS: dict = defaultdict(int)
_HISTS: dict = defaultdict(list)
_HIST_CAP = 500
SENSITIVE = re.compile(r"(^q$|question|sql|password|passwd|secret|token|key$|dsn|prompt|text$)", re.I)
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
_URL = re.compile(r"\b(https?|jdbc:[a-z0-9]+)://[^/\s\"'<>]+", re.I)


def obs_dir():
    return os.path.join(os.environ.get("DATAMIND_OBS_DIR") or srv_context.WORK, "obs")


def fingerprint(text):
    """敏感文本只记指纹与长度,便于同一问题跨次比对而不留原文。"""
    raw = str(text or "")
    return {"sha": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12], "len": len(raw)}


def _clean(attrs):
    out = {}
    for k, v in (attrs or {}).items():
        if v is None:
            continue
        if SENSITIVE.search(str(k)):
            out[f"{k}_fp"] = fingerprint(v)
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[k] = v
        elif isinstance(v, str):
            out[k] = _IP.sub("[地址]", _URL.sub(lambda m: m.group(1) + "://[地址]", v))[:160]
        elif isinstance(v, (list, tuple)) and all(isinstance(x, (str, int, float)) for x in v):
            out[k] = [(_IP.sub("[地址]", x)[:60] if isinstance(x, str) else x) for x in list(v)[:20]]
    return out


def _labels_key(name, labels):
    return (name, tuple(sorted((k, str(v)) for k, v in (labels or {}).items())))


def count(name, n=1, **labels):
    with _LOCK:
        _COUNTERS[_labels_key(name, labels)] += n


def observe(name, value, **labels):
    with _LOCK:
        h = _HISTS[_labels_key(name, labels)]
        h.append(float(value))
        if len(h) > _HIST_CAP:
            del h[: len(h) - _HIST_CAP]


def _write(event):
    try:
        d = obs_dir()
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, time.strftime("events-%Y%m%d.jsonl"))
        line = json.dumps(event, ensure_ascii=False, default=str)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass                                   # 观测不能让主流程失败


class Run:
    """一次构建 / 问数。step() 记录步骤事件与距上一步的耗时;set() 补充汇总属性。"""

    def __init__(self, kind, **attrs):
        self.kind, self.id = kind, uuid.uuid4().hex[:12]
        self.t0 = self.last = time.monotonic()
        self.attrs = _clean(attrs)
        self.steps = 0
        self.failed_steps = 0
        count(f"{kind}.runs")
        _write({"ts": time.time(), "run": self.id, "kind": kind, "event": "start", **self.attrs})

    def step(self, name, ok=True, **attrs):
        now = time.monotonic()
        dt = round((now - self.last) * 1000, 1)
        self.last = now
        self.steps += 1
        if not ok:
            self.failed_steps += 1
            count(f"{self.kind}.step_failed", step=name)
        observe(f"{self.kind}.step_ms", dt, step=name)
        _write({"ts": time.time(), "run": self.id, "kind": self.kind, "event": "step", "step": name,
                "ok": bool(ok), "ms": dt, **_clean(attrs)})

    def set(self, **attrs):
        self.attrs.update(_clean(attrs))

    def finish(self, status="ok", **attrs):
        if getattr(self, "_finished", False):      # 只结束一次:手动 finish 后,上下文退出不再重复记录
            return
        self._finished = True
        self.set(**attrs)
        ms = round((time.monotonic() - self.t0) * 1000, 1)
        count(f"{self.kind}.finished", status=status)
        observe(f"{self.kind}.total_ms", ms)
        _write({"ts": time.time(), "run": self.id, "kind": self.kind, "event": "finish", "status": status,
                "ms": ms, "steps": self.steps, "failed_steps": self.failed_steps, **self.attrs})


@contextmanager
def run(kind, **attrs):
    r = Run(kind, **attrs)
    try:
        yield r
    except Exception as exc:
        r.finish("error", error=type(exc).__name__)
        raise
    else:
        if not getattr(r, "_finished", False):
            r.finish("ok")


def _pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 1)


def snapshot():
    """指标快照:计数器按标签展开,耗时给出次数、p50、p95、最大值。"""
    with _LOCK:
        counters = [{"name": n, "labels": dict(lb), "value": v} for (n, lb), v in sorted(_COUNTERS.items())]
        hists = [{"name": n, "labels": dict(lb), "count": len(xs), "p50": _pct(xs, 50), "p95": _pct(xs, 95),
                  "max": round(max(xs), 1) if xs else None} for (n, lb), xs in sorted(_HISTS.items())]
    return {"counters": counters, "histograms": hists}


def recent(limit=200, kind=None):
    """最近的追踪事件(当天文件),最新在后。"""
    path = os.path.join(obs_dir(), time.strftime("events-%Y%m%d.jsonl"))
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()[-5000:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if kind is None or ev.get("kind") == kind:
            out.append(ev)
    return out[-limit:]


def reset():
    """仅供测试:清空进程内指标。"""
    with _LOCK:
        _COUNTERS.clear()
        _HISTS.clear()
