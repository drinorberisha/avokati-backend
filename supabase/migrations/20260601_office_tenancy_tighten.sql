-- Stage 8 of docs/BUILD_ORDER.md — tighten office tenancy now that every
-- insert path (backend app-layer + the Stage 3 auto-stamp trigger) sets
-- office_id.
--
--   1. Defensive backfill of any stray NULL office_id into the Default Office.
--   2. office_id NOT NULL on every tenant data table.
--   3. Re-scope client email uniqueness from global to per-office, so two
--      different offices can each have a client with the same email.
--
-- Safe to re-run.

-- 1. Defensive: fill any stragglers (expected: none).
do $$
declare
  v_office_id uuid;
begin
  select id into v_office_id
  from public.offices
  where name = 'Default Office'
  order by created_at asc
  limit 1;

  if v_office_id is not null then
    update public.clients         set office_id = v_office_id where office_id is null;
    update public.cases           set office_id = v_office_id where office_id is null;
    update public.documents       set office_id = v_office_id where office_id is null;
    update public.events          set office_id = v_office_id where office_id is null;
    update public.invoices        set office_id = v_office_id where office_id is null;
    update public.case_milestones set office_id = v_office_id where office_id is null;
  end if;
end $$;

-- 2. Enforce presence going forward.
alter table public.clients         alter column office_id set not null;
alter table public.cases           alter column office_id set not null;
alter table public.documents       alter column office_id set not null;
alter table public.events          alter column office_id set not null;
alter table public.invoices        alter column office_id set not null;
alter table public.case_milestones alter column office_id set not null;

-- 3. Per-office client email uniqueness.
--    Drop the old global UNIQUE(email) (this also drops its backing index),
--    then add UNIQUE(office_id, email). The create_client endpoint already
--    checks for duplicate emails scoped to the office.
alter table public.clients drop constraint if exists clients_email_key;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'clients_office_email_key'
  ) then
    alter table public.clients
      add constraint clients_office_email_key unique (office_id, email);
  end if;
end $$;

-- ─────────────────────────────────────────────────────────────────────────
-- Verification (after applying):
--   -- all NOT NULL now:
--   select column_name, is_nullable from information_schema.columns
--     where table_name='clients' and column_name='office_id';   -- NO
--   -- uniqueness scoped to office:
--   select conname, pg_get_constraintdef(oid) from pg_constraint
--     where conrelid='public.clients'::regclass and contype='u';
-- ─────────────────────────────────────────────────────────────────────────
