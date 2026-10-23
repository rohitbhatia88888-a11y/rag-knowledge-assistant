import time

from fastapi import Depends, HTTPException, Request, status

from app.auth import Principal, current_principal
from app.config import get_settings


async def rate_limit(request: Request, principal: Principal = Depends(current_principal)) -> Principal:
    """Fixed one-minute window per user, counted in Redis."""
    redis = request.app.state.redis
    window = int(time.time() // 60)
    key = f"rl:{principal.tenant_id}:{principal.user_id}:{window}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 70)
    if count > get_settings().rate_limit_per_minute:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded, try again in a minute")
    return principal
