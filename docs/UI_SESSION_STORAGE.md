# UI Session Storage

This document describes the UI session storage feature that enables chat display persistence across page reloads.

## Overview

The UI session storage system provides a lightweight mechanism to save and restore the chat interface display state. This is **separate** from the continuum/message history system and is purely for UI convenience.

## Architecture

### Storage Layer
- **Backend**: Valkey (Redis) with 7-day TTL
- **Key Pattern**: `ui_session:{user_id}:chat_display`
- **Data Format**: JSON array of message objects

### Message Structure
```json
{
  "role": "user|assistant|system",
  "content": "message text"
}
```

## API Endpoints

### Save Display State
**Endpoint**: `POST /v0/api/actions`

**Request**:
```json
{
  "domain": "ui_session",
  "action": "save_display",
  "data": {
    "messages": [
      {"role": "user", "content": "Hello"},
      {"role": "assistant", "content": "Hi there!"}
    ]
  }
}
```

**Response**:
```json
{
  "success": true,
  "saved": true,
  "message_count": 2,
  "message": "UI display state saved"
}
```

### Get Display State
**Endpoint**: `POST /v0/api/actions`

**Request**:
```json
{
  "domain": "ui_session",
  "action": "get_display"
}
```

**Response**:
```json
{
  "success": true,
  "messages": [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi there!"}
  ],
  "message_count": 2,
  "message": "UI display state retrieved"
}
```

### Clear Display State
**Endpoint**: `POST /v0/api/actions`

**Request**:
```json
{
  "domain": "ui_session",
  "action": "clear_display"
}
```

**Response**:
```json
{
  "success": true,
  "cleared": true,
  "message": "UI display state cleared"
}
```

## Frontend Integration

### Initialization Flow
1. User loads page
2. API key is fetched
3. Preferences are loaded
4. **UI display state is restored** (new)
5. Chat interface becomes interactive

### Message Flow
1. User sends message
2. Assistant responds
3. Both messages are displayed
4. **Display state is saved to server** (automatic)

### Clear Flow
1. User types `/clear`
2. Local UI is cleared
3. **Server display state is cleared** (automatic)

## Implementation Details

### Backend Handler
- **File**: `cns/api/actions.py`
- **Class**: `UISessionDomainHandler`
- **Domain Type**: `ui_session`

The handler validates message structure:
- Messages must be a list
- Each message must have `role` and `content`
- Valid roles: `user`, `assistant`, `system`

### Frontend Methods
- **File**: `static/js/app.js`
- **Methods**:
  - `loadDisplayState()` - Restores messages on page load
  - `saveDisplayState()` - Saves current display after each interaction
  - `clearServerDisplayState()` - Clears server storage on `/clear` command

### Data Flow
```
User Action          Frontend                Backend (Valkey)
─────────────────────────────────────────────────────────────
Page Load     →      loadDisplayState()  →   GET from Redis
                     ↓ messages
                     Render to DOM

Send Message  →      User + Assistant     →   [Chat API]
                     displayed in UI
                     ↓
                     saveDisplayState()   →   SAVE to Redis
                     Extract from DOM
                     Send to server

/clear        →      clearMessages()      →   DELETE from Redis
                     (clears DOM)
                     ↓
                     clearServerDisplayState()
```

## Separation from Continuum System

**UI Session Storage** is intentionally separate from the **Continuum/Message History** system:

| Feature | UI Session Storage | Continuum System |
|---------|-------------------|------------------|
| Purpose | Display convenience | Conversation management |
| Storage | Valkey (ephemeral) | PostgreSQL (persistent) |
| Scope | Current chat window | Full conversation history |
| Lifetime | 7 days (auto-expires) | Permanent |
| Clear behavior | `/clear` wipes display | Messages remain in history |

This separation ensures:
- UI state doesn't pollute conversation data
- Fast restore on page reload
- Simple clear operation without affecting history
- No risk of corrupting the continuum aggregate

## Cross-Device Behavior

Since the storage is server-side (not localStorage):
- ✅ Same UI state across devices (for the same user)
- ✅ Survives browser cache clears
- ✅ Automatic cleanup after 7 days
- ⚠️ Multiple tabs share the same display state (last save wins)

## Future Enhancements

Possible improvements:
1. **Multiple chat sessions**: Allow users to have multiple named chat windows
2. **Tab-specific state**: Add tab identifier to key pattern for independent displays
3. **Automatic save throttling**: Debounce saves to reduce server calls
4. **Offline queue**: Queue save operations when offline, sync when reconnected
5. **Compressed storage**: Use gzip for large message histories

## Testing

Tests are located in `tests/api/test_ui_session.py` and cover:
- Saving valid display state
- Validation of message structure
- Retrieving stored state
- Handling missing/corrupt data
- Clearing display state

Run tests with:
```bash
pytest tests/api/test_ui_session.py -v
```
