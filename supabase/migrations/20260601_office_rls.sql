-- Stage 3 of docs/BUILD_ORDER.md — RLS office isolation + office_id auto-stamp.
--
-- WHY THIS IS REQUIRED (not optional): the frontend reads/writes several tables
-- DIRECTLY via the Supabase JS SDK (anon key + the user's JWT), bypassing the
-- FastAPI backend entirely:
--   * store/index.ts            -> users   (read own row, by id and by email)
--   * pages/clients/new.tsx     -> clients (insert)
--   * pages/cases/new.tsx       -> clients (read dropdown) + cases (insert)
--   * lib/api.ts                -> documents (list / insert / delete)
-- App-layer scoping in FastAPI does NOT cover those paths. Without RLS the
-- cases/new client dropdown would list EVERY office's clients.
--
-- Roles: the frontend uses the `authenticated` role (RLS applies). The backend
-- uses the service role (supabase-py) and the `postgres` role (asyncpg via the
-- pooler) — both BYPASS RLS, so backend behavior is unchanged; the backend keeps
-- its own app-layer office filter from Stage 2.
--
-- Depends on public.auth_office_id() from 20260601_office_tenancy.sql.
-- Apply AFTER the Stage 1 migration + its Default-Office backfill have run.
-- Idempotent: policies/triggers are dropped-if-exists before (re)creating.

-- ─────────────────────────────────────────────────────────────────────────
-- 1. Auto-stamp office_id on direct inserts.
--    SECURITY DEFINER so it can call auth_office_id(). For backend inserts
--    (service role / postgres) auth.uid() is NULL → auth_office_id() is NULL →
--    the office_id the backend already set is left untouched. For direct
--    frontend inserts (authenticated) it fills office_id from the caller's
--    office, so the row can't be mis-stamped or spoofed.
-- ─────────────────────────────────────────────────────────────────────────

create or replace function public.set_office_id()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.office_id is null then
    new.office_id := public.auth_office_id();
  end if;
  return new;
end;
$$;

do $$
declare
  t text;
begin
  foreach t in array array['clients', 'cases', 'documents', 'events', 'invoices', 'case_milestones']
  loop
    execute format('drop trigger if exists set_office_id_trg on public.%I', t);
    execute format(
      'create trigger set_office_id_trg before insert on public.%I
         for each row execute function public.set_office_id()', t);
  end loop;
end $$;

-- ─────────────────────────────────────────────────────────────────────────
-- 2. Enable RLS + office-predicate policies.
-- ─────────────────────────────────────────────────────────────────────────

-- 2a. Tenant data tables: full office isolation.
do $$
declare
  t text;
begin
  foreach t in array array['clients', 'cases', 'documents', 'events', 'invoices', 'case_milestones']
  loop
    execute format('alter table public.%I enable row level security', t);

    execute format('drop policy if exists office_select on public.%I', t);
    execute format(
      'create policy office_select on public.%I for select
         using (office_id = public.auth_office_id())', t);

    execute format('drop policy if exists office_insert on public.%I', t);
    execute format(
      'create policy office_insert on public.%I for insert
         with check (office_id = public.auth_office_id())', t);

    execute format('drop policy if exists office_update on public.%I', t);
    execute format(
      'create policy office_update on public.%I for update
         using (office_id = public.auth_office_id())
         with check (office_id = public.auth_office_id())', t);

    execute format('drop policy if exists office_delete on public.%I', t);
    execute format(
      'create policy office_delete on public.%I for delete
         using (office_id = public.auth_office_id())', t);
  end loop;
end $$;

-- 2b. users: a user may read their own row (by id, or by email for the legacy
--     id-drift case) and their office colleagues; may update only their own row.
alter table public.users enable row level security;

drop policy if exists users_select on public.users;
create policy users_select on public.users for select
  using (
    id = auth.uid()
    or email = (auth.jwt() ->> 'email')
    or office_id = public.auth_office_id()
  );

drop policy if exists users_update_self on public.users;
create policy users_update_self on public.users for update
  using (id = auth.uid())
  with check (id = auth.uid());
-- NB: no INSERT policy for users — provisioning is done by the SECURITY DEFINER
-- trigger handle_new_auth_user() (20260520_auth_user_autoprovision.sql), which
-- bypasses RLS. Office membership changes are done by the backend (service role).

-- 2c. offices: members can read their own office. Create/rename happen through
--     the backend (service role), so no anon insert/update policy is needed.
alter table public.offices enable row level security;

drop policy if exists offices_select on public.offices;
create policy offices_select on public.offices for select
  using (id = public.auth_office_id());

-- 2d. office_invites: admins of the office can read them directly if ever needed;
--     all writes go through the backend (service role).
alter table public.office_invites enable row level security;

drop policy if exists office_invites_select on public.office_invites;
create policy office_invites_select on public.office_invites for select
  using (office_id = public.auth_office_id());

-- ─────────────────────────────────────────────────────────────────────────
-- Verification (run as an authenticated user via the frontend, NOT service role):
--   * cases/new.tsx client dropdown shows only your office's clients
--   * documents list shows only your office's documents
--   * a second office's user sees none of the first office's rows
-- ─────────────────────────────────────────────────────────────────────────
