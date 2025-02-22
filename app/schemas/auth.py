from pydantic import BaseModel, EmailStr

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenPayload(BaseModel):
    sub: str | None = None
    email: str | None = None

class UserBase(BaseModel):
    email: EmailStr
    full_name: str | None = None
    role: str | None = None

class UserCreate(UserBase):
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserInDB(UserBase):
    id: str
    is_active: bool = True
    is_superuser: bool = False

class User(UserInDB):
    pass 