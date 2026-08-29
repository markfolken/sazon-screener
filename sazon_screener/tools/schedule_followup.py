"""Schedule a follow-up for a candidate who goes quiet mid-conversation.

Thin wrapper over the cronjob tool that makes it natural for the LLM
to use: pass candidate_name, hours (when to follow up), and a short note.
"""

import logging
from datetime import datetime, timezone

from google.adk.tools import FunctionTool

from ..cron.service import get_service

logger = logging.getLogger(__name__)


def schedule_followup(
    candidate_name: str,
    hours: int = 48,
    note: str = "",
    tool_context=None,
) -> dict:
    """Schedule a reminder to follow up with a candidate who paused mid-screening.

    Call this when a candidate says they'll be away ("viajo manana", "te
    escribo luego", etc.) or goes silent. The system will re-engage them
    after the given number of hours.

    Args:
        candidate_name: The candidate's full name (for the job name).
        hours: Hours from now to follow up (default 48).
        note: Short context note about why the follow-up is scheduled
              (e.g. "candidate going on trip, resume at availability stage").

    Returns:
        Dict with the created job info or an error message.
    """
    try:
        svc = get_service()
    except Exception as exc:
        logger.warning("Cron service not available: %s", exc)
        return {
            "status": "error",
            "message": "Scheduler not available in this environment. The follow-up was noted but won't fire automatically.",
            "note": note,
        }

    safe_name = candidate_name.replace(" ", "_")[:30]
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    job_name = f"followup_{safe_name}_{today}"
    prompt = (
        f"Follow-up with candidate {candidate_name}. "
        f"Context: {note}. "
        f"Acknowledge the pause and resume the screening exactly where it left off. "
        f"Read the latest stage from data/stage_tracker/ to determine where to resume. "
        f"Do NOT repeat stages already completed."
    )

    try:
        job = svc.create_job(
            name=job_name,
            prompt=prompt,
            schedule=f"{hours}h",
            delivery="origin",
        )
        logger.info("Follow-up scheduled: %s in %dh -- %s", candidate_name, hours, note)
        return {
            "status": "ok",
            "job_name": job_name,
            "scheduled_in_hours": hours,
            "note": note,
            "candidate": candidate_name,
        }
    except Exception as exc:
        logger.error("Failed to schedule follow-up: %s", exc)
        return {
            "status": "error",
            "message": f"Failed to schedule: {exc}",
        }


schedule_followup_tool = FunctionTool(schedule_followup)