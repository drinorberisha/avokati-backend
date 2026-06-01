-- Office document library for the AvokAI "Documents" tab.
--
-- A simple, office-scoped store of uploaded legal documents (file in S3,
-- metadata here). NOT indexed into Pinecone — this is a library, it does not
-- affect AvokAI answers. Mirrors the templates table's tenancy machinery.
--
-- Depends on: offices, set_office_id(), auth_office_id() (office tenancy migrations).
-- Safe to re-run.

create table if not exists library_documents (
  id            uuid primary key default gen_random_uuid(),
  office_id     uuid not null references offices(id) on delete cascade,
  owner_id      uuid references users(id),              -- uploader
  title         text not null,
  document_type text,                                   -- law|regulation|case_law|contract|article|other
  file_name     text,
  file_url      text not null,                          -- S3 object key
  file_size     bigint,
  mime_type     text,
  created_at    timestamptz not null default now()
);

create index if not exists library_documents_office_idx         on library_documents(office_id);
create index if not exists library_documents_office_created_idx on library_documents(office_id, created_at desc);

-- Auto-stamp office_id on direct inserts (defensive; backend stamps it too).
drop trigger if exists set_office_id_trg on public.library_documents;
create trigger set_office_id_trg before insert on public.library_documents
  for each row execute function public.set_office_id();

-- RLS: office isolation.
alter table public.library_documents enable row level security;

drop policy if exists office_select on public.library_documents;
create policy office_select on public.library_documents for select
  using (office_id = public.auth_office_id());

drop policy if exists office_insert on public.library_documents;
create policy office_insert on public.library_documents for insert
  with check (office_id = public.auth_office_id());

drop policy if exists office_update on public.library_documents;
create policy office_update on public.library_documents for update
  using (office_id = public.auth_office_id())
  with check (office_id = public.auth_office_id());

drop policy if exists office_delete on public.library_documents;
create policy office_delete on public.library_documents for delete
  using (office_id = public.auth_office_id());
