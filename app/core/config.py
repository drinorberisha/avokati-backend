from typing import Annotated, List, Optional
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic import AnyHttpUrl, field_validator
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions

class Settings(BaseSettings):
    PROJECT_NAME: str
    VERSION: str
    DESCRIPTION: str
    API_V1_STR: str = "/api/v1"

    # CORS
    # `NoDecode` tells pydantic-settings v2 NOT to try `json.loads()` on the
    # raw env value before our `field_validator(mode="before")` runs. Without
    # this, a `.env` line like `BACKEND_CORS_ORIGINS=http://localhost:3000`
    # (a plain string, not JSON) crashes with a JSONDecodeError before the
    # validator can convert it to a list. Same idea on the other List[str]
    # env-fed fields below.
    BACKEND_CORS_ORIGINS: Annotated[List[str], NoDecode] = [
        "http://localhost:3000",  # React app development
        "http://127.0.0.1:3000",
        "http://localhost:5173",  # Vite fallback/default
        "http://127.0.0.1:5173",
        "http://localhost:8000",  # Backend development
        "http://127.0.0.1:8000",
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
    DB_CONNECT_TIMEOUT: int = 15  # seconds to wait for a single connect (TCP+TLS) before retrying
    DB_INIT_RETRIES: int = 5  # startup connection attempts before degrading to no-DB mode
    SQL_ECHO: bool = False  # Set to True to log SQL queries (development only)

    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    SUPABASE_JWT_SECRET: str
    SUPABASE_SERVICE_ROLE_KEY: str

    # Security
    ACCESS_TOKEN_EXPIRE_SECONDS: int
    REFRESH_TOKEN_EXPIRE_DAYS: int

    # Google Cloud Storage (EU file storage)
    GCS_BUCKET_NAME: str = "avokati-documents-eu"
    GCS_SIGNER_SA: Optional[str] = None   # SA email used for V4 signing (Cloud Run)

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
    MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50MB
    ALLOWED_UPLOAD_TYPES: Annotated[List[str], NoDecode] = [
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
        "application/rtf"
    ]
    
    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"
    CELERY_TASK_SERIALIZER: str = "json"
    CELERY_RESULT_SERIALIZER: str = "json"
    CELERY_ACCEPT_CONTENT: Annotated[List[str], NoDecode] = ["json"]
    CELERY_TIMEZONE: str = "UTC"
    CELERY_TASK_TRACK_STARTED: bool = True
    CELERY_TASK_TIME_LIMIT: int = 30 * 60  # 30 minutes
    CELERY_TASK_SOFT_TIME_LIMIT: int = 25 * 60  # 25 minutes
    CELERY_TASK_MAX_RETRIES: int = 3
    CELERY_TASK_RETRY_DELAY: int = 60  # 1 minute

    # Combined with the `NoDecode` annotations above, this validator is now
    # responsible for parsing list-shaped env values entirely. Operators may
    # use either form interchangeably:
    #   BACKEND_CORS_ORIGINS=http://a,http://b           ← comma-separated
    #   BACKEND_CORS_ORIGINS=["http://a","http://b"]     ← JSON array
    # Without `NoDecode`, the env source tries `json.loads()` before the
    # validator runs and crashes on the comma form. Now we own the parsing.
    @field_validator(
        "BACKEND_CORS_ORIGINS", "ALLOWED_UPLOAD_TYPES", "CELERY_ACCEPT_CONTENT",
        mode="before",
    )
    @classmethod
    def parse_list_field(cls, v):
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                # JSON array form
                import json
                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
            # Comma-separated string form (or single value with no commas)
            return [i.strip() for i in s.split(",") if i.strip()]
        raise ValueError(v)

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",  # tolerate unknown vars in .env (e.g. DEEPSEEK_API_KEY)
    )

settings = Settings()


# Supabase client is built lazily so importing `app.core.config` does NOT
# instantiate a network client. Two reasons:
#   1. The supabase-py `ClientOptions` API changed shape across versions
#      (older versions accepted `postgrest_client_timeout` etc. as kwargs;
#      newer versions expect different attribute names like `storage`).
#      Eager instantiation here was crashing module import for environments
#      whose pinned supabase version doesn't match what the original code
#      assumed.
#   2. AvokAI retrieval / generation does NOT need supabase — only
#      auth/db modules do. Holding off on creating the client until
#      something actually asks for it lets retrieval-only deployments
#      (the eval harness, scripts/build_v2_index.py) skip it entirely.
#
# Callers that need the client should `from app.core.config import
# get_supabase_client` and call it. The instance is memoized.
_supabase_client: Client | None = None


def get_supabase_client() -> Client:
    """Return the lazily-built Supabase client (memoized)."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    # Try the modern kwarg shape first; fall back to bare init if the
    # installed supabase version rejects unknown kwargs.
    try:
        options = ClientOptions(
            postgrest_client_timeout=10,
            storage_client_timeout=10,
            auto_refresh_token=True,
            persist_session=True,
            realtime=dict(eventsPerSecond=10, timeout=60000),
        )
        _supabase_client = create_client(
            supabase_url=settings.SUPABASE_URL,
            supabase_key=settings.SUPABASE_KEY,
            options=options,
        )
    except (TypeError, AttributeError):
        # Older or newer supabase-py whose ClientOptions doesn't accept
        # these kwargs. Use defaults; behavior degrades gracefully.
        _supabase_client = create_client(
            supabase_url=settings.SUPABASE_URL,
            supabase_key=settings.SUPABASE_KEY,
        )
    return _supabase_client


def __getattr__(name: str):
    """Module-level lazy accessor: `from app.core.config import supabase`
    still works for code that already imports the legacy global, but the
    client only spins up the first time it's actually accessed.
    """
    if name == "supabase":
        return get_supabase_client()
    raise AttributeError(f"module 'app.core.config' has no attribute {name!r}") 
