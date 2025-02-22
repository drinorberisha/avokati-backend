from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from app.core.supabase import supabase
from app.schemas.user import User, UserCreate, Token
from app.core.auth import get_current_user
from app.crud.user import sync_user_to_db
from sqlalchemy.orm import Session
from app.core.database import get_db

router = APIRouter()

@router.post("/register", response_model=User)
async def register(
    *,
    db: Session = Depends(get_db),
    user_in: UserCreate
) -> Any:
    """
    Register new user using Supabase Auth.
    """
    try:
        print(f"Received registration data - role: {user_in.role}")  # Debug print
        
        # Register with Supabase Auth
        auth_response = supabase.auth.sign_up({
            "email": user_in.email,
            "password": user_in.password,
            "options": {
                "data": {
                    "full_name": user_in.full_name,
                    "role": user_in.role.value.lower() if hasattr(user_in.role, 'value') else str(user_in.role).lower(),
                    "phone": user_in.phone
                }
            }
        })
        
        print(f"Supabase auth response - user: {auth_response.user}")  # Debug print
        
        # Sync user to our database
        db_user = sync_user_to_db(db, auth_response.user, user_in)
        return db_user
        
    except Exception as e:
        print(f"Registration error: {str(e)}")  # Debug print
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends()
) -> Any:
    """
    Login using Supabase Auth.
    """
    try:
        auth_response = supabase.auth.sign_in_with_password({
            "email": form_data.username,
            "password": form_data.password
        })
        
        return {
            "access_token": auth_response.session.access_token,
            "token_type": "bearer",
            "refresh_token": auth_response.session.refresh_token
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """
    Logout using Supabase Auth.
    """
    try:
        supabase.auth.sign_out()
        return {"message": "Successfully logged out"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/refresh-token", response_model=Token)
async def refresh_token(refresh_token: str):
    """
    Refresh access token using Supabase Auth.
    """
    try:
        auth_response = supabase.auth.refresh_session({
            "refresh_token": refresh_token
        })
        
        return {
            "access_token": auth_response.session.access_token,
            "token_type": "bearer",
            "refresh_token": auth_response.session.refresh_token
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )

@router.post("/test-token", response_model=User)
def test_token(current_user: User = Depends(get_current_user)) -> Any:
    """
    Test access token.
    """
    return current_user 