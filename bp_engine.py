#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引擎设置 blueprint(DR-017 路由)—— IR-011 首个拆出的路由簇。

前置已就绪:引擎配置层与运行时缓存在 [[srv_engine]]、基础路径/原语在 [[srv_context]],
故本模块只含**路由**,不含跨簇共享状态——server 与本 blueprint 都从共享层 import,无循环依赖。

路由(5):GET/POST /api/engine/config、POST /api/engine/llm/test、
        POST /api/engine/llm/models、POST /api/engine/test
"""
import importlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid

from flask import Blueprint, jsonify, request

from srv_engine import (ENGINE_HERMES_PROVIDERS, ENGINE_KEY_VARS, ENGINE_MODEL_OPTS,
                        ENGINE_TASKS, LLM_ENV, LLM_PRESETS, _ENV_LOCKED_AT_BOOT,
                        _apply_engine_cfg, _load_engine_cfg, _looks_like_error,
                        _mask_in_text, _mask_key, _save_engine_cfg)

bp_engine = Blueprint("engine", __name__)

@bp_engine.get("/api/engine/config")
def engine_config_get():
    # 副作用导入:serve_claw 在 import 时把 openclaw 注册进运行时表。用 import_module 表达
    # "只为副作用",既不留未使用绑定(静态检查干净),也让意图对读者显式。
    try: importlib.import_module("serve_claw")
    except Exception: pass
    from agent_runtime import available
    cfg = _load_engine_cfg()
    keys = cfg.get("keys") or {}
    return jsonify({
        "driver": os.environ.get("CLAW_DRIVER") or "hermes",
        "runtimes": available(),
        "models": {"claude-code": os.environ.get("CLAUDE_MODEL") or "claude-opus-4-8",
                   "hermes": os.environ.get("HERMES_MODEL", ""),
                   "openclaw": os.environ.get("OPENCLAW_MODEL", "")},
        "hermes_provider": os.environ.get("HERMES_PROVIDER", ""),
        "model_options": ENGINE_MODEL_OPTS, "hermes_providers": ENGINE_HERMES_PROVIDERS,
        "task_models": {t: (cfg.get("task_models") or {}).get(t, "") for t in ENGINE_TASKS},
        "keys": {v: _mask_key(keys.get(v) or os.environ.get(v, "")) for v in ENGINE_KEY_VARS},
        "llm": {
            "base": os.environ.get(LLM_ENV["base"], ""),
            "key": _mask_key(os.environ.get(LLM_ENV["key"], "")),
            "key_set": bool(os.environ.get(LLM_ENV["key"])),
            "model": os.environ.get(LLM_ENV["model"], ""),
            "timeout": os.environ.get(LLM_ENV["timeout"], ""),
            "max_tokens": os.environ.get(LLM_ENV["max_tokens"], ""),
            "ready": "openai" in available(),
            # env 注入的项不可经界面覆盖,前端据此置灰并说明原因。
            # 判据只看「启动时该 env 是否存在」——与配置文件里存了什么无关:
            # 原先拿两者比对,值恰好相同就漏判为未锁定。
            "env_locked": [f for f, v in LLM_ENV.items()
                           if f != "key" and v in _ENV_LOCKED_AT_BOOT],
        },
        "llm_presets": LLM_PRESETS})

@bp_engine.post("/api/engine/config")
def engine_config_set():
    """部分更新:driver / 各运行时模型 / hermes provider / API keys(空串=清除)。持久化+即时生效。"""
    from agent_runtime import available
    body = request.json or {}
    cfg = _load_engine_cfg()
    if "driver" in body:
        if body["driver"] not in available():
            return jsonify({"error": f"无此运行时: {body['driver']}", "available": available()}), 400
        cfg["driver"] = body["driver"]
    for k in ("claude_model", "hermes_model", "hermes_provider"):
        if k in body: cfg[k] = str(body[k]).strip()[:80]
    if isinstance(body.get("task_models"), dict):        # B4 按任务选模(空串=清除该任务覆盖)
        tm = cfg.setdefault("task_models", {})
        for t, v in body["task_models"].items():
            if t not in ENGINE_TASKS: return jsonify({"error": f"未知任务: {t}", "tasks": ENGINE_TASKS}), 400
            v = str(v or "").strip()[:80]
            if v: tm[t] = v
            else: tm.pop(t, None)
    if isinstance(body.get("llm"), dict):                # OpenAI 兼容端点
        lm = cfg.setdefault("llm", {})
        for fld in LLM_ENV:
            if fld not in body["llm"]: continue
            v = str(body["llm"][fld] or "").strip()
            if fld in ("timeout", "max_tokens") and v:
                if not v.isdigit() or int(v) <= 0:
                    return jsonify({"error": f"{fld} 需为正整数"}), 400
            if fld == "base" and v and not v.startswith(("http://", "https://")):
                return jsonify({"error": "端点地址须以 http:// 或 https:// 开头"}), 400
            if v: lm[fld] = v[:400]
            else:
                lm.pop(fld, None); os.environ.pop(LLM_ENV[fld], None)
    # 端点配好却仍指向未注册的运行时,是最常见的「配了没反应」:自动切过去,并告知已切
    _switched = ""
    if isinstance(body.get("llm"), dict) and "driver" not in body:
        _cur = cfg.get("driver") or os.environ.get("CLAW_DRIVER", "")
        _lm = cfg.get("llm") or {}
        if _lm.get("base") and _lm.get("key") and _cur != "openai":
            cfg["driver"] = "openai"; _switched = _cur or "(未设置)"
    if isinstance(body.get("keys"), dict):
        ks = cfg.setdefault("keys", {})
        for var, val in body["keys"].items():
            if var not in ENGINE_KEY_VARS: return jsonify({"error": f"不支持的 Key 变量: {var}"}), 400
            val = str(val or "").strip()
            if val: ks[var] = val[:200]
            else:
                ks.pop(var, None); os.environ.pop(var, None)   # 清除须同步弹出进程 env
    _save_engine_cfg(cfg)
    _apply_engine_cfg(cfg)
    resp = engine_config_get()
    if _switched:
        d = resp.get_json(); d["switched_from"] = _switched
        return jsonify(d)
    return resp

@bp_engine.post("/api/engine/llm/test")
def engine_llm_test():
    """用**给定的**(可未保存)端点配置跑一次最小请求,回真实延迟与真实报错。

    参考通行做法:配置面板里「测试连接」应当测的是你正在填的那份配置,
    而不是已保存的那份 —— 否则先存后测,存错了还得回滚。
    Key 留空时沿用已保存的,便于只改模型名时复测。"""
    b = request.json or {}
    base = str(b.get("base") or os.environ.get(LLM_ENV["base"], "")).strip().rstrip("/")
    key = str(b.get("key") or "").strip() or os.environ.get(LLM_ENV["key"], "")
    model = str(b.get("model") or os.environ.get(LLM_ENV["model"], "")).strip()
    if not base or not key:
        return jsonify({"ok": False, "error": "端点地址与 API Key 缺一不可"}), 400
    if not base.startswith(("http://", "https://")):
        return jsonify({"ok": False, "error": "端点地址须以 http:// 或 https:// 开头"}), 400
    if not model:
        return jsonify({"ok": False, "error": "未指定模型名"}), 400
    # 额度取用户填的值(缺省 1024):写死小额度会把推理型模型卡在 finish_reason=length,
    # 正文为空,看着像「模型不可用」——实际只是测试请求自己给少了。
    _mt = b.get("max_tokens") or os.environ.get(LLM_ENV["max_tokens"]) or 1024
    try: _mt = max(64, int(_mt))
    except (TypeError, ValueError): _mt = 1024
    payload = json.dumps({"model": model, "temperature": 0, "max_tokens": _mt,
                          "messages": [{"role": "user", "content": "只回复两个字:在线"}]}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=payload,
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=int(b.get("timeout") or 60)) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_txt = ""
        try: body_txt = e.read().decode("utf-8", "ignore")[:200]
        except Exception: pass
        return jsonify({"ok": False, "ms": int((time.time() - t0) * 1000),
                        "error": f"HTTP {e.code}", "detail": _mask_in_text(body_txt, key)})
    except Exception as e:
        return jsonify({"ok": False, "ms": int((time.time() - t0) * 1000),
                        "error": f"{type(e).__name__}: {_mask_in_text(str(e)[:200], key)}"})
    _ch = (d.get("choices") or [{}])[0]
    msg = _ch.get("message") or {}
    txt = (msg.get("content") or "").strip()
    _fin = _ch.get("finish_reason")
    think = len(msg.get("reasoning_content") or "")
    usage = d.get("usage") or {}
    return jsonify({"ok": bool(txt), "ms": int((time.time() - t0) * 1000),
                    "reply": txt[:80], "reasoning_chars": think,
                    "usage": {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens")},
                    # 正文为空多半是推理占满了额度,直接把处置写出来,免得配置者从头猜
                    "finish_reason": _fin,
                    "warn": (("回复被额度截断(finish_reason=length,本次上限 %d):"
                              "推理型模型的思维链占用同一份额度,请把「回复上限」调大" % _mt)
                             if _fin == "length" else
                             ("正文为空而思维链 %d 字:模型未产出正文,可尝试调大回复上限或换模型" % think)
                             if (not txt and think) else "")})


@bp_engine.post("/api/engine/llm/models")
def engine_llm_models():
    """拉取端点的可用模型列表(GET {base}/models)。部分服务不提供该接口,失败即如实返回。"""
    b = request.json or {}
    base = str(b.get("base") or os.environ.get(LLM_ENV["base"], "")).strip().rstrip("/")
    key = str(b.get("key") or "").strip() or os.environ.get(LLM_ENV["key"], "")
    if not base or not key:
        return jsonify({"models": [], "error": "端点地址与 API Key 缺一不可"}), 400
    req = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
    except Exception as e:
        return jsonify({"models": [], "error": "%s: %s" % (type(e).__name__,
                                                           _mask_in_text(str(e)[:160], key))})
    ids = sorted({str(m.get("id")) for m in (d.get("data") or []) if m.get("id")})
    return jsonify({"models": ids[:200], "count": len(ids)})


@bp_engine.post("/api/engine/test")
def engine_test():
    """连通性测试:对指定运行时跑一条最小指令,回真实延迟或真实报错(切换前先测,best practice)。"""
    drv = (request.json or {}).get("driver", "")
    from agent_runtime import get_runtime, available
    if drv not in available(): return jsonify({"error": "无此运行时"}), 400
    t0 = time.time()
    try:
        ok, reply = get_runtime(drv).run_turn(f"tst_{uuid.uuid4().hex[:6]}", "只回复两个字:在线", timeout=60)
    except Exception as e:
        ok, reply = False, str(e)
    if ok and _looks_like_error(str(reply or "")):
        ok = False
    return jsonify({"ok": bool(ok), "seconds": round(time.time() - t0, 1),
                    "reply": str(reply or "")[:200]})

