-- Migration: Add notified_at column to reminders table (in user SQLite databases)
-- Date: 2025-12-26
-- Purpose: Track when push notifications have been sent for reminders

-- Note: This migration is for documentation purposes.
-- The reminders table lives in user-specific SQLite databases managed by UserDataManager.
-- The schema update will be applied automatically by the reminder_tool on first access.

-- For reference, the column to be added:
-- ALTER TABLE reminders ADD COLUMN notified_at TEXT;

-- This allows the notification system to track which reminders have already
-- been pushed to Google Chat, preventing duplicate notifications.
