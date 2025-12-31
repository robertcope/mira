"""
Tag parsing service for CNS.

Extracts semantic tags from assistant responses.
"""
import re
from typing import Dict, Any, Optional


# =============================================================================
# Memory ID Formatting Utilities
# =============================================================================

MEMORY_ID_PREFIX = "mem_"
MEMORY_ID_LENGTH = 8


def format_memory_id(uuid_str: str) -> str:
    """
    Format a full UUID to the shortened mem_XXXXXXXX display format.

    Preserves case from the UUID for visual distinction between IDs.

    Args:
        uuid_str: Full UUID string (with or without dashes)

    Returns:
        Formatted ID like "mem_5E9a8D3c" (mixed case preserved)
        Empty string if input is empty/None
    """
    if not uuid_str:
        return ""
    clean = uuid_str.replace('-', '')
    return f"{MEMORY_ID_PREFIX}{clean[:MEMORY_ID_LENGTH]}"


def parse_memory_id(formatted_id: str) -> str:
    """
    Extract the 8-character ID portion from a formatted memory ID.

    Handles both:
    - "mem_5E9a8D3c" -> "5E9a8D3c"
    - "5E9a8D3c" -> "5E9a8D3c" (passthrough for raw IDs)

    Args:
        formatted_id: Either "mem_XXXXXXXX" or raw "XXXXXXXX"

    Returns:
        The 8-character ID portion, empty string if invalid
    """
    if not formatted_id:
        return ""
    if formatted_id.startswith(MEMORY_ID_PREFIX):
        return formatted_id[len(MEMORY_ID_PREFIX):]
    return formatted_id


def match_memory_id(full_uuid: str, short_id: str) -> bool:
    """
    Check if a shortened ID matches a full UUID.

    Case-insensitive comparison for robust matching.

    Args:
        full_uuid: Full UUID string
        short_id: Short ID (with or without mem_ prefix)

    Returns:
        True if the short ID matches the UUID's prefix
    """
    if not full_uuid or not short_id:
        return False

    uuid_prefix = full_uuid.replace('-', '')[:MEMORY_ID_LENGTH].lower()
    parsed_short = parse_memory_id(short_id).lower()
    return uuid_prefix == parsed_short


# =============================================================================
# Tag Parser Class
# =============================================================================


class TagParser:
    """
    Service for parsing semantic tags from assistant responses.

    Handles memory references and other semantic markup in LLM responses.
    """

    # Tag patterns
    ERROR_ANALYSIS_PATTERN = re.compile(r'<error_analysis\s+error_id=["\']([^"\']+)["\']>(.*?)</error_analysis>', re.DOTALL | re.IGNORECASE)
    # Pattern for memory references block: <mira:memory_refs>mem_XXX, mem_YYY</mira:memory_refs>
    MEMORY_REFS_PATTERN = re.compile(
        r'<mira:memory_refs>(.*?)</mira:memory_refs>',
        re.IGNORECASE | re.DOTALL
    )
    # Pattern to extract individual memory IDs from the block
    # UUIDs only contain hex chars (0-9, a-f)
    MEMORY_ID_PATTERN = re.compile(
        r'mem_([a-fA-F0-9]{8})',
        re.IGNORECASE
    )
    # Pattern for emotion emoji: <mira:my_emotion>emoji</mira:my_emotion>
    EMOTION_PATTERN = re.compile(
        r'<mira:my_emotion>\s*([^\s<]+)\s*</mira:my_emotion>',
        re.IGNORECASE
    )
    # Pattern for segment display title: <mira:display_title>title</mira:display_title>
    DISPLAY_TITLE_PATTERN = re.compile(
        r'<mira:display_title>(.*?)</mira:display_title>',
        re.DOTALL | re.IGNORECASE
    )
    # Pattern for segment complexity score: <mira:complexity>1-3</mira:complexity>
    COMPLEXITY_PATTERN = re.compile(
        r'<mira:complexity>\s*([123])\s*</mira:complexity>',
        re.IGNORECASE
    )
    
    def parse_response(self, response_text: str, preserve_tags: list = None) -> Dict[str, Any]:
        """
        Parse all tags from response text.

        Args:
            response_text: Assistant response to parse
            preserve_tags: Optional list of tag names to preserve in clean_text (e.g., ['my_emotion'])

        Returns:
            Dictionary with parsed tag information
        """
        # Extract error analysis
        error_analyses = []
        for match in self.ERROR_ANALYSIS_PATTERN.finditer(response_text):
            error_analyses.append({
                'error_id': match.group(1),
                'analysis': match.group(2).strip()
            })

        # Extract memory references from <mira:memory_refs> block
        memory_refs = []
        refs_match = self.MEMORY_REFS_PATTERN.search(response_text)
        if refs_match:
            refs_content = refs_match.group(1)
            # Extract individual mem_XXXXXXXX IDs from the block
            for id_match in self.MEMORY_ID_PATTERN.finditer(refs_content):
                memory_refs.append(id_match.group(1))

        # Extract emotion emoji
        emotion = None
        emotion_match = self.EMOTION_PATTERN.search(response_text)
        if emotion_match:
            emotion_text = emotion_match.group(1).strip()
            if emotion_text:
                emotion = emotion_text

        # Extract display title
        display_title = None
        display_title_match = self.DISPLAY_TITLE_PATTERN.search(response_text)
        if display_title_match:
            display_title_text = display_title_match.group(1).strip()
            if display_title_text:
                display_title = display_title_text

        # Extract complexity score
        complexity = None
        complexity_match = self.COMPLEXITY_PATTERN.search(response_text)
        if complexity_match:
            complexity = int(complexity_match.group(1))

        parsed = {
            'error_analysis': error_analyses,
            'referenced_memories': memory_refs,
            'emotion': emotion,
            'display_title': display_title,
            'complexity': complexity,
            'clean_text': self.remove_all_tags(response_text, preserve_tags=preserve_tags)
        }

        return parsed

    def remove_all_tags(self, text: str, preserve_tags: list = None) -> str:
        """
        Remove all semantic tags from text for clean display.

        Args:
            text: Text with tags
            preserve_tags: Optional list of tag names to preserve (e.g., ['my_emotion'])

        Returns:
            Text with tags removed (except preserved ones)
        """
        preserve_tags = preserve_tags or []

        if preserve_tags:
            # Build pattern to match all tags EXCEPT preserved ones
            preserve_pattern = '|'.join(re.escape(tag) for tag in preserve_tags)

            # Remove paired mira tags that are NOT in preserve list
            text = re.sub(
                r'<mira:([^>\/\s]+)(?:\s[^>]*)?>[\s\S]*?</mira:\1>',
                lambda m: m.group(0) if m.group(1).lower() in [t.lower() for t in preserve_tags] else '',
                text,
                flags=re.IGNORECASE
            )

            # Remove self-closing mira tags that are NOT in preserve list
            text = re.sub(
                r'<mira:([^>\s\/]+)[^>]*\/>',
                lambda m: m.group(0) if m.group(1).lower() in [t.lower() for t in preserve_tags] else '',
                text,
                flags=re.IGNORECASE
            )

            # Remove any remaining malformed mira tags (but not preserved ones)
            text = re.sub(
                r'</?mira:([^>\s]+)[^>]*>',
                lambda m: m.group(0) if m.group(1).lower() in [t.lower() for t in preserve_tags] else '',
                text,
                flags=re.IGNORECASE
            )
        else:
            # Remove all paired mira tags with their content
            text = re.sub(r'<mira:([^>\/\s]+)(?:\s[^>]*)?>[\s\S]*?</mira:\1>', '', text, flags=re.IGNORECASE)

            # Remove all self-closing mira tags
            text = re.sub(r'<mira:[^>]*\/>', '', text, flags=re.IGNORECASE)

            # Remove any remaining malformed mira tags
            text = re.sub(r'</?mira:[^>]*>', '', text, flags=re.IGNORECASE)

        # Remove specific error analysis patterns
        text = self.ERROR_ANALYSIS_PATTERN.sub('', text)

        # Clean up extra whitespace
        text = re.sub(r'\n\s*\n', '\n\n', text)  # Remove blank lines
        text = text.strip()

        return text