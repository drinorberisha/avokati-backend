alter table if exists events
  add column if not exists type text not null default 'meeting'
  check (type in ('court', 'meeting', 'deadline'));

create table if not exists case_milestones (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references cases(id) on delete cascade,
  title text not null,
  description text,
  due_date date,
  status text not null default 'not-started' check (status in ('not-started', 'in-progress', 'completed', 'overdue')),
  priority text not null default 'medium' check (priority in ('low', 'medium', 'high'))
);

create index if not exists idx_case_milestones_case_id on case_milestones(case_id);
