from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
from app.core.config import settings
from app.db.base_class import Base
import logging
from typing import AsyncGenerator
from contextlib import asynccontextmanager
import re

logger = logging.getLogger(__name__)

# Configure database connection pooling
try:
    # Convert the DATABASE_URL to use asyncpg instead of psycopg2
    db_url = settings.DATABASE_URL
    
    # If using postgresql://, convert to postgresql+asyncpg://
    if db_url.startswith('postgresql://'):
        db_url = db_url.replace('postgresql://', 'postgresql+asyncpg://')
    # If using postgres://, convert to postgresql+asyncpg://
    elif db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql+asyncpg://')
    
    logger.info(f"Using async database connection with asyncpg")
    
    # Create async engine with optimized connection pooling
    engine = create_async_engine(
        db_url,
        echo=settings.SQL_ECHO,  # Set to True for debugging SQL queries
        future=True,
        pool_pre_ping=True,  # Verify connections before using them
        pool_size=settings.DB_POOL_SIZE,  # Default connection pool size
        max_overflow=settings.DB_MAX_OVERFLOW,  # Maximum number of connections to create above pool_size
        pool_timeout=settings.DB_POOL_TIMEOUT,  # Seconds to wait before giving up on getting a connection
        pool_recycle=settings.DB_POOL_RECYCLE,  # Recycle connections after this many seconds
        # asyncpg-specific connect args
        connect_args={
            "command_timeout": settings.DB_COMMAND_TIMEOUT,  # Maximum time for a command to run
        }
    )
    
    # Create async session factory with optimized settings
    AsyncSessionLocal = sessionmaker(
        engine, 
        class_=AsyncSession, 
        expire_on_commit=False,  # Don't expire objects after commit
        autocommit=False, 
        autoflush=False
    )
except OperationalError as e:
    logger.error(f"Failed to connect to database: {e}")
    raise

Base = declarative_base()

# Dependency to use in FastAPI endpoints
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency that provides an async database session.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

# Context manager for use in scripts and tests
@asynccontextmanager
async def get_db_context():
    """
    Context manager for database sessions outside of request handlers.
    """
    session = AsyncSessionLocal()
    try:
        yield session
    finally:
        await session.close()

# Function to initialize database connection
async def initialize_db():
    """
    Initialize database connection and verify it's working.
    """
    async with engine.begin() as conn:
        logger.info("Database connection initialized successfully")
    
    return True

# Function to close database connection
async def close_db_connection():
    """
    Close database connection pool.
    """
    await engine.dispose()
    logger.info("Database connection pool closed") 