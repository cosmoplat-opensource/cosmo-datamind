#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JSON store 抽象 —— DR-044。

server.py 现有 15 个手搓 JSON store + 64 处散落 json.load/dump,无 schema/校验/迁移,
加一个字段要改多处;`_atomic_json` 是安全叙事却无负向测试。本模块把「workdir 下单文件
JSON 持久化」收敛为一个可测抽象,供 server 增量迁移:

- **原子写**:tempfile + os.replace(与 server._atomic_json 同规范),崩溃不留半截文件;
- **坏文件优雅恢复**:读到非法 JSON 退回 default(显式记录),不让一处坏档打崩整个端点;
- **可选 schema 校验**:save 前校验形状,非法抛 ValueError 且不破坏旧值;
- **可选迁移钩子**:load 时按版本迁移旧结构;
- **并发安全**:每 path 一把可重入锁,update() 读-改-写全程持锁,无丢更新。

零外部依赖;可被 server.py 的写端点逐个替换,不需一次性改 64 处。
"""
import json
import os
import sys
import threading

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path):
    """每个文件路径一把可重入锁(进程内共享,保证同文件的并发写串行)。"""
    with _LOCKS_GUARD:
        lk = _LOCKS.get(path)
        if lk is None:
            lk = _LOCKS[path] = threading.RLock()
        return lk


class JsonStore:
    def __init__(self, path, default=None, validate=None, migrate=None, mode=None):
        """mode:目标文件权限(如 0o600)。在 os.replace **之前**打到临时文件上,
        使目标文件从出现的第一刻起就是该权限——先落盘再 chmod 会留下一个可被读到的窗口,
        对存放密钥的文件不可接受。"""
        self.path = path
        self._default = default if default is not None else {}
        self._validate = validate
        self._migrate = migrate
        self._mode = mode
        self._lock = _lock_for(path)

    def _fresh_default(self):
        # 返回 default 的独立副本,避免多个 store 共享同一可变对象
        return json.loads(json.dumps(self._default))

    def load(self):
        with self._lock:
            try:
                with open(self.path, encoding="utf-8") as fp:
                    data = json.load(fp)
            except FileNotFoundError:
                return self._fresh_default()          # 首次使用,非异常
            except (json.JSONDecodeError, ValueError, OSError) as e:
                # 坏文件/半截写:退回 default 保证不崩,但**必须留痕**——
                # 静默退回会把「文件损坏」伪装成「本来就是空的」,数据丢失被掩盖。
                print(f"[store] 无法解析 {self.path}({type(e).__name__}: {e});"
                      f"本次读取退回默认值,原文件未被改动,请人工核查是否损坏",
                      file=sys.stderr, flush=True)
                return self._fresh_default()
            if self._migrate is not None:
                data = self._migrate(data)
            return data

    def save(self, data):
        if self._validate is not None and not self._validate(data):
            raise ValueError(f"JsonStore 校验失败,拒绝写入: {self.path}")
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False)
            if self._mode is not None:
                os.chmod(tmp, self._mode)    # 替换前定权限:目标文件不存在「短暂可读」的窗口
            os.replace(tmp, self.path)       # 原子替换,读者绝不会看到半截文件
        return data

    def update(self, fn):
        """读-改-写全程持锁:fn(当前值)->新值。并发下无丢更新。"""
        with self._lock:
            new = fn(self.load())
            self.save(new)
            return new
