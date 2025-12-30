"""
Tier control tool for managing LLM tier preferences.

This tool allows MIRA to change the user's LLM tier preference when requested
in natural language. Users can ask "switch to fast mode" or "use the nuanced model"
and MIRA will understand and change the tier accordingly.
"""

import logging
from typing import Dict, Any
from pydantic import BaseModel, Field

from tools.repo import Tool
from tools.registry import registry

logger = logging.getLogger(__name__)


class TierControlToolConfig(BaseModel):
    """Configuration for the tier_control_tool."""
    enabled: bool = Field(
        default=True,
        description="Whether this tool is enabled by default"
    )


# Register with registry
registry.register("tier_control_tool", TierControlToolConfig)


class TierControlTool(Tool):
    """
    Control LLM tier preferences for conversation quality and speed.

    This tool allows changing between different LLM tiers based on user needs:
    - fast: Qwen3 32B via Groq - optimized for speed
    - balanced: Kimi K2 via Groq - balance of speed and quality
    - nuanced: Opus with extended thinking - maximum reasoning depth
    """

    name = "tier_control_tool"
    simple_description = "Change LLM tier preference for conversation quality and speed"

    anthropic_schema = {
        "name": "tier_control_tool",
        "description": (
            "Manage the LLM tier preference for conversations. Use this when the user wants to:\n"
            "- Switch to faster responses (tier='fast')\n"
            "- Balance speed and quality (tier='balanced')\n"
            "- Get more thoughtful, nuanced reasoning (tier='nuanced')\n"
            "- Check current tier setting (operation='get')\n"
            "\n"
            "Tier descriptions:\n"
            "- fast: Qwen3 32B - Quick responses, best for rapid interactions\n"
            "- balanced: Kimi K2 - Good balance of speed and reasoning quality (default)\n"
            "- nuanced: Opus with extended thinking - Deep reasoning for complex problems\n"
            "\n"
            "The tier change applies immediately to subsequent messages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["get", "set"],
                    "description": (
                        "Operation to perform:\n"
                        "- get: Retrieve current tier and available options\n"
                        "- set: Change to a different tier"
                    )
                },
                "tier": {
                    "type": "string",
                    "enum": ["fast", "balanced", "nuanced"],
                    "description": (
                        "Tier to switch to (required for 'set' operation):\n"
                        "- fast: Quick responses with Qwen3 32B\n"
                        "- balanced: Balanced performance with Kimi K2\n"
                        "- nuanced: Deep reasoning with Opus + extended thinking"
                    )
                }
            },
            "required": ["operation"]
        }
    }

    def __init__(self):
        """Initialize tier control tool."""
        super().__init__()
        logger.info("TierControlTool initialized")

    def run(self, operation: str, tier: str = None) -> Dict[str, Any]:
        """
        Execute tier control operation.

        Args:
            operation: Operation to perform ('get' or 'set')
            tier: Tier to switch to (required for 'set' operation)

        Returns:
            Dictionary with operation result
        """
        try:
            if operation == "get":
                return self._get_tier_info()
            elif operation == "set":
                if not tier:
                    return {
                        "success": False,
                        "error": "tier parameter is required for 'set' operation"
                    }
                return self._set_tier(tier)
            else:
                return {
                    "success": False,
                    "error": f"Unknown operation: {operation}. Valid operations: get, set"
                }

        except Exception as e:
            error_msg = f"Tier control operation failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "error": error_msg
            }

    def _get_tier_info(self) -> Dict[str, Any]:
        """
        Get current tier and available tier information.

        Returns:
            Dictionary with current tier and available options
        """
        from utils.user_context import get_user_preferences, get_account_tiers, get_accessible_tiers
        from utils.thread_tier import get_thread_tier

        prefs = get_user_preferences()
        all_tiers = get_account_tiers()
        accessible = get_accessible_tiers(prefs.max_tier)
        accessible_names = {t.name for t in accessible}

        # Get current tier from thread-scoped storage
        current_tier = get_thread_tier(self.user_id)

        # Build tier list with access information
        tier_list = []
        for tier in sorted(all_tiers.values(), key=lambda t: t.display_order):
            is_accessible = tier.name in accessible_names
            # Show tier if accessible or if show_locked is true
            if is_accessible or tier.show_locked:
                tier_list.append({
                    "name": tier.name,
                    "description": tier.description,
                    "accessible": is_accessible,
                    "locked_message": tier.locked_message if not is_accessible else None
                })

        # Create user-friendly response
        current_tier_info = all_tiers.get(current_tier)
        current_tier_desc = current_tier_info.description if current_tier_info else current_tier

        response_parts = [f"Current tier: {current_tier} ({current_tier_desc})"]

        if len([t for t in tier_list if t["accessible"]]) > 1:
            response_parts.append("\nAvailable tiers:")
            for tier in tier_list:
                if tier["accessible"]:
                    marker = "→" if tier["name"] == current_tier else " "
                    response_parts.append(f"{marker} {tier['name']}: {tier['description']}")

        logger.info(f"Retrieved tier info: current={current_tier}, max={prefs.max_tier}")

        return {
            "success": True,
            "current_tier": current_tier,
            "max_tier": prefs.max_tier,
            "available_tiers": tier_list,
            "response": "\n".join(response_parts)
        }

    def _set_tier(self, tier: str) -> Dict[str, Any]:
        """
        Change the LLM tier preference for the current thread.

        Args:
            tier: Tier name to switch to

        Returns:
            Dictionary with operation result
        """
        from utils.user_context import (
            get_account_tiers,
            can_access_tier,
            get_user_preferences
        )
        from utils.thread_tier import get_thread_tier, set_thread_tier

        # Validate tier exists
        tiers = get_account_tiers()
        if tier not in tiers:
            valid_tiers = list(tiers.keys())
            return {
                "success": False,
                "error": f"Invalid tier '{tier}'. Valid tiers: {', '.join(valid_tiers)}"
            }

        # Check if user has access to this tier
        prefs = get_user_preferences()
        if not can_access_tier(tier, prefs.max_tier):
            tier_info = tiers[tier]
            locked_msg = tier_info.locked_message or f"Tier '{tier}' is not available on your account"
            return {
                "success": False,
                "error": locked_msg,
                "requires_upgrade": True
            }

        # Get current tier before update
        previous_tier = get_thread_tier(self.user_id)

        # Update tier in thread-scoped storage
        success = set_thread_tier(self.user_id, tier)
        if not success:
            return {
                "success": False,
                "error": "Unable to set tier: no thread context available"
            }

        tier_info = tiers[tier]
        response = f"Switched to {tier} tier ({tier_info.description}). This change applies to subsequent messages in this thread."

        logger.info(f"Changed tier from {previous_tier} to {tier}")

        return {
            "success": True,
            "previous_tier": previous_tier,
            "new_tier": tier,
            "response": response
        }
