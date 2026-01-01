# TimeTool Natural Language Date Parsing

## Overview

The `time_tool` uses the `parsedatetime` library to handle a wide range of natural language date/time expressions. This eliminates LLM date hallucination by providing precise, timezone-aware date calculations.

## Usage

The tool now supports a `relative_date` query type that accepts natural language expressions:

```python
# Example tool call
tool.run(
    query_type="relative_date",
    relative_expression="next Tuesday"
)

# Returns:
{
    "success": True,
    "date": "2026-01-06",
    "day_of_week": "Tuesday",
    "full_date": "January 06, 2026",
    "response": "'next tuesday' is Tuesday, January 06, 2026 (2026-01-06)",
    "timestamp_local": "2026-01-06T14:30:00-08:00"
}
```

## Supported Expressions

The tool supports a comprehensive range of date/time expressions via `parsedatetime`:

### Relative Dates
- **Weekdays**: `"next monday"`, `"this friday"`, `"next tue"`
- **Simple offsets**: `"tomorrow"`, `"yesterday"`
- **Numeric offsets**: `"3 days from now"`, `"2 weeks ago"`, `"5 hours from now"`
- **Period references**: `"next week"`, `"next month"`, `"next year"`

### Absolute Dates
- **Full dates**: `"August 25th 2024"`, `"25 Aug 2024"`, `"2024-08-25"`
- **With times**: `"Aug 25 5pm"`, `"5pm August 25"`, `"August 25th at 4pm"`
- **Date only**: `"August 25th"`, `"Dec 31"`

### Special Expressions
- **End of periods**: `"eod"` (end of day), `"eom"` (end of month), `"eoy"` (end of year)
- **Combined**: `"tomorrow eod"`, `"eod Tuesday"`
- **Time references**: `"at 4pm"`, `"noon"`, `"midnight"`

### Complex Expressions
- **Relative to times**: `"5 hours before noon"`, `"2 hours after midnight"`
- **Chained references**: `"2 days from tomorrow"`, `"3 weeks from next Monday"`
- **In X minutes**: `"in 5 minutes"`, `"5 minutes from now"`

## Examples

Assuming current date is Thursday, January 1, 2026:

| Expression | Result |
|------------|--------|
| `"next tuesday"` | Tuesday, January 6, 2026 |
| `"next monday"` | Monday, January 5, 2026 |
| `"next thursday"` | Thursday, January 8, 2026 (not today) |
| `"this friday"` | Friday, January 2, 2026 |
| `"this thursday"` | Thursday, January 1, 2026 (today) |
| `"tomorrow"` | Friday, January 2, 2026 |
| `"3 days from now"` | Sunday, January 4, 2026 |
| `"2 weeks from now"` | Thursday, January 15, 2026 |
| `"5 days ago"` | Saturday, December 27, 2025 |

## Use Cases

### Scheduling
When a user says "schedule something for next Tuesday", the LLM can now:
1. Call `time_tool` with `query_type="relative_date"` and `relative_expression="next tuesday"`
2. Receive precise ISO date: `"2026-01-06"`
3. Use that date for calendar/scheduling operations

### Date Context
Users can ask questions like:
- "What's happening next Monday?" → LLM gets precise date to query events
- "Remind me in 3 days" → LLM calculates exact future date
- "What did I do 2 weeks ago?" → LLM calculates precise past date

## Technical Details

### Timezone Awareness
- All calculations respect the user's timezone preference from `get_user_preferences()`
- Dates are calculated in local time, then returned with full timezone information
- ISO timestamps include timezone offset for precision

### Error Handling
Invalid expressions return:
```python
{
    "success": False,
    "error": "Could not parse relative date expression: 'invalid'. Try formats like 'next monday', '3 days from now', 'tomorrow', etc."
}
```

### Algorithm
- **Weekday calculations**: Use modulo arithmetic on `datetime.weekday()` to find target day
- **Relative offsets**: Use `timedelta` for precise day/week arithmetic
- **Month/year approximations**: Use 30-day months and 365-day years (sufficient for scheduling)

## Integration

The tool maintains backward compatibility with existing query types:
- `"full"`: Complete date and time
- `"time_only"`: Just the time
- `"date_only"`: Just the date
- `"day_only"`: Just the day of week
- `"relative_date"`: New relative date calculation

No changes needed to existing code using the tool.
