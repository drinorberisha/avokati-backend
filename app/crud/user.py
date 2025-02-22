from typing import Optional, List, Any
from sqlalchemy.orm import Session
from app.db.models import User
from app.schemas.user import UserCreate, UserUpdate, UserRole
from app.core.security import get_password_hash
from app.core.supabase import supabase

def get_user(db: Session, user_id: str) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()

def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """
    Get a user by email from the database.
    """
    return db.query(User).filter(User.email == email).first()

def get_users(db: Session, skip: int = 0, limit: int = 100) -> List[User]:
    return db.query(User).offset(skip).limit(limit).all()

def sync_user_to_db(db: Session, auth_user: Any, user_in: UserCreate = None) -> Optional[User]:
    """
    Sync a Supabase auth user with our database.
    """
    # Check if user exists
    db_user = get_user_by_email(db, email=auth_user.email)
    if db_user:
        return db_user
        
    # Create new user
    user_metadata = getattr(auth_user, 'user_metadata', {}) or {}
    
    # Get role and ensure it's lowercase
    role = None
    if user_in and user_in.role:
        print(f"Original role value: {user_in.role}")  # Debug print
        if isinstance(user_in.role, str):
            role = user_in.role.lower()
        else:
            role = user_in.role.value.lower()
    else:
        role = UserRole.PARALEGAL.value
    
    print(f"Final role value: {role}")  # Debug print
    
    user_data = {
        "email": auth_user.email,
        "full_name": user_in.full_name if user_in else user_metadata.get('full_name'),
        "role": role,
        "is_active": True,
        "is_superuser": False,
        "hashed_password": "SUPABASE_AUTH"  # We don't store actual passwords
    }
    
    print(f"User data being sent to DB: {user_data}")  # Debug print
    
    db_user = User(**user_data)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def create_user(db: Session, user: UserCreate) -> User:
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
    db.commit()
    db.refresh(db_user)
    return db_user

def update_user(db: Session, user_id: str, user: UserUpdate) -> Optional[User]:
    db_user = get_user(db, user_id)
    if not db_user:
        return None
    
    update_data = user.model_dump(exclude_unset=True)
    if "password" in update_data:
        update_data["hashed_password"] = get_password_hash(update_data.pop("password"))
    
    for field, value in update_data.items():
        setattr(db_user, field, value)
    
    db.commit()
    db.refresh(db_user)
    return db_user

def delete_user(db: Session, user_id: str) -> Optional[User]:
    db_user = get_user(db, user_id)
    if not db_user:
        return None
    
    db.delete(db_user)
    db.commit()
    return db_user 