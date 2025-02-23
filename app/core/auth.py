from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, OAuth2PasswordBearer
import jwt
from jose import jwt, JWTError
from app.core.config import settings
from app.schemas.user import User
from app.core.supabase import supabase
from app.crud.user import get_user_by_email
from sqlalchemy.orm import Session
from app.core.database import get_db

security = HTTPBearer()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login")

def verify_jwt(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated"
        )
        return payload
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials"
        )

async def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # Verify the token with Supabase
        try:
            user = supabase.auth.get_user(token)
            if not user:
                raise credentials_exception
        except Exception as e:
            if "timeout" in str(e).lower():
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="Authentication service timeout. Please try again."
                )
            raise credentials_exception
            
        # Get user from our database
        db_user = get_user_by_email(db, email=user.user.email)
        if not db_user:
            raise credentials_exception
            
        return db_user
        
    except JWTError:
        raise credentials_exception

def get_current_active_superuser(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Check if current user is a superuser.
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return current_user 