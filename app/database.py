from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings
from app.models import Base

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    connect_args={"command_timeout": 30},
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """Dev convenience only. In production, schema changes go through Alembic
    (see migrations/) so they're versioned and reversible."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
