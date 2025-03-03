from typing import Optional, List, Any, Dict, Union
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
import logging
from app.db.models import User
from app.schemas.user import UserCreate, UserUpdate, UserRole
from app.core.security import get_password_hash
from app.core.supabase import supabase

logger = logging.getLogger(__name__)

async def get_user(db: AsyncSession, user_id: str) -> Optional[User]:
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_user: {e}")
        return None

async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    """
    Get a user by email from the database.
    """
    try:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_user_by_email: {e}")
        return None

async def get_users(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[User]:
    try:
        result = await db.execute(select(User).offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        logger.error(f"Database error in get_users: {e}")
        return []

async def sync_user_to_db(db: AsyncSession, auth_user: Any, user_in: UserCreate = None) -> Optional[User]:
    """
    Sync a Supabase auth user with our database.
    """
    try:
        # Check if user exists
        db_user = await get_user_by_email(db, email=auth_user.email)
        if db_user:
            return db_user
            
        # Create new user
        user_metadata = getattr(auth_user, 'user_metadata', {}) or {}
        
        # Get role and ensure it's lowercase
        role = None
        if user_in and user_in.role:
            logger.debug(f"Original role value: {user_in.role}")
            if isinstance(user_in.role, str):
                role = user_in.role.lower()
            else:
                role = user_in.role.value.lower()
        else:
            role = UserRole.paralegal.value
        
        logger.debug(f"Final role value: {role}")
        
        user_data = {
            "email": auth_user.email,
            "full_name": user_in.full_name if user_in else user_metadata.get('full_name'),
            "role": role,
            "is_active": True,
            "is_superuser": False,
            "hashed_password": "SUPABASE_AUTH"  # We don't store actual passwords
        }
        
        logger.debug(f"User data being sent to DB: {user_data}")
        
        db_user = User(**user_data)
        db.add(db_user)
        await db.commit()
        await db.refresh(db_user)
        return db_user
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in sync_user_to_db: {e}")
        return None

async def create_user(db: AsyncSession, user: UserCreate) -> Optional[User]:
    try:
        hashed_password = get_password_hash(user.password)
        db_user = User(
            email=user.email,
            full_name=user.full_name,
            hashed_password=hashed_password,
            role=user.role,
            is_active=user.is_active,
            is_superuser=user.is_superuser
        )
        db.add(db_user)
        await db.commit()
        await db.refresh(db_user)
        return db_user
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in create_user: {e}")
        return None

async def update_user(db: AsyncSession, user_id: str, user: Union[UserUpdate, Dict[str, Any]]) -> Optional[User]:
    try:
        db_user = await get_user(db, user_id)
        if not db_user:
            return None
        
        if isinstance(user, dict):
            update_data = user
        else:
            update_data = user.model_dump(exclude_unset=True)
            
        if "password" in update_data:
            update_data["hashed_password"] = get_password_hash(update_data.pop("password"))
        
        for field, value in update_data.items():
            setattr(db_user, field, value)
        
        await db.commit()
        await db.refresh(db_user)
        return db_user
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in update_user: {e}")
        return None

async def delete_user(db: AsyncSession, user_id: str) -> bool:
    try:
        db_user = await get_user(db, user_id)
        if not db_user:
            return False
        
        await db.delete(db_user)
        await db.commit()
        return True
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error in delete_user: {e}")
        return False 