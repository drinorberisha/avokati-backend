from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, OAuth2PasswordBearer
import jwt
from jose import jwt, JWTError
import logging
from app.core.config import settings
from app.schemas.user import User
from app.core.supabase import supabase
from app.crud.user import get_user_by_email
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db

logger = logging.getLogger(__name__)
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
    db: AsyncSession = Depends(get_db),
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
        except HTTPException:
            raise
        except Exception as e:
            if "timeout" in str(e).lower():
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="Authentication service timeout. Please try again."
                )
            # Only a rejection of the token itself is a credentials failure.
            # gotrue's AuthApiError carries the Supabase HTTP status on
            # `.status`; 400/401/403 mean the token is invalid/expired.
            if getattr(e, "status", None) in (400, 401, 403):
                logger.info(f"Supabase rejected token: {e}")
                raise credentials_exception
            # Anything else (rate limit, 5xx, connection error) is a service
            # failure, NOT an invalid session. Returning 401 here made the
            # frontend sign the user out on transient blips (login/dashboard
            # bounce loop) — return 503 so clients can retry instead.
            logger.error(f"Supabase auth service error: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service temporarily unavailable. Please try again.",
            )
            
        # Get user from our database
        db_user = await get_user_by_email(db, email=user.user.email)
        if not db_user:
            logger.warning(f"User {user.user.email} authenticated but not found in database")
            raise credentials_exception
            
        return db_user
        
    except JWTError as e:
        logger.error(f"JWT error: {e}")
        raise credentials_exception

async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Check if current user is active.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )
    return current_user

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