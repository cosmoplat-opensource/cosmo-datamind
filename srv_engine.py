#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引擎运行时与配置共享层 —— DR-043 蓝图化前置(IR-011)。

engine 簇的**配置层是跨簇共享的**,不能直接搬进 blueprint:
`_load_engine_cfg` 既被 `/api/engine/*` 路由用,也被 deepqa 的「按任务选模」(`_task_model`)
与启动自举调用;`LLM_ENV`/`_mask_key`/`ENGINE_TASKS` 同样跨簇。若把它们塞进 bp_engine,
deepqa 就得反向 import blueprint —— 循环导入。故先抽为共享层,各方(server / 未来各 blueprint)
统一从此 import,之后 engine **路由**才能安全迁出。

含两块(均与 Flask/app 无关,故可独立测试):
  1. 运行时实例缓存与引擎优先序(`runtime_cached` / `_drv_order`);
  2. 引擎配置(常量 + 读写/应用/掩码),含「env 优先」规范。

注意 `_ENV_LOCKED_AT_BOOT` 在**本模块 import 时**快照:必须早于任何 `_apply_engine_cfg`
写 os.environ,否则分不清「运维注入」与「界面保存」(server 无模块级 env 写入,故安全)。
"""
import os
import re

import store
from srv_context import WORK

# ── 引擎回复语义(跨簇共用:deepqa / 本体 / 构建 / engine 自测都要判「这条回复其实是错误」)──
_ERR_REPLY = re.compile(r"API call failed|HTTP (?:4\d\d|5\d\d)|usage limit|rate ?limit|quota|Traceback|exceeded|无法.*(连接|执行)|Error:", re.I)


def _looks_like_error(reply):
    """引擎有时把错误文案当正文返回(ok=True 但内容是 429/超限等);识别后视为失败,交由规则兜底。"""
    r = (reply or "").strip()
    return (not r) or (len(r) < 400 and bool(_ERR_REPLY.search(r)))

# ── 运行时实例缓存 ──
# 复用 driver 实例:get_runtime 每次返回新实例,会重置 _started/_primed,使会话式对话(稳定 cid)
# 的多轮续接失效。按 driver 缓存一份,让 hermes/claude-code 的多轮语境/续接生效。
_RT_CACHE = {}


def runtime_cached(drv):
    from agent_runtime import get_runtime
    if drv not in _RT_CACHE:
        _RT_CACHE[drv] = get_runtime(drv)
    return _RT_CACHE[drv]


def _drv_order(cands=("openai", "hermes", "claude-code", "openclaw")):
    # LLM 引擎尝试顺序遵循 CLAW_DRIVER:选中的排最前(其余按原序回退),
    # 使 /api/ont/runtime 的引擎切换对所有 LLM 流程真正生效(而非只改显示标签)。
    pref = os.environ.get("CLAW_DRIVER", "hermes")
    if pref in cands:
        return (pref,) + tuple(d for d in cands if d != pref)
    return tuple(cands)   # 未知取值:按原序尝试,不因拼错而全盘停摆


# ── 引擎设置(DR-017):运行时/模型/API Key 实时切换;Key 只写不回读(掩码),文件 0600 ──
ENGINE_CFG_F = os.path.join(WORK, "engine_config.json")
# OpenAI 兼容端点:本仓自带驱动的全部配置项。此前仅可经环境变量注入,界面上无处可填 ——
# 而这恰是「不接上游引擎也能用」的唯一通路,配不了等于这条路只对读过源码的人开放。
LLM_ENV = {"base": "DATAMIND_LLM_BASE", "key": "DATAMIND_LLM_KEY", "model": "DATAMIND_LLM_MODEL",
           "timeout": "DATAMIND_LLM_TIMEOUT", "max_tokens": "DATAMIND_LLM_MAX_TOKENS"}
# 启动时就存在的 LLM 环境变量:这些由部署方注入,界面一律不覆盖。
# 必须在进程启动、尚未应用本地配置之前快照 —— 之后 _apply_engine_cfg 会把配置写进
# os.environ,那时再判断就分不清「运维注入」与「界面保存」了。
_ENV_LOCKED_AT_BOOT = {v for v in ("DATAMIND_LLM_BASE", "DATAMIND_LLM_KEY", "DATAMIND_LLM_MODEL",
                                   "DATAMIND_LLM_TIMEOUT", "DATAMIND_LLM_MAX_TOKENS")
                       if os.environ.get(v)}

# 本地自建端点的默认前缀:不写死 IP —— 各家 vLLM/Ollama 的监听地址与端口并不一致,
# 部署方用 DATAMIND_LOCAL_LLM_BASE 覆盖即可,界面上的「一键填入」随之跟着变。
LOCAL_LLM_BASE = os.environ.get("DATAMIND_LOCAL_LLM_BASE", "http://localhost:8000/v1")

LLM_PRESETS = [   # 常见服务商的端点前缀,供界面一键填入;模型名随各家版本变动,故只给端点
    {"id": "zhipu", "name": "智谱 GLM", "base": "https://open.bigmodel.cn/api/coding/paas/v4"},
    {"id": "deepseek", "name": "DeepSeek", "base": "https://api.deepseek.com/v1"},
    {"id": "moonshot", "name": "Moonshot Kimi", "base": "https://api.moonshot.cn/v1"},
    {"id": "dashscope", "name": "阿里百炼", "base": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    {"id": "openai", "name": "OpenAI", "base": "https://api.openai.com/v1"},
    {"id": "local", "name": "本地自建(vLLM / Ollama)", "base": LOCAL_LLM_BASE},
]
ENGINE_KEY_VARS = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ZHIPU_API_KEY",
                   "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "DASHSCOPE_API_KEY"]
ENGINE_MODEL_OPTS = {
    # 全部经 claude CLI 实测可用(2026-07-25);别名 opus/sonnet/haiku 自动跟踪最新版(opus 现解析到 claude-opus-5)
    "claude-code": ["claude-opus-5", "claude-opus-4-8", "claude-sonnet-5", "claude-fable-5",
                    "claude-haiku-4-5-20251001", "opus", "sonnet", "haiku"],
    "hermes": ["gpt-5.5", "gpt-5.3-codex", "glm-4.6", "deepseek-v3", "kimi-k2"],
    "openclaw": ["openai/gpt-5.5", "openai/gpt-5.3-codex"],
}
ENGINE_HERMES_PROVIDERS = ["", "openai-codex", "zai", "deepseek", "moonshot", "qwen-oauth"]
# B4 按任务选模:每个环节可配独立模型(空=跟随当前引擎缺省)。SQL 计划/叙述可用轻量模型提速,诊断可用强模型保质。
ENGINE_TASKS = ["plan", "narrative", "diagnose"]
ENGINE_TASK_CN = {"plan": "SQL 计划生成", "narrative": "洞察叙述", "diagnose": "根因诊断"}


# 该文件含 API Key:0600 在替换前打到临时文件上,不留「短暂可读」的窗口;
# 读到坏档时退回 {} 但**留痕告警**(此前静默吞掉,配置损坏会伪装成「没配过」而悄悄重置驱动)。
_ENGINE_STORE = store.JsonStore(ENGINE_CFG_F, default={}, mode=0o600)


def _load_engine_cfg():
    return _ENGINE_STORE.load()


def _save_engine_cfg(cfg):
    _ENGINE_STORE.save(cfg)


def _apply_engine_cfg(cfg):
    """配置 → 进程环境,立即生效(_cmd 调用时读 env);清运行时实例缓存使模型切换即时。"""
    for k, var in (("driver", "CLAW_DRIVER"), ("claude_model", "CLAUDE_MODEL"),
                   ("hermes_model", "HERMES_MODEL"), ("hermes_provider", "HERMES_PROVIDER")):
        v = cfg.get(k)
        if v: os.environ[var] = v
        elif k != "driver" and v == "": os.environ.pop(var, None)
    # OpenAI 兼容端点:同样遵循「环境变量优先」——运维注入的 env 不被本地配置覆盖
    _llm = cfg.get("llm") or {}
    for fld, var in LLM_ENV.items():
        v = str(_llm.get(fld) or "").strip()
        if os.environ.get(var) and fld != "key":   # key 允许配置文件补位(env 未注入时)
            continue
        if v: os.environ[var] = v
        elif not os.environ.get(var): os.environ.pop(var, None)
    try:                                            # 端点变了要重新注册,否则改完仍用旧实例
        import agent_runtime as _arx, openai_runtime as _orx
        _orx.register(_arx)
    except Exception:
        pass
    for var, val in (cfg.get("keys") or {}).items():
        if var not in ENGINE_KEY_VARS: continue
        # 环境变量优先:部署时由运维注入的 env 不被本地配置文件覆盖
        if os.environ.get(var): continue
        if val: os.environ[var] = val
        else: os.environ.pop(var, None)
    _RT_CACHE.clear()


def _mask_key(v):
    return "" if not v else ("*" * 6 + v[-4:] if len(v) > 8 else "*" * len(v))


def _mask_in_text(text, secret):
    """把可能出现在报错详情里的密钥替换掉 —— 上游错误体常把请求头原样回显。"""
    t = str(text or "")
    if secret and len(secret) >= 8:
        t = t.replace(secret, _mask_key(secret))
    return t
