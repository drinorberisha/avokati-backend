-- Stage 1 of docs/BUILD_ORDER.md — introduce office (multi-tenant) isolation.
--
-- Problem: all app data is globally shared. Every endpoint fetches unscoped
-- and a brand-new signup immediately sees every existing office's clients,
-- cases and documents. This migration introduces an `offices` tenant and a
-- nullable `office_id` on every shared table, then backfills a single
-- "Default Office" so existing data and users keep working unchanged.
--
-- This migration is NON-DESTRUCTIVE and SAFE TO RE-RUN:
--   * all columns/tables use IF NOT EXISTS
--   * the backfill reuses an existing Default Office and only fills NULLs
--   * office_id is added NULLABLE here; a later migration sets NOT NULL once
--     the scoping code (Stage 2/3) is guaranteed to stamp it on every insert.
--
-- Apply via the Supabase SQL editor or psql against the Session-pooler URL.

-- ─────────────────────────────────────────────────────────────────────────
-- 1. Tenant tables
-- ─────────────────────────────────────────────────────────────────────────

create table if not exists offices (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  owner_id    uuid references users(id),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table if not exists office_invites (
  id          uuid primary key default gen_random_uuid(),
  office_id   uuid not null references offices(id) on delete cascade,
  email       text,                                    -- optional: invite a specific address
  token       text not null unique,                    -- random; used in the invite link
  role        text not null default 'member' check (role in ('admin', 'member')),
  invited_by  uuid references users(id),
  expires_at  timestamptz,
  accepted_at timestamptz,                              -- null = pending
  created_at  timestamptz not null default now()
);

create index if not exists office_invites_office_idx on office_invites(office_id);
create index if not exists office_invites_email_idx  on office_invites(email);

-- ─────────────────────────────────────────────────────────────────────────
-- 2. users: office membership + office-level role
--    (office_role is orthogonal to the professional `role` enum)
-- ─────────────────────────────────────────────────────────────────────────

alter table users
  add column if not exists office_id   uuid references offices(id),
  add column if not exists office_role text not null default 'member';

-- guard the office_role domain without failing if the constraint already exists
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'users_office_role_check'
  ) then
    alter table users
      add constraint users_office_role_check
      check (office_role in ('owner', 'admin', 'member'));
  end if;
end $$;

create index if not exists users_office_idx on users(office_id);

-- ─────────────────────────────────────────────────────────────────────────
-- 3. office_id on every shared data table (nullable for now)
-- ─────────────────────────────────────────────────────────────────────────

alter table clients         add column if not exists office_id uuid references offices(id);
alter table cases           add column if not exists office_id uuid references offices(id);
alter table documents       add column if not exists office_id uuid references offices(id);
alter table events          add column if not exists office_id uuid references offices(id);
alter table invoices        add column if not exists office_id uuid references offices(id);
alter table case_milestones add column if not exists office_id uuid references offices(id);

create index if not exists clients_office_idx         on clients(office_id);
create index if not exists cases_office_idx           on cases(office_id);
create index if not exists documents_office_idx       on documents(office_id);
create index if not exists events_office_idx          on events(office_id);
create index if not exists invoices_office_idx        on invoices(office_id);
create index if not exists case_milestones_office_idx on case_milestones(office_id);

-- ─────────────────────────────────────────────────────────────────────────
-- 4. RLS helper: the current request's office, derived from auth.uid().
--    SECURITY DEFINER so it can read public.users without recursing through
--    that table's own RLS policies (added in Stage 3).
-- ─────────────────────────────────────────────────────────────────────────

-- Match by id, falling back to email so the legacy "id drift" account
-- (public.users.id != auth.uid() but email matches; see HANDOFF §6.3) still
-- resolves to its office. Prefer the id match when both exist.
create or replace function public.auth_office_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select office_id
  from public.users
  where id = auth.uid()
     or email = (auth.jwt() ->> 'email')
  order by (id = auth.uid()) desc
  limit 1
$$;

-- ─────────────────────────────────────────────────────────────────────────
-- 4b. Repair a pre-existing trigger that would otherwise block the backfill.
--     Several tables carry a BEFORE UPDATE trigger calling
--     update_updated_at_column(), which does `NEW.updated_at = NOW()`. The May
--     2026 schema refactor (20260517_frontend_functional_schema.sql) DROPPED the
--     `updated_at` column from some of those tables (e.g. clients), so the
--     trigger now fails with: record "new" has no field "updated_at" on any
--     update — including this backfill, and any normal row update from the app.
--     Make the function defensive: only touch updated_at when the column
--     actually exists on the row being modified. Safe for every table.
-- ─────────────────────────────────────────────────────────────────────────

create or replace function public.update_updated_at_column()
returns trigger
language plpgsql
as $$
begin
  if to_jsonb(NEW) ? 'updated_at' then
    NEW.updated_at = NOW();
  end if;
  return NEW;
end;
$$;

-- ─────────────────────────────────────────────────────────────────────────
-- 5. Backfill the Default Office and assign all existing users + rows to it.
--    Idempotent: reuses the existing Default Office and only fills NULLs.
-- ─────────────────────────────────────────────────────────────────────────

do $$
declare
  v_office_id uuid;
  v_owner_id  uuid;
begin
  -- Deterministic owner: a superuser if one exists, else the earliest user.
  select id into v_owner_id
  from public.users
  order by is_superuser desc nulls last, created_at asc nulls last
  limit 1;

  -- Reuse an existing Default Office if this migration already ran.
  select id into v_office_id
  from public.offices
  where name = 'Default Office'
  order by created_at asc
  limit 1;

  if v_office_id is null then
    insert into public.offices (name, owner_id)
    values ('Default Office', v_owner_id)
    returning id into v_office_id;
  end if;

  -- Assign every user that has no office yet.
  update public.users set office_id = v_office_id where office_id is null;

  -- Make the chosen owner the office owner.
  if v_owner_id is not null then
    update public.users set office_role = 'owner' where id = v_owner_id;
  end if;

  -- Assign every existing data row.
  update public.clients         set office_id = v_office_id where office_id is null;
  update public.cases           set office_id = v_office_id where office_id is null;
  update public.documents       set office_id = v_office_id where office_id is null;
  update public.events          set office_id = v_office_id where office_id is null;
  update public.invoices        set office_id = v_office_id where office_id is null;
  update public.case_milestones set office_id = v_office_id where office_id is null;

  raise notice 'Default Office %, owner %', v_office_id, v_owner_id;
end $$;

-- ─────────────────────────────────────────────────────────────────────────
-- Verification (run manually after applying):
--   select count(*) from users          where office_id is null;  -- expect 0
--   select count(*) from clients        where office_id is null;  -- expect 0
--   select count(*) from cases          where office_id is null;  -- expect 0
--   select count(*) from documents      where office_id is null;  -- expect 0
--   select count(*) from events         where office_id is null;  -- expect 0
--   select count(*) from invoices       where office_id is null;  -- expect 0
--   select count(*) from case_milestones where office_id is null; -- expect 0
-- ─────────────────────────────────────────────────────────────────────────
