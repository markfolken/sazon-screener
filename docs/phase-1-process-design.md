# Phase 1 — Process Design Document

## AI Candidate Screening Agent for Grupo Sazón

---

## 1. Overview

Grupo Sazón is a restaurant chain with 45 locations across Spain and Mexico, receiving ~200 delivery driver applications per week. Currently 3 recruiters handle manual phone screening (~15 calls/day each), with 60% of calls unanswered and 80% of recruiter time spent on unqualified candidates.

The AI screening agent conducts structured conversations via messaging, collecting and validating 7 screening fields, disqualifying unqualified candidates early, and passing qualified candidates to recruiters with a structured summary.

---

## 2. Conversation Stages

The screening follows a fixed 7-stage sequence. Each stage collects one field, validates it, and gates progress. Stages are strictly sequential — no stage is skipped unless the candidate is disqualified.

| # | Stage | Field | Type | Gate? | Validation |
|---|-------|-------|------|-------|------------|
| 1 | Greeting | Full name | Free text | ❌ | Non-empty, natural name format |
| 2 | License | Driver's license | Yes/No | **✅** | "No" → immediate disqualification |
| 3 | City | City/zone | Free text | **✅** | Must match a service area (ES: Madrid, Barcelona, Valencia, Seville; MX: Mexico City/CDMX/DF, Guadalajara, Monterrey) |
| 4 | Availability | Work schedule | Enum | ❌ | full-time, part-time, or weekends |
| 5 | Schedule | Preferred shift | Enum | ❌ | morning, afternoon, evening, or flexible |
| 6 | Experience | Delivery experience | Structured | ❌ | Years (optional number) + platform (optional: Glovo, Uber Eats, Just Eat, Deliveroo, etc.) |
| 7 | Start date | Availability date | Free text | ❌ | Any reasonable date expression |

### Branching Logic

```
Greeting (name)
  │
  ▼
License? ── No ──► Farewell + Save (disqualified=true)
  │ Yes
  ▼
City? ── Outside service area ──► Farewell + Save (disqualified=true)
  │ In area
  ▼
Availability? → Schedule? → Experience? → Start date?
  │
  ▼
Summary + Confirmation
  │
  ▼
Save (disqualified=false) + Farewell
```

---

## 3. Data Fields and Validation Rules

| Field | Schema key | Type | Required | Validation |
|-------|-----------|------|----------|------------|
| Full name | `full_name` | string | ✅ | Non-empty text. Single-word names accepted after 3 attempts. |
| Driver's license | `has_drivers_license` | boolean | ✅ | Must be clear yes/no. Ambiguous → re-prompt with options. |
| City | `city` | string | ✅ | Must match or alias one of the 7 service cities. |
| Availability | `availability` | enum | ✅ | One of: `full-time`, `part-time`, `weekends` |
| Preferred schedule | `preferred_schedule` | enum | ✅ | One of: `morning`, `afternoon`, `evening`, `flexible` |
| Experience years | `delivery_experience_years` | float or null | ❌ | Optional. If candidate can't provide after 2 attempts, set null and continue. |
| Platform | `delivery_platform` | string or null | ❌ | Optional. Free text. |
| Start date | `start_date` | string | ✅ | Free text date expression. |
| Disqualified | `disqualified` | boolean | ✅ | Set by gate outcome. |
| Reason | `disqualification_reason` | string or null | ✅ If disqualified | Explain why (no license, outside area). |
| Language | `language` | enum | ✅ | `es` or `en` (or other ISO code). Determined by conversation. |

### Output format

The agent produces a structured JSON object:

```json
{
  "screened_at": "2026-08-21T17:30:00+00:00",
  "full_name": "Isabel Núñez Peña",
  "has_drivers_license": true,
  "city": "Barcelona",
  "availability": "full-time",
  "preferred_schedule": "morning",
  "delivery_experience_years": 2.5,
  "delivery_platform": "Glovo",
  "start_date": "next Monday",
  "disqualified": false,
  "disqualification_reason": null,
  "language": "es"
}
```

---

## 4. Edge Cases

### Candidate stops responding mid-conversation

- After each candidate message, wait for a response. If none within ~60 seconds, send one follow-up.
- After 3 follow-ups with no response, send a "ping me when you're ready" message and stop. Do not save.
- If the candidate announced they'd be away ("viajo mañana, te escribo luego"), schedule a follow-up in 48h (or their timeframe + 24h margin). On return, acknowledge the pause naturally and continue exactly where the conversation left off.
- Maximum 2 automatic re-engagement attempts; after that, the candidate is closed.

### Invalid or ambiguous answers

| Scenario | Handling |
|----------|----------|
| License answer is unclear ("maybe", "I think so") | Re-prompt with concrete options: "¿Tienes carnet de conducir vigente? Sí o No." |
| City name is not in service areas | Reject gracefully: "Actualmente solo tenemos vacantes en [list of cities]." If candidate insists, disqualify. |
| Availability is vague ("whenever") | Offer the three options: tiempo completo, medio turno, fines de semana. |
| Name has special characters (apostrophe, hyphen, accent) | Accept as-is. Do not sanitize. |
| Name is a single word ("Pedro") | After 3 genuine attempts to get a surname, accept the single word and continue. |
| Candidate gives unrelated answers | Redirect back to the last question politely. Do not advance without a valid answer. |

### Language switching (Spanish ↔ English)

- Default language: **Spanish**.
- If the candidate writes in English (or any other language), respond in that language from the next message — no announcement, no mixing.
- Maintain the language until the candidate switches again.
- Save `language` field accordingly (`"es"`, `"en"`, or other ISO code).

### Inappropriate or off-topic input

| Type | Handling |
|------|----------|
| Aggressive/offensive language | Professional redirection. After 3 incidents, terminate the conversation without saving. |
| Job questions mid-flow | Answer briefly from FAQ (salary, hours, benefits, vehicle, delivery zones, contract) in 1-2 sentences, then immediately return to the pending question. |
| Personal questions ("are you a real person?") | One-sentence honest response ("Soy el asistente virtual de selección de Grupo Sazón"), then continue. |
| Irrelevant topics | Redirect with one sentence and return to the interview. |

---

## 5. Qualified vs. Disqualified Paths

### Disqualified outcomes

| Condition | Action | Data |
|----------|--------|------|
| Candidate has no driver's license | Polite farewell. Save with `disqualified: true, reason: "No cuenta con carné/licencia de conducir"` | Collect name first, then disqualify at stage 2 |
| Candidate city is outside service areas | Polite farewell. Save with `disqualified: true, reason: "Su ciudad no está en nuestras zonas de reparto"` | Collect license first, then disqualify at stage 3 |
| Inappropriate behavior (3 strikes) | Terminate conversation. **Do not save** any data. | No record created |

### Qualified outcome

Once all 7 stages are completed and validated:

1. Present a brief summary to the candidate with the key data points collected
2. Ask for confirmation ("¿Todo correcto?")
3. On confirmation → save with `disqualified: false`
4. Thank them and inform them the team will follow up

---

## 6. Message Tone and Length Guidelines

The agent communicates via messaging (WhatsApp-style), not email.

| Rule | Detail |
|------|--------|
| Message length | 1-3 short sentences per message |
| Pacing | One question per message. Never two. |
| Emojis | 0-2 per message, natural and moderate |
| Tone | Warm, professional, conversational |
| Formatting | No long paragraphs, numbered lists, or formal letter structure |
| Acknowledgment | Briefly acknowledge each answer ("perfecto", "genial", "apuntado") before asking the next question |

### Example exchange

> **Agent:** ¡Hola! Soy Carlos, del equipo de selección de Grupo Sazón. Estamos buscando repartidores para nuestro equipo. ¿Cómo te llamas?
>
> **Candidate:** Me llamo Isabel Núñez Peña
>
> **Agent:** ¡Mucho gusto, Isabel! Para empezar, ¿tienes carnet de conducir vigente?
>
> **Candidate:** Sí, tengo carné B
>
> **Agent:** ¡Genial! ¿En qué ciudad te gustaría trabajar con nosotros?

---

## 7. Voice Agent Considerations

The same conversation flow applies to voice interaction. Key differences:

- Turn length: shorter utterances (voice is slower than text)
- Confirmation: verbal acknowledgments replace emojis
- Audio capture: 16kHz PCM via browser WebRTC, streamed to the ADK multimodal API
- Language detection: automatic from audio stream
- The voice client (HTML/JS) is already scaffolded in the repository

---

## 8. Bonus: Re-engagement Flow

For candidates who pause mid-conversation:

```
Silence detected (no response)
  │
  ├── 1st follow-up (60s): Natural nudge ("¿Sigues ahí?")
  │
  ├── 2nd follow-up (120s): Soft re-engagement ("Sin prisas, cuando quieras")
  │
  ├── 3rd follow-up (180s): Final ("Te dejo el link, retomamos cuando quieras")
  │
  └── Closed. No record saved.
```

If candidate explicitly says they'll return: schedule a single `schedule_followup` in 48h (or their timeframe + 24h), and on resume acknowledge the pause and continue exactly where they left off.