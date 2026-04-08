from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool
from sqlalchemy import text, event
import structlog
import ssl
import os
from uuid import uuid4

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# Supabase/PgBouncer connection configuration
# The pooler uses PgBouncer which requires special handling for asyncpg (prepared statements disabled)
_connect_args = {}
_pool_class = None

# Detect Supabase or PgBouncer usage:
# - Check for any supabase-related string in the URL (handles regional poolers like aws-0-*.pooler.supabase.com)
# - Allow explicit override via PGBOUNCER_MODE environment variable
_db_url_lower = settings.database_url.lower()
_is_supabase = (
    "supabase" in _db_url_lower or 
    "pooler" in _db_url_lower or
    os.getenv("PGBOUNCER_MODE", "").lower() in ("true", "1", "yes")
)

if _is_supabase:
    logger.info("PgBouncer/Supabase mode detected - disabling prepared statements")
    
    # Custom connection class that generates unique prepared statement names using UUIDs
    # This prevents collisions when pgbouncer routes requests to different connections
    try:
        from asyncpg import Connection as AsyncpgConnection
        
        class PgBouncerConnection(AsyncpgConnection):
            """Custom asyncpg connection class for PgBouncer compatibility.
            
            Generates unique prepared statement names using UUIDs to prevent
            'prepared statement already exists' errors when using pgbouncer
            in transaction or statement pooling mode.
            """
            def _get_unique_id(self, prefix: str) -> str:
                return f'__asyncpg_{prefix}_{uuid4()}__'
        
        _connection_class = PgBouncerConnection
        logger.info("Using custom PgBouncer-compatible connection class")
    except ImportError:
        _connection_class = None
        logger.warning("Could not import asyncpg Connection class, falling back to cache disabling only")
    
    # Create SSL context that doesn't verify certificates (required for Supabase pooler)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    _connect_args = {
        "ssl": ssl_context,
        # Disable ALL prepared statement caches for PgBouncer compatibility
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    }
    
    # Add custom connection class if available
    if _connection_class:
        _connect_args["connection_class"] = _connection_class
    
    # Use NullPool to let Supabase's pooler handle connection pooling
    _pool_class = NullPool

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.log_level == "DEBUG",
    poolclass=_pool_class,
    connect_args=_connect_args,
    # Disable SQLAlchemy's prepared statement cache for PgBouncer compatibility
    query_cache_size=0 if _is_supabase else 500,
)

# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Base class for models
Base = declarative_base()


async def init_db():
    """Initialize database connection."""
    try:
        async with engine.begin() as conn:
            # Test connection
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection successful")
    except Exception as e:
        logger.error("Database connection failed", error=str(e))
        raise


async def close_db():
    """Close database connection."""
    await engine.dispose()
    logger.info("Database connection disposed")


async def get_db() -> AsyncSession:
    """Dependency to get database session."""
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
