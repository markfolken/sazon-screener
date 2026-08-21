"""
Save screening results tool for sazon-screener.

Stores completed candidate screening results as timestamped JSON files
under the agent's data/screenings/ directory.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from google.adk.tools import FunctionTool, ToolContext

from ..config.paths import _PKG

logger = logging.getLogger(__name__)

CITIES = [
    "madrid", "barcelona", "valencia", "seville", "sevilla",
    "mexico city", "guadalajara", "monterrey",
]
AVAILABILITY = ["full-time", "part-time", "weekends"]
SCHEDULE = ["morning", "afternoon", "evening", "flexible"]


def _screenings_dir() -> Path:
    """Return the data/screenings/ directory under the agent root."""
    return _PKG.parent / "data" / "screenings"


def save_screening_result(
    full_name: str,
    has_drivers_license: bool,
    city: str,
    availability: str,
    preferred_schedule: str,
    delivery_experience_years: Optional[float],
    delivery_platform: Optional[str],
    start_date: str,
    disqualified: bool = False,
    disqualification_reason: Optional[str] = None,
    language: str = "es",
    tool_context: ToolContext = None,
) -> dict:
    """Save a completed screening result to disk as JSON.

    Creates the data/screenings/ directory under the agent's root and
    writes a timestamped JSON file with the full screening record.

    Args:
        full_name: The candidate's full name.
        has_drivers_license: Whether the candidate holds a valid driver's license.
        city: The candidate's city (must be in the supported cities list).
        availability: Availability type — full-time, part-time, or weekends.
        preferred_schedule: Preferred shift — morning, afternoon, evening, or flexible.
        delivery_experience_years: Years of delivery experience (can be None).
        delivery_platform: Previous delivery platform (Glovo, Uber Eats, etc.) or None.
        start_date: When the candidate can start (date string).
        disqualified: Whether the candidate was disqualified during screening.
        disqualification_reason: Why the candidate was disqualified, if applicable.
        language: The language the screening was conducted in (es or en).

    Returns:
        A dict with ``success``, ``file_path``, and a human-readable ``summary``.
    """
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in full_name.lower().replace(" ", "_"))

    record = {
        "screened_at": now.isoformat(),
        "full_name": full_name,
        "has_drivers_license": has_drivers_license,
        "city": city,
        "availability": availability,
        "preferred_schedule": preferred_schedule,
        "delivery_experience_years": delivery_experience_years,
        "delivery_platform": delivery_platform,
        "start_date": start_date,
        "disqualified": disqualified,
        "disqualification_reason": disqualification_reason,
        "language": language,
    }

    out_dir = _screenings_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{timestamp}_{safe_name}.json"
    file_path = out_dir / filename

    file_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    status = "DESCALIFICADO" if disqualified else "APTO"
    summary = (
        f"{full_name} — {status}\n"
        f"Ciudad: {city} | Disponibilidad: {availability} | Horario: {preferred_schedule}\n"
        f"Experiencia: {delivery_experience_years or 0:.1f} años{' en ' + delivery_platform if delivery_platform else ''}\n"
        f"Inicio: {start_date}"
    )
    if disqualification_reason:
        summary += f"\nMotivo: {disqualification_reason}"

    logger.info("Screening saved: %s → %s", full_name, filename)

    return {
        "success": True,
        "file_path": str(file_path),
        "summary": summary,
    }


save_screening_tool = FunctionTool(save_screening_result)