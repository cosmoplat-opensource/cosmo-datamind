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


def _extra_body():
    """DATAMIND_LLM_EXTRA_BODY：合并进 chat/completions 请求体的 JSON 片段。

    用途是不改代码就能传服务商特有参数——典型是推理型模型的思考开关：
    DeepSeek/GLM 系列 `{"thinking":{"type":"disabled"}}`、Qwen `{"enable_thinking":false}`。
    背景：本体抽取的长提示词会让推理型模型把 max_tokens 全花在思考上，正文为空
    （实测 deepseek-v4-flash 思考 246s 后返回空 content），关掉思考即可稳定产出 JSON。
    解析失败时忽略并回空，不让一处配置笔误拖垮所有调用。"""
    raw = (os.environ.get("DATAMIND_LLM_EXTRA_BODY") or "").strip()
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


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
            **_extra_body(),
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

    def run_turn_stream(self, session_id, message, timeout=300, model=None, on_delta=None,
                        on_reasoning=None):
        """流式版 run_turn：返回同样的 (ok, text)，但正文增量经 on_delta(str) 逐块回报；
        推理型模型的思考增量（reasoning_content）经 on_reasoning(str) 回报——
        这类模型正文之前常先思考一两分钟，只回报正文会让面板在最需要它的时候空着。

        用途是让几分钟的长生成在界面上可见（本体构建的过程输出面板）。
        端点不支持流式（返回普通 JSON）时自动按整包解析，行为退化为 run_turn。"""
        if not self.available():
            return False, "未配置 DATAMIND_LLM_BASE / DATAMIND_LLM_KEY"
        body = json.dumps({
            "model": model or self.model,
            "messages": [{"role": "user", "content": message}],
            "temperature": 0,
            "max_tokens": _int_env("DATAMIND_LLM_MAX_TOKENS", 16384),
            **_extra_body(),
            "stream": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/chat/completions", data=body,
            headers={"Authorization": "Bearer " + self.key,
                     "Content-Type": "application/json", "Accept": "text/event-stream"})
        parts, reasoning = [], []
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ctype = (r.headers.get("Content-Type") or "").lower()
                if "text/event-stream" not in ctype:
                    # 端点不认 stream：整包回来，按非流式解析
                    d = json.loads(r.read())
                    msg = (d.get("choices") or [{}])[0].get("message") or {}
                    txt = (msg.get("content") or "").strip()
                    if txt and on_delta:
                        on_delta(txt)
                    return (True, txt) if txt else (False, "模型返回空 content")
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except Exception:
                        continue
                    delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                    piece = delta.get("content") or ""
                    if piece:
                        parts.append(piece)
                        if on_delta:
                            try: on_delta(piece)
                            except Exception: pass
                    elif delta.get("reasoning_content"):
                        reasoning.append(delta["reasoning_content"])
                        if on_reasoning:
                            try: on_reasoning(delta["reasoning_content"])
                            except Exception: pass
        except Exception as e:
            return False, f"{type(e).__name__}: {str(e)[:160]}"
        txt = "".join(parts).strip()
        if not txt:
            rc = "".join(reasoning)[:80]
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
