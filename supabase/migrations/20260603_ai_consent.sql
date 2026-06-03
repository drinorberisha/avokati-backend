-- AI-processing consent gate (Compliance Phase 2).
--
-- Records each user's explicit, informed consent to have their AvokAI queries
-- and uploaded documents processed by third-country LLM providers
-- (OpenAI/Gemini — US, DeepSeek — China). Kosovo Law 06/L-082 / GDPR Art. 49
-- informed-consent derogation for cross-border transfers.
--
-- One current row per (user, purpose); `withdrawn_at` set on withdrawal, and a
-- `version` bump (see app/core/consent.py AI_CONSENT_VERSION) forces re-consent
-- when the disclosure text materially changes.
--
-- Depends on: offices, users, set_office_id(), auth_office_id() (office tenancy).
-- Safe to re-run.

create table if not exists consents (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references users(id) on delete cascade,
  office_id    uuid not null references offices(id) on delete cascade,
  purpose      text not null,                         -- e.g. 'ai_processing'
  version      text not null,                         -- disclosure text version consented to
  granted_at   timestamptz not null default now(),
  withdrawn_at timestamptz,                            -- null = currently granted
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (user_id, purpose)
);

create index if not exists consents_user_purpose_idx on consents(user_id, purpose);
create index if not exists consents_office_idx        on consents(office_id);

-- Auto-stamp office_id on direct inserts (defensive; backend stamps it too).
drop trigger if exists set_office_id_trg on public.consents;
create trigger set_office_id_trg before insert on public.consents
  for each row execute function public.set_office_id();

-- Keep updated_at fresh.
drop trigger if exists set_updated_at_trg on public.consents;
create trigger set_updated_at_trg before update on public.consents
  for each row execute function public.update_updated_at_column();

-- RLS: office isolation (parallel guard for the frontend's anon-key reads;
-- the backend uses the service-role key and filters by user_id in app code).
alter table public.consents enable row level security;

drop policy if exists office_select on public.consents;
create policy office_select on public.consents for select
  using (office_id = public.auth_office_id());

drop policy if exists office_insert on public.consents;
create policy office_insert on public.consents for insert
  with check (office_id = public.auth_office_id());

drop policy if exists office_update on public.consents;
create policy office_update on public.consents for update
  using (office_id = public.auth_office_id())
  with check (office_id = public.auth_office_id());

drop policy if exists office_delete on public.consents;
create policy office_delete on public.consents for delete
  using (office_id = public.auth_office_id());
