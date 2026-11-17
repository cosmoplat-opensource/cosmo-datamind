"""Run the DataMind end-to-end test with the official ATH Python SDK (ath-sdk 0.1.0) on the agent side.

Exposes the same interface as ath_client.ATHGatewayClient so run_e2e.py can use either.  Requires the
python-sdk branch feat/gateway-user-token (user_token= option, see contrib/python-sdk): the reference
gateway needs X-ATH-User-Token on every /ath/* call and the released SDK cannot send it.
"""
from cryptography.hazmat.primitives import serialization
from ath import ATHGatewayClient as Official
from ath.exceptions import ATHError

from ath_client import ATHClientError


def _pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _wrap(fn):
    def inner(*a, **k):
        try:
            return fn(*a, **k)
        except ATHError as e:
            raise ATHClientError(e.code, str(e), e.status, e.details) from None
    return inner


class OfficialGatewayClient:
    def __init__(self, url, agent_id, private_key, key_id="default", user_token=None, timeout=60):
        self.url, self.agent_id = url.rstrip("/"), agent_id
        self._c = Official(url, agent_id, _pem(private_key), key_id=key_id, timeout=timeout, user_token=user_token)

    client_id = property(lambda s: s._c.client_id, lambda s, v: s._c.set_credentials(v, s._c._client_secret))
    client_secret = property(lambda s: s._c._client_secret, lambda s, v: s._c.set_credentials(s._c._client_id, v))
    token = property(lambda s: s._c.access_token, lambda s, v: s._c.set_token(v))

    def attest(self, audience=None):
        return self._c._attest(audience)

    @_wrap
    def discover(self):
        return self._c.discover().model_dump()

    @_wrap
    def register(self, developer, providers, purpose, redirect_uris=None):
        return self._c.register(developer=developer, providers=providers, purpose=purpose, redirect_uris=redirect_uris).model_dump()

    @_wrap
    def authorize(self, provider, scopes, redirect_uri=None, resource=None):
        return self._c.authorize(provider, scopes, redirect_uri=redirect_uri, resource=resource).model_dump()

    @_wrap
    def exchange_token(self, code, session_id):
        return self._c.exchange_token(code, session_id).model_dump()

    @_wrap
    def proxy(self, provider, method, path, body=None):
        return self._c.proxy(provider, method, path, body)

    def mcp(self, provider, method, params=None, rid=1):
        return self.proxy(provider, "POST", "/mcp", {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})

    @_wrap
    def revoke(self):
        self._c.revoke()

    @_wrap
    def _request(self, method, path, body=None, headers=None):
        r = self._c._raw_request(method, self.url + path, body, headers)
        return r.json()
