from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import AnyHttpUrl, validator
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions

class Settings(BaseSettings):
    PROJECT_NAME: str
    VERSION: str
    DESCRIPTION: str
    API_V1_STR: str = "/api/v1"
    
    # CORS
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",  # React app development
        "http://localhost:8000",  # Backend development
        "https://avokati.vercel.app",  # Production frontend
    ]

    # Database
    DATABASE_URL: str
    
    # Database connection pool settings
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800  # 30 minutes
    DB_STATEMENT_TIMEOUT: int = 60000  # 60 seconds in milliseconds
    DB_COMMAND_TIMEOUT: int = 60  # 60 seconds
    SQL_ECHO: bool = False  # Set to True to log SQL queries (development only)

    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    SUPABASE_JWT_SECRET: str
    SUPABASE_SERVICE_ROLE_KEY: str

    # Security
    ACCESS_TOKEN_EXPIRE_SECONDS: int
    REFRESH_TOKEN_EXPIRE_DAYS: int

    # AWS S3
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str
    AWS_REGION: str 
    S3_BUCKET_NAME: str
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # Performance
    ENABLE_RESPONSE_COMPRESSION: bool = True
    ENABLE_CACHE: bool = True
    CACHE_TTL_SECONDS: int = 300  # 5 minutes
    
    # AI and Retrieval
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-large"
    
    # Pinecone
    PINECONE_API_KEY: str = ""
    PINECONE_INDEX_NAME: str = "legal-documents"
    PINECONE_NAMESPACE: str = "default"
    PINECONE_CLOUD: str = "aws"
    PINECONE_REGION: str = "us-west-2"
    
    # Legal Document API
    LEGAL_DOCUMENT_API_URL: str = ""
    
    # Upload Directory
    UPLOAD_DIR: str = "uploads"

    @validator("BACKEND_CORS_ORIGINS", pre=True)
    def assemble_cors_origins(cls, v: str | List[str]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()

# Initialize Supabase client with optimized settings
supabase: Client = create_client(
    supabase_url=settings.SUPABASE_URL,
    supabase_key=settings.SUPABASE_KEY,
    options=ClientOptions(
        postgrest_client_timeout=10,
        storage_client_timeout=10,
        auto_refresh_token=True,
        persist_session=True,
        realtime=dict(
            eventsPerSecond=10,
            timeout=60000
        )
    )
) 