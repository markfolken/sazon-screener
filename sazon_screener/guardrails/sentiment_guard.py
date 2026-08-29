"""Sentiment guard — detects frustrated/confused candidates and flags escalation.

Hooks into ADK's before_tool_callback chain. Uses heuristic pattern matching
(no ML, no API calls). Stores sentiment state in ToolContext so the agent
can adjust tone or suggest human handoff.
"""

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Signal patterns ───────────────────────────────────────────────────

_ANNOYED_PATTERNS = [
    r"\bya\s+te\s+he\s+dich[ao]\b",
    r"\botra\s+vez\b",
    r"\bno\s+me\s+hagas\s+perder\s+(el\s+)?tiempo\b",
    r"\bestoy\s+(hart[ao]|cansad[ao])\b",
    r"\bme\s+tien[es]\s+hart[ao]\b",
    r"\b(ya\s+)?dije\b",
    r"\bdej[ae]\s+de\s+preguntar\b",
    r"\b(esto\s+)?es\s+una\s+p[eé]rdida\s+de\s+tiempo\b",
    r"\bno\s+(quiero|voy\s+a)\s+responder\b",
]

_CONFUSION_PATTERNS = [
    r"\bno\s+entien[dt]o\b",
    r"\bno\s+s[eé]\b",
    r"\bqu[eé]\s+(significa|es|quieres decir)\b",
    r"\bno\s+te\s+entiendo\b",
    r"\brep[ií]tem[ea]\b",
    r"\bpuedes\s+explicar\b",
    r"\bconfus[oó]\b",
    r"\bno\s+est[aá]\s+claro\b",
    r"\ba\s+q[uú]\s+te\s+refieres\b",
    r"\bno\s+comprendo\b",
    r"\bno\s+te\s+entiendo\b",
    r"\bm[eá]s\s+explico\b",
]

_ESCALATE_PATTERNS = [
    r"\btont[oai]\b",
    r"\bin[uú]til\b",
    r"\bidiot[ao]\b",
    r"\best[úu]pid[oao]\b",
    r"\bimb[eé]cil\b",
    r"\bhijo\s+de\s+puta\b",
    r"\bvi[oó]late\b",
    r"\bdenuncio\b",
    r"\babogad[oai]\b",
    r"\bdemanda\b",
    r"\bqueja\s+formal\b",
]

_NEGATIVE_WORDS = {
    "no", "nunca", "jamás", "nadie", "nada", "mal", "peor",
    "horrible", "terrible", "pésimo", "decepcionante",
}


def _count_caps(text: str) -> float:
    """Ratio of uppercase letters in alphabetic chars. Returns 0.0 for empty."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)


def _count_punctuation_excess(text: str) -> int:
    """Count repeated ! and ? sequences (e.g. '!!!' counts as 1, '?!?' as 2)."""
    count = 0
    for m in re.finditer(r"[!?]{2,}", text):
        count += 1
    for m in re.finditer(r"[!?][?!]", text):
        count += 1
    return count


def detect_sentiment(user_message: str) -> dict[str, Any]:
    """Detect sentiment level in a user message.

    Returns:
        dict with keys:
          level: "neutral" | "annoyed" | "confused" | "escalate"
          signals: list of matching signal names
          score: 0.0 to 1.0 confidence
    """
    if not user_message or not user_message.strip():
        return {"level": "neutral", "signals": [], "score": 0.0}

    text = user_message.strip()
    signals: list[str] = []
    lower = text.lower()

    # 1. Check escalate patterns first (highest priority)
    for pat in _ESCALATE_PATTERNS:
        if re.search(pat, lower):
            signals.append(f"escalate: insult threat detected")

    # 2. Check annoyed patterns
    for pat in _ANNOYED_PATTERNS:
        if re.search(pat, lower):
            signals.append(f"annoyed: {pat}")

    # 3. Check confusion patterns
    for pat in _CONFUSION_PATTERNS:
        if re.search(pat, lower):
            signals.append(f"confused: {pat}")

    # 4. Stylistic signals
    caps_ratio = _count_caps(text)
    if caps_ratio > 0.6 and len(text) > 20:
        signals.append("annoyed: caps emphasis")

    punct_excess = _count_punctuation_excess(text)
    if punct_excess >= 1:
        signals.append(f"annoyed: repeated punctuation")

    neg_words = sum(1 for w in text.lower().split() if w.strip(".,!?;:") in _NEGATIVE_WORDS)
    if neg_words >= 3:
        signals.append("annoyed: negative word density")

    # 5. Score calculation
    score = 0.0
    for s in signals:
        if "escalate" in s and "insult" in s:
            score += 0.35
        elif "insult" in s:
            score += 0.4
        elif "annoyed" in s:
            score += 0.12
        elif "confused" in s:
            score += 0.15

    score = min(score, 1.0)

    # 6. Level determination
    level = "neutral"
    if score >= 0.7:
        level = "escalate"
    elif score >= 0.35:
        level = "annoyed"
    elif any("confused" in s for s in signals):
        level = "confused"

    # If any escalate pattern matched, overrule
    if any("insult" in s for s in signals):
        level = "escalate"

    return {
        "level": level,
        "signals": signals,
        "score": round(score, 2),
    }


def sentiment_callback(tool=None, args=None, tool_context=None) -> Optional[dict]:
    """ADK before_tool_callback: detect sentiment and store in state.

    Handles both ADK's 3-arg signature (tool, args, tool_context) and
    a simplified 2-arg shape (tool_context, user_message). Always returns
    None so it never blocks tool execution — it only observes and stores.
    """
    ctx = tool_context
    if ctx is None:
        return None

    # Try to get the latest user message from the conversation history
    user_text = ""
    try:
        if hasattr(ctx, "session") and ctx.session:
            history = list(ctx.session.history)
            for entry in reversed(history):
                if hasattr(entry, "role") and entry.author == "user":
                    user_text = entry.content or ""
                    break
                if hasattr(entry, "author") and entry.author == "user":
                    user_text = entry.text or ""
                    break
    except Exception:
        pass

    if not user_text:
        return None

    # Run detection
    result = detect_sentiment(user_text)

    # Store in tool context state so agent can read it
    if result["level"] != "neutral":
        logger.info(
            "Sentiment: %s (score=%.2f, signals=%s)",
            result["level"], result["score"], result["signals"]
        )

    try:
        if hasattr(ctx, "state") and ctx.state is not None:
            ctx.state["sentiment"] = result
    except Exception:
        pass

    # If escalate, log a warning
    if result["level"] == "escalate":
        logger.warning(
            "ESCALATION: sentiment=%.2f, signals=%s, msg=%r",
            result["score"], result["signals"], user_text[:100]
        )

    return None  # Never block tools