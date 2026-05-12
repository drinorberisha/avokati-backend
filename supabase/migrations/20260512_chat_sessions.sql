-- Chat sessions + messages for AvokAI conversation memory persistence.
--
-- Today the React app keeps chat history in component state — lost on
-- reload. This migration adds durable storage so a lawyer can revisit
-- prior research and follow up across sessions.
--
-- Run via Supabase SQL editor (or `supabase db push` if linked).

CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'Bisedë e re',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Used to sort the sidebar; updated whenever a message is appended.
    last_message_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_sessions_user_id_idx ON chat_sessions(user_id);
CREATE INDEX IF NOT EXISTS chat_sessions_user_last_msg_idx
    ON chat_sessions(user_id, last_message_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,

    -- Assistant-only fields (null for user messages). Stored as JSONB so
    -- we can evolve the shape of SourceCard / citations without DDL.
    intent TEXT,
    sources JSONB,
    citations JSONB,
    abolishment_warnings TEXT[],
    llm_usage JSONB,
    elapsed_ms INTEGER,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_messages_session_created_idx
    ON chat_messages(session_id, created_at);
