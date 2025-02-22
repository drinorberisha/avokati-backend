from supabase import create_client
from app.core.config import settings

def get_supabase_client():
    supabase = create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_ROLE_KEY
    )
    return supabase

# Create a default client instance
supabase = get_supabase_client() 