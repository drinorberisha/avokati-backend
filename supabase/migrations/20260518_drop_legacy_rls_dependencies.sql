-- Remove legacy RLS policies that reference fields removed from the frontend-approved schema.

drop policy if exists "Authorized users can delete cases" on cases;
drop policy if exists "Authorized users can update cases" on cases;
drop policy if exists "Authorized users can view cases" on cases;
drop policy if exists "Primary attorneys can update their cases" on cases;
drop policy if exists "Users can view their assigned cases" on cases;

drop policy if exists "Users can create documents" on documents;
drop policy if exists "Collaborators can delete documents" on documents;
drop policy if exists "Collaborators can insert documents" on documents;
drop policy if exists "Collaborators can update documents" on documents;
drop policy if exists "Collaborators can view documents" on documents;
drop policy if exists "Users can view documents they have access to" on documents;

drop policy if exists "Users can view clients they work with" on clients;
drop policy if exists "Document owners can manage collaborators" on document_collaborators;
drop policy if exists "Users can create and view document versions" on document_versions;

alter table if exists cases
  drop constraint if exists cases_primary_attorney_id_fkey,
  drop column if exists case_number,
  drop column if exists title,
  drop column if exists next_hearing,
  drop column if exists primary_attorney_id,
  drop column if exists created_at,
  drop column if exists updated_at;

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
  drop column if exists collaborators,
  add constraint document_case_or_client
    check (
      (case_id is not null and client_id is null)
      or (case_id is null and client_id is not null)
    );

drop table if exists document_versions cascade;
drop table if exists document_collaborators cascade;
