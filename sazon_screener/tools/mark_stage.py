"""Track which screening stage the candidate completed.

The SKILL.md instructs the agent to call `mark_screening_stage(stage)`
after each validated stage. This tool persists the stage to a JSON file
under data/stage_tracker/ so the cron re-engagement system and dashboard
can read the candidate's last completed stage.
"""

import json
import logging
from datetime import datetime, timezone

from google.adk.tools import FunctionTool

from ..config.paths import _PKG

logger = logging.getLogger(__name__)

STAGE_NAMES = ["greeting", "license", "city", "availability", "schedule", "experience", "start_date"]


def mark_screening_stage(stage: str, tool_context=None) -> dict:
    """Record a completed screening stage for the current candidate.

    Call this immediately after the candidate provides a valid answer for a
    stage. The stage name must be one of: greeting, license, city, availability,
    schedule, experience, start_date.

    Must be called BEFORE save_screening_result when disqualifying.
    Saves to data/stage_tracker/ so re-engagement knows where to resume.

    Args:
        stage: The name of the stage just completed (lowercase).

    Returns:
        A dict with success status and the current stage progress.
    """
    if stage not in STAGE_NAMES:
        return {"status": "error", "message": f"Invalid stage '{stage}'. Must be one of: {', '.join(STAGE_NAMES)}"}

    session_id = "unknown_session"
    if tool_context and hasattr(tool_context, "session_service") and tool_context.session_service:
        try:
            session_id = tool_context.session_service.session_id
        except Exception:
            pass

    tracker_dir = _PKG.parent / "data" / "stage_tracker"
    tracker_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "session_id": session_id,
        "stage": stage,
        "stage_index": STAGE_NAMES.index(stage) + 1,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Append to the session-stage log
    log_file = tracker_dir / f"{session_id[:32]}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Write a latest-stage pointer for re-engagement
    latest = tracker_dir / f"{session_id[:32]}_latest.json"
    latest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("Stage marked: %s (session=%s)", stage, session_id)

    return {
        "status": "ok",
        "stage": stage,
        "stage_number": STAGE_NAMES.index(stage) + 1,
        "total_stages": len(STAGE_NAMES),
        "all_completed": STAGE_NAMES.index(stage) == len(STAGE_NAMES) - 1,
    }


mark_stage_tool = FunctionTool(mark_screening_stage)