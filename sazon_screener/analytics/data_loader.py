"""Data loaders for screening analytics.

Reads from data/screenings/, data/stage_tracker/, and traces/.
All paths are optional — missing directories degrade gracefully.
"""

import json
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Default paths ────────────────────────────────────────────────────

_PKG = Path(__file__).resolve().parent.parent.parent
_SCREENINGS_DIR = _PKG / "data" / "screenings"
_STAGE_DIR = _PKG / "data" / "stage_tracker"
_TRACES_DIR = _PKG / "traces"


# ── Loaders ───────────────────────────────────────────────────────────


def load_screenings(path: Optional[str] = None) -> list[dict]:
    """Load all screening records from JSON files."""
    p = Path(path) if path else _SCREENINGS_DIR
    if not p.is_dir():
        logger.info("Screenings dir not found: %s", p)
        return []
    results = []
    for f in sorted(p.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_file"] = f.name
            data["_date"] = f.stem[:8] if len(f.stem) >= 8 else f.stem
            results.append(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping %s: %s", f.name, e)
    return results


def load_stage_tracker(path: Optional[str] = None) -> list[dict]:
    """Load all stage tracking records from JSONL files."""
    p = Path(path) if path else _STAGE_DIR
    if not p.is_dir():
        logger.info("Stage tracker dir not found: %s", p)
        return []
    results = []
    for f in sorted(p.glob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if line.strip():
                    results.append(json.loads(line))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping %s: %s", f.name, e)
    return results


def load_traces(path: Optional[str] = None) -> list[dict]:
    """Load trace events from JSONL files."""
    p = Path(path) if path else _TRACES_DIR
    if not p.is_dir():
        logger.info("Traces dir not found: %s", p)
        return []
    results = []
    for f in sorted(p.glob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if line.strip():
                    results.append(json.loads(line))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping %s: %s", f.name, e)
    return results


# ── Computations ──────────────────────────────────────────────────────


def compute_funnel(stages: list[dict]) -> list[dict]:
    """Compute drop-off funnel from stage tracker data.

    Returns list of {stage, stage_index, reached, pct} ordered by stage.
    """
    if not stages:
        return []
    # Group by session, count unique stage completions per session
    session_stages: dict[str, set[int]] = defaultdict(set)
    for s in stages:
        sid = s.get("session_id", "unknown")
        idx = s.get("stage_index", 0)
        if idx:
            session_stages[sid].add(idx)

    total_sessions = len(session_stages) if session_stages else 1
    stage_names_by_idx = {}
    for s in stages:
        idx = s.get("stage_index", 0)
        name = s.get("stage", f"stage_{idx}")
        if idx and idx not in stage_names_by_idx:
            stage_names_by_idx[idx] = name

    funnel = []
    for idx in sorted(stage_names_by_idx):
        reached = sum(1 for stages in session_stages.values() if idx in stages)
        funnel.append({
            "stage": stage_names_by_idx[idx],
            "stage_index": idx,
            "reached": reached,
            "pct": round(reached / total_sessions * 100, 1),
        })
    return funnel


def compute_city_dist(screenings: list[dict]) -> list[dict]:
    """City distribution from screening records."""
    counts: Counter[str] = Counter()
    for s in screenings:
        city = (s.get("city") or "unknown").strip()
        counts[city] += 1
    total = sum(counts.values()) or 1
    return [
        {"city": city, "count": c, "pct": round(c / total * 100, 1)}
        for city, c in sorted(counts.items(), key=lambda x: -x[1])
    ]


def compute_daily_trends(screenings: list[dict]) -> list[dict]:
    """Daily screening counts."""
    counts: Counter[str] = Counter()
    for s in screenings:
        date = s.get("_date", "")
        if len(date) == 8:
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        elif not date:
            continue
        counts[date] += 1
    return [
        {"date": d, "count": c}
        for d, c in sorted(counts.items())
    ]


def compute_qualification_stats(screenings: list[dict]) -> dict:
    """Qualified vs disqualified breakdown."""
    total = len(screenings)
    disq = sum(1 for s in screenings if s.get("disqualified"))
    qual = total - disq
    reasons: Counter[str] = Counter()
    for s in screenings:
        if s.get("disqualified"):
            r = s.get("disqualification_reason") or "unknown"
            reasons[r] += 1
    return {
        "total": total,
        "qualified": qual,
        "disqualified": disq,
        "qual_pct": round(qual / total * 100, 1) if total else 0,
        "disq_pct": round(disq / total * 100, 1) if total else 0,
        "disq_by_reason": [
            {"reason": r, "count": c}
            for r, c in sorted(reasons.items(), key=lambda x: -x[1])
        ],
    }


def compute_language_split(screenings: list[dict]) -> list[dict]:
    """ES vs EN split."""
    counts: Counter[str] = Counter()
    for s in screenings:
        lang = (s.get("language") or "es").strip()
        counts[lang] += 1
    total = sum(counts.values()) or 1
    return [
        {"language": lang, "count": c, "pct": round(c / total * 100, 1)}
        for lang, c in sorted(counts.items(), key=lambda x: -x[1])
    ]


def compute_trace_stats(traces: list[dict]) -> dict:
    """Aggregate stats from trace events."""
    if not traces:
        return {"total_events": 0, "total_llm_calls": 0, "total_tokens": 0, "estimated_cost": 0.0}

    llm_calls = sum(1 for t in traces if t.get("event") in ("llm_response",))
    total_tokens = sum(
        (t.get("prompt_tokens") or 0) + (t.get("completion_tokens") or 0)
        for t in traces
        if t.get("event") == "llm_response"
    )

    # Rough cost estimate: $0.375/M input, $1.50/M output (gemini-3.7-flash)
    total_input = sum(t.get("prompt_tokens") or 0 for t in traces if t.get("event") == "llm_response")
    total_output = sum(t.get("completion_tokens") or 0 for t in traces if t.get("event") == "llm_response")
    cost = (total_input / 1_000_000 * 0.375) + (total_output / 1_000_000 * 1.50)

    runs = set()
    for t in traces:
        sid = t.get("session_id") or t.get("trace_id")
        if sid:
            runs.add(sid)

    return {
        "total_events": len(traces),
        "total_llm_calls": llm_calls,
        "total_tokens": total_tokens,
        "estimated_cost": round(cost, 4),
        "unique_runs": len(runs),
    }


# ── Aggregate ─────────────────────────────────────────────────────────


def compute_all(paths: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Load everything and compute all metrics.

    Args:
        paths: optional dict with keys 'screenings', 'stage_tracker', 'traces'

    Returns:
        dict with all metrics
    """
    p = paths or {}
    screenings = load_screenings(p.get("screenings"))
    stages = load_stage_tracker(p.get("stage_tracker"))
    traces = load_traces(p.get("traces"))

    return {
        "screenings": screenings,
        "stages": stages,
        "traces": traces,
        "funnel": compute_funnel(stages),
        "city_dist": compute_city_dist(screenings),
        "daily_trends": compute_daily_trends(screenings),
        "qual_stats": compute_qualification_stats(screenings),
        "lang_split": compute_language_split(screenings),
        "trace_stats": compute_trace_stats(traces),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }