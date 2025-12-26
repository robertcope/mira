# Google Chat Push Notifications

This guide explains how to set up proactive push notifications from MIRA to Google Chat, enabling features like reminder notifications, alerts, and system updates sent directly to you without requiring you to initiate a conversation.

## Overview

MIRA's Google Chat integration supports two types of communication:

1. **Webhook-based (already configured)**: Google Chat → MIRA → Response
2. **Push notifications (this guide)**: MIRA → Google Chat (proactive messages)

Push notifications enable MIRA to:
- Send reminder notifications when reminders are due
- Alert you about long-running operations completing
- Notify you of system events
- Send scheduled reports or summaries

## Architecture

```
┌─────────────────┐         ┌──────────────────┐
│  Google Chat    │◄────────│  MIRA Scheduler  │
│  (Your Space)   │         │  (Every 1 min)   │
└─────────────────┘         └──────────────────┘
        ▲                           │
        │                           │
        │ Proactive                 │ Checks due
        │ Messages                  │ reminders
        │                           │
        │                           ▼
┌───────┴──────────┐       ┌──────────────────┐
│ Google Chat API  │       │  Reminder Tool   │
│ (Service Account)│       │  (User SQLite)   │
└──────────────────┘       └──────────────────┘
```

## Prerequisites

- Completed basic Google Chat setup (see [GOOGLE_CHAT_SETUP.md](GOOGLE_CHAT_SETUP.md))
- Google Cloud Project with billing enabled
- Google Workspace admin access (for domain-wide delegation)
- HashiCorp Vault running and configured

## Step 1: Create Service Account

1. Go to [Google Cloud Console → IAM & Admin → Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts)
2. Click **Create Service Account**
3. Configure the service account:
   - **Name**: `mira-chat-bot`
   - **Description**: MIRA Google Chat Bot Service Account
   - Click **Create and Continue**
4. Skip role assignment (not needed for Chat API)
5. Click **Done**

## Step 2: Enable Domain-Wide Delegation

1. Click on the newly created service account
2. Go to **Keys** tab → **Add Key** → **Create new key**
3. Select **JSON** format
4. Click **Create** (downloads JSON file - keep this secure!)
5. Go to **Details** tab
6. Note the **Client ID** (you'll need this for domain-wide delegation)
7. Enable domain-wide delegation:
   - Check **Enable Google Workspace Domain-wide Delegation**
   - Click **Save**

## Step 3: Authorize Service Account in Google Workspace

1. Go to [Google Workspace Admin Console](https://admin.google.com)
2. Navigate to **Security** → **API Controls** → **Domain-wide Delegation**
3. Click **Add new**
4. Configure API client:
   - **Client ID**: Paste the Client ID from Step 2
   - **OAuth Scopes**: `https://www.googleapis.com/auth/chat.bot`
   - Click **Authorize**

## Step 4: Store Credentials in Vault

Store the service account JSON in Vault:

```bash
# Read the service account JSON file
cat /path/to/service-account-key.json

# Store in Vault at the expected path
vault kv put google_chat/service_account @/path/to/service-account-key.json
```

**Important**: The JSON file should contain these fields:
```json
{
  "type": "service_account",
  "project_id": "your-project-id",
  "private_key_id": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...",
  "client_email": "mira-chat-bot@your-project.iam.gserviceaccount.com",
  "client_id": "...",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "..."
}
```

Verify storage:
```bash
vault kv get google_chat/service_account
```

## Step 5: Run Database Migration

Apply the database migration to create the `google_chat_spaces` table:

```bash
psql -U postgres -h localhost -d mira_service -f deploy/migrations/add_google_chat_spaces.sql
```

This creates:
- `google_chat_spaces` table with RLS enabled
- Indexes for efficient queries
- Proper foreign keys to `users` table

## Step 6: Install Python Dependencies

Install the Google Chat API libraries:

```bash
pip install google-auth google-api-python-client
```

Or reinstall all requirements:
```bash
pip install -r requirements.txt
```

## Step 7: Restart MIRA

Restart MIRA to load the new notification system:

```bash
# If running as systemd service
sudo systemctl restart mira

# If running directly
pkill -f "python main.py"
python main.py
```

Check logs to verify the notification job is registered:
```bash
tail -f mira.log | grep "Google Chat"
```

Expected output:
```
Successfully registered Google Chat notification job
```

## Step 8: Test Push Notifications

### Test 1: Send a Message to MIRA via Google Chat

This stores your space identifier:

1. Open Google Chat
2. Send any message to MIRA (e.g., "hello")
3. Check logs:
   ```bash
   tail -f mira.log | grep "Stored Google Chat space"
   ```

Expected: `Stored Google Chat space spaces/AAAAxxxxxxx for user <user_id>`

### Test 2: Create a Test Reminder

Create a reminder due in 2 minutes:

```
Set a reminder: Test push notification
Due: in 2 minutes
```

Wait 2-3 minutes. You should receive a proactive message from MIRA:

```
⏰ **Reminder: Test push notification**

Due: just now
```

## How It Works

### Space Storage

When you send a message to MIRA via Google Chat:
1. Google Chat webhook includes the `space.name` identifier
2. MIRA stores this in `google_chat_spaces` table (linked to your user_id)
3. Your space identifier persists for future push notifications

### Reminder Notifications

Every minute, the scheduler:
1. Queries all active users
2. For each user:
   - Sets user context (accesses their SQLite database)
   - Checks for due reminders (overdue + today)
   - Filters unnotified reminders (`notified_at IS NULL`)
3. If due reminders exist:
   - Looks up user's Google Chat space
   - Formats notification message
   - Sends via Google Chat API
   - Marks reminders as notified

### Idempotency

The system is idempotent:
- Reminders marked `notified_at` won't be sent again
- Multiple scheduler runs won't duplicate notifications
- Missing Google Chat credentials fail gracefully (logs warning, continues)

## Configuration

### Change Notification Frequency

Edit [utils/google_chat_notifier.py:282](utils/google_chat_notifier.py#L282):

```python
# Check every 5 minutes instead of 1 minute
trigger = IntervalTrigger(minutes=5)
```

### Customize Notification Message

Edit `_format_reminder_notification()` in [utils/google_chat_notifier.py:191](utils/google_chat_notifier.py#L191).

### Disable Notifications for Specific Users

Notifications only send if:
1. User has Google Chat space configured (sent at least one message)
2. Reminder is `category='user'` (not internal)
3. Reminder hasn't been notified yet

To disable: Simply don't send messages to MIRA via Google Chat (no space will be stored).

## Troubleshooting

### Notifications Not Sending

**Check 1: Service account credentials**
```bash
vault kv get google_chat/service_account
```

If missing, see Step 4.

**Check 2: Google Chat space stored**
```sql
psql -U postgres -h localhost -d mira_service -c "SELECT * FROM google_chat_spaces;"
```

If empty, send a message to MIRA via Google Chat.

**Check 3: Scheduler running**
```bash
tail -f mira.log | grep "reminder notification"
```

Expected: `Check for due reminders` every minute.

**Check 4: Due reminders exist**

Set user context and check reminders:
```python
from utils.user_context import set_current_user_id
from tools.implementations.reminder_tool import ReminderTool

set_current_user_id("your-user-id")
tool = ReminderTool()
result = tool.run(operation="get_reminders", date_type="overdue", category="user")
print(result)
```

### "Google Chat client not available" Warning

This means service account credentials are missing or invalid:

1. Verify Vault path: `vault kv get google_chat/service_account`
2. Check JSON structure matches Step 4 format
3. Verify service account has domain-wide delegation enabled
4. Check Google Workspace admin authorized the scopes

### Notifications Send But Don't Appear in Chat

**Check 1: Correct space identifier**
```sql
SELECT space_name FROM google_chat_spaces WHERE user_id = 'your-user-id';
```

Should match format: `spaces/AAAAxxxxxxx`

**Check 2: API errors in logs**
```bash
tail -f mira.log | grep "HTTP error sending"
```

Common errors:
- `403 Forbidden`: Service account not authorized in Workspace
- `404 Not Found`: Space identifier is wrong
- `401 Unauthorized`: Credentials invalid

**Check 3: Thread key**

If using threaded conversations, ensure thread_key is stored:
```sql
SELECT thread_key FROM google_chat_spaces WHERE user_id = 'your-user-id';
```

### Multiple Notifications for Same Reminder

Check if `notified_at` is being set:

```python
from utils.user_context import set_current_user_id
from tools.implementations.reminder_tool import ReminderTool

set_current_user_id("your-user-id")
tool = ReminderTool()

# Check specific reminder
result = tool.db.select('reminders', 'id = :id', {'id': 'rem_xxxxxxxx'})
print(result[0].get('notified_at'))  # Should have timestamp after first notification
```

If `notified_at` is NULL after notification, this is a persistence bug.

## Security Considerations

### Service Account Best Practices

- **Rotate keys regularly**: Create new keys every 90 days
- **Limit scope**: Only grant `chat.bot` scope, nothing more
- **Secure storage**: Always use Vault, never hardcode credentials
- **Audit access**: Monitor service account usage in Google Cloud Console

### Data Privacy

- Google Chat space identifiers are stored in PostgreSQL with RLS
- All queries automatically scoped to current user
- Space identifiers are non-sensitive (just routing info)
- Message content is never stored (only sent via API)

### Rate Limiting

Google Chat API limits:
- **60 requests per minute per project**
- **1 request per second per space**

MIRA's scheduler runs every minute, so even with 100 users, you'll stay well under limits.

## Extending Push Notifications

The infrastructure supports any proactive notification, not just reminders:

### Example: Memory Extraction Completion

In [lt_memory/extraction.py](../lt_memory/extraction.py), after extraction completes:

```python
from utils.google_chat_client import get_google_chat_client
from utils.google_chat_spaces_repository import GoogleChatSpacesRepository

# Get user's space
spaces_repo = GoogleChatSpacesRepository()
space_info = spaces_repo.get_space_for_user(user_id)

if space_info:
    # Send notification
    chat_client = get_google_chat_client()
    chat_client.send_message(
        space_name=space_info['space_name'],
        text=f"📊 Memory extraction completed: {entity_count} entities extracted",
        thread_key=space_info.get('thread_key')
    )
```

### Example: System Alerts

```python
def send_system_alert(user_id: str, alert_text: str):
    """Send system alert to user via Google Chat."""
    from utils.google_chat_client import get_google_chat_client
    from utils.google_chat_spaces_repository import GoogleChatSpacesRepository
    from utils.user_context import set_current_user_id

    set_current_user_id(user_id)

    spaces_repo = GoogleChatSpacesRepository()
    space_info = spaces_repo.get_space_for_user(user_id)

    if space_info:
        chat_client = get_google_chat_client()
        chat_client.send_message(
            space_name=space_info['space_name'],
            text=f"⚠️ {alert_text}",
            thread_key=space_info.get('thread_key')
        )
```

## Implementation Files

- [utils/google_chat_client.py](../utils/google_chat_client.py) - Google Chat API client
- [utils/google_chat_notifier.py](../utils/google_chat_notifier.py) - Notification service
- [utils/google_chat_spaces_repository.py](../utils/google_chat_spaces_repository.py) - Space storage
- [cns/api/google_chat.py](../cns/api/google_chat.py) - Webhook handler (stores spaces)
- [deploy/migrations/add_google_chat_spaces.sql](../deploy/migrations/add_google_chat_spaces.sql) - Database schema

## Resources

- [Google Chat API Documentation](https://developers.google.com/chat)
- [Service Account Authentication](https://cloud.google.com/iam/docs/service-accounts)
- [Domain-Wide Delegation](https://developers.google.com/identity/protocols/oauth2/service-account#delegatingauthority)
- [Google Chat API Quotas](https://developers.google.com/chat/api/guides/quotas)
