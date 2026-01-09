# LLM Request/Response Logging

Complete logging system for all LLM interactions in MIRA. Captures requests, responses, and streaming events to structured JSON files for debugging, auditing, and analysis.

## Features

- **Request/Response Correlation**: Each request gets a unique correlation ID that links to its response
- **Organized by Date**: Logs are automatically organized into date-based directories
- **Optional User Scoping**: Can organize logs by user_id for multi-user analysis
- **Streaming Event Capture**: Records streaming events (text deltas, tool calls, etc.) in JSONL format
- **Minimal Performance Impact**: Logging runs asynchronously and fails gracefully
- **Structured JSON**: All logs are valid JSON for easy parsing and analysis

## Configuration

Enable and configure LLM logging via environment variables:

### Environment Variables

```bash
# Enable LLM logging (disabled by default)
export MIRA_LLM_LOGGING=1

# Optional: Custom log directory (default: data/llm_logs)
export MIRA_LLM_LOG_DIR=/path/to/custom/logs

# Optional: Organize logs by user_id (disabled by default)
export MIRA_LLM_LOG_USER_SCOPED=1
```

### Quick Start

```bash
# Enable logging for a single session
MIRA_LLM_LOGGING=1 python main.py

# Enable with custom directory
MIRA_LLM_LOGGING=1 MIRA_LLM_LOG_DIR=/var/log/mira/llm python main.py

# Enable with user-scoped organization
MIRA_LLM_LOGGING=1 MIRA_LLM_LOG_USER_SCOPED=1 python main.py
```

## Log Structure

### Directory Organization

**Non-user-scoped** (default):
```
data/llm_logs/
├── 2026-01-08/
│   ├── 143025_a1b2c3d4_request.json
│   ├── 143025_a1b2c3d4_response.json
│   ├── 143026_e5f6g7h8_request.json
│   └── 143026_e5f6g7h8_response.json
└── 2026-01-09/
    └── ...
```

**User-scoped**:
```
data/llm_logs/
├── 2026-01-08/
│   ├── user_abc123/
│   │   ├── 143025_a1b2c3d4_request.json
│   │   └── 143025_a1b2c3d4_response.json
│   └── user_xyz789/
│       ├── 143026_e5f6g7h8_request.json
│       └── 143026_e5f6g7h8_response.json
└── 2026-01-09/
    └── ...
```

### File Naming Convention

- **Request files**: `HHMMSS_correlationId_request.json`
- **Response files**: `HHMMSS_correlationId_response.json`
- **Streaming files**: `correlationId_streaming.jsonl` (JSONL format, one event per line)

The first 8 characters of the correlation ID are used in filenames for easy matching.

## Request Log Format

```json
{
  "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp": "2026-01-08T14:30:25.123456+00:00",
  "type": "request",
  "provider": "anthropic",
  "model": "claude-sonnet-4.5-20250929",
  "endpoint": null,
  "user_id": "abc123",
  "system_prompt": "You are a helpful assistant.",
  "messages": [
    {
      "role": "user",
      "content": "What is the weather today?"
    }
  ],
  "tools": [
    {
      "name": "get_weather",
      "description": "Get current weather",
      "input_schema": {
        "type": "object",
        "properties": {
          "location": {"type": "string"}
        }
      }
    }
  ],
  "max_tokens": 4096,
  "temperature": 0.1,
  "thinking_enabled": true,
  "thinking_budget": 1024
}
```

## Response Log Format

```json
{
  "correlation_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp": "2026-01-08T14:30:27.456789+00:00",
  "type": "response",
  "user_id": "abc123",
  "error": null,
  "response": {
    "id": "msg_01ABC123",
    "content": [
      {
        "type": "text",
        "text": "I'll check the weather for you."
      },
      {
        "type": "tool_use",
        "id": "toolu_01XYZ789",
        "name": "get_weather",
        "input": {
          "location": "San Francisco"
        }
      }
    ],
    "stop_reason": "tool_use",
    "usage": {
      "input_tokens": 1234,
      "output_tokens": 567,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 890
    }
  }
}
```

## Streaming Event Log Format

Streaming events are recorded in JSONL format (one JSON object per line):

```jsonl
{"correlation_id": "a1b2c3d4", "timestamp": "2026-01-08T14:30:26.100000+00:00", "type": "streaming_event", "event_type": "text_delta", "user_id": "abc123", "event": {"content": "I'll "}}
{"correlation_id": "a1b2c3d4", "timestamp": "2026-01-08T14:30:26.150000+00:00", "type": "streaming_event", "event_type": "text_delta", "user_id": "abc123", "event": {"content": "check "}}
{"correlation_id": "a1b2c3d4", "timestamp": "2026-01-08T14:30:26.200000+00:00", "type": "streaming_event", "event_type": "text_delta", "user_id": "abc123", "event": {"content": "the weather"}}
```

## Programmatic Usage

### Basic Usage

```python
from utils.llm_logger import get_llm_logger

# Get the global logger instance (configured from environment)
logger = get_llm_logger()

# Log a request (returns correlation_id)
correlation_id = logger.log_request(
    provider="anthropic",
    model="claude-sonnet-4.5",
    system_prompt="You are helpful.",
    messages=[{"role": "user", "content": "Hello"}],
    tools=None,
    max_tokens=1024,
    temperature=0.7,
    user_id="user123"
)

# Log the response
logger.log_response(
    correlation_id=correlation_id,
    response_data=response_dict,
    user_id="user123"
)

# Log streaming events
logger.log_streaming_event(
    correlation_id=correlation_id,
    event_type="text_delta",
    event_data={"content": "Hello"},
    user_id="user123"
)
```

### Custom Configuration

```python
from utils.llm_logger import configure_llm_logger

# Configure with custom settings
logger = configure_llm_logger(
    base_dir="/custom/log/path",
    enabled=True,
    user_scoped=True
)
```

## Integration with LLMProvider

The logging is automatically integrated into `LLMProvider`. When `MIRA_LLM_LOGGING=1` is set:

- All requests through `generate_response()` are logged
- All responses (streaming and non-streaming) are logged
- Correlation IDs link request/response pairs
- User context is automatically captured when available

No code changes needed - just enable the environment variable.

## Analysis Examples

### Find All Requests for a User

```bash
# User-scoped logging
ls data/llm_logs/2026-01-08/user_abc123/*_request.json

# Non-user-scoped (grep through all files)
grep -l '"user_id": "abc123"' data/llm_logs/2026-01-08/*_request.json
```

### Extract Token Usage

```bash
# Get token usage from all responses for a day
jq '.response.usage' data/llm_logs/2026-01-08/*_response.json
```

### Find Failed Requests

```bash
# Find requests with errors
jq 'select(.error != null)' data/llm_logs/2026-01-08/*_response.json
```

### Match Request/Response Pairs

```bash
# Find matching files by correlation ID
correlation_id="a1b2c3d4"
ls data/llm_logs/2026-01-08/*${correlation_id:0:8}*
```

### Analyze Streaming Events

```bash
# Count events per request
wc -l data/llm_logs/2026-01-08/*_streaming.jsonl

# Extract specific event types
jq 'select(.event_type == "tool_use")' data/llm_logs/2026-01-08/a1b2c3d4_streaming.jsonl
```

## Performance Considerations

- **Disk I/O**: Logs are written synchronously but kept minimal (JSON only, no binary data)
- **Failure Handling**: Logging failures are logged to application logs but don't interrupt LLM calls
- **Disk Space**: Organize cleanup scripts to archive/delete old logs
- **Production Use**: Consider enabling only for debugging sessions or sampling (e.g., 1% of requests)

## Rotation and Cleanup

Logs are automatically organized by date. Implement cleanup based on your retention policy:

```bash
# Delete logs older than 30 days
find data/llm_logs -type d -name "20*" -mtime +30 -exec rm -rf {} \;

# Archive logs older than 7 days
find data/llm_logs -type d -name "20*" -mtime +7 -exec tar -czf {}.tar.gz {} \; -exec rm -rf {} \;
```

## Comparison with Firehose Mode

MIRA has two logging mechanisms:

| Feature | LLM Logging (`MIRA_LLM_LOGGING`) | Firehose (`MIRA_FIREHOSE`) |
|---------|----------------------------------|----------------------------|
| Purpose | Production debugging & auditing | Development debugging |
| Request logging | ✓ (with correlation IDs) | ✓ (overwrites single file) |
| Response logging | ✓ | ✗ |
| History | ✓ (one file per request) | ✗ (overwrites) |
| Streaming events | ✓ | ✗ |
| User scoping | ✓ (optional) | ✗ |
| Date organization | ✓ | ✗ |
| File format | Structured JSON | Single JSON file |
| Use case | Production, analysis | Quick debugging |

**Recommendation**: Use `MIRA_LLM_LOGGING` for persistent logging and analysis. Use `MIRA_FIREHOSE` only for quick debugging single requests.

## Security Considerations

- **Sensitive Data**: Logs contain full request/response content including system prompts, user messages, and tool arguments
- **Access Control**: Ensure log directories have appropriate file permissions
- **PII/PHI**: Consider data retention policies and encryption for regulated environments
- **API Keys**: API keys are NOT logged, only endpoint URLs
- **User Data**: User IDs are logged for scoping - ensure compliance with privacy policies

## Troubleshooting

### Logs Not Being Created

1. Check environment variable: `echo $MIRA_LLM_LOGGING`
2. Check permissions on log directory: `ls -ld data/llm_logs`
3. Check application logs for errors: `grep "LLM log" logs/*.log`

### Missing Response Logs

- Response logging only occurs after successful LLM API calls
- Check for exceptions in application logs
- Verify correlation_id was generated for the request

### Disk Space Issues

- Implement log rotation (see "Rotation and Cleanup" above)
- Consider sampling in production (log every Nth request)
- Use user-scoped logging to isolate high-volume users

## Future Enhancements

Potential improvements (not yet implemented):

- Sampling configuration (log only N% of requests)
- Async logging for minimal performance impact
- Log compression (gzip individual files)
- Metrics aggregation (token usage, latency, error rates)
- Integration with observability platforms (Datadog, New Relic, etc.)
