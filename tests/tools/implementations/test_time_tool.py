"""
Tests for TimeTool.

Following MIRA's real testing philosophy:
- No mocks, use real timezone utilities
- Test contracts, not implementation
- Verify exact return structures and error messages
- Cover all edge cases identified by contract analysis
"""
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tools.implementations.time_tool import TimeTool
from utils.user_context import set_current_user_id
from utils.timezone_utils import utc_now


class TestTimeToolContract:
    """Tests that enforce TimeTool's contract guarantees."""

    @pytest.fixture
    def time_tool(self):
        """Create TimeTool instance."""
        return TimeTool()

    def test_tool_name_and_schema(self, time_tool):
        """Verify tool name matches schema name."""
        assert time_tool.name == "time_tool"
        assert time_tool.anthropic_schema["name"] == "time_tool"

    def test_full_query_returns_complete_structure(self, time_tool, authenticated_user):
        """Verify full query returns all required fields."""
        result = time_tool.run(query_type="full")

        assert result["success"] is True
        assert "response" in result
        assert "timestamp_utc" in result
        assert "timestamp_local" in result
        assert "timezone" in result

        # Verify timestamps are ISO format
        datetime.fromisoformat(result["timestamp_utc"])
        datetime.fromisoformat(result["timestamp_local"])

    def test_time_only_query(self, time_tool, authenticated_user):
        """Verify time_only query returns time information."""
        result = time_tool.run(query_type="time_only")

        assert result["success"] is True
        assert "response" in result
        assert "It is" in result["response"]

    def test_date_only_query(self, time_tool, authenticated_user):
        """Verify date_only query returns date information."""
        result = time_tool.run(query_type="date_only")

        assert result["success"] is True
        assert "response" in result
        assert "Today is" in result["response"]

    def test_day_only_query(self, time_tool, authenticated_user):
        """Verify day_only query returns day of week."""
        result = time_tool.run(query_type="day_only")

        assert result["success"] is True
        assert "response" in result
        assert "Today is" in result["response"]


class TestRelativeDateCalculation:
    """Tests for relative date calculation functionality."""

    @pytest.fixture
    def time_tool(self):
        """Create TimeTool instance."""
        return TimeTool()

    def test_next_weekday(self, time_tool, authenticated_user):
        """Verify next weekday calculation returns correct structure."""
        result = time_tool.run(query_type="relative_date", relative_expression="next tuesday")

        assert result["success"] is True
        assert "date" in result
        assert "day_of_week" in result
        assert "full_date" in result
        assert "response" in result
        assert result["day_of_week"] == "Tuesday"

        # Verify date is in ISO format
        parsed_date = datetime.fromisoformat(result["date"])
        assert parsed_date > datetime.now()

    def test_tomorrow(self, time_tool, authenticated_user):
        """Verify tomorrow calculation."""
        result = time_tool.run(query_type="relative_date", relative_expression="tomorrow")

        assert result["success"] is True
        assert "date" in result

        # Verify it's actually tomorrow
        expected_date = (utc_now() + timedelta(days=1)).strftime('%Y-%m-%d')
        # Allow for timezone differences
        parsed_date = datetime.fromisoformat(result["date"])
        now = utc_now()
        assert (parsed_date.date() - now.date()).days in [1, 2]

    def test_yesterday(self, time_tool, authenticated_user):
        """Verify yesterday calculation."""
        result = time_tool.run(query_type="relative_date", relative_expression="yesterday")

        assert result["success"] is True
        assert "date" in result

        # Verify it's actually yesterday
        parsed_date = datetime.fromisoformat(result["date"])
        now = utc_now()
        assert (now.date() - parsed_date.date()).days in [0, 1, 2]

    def test_days_from_now(self, time_tool, authenticated_user):
        """Verify N days from now calculation."""
        result = time_tool.run(query_type="relative_date", relative_expression="5 days from now")

        assert result["success"] is True
        assert "date" in result

        parsed_date = datetime.fromisoformat(result["date"])
        now = utc_now()
        days_diff = (parsed_date.date() - now.date()).days
        assert 4 <= days_diff <= 6  # Allow for timezone differences

    def test_weeks_from_now(self, time_tool, authenticated_user):
        """Verify N weeks from now calculation."""
        result = time_tool.run(query_type="relative_date", relative_expression="2 weeks from now")

        assert result["success"] is True
        assert "date" in result

        parsed_date = datetime.fromisoformat(result["date"])
        now = utc_now()
        days_diff = (parsed_date.date() - now.date()).days
        assert 13 <= days_diff <= 15  # Allow for timezone differences

    def test_days_ago(self, time_tool, authenticated_user):
        """Verify N days ago calculation."""
        result = time_tool.run(query_type="relative_date", relative_expression="3 days ago")

        assert result["success"] is True
        assert "date" in result

        parsed_date = datetime.fromisoformat(result["date"])
        now = utc_now()
        days_diff = (now.date() - parsed_date.date()).days
        assert 2 <= days_diff <= 4  # Allow for timezone differences

    def test_next_week(self, time_tool, authenticated_user):
        """Verify next week calculation."""
        result = time_tool.run(query_type="relative_date", relative_expression="next week")

        assert result["success"] is True
        assert "date" in result

    def test_next_month(self, time_tool, authenticated_user):
        """Verify next month calculation."""
        result = time_tool.run(query_type="relative_date", relative_expression="next month")

        assert result["success"] is True
        assert "date" in result

    def test_invalid_expression_returns_error(self, time_tool, authenticated_user):
        """Verify invalid expression returns error with guidance."""
        result = time_tool.run(query_type="relative_date", relative_expression="invalid garbage")

        assert result["success"] is False
        assert "error" in result
        assert "Could not parse" in result["error"]

    def test_missing_expression_returns_error(self, time_tool, authenticated_user):
        """Verify missing expression parameter returns error."""
        result = time_tool.run(query_type="relative_date")

        assert result["success"] is False
        assert "error" in result
        assert "required" in result["error"]

    def test_this_weekday(self, time_tool, authenticated_user):
        """Verify 'this weekday' calculation."""
        result = time_tool.run(query_type="relative_date", relative_expression="this friday")

        assert result["success"] is True
        assert result["day_of_week"] == "Friday"

    def test_abbreviated_weekdays(self, time_tool, authenticated_user):
        """Verify abbreviated weekday names work."""
        result = time_tool.run(query_type="relative_date", relative_expression="next mon")

        assert result["success"] is True
        assert result["day_of_week"] == "Monday"
