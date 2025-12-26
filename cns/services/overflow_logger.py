"""
Per-event context overflow logging for tuning analysis.

Logs overflow detection and remediation results to JSONL files for
offline analysis of topic drift thresholds, remediation effectiveness,
and token estimation accuracy.

Log location: data/users/{user_id}/overflow_logs/{date}.jsonl
"""
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from uuid import UUID

from config import config
from utils.timezone_utils import utc_now
from utils.user_context import get_current_user_id

logger = logging.getLogger(__name__)


class ContextOverflowLogger:
    """
    Logs context overflow events for tuning analysis.

    Captures:
    - Token estimates vs context limits
    - Remediation tier effectiveness
    - Topic drift detection accuracy
    - LLM judgment results
    """

    def __init__(self, base_path: str = "data/users"):
        self.base_path = Path(base_path)

    def log_overflow(
        self,
        continuum_id: UUID,
        event_type: str,
        estimated_tokens: int,
        remediation_tier: Optional[int] = None,
        messages_before: Optional[int] = None,
        messages_after: Optional[int] = None,
        topic_drift_result: Optional[Dict[str, Any]] = None,
        llm_judgment_result: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error: Optional[str] = None
    ) -> None:
        """
        Log an overflow event for later analysis.

        Args:
            continuum_id: ID of the current continuum
            event_type: 'proactive' (pre-flight) or 'reactive' (API error)
            estimated_tokens: Token count that triggered overflow
            remediation_tier: 1=memory_evac, 2=topic_drift, 3=oldest_first
            messages_before: Message count before pruning
            messages_after: Message count after pruning
            topic_drift_result: Details from topic drift detection
            llm_judgment_result: Details from LLM cut point selection
            success: Whether remediation succeeded
            error: Error message if failed
        """
        user_id = get_current_user_id()
        if not user_id:
            return  # No user context

        # Build log directory
        log_dir = self.base_path / str(user_id) / "overflow_logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Date-based log file
        today = utc_now().strftime("%Y-%m-%d")
        log_file = log_dir / f"{today}.jsonl"

        # Context limits for analysis
        context_window = config.api.context_window_tokens
        max_tokens = config.api.max_tokens
        available = context_window - max_tokens

        # Build log entry
        entry = {
            "timestamp": utc_now().isoformat(),
            "continuum_id": str(continuum_id),
            "event_type": event_type,
            "tokens": {
                "estimated": estimated_tokens,
                "context_window": context_window,
                "available": available,
                "overflow_ratio": round(estimated_tokens / available, 3) if available > 0 else None
            },
            "remediation": {
                "tier": remediation_tier,
                "tier_name": self._tier_name(remediation_tier),
                "messages_before": messages_before,
                "messages_after": messages_after,
                "messages_pruned": (messages_before - messages_after) if messages_before and messages_after else None,
                "success": success,
                "error": error
            }
        }

        # Add topic drift details if present
        if topic_drift_result:
            entry["topic_drift"] = topic_drift_result

        # Add LLM judgment details if present
        if llm_judgment_result:
            entry["llm_judgment"] = llm_judgment_result

        # Remove empty nested dicts for cleaner logs
        entry = {k: v for k, v in entry.items() if v}

        # Append to JSONL
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logger.debug(f"Logged overflow event to {log_file}")

    def log_topic_drift_analysis(
        self,
        continuum_id: UUID,
        candidate_cuts: List[Dict[str, Any]],
        selected_index: Optional[int],
        selection_method: str,
        window_size: int,
        threshold: float
    ) -> Dict[str, Any]:
        """
        Create topic drift analysis details for logging.

        Returns dict to be passed to log_overflow() as topic_drift_result.
        """
        return {
            "window_size": window_size,
            "threshold": threshold,
            "candidates_found": len(candidate_cuts),
            "candidate_drops": [
                {"index": c["index"], "drop": round(c["drop"], 3)}
                for c in candidate_cuts[:5]  # Top 5
            ],
            "selected_index": selected_index,
            "selection_method": selection_method  # 'largest_drop', 'llm_judgment', 'fallback'
        }

    def log_llm_judgment(
        self,
        continuum_id: UUID,
        candidates_presented: int,
        llm_response: str,
        selected_boundary: Optional[int],
        parse_success: bool
    ) -> Dict[str, Any]:
        """
        Create LLM judgment details for logging.

        Returns dict to be passed to log_overflow() as llm_judgment_result.
        """
        return {
            "candidates_presented": candidates_presented,
            "llm_response": llm_response[:100],  # Truncate
            "selected_boundary": selected_boundary,
            "parse_success": parse_success
        }

    @staticmethod
    def _tier_name(tier: Optional[int]) -> Optional[str]:
        """Convert tier number to readable name."""
        names = {
            1: "memory_evacuation",
            2: "topic_drift",
            3: "oldest_first"
        }
        return names.get(tier)


# Singleton instance
_overflow_logger: ContextOverflowLogger = None


def get_overflow_logger() -> ContextOverflowLogger:
    """Get singleton overflow logger instance."""
    global _overflow_logger
    if _overflow_logger is None:
        _overflow_logger = ContextOverflowLogger()
    return _overflow_logger
