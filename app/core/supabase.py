from supabase import create_client, Client
from app.core.config import settings
from supabase.lib.client_options import ClientOptions

def get_supabase_client() -> Client:
    """Service-role client — BYPASSES RLS.

    Use ONLY for: (a) auth/onboarding paths where the caller has no office yet
    (create-office, accept-invite, auth provisioning), and (b) deliberate
    cross-office admin work. For ordinary per-office data access use
    ``get_user_client`` so Postgres RLS — not app-layer discipline — enforces
    tenant isolation. See docs/PRODUCT_ROADMAP.md P1.
    """
    supabase = create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_ROLE_KEY
    )
    return supabase


def get_user_client(access_token: str) -> Client:
    """Per-request client authenticated AS THE CALLING USER → RLS binds.

    Built on the public anon key, then the user's real Supabase access token is
    attached as the PostgREST bearer. PostgREST then runs as ``authenticated``
    with the user's claims, so the existing policies
    (``using (office_id = auth_office_id())``) make the database itself refuse
    cross-office reads/writes. Verified: a real user token sees only its own
    office's rows; service-role sees all.

    NOTE: the project uses asymmetric/rotated JWT signing keys, so we must
    forward the user's REAL token — a server-minted HS256 token (with the legacy
    SUPABASE_JWT_SECRET) is rejected by PostgREST.
    """
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    client.postgrest.auth(access_token)
    return client


# Default service-role instance (used by auth: supabase.auth.get_user(token)).
supabase = get_supabase_client()