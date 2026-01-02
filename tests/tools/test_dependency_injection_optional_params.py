"""
Test that dependency injection works for parameters with default values.

This test verifies the fix for the bug where Optional[ToolRepository] = None
parameters were being skipped during dependency injection because they had
default values.
"""
import pytest
from typing import Optional, TYPE_CHECKING
from unittest.mock import Mock

from tools.repo import Tool, ToolRepository

if TYPE_CHECKING:
    from working_memory.core import WorkingMemory


class TestToolWithOptionalDeps(Tool):
    """Test tool with Optional dependencies that have default values."""

    name = "test_optional_deps_tool"

    def __init__(self,
                 tool_repo: Optional['ToolRepository'] = None,
                 working_memory: Optional['WorkingMemory'] = None):
        """Initialize with optional dependencies (mimics GetContextTool pattern)."""
        super().__init__()
        self.tool_repo = tool_repo
        self.working_memory = working_memory

    def run(self, **kwargs):
        """Dummy run method."""
        return {
            "has_tool_repo": self.tool_repo is not None,
            "has_working_memory": self.working_memory is not None
        }


@pytest.fixture
def mock_working_memory():
    """Mock WorkingMemory instance."""
    return Mock()


def test_dependency_injection_with_optional_defaults(mock_working_memory):
    """Test that Optional[Type] = None parameters still get injected."""
    # Create repository with working memory
    repo = ToolRepository(working_memory=mock_working_memory)

    # Register the test tool
    repo.register_tool_class(TestToolWithOptionalDeps, "test_optional_deps_tool")
    repo.enable_tool("test_optional_deps_tool")

    # Get tool instance - dependencies should be injected despite default values
    tool = repo.get_tool("test_optional_deps_tool")

    # Verify dependencies were injected
    assert tool.tool_repo is not None, "tool_repo should be injected despite having default value"
    assert tool.tool_repo is repo, "tool_repo should be the repository instance"

    assert tool.working_memory is not None, "working_memory should be injected despite having default value"
    assert tool.working_memory is mock_working_memory, "working_memory should be the mock instance"

    # Verify tool can actually use the dependencies
    result = tool.run()
    assert result["has_tool_repo"] is True
    assert result["has_working_memory"] is True


def test_dependency_injection_without_working_memory():
    """Test that tools work when working_memory is unavailable."""
    # Create repository without working memory
    repo = ToolRepository(working_memory=None)

    # Register the test tool
    repo.register_tool_class(TestToolWithOptionalDeps, "test_optional_deps_tool")
    repo.enable_tool("test_optional_deps_tool")

    # Get tool instance
    tool = repo.get_tool("test_optional_deps_tool")

    # tool_repo should still be injected
    assert tool.tool_repo is not None
    assert tool.tool_repo is repo

    # working_memory should remain None (not available)
    assert tool.working_memory is None

    # Verify tool still works
    result = tool.run()
    assert result["has_tool_repo"] is True
    assert result["has_working_memory"] is False


class TestToolWithRequiredDeps(Tool):
    """Test tool with required dependencies (no defaults)."""

    name = "test_required_deps_tool"

    def __init__(self, tool_repo: 'ToolRepository'):
        """Initialize with required dependency (no default)."""
        super().__init__()
        self.tool_repo = tool_repo

    def run(self, **kwargs):
        return {"has_tool_repo": self.tool_repo is not None}


def test_dependency_injection_with_required_params():
    """Test that required parameters (no defaults) still work."""
    repo = ToolRepository()

    repo.register_tool_class(TestToolWithRequiredDeps, "test_required_deps_tool")
    repo.enable_tool("test_required_deps_tool")

    # Get tool instance
    tool = repo.get_tool("test_required_deps_tool")

    # Verify dependency was injected
    assert tool.tool_repo is not None
    assert tool.tool_repo is repo


class TestToolWithNoAnnotations(Tool):
    """Test tool with no type annotations."""

    name = "test_no_annotations_tool"

    def __init__(self, some_param=None):
        """Initialize with unannotated parameter."""
        super().__init__()
        self.some_param = some_param

    def run(self, **kwargs):
        return {"some_param": self.some_param}


def test_dependency_injection_skips_unannotated_params():
    """Test that parameters without annotations are skipped."""
    repo = ToolRepository()

    repo.register_tool_class(TestToolWithNoAnnotations, "test_no_annotations_tool")
    repo.enable_tool("test_no_annotations_tool")

    # Get tool instance - should work with defaults
    tool = repo.get_tool("test_no_annotations_tool")

    # Unannotated param should use its default value
    assert tool.some_param is None
