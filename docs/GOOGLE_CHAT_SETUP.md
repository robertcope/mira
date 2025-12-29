# Google Chat Integration Setup Guide

This guide walks you through setting up MIRA as a Google Chat bot using Google Workspace and Google Cloud.

## Architecture Overview

MIRA's Google Chat integration works as an HTTP-based webhook bot:
- Google Chat sends events (messages, mentions) to your MIRA server
- MIRA processes messages through the same orchestrator as the web UI
- Responses are formatted as Google Chat Cards and sent back
- File attachments (images, documents) are automatically downloaded and processed

## Prerequisites

- Google Workspace account (for creating the Chat app)
- Google Cloud Project with billing enabled
- MIRA instance accessible via HTTPS (Google Chat requires HTTPS webhooks)
- Domain with SSL certificate (Let's Encrypt works fine)

## Step 1: Set Up Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project or select an existing one
3. Enable the **Google Chat API**:
   - Navigate to "APIs & Services" → "Library"
   - Search for "Google Chat API"
   - Click "Enable"

## Step 2: Configure Google Chat App

1. Go to [Google Chat API Configuration](https://console.cloud.google.com/apis/api/chat.googleapis.com/hangouts-chat)
2. Click "Configuration" in the left sidebar
3. Fill in the app details:
   - **App name**: MIRA
   - **Avatar URL**: (optional, use your MIRA logo)
   - **Description**: Your AI assistant with persistent memory
4. Configure **Functionality**:
   - ☑ **Receive 1:1 messages**: Enable (allows direct messages)
   - ☑ **Join spaces and group conversations**: Enable (optional, for group chats)
5. Configure **Connection settings**:
   - **App URL**: `https://your-domain.com/v0/api/google-chat`
   - Replace `your-domain.com` with your actual MIRA server domain
   - Must be HTTPS (Google Chat requires SSL)
6. Configure **Visibility**:
   - For testing: Keep "Specific people and groups in your domain"
   - For production: Choose appropriate visibility setting
   - Add your email for testing
7. Click "Save"

## Step 3: Configure MIRA Server

### Expose MIRA via HTTPS

Google Chat requires HTTPS webhooks. Options:

**Option A: Reverse Proxy with Nginx**
```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Option B: Cloudflare Tunnel** (easiest for development)
```bash
cloudflared tunnel --url http://localhost:8000
```

### Verify Webhook Endpoint

Start your MIRA server and verify the endpoint is accessible:
```bash
curl -X POST https://your-domain.com/v0/api/google-chat \
  -H "Content-Type: application/json" \
  -d '{"type":"ADDED_TO_SPACE","space":{"name":"spaces/test"},"user":{"name":"users/test"}}'
```

Expected response:
```json
{"text": "👋 Hi! I'm MIRA, your AI assistant. Send me a message to get started!"}
```

## Step 4: Test the Integration

1. Open Google Chat (web or mobile app)
2. Search for "MIRA" in the chat search
3. Start a direct message
4. Send a test message: "Hello, what can you do?"
5. MIRA should respond with a formatted card message

## Features Supported

### Text Messages
- All text messages are processed through MIRA's orchestrator
- Full access to tools, memory, and working memory
- Responses formatted as Google Chat Cards

### Image Attachments
- Supports JPEG, PNG, GIF, WebP
- Automatically compressed (inference tier: 1200px, storage tier: 512px WebP)
- Images persist across conversation context

### Document Attachments
- Supports PDF, DOCX, XLSX, TXT, CSV, JSON
- PDFs: Sent as base64 document blocks
- Structured data (CSV, XLSX, JSON): Uploaded via Anthropic Files API
- Text extraction for DOCX/TXT files

### Conversation Continuity
- All messages share the same continuum (conversation history)
- Memory extraction works identically to web UI
- Tool usage, segment management, and working memory all function normally

## Troubleshooting

### Webhook Not Receiving Events

1. **Check HTTPS**: Google Chat requires HTTPS webhooks
   ```bash
   curl -I https://your-domain.com/v0/api/google-chat
   ```
   Should return 200/404, not SSL errors

2. **Check MIRA Logs**:
   ```bash
   tail -f mira.log | grep "Google Chat"
   ```

3. **Verify Google Chat Configuration**:
   - App URL must match exactly: `https://your-domain.com/v0/api/google-chat`
   - App must be "Published" or visible to your test user

### Messages Not Processing

1. **Check Single-User Mode**: Ensure MIRA started with valid user
   ```bash
   # Should see in logs:
   # "MIRA Ready - User: user@localhost"
   ```

2. **Check User Lock**: If stuck, release the lock manually:
   ```python
   from utils.distributed_lock import UserRequestLock
   lock = UserRequestLock(ttl=60)
   lock.release("your-user-id")
   ```

3. **Check Orchestrator**: Verify orchestrator is initialized
   ```bash
   # Should see in startup logs:
   # "CNS Orchestrator initialized as global singleton"
   ```

### Attachment Processing Fails

1. **Image Size**: Max 5MB per image
2. **Document Size**: Max size defined in `utils/document_processing.py`
3. **Check Bearer Token**: Google Chat includes token in event payload for attachment downloads

### Response Not Showing in Google Chat

1. **Check Response Format**: Must be valid Google Chat Card JSON
2. **Check Logs**: Look for "Google Chat webhook error" in logs
3. **Verify Card Structure**: Use [Google Chat Card Validator](https://developers.google.com/chat/format-structure)

## Configuration Options

### Enable Metadata Footer

To show tools used, processing time, and memory references in responses:

Edit [cns/api/google_chat.py:253](cns/api/google_chat.py#L253):
```python
return format_response_as_card(
    response_text,
    metadata=metadata,
    include_metadata=True  # Change to True
)
```

### Multi-User Support (Future)

For multi-tenant MIRA deployments, update the user mapping logic:

Edit [cns/api/google_chat.py:293](cns/api/google_chat.py#L293):
```python
# Map Google user email to MIRA user_id
google_user = event_data.get("user", {})
google_email = google_user.get("email")

# Query database to find MIRA user by email
from clients.postgres_client import PostgresClient
db = PostgresClient("mira_service")
result = db.execute_single(
    "SELECT id FROM users WHERE email = %(email)s",
    {"email": google_email}
)
user_id = str(result["id"]) if result else None
if not user_id:
    return JSONResponse(content=format_error_as_card(
        "UNAUTHORIZED",
        "No MIRA account found for this Google Workspace user"
    ))
```

## Security Considerations

### Webhook Signature Verification

For production deployments, verify webhook signatures from Google:

```python
import hmac
import hashlib

def verify_google_chat_signature(request_body: bytes, signature: str, secret: str) -> bool:
    """Verify webhook signature from Google Chat."""
    expected = hmac.new(
        secret.encode(),
        request_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
```

Store webhook secret in Vault:
```bash
vault kv put google_chat/webhook_secret secret="your-secret-from-google"
```

### Rate Limiting

The endpoint inherits MIRA's existing rate limiting via `UserRequestLock` (one concurrent request per user).

For additional rate limiting, add middleware in [main.py](main.py).

## API Reference

### Webhook Endpoint

**POST** `/v0/api/google-chat`

Receives Google Chat webhook events and processes them through MIRA.

**Event Types Handled:**
- `MESSAGE`: User sent a message (processed through orchestrator)
- `ADDED_TO_SPACE`: Bot added to space (responds with greeting)
- `REMOVED_FROM_SPACE`: Bot removed (no response)

**Request Body:**
```json
{
  "type": "MESSAGE",
  "message": {
    "text": "Hello MIRA",
    "sender": {
      "name": "users/123",
      "displayName": "Taylor",
      "email": "taylor@example.com"
    }
  },
  "user": { ... },
  "space": { ... },
  "token": "bearer-token-for-attachments"
}
```

**Response (Success):**
```json
{
  "cardsV2": [{
    "cardId": "mira-response",
    "card": {
      "sections": [{
        "widgets": [{
          "textParagraph": {
            "text": "MIRA's response here"
          }
        }]
      }]
    }
  }]
}
```

**Response (Error):**
```json
{
  "cardsV2": [{
    "cardId": "mira-error",
    "card": {
      "sections": [{
        "widgets": [{
          "textParagraph": {
            "text": "<font color=\"#d93025\">❌ Error message here</font>"
          }
        }]
      }]
    }
  }]
}
```

## Implementation Files

- [cns/api/google_chat.py](../cns/api/google_chat.py) - Webhook handler
- [utils/google_chat_formatter.py](../utils/google_chat_formatter.py) - Response formatter
- [tests/test_google_chat_webhook.py](../tests/test_google_chat_webhook.py) - Test suite

## Resources

- [Google Chat API Documentation](https://developers.google.com/chat)
- [Message Formats and Cards](https://developers.google.com/chat/api/guides/message-formats/cards)
- [Webhook Events Reference](https://developers.google.com/chat/api/guides/message-formats/events)
- [Google Cloud Console](https://console.cloud.google.com)
