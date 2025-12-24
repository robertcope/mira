# MIRA Web Interface

A web-based chat interface for MIRA that mirrors the CLI experience with real-time WebSocket streaming.

## Features

- **Real-time Streaming**: WebSocket-based streaming responses for instant feedback
- **CLI-Inspired Design**: Matching aesthetics with cyan/magenta color scheme
- **Splash Animation**: ASCII art loading animation matching the CLI
- **Status Bar**: Shows current LLM tier and enabled domaindocs
- **Slash Commands**: Same commands as CLI (`/help`, `/tier`, `/clear`)
- **Responsive Design**: Works on desktop and mobile devices

## Quick Start

1. **Start MIRA server** (if not already running):
   ```bash
   python main.py
   ```

2. **Open web interface**:
   - Navigate to `http://localhost:1993` in your browser
   - The API key will be automatically fetched from the server

3. **Start chatting**:
   - Type your message and press Enter to send
   - Use Shift+Enter for multi-line messages
   - Use `/help` to see available commands

## Architecture

### Backend Components

1. **WebSocket Endpoint** ([cns/api/websocket.py](cns/api/websocket.py:1))
   - Path: `/v0/ws/chat`
   - Authentication: Bearer token (header) or auth message
   - Streaming: Real-time text chunks via WebSocket
   - Message types: `message`, `ping`, `auth`
   - Response types: `text`, `complete`, `error`, `pong`

2. **Web Auth Endpoint** ([cns/api/web_auth.py](cns/api/web_auth.py:1))
   - Path: `/v0/api/auth/key`
   - Returns API key for single-user mode
   - Enables web interface authentication

3. **Static Files** ([static/](static/):1)
   - HTML: [index.html](static/index.html:1)
   - CSS: [style.css](static/css/style.css:1)
   - JavaScript: [app.js](static/js/app.js:1)

### Frontend Components

1. **MIRAClient Class** ([static/js/app.js](static/js/app.js:1))
   - WebSocket connection management
   - Message streaming and rendering
   - Slash command handling
   - Status bar updates
   - Thinking indicator animation

2. **UI Elements**:
   - Splash screen with ASCII animation
   - Message bubbles (user: cyan, assistant: magenta)
   - Status bar (tier + domaindocs)
   - Input area with send button
   - Thinking indicator (`^_^` bouncing face)

## WebSocket Protocol

### Inbound Messages (Client → Server)

```json
{
  "type": "message|ping|auth",
  "content": "user message text",
  "image": "base64_encoded_image",
  "image_type": "image/jpeg",
  "document": "base64_encoded_document",
  "document_type": "application/pdf",
  "token": "api_key_for_auth"
}
```

### Outbound Messages (Server → Client)

**Text Chunk**:
```json
{
  "type": "text",
  "content": "streamed text chunk"
}
```

**Completion**:
```json
{
  "type": "complete",
  "continuum_id": "uuid",
  "metadata": {
    "tools_used": ["tool1", "tool2"],
    "referenced_memories": ["mem1", "mem2"],
    "processing_time_ms": 1234
  }
}
```

**Error**:
```json
{
  "type": "error",
  "message": "error description"
}
```

**Pong** (keepalive response):
```json
{
  "type": "pong"
}
```

## Slash Commands

Same commands as CLI:

- `/help` - Show available commands
- `/tier [fast|balanced|nuanced]` - Get or set LLM tier
- `/clear` - Clear message history
- `/quit`, `/exit`, `/bye` - Exit message (close tab manually)

## Authentication

In single-user OSS mode:
1. Web interface fetches API key from `/v0/api/auth/key`
2. API key stored in localStorage for subsequent sessions
3. WebSocket authenticated via Bearer token

For multi-user deployments:
- Replace `/v0/api/auth/key` with proper OAuth flow
- Implement session-based authentication
- Update WebSocket auth to use session tokens

## Development

### Adding Features

1. **New Message Types**: Update `handleMessage()` in [app.js](static/js/app.js:1)
2. **New Commands**: Add to `handleSlashCommand()` in [app.js](static/js/app.js:1)
3. **UI Changes**: Modify [style.css](static/css/style.css:1)
4. **Protocol Changes**: Update [websocket.py](cns/api/websocket.py:1)

### Testing

Run the WebSocket integration tests:
```bash
pytest tests/api/test_websocket_endpoint.py -v
```

These tests verify:
- Authentication flows (header and message-based)
- Message protocol (ping/pong, text streaming, completion)
- Error handling (empty content, invalid types)
- Image and document attachments
- Metadata in completion messages

## Browser Compatibility

Tested on:
- Chrome/Edge (Chromium)
- Firefox
- Safari

WebSocket support required (all modern browsers).

## Security Notes

**Single-User Mode (OSS)**:
- API key served via `/v0/api/auth/key` endpoint
- Safe for localhost deployment
- No authentication beyond API key validation

**Multi-User Deployments**:
- Remove `/v0/api/auth/key` endpoint
- Implement proper OAuth/session authentication
- Use secure WebSocket (wss://) in production
- Add CORS restrictions for web interface origin
- Implement rate limiting per user

## Troubleshooting

### "Failed to retrieve API key"

1. Check that MIRA is running: `curl http://localhost:1993/v0/api/health`
2. Check console for errors: Open browser DevTools → Console
3. Try manually entering API key when prompted
4. Get API key via CLI: `python talkto_mira.py --show-key`

### "Connection closed. Refresh to reconnect."

1. Server may have restarted - refresh the page
2. Check server logs for errors
3. Verify WebSocket isn't blocked by firewall/proxy

### Messages not streaming

1. Check WebSocket connection in DevTools → Network → WS
2. Verify authentication succeeded (check console)
3. Check for errors in server logs

### Splash screen stuck

1. Wait up to 30 seconds for server startup
2. Check that server is running: `curl http://localhost:1993/v0/api/health`
3. Refresh page after confirming server is up

## Future Enhancements

Potential additions (not yet implemented):

- [ ] Image upload via file picker or drag-and-drop
- [ ] Document upload support
- [ ] Markdown rendering for assistant responses
- [ ] Code syntax highlighting
- [ ] Message editing and regeneration
- [ ] Conversation export (JSON/Markdown)
- [ ] Dark/light theme toggle
- [ ] Mobile app wrapper (Capacitor/React Native)
- [ ] Desktop app wrapper (Electron/Tauri)
- [ ] Multi-user support with proper auth
- [ ] Voice input/output (Web Speech API)
- [ ] Typing indicators
- [ ] Read receipts
- [ ] Message reactions

## Comparison: Web vs CLI

| Feature | Web Interface | CLI (talkto_mira.py) |
|---------|---------------|----------------------|
| Real-time streaming | ✅ WebSocket | ✅ HTTP |
| Slash commands | ✅ | ✅ |
| Status bar | ✅ | ✅ |
| Splash animation | ✅ ASCII | ✅ ASCII |
| Message history | ✅ In-memory | ✅ In-memory |
| Multi-line input | ✅ Shift+Enter | ✅ (prompt_toolkit) |
| Image support | 🚧 (protocol ready) | ❌ |
| Document support | 🚧 (protocol ready) | ❌ |
| Accessibility | ✅ Standard web | ✅ Terminal |
| Offline usage | ❌ Requires server | ✅ Starts server |

## Contributing

When modifying the web interface:

1. Maintain CLI aesthetic (cyan/magenta color scheme)
2. Keep WebSocket protocol compatible with tests
3. Update this documentation
4. Test on multiple browsers
5. Follow existing code patterns

## License

Same license as MIRA (see main README).
