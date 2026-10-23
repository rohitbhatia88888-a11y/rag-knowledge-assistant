"""Single sign-on with OpenID Connect (authorization code flow with PKCE).

Works with any standard IdP: Google, Okta, Entra ID, Keycloak. The flow is stateless on our side:
state, nonce and the PKCE verifier travel in a short-lived signed cookie scoped to /auth/oidc.

  /auth/oidc/login     -> redirect to the IdP
  /auth/oidc/callback  -> exchange the code, verify the ID token, map the email domain to a tenant,
                          then redirect to the web app with our session token in the URL fragment
                          (fragments are never sent to servers or written to access logs)
"""

import base64
import hashlib
import secrets
import time
from functools import lru_cache
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth import issue_token
from app.config import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])
TX_COOKIE = "oidc_tx"
TX_TTL = 600


class OIDCProvider:
    def __init__(self, issuer: str, http: httpx.AsyncClient | None = None) -> None:
        self.issuer = issuer.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=15)
        self._meta: dict | None = None
        self._jwks: jwt.PyJWKSet | None = None
        self._jwks_at = 0.0

    async def metadata(self) -> dict:
        if self._meta is None:
            resp = await self.http.get(f"{self.issuer}/.well-known/openid-configuration")
            resp.raise_for_status()
            self._meta = resp.json()
        return self._meta

    async def signing_key(self, kid: str | None):
        # Refresh keys hourly, or right away when an unknown key id shows up (IdP key rotation).
        stale = time.time() - self._jwks_at > 3600
        if self._jwks is None or stale or (kid and kid not in {k.key_id for k in self._jwks.keys}):
            resp = await self.http.get((await self.metadata())["jwks_uri"])
            resp.raise_for_status()
            self._jwks, self._jwks_at = jwt.PyJWKSet.from_dict(resp.json()), time.time()
        for key in self._jwks.keys:
            if kid is None or key.key_id == kid:
                return key
        raise jwt.InvalidTokenError(f"No signing key {kid!r} at the IdP")

    async def exchange(self, code: str, verifier: str, s: Settings) -> dict:
        resp = await self.http.post(
            (await self.metadata())["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.oidc_redirect_uri,
                "client_id": s.oidc_client_id,
                "client_secret": s.oidc_client_secret or "",
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()

    async def verify_id_token(self, id_token: str, nonce: str, s: Settings) -> dict:
        header = jwt.get_unverified_header(id_token)
        key = await self.signing_key(header.get("kid"))
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256", "ES256", "PS256"],
            audience=s.oidc_client_id,
            issuer=(await self.metadata())["issuer"],
            options={"require": ["exp", "iat", "sub"]},
            leeway=60,
        )
        if claims.get("nonce") != nonce:
            raise jwt.InvalidTokenError("nonce mismatch")
        return claims


@lru_cache
def _provider(issuer: str) -> OIDCProvider:
    return OIDCProvider(issuer)


def get_provider(s: Settings = Depends(get_settings)) -> OIDCProvider:
    if not (s.oidc_issuer and s.oidc_client_id):
        raise HTTPException(404, "SSO is not configured")
    return _provider(s.oidc_issuer)


def tenant_for(claims: dict, s: Settings) -> tuple[str, str]:
    """(email, tenant) for a verified sign-in, or raise. Unverified or unmapped emails are refused."""
    email = (claims.get("email") or "").lower()
    if not email or "@" not in email:
        raise PermissionError("The identity provider did not return an email address")
    if claims.get("email_verified") is False:
        raise PermissionError("Email address is not verified")
    tenant = s.oidc_domain_tenants.get(email.rsplit("@", 1)[1])
    if tenant is None:
        raise PermissionError(f"Sign-ins from {email.rsplit('@', 1)[1]} are not allowed")
    return email, tenant


@router.get("/config")
def auth_config(s: Settings = Depends(get_settings)) -> dict:
    return {"sso": bool(s.oidc_issuer and s.oidc_client_id), "dev_login": s.env == "dev"}


@router.get("/oidc/login")
async def login(s: Settings = Depends(get_settings), provider: OIDCProvider = Depends(get_provider)):
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {
        "response_type": "code",
        "client_id": s.oidc_client_id,
        "redirect_uri": s.oidc_redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    resp = RedirectResponse(f"{(await provider.metadata())['authorization_endpoint']}?{urlencode(params)}", 302)
    tx = jwt.encode(
        {"state": state, "nonce": nonce, "verifier": verifier, "exp": int(time.time()) + TX_TTL},
        s.jwt_secret,
        algorithm="HS256",
    )
    resp.set_cookie(
        TX_COOKIE, tx, max_age=TX_TTL, httponly=True, samesite="lax", path="/auth/oidc",
        secure=s.oidc_redirect_uri.startswith("https://"),
    )
    return resp


@router.get("/oidc/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    s: Settings = Depends(get_settings),
    provider: OIDCProvider = Depends(get_provider),
):
    def back(fragment: dict) -> RedirectResponse:
        resp = RedirectResponse(f"{s.web_url}/#{urlencode(fragment)}", 302)
        resp.delete_cookie(TX_COOKIE, path="/auth/oidc")
        return resp

    if error:
        return back({"error": f"Sign-in was cancelled or refused ({error})"})
    try:
        tx = jwt.decode(request.cookies.get(TX_COOKIE, ""), s.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return back({"error": "Sign-in expired, please try again"})
    if not code or not state or not secrets.compare_digest(state, tx["state"]):
        return back({"error": "Sign-in state did not match, please try again"})

    try:
        tokens = await provider.exchange(code, tx["verifier"], s)
        claims = await provider.verify_id_token(tokens["id_token"], tx["nonce"], s)
        email, tenant = tenant_for(claims, s)
    except PermissionError as e:
        return back({"error": str(e)})
    except (httpx.HTTPError, jwt.PyJWTError, KeyError) as e:
        return back({"error": f"Sign-in failed: {type(e).__name__}"})

    groups = claims.get(s.oidc_groups_claim) or []
    groups = [str(g).lower() for g in groups] if isinstance(groups, list) else []
    return back({"token": issue_token(email, tenant, groups, s)})
