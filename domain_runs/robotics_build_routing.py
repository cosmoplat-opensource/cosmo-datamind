"""Snapshot platform model routing without probing or fallback — DR-070 / IR-024 T03."""
from copy import deepcopy
import hashlib
import json
import math
import os
from urllib.parse import urlsplit

from openai_runtime import DEFAULT_MODEL, ThinkingConfigurationError
from .build_service import BuildRequestError

PROVIDERS = ('openai', 'hermes', 'claude-code', 'openclaw')


def _configuration_error():
    return BuildRequestError('configuration_error',
        '模型配置缺失、无效或与已注册运行时不一致，请检查平台引擎设置。', 503)


def _text(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise _configuration_error()
    return value.strip()


def _base(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or '?' in value or '#' in value or any(c.isspace() or ord(c) < 32 for c in value)):
            raise ValueError('base')
        _ = parsed.port
    except (ValueError, TypeError):
        raise _configuration_error() from None
    return value.rstrip('/')


class BuildRoute:
    """One resolved runtime; public snapshots never contain transport credentials."""

    def __init__(self, runtime=None, *, provider=None, model=None, identity=None):
        self._runtime = runtime
        self._public = {'method': 'legacy_xml', 'model': None}
        if runtime is not None:
            digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                               separators=(',', ':')).encode()).hexdigest()
            self._public = {'method': 'llm_tbox_abox', 'model': {
                'provider': provider, 'model': model, 'configuration_id': digest}}

    def public(self):
        return deepcopy(self._public)

    def request(self, session_id, prompt, *, timeout):
        if self._runtime is None:
            raise BuildRequestError('model_not_available', '离线构建不调用模型。', 503)
        if (not isinstance(session_id, str) or not session_id.strip()
                or not isinstance(prompt, str) or not prompt.strip()
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise BuildRequestError('invalid_request', '模型调用需要有效会话、文本和正数超时。')
        # DR-070: per-call evidence, without shared mutable runtime error state.
        from llm_diagnostics import safe_diagnostic, transport_diagnostic, describe
        diagnostic=None
        try:
            # BuildRoute serves TBox proposals only. Other applications call the
            # shared runtime normally and retain their own thinking defaults.
            detailed=getattr(self._runtime,'run_tbox_turn_detailed',None)
            if not callable(detailed):
                detailed=getattr(self._runtime,'run_turn_detailed',None)
            if callable(detailed):
                ok,content,diagnostic=detailed(session_id,prompt,timeout=timeout,model=self._public['model']['model'])
                diagnostic=safe_diagnostic(diagnostic)
            else:
                ok,content=self._runtime.run_turn(session_id,prompt,timeout=timeout,model=self._public['model']['model'])
        except ThinkingConfigurationError:
            raise BuildRequestError('configuration_error',
                '机器人本体设计思考配置无效；DATAMIND_ROBOTICS_DESIGN_THINKING 仅支持 disabled、low、inherit。',
                503) from None
        except Exception as exc:
            ok,content=False,None;diagnostic=transport_diagnostic(exc)
        if ok is not True or not isinstance(content,str) or not content.strip():
            diagnostic=diagnostic or {'code':'model_empty_content' if ok is True and isinstance(content,str) else 'model_runtime_error'}
            raise BuildRequestError('model_request_failed',describe(diagnostic),502,issues=[diagnostic]) from None
        return content


def require_same_route(pinned_public, current_public):
    """DR-070 §7: validate identity before a new turn, never mutate old snapshots."""
    def identity(value):
        if not isinstance(value, dict):
            raise ValueError('route')
        if value.get('method') == 'legacy_xml' and value.get('model') is None:
            return ('legacy_xml',)
        model = value.get('model')
        if value.get('method') != 'llm_tbox_abox' or not isinstance(model, dict):
            raise ValueError('route')
        fields = tuple(model.get(k) for k in ('provider', 'model', 'configuration_id'))
        if any(not isinstance(v, str) or not v.strip() for v in fields):
            raise ValueError('identity')
        return ('llm_tbox_abox', *fields)
    try:
        if identity(pinned_public) != identity(current_public):
            raise ValueError('changed')
    except ValueError:
        raise BuildRequestError('configuration_changed',
            '构建方式或模型身份已变化，请恢复原配置重试，或发起新的构建。', 409) from None


def resolve_route(*, config=None, environ=None, registry=None):
    """Resolve actual platform state; explicit arguments isolate tests and hosts.

    Neither registry lookup nor configuration validation invokes a model turn.
    A local registration is readiness to attempt a request, not an online claim.
    """
    try:
        if config is None:
            from srv_engine import _load_engine_cfg
            config = _load_engine_cfg()
        env = dict(os.environ if environ is None else environ)
        if not isinstance(config, dict):
            raise _configuration_error()
        llm = config.get('llm', {})
        if not isinstance(llm, dict):
            raise _configuration_error()

        def effective(name):
            return _text(env.get('DATAMIND_LLM_' + name.upper())) or _text(llm.get(name))

        base, key, model = (effective(name) for name in ('base', 'key', 'model'))
        for name in ('timeout', 'max_tokens'):
            raw = env.get('DATAMIND_LLM_' + name.upper()) or llm.get(name)
            if raw not in (None, ''):
                # Match the platform settings API's positive integer contract.
                if isinstance(raw, bool) or not str(raw).isdigit() or int(raw) <= 0:
                    raise _configuration_error()
        configured_openai = bool(base or key or model)
        if configured_openai and not (base and key):
            raise _configuration_error()
        if base:
            base = _base(base)
        preferred = _text(env.get('CLAW_DRIVER')) or _text(config.get('driver'))
        if preferred and preferred not in PROVIDERS:
            raise _configuration_error()
        if registry is None:
            try:
                import agent_runtime as registry
            except ModuleNotFoundError as exc:
                if exc.name != 'agent_runtime':
                    raise
                if configured_openai or preferred:
                    raise _configuration_error() from None
                return BuildRoute()
        names = registry.available()
        if not isinstance(names, (list, tuple, set)) or any(not isinstance(n, str) for n in names):
            raise _configuration_error()
        if configured_openai != ('openai' in names):
            raise _configuration_error()
        if not names:
            if preferred:
                raise _configuration_error()
            return BuildRoute()
        if preferred and preferred not in names:
            raise _configuration_error()
        first = preferred or 'hermes'
        ordered = (first,) + tuple(n for n in PROVIDERS if n != first)
        provider = next((name for name in ordered if name in names), None)
        if provider is None:
            raise _configuration_error()
        runtime = registry.get_runtime(provider)
        if not callable(getattr(runtime, 'run_turn', None)):
            raise _configuration_error()
        actual_model = _text(getattr(runtime, 'model', None))
        if not actual_model:
            raise _configuration_error()
        if provider == 'openai':
            if (_base(_text(getattr(runtime, 'base', None))) != base
                    or _text(getattr(runtime, 'key', None)) != key
                    or actual_model != (model or DEFAULT_MODEL)):
                raise _configuration_error()
            identity = {'provider': provider, 'model': actual_model, 'base': base}
        else:
            fields = {'hermes': ('HERMES_MODEL', 'hermes_model'),
                      'claude-code': ('CLAUDE_MODEL', 'claude_model'),
                      'openclaw': ('OPENCLAW_MODEL', 'openclaw_model')}
            variable, field = fields[provider]
            expected = _text(env.get(variable)) or _text(config.get(field))
            if expected and actual_model != expected:
                raise _configuration_error()
            provider_id = _text(getattr(runtime, 'provider', None))
            if provider == 'hermes':
                expected_provider = _text(env.get('HERMES_PROVIDER')) or _text(config.get('hermes_provider'))
                if provider_id and expected_provider and provider_id != expected_provider:
                    raise _configuration_error()
                provider_id = provider_id or expected_provider
            identity = {'provider': provider, 'model': actual_model, 'provider_id': provider_id}
        return BuildRoute(runtime, provider=provider, model=actual_model, identity=identity)
    except BuildRequestError:
        raise
    except Exception:
        raise _configuration_error() from None
