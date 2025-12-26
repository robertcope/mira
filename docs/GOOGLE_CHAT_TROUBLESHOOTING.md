# Google Chat Integration Troubleshooting

## Issue: Google Chat doesn't receive MIRA's response

### Symptoms
- tcpdump shows request arriving at `/v0/api/google-chat`
- Local curl test works and returns Card response
- Google Chat client shows no response or times out
- MIRA logs show successful processing

### Root Cause: Reverse Proxy Response Buffering

Google Chat has strict requirements for webhook responses:
- **30 second timeout** - Must respond within 30s or Google gives up
- **HTTP/2 support** - Prefers HTTP/2 connections
- **No buffering** - Response must be flushed immediately

If you're using a reverse proxy (nginx, Cloudflare, etc.), it may be:
1. Buffering the entire response before sending
2. Timing out before MIRA finishes processing
3. Not properly handling HTTP/2

---

## Solution 1: Disable Nginx Response Buffering

If using nginx as reverse proxy, add these directives to disable buffering for the Google Chat endpoint:

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    # ... SSL config ...

    # Google Chat webhook - disable buffering
    location /v0/api/google-chat {
        proxy_pass http://localhost:1993;

        # Disable all buffering for immediate response
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_http_version 1.1;

        # Increase timeout for LLM processing
        proxy_read_timeout 60s;
        proxy_connect_timeout 10s;
        proxy_send_timeout 60s;

        # Forward headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Regular endpoints (can use buffering)
    location / {
        proxy_pass http://localhost:1993;
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

After updating nginx config:
```bash
sudo nginx -t  # Test config
sudo systemctl reload nginx  # Reload without downtime
```

---

## Solution 2: Use Direct HTTPS from Hypercorn

Skip the reverse proxy entirely and expose Hypercorn directly with SSL:

1. **Get SSL certificates** (Let's Encrypt recommended):
```bash
sudo certbot certonly --standalone -d your-domain.com
```

2. **Update Hypercorn config** in `main.py`:
```python
hypercorn_config = Config()
hypercorn_config.bind = ["0.0.0.0:443"]
hypercorn_config.certfile = "/etc/letsencrypt/live/your-domain.com/fullchain.pem"
hypercorn_config.keyfile = "/etc/letsencrypt/live/your-domain.com/privkey.pem"
hypercorn_config.alpn_protocols = ["h2", "http/1.1"]
```

3. **Run as root** (port 443 requires root):
```bash
sudo python main.py
```

---

## Solution 3: Cloudflare Tunnel (Development/Testing)

For quick testing without SSL setup:

1. **Install cloudflared**:
```bash
brew install cloudflared  # macOS
# or
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x cloudflared-linux-amd64
sudo mv cloudflared-linux-amd64 /usr/local/bin/cloudflared
```

2. **Start tunnel**:
```bash
cloudflared tunnel --url http://localhost:1993
```

3. **Use the generated URL** in Google Chat app configuration:
```
https://xyz-abc-123.trycloudflare.com/v0/api/google-chat
```

**Note**: Cloudflare Tunnels reset on restart, use for testing only.

---

## Solution 4: Async Response Pattern (Advanced)

For very long-processing messages, use Google Chat's async response pattern:

1. **Return immediate acknowledgment** (under 30s)
2. **Process in background**
3. **Send response via Chat API** when ready

Implementation sketch:
```python
@router.post("/google-chat")
async def google_chat_webhook(request: Request):
    event_data = await request.json()

    if event_data.get("type") == "MESSAGE":
        # Spawn background task
        asyncio.create_task(process_message_async(event_data))

        # Return immediate acknowledgment
        return JSONResponse(content={"text": "Processing..."})

    # ... rest of handling ...

async def process_message_async(event_data):
    # Process with orchestrator
    response = handler.process_message_event(...)

    # Send response via Google Chat API
    # Requires service account credentials
    await send_to_google_chat(response, space_id, message_id)
```

This requires Google Chat API credentials (service account) to send messages programmatically.

---

## Verification Steps

### 1. Check MIRA Logs
Look for these log lines indicating successful processing:
```
[Google Chat] Received MESSAGE event
[Google Chat] MESSAGE processed in 3421ms, sending response
```

If you see these, MIRA is working correctly.

### 2. Test Response Time Locally
```bash
time curl -X POST http://localhost:1993/v0/api/google-chat \
  -H "Content-Type: application/json" \
  -d '{"type":"MESSAGE","message":{"text":"test"},...}'
```

If response takes >25 seconds, you're close to Google's 30s timeout.

### 3. Test Through Reverse Proxy
```bash
time curl -X POST https://your-domain.com/v0/api/google-chat \
  -H "Content-Type: application/json" \
  -d '{"type":"MESSAGE","message":{"text":"test"},...}'
```

Compare timing with local test. If much slower, proxy is buffering.

### 4. Check Nginx Logs
```bash
tail -f /var/log/nginx/access.log | grep google-chat
tail -f /var/log/nginx/error.log
```

Look for timeout errors or buffering warnings.

### 5. Test from Google Chat
Send a simple message and check:
- MIRA logs (should show processing)
- Nginx logs (should show request/response)
- Google Chat (should show response)

---

## Common Issues

### Issue: 502 Bad Gateway
**Cause**: Nginx can't reach MIRA
**Fix**: Check MIRA is running on correct port
```bash
curl http://localhost:1993/v0/api/health
```

### Issue: 504 Gateway Timeout
**Cause**: MIRA taking too long (>60s default)
**Fix**: Increase `proxy_read_timeout` in nginx config

### Issue: Empty Response in Google Chat
**Cause**: Invalid JSON or Card format
**Fix**: Validate response format matches Google Chat Card spec

### Issue: Response Delayed by 30+ Seconds
**Cause**: Nginx proxy_buffering or MIRA slow processing
**Fix**: Disable buffering (Solution 1) or optimize MIRA

---

## Performance Optimization

If messages consistently take >10 seconds:

1. **Check LLM Provider Latency**:
   - Anthropic API usually responds in 2-5s
   - Check API status: https://status.anthropic.com

2. **Check Database Queries**:
   - Slow queries in continuum_repository?
   - Run `EXPLAIN ANALYZE` on slow queries

3. **Check Memory Extraction**:
   - Is lt_memory batch processing blocking?
   - Check batch queue size

4. **Reduce System Prompt Size**:
   - Working memory trinkets too large?
   - Profile with firehose mode: `python main.py --firehose`

---

## Testing Google Chat Responses

### Valid Card Response
```json
{
  "cardsV2": [{
    "cardId": "mira-response",
    "card": {
      "sections": [{
        "widgets": [{
          "textParagraph": {
            "text": "Response text here"
          }
        }]
      }]
    }
  }]
}
```

### Simple Text Response (for quick acks)
```json
{
  "text": "Message received, processing..."
}
```

### Error Response
```json
{
  "cardsV2": [{
    "cardId": "mira-error",
    "card": {
      "sections": [{
        "widgets": [{
          "textParagraph": {
            "text": "<font color=\"#d93025\">❌ Error message</font>"
          }
        }]
      }]
    }
  }]
}
```

---

## Next Steps

1. **Identify your setup**: Are you using nginx, Cloudflare, direct HTTPS, or something else?
2. **Apply appropriate solution**: Follow Solution 1-4 based on your setup
3. **Test locally first**: Verify `curl` test works before testing with Google Chat
4. **Check logs**: Monitor both MIRA and proxy logs during testing
5. **Measure timing**: Response must be under 30 seconds

If issues persist after trying these solutions, check:
- Google Chat app configuration (webhook URL correct?)
- Firewall rules (port 443 open?)
- DNS resolution (domain resolving correctly?)
- SSL certificate validity (not expired?)
