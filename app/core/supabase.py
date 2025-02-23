from supabase import create_client, Client
from app.core.config import settings
from supabase.lib.client_options import ClientOptions

def get_supabase_client() -> Client:
    # Create Supabase client with default options
    supabase = create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_ROLE_KEY
    )
    return supabase

# Create a default client instance
supabase = get_supabase_client() 