-- Align app data tables with the frontend-approved model.

alter table if exists users
  add column if not exists bar_number text;

alter table if exists clients
  drop column if exists status,
  drop column if exists updated_at,
  add column if not exists client_since timestamptz default current_timestamp;

update clients
set client_since = coalesce(client_since, created_at, current_timestamp)
where client_since is null;

alter table if exists cases
  drop constraint if exists cases_primary_attorney_id_fkey,
  add column if not exists name text,
  add column if not exists description text;

update cases
set name = coalesce(name, title, case_number)
where name is null;

alter table if exists cases
  drop constraint if exists cases_case_number_key,
  drop constraint if exists cases_primary_attorney_id_fkey,
  drop column if exists case_number,
  drop column if exists title,
  drop column if exists next_hearing,
  drop column if exists primary_attorney_id,
  drop column if exists created_at,
  drop column if exists updated_at;

alter table if exists cases
  alter column name set not null,
  alter column court drop not null,
  alter column judge drop not null;

alter table if exists documents
  add column if not exists name text,
  add column if not exists description text,
  add column if not exists url text;

update documents
set
  name = coalesce(name, title, file_name, 'Document'),
  description = coalesce(description, metadata->>'description'),
  url = coalesce(url, download_url, file_path)
where name is null or url is null;

alter table if exists documents
  drop constraint if exists document_case_or_client,
  drop column if exists title,
  drop column if exists type,
  drop column if exists status,
  drop column if exists size,
  drop column if exists version,
  drop column if exists file_path,
  drop column if exists tags,
  drop column if exists updated_at,
  drop column if exists file_name,
  drop column if exists file_size,
  drop column if exists mime_type,
  drop column if exists download_url,
  drop column if exists created_by,
  drop column if exists metadata,
  drop column if exists collaborators;

alter table if exists documents
  alter column name set not null,
  alter column url set not null,
  add constraint document_case_or_client
    check (
      (case_id is not null and client_id is null)
      or (case_id is null and client_id is not null)
    );

drop table if exists document_versions;
drop table if exists document_collaborators;

create table if not exists events (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  description text,
  time text,
  date_time timestamptz not null
);

create table if not exists invoices (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references clients(id) on delete cascade,
  case_id uuid references cases(id) on delete set null,
  due_date date not null,
  description text not null,
  price double precision not null,
  status text not null default 'draft' check (status in ('draft', 'sent', 'paid', 'overdue'))
);
