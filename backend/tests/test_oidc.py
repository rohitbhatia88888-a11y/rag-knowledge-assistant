"""OIDC login flow against a fake identity provider that signs real RS256 ID tokens."""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import oidc
from app.config import Settings, get_settings

ISSUER = "https://idp.example"
CLIENT_ID = "rag-web"


class FakeIdP:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.claims: dict = {"email": "alice@halcyon.com", "email_verified": True, "groups": ["HR", "admins"]}
        self.nonce_override: str | None = None
        self.codes: dict[str, dict] = {}  # code -> authorize params

    def jwks(self) -> dict:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
            })
        if path == "/jwks":
            return httpx.Response(200, json=self.jwks())
        if path == "/token":
            form = parse_qs(request.content.decode())
            auth = self.codes.pop(form["code"][0], None)
            if auth is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            # PKCE: the verifier must hash to the challenge sent at /authorize.
            expected = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
            if expected != auth["code_challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE"})
            now = int(time.time())
            id_token = jwt.encode(
                {"iss": ISSUER, "aud": CLIENT_ID, "sub": "u-123", "iat": now, "exp": now + 300,
                 "nonce": self.nonce_override or auth["nonce"], **self.claims},
                self.key, algorithm="RS256", headers={"kid": "k1"},
            )
            return httpx.Response(200, json={"id_token": id_token, "access_token": "x", "token_type": "Bearer"})
        return httpx.Response(404)

    def authorize(self, url: str) -> str:
        """What the browser does at the IdP: sign in, get redirected back with a code."""
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        code = f"code-{len(self.codes)}"
        self.codes[code] = params
        return f"{params['redirect_uri']}?code={code}&state={params['state']}"


@pytest.fixture
def setup():
    idp = FakeIdP()
    settings = Settings(
        _env_file=None, env="test", jwt_secret="test-secret-" + "x" * 32,
        oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID, oidc_client_secret="s",
        oidc_redirect_uri="http://testserver/auth/oidc/callback",
        oidc_domain_tenants={"halcyon.com": "halcyon"}, web_url="http://web.example",
    )
    provider = oidc.OIDCProvider(ISSUER, httpx.AsyncClient(transport=httpx.MockTransport(idp.handler)))
    app = FastAPI()
    app.include_router(oidc.router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[oidc.get_provider] = lambda: provider
    return TestClient(app, follow_redirects=False), idp, settings


def sign_in(client: TestClient, idp: FakeIdP, tamper_state: bool = False) -> dict:
    login = client.get("/auth/oidc/login")
    assert login.status_code == 302 and "code_challenge_method=S256" in login.headers["location"]
    back = idp.authorize(login.headers["location"])
    if tamper_state:
        back = back.replace("state=", "state=evil")
    cb = client.get(back.replace("http://testserver", ""))
    assert cb.status_code == 302 and cb.headers["location"].startswith("http://web.example/#")
    return {k: v[0] for k, v in parse_qs(urlparse(cb.headers["location"]).fragment).items()}


def test_successful_sign_in_issues_session_token(setup):
    client, idp, settings = setup
    result = sign_in(client, idp)
    claims = jwt.decode(result["token"], settings.jwt_secret, algorithms=["HS256"])
    assert claims["sub"] == "alice@halcyon.com" and claims["tenant"] == "halcyon"
    assert claims["groups"] == ["hr", "admins"] and claims["exp"] > time.time()


def test_tampered_state_is_rejected(setup):
    client, idp, _ = setup
    assert "did not match" in sign_in(client, idp, tamper_state=True)["error"]


def test_replayed_or_wrong_nonce_is_rejected(setup):
    client, idp, _ = setup
    idp.nonce_override = "attacker-nonce"
    assert sign_in(client, idp)["error"] == "Sign-in failed: InvalidTokenError"


def test_unknown_domain_and_unverified_email_are_refused(setup):
    client, idp, _ = setup
    idp.claims["email"] = "mallory@gmail.com"
    assert "gmail.com are not allowed" in sign_in(client, idp)["error"]
    idp.claims.update(email="bob@halcyon.com", email_verified=False)
    assert "not verified" in sign_in(client, idp)["error"]


def test_callback_without_login_cookie_is_rejected(setup):
    client, idp, _ = setup
    login = client.get("/auth/oidc/login")
    back = idp.authorize(login.headers["location"])
    client.cookies.clear()
    cb = client.get(back.replace("http://testserver", ""))
    assert "expired" in parse_qs(urlparse(cb.headers["location"]).fragment)["error"][0]


def test_config_endpoint(setup):
    client, _, _ = setup
    assert client.get("/auth/config").json() == {"sso": True, "dev_login": False, "demo": False}
