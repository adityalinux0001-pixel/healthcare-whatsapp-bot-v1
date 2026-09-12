from redis.asyncio import Redis, from_url
from arq import create_pool
from arq.connections import RedisSettings, ArqRedis

from app.config import settings

redis_client: Redis = from_url(settings.redis_url, decode_responses=True)
_arq_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _arq_pool


async def close_arq_pool() -> None:
    global _arq_pool
    if _arq_pool is not None:
        await _arq_pool.close()
        _arq_pool = None
