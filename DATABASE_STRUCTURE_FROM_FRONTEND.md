# Database Structure From Frontend

This document maps the current `frontend/` product structure to the database shape it needs. It is based on the routed pages in `frontend/src/App.tsx`, the API hooks under `frontend/src/hooks/api`, frontend types, and the existing backend models under `app/db/models`.

## Frontend Areas

| Area | Routes | Current data source | Main tables needed |
| --- | --- | --- | --- |
| Dashboard | `/` | `cases` API plus mocked events/bills | `cases`, `calendar_events`, `invoices` |
| Clients | `/clients`, `/clients/:id` | API | `clients`, `cases`, `documents` |
| Cases | `/cases`, `/cases/:id` | API | `cases`, `case_milestones`, `clients`, `users`, `documents` |
| Documents | `/documents` | API | `documents`, `document_versions`, `document_collaborators` |
| Templates | `/templates`, `/templates/:id`, `/templates/:id/edit`, `/templates/:id/generate` | mocked frontend state | `document_templates`, `template_variables`, `generated_documents` |
| Calendar | `/calendar` | mocked frontend state | `calendar_events` |
| Billing | `/billing` | mocked frontend state | `invoices`, `invoice_items`, optionally `payments` |
| AvokAI | `/avokai`, `/avokai/:sessionId` | API | `chat_sessions`, `chat_messages`, `legaldocument` and legal document support tables |
| Settings/Profile/Auth | `/settings`, `/profile`, `/auth/*` | Supabase auth + user profile | `users`, optional `user_preferences` |

## Core Practice Tables

### `users`

Stores internal users and auth profile data.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | Should align with Supabase auth user id where possible. |
| `email` | text unique not null | Login/profile identity. |
| `hashed_password` | text | Existing backend model expects this, but Supabase auth may own password storage. |
| `full_name` | text nullable | Used in auth/register and profile. |
| `phone` | text nullable | Used in register/settings. |
| `role` | enum/text | `attorney`, `paralegal`, `admin`, `client`. |
| `is_active` | boolean | Default true. |
| `is_superuser` | boolean | Default false. |
| `bar_number` | text nullable | Frontend settings expects this; current backend model does not yet include it. |
| `created_at` | timestamptz | Default now. |
| `updated_at` | timestamptz | Default now, update on write. |

Relations:

| Relation | Meaning |
| --- | --- |
| `users.id -> cases.primary_attorney_id` | One attorney can own many cases. |
| `users.id -> document_versions.created_by_id` | One user can create many document versions. |
| `users.id -> document_collaborators.user_id` | Many-to-many user/document collaboration. |
| `users.id -> audit_logs.user_id` | User activity tracking. |
| `users.id -> chat_sessions.user_id` | One user has many private AvokAI sessions. |

### `clients`

Stores people or organizations represented by the practice.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `name` | text not null | Client display name. |
| `email` | text unique not null | Frontend requires it on create. |
| `phone` | text nullable | |
| `address` | text nullable | |
| `status` | enum/text | `active`, `inactive`; default `active`. |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

Relations:

| Relation | Meaning |
| --- | --- |
| `clients.id -> cases.client_id` | One client has many cases. |
| `clients.id -> documents.client_id` | Client-level documents not tied to a case. |

Frontend computed fields:

| Field | Source |
| --- | --- |
| `cases` count | Count of rows in `cases` for the client. |
| `client metrics` | Aggregate from cases, invoices/payments, and activity. |

### `cases`

Stores legal case/matter records.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `case_number` | text unique not null | Shown as `#case_number`. |
| `title` | text not null | |
| `type` | text not null | Frontend options include `civil`, `criminal`, `family`, `corporate`, `administrative`, `labor`, `tax`, `intellectual_property`, `real_estate`, `other`. |
| `status` | enum/text not null | `open`, `pending`, `closed`; default `open`. |
| `court` | text nullable recommended | Current model is not null, frontend treats as optional in some places. |
| `judge` | text nullable recommended | Current model is not null, frontend treats as optional in some places. |
| `description` | text nullable | Frontend sends/displays it; current backend model/schema does not include it yet. |
| `next_hearing` | timestamptz nullable | |
| `client_id` | uuid FK not null | References `clients.id`. |
| `primary_attorney_id` | uuid FK not null | References `users.id`. |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

Relations:

| Relation | Meaning |
| --- | --- |
| `cases.client_id -> clients.id` | Many cases belong to one client. |
| `cases.primary_attorney_id -> users.id` | Many cases assigned to one primary attorney. |
| `cases.id -> case_milestones.case_id` | One case has many milestones. |
| `cases.id -> documents.case_id` | One case has many documents. |
| `cases.id -> calendar_events.case_id` | One case can have hearings, meetings, and deadlines. |
| `cases.id -> invoices.case_id` | One case can have many invoices. |

### `case_milestones`

Needed by `CaseMilestones.tsx`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `case_id` | uuid FK not null | References `cases.id`, cascade delete. |
| `title` | text not null | |
| `description` | text nullable | |
| `due_date` | date nullable | API may expose as `dueDate` or `due_date`; standardize to `due_date` in DB. |
| `status` | enum/text not null | `not-started`, `in-progress`, `completed`, `overdue`; default `not-started`. |
| `priority` | enum/text not null | `low`, `medium`, `high`; default `medium`. |
| `position` | integer nullable | Useful for manual ordering later. |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

Recommended indexes:

| Index | Purpose |
| --- | --- |
| `(case_id, due_date)` | Timeline loading. |
| `(case_id, status)` | Milestone filters/counts. |

## Documents

### `documents`

Stores uploaded practice documents, separate from AI legal corpus documents.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `title` | text not null | |
| `type` or `document_type` | text not null | Frontend type uses `type`; backend schema uses `document_type`; standardize API mapping. |
| `category` | text not null | Frontend labels: `contract`, `court_filing`, `correspondence`, `evidence`, `other`. |
| `status` | enum/text not null | `draft`, `final`, `archived`. |
| `size` | text nullable | Human-readable display. Can be derived from `file_size`. |
| `version` | integer not null | Current version number; default `1`. |
| `file_path` or `file_key` | text not null | Current frontend expects `file_path`, backend versions use `file_key`; standardize naming. |
| `file_name` | text not null | Original file name. |
| `file_size` | integer not null | Bytes. |
| `mime_type` | text not null | |
| `download_url` | text nullable | Usually generated by API/storage, not stored permanently. |
| `tags` | text[] or jsonb | |
| `metadata` | jsonb | Author, description, version history, etc. |
| `case_id` | uuid FK nullable | References `cases.id`. |
| `client_id` | uuid FK nullable | References `clients.id`. |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

Important constraint:

`documents` should belong to exactly one scope: either `case_id` or `client_id`, but not both. If general firm documents are needed later, change this to allow both nullable with a separate `scope` field.

### `document_versions`

Stores uploaded versions for a practice document.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `document_id` | uuid FK not null | References `documents.id`, cascade delete. |
| `version_number` | integer not null | Unique per document. |
| `file_key` / `file_path` | text not null | Storage key/path. |
| `file_name` | text not null | |
| `file_size` | integer not null | |
| `mime_type` | text not null | |
| `changes_description` | text nullable | Frontend type expects this. |
| `created_by_id` | uuid FK not null | References `users.id`. |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

Constraint: unique `(document_id, version_number)`.

### `document_collaborators`

Many-to-many permissions on documents.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `document_id` | uuid FK not null | References `documents.id`. |
| `user_id` | uuid FK not null | References `users.id`. |
| `role` | enum/text not null | `viewer`, `editor`, `owner`. |
| `added_at` | timestamptz | |

Constraint: unique `(document_id, user_id)`.

## Templates

The frontend currently uses mocked templates but routes/components expect templates to be persisted.

### `document_templates`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `title` | text not null | |
| `description` | text nullable | |
| `category` | text not null | Current categories: `Contracts`, `Legal`, `HR`, `Finance`, `Court Filing`. |
| `language` | text not null | Current values: `English`, `Albanian`, `Serbian`. |
| `content` | text not null | HTML/template body with `{{variableName}}` placeholders. |
| `status` | enum/text not null | `draft`, `published`, `archived`. |
| `created_by_id` | uuid FK nullable | References `users.id`. |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

### `template_variables`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `template_id` | uuid FK not null | References `document_templates.id`, cascade delete. |
| `name` | text not null | Must match placeholder name. |
| `type` | enum/text not null | `text`, `number`, `date`, `select`, `boolean`. |
| `required` | boolean not null | |
| `default_value` | text nullable | |
| `options` | text[] or jsonb nullable | For `select`. |
| `description` | text nullable | |
| `position` | integer nullable | |

Constraint: unique `(template_id, name)`.

### `generated_documents`

Optional but useful for saving generated output from templates.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `template_id` | uuid FK not null | References `document_templates.id`. |
| `created_by_id` | uuid FK not null | References `users.id`. |
| `case_id` | uuid FK nullable | References `cases.id`. |
| `client_id` | uuid FK nullable | References `clients.id`. |
| `title` | text not null | |
| `rendered_content` | text not null | Final HTML/text after variable replacement. |
| `variable_values` | jsonb not null | Submitted values. |
| `document_id` | uuid FK nullable | References `documents.id` if saved as a file/document. |
| `created_at` | timestamptz | |

## Calendar

The dashboard and `/calendar` page currently use mocked events. Persist them as first-class records.

### `calendar_events`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `title` | text not null | |
| `type` | enum/text not null | `court`, `meeting`, `deadline`. |
| `starts_at` | timestamptz not null | Combines date and time. |
| `ends_at` | timestamptz nullable | |
| `case_id` | uuid FK nullable | References `cases.id`. |
| `client_id` | uuid FK nullable | References `clients.id`. |
| `location` | text nullable | |
| `notes` | text nullable | |
| `created_by_id` | uuid FK nullable | References `users.id`. |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

Recommended indexes:

| Index | Purpose |
| --- | --- |
| `(starts_at)` | Calendar month/day loading. |
| `(case_id, starts_at)` | Case timeline. |
| `(client_id, starts_at)` | Client timeline. |

## Billing

The `/billing` page currently uses mocked invoices.

### `invoices`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `number` | text unique not null | Example: `INV-2026-001`. |
| `client_id` | uuid FK not null | References `clients.id`. |
| `case_id` | uuid FK nullable | References `cases.id`. |
| `status` | enum/text not null | `draft`, `sent`, `paid`, `overdue`, optionally `void`. |
| `amount` | numeric(12,2) not null | Can be derived from items but stored for reporting. |
| `currency` | text not null | Default `EUR`. |
| `due_date` | date not null | |
| `issued_at` | date nullable | When moved from draft/sent. |
| `paid_at` | timestamptz nullable | |
| `notes` | text nullable | |
| `created_by_id` | uuid FK nullable | References `users.id`. |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

### `invoice_items`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `invoice_id` | uuid FK not null | References `invoices.id`, cascade delete. |
| `description` | text not null | |
| `quantity` | numeric(10,2) not null | |
| `unit_price` | numeric(12,2) not null | |
| `line_total` | numeric(12,2) not null | Can be generated as `quantity * unit_price`. |
| `position` | integer nullable | |

### `payments`

Optional but recommended if invoices can have partial payments.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `invoice_id` | uuid FK not null | References `invoices.id`. |
| `amount` | numeric(12,2) not null | |
| `paid_at` | timestamptz not null | |
| `method` | text nullable | Bank transfer, cash, card, etc. |
| `reference` | text nullable | |
| `notes` | text nullable | |

## AvokAI And Legal Corpus

There are two document domains:

| Domain | Purpose | Tables |
| --- | --- | --- |
| Practice documents | Client/case file management | `documents`, `document_versions`, `document_collaborators` |
| Legal corpus documents | Retrieval-augmented legal AI | `legaldocument`, `legal_document_*`, vector DB IDs |

### `chat_sessions`

Used by `/avokai` and `/avokai/:sessionId`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `user_id` | uuid FK not null | References `users.id`, cascade delete. |
| `title` | text not null | Default `Bisedë e re`. |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |
| `last_message_at` | timestamptz | Used for sidebar sorting. |

### `chat_messages`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `session_id` | uuid FK not null | References `chat_sessions.id`, cascade delete. |
| `role` | enum/text not null | `user`, `assistant`. |
| `content` | text not null | |
| `intent` | text nullable | AvokAI route intent. |
| `sources` | jsonb nullable | Array of source cards. |
| `citations` | jsonb nullable | Array of citation records. |
| `abolishment_warnings` | text[] nullable | |
| `llm_usage` | jsonb nullable | Model/token/cost metadata. |
| `elapsed_ms` | integer nullable | |
| `created_at` | timestamptz | |

### `legaldocument`

Stores legal documents used for AvokAI retrieval.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid/text PK | Current model uses UUID stored as string. |
| `title` | text not null | |
| `content` | text nullable | |
| `document_type` | text not null | Law, regulation, court decision, etc. |
| `status` | text not null | `pending`, `processing`, `processed`, `failed`. |
| `document_metadata` | json/jsonb nullable | Gazette, source URL, publication date, tags, etc. |
| `vector_id` | text nullable | External vector DB id. |
| `is_abolished` | boolean | |
| `is_updated` | boolean | |
| `is_annex` | boolean | |
| `user_id` | uuid/text nullable | Uploader. |
| `file_key` | text nullable | Storage key. |
| `file_name` | text nullable | |
| `file_size` | integer nullable | |
| `mime_type` | text nullable | |
| `version` | integer not null | |
| `parent_document_id` | uuid/text FK nullable | References `legaldocument.id`. |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

Support tables already represented in backend models:

| Table | Purpose |
| --- | --- |
| `legal_document_version` | Version history for legal corpus files. |
| `legal_document_article` | Article-level text and metadata. |
| `legal_document_relationship` | Relationships like amends, abolishes, references. |
| `legal_document_citation` | Citation links between corpus documents. |
| `legal_document_annotation` | Notes/annotations on documents/articles. |
| `legal_document_article_amendment` | Article-level amendment history. |

## Audit And Operational Tables

### `audit_logs`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `user_id` | uuid FK not null | References `users.id`. |
| `action` | text not null | Create/update/delete/login/etc. |
| `entity_type` | text not null | `client`, `case`, `document`, `invoice`, etc. |
| `entity_id` | uuid not null | Polymorphic target id. |
| `changes` | jsonb | Before/after diff. |
| `ip_address` | text nullable | |
| `user_agent` | text nullable | |
| `description` | text nullable | |
| `created_at` | timestamptz | |

### `user_preferences`

Optional table for `/settings` preferences that should survive reloads.

| Column | Type | Notes |
| --- | --- | --- |
| `user_id` | uuid PK/FK | References `users.id`, cascade delete. |
| `language` | text | Frontend currently uses `sq`. |
| `theme` | text nullable | |
| `timezone` | text nullable | |
| `notification_settings` | jsonb | |
| `updated_at` | timestamptz | |

## Relationship Summary

```text
users
  ├─ cases.primary_attorney_id
  ├─ document_versions.created_by_id
  ├─ document_collaborators.user_id
  ├─ chat_sessions.user_id
  ├─ audit_logs.user_id
  ├─ invoices.created_by_id
  └─ calendar_events.created_by_id

clients
  ├─ cases.client_id
  ├─ documents.client_id
  ├─ invoices.client_id
  └─ calendar_events.client_id

cases
  ├─ case_milestones.case_id
  ├─ documents.case_id
  ├─ invoices.case_id
  └─ calendar_events.case_id

documents
  ├─ document_versions.document_id
  ├─ document_collaborators.document_id
  └─ generated_documents.document_id

document_templates
  ├─ template_variables.template_id
  └─ generated_documents.template_id

invoices
  ├─ invoice_items.invoice_id
  └─ payments.invoice_id

chat_sessions
  └─ chat_messages.session_id

legaldocument
  ├─ legal_document_version.document_id
  ├─ legal_document_article.document_id
  ├─ legal_document_relationship.source_document_id / target_document_id
  ├─ legal_document_citation.source_document_id / cited_document_id
  └─ legal_document_annotation.document_id
```

## API/Schema Alignment Notes

These are the main inconsistencies to resolve before treating the schema as final:

| Area | Frontend expects | Current backend/model hint | Recommended fix |
| --- | --- | --- | --- |
| Cases | `description` | `cases` model/schema does not include it | Add nullable `description` to DB/schema/API. |
| Cases | `court`, `judge` can be empty | Current model requires non-null | Either make nullable or enforce required in UI. |
| Documents | `type`, `file_path`, `download_url`, `metadata`, `collaborators` | Backend schema uses `document_type`, `file_key`; download URLs likely generated | Standardize API response to frontend shape or update frontend types. |
| Document versions | `file_path`, `changes_description`, `download_url` | Model has `file_key`, no `changes_description` in `document_versions`; legal corpus version has it | Add/mirror fields or normalize API response. |
| Users/settings | `bar_number`, preferences | Not in current `users` model | Add `bar_number` and optional `user_preferences`. |
| Case milestones | Frontend route exists | The migration files open in IDE were not present at the inspected paths | Add/verify `case_milestones` migration. |
| Templates/calendar/billing | Full UI exists | Mostly mocked in frontend | Add tables and backend endpoints before production use. |

## Suggested Build Order

1. Finalize existing live areas: `users`, `clients`, `cases`, `case_milestones`, `documents`, `document_versions`, `document_collaborators`.
2. Fix naming mismatches in API responses: especially `document_type` vs `type`, `file_key` vs `file_path`.
3. Persist mocked product areas: `calendar_events`, `invoices`, `invoice_items`, `document_templates`, `template_variables`.
4. Keep AvokAI split from practice documents: legal corpus tables should not be mixed with client/case file management.
5. Add audit logging consistently for create/update/delete operations across clients, cases, documents, invoices, templates, and milestones.
