from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import Principal, current_principal, issue_token
from app.config import Settings, get_settings
from app.demo import PERSONAS, persona_groups

router = APIRouter(prefix="/auth", tags=["auth"])
DEMO_TENANT = "halcyon"


class DevTokenRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default="acme", min_length=1, max_length=64)
    groups: list[str] = []


class DemoTokenRequest(BaseModel):
    persona: str


@router.post("/dev-token")
def dev_token(body: DevTokenRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Mint a token for any user/groups. Dev only; production uses SSO."""
    if settings.env != "dev":
        raise HTTPException(404)
    return {"token": issue_token(body.user_id, body.tenant_id, body.groups, settings)}


@router.get("/demo/personas")
def demo_personas(settings: Settings = Depends(get_settings)) -> list[dict]:
    if not settings.demo_mode:
        raise HTTPException(404)
    return [{"id": pid, **p, "groups": persona_groups(pid)} for pid, p in PERSONAS.items()]


@router.post("/demo/token")
def demo_token(body: DemoTokenRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Sign in as one of the fixed demo personas. Only these identities exist in the demo."""
    if not settings.demo_mode:
        raise HTTPException(404)
    if body.persona not in PERSONAS:
        raise HTTPException(422, "Unknown persona")
    return {"token": issue_token(f"{body.persona}@halcyon.example", DEMO_TENANT, persona_groups(body.persona), settings)}


@router.get("/me")
def me(principal: Principal = Depends(current_principal)) -> dict:
    return {"user_id": principal.user_id, "tenant_id": principal.tenant_id, "groups": list(principal.groups)}
