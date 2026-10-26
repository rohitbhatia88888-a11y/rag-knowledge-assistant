import time
from collections import defaultdict

from fastapi import Depends, HTTPException, Request, status

from app.auth import Principal, current_principal
from app.config import get_settings


class DemoLimiter:
    """In-process caps for the public demo: questions per visitor IP per hour, and per day overall.

    Single-process only (the demo runs one container); counts reset on restart, which is fine
    because the API key's own daily spending limit is the hard backstop.
    """

    def __init__(self) -> None:
        self.per_ip: dict[tuple[str, int], int] = defaultdict(int)
        self.per_day: dict[int, int] = defaultdict(int)

    def check(self, ip: str) -> None:
        s = get_settings()
        now = time.time()
        hour, day = int(now // 3600), int(now // 86400)
        if self.per_day[day] >= s.demo_questions_per_day:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "The demo has reached today's question limit. Please come back tomorrow.",
            )
        if self.per_ip[(ip, hour)] >= s.demo_questions_per_hour:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Demo limit: {s.demo_questions_per_hour} questions per hour. Please try again later.",
            )
        self.per_ip[(ip, hour)] += 1
        self.per_day[day] += 1
        # Drop old windows so memory stays bounded.
        for key in [k for k in self.per_ip if k[1] < hour]:
            del self.per_ip[key]


demo_limiter = DemoLimiter()


def client_ip(request: Request) -> str:
    # Behind the hosting proxy the visitor is the first X-Forwarded-For entry.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit(request: Request, principal: Principal = Depends(current_principal)) -> Principal:
    s = get_settings()
    if s.demo_mode:
        demo_limiter.check(client_ip(request))
        return principal
    # Fixed one-minute window per user, counted in Redis.
    redis = request.app.state.redis
    window = int(time.time() // 60)
    key = f"rl:{principal.tenant_id}:{principal.user_id}:{window}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 70)
    if count > s.rate_limit_per_minute:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded, try again in a minute")
    return principal
