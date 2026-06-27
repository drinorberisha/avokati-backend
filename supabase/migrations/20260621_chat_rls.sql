-- Chat tenant isolation via RLS (P1, chat path).
--
-- chat_sessions/chat_messages were isolated only in app code (user_id filter)
-- and served over the SQLAlchemy `postgres` connection, which has BYPASSRLS --
-- so the DB never enforced ownership. We move chat onto the same user-JWT
-- PostgREST path as the rest of the app; these policies make the database
-- itself refuse cross-user access.
--
-- Identity: chat_sessions.user_id = public.users.id, which is NOT always equal
-- to auth.uid() (the Supabase auth id) -- ~1 in 20 users differ. So we resolve
-- the app user the same way auth_office_id() does: by auth.uid() OR the email
-- claim. Keying naively on `user_id = auth.uid()` breaks insert for mismatched
-- users.
--
-- Non-breaking during transition: the legacy `postgres` SQLAlchemy connection
-- still bypasses RLS, so existing chat keeps working until the CRUD is moved
-- to the user-JWT client.

create or replace function public.auth_user_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select id
  from public.users
  where id = auth.uid()
     or email = (auth.jwt() ->> 'email')
  order by (id = auth.uid()) desc
  limit 1
$$;

alter table public.chat_sessions enable row level security;
alter table public.chat_messages enable row level security;

grant select, insert, update, delete on public.chat_sessions to authenticated;
grant select, insert, update, delete on public.chat_messages to authenticated;

drop policy if exists chat_sessions_owner on public.chat_sessions;
create policy chat_sessions_owner on public.chat_sessions
  for all to authenticated
  using (user_id = public.auth_user_id())
  with check (user_id = public.auth_user_id());

drop policy if exists chat_messages_owner on public.chat_messages;
create policy chat_messages_owner on public.chat_messages
  for all to authenticated
  using (exists (
    select 1 from public.chat_sessions s
    where s.id = chat_messages.session_id and s.user_id = public.auth_user_id()
  ))
  with check (exists (
    select 1 from public.chat_sessions s
    where s.id = chat_messages.session_id and s.user_id = public.auth_user_id()
  ));
