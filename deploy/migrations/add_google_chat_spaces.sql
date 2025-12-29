-- Migration: Add google_chat_spaces table for push notification support
-- Date: 2025-12-26
-- Purpose: Store Google Chat space identifiers for proactive messaging

-- Create google_chat_spaces table with RLS
CREATE TABLE IF NOT EXISTS google_chat_spaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    space_name VARCHAR(255) NOT NULL,  -- Google Chat space ID (e.g., 'spaces/AAAAxxxxxxx')
    space_type VARCHAR(50) NOT NULL DEFAULT 'DM',  -- 'DM' or 'ROOM'
    thread_key VARCHAR(255),  -- Optional thread key for threading
    last_message_at TIMESTAMP WITH TIME ZONE,  -- Last message received from this space
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, space_name)  -- One space per user
);

-- Create index for user_id lookups (required for RLS performance)
CREATE INDEX idx_google_chat_spaces_user_id ON google_chat_spaces(user_id);

-- Create index for space_name lookups
CREATE INDEX idx_google_chat_spaces_space_name ON google_chat_spaces(space_name);

-- Enable Row Level Security
ALTER TABLE google_chat_spaces ENABLE ROW LEVEL SECURITY;

-- RLS policy: Users can only access their own spaces
CREATE POLICY google_chat_spaces_user_isolation ON google_chat_spaces
    USING (user_id = current_setting('app.current_user_id', true)::uuid);

-- Grant permissions to application role
GRANT SELECT, INSERT, UPDATE, DELETE ON google_chat_spaces TO mira_dbuser;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mira_dbuser;

-- Add comment for documentation
COMMENT ON TABLE google_chat_spaces IS 'Stores Google Chat space identifiers for each user to enable proactive push notifications (reminders, alerts, etc.)';
