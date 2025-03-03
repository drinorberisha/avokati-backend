import os
import pytest
import asyncio
from typing import AsyncGenerator, Generator
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from fastapi import FastAPI
from asgi_lifespan import LifespanManager

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from main import app

# Test database URL
TEST_DATABASE_URL = settings.DATABASE_URL.replace(
    settings.DATABASE_NAME, f"{settings.DATABASE_NAME}_test"
)

# Create test engine
engine_test = create_async_engine(TEST_DATABASE_URL, echo=True, future=True)
TestingSessionLocal = sessionmaker(
    engine_test, class_=AsyncSession, expire_on_commit=False
)

@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
async def test_app() -> AsyncGenerator[FastAPI, None]:
    """Create a test instance of the FastAPI application."""
    async with LifespanManager(app):
        yield app

@pytest.fixture(scope="session")
async def test_db_setup() -> AsyncGenerator[None, None]:
    """Set up the test database."""
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.fixture
async def test_db(test_db_setup) -> AsyncGenerator[AsyncSession, None]:
    """Create a new database session for a test."""
    async with TestingSessionLocal() as session:
        yield session
        await session.rollback()
        await session.close()

@pytest.fixture
async def client(test_app, test_db) -> AsyncGenerator[AsyncClient, None]:
    """Create a test client with the test database."""
    async with AsyncClient(
        app=test_app,
        base_url="http://test",
    ) as client:
        yield client

@pytest.fixture
def test_password() -> str:
    """Return a test password."""
    return "test_password123"

@pytest.fixture
def test_user_data(test_password):
    """Return test user data."""
    return {
        "email": "test@example.com",
        "password": test_password,
        "full_name": "Test User"
    }

@pytest.fixture
def test_superuser_data(test_password):
    """Return test superuser data."""
    return {
        "email": "admin@example.com",
        "password": test_password,
        "full_name": "Admin User",
        "is_superuser": True
    } 