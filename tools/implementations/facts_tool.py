"""
Facts tool for storing and retrieving discrete facts and list items.

This tool provides simple storage for explicit facts the user wants to remember:
tracking numbers, hashtags, todo items, lists, bookmarks, etc. Unlike LT_Memory's
semantic search, this tool guarantees retrieval of explicitly stored facts.
"""

import json
import logging
import uuid
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field

from tools.repo import Tool
from tools.registry import registry
from utils.timezone_utils import utc_now, format_utc_iso


class FactsToolConfig(BaseModel):
    """Configuration for the facts_tool."""
    enabled: bool = Field(default=True, description="Whether this tool is enabled by default")


# Register with registry
registry.register("facts_tool", FactsToolConfig)


class FactsTool(Tool):
    """
    Store and retrieve discrete facts and list items.

    This tool handles explicit facts that need guaranteed retrieval:
    - Tracking numbers (UPS, FedEx, etc.)
    - Thread hashtags used
    - Todo lists
    - Book recommendations
    - Gift ideas
    - Any discrete fact the user explicitly wants stored
    """

    name = "facts_tool"

    simple_description = "Store and retrieve discrete facts, tracking numbers, hashtags, todo lists, and other explicit information that needs guaranteed retrieval"

    anthropic_schema = {
        "name": "facts_tool",
        "description": """Manages discrete facts and list items with guaranteed retrieval.

Use this tool for explicit facts the user wants to remember:
- Tracking numbers (UPS, FedEx, Amazon orders)
- Thread hashtags they've used
- Todo lists (grocery lists, shopping lists, task lists)
- Book/movie/gift recommendations
- Reference numbers, account numbers, confirmation codes
- Any discrete fact that needs guaranteed retrieval (not semantic search)

OPERATIONS:

• add_fact - Store a new fact or list item
  Required: content
  Optional: category, list_name, metadata (JSON string for extensible fields)

• get_facts - Query facts by category, list, or search
  Optional: category, list_name, search_text, fact_id
  Returns matching facts

• update_fact - Modify an existing fact
  Required: fact_id
  Optional: content, category, list_name, metadata

• delete_fact - Remove a fact
  Required: fact_id

• list_categories - Show all categories and lists in use
  Returns categories/lists with counts

• batch_delete - Delete multiple facts at once
  Required: fact_ids (array of fact IDs)

CATEGORIES:
Use descriptive categories: tracking_number, hashtag, todo, book_recommendation,
gift_idea, confirmation_code, or any custom category that fits the data.

LIST NAMES:
Optional grouping within a category. Examples:
- category=todo, list_name=grocery_list
- category=todo, list_name=work_tasks
- category=book_recommendation, list_name=2024_reading

METADATA:
JSON string for category-specific fields:
- {"completed": true} for todos
- {"url": "https://..."} for bookmarks
- {"priority": "high"} for prioritized items
- Any other structured data""",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["add_fact", "get_facts", "update_fact", "delete_fact", "list_categories", "batch_delete"],
                    "description": "The operation to perform"
                },
                "content": {
                    "type": "string",
                    "description": "The fact content/text (required for add_fact, optional for update_fact)"
                },
                "category": {
                    "type": "string",
                    "description": "Category for organizing facts (e.g., tracking_number, hashtag, todo, book_recommendation, gift_idea). Default: 'general'"
                },
                "list_name": {
                    "type": "string",
                    "description": "Optional list name for grouping facts within a category (e.g., 'grocery_list', 'work_tasks')"
                },
                "metadata": {
                    "type": "string",
                    "description": "JSON string with additional structured data (e.g., '{\"completed\": true}', '{\"url\": \"https://...\"}', '{\"priority\": \"high\"}')"
                },
                "fact_id": {
                    "type": "string",
                    "description": "ID of the fact (required for update_fact, delete_fact, optional for get_facts)"
                },
                "search_text": {
                    "type": "string",
                    "description": "Search for facts containing this text (optional for get_facts)"
                },
                "fact_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Array of fact IDs for batch operations (required for batch_delete)"
                }
            },
            "required": ["operation"],
            "additionalProperties": False
        }
    }

    def __init__(self):
        """Initialize the facts tool with SQLite storage."""
        super().__init__()
        self.logger = logging.getLogger(__name__)

        # Only create tables if user context is available
        from utils.user_context import has_user_context
        if has_user_context():
            self._ensure_facts_table()

    def _ensure_facts_table(self):
        """Create facts table if it doesn't exist."""
        schema = """
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL DEFAULT 'general',
            list_name TEXT,
            encrypted__content TEXT NOT NULL,
            encrypted__metadata TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        """
        self.db.create_table('facts', schema)

        # Create indexes for faster queries
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_facts_list ON facts(list_name)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_facts_created ON facts(created_at)")

    def run(self, operation: str, **kwargs) -> Dict[str, Any]:
        """
        Execute a facts tool operation.

        Args:
            operation: Operation to perform
            **kwargs: Parameters for the specific operation

        Returns:
            Response data for the operation

        Raises:
            ValueError: If operation fails or parameters are invalid
        """
        try:
            # Ensure facts table exists on first use
            self._ensure_facts_table()

            # Parse kwargs JSON string if provided that way
            if "kwargs" in kwargs and isinstance(kwargs["kwargs"], str):
                try:
                    params = json.loads(kwargs["kwargs"])
                    kwargs = params
                except json.JSONDecodeError as e:
                    self.logger.error(f"Invalid JSON in kwargs for {operation}: {e}")
                    raise ValueError(f"Invalid JSON in kwargs: {e}")

            # Route to the appropriate operation
            if operation == "add_fact":
                return self._add_fact(**kwargs)
            elif operation == "get_facts":
                return self._get_facts(**kwargs)
            elif operation == "update_fact":
                return self._update_fact(**kwargs)
            elif operation == "delete_fact":
                return self._delete_fact(**kwargs)
            elif operation == "list_categories":
                return self._list_categories(**kwargs)
            elif operation == "batch_delete":
                return self._batch_delete(**kwargs)
            else:
                self.logger.error(f"Unknown operation: {operation}")
                raise ValueError(
                    f"Unknown operation: {operation}. Valid operations are: "
                    "add_fact, get_facts, update_fact, delete_fact, list_categories, batch_delete"
                )
        except Exception as e:
            self.logger.error(f"Error executing {operation} in facts_tool: {e}")
            raise

    def _add_fact(
        self,
        content: str,
        category: str = "general",
        list_name: Optional[str] = None,
        metadata: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Add a new fact.

        Args:
            content: The fact content/text
            category: Category for organizing (default: "general")
            list_name: Optional list name for grouping
            metadata: Optional JSON string with additional data

        Returns:
            Dict containing the created fact

        Raises:
            ValueError: If required fields are missing or invalid
        """
        if not content or not isinstance(content, str) or not content.strip():
            self.logger.error(f"Invalid content provided: {repr(content)}")
            raise ValueError("Content is required and must be a non-empty string")

        # Validate metadata if provided
        parsed_metadata = None
        if metadata:
            try:
                parsed_metadata = json.loads(metadata)
                if not isinstance(parsed_metadata, dict):
                    raise ValueError("Metadata must be a JSON object")
            except json.JSONDecodeError as e:
                self.logger.error(f"Invalid JSON in metadata: {e}")
                raise ValueError(f"Invalid JSON in metadata: {e}")

        # Generate unique ID
        fact_id = f"fact_{uuid.uuid4().hex[:8]}"
        timestamp = format_utc_iso(utc_now())

        fact_data = {
            'id': fact_id,
            'category': category.strip().lower(),
            'list_name': list_name.strip().lower() if list_name else None,
            'encrypted__content': content.strip(),
            'encrypted__metadata': metadata,  # Store as JSON string
            'created_at': timestamp,
            'updated_at': timestamp
        }

        # Insert into database
        self.db.insert('facts', fact_data)

        # Return formatted response
        response_fact = {
            "id": fact_id,
            "category": fact_data['category'],
            "list_name": fact_data['list_name'],
            "encrypted__content": content.strip(),
            "created_at": timestamp,
            "updated_at": timestamp
        }

        if parsed_metadata:
            response_fact["metadata"] = parsed_metadata

        message = f"Added fact to category '{category}'"
        if list_name:
            message += f" in list '{list_name}'"

        return {
            "success": True,
            "fact": response_fact,
            "message": message
        }

    def _get_facts(
        self,
        category: Optional[str] = None,
        list_name: Optional[str] = None,
        search_text: Optional[str] = None,
        fact_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get facts based on filters.

        Args:
            category: Filter by category
            list_name: Filter by list name
            search_text: Search for facts containing this text
            fact_id: Get specific fact by ID

        Returns:
            Dict containing matching facts
        """
        # Get specific fact by ID
        if fact_id:
            facts = self.db.select('facts', 'id = :id', {'id': fact_id})
            if not facts:
                return {
                    "success": False,
                    "message": f"Fact '{fact_id}' not found"
                }
            return {
                "success": True,
                "fact": self._format_fact(facts[0]),
                "message": "Found fact"
            }

        # Build query based on filters
        where_clauses = []
        params = {}

        if category:
            where_clauses.append("category = :category")
            params['category'] = category.strip().lower()

        if list_name:
            where_clauses.append("list_name = :list_name")
            params['list_name'] = list_name.strip().lower()

        # Execute query
        if where_clauses:
            where_sql = " AND ".join(where_clauses)
            facts = self.db.select('facts', where_sql, params)
        else:
            facts = self.db.select('facts')

        # Apply text search filter if provided (post-query since content is encrypted)
        if search_text:
            search_lower = search_text.lower()
            facts = [
                f for f in facts
                if search_lower in f.get('encrypted__content', '').lower()
            ]

        # Sort by created_at descending (newest first)
        facts.sort(key=lambda f: f.get('created_at', ''), reverse=True)

        # Format for display
        formatted_facts = [self._format_fact(f) for f in facts]

        # Build descriptive message
        filters = []
        if category:
            filters.append(f"category '{category}'")
        if list_name:
            filters.append(f"list '{list_name}'")
        if search_text:
            filters.append(f"containing '{search_text}'")

        filter_desc = " and ".join(filters) if filters else "all facts"
        message = f"Found {len(formatted_facts)} fact(s) for {filter_desc}"

        return {
            "success": True,
            "facts": formatted_facts,
            "count": len(formatted_facts),
            "message": message
        }

    def _update_fact(
        self,
        fact_id: str,
        content: Optional[str] = None,
        category: Optional[str] = None,
        list_name: Optional[str] = None,
        metadata: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update an existing fact.

        Args:
            fact_id: ID of the fact to update
            content: New content
            category: New category
            list_name: New list name
            metadata: New metadata JSON string

        Returns:
            Dict containing the updated fact

        Raises:
            ValueError: If fact_id is invalid or not found
        """
        if not fact_id:
            self.logger.error("Missing fact_id in update_fact operation")
            raise ValueError("fact_id is required")

        # Find the fact
        facts = self.db.select('facts', 'id = :id', {'id': fact_id})
        if not facts:
            self.logger.error(f"Fact '{fact_id}' not found")
            return {
                "success": False,
                "message": f"Fact '{fact_id}' not found"
            }

        # Build update data
        update_data = {}
        changes = []

        if content is not None:
            update_data['encrypted__content'] = content.strip()
            changes.append("content")

        if category is not None:
            update_data['category'] = category.strip().lower()
            changes.append("category")

        if list_name is not None:
            update_data['list_name'] = list_name.strip().lower() if list_name else None
            changes.append("list_name")

        if metadata is not None:
            # Validate JSON
            try:
                parsed = json.loads(metadata)
                if not isinstance(parsed, dict):
                    raise ValueError("Metadata must be a JSON object")
                update_data['encrypted__metadata'] = metadata
                changes.append("metadata")
            except json.JSONDecodeError as e:
                self.logger.error(f"Invalid JSON in metadata: {e}")
                raise ValueError(f"Invalid JSON in metadata: {e}")

        # Always update timestamp
        update_data['updated_at'] = format_utc_iso(utc_now())

        if not changes:
            return {
                "success": False,
                "message": "No changes provided"
            }

        # Update in database
        self.db.update('facts', update_data, 'id = :id', {'id': fact_id})

        # Get updated fact
        updated_facts = self.db.select('facts', 'id = :id', {'id': fact_id})
        if updated_facts:
            return {
                "success": True,
                "fact": self._format_fact(updated_facts[0]),
                "updated_fields": changes,
                "message": f"Updated fact: {', '.join(changes)}"
            }

        raise ValueError("Failed to retrieve updated fact")

    def _delete_fact(self, fact_id: str) -> Dict[str, Any]:
        """
        Delete a fact.

        Args:
            fact_id: ID of the fact to delete

        Returns:
            Dict containing deletion confirmation

        Raises:
            ValueError: If fact_id is invalid or not found
        """
        if not fact_id:
            self.logger.error("Missing fact_id in delete_fact operation")
            raise ValueError("fact_id is required")

        # Find the fact first
        facts = self.db.select('facts', 'id = :id', {'id': fact_id})
        if not facts:
            self.logger.error(f"Fact '{fact_id}' not found")
            return {
                "success": False,
                "message": f"Fact '{fact_id}' not found"
            }

        fact = facts[0]

        # Delete from database
        self.db.delete('facts', 'id = :id', {'id': fact_id})

        content_preview = fact.get('encrypted__content', '')[:50]
        if len(fact.get('encrypted__content', '')) > 50:
            content_preview += "..."

        return {
            "success": True,
            "deleted_fact": {
                "id": fact_id,
                "category": fact.get('category'),
                "content_preview": content_preview
            },
            "message": f"Deleted fact: {content_preview}"
        }

    def _batch_delete(self, fact_ids: List[str]) -> Dict[str, Any]:
        """
        Delete multiple facts at once.

        Args:
            fact_ids: List of fact IDs to delete

        Returns:
            Dict with succeeded/failed lists and summary

        Raises:
            ValueError: If fact_ids is empty or invalid
        """
        if not fact_ids or not isinstance(fact_ids, list):
            raise ValueError("fact_ids must be a non-empty array")

        succeeded = []
        not_found = []

        for fact_id in fact_ids:
            result = self._delete_fact(fact_id)
            if result.get("success"):
                succeeded.append(fact_id)
            else:
                not_found.append(fact_id)

        total = len(fact_ids)
        success_count = len(succeeded)

        return {
            "success": success_count == total,
            "succeeded": succeeded,
            "not_found": not_found,
            "message": f"Deleted {success_count} of {total} facts"
        }

    def _list_categories(self) -> Dict[str, Any]:
        """
        List all categories and lists in use with counts.

        Returns:
            Dict containing categories and lists with fact counts
        """
        # Get all facts
        facts = self.db.select('facts')

        # Count by category
        category_counts: Dict[str, int] = {}
        list_counts: Dict[str, Dict[str, int]] = {}

        for fact in facts:
            category = fact.get('category', 'general')
            list_name = fact.get('list_name')

            # Count category
            category_counts[category] = category_counts.get(category, 0) + 1

            # Count list if present
            if list_name:
                if category not in list_counts:
                    list_counts[category] = {}
                list_counts[category][list_name] = list_counts[category].get(list_name, 0) + 1

        # Format for display
        categories = [
            {
                "category": cat,
                "count": count,
                "lists": [
                    {"list_name": list_name, "count": list_count}
                    for list_name, list_count in sorted(list_counts.get(cat, {}).items())
                ] if cat in list_counts else []
            }
            for cat, count in sorted(category_counts.items())
        ]

        return {
            "success": True,
            "categories": categories,
            "total_facts": len(facts),
            "message": f"Found {len(category_counts)} categories with {len(facts)} total facts"
        }

    def _format_fact(self, fact: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format a fact for display.

        Args:
            fact: Raw fact dict from database

        Returns:
            Formatted fact dict
        """
        formatted = {
            "id": fact['id'],
            "category": fact.get('category', 'general'),
            "list_name": fact.get('list_name'),
            "encrypted__content": fact.get('encrypted__content', ''),
            "created_at": fact.get('created_at'),
            "updated_at": fact.get('updated_at')
        }

        # Parse metadata if present
        if fact.get('encrypted__metadata'):
            try:
                formatted["metadata"] = json.loads(fact['encrypted__metadata'])
            except json.JSONDecodeError:
                pass  # Skip malformed metadata

        return formatted
