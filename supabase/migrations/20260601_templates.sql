-- Stage 5 of docs/BUILD_ORDER.md — Templates persistence.
--
-- The Templates feature was a UI shell on mock data. This adds the real table,
-- born office-scoped from day one (office_id NOT NULL), with the same RLS +
-- auto-stamp + updated_at machinery as the other tenant tables.
--
-- Depends on: offices table, set_office_id(), update_updated_at_column(),
-- auth_office_id() (all from the 20260601 office tenancy migrations).
-- Safe to re-run.

create table if not exists templates (
  id          uuid primary key default gen_random_uuid(),
  office_id   uuid not null references offices(id) on delete cascade,
  owner_id    uuid references users(id),                 -- creator, for audit
  title       text not null,
  description text,
  category    text,
  language    text,
  status      text not null default 'draft'  check (status in ('draft', 'published', 'archived')),
  content     text not null default '',                  -- HTML with {{variable}} tokens
  variables   jsonb not null default '[]'::jsonb,        -- TemplateVariable[]
  source_type text not null default 'manual' check (source_type in ('manual', 'imported')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index if not exists templates_office_idx         on templates(office_id);
create index if not exists templates_office_updated_idx  on templates(office_id, updated_at desc);

-- Auto-stamp office_id on direct inserts; maintain updated_at on updates.
drop trigger if exists set_office_id_trg on public.templates;
create trigger set_office_id_trg before insert on public.templates
  for each row execute function public.set_office_id();

drop trigger if exists templates_updated_at on public.templates;
create trigger templates_updated_at before update on public.templates
  for each row execute function public.update_updated_at_column();

-- RLS: office isolation (frontend may read templates directly someday; backend
-- bypasses via service role and keeps its own app-layer filter).
alter table public.templates enable row level security;

drop policy if exists office_select on public.templates;
create policy office_select on public.templates for select
  using (office_id = public.auth_office_id());

drop policy if exists office_insert on public.templates;
create policy office_insert on public.templates for insert
  with check (office_id = public.auth_office_id());

drop policy if exists office_update on public.templates;
create policy office_update on public.templates for update
  using (office_id = public.auth_office_id())
  with check (office_id = public.auth_office_id());

drop policy if exists office_delete on public.templates;
create policy office_delete on public.templates for delete
  using (office_id = public.auth_office_id());
