from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import Principal, current_principal, issue_token
from app.config import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class DevTokenRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default="acme", min_length=1, max_length=64)
    groups: list[str] = []


@router.post("/dev-token")
def dev_token(body: DevTokenRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Mint a token for any user/groups. Dev only; production uses SSO."""
    if settings.env != "dev":
        raise HTTPException(404)
    return {"token": issue_token(body.user_id, body.tenant_id, body.groups, settings)}


@router.get("/me")
def me(principal: Principal = Depends(current_principal)) -> dict:
    return {"user_id": principal.user_id, "tenant_id": principal.tenant_id, "groups": list(principal.groups)}
