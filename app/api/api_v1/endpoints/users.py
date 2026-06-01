from typing import List, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.crud import user as user_crud
from app.schemas.user import User, UserCreate, UserUpdate
from app.core.auth import get_current_user

router = APIRouter()

@router.get("/me", response_model=User)
async def read_user_me(current_user: User = Depends(get_current_user)) -> Any:
    """
    Get current user.
    """
    return current_user

# Fields a user may change about themselves. Role / privilege / office
# membership are deliberately excluded — a user cannot escalate their own role
# (only the office owner can change roles, via /offices/me/members).
_SELF_EDITABLE_FIELDS = {"full_name", "phone", "bar_number", "password"}


@router.put("/me", response_model=User)
async def update_user_me(
    user_in: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Update current user's own profile. Role, is_superuser, is_active, email and
    office membership are NOT self-editable and are ignored if sent.
    """
    data = user_in.model_dump(exclude_unset=True)
    safe = {k: v for k, v in data.items() if k in _SELF_EDITABLE_FIELDS}
    user = await user_crud.update_user(db, str(current_user.id), safe)
    return user

@router.get("/{user_id}", response_model=User)
async def read_user_by_id(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Get a specific user by id.
    """
    user = await user_crud.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    if not current_user.is_superuser and current_user.id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return user

@router.get("/", response_model=List[User])
async def read_users(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Retrieve users.
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    users = await user_crud.get_users(db, skip=skip, limit=limit)
    return users

@router.post("/", response_model=User)
async def create_user(
    user_in: UserCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Any:
    """
    Create new user.
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    user = await user_crud.get_user_by_email(db, email=user_in.email)
    if user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    return await user_crud.create_user(db, user_in) 
