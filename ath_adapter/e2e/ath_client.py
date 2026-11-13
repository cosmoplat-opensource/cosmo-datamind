"""ath_client — a Python client for the Agent Trust Handshake (ATH) protocol v0.1, gateway mode.

Mirrors the official TypeScript client (@ath-protocol/client v0.1.1): ES256 agent attestation JWTs with a
unique ``jti`` (replay protection) and an audience bound to the endpoint, then

    discover() -> register() -> authorize() -> exchange_token() -> proxy() -> revoke()

Dependencies: PyJWT and cryptography.  The agent identity document (``agent_id`` URL) is produced by
``identity_document()``; it is an A2A AgentCard that also carries the ATH ``public_key`` JWK, so one URL
serves both protocols (A2A 0.3.0 AgentCard fields; see identity_document).
"""
import json
import time
import uuid
import urllib.error
import urllib.request

import jwt
from cryptography.hazmat.primitives.asymmetric import ec


class ATHClientError(Exception):
    def __init__(self, code, message, status=None, details=None):
        super().__init__(f"[{code}] {message}")
        self.code, self.status, self.details = code, status, details or {}


def new_key():
    return ec.generate_private_key(ec.SECP256R1())


def public_jwk(private_key, kid="default"):
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key()))
    jwk.update(kid=kid, alg="ES256", use="sig")
    return jwk


def identity_document(agent_id, name, description, url, skills, private_key, kid="default", provider=None):
    """A2A 0.3.0 AgentCard plus the ATH ``public_key`` JWK.

    Publish it at ``https://<host>/.well-known/agent-card.json`` (the A2A 0.3 well-known path) and use that
    URL as the ATH ``agent_id``; one document then serves A2A discovery and ATH key resolution.
    """
    return {
        "protocolVersion": "0.3.0", "preferredTransport": "JSONRPC",
        "name": name, "description": description, "url": url, "version": "0.1.0",
        "provider": provider or {"organization": "", "url": ""},
        "capabilities": {"streaming": False, "pushNotifications": False, "extensions": [
            {"uri": "https://github.com/ath-protocol/agent-trust-handshake-protocol", "description": "ATH v0.1 agent identity (ES256 public_key)", "required": False}]},
        "defaultInputModes": ["application/json"], "defaultOutputModes": ["application/json"],
        "skills": skills,
        "agent_id": agent_id,
        "public_key": public_jwk(private_key, kid),
    }


class ATHGatewayClient:
    def __init__(self, url, agent_id, private_key, key_id="default", user_token=None, timeout=60):
        self.url, self.agent_id, self.private_key, self.key_id = url.rstrip("/"), agent_id, private_key, key_id
        self.user_token, self.timeout = user_token, timeout
        self.client_id = self.client_secret = self.token = None

    # ---------------------------------------------------------------- primitives
    def attest(self, audience=None, ttl=3600):
        now = int(time.time())
        claims = {"iss": self.agent_id, "sub": self.agent_id, "aud": audience or self.url, "iat": now,
                  "exp": now + ttl, "jti": str(uuid.uuid4()), "capabilities": []}
        return jwt.encode(claims, self.private_key, algorithm="ES256", headers={"kid": self.key_id})

    def _request(self, method, path, body=None, headers=None):
        h = {"Content-Type": "application/json"}
        if self.user_token:
            h["X-ATH-User-Token"] = self.user_token
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw, status = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {"raw": raw.decode(errors="replace")}
        if status >= 400:
            if not isinstance(payload, dict):
                payload = {"raw": payload}
            err = payload.get("error") if isinstance(payload.get("error"), dict) else payload
            raise ATHClientError(err.get("code") or payload.get("code") or str(payload.get("error") or "UNKNOWN"),
                                 err.get("message") or payload.get("message") or f"HTTP {status}", status, payload)
        return payload

    # ---------------------------------------------------------------- lifecycle
    def discover(self):
        return self._request("GET", "/.well-known/ath.json")

    def register(self, developer, providers, purpose, redirect_uris=None):
        res = self._request("POST", "/ath/agents/register", {
            "agent_id": self.agent_id, "agent_attestation": self.attest(), "developer": developer,
            "requested_providers": providers, "purpose": purpose,
            "redirect_uris": redirect_uris or [f"{self.url}/ath/callback"]})
        self.client_id, self.client_secret = res["client_id"], res["client_secret"]
        return res

    def authorize(self, provider, scopes, redirect_uri=None, resource=None):
        if not self.client_id:
            raise ATHClientError("NOT_REGISTERED", "call register() first")
        body = {"client_id": self.client_id, "agent_attestation": self.attest(), "provider_id": provider,
                "scopes": scopes, "user_redirect_uri": redirect_uri or f"{self.url}/ath/callback", "state": str(uuid.uuid4())}
        if resource:
            body["resource"] = resource
        return self._request("POST", "/ath/authorize", body)

    def exchange_token(self, code, session_id):
        res = self._request("POST", "/ath/token", {
            "grant_type": "authorization_code", "client_id": self.client_id, "client_secret": self.client_secret,
            "agent_attestation": self.attest(audience=f"{self.url}/ath/token"), "code": code, "ath_session_id": session_id})
        self.token = res["access_token"]
        return res

    def proxy(self, provider, method, path, body=None):
        if not self.token:
            raise ATHClientError("NO_TOKEN", "complete the handshake first")
        return self._request(method, f"/ath/proxy/{provider}{path}", body,
                             {"Authorization": f"Bearer {self.token}", "X-ATH-Agent-ID": self.agent_id})

    def mcp(self, provider, method, params=None, rid=1):
        """MCP JSON-RPC over the ATH proxy: the ATH token authorises, MCP carries the tool call."""
        return self.proxy(provider, "POST", "/mcp", {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})

    def revoke(self):
        if self.token:
            self._request("POST", "/ath/revoke", {"client_id": self.client_id, "client_secret": self.client_secret, "token": self.token})
            self.token = None
