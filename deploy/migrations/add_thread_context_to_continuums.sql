-- Migration: Add thread_context support to continuums table
-- This enables per-thread conversations for Google Chat while maintaining
-- backward compatibility with non-threaded contexts (CLI, future integrations)

-- Step 1: Add thread_context column (nullable for backward compatibility)
ALTER TABLE continuums
  ADD COLUMN IF NOT EXISTS thread_context VARCHAR(255);

-- Step 2: Drop old UNIQUE constraint on user_id
-- This allows multiple continuums per user (one per thread)
ALTER TABLE continuums
  DROP CONSTRAINT IF EXISTS continuums_user_id_key;

-- Step 3: Create composite unique constraint
-- Allows one continuum per (user_id, thread_context) pair
-- COALESCE handles NULL values so each user can have one default continuum (NULL thread_context)
CREATE UNIQUE INDEX IF NOT EXISTS idx_continuums_user_thread
  ON continuums(user_id, COALESCE(thread_context, ''));

-- Step 4: Add index for thread-aware lookups
-- Partial index only for non-NULL thread_context values
CREATE INDEX IF NOT EXISTS idx_continuums_thread_context
  ON continuums(thread_context)
  WHERE thread_context IS NOT NULL;

-- Note: Existing continuums remain with thread_context=NULL (default continuum)
-- New Google Chat threads will create new continuums with thread_context set
