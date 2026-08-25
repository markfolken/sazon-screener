# Sazón Screener

**AI-Powered Candidate Screening Agent for Grupo Sazón**

Grupo Sazón is a restaurant chain hiring delivery drivers across Spain and Mexico — 45 locations, roughly 200 applications a week. Screening those by hand is a recruiter reading the same seven answers two hundred times, in two languages, and typing them into a spreadsheet.

This agent does the first pass. It runs a warm, messaging-style interview in Spanish (auto-switching to English when the candidate does), validates each answer as it goes, exits early and politely when a candidate can't qualify, and writes a structured JSON record that a human recruiter — or an ATS — can act on.

Built for the **Orbio FDE Technical Assignment** on Google ADK, scaffolded with the [Nuvel](https://github.com/Folken2/meta-agent) CLI.

---

## Architecture Overview

### The stack

```
   Candidate (Telegram / ADK web UI / HTTP)
                 │
                 ▼
   FastAPI  ──  run_adk.py
   · API-key middleware      · /health, /debug-info
   · Telegram gateway        · request-ID tracing
                 │
                 ▼
   Google ADK  ──  LlmAgent (sazon_screener/agent.py)
   · multi-turn session state   · FunctionTool schemas
   · SkillToolset (hot-reload)  · before/after callbacks
                 │
                 ▼
   LiteLlm  ──  google/gemini-3.7-flash  (via OpenRouter)
   · 3 retries on transient errors  · 120s timeout
                 │
                 ▼
   data/screenings/<timestamp>_<name>.json
```

ADK owns the conversation: session state, tool dispatch, the plugin lifecycle. LiteLLM is the model-adapter layer, which is what lets the model be a single env var instead of a code change. OpenRouter is the gateway — one API key, any model behind it.

### Key components

| File | Role |
|---|---|
| `sazon_screener/agent.py` | The `LlmAgent` — model, instruction provider, tool list, guardrail callbacks. Also defines `LazySkillToolset`, which re-reads `SKILL.md` files on mtime change so skills can be edited without restarting the process. |
| `sazon_screener/prompt/instructions.py` | Identity prompt ("Carlos") — tone, messaging style, language-switch rule. Screening flow lives in `SKILL.md`. |
| `sazon_screener/tools/save_screening.py` | `FunctionTool` wrapping `save_screening_result(...)`. Typed signature → ADK derives the schema the model must fill. Writes one timestamped JSON file per screening. |
| `sazon_screener/config/llm.py` | `FAST_MODEL` / `REASONING_MODEL` as `LiteLlm` instances pointed at OpenRouter, plus retry and timeout config. |
| `run_adk.py` | FastAPI entrypoint. Mounts the ADK app, API-key auth, health checks, and the Telegram gateway router. |

### The screening flow

Seven data-collection stages, two of which are hard gates:

| # | Stage | Field | Validation | Gate |
|---|---|---|---|---|
| 1 | Nombre | `full_name` | Non-empty, first + last | — |
| 2 | Licencia | `has_drivers_license` | Yes/No — *No* ends the interview | **Yes** |
| 3 | Ciudad | `city` | Must be a service area; normalized to canonical name | **Yes** |
| 4 | Disponibilidad | `availability` | `full-time` / `part-time` / `weekends` | — |
| 5 | Horario | `preferred_schedule` | `morning` / `afternoon` / `evening` / `flexible` | — |
| 6 | Experiencia | `delivery_experience_years`, `delivery_platform` | Years as float (`0.5` = 6 months); platform nullable | — |
| 7 | Fecha inicio | `start_date` | Free text — "mañana", "el lunes", "inmediato" | — |

Wrapped by a greeting up front and a summary-and-confirm at the end.

**Service areas:** Madrid, Barcelona, Valencia, Sevilla/Seville · Ciudad de México (CDMX/DF), Guadalajara, Monterrey.

Both gates still call `save_screening_result` with `disqualified=True` and a reason. A rejected candidate is data — it tells you how much of your funnel is out-of-area, which is the kind of thing that changes where you open location 46.

### Output record

```json
{
  "screened_at": "2026-08-21T17:30:00+00:00",
  "full_name": "María López García",
  "has_drivers_license": true,
  "city": "Madrid",
  "availability": "full-time",
  "preferred_schedule": "morning",
  "delivery_experience_years": 1.5,
  "delivery_platform": "Glovo",
  "start_date": "1 de septiembre",
  "disqualified": false,
  "disqualification_reason": null,
  "language": "es"
}
```

Written to `data/screenings/20260821_173000_maría_lópez_garcía.json`.

---

## Setup

**Requirements:** Python 3.11+, an OpenRouter API key.

```bash
cd generated-agents/sazon-screener

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # add your OPENROUTER_API_KEY
$EDITOR .env

DEV_MODE=true python run_adk.py
```

The server comes up on `http://localhost:8000`. `DEV_MODE=true` uses in-memory sessions — no database needed.

**For an interactive chat UI**, use the ADK web UI instead (`run_adk.py` mounts the API with `web=False`):

```bash
DEV_MODE=true adk web .
```

Then pick `sazon_screener` from the agent dropdown and start typing.

**Useful endpoints:** `GET /health` (public), `POST /run_sse` (streaming, auth required). Set `API_KEY` in `.env` to require a bearer token; leave it unset in dev and the server warns you that endpoints are open.

**Telegram:** set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET`, expose the host, and point your bot's webhook at `/gateways/telegram`.

---

## Key Design Decisions

**Why Google ADK?** The screening problem is *stateful multi-turn conversation with a structured artifact at the end* — exactly ADK's shape. Session state across turns is handled by the framework, so the agent never re-asks for the city it already has. `FunctionTool` derives its schema from the Python signature, so `save_screening_result`'s typed parameters *are* the output contract — the model cannot save a record missing `has_drivers_license`. And the plugin/callback lifecycle means cost tracking, tracing, and guardrails live outside agent code. Rolling this by hand means rebuilding all three.

**Why `gemini-3.7-flash`?** Spanish quality first — this agent is Spanish-first with English code-switching, and Gemini handles both natively rather than translating through English. Then cost: at ~$0.375/M input tokens, 200 screenings a week is rounding error, which matters when the whole pitch is "cheaper than a recruiter's afternoon." Then latency — a candidate on Telegram expects a reply in seconds. Behind OpenRouter, swapping to Claude or Llama is an env-var edit, not a dependency change.

**Why JSON files for storage?** Because the right amount of infrastructure for a screening demo is none. Files are auditable (`cat` the record), trivially diffable, easy to `jq` into aggregate stats, and they make the output contract *visible* instead of hidden behind a connection string. It's deliberately the wrong choice at scale: 200/week is ~10k records a year, at which point you want Postgres for queryability or a direct ATS push. The tool boundary is where that swap happens — `save_screening_result` changes, nothing else does.

---

## Sample Conversation — happy path, qualified candidate

```
🤖  ¡Hola! Soy el asistente de selección de Grupo Sazón 🛵. Estamos buscando
    repartidores para nuestra cadena de restaurantes. ¿Me puedes dar tu nombre
    completo?

👤  Hola! Soy María López García

🤖  ¡Encantado, María! ¿Tienes licencia de conducir válida?

👤  Sí, tengo el carnet B y también el A2 para moto

🤖  Perfecto 👍 ¿En qué ciudad vives?

👤  En Madrid, zona de Chamberí

🤖  ¡Genial, tenemos varios restaurantes por ahí! ¿Qué disponibilidad tienes?
    • Tiempo completo  • Medio tiempo  • Solo fines de semana

👤  Tiempo completo

🤖  ¿Y qué horario prefieres?
    • Mañana  • Tarde  • Noche  • Flexible

👤  Prefiero mañanas, tengo clases por la tarde

🤖  Anotado, mañanas 🕐 ¿Tienes experiencia como repartidora? Cuéntame cuánto
    tiempo y en qué plataformas has trabajado.

👤  Sí, estuve año y medio en Glovo

👤  Por cierto, ¿cómo va el tema del salario?

🤖  Es competitivo según el mercado local, más propinas — y las propinas son
    100% para el repartidor. Los detalles se ven en la entrevista con RRHH 😊
    Volviendo a lo nuestro: ¿cuándo podrías empezar?

👤  El 1 de septiembre

🤖  Perfecto, déjame confirmar:
    📋 María López García · 🚗 Licencia: Sí · 📍 Madrid
    ⏰ Tiempo completo · 🕐 Mañana · 📦 1.5 años en Glovo · 📅 1 de septiembre
    ¿Es correcto?

👤  Sí, todo correcto

    ┌─ tool call ─────────────────────────────────────────────┐
    │ save_screening_result(                                  │
    │   full_name="María López García",                       │
    │   has_drivers_license=True, city="Madrid",              │
    │   availability="full-time", preferred_schedule="morning",│
    │   delivery_experience_years=1.5,                        │
    │   delivery_platform="Glovo",                            │
    │   start_date="1 de septiembre",                         │
    │   disqualified=False, language="es")                    │
    │ → data/screenings/20260821_173000_maría_lópez_garcía.json│
    └─────────────────────────────────────────────────────────┘

🤖  ¡Listo! Tu solicitud ha sido registrada. El equipo de recursos humanos
    revisará tu perfil y se pondrá en contacto contigo pronto. ¡Gracias! 🛵✨
```

Note the salary question mid-flow: the agent answers from the FAQ in two lines and returns to the interview without losing its place. That's session state doing its job.

---

## Bonus Features (shipped)

- **Multi-language code-switching** — Spanish by default; if the candidate writes in English the agent switches for the rest of the interview and tags the record `language="en"`. One prompt, detected at runtime.
- **Structured screening analytics** — every screening, completed or disqualified, lands in `data/screenings/` as flat JSON. `jq -s 'group_by(.city)' data/screenings/*.json` is a funnel report.
- **Skill hot-reload** — `LazySkillToolset` watches `SKILL.md` mtimes and rebuilds the toolset when they change. No restart.
- **Telegram gateway** — wired at `/gateways/telegram`.
- **Guardrails + cost tracking** — exfil and command guards on every tool call; the cost-guard plugin prices each turn against `plugins/pricing.json`.
- **Record/replay regression tests** — golden conversations captured with `RECORD=true`, replayed free and deterministically in CI.

---

## Directory Layout

```
sazon-screener/
├── run_adk.py                       # FastAPI entrypoint — auth, health, Telegram router
├── .env.example                     # OPENROUTER_API_KEY, DEV_MODE, TELEGRAM_*, …
│
├── sazon_screener/
│   ├── agent.py                     # ★ LlmAgent + LazySkillToolset + guardrail callbacks
│   ├── prompt/instructions.py       # ★ Identity, tone, language rules
│   ├── tools/save_screening.py      # ★ FunctionTool → data/screenings/*.json
│   ├── skills/default/SKILL.md      # ★ Screening flow — stages, gates, output schema
│   ├── config/llm.py                # ★ LiteLlm → OpenRouter → gemini-3.7-flash
│   ├── gateways/                    # Telegram webhook, commands, voice transcription
│   ├── plugins/                     # Cost guard, trace, context window, cache, …
│   └── guardrails/                  # exfil_guard, command_guard
│
├── tests/test_agent.py              # Record/replay golden conversation tests
└── data/screenings/                 # ← Output. Created on first completed screening.
```

★ = the five files worth reading first.

---

## Testing

```bash
# Replay mode — deterministic, no LLM calls, no cost
python -m pytest tests/ -v

# Record a new golden conversation (hits the real model)
RECORD=true python -m pytest tests/test_agent.py -v -k test_golden
```

Recordings live in `tests/recordings/` and are committed. Tests skip cleanly with a message if a recording is missing rather than silently passing.

---

Built by Mark Folken for the Orbio FDE Technical Assignment · Scaffolded with [Nuvel](https://github.com/Folken2/meta-agent)
