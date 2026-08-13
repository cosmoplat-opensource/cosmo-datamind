#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI 兼容运行时 —— DR-029。

本系统的深度问数与本体构建都经 `agent_runtime` 调 LLM,而该抽象由上游引擎提供,
内置驱动是 claude-code / hermes / openclaw —— 都依赖本机装好特定 CLI。
开源用户拿到仓库后,若手上只有一个 OpenAI 兼容的 API(GLM、DeepSeek、Qwen、
Moonshot、vLLM 自建……),就无从接入:这与"开箱可用"相悖,也把系统绑死在少数几家。

本模块在 **DataMind 侧**补一个通用驱动:任何 OpenAI 兼容的 `/chat/completions`
端点都能接,配置全走环境变量,不改任何上游代码。

  DATAMIND_LLM_BASE   端点前缀,如 https://open.bigmodel.cn/api/coding/paas/v4
  DATAMIND_LLM_KEY    API Key
  DATAMIND_LLM_MODEL  模型名,如 glm-4.5
  DATAMIND_LLM_MAX_TOKENS  单次回复上限,默认 16384。推理型模型务必留足:
                      思考占用计入该额度,给小了会出现「返回几十字」的静默失败
  CLAW_DRIVER=openai  选用本驱动

设计要点:
- **不注册就不出现**。未配置端点时 `available()` 返回空,系统按既有逻辑降级到
  模板兜底,不会因为"多了一个驱动"而误以为 LLM 可用。
- **推理模型的空 content 要当失败**。GLM-5 一类会把预算耗在 reasoning_content 上,
  返回 content="" ——若当成功回传,上游会拿空串去解析 SQL 计划,产生难查的静默失败。
- **不打印 Key**。异常信息里剔除鉴权头,避免 Key 随日志外泄。
"""
import json
import os
import urllib.request


def _int_env(name, default):
    try: return max(1, int(os.environ.get(name) or default))
    except (TypeError, ValueError): return default


class OpenAICompatRuntime:
    """OpenAI 兼容 Chat Completions 驱动(无状态:每轮独立请求,不维护服务端会话)。"""

    name = "openai"

    def __init__(self):
        self.base = (os.environ.get("DATAMIND_LLM_BASE") or "").rstrip("/")
        self.key = os.environ.get("DATAMIND_LLM_KEY") or ""
        self.model = os.environ.get("DATAMIND_LLM_MODEL") or "gpt-4o-mini"

    def available(self):
        """端点与 Key 齐备才算可用 —— 缺一即视为未配置,不制造"看似可用"的假象。"""
        return bool(self.base and self.key)

    def run_turn(self, session_id, message, timeout=300, model=None):
        """返回 (ok, text)。与既有驱动同签名,故上游调用点零改动。"""
        if not self.available():
            return False, "未配置 DATAMIND_LLM_BASE / DATAMIND_LLM_KEY"
        body = json.dumps({
            "model": model or self.model,
            "messages": [{"role": "user", "content": message}],
            "temperature": 0,
            # 推理型模型(glm-4.5/5、o 系列)会把预算先花在 reasoning 上,4096 常常
            # 只够思考、正文只剩几十字 —— 表现为"规划失败"却查不出原因。故可配且默认放宽。
            "max_tokens": _int_env("DATAMIND_LLM_MAX_TOKENS", 16384),
        }).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/chat/completions", data=body,
            headers={"Authorization": "Bearer " + self.key,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read())
        except Exception as e:
            # 剔除可能含 Key 的细节,只回传类型与摘要
            return False, f"{type(e).__name__}: {str(e)[:160]}"
        try:
            msg = (d.get("choices") or [{}])[0].get("message") or {}
            txt = (msg.get("content") or "").strip()
        except Exception:
            return False, "响应结构异常"
        if not txt:
            # 推理型模型可能把 token 预算耗在 reasoning_content 上而 content 为空。
            # 必须当失败:回传空串会让上游拿空计划继续跑,变成难排查的静默故障。
            rc = (msg.get("reasoning_content") or "")[:80]
            return False, f"模型返回空 content(推理占满预算?){' · 思考片段: ' + rc if rc else ''}"
        return True, txt


def register(agent_runtime):
    """把本驱动注册进上游注册表。上游不可用时静默跳过,不影响系统启动。"""
    try:
        rt = OpenAICompatRuntime()
        if not rt.available():
            return False
        agent_runtime.register("openai", lambda: rt)
        return True
    except Exception:
        return False
