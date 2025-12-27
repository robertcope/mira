"""Tool guidance trinket for displaying tool hints and usage tips."""
import logging
from typing import Dict, Any

from .base import EventAwareTrinket

logger = logging.getLogger(__name__)


class ToolGuidanceTrinket(EventAwareTrinket):
    """
    Manages tool hints and guidance for the system prompt.

    Collects hints from enabled tools that provide usage tips
    beyond their function definitions.
    """
    
    def _get_variable_name(self) -> str:
        """Tool guidance publishes to 'tool_guidance'."""
        return "tool_guidance"
    
    def generate_content(self, context: Dict[str, Any]) -> str:
        """
        Generate tool guidance content from tool hints.
        
        Args:
            context: Update context containing 'tool_hints' dict
                - tool_hints: Dict mapping tool names to hint strings
            
        Returns:
            Formatted tool guidance section or empty string
        """
        tool_hints = context.get('tool_hints', {})
        
        # Filter out empty hints
        valid_hints = {name: hint for name, hint in tool_hints.items() 
                      if hint and hint.strip()}
        
        if not valid_hints:
            logger.debug("No tool hints available")
            return ""
        
        # Generate tool guidance section with XML structure
        parts = ["<tool_guidance>"]

        # Add each tool's hints
        for tool_name, hint in sorted(valid_hints.items()):
            # Use raw tool name for attribute (without _tool suffix for readability)
            attr_name = tool_name.replace('_tool', '')
            parts.append(f"<tool name=\"{attr_name}\">")
            parts.append(hint.strip())
            parts.append("</tool>")

        parts.append("</tool_guidance>")
        result = "\n".join(parts)
        
        logger.debug(f"Generated tool guidance for {len(valid_hints)} tools with hints")
        return result