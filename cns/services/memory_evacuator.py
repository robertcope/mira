"""
Memory evacuation service for curating pinned memories under pressure.

When pinned memory count exceeds threshold, forces prioritization to maintain
a tight, high-signal memory set. Uses larger conversation window than fingerprint
for better trajectory prediction.
"""
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Set

from config.config import ProactiveConfig
from cns.core.continuum import Continuum
from clients.vault_client import get_api_key
from utils.tag_parser import format_memory_id

logger = logging.getLogger(__name__)


class MemoryEvacuator:
    """
    Curates pinned memories when count exceeds threshold.

    Forces prioritization to maintain high-signal memory set.
    Uses larger conversation window than fingerprint for trajectory prediction.
    Reuses fingerprint LLM config (analysis_endpoint, analysis_model).
    """

    def __init__(
        self,
        proactive_config: ProactiveConfig,
        llm_provider
    ):
        """
        Initialize memory evacuator.

        Args:
            proactive_config: Proactive config with evacuation thresholds
            llm_provider: LLM provider for evacuation calls

        Raises:
            FileNotFoundError: If prompt files not found
            ValueError: If API key not found in Vault
        """
        self.config = proactive_config
        self.llm_provider = llm_provider

        # Load prompt templates
        system_prompt_path = Path("config/prompts/memory_evacuation_system.txt")
        user_prompt_path = Path("config/prompts/memory_evacuation_user.txt")

        if not system_prompt_path.exists():
            raise FileNotFoundError(
                f"Evacuation system prompt not found at {system_prompt_path}"
            )

        if not user_prompt_path.exists():
            raise FileNotFoundError(
                f"Evacuation user prompt not found at {user_prompt_path}"
            )

        with open(system_prompt_path, 'r') as f:
            self.system_prompt_template = f.read()

        with open(user_prompt_path, 'r') as f:
            self.user_prompt_template = f.read()

        # Get LLM config from database
        from utils.user_context import get_internal_llm
        llm_config = get_internal_llm('analysis')
        self._llm_config = llm_config

        # Get API key for LLM endpoint (None for local providers like Ollama)
        if llm_config.api_key_name:
            self.api_key = get_api_key(llm_config.api_key_name)
            if not self.api_key:
                raise ValueError(
                    f"API key '{llm_config.api_key_name}' not found in Vault"
                )
        else:
            self.api_key = None  # Local provider (Ollama) - no API key needed

        logger.info(
            f"MemoryEvacuator initialized: threshold={self.config.evacuation_trigger_threshold}, "
            f"target={self.config.evacuation_target_count}"
        )

    def should_evacuate(self, memories: List[Dict[str, Any]]) -> bool:
        """
        Check if anchor count warrants evacuation.

        Args:
            memories: List of memory dicts (anchors only, not linked)

        Returns:
            True if evacuation should be triggered
        """
        return len(memories) > self.config.evacuation_trigger_threshold

    def evacuate(
        self,
        memories: List[Dict[str, Any]],
        continuum: Continuum,
        user_message: str
    ) -> List[Dict[str, Any]]:
        """
        Call evacuation LLM to reduce pinned anchor load.

        Args:
            memories: List of memory dicts to evaluate
            continuum: Continuum with conversation history
            user_message: Current user message

        Returns:
            Filtered memory list (survivors only)

        Raises:
            RuntimeError: On LLM failure
        """
        memory_count = len(memories)
        target = self.config.evacuation_target_count

        logger.debug(
            f"Evacuating {memory_count} anchors to target {target} "
            f"(window: {self.config.evacuation_conversation_window} pairs)"
        )

        # Format memories with full signal suite
        memories_block = self._format_memories_for_evacuation(memories)

        # Get larger conversation window
        conversation_turns = self._format_extended_turns(
            continuum,
            user_message,
            window_size=self.config.evacuation_conversation_window
        )

        # Build system prompt with target count
        system_prompt = self.system_prompt_template.replace(
            "{target_count}",
            str(target)
        )

        # Build user message
        user_content = self.user_prompt_template.replace(
            "{conversation_turns}",
            conversation_turns
        ).replace(
            "{user_message}",
            user_message
        ).replace(
            "{memories_block}",
            memories_block
        ).replace(
            "{target_count}",
            str(target)
        )

        try:
            response = self.llm_provider.generate_response(
                messages=[{"role": "user", "content": user_content}],
                stream=False,
                endpoint_url=self._llm_config.endpoint_url,
                model_override=self._llm_config.model,
                api_key_override=self.api_key,
                system_override=system_prompt
            )

            response_text = self.llm_provider.extract_text_content(response).strip()

            if not response_text:
                raise RuntimeError("Evacuation LLM returned empty response")

            # Parse survivor IDs
            survivor_ids = self._parse_survivors(response_text)

            if not survivor_ids:
                logger.warning(
                    "No survivor IDs parsed from evacuation response, keeping all memories"
                )
                return memories

            # Filter to survivors (compare by 8-char prefix, case-insensitive)
            survivors = [
                m for m in memories
                if m.get('id', '').replace('-', '')[:8].lower() in survivor_ids
            ]

            logger.debug(
                f"Evacuation LLM returned {len(survivor_ids)} survivor IDs, "
                f"matched {len(survivors)}/{memory_count} anchors"
            )

            return survivors

        except Exception as e:
            logger.error(f"Evacuation failed: {e}", exc_info=True)
            raise RuntimeError(f"Memory evacuation failed: {str(e)}") from e

    def _format_memories_for_evacuation(self, memories: List[Dict[str, Any]]) -> str:
        """
        Format memories with full signal suite: imp, sim, links, mentions.

        Args:
            memories: List of memory dicts

        Returns:
            Formatted memory block for prompt
        """
        lines = []
        for m in memories:
            formatted_id = format_memory_id(m.get('id', ''))
            imp = m.get('importance_score', 0.5)
            sim = m.get('similarity_score') or 0.0
            inbound = m.get('inbound_links', [])
            outbound = m.get('outbound_links', [])
            links = len(inbound) + len(outbound)
            mentions = m.get('mention_count') or 0
            text = m.get('text', '')

            lines.append(
                f"- {formatted_id} [imp:{imp:.2f} | sim:{sim:.2f} | links:{links} | mentions:{mentions}] - {text}"
            )

        return "\n".join(lines)

    def _format_extended_turns(
        self,
        continuum: Continuum,
        current_user_message: str,
        window_size: int
    ) -> str:
        """
        Format extended conversation window for context.

        Larger window than fingerprint for better trajectory prediction.

        Args:
            continuum: Continuum with message cache
            current_user_message: Current user message
            window_size: Number of user/assistant pairs to include

        Returns:
            Formatted conversation string
        """
        lines = []
        pairs_found = 0
        i = len(continuum.messages) - 1

        # Walk backwards to extract user/assistant pairs
        while i >= 0 and pairs_found < window_size:
            # Find assistant message (skip segment summaries)
            while i >= 0:
                msg = continuum.messages[i]
                if msg.role == "assistant" and not self._is_segment_summary(msg):
                    break
                i -= 1
            if i < 0:
                break
            assistant_msg = continuum.messages[i]
            i -= 1

            # Find preceding user message
            while i >= 0 and continuum.messages[i].role != "user":
                i -= 1
            if i < 0:
                break
            user_msg = continuum.messages[i]
            i -= 1

            # Prepend pair (we're walking backwards)
            user_content = self._extract_text_content(user_msg.content)
            lines.insert(0, f"Assistant: {assistant_msg.content}")
            lines.insert(0, f"User: {user_content}")
            pairs_found += 1

        # Append current user message
        lines.append(f"User: {current_user_message}")

        return "\n".join(lines)

    def _parse_survivors(self, response_text: str) -> Set[str]:
        """
        Parse survivor IDs from LLM response.

        Args:
            response_text: Raw LLM response

        Returns:
            Set of 8-char memory IDs to keep
        """
        # Extract <survivors> block
        survivors_match = re.search(
            r'<survivors>(.*?)</survivors>',
            response_text,
            re.DOTALL | re.IGNORECASE
        )

        if not survivors_match:
            logger.warning("No <survivors> block found in evacuation response")
            return set()

        survivors_block = survivors_match.group(1)

        # Extract prefixed memory IDs (one per line)
        # Format: mem_a1B2c3D4
        # UUIDs only contain hex chars (0-9, a-f)
        id_matches = re.findall(
            r'\bmem_([a-fA-F0-9]{8})\b',
            survivors_block,
            re.IGNORECASE
        )

        survivor_ids = {match.lower() for match in id_matches}
        logger.debug(f"Parsed {len(survivor_ids)} survivor IDs from response")

        return survivor_ids

    def _is_segment_summary(self, message) -> bool:
        """Check if message is a collapsed segment summary."""
        metadata = getattr(message, 'metadata', {}) or {}
        return (
            metadata.get('is_segment_boundary', False) and
            metadata.get('status') == 'collapsed'
        )

    def _extract_text_content(self, content) -> str:
        """Extract text from potentially multimodal content."""
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            text_parts = [
                item['text'] for item in content
                if isinstance(item, dict) and item.get('type') == 'text'
            ]
            return ' '.join(text_parts) if text_parts else '[non-text content]'

        return str(content)
