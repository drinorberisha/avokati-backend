from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import OperationalError
from app.core.config import settings
from app.db.base_class import Base
import logging

logger = logging.getLogger(__name__)

try:
    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
except OperationalError as e:
    logger.error(f"Failed to connect to database: {e}")
    raise

Base = declarative_base()

# Dependency to use in FastAPI endpoints
def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close() 