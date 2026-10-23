from dataclasses import dataclass, field

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    groups: tuple[str, ...] = field(default_factory=tuple)

    @property
    def principals(self) -> list[str]:
        """Every ACL entry that grants this user access. Matched against chunk payloads."""
        return [f"user:{self.user_id}", "group:everyone", *(f"group:{g}" for g in self.groups)]


def issue_token(user_id: str, tenant_id: str, groups: list[str], settings: Settings) -> str:
    return jwt.encode(
        {"sub": user_id, "tenant": tenant_id, "groups": groups},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        claims = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {e}") from e
    return Principal(user_id=claims["sub"], tenant_id=claims["tenant"], groups=tuple(claims.get("groups", [])))
