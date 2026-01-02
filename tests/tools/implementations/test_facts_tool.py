"""
Tests for FactsTool.

Following MIRA's real testing philosophy:
- No mocks, use real SQLite database
- Test contracts, not implementation
- Verify exact return structures and error messages
- Cover all edge cases identified by contract analysis
"""
import pytest
import json
from datetime import datetime

from tools.implementations.facts_tool import FactsTool
from utils.user_context import set_current_user_id


class TestFactsToolContract:
    """Tests that enforce FactsTool's contract guarantees."""

    @pytest.fixture
    def facts_tool(self):
        """Create FactsTool instance."""
        return FactsTool()

    def test_tool_name_and_schema(self, facts_tool):
        """Verify tool name matches schema name."""
        assert facts_tool.name == "facts_tool"
        assert facts_tool.anthropic_schema["name"] == "facts_tool"

    def test_unknown_operation_raises_valueerror(self, facts_tool, authenticated_user):
        """CONTRACT E1: Unknown operation raises ValueError with specific pattern."""
        with pytest.raises(ValueError, match="Unknown operation:.*Valid operations are:"):
            facts_tool.run("invalid_operation")


class TestAddFactOperation:
    """Tests for add_fact operation."""

    @pytest.fixture
    def facts_tool(self):
        """Create FactsTool instance."""
        return FactsTool()

    def test_add_fact_minimal_fields(self, facts_tool, authenticated_user):
        """CONTRACT R1: Add fact with only required content field."""
        result = facts_tool.run(
            "add_fact",
            content="UPS tracking: 1Z999AA10123456784"
        )

        assert result["success"] is True
        assert "fact" in result
        fact = result["fact"]

        # Verify fact structure
        assert "id" in fact
        assert fact["id"].startswith("fact_")
        assert fact["encrypted__content"] == "UPS tracking: 1Z999AA10123456784"
        assert fact["category"] == "general"  # Default category
        assert fact["list_name"] is None
        assert "created_at" in fact
        assert "updated_at" in fact

        # Verify timestamps are ISO format
        datetime.fromisoformat(fact["created_at"].replace('Z', '+00:00'))
        datetime.fromisoformat(fact["updated_at"].replace('Z', '+00:00'))

        # Verify message
        assert "Added fact to category 'general'" in result["message"]

    def test_add_fact_with_category(self, facts_tool, authenticated_user):
        """CONTRACT R2: Add fact with category."""
        result = facts_tool.run(
            "add_fact",
            content="1Z999AA10123456784",
            category="tracking_number"
        )

        assert result["success"] is True
        assert result["fact"]["category"] == "tracking_number"
        assert "Added fact to category 'tracking_number'" in result["message"]

    def test_add_fact_with_list_name(self, facts_tool, authenticated_user):
        """CONTRACT R3: Add fact with category and list_name."""
        result = facts_tool.run(
            "add_fact",
            content="Buy milk",
            category="todo",
            list_name="grocery_list"
        )

        assert result["success"] is True
        assert result["fact"]["category"] == "todo"
        assert result["fact"]["list_name"] == "grocery_list"
        assert "in list 'grocery_list'" in result["message"]

    def test_add_fact_with_metadata(self, facts_tool, authenticated_user):
        """CONTRACT R4: Add fact with metadata JSON."""
        metadata = json.dumps({"completed": False, "priority": "high"})
        result = facts_tool.run(
            "add_fact",
            content="Call dentist",
            category="todo",
            metadata=metadata
        )

        assert result["success"] is True
        assert "metadata" in result["fact"]
        assert result["fact"]["metadata"]["completed"] is False
        assert result["fact"]["metadata"]["priority"] == "high"

    def test_add_fact_normalizes_category_and_list(self, facts_tool, authenticated_user):
        """CONTRACT R5: Category and list_name are normalized (lowercase, trimmed)."""
        result = facts_tool.run(
            "add_fact",
            content="Test fact",
            category="  TRACKING_NUMBER  ",
            list_name="  Important_List  "
        )

        assert result["fact"]["category"] == "tracking_number"
        assert result["fact"]["list_name"] == "important_list"

    def test_add_fact_empty_content_raises_valueerror(self, facts_tool, authenticated_user):
        """CONTRACT E2: Empty content raises ValueError."""
        with pytest.raises(ValueError, match="Content is required"):
            facts_tool.run("add_fact", content="")

        with pytest.raises(ValueError, match="Content is required"):
            facts_tool.run("add_fact", content="   ")

    def test_add_fact_invalid_metadata_raises_valueerror(self, facts_tool, authenticated_user):
        """CONTRACT E3: Invalid JSON metadata raises ValueError."""
        with pytest.raises(ValueError, match="Invalid JSON in metadata"):
            facts_tool.run(
                "add_fact",
                content="Test fact",
                metadata="not valid json"
            )


class TestGetFactsOperation:
    """Tests for get_facts operation."""

    @pytest.fixture
    def facts_tool(self):
        """Create FactsTool instance with sample data."""
        tool = FactsTool()
        return tool

    @pytest.fixture
    def populated_facts(self, facts_tool, authenticated_user):
        """Create sample facts for testing."""
        # Add various facts
        facts_tool.run("add_fact", content="UPS123", category="tracking_number")
        facts_tool.run("add_fact", content="FedEx456", category="tracking_number")
        facts_tool.run("add_fact", content="#project-alpha", category="hashtag")
        facts_tool.run("add_fact", content="Buy milk", category="todo", list_name="grocery_list")
        facts_tool.run("add_fact", content="Buy eggs", category="todo", list_name="grocery_list")
        facts_tool.run("add_fact", content="Fix bug", category="todo", list_name="work_tasks")
        return facts_tool

    def test_get_all_facts(self, populated_facts, authenticated_user):
        """CONTRACT R6: Get all facts with no filters."""
        result = populated_facts.run("get_facts")

        assert result["success"] is True
        assert result["count"] == 6
        assert len(result["facts"]) == 6
        assert "Found 6 fact(s) for all facts" in result["message"]

    def test_get_facts_by_category(self, populated_facts, authenticated_user):
        """CONTRACT R7: Get facts filtered by category."""
        result = populated_facts.run("get_facts", category="tracking_number")

        assert result["success"] is True
        assert result["count"] == 2
        assert all(f["category"] == "tracking_number" for f in result["facts"])

    def test_get_facts_by_list_name(self, populated_facts, authenticated_user):
        """CONTRACT R8: Get facts filtered by list_name."""
        result = populated_facts.run("get_facts", category="todo", list_name="grocery_list")

        assert result["success"] is True
        assert result["count"] == 2
        assert all(f["list_name"] == "grocery_list" for f in result["facts"])

    def test_get_facts_by_search_text(self, populated_facts, authenticated_user):
        """CONTRACT R9: Get facts containing search text (case-insensitive)."""
        result = populated_facts.run("get_facts", search_text="buy")

        assert result["success"] is True
        assert result["count"] == 2  # "Buy milk" and "Buy eggs"
        assert all("buy" in f["encrypted__content"].lower() for f in result["facts"])

    def test_get_fact_by_id(self, facts_tool, authenticated_user):
        """CONTRACT R10: Get specific fact by ID."""
        # Add a fact
        add_result = facts_tool.run("add_fact", content="Test fact")
        fact_id = add_result["fact"]["id"]

        # Retrieve by ID
        result = facts_tool.run("get_facts", fact_id=fact_id)

        assert result["success"] is True
        assert "fact" in result  # Single fact, not array
        assert result["fact"]["id"] == fact_id

    def test_get_fact_by_invalid_id_returns_not_found(self, facts_tool, authenticated_user):
        """CONTRACT E4: Getting non-existent fact returns success=False."""
        result = facts_tool.run("get_facts", fact_id="fact_nonexistent")

        assert result["success"] is False
        assert "not found" in result["message"].lower()

    def test_get_facts_sorted_newest_first(self, populated_facts, authenticated_user):
        """CONTRACT R11: Facts are sorted by created_at descending (newest first)."""
        result = populated_facts.run("get_facts")

        # Verify descending order
        timestamps = [f["created_at"] for f in result["facts"]]
        assert timestamps == sorted(timestamps, reverse=True)


class TestUpdateFactOperation:
    """Tests for update_fact operation."""

    @pytest.fixture
    def facts_tool(self):
        """Create FactsTool instance."""
        return FactsTool()

    def test_update_fact_content(self, facts_tool, authenticated_user):
        """CONTRACT R12: Update fact content."""
        # Add fact
        add_result = facts_tool.run("add_fact", content="Old content")
        fact_id = add_result["fact"]["id"]

        # Update content
        result = facts_tool.run("update_fact", fact_id=fact_id, content="New content")

        assert result["success"] is True
        assert result["fact"]["encrypted__content"] == "New content"
        assert "content" in result["updated_fields"]

    def test_update_fact_category(self, facts_tool, authenticated_user):
        """CONTRACT R13: Update fact category."""
        add_result = facts_tool.run("add_fact", content="Test")
        fact_id = add_result["fact"]["id"]

        result = facts_tool.run("update_fact", fact_id=fact_id, category="new_category")

        assert result["success"] is True
        assert result["fact"]["category"] == "new_category"
        assert "category" in result["updated_fields"]

    def test_update_fact_metadata(self, facts_tool, authenticated_user):
        """CONTRACT R14: Update fact metadata."""
        add_result = facts_tool.run("add_fact", content="Todo item", category="todo")
        fact_id = add_result["fact"]["id"]

        new_metadata = json.dumps({"completed": True})
        result = facts_tool.run("update_fact", fact_id=fact_id, metadata=new_metadata)

        assert result["success"] is True
        assert result["fact"]["metadata"]["completed"] is True
        assert "metadata" in result["updated_fields"]

    def test_update_fact_no_changes_returns_error(self, facts_tool, authenticated_user):
        """CONTRACT E5: Update with no changes returns success=False."""
        add_result = facts_tool.run("add_fact", content="Test")
        fact_id = add_result["fact"]["id"]

        result = facts_tool.run("update_fact", fact_id=fact_id)

        assert result["success"] is False
        assert "No changes provided" in result["message"]

    def test_update_nonexistent_fact_returns_error(self, facts_tool, authenticated_user):
        """CONTRACT E6: Updating non-existent fact returns success=False."""
        result = facts_tool.run("update_fact", fact_id="fact_nonexistent", content="New")

        assert result["success"] is False
        assert "not found" in result["message"].lower()


class TestDeleteFactOperation:
    """Tests for delete_fact operation."""

    @pytest.fixture
    def facts_tool(self):
        """Create FactsTool instance."""
        return FactsTool()

    def test_delete_fact(self, facts_tool, authenticated_user):
        """CONTRACT R15: Delete fact by ID."""
        # Add fact
        add_result = facts_tool.run("add_fact", content="To be deleted")
        fact_id = add_result["fact"]["id"]

        # Delete
        result = facts_tool.run("delete_fact", fact_id=fact_id)

        assert result["success"] is True
        assert result["deleted_fact"]["id"] == fact_id
        assert "Deleted fact" in result["message"]

        # Verify it's gone
        get_result = facts_tool.run("get_facts", fact_id=fact_id)
        assert get_result["success"] is False

    def test_delete_nonexistent_fact_returns_error(self, facts_tool, authenticated_user):
        """CONTRACT E7: Deleting non-existent fact returns success=False."""
        result = facts_tool.run("delete_fact", fact_id="fact_nonexistent")

        assert result["success"] is False
        assert "not found" in result["message"].lower()

    def test_delete_fact_missing_id_raises_valueerror(self, facts_tool, authenticated_user):
        """CONTRACT E8: Delete without fact_id raises ValueError."""
        with pytest.raises(ValueError, match="fact_id is required"):
            facts_tool.run("delete_fact")


class TestBatchDeleteOperation:
    """Tests for batch_delete operation."""

    @pytest.fixture
    def facts_tool(self):
        """Create FactsTool instance."""
        return FactsTool()

    def test_batch_delete_all_exist(self, facts_tool, authenticated_user):
        """CONTRACT R16: Batch delete multiple facts."""
        # Add facts
        fact1 = facts_tool.run("add_fact", content="Fact 1")["fact"]["id"]
        fact2 = facts_tool.run("add_fact", content="Fact 2")["fact"]["id"]
        fact3 = facts_tool.run("add_fact", content="Fact 3")["fact"]["id"]

        # Batch delete
        result = facts_tool.run("batch_delete", fact_ids=[fact1, fact2, fact3])

        assert result["success"] is True
        assert len(result["succeeded"]) == 3
        assert len(result["not_found"]) == 0
        assert "Deleted 3 of 3 facts" in result["message"]

    def test_batch_delete_some_not_found(self, facts_tool, authenticated_user):
        """CONTRACT R17: Batch delete with some non-existent facts."""
        fact1 = facts_tool.run("add_fact", content="Fact 1")["fact"]["id"]

        result = facts_tool.run("batch_delete", fact_ids=[fact1, "fact_nonexistent"])

        assert result["success"] is False  # Not all succeeded
        assert len(result["succeeded"]) == 1
        assert len(result["not_found"]) == 1
        assert "Deleted 1 of 2 facts" in result["message"]

    def test_batch_delete_empty_array_raises_valueerror(self, facts_tool, authenticated_user):
        """CONTRACT E9: Batch delete with empty array raises ValueError."""
        with pytest.raises(ValueError, match="fact_ids must be a non-empty array"):
            facts_tool.run("batch_delete", fact_ids=[])


class TestListCategoriesOperation:
    """Tests for list_categories operation."""

    @pytest.fixture
    def facts_tool(self):
        """Create FactsTool instance."""
        return FactsTool()

    @pytest.fixture
    def populated_facts(self, facts_tool, authenticated_user):
        """Create sample facts with various categories."""
        facts_tool.run("add_fact", content="UPS123", category="tracking_number")
        facts_tool.run("add_fact", content="FedEx456", category="tracking_number")
        facts_tool.run("add_fact", content="#alpha", category="hashtag")
        facts_tool.run("add_fact", content="Buy milk", category="todo", list_name="groceries")
        facts_tool.run("add_fact", content="Buy eggs", category="todo", list_name="groceries")
        facts_tool.run("add_fact", content="Fix bug", category="todo", list_name="work")
        return facts_tool

    def test_list_categories_with_counts(self, populated_facts, authenticated_user):
        """CONTRACT R18: List all categories with counts."""
        result = populated_facts.run("list_categories")

        assert result["success"] is True
        assert result["total_facts"] == 6

        # Verify categories structure
        categories = {cat["category"]: cat for cat in result["categories"]}
        assert "tracking_number" in categories
        assert "hashtag" in categories
        assert "todo" in categories

        # Verify counts
        assert categories["tracking_number"]["count"] == 2
        assert categories["hashtag"]["count"] == 1
        assert categories["todo"]["count"] == 3

        # Verify lists
        todo_lists = {lst["list_name"]: lst for lst in categories["todo"]["lists"]}
        assert "groceries" in todo_lists
        assert "work" in todo_lists
        assert todo_lists["groceries"]["count"] == 2
        assert todo_lists["work"]["count"] == 1

    def test_list_categories_empty(self, facts_tool, authenticated_user):
        """CONTRACT R19: List categories with no facts."""
        result = facts_tool.run("list_categories")

        assert result["success"] is True
        assert result["total_facts"] == 0
        assert len(result["categories"]) == 0


class TestUserIsolation:
    """Test that facts are properly isolated between users."""

    @pytest.fixture
    def facts_tool(self):
        """Create FactsTool instance."""
        return FactsTool()

    def test_facts_isolated_between_users(self, facts_tool):
        """CONTRACT R20: Facts from different users don't interfere."""
        # User 1
        user1_id = "user1"
        set_current_user_id(user1_id)
        facts_tool.run("add_fact", content="User 1 fact", category="test")

        # User 2
        user2_id = "user2"
        set_current_user_id(user2_id)
        facts_tool.run("add_fact", content="User 2 fact", category="test")

        # User 2 should only see their fact
        result = facts_tool.run("get_facts", category="test")
        assert result["count"] == 1
        assert result["facts"][0]["encrypted__content"] == "User 2 fact"

        # User 1 should only see their fact
        set_current_user_id(user1_id)
        result = facts_tool.run("get_facts", category="test")
        assert result["count"] == 1
        assert result["facts"][0]["encrypted__content"] == "User 1 fact"
