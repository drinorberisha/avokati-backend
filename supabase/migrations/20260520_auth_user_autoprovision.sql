-- Auto-provision public.users on auth.users insert.
--
-- Problem before this migration: a Supabase auth signup created a row in
-- auth.users but nothing created the matching row in public.users. The
-- backend's get_current_user looks up by email in public.users and 401s if
-- absent. The frontend useAuth hook was working around this by creating
-- the row from the client on first sign-in — fragile (admin invites,
-- mobile clients, etc. would bypass it).
--
-- Fix: a DB trigger that runs on every insert into auth.users and creates
-- the corresponding public.users row from Supabase user metadata. After
-- this is applied, every Supabase signup automatically has a matching
-- application row.

create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.users (id, email, full_name, role, is_active, hashed_password, is_superuser)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data->>'full_name', null),
    -- role column is the user_role enum; cast from the text we pulled out
    -- of the JSONB metadata. Defaults to 'attorney' if unspecified.
    coalesce(new.raw_user_meta_data->>'role', 'attorney')::user_role,
    true,
    'SUPABASE_AUTH',
    false
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_auth_user();


-- Backfill: create public.users rows for any auth.users that exist today
-- without a matching row (everyone who signed up before this migration).
--
-- We skip BOTH id matches AND email matches. Pre-existing rows where the
-- email matches but the id differs ("id drift") are NOT inserted here —
-- they're handled out-of-band by the reconcile script below, because
-- the email column has a UNIQUE constraint and a plain INSERT would
-- violate it.
--
-- Safe to re-run.
insert into public.users (id, email, full_name, role, is_active, hashed_password, is_superuser)
select
  au.id,
  au.email,
  au.raw_user_meta_data->>'full_name',
  coalesce(au.raw_user_meta_data->>'role', 'attorney')::user_role,
  true,
  'SUPABASE_AUTH',
  false
from auth.users au
where not exists (select 1 from public.users pu where pu.id = au.id)
  and not exists (select 1 from public.users pu where pu.email = au.email);

-- Diagnostic: list any drifted users (email matches auth.users but id
-- does not). After running the backfill, run this to see if there's
-- anything to reconcile. If it returns rows, see reconcile script below.
--
--   select au.id as auth_id, pu.id as public_id, au.email, pu.role
--   from auth.users au join public.users pu on pu.email = au.email
--   where pu.id != au.id;
