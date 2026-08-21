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
| `sazon_screener/prompt/instructions.py` | The system prompt. The full 9-step conversation script (greeting → 7 screening stages → summary + confirmation), tone rules, language-switch rule, edge-case handling, and the job FAQ the agent answers from. |
| `sazon_screener/tools/save_screening.py` | `FunctionTool` wrapping `save_screening_result(...)`. Typed signature → ADK derives the schema the model must fill. Writes one timestamped JSON file per screening and returns a human-readable summary. |
| `sazon_screener/config/llm.py` | `FAST_MODEL` / `REASONING_MODEL` as `LiteLlm` instances pointed at OpenRouter, plus retry and timeout config. |
| `sazon_screener/skills/default/SKILL.md` | The screening process as a progressive-disclosure skill: stage table, service areas, disqualification paths, edge-case matrix, output schema. Loaded at runtime via `SkillToolset` — the agent can read it mid-conversation. |
| `run_adk.py` | FastAPI entrypoint. Mounts the ADK app, API-key auth, health checks, and the Telegram gateway router. |
| `sazon_screener/plugins/` | Cross-cutting plugin chain — cost guard, trace writer, context-window management, resilience, caching, console logging. Applied without touching agent code. |
| `sazon_screener/guardrails/` | `exfil_guard` and `command_guard_callback`, wired as `before_tool_callback`. |

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

Wrapped by a greeting up front and a summary-and-confirm at the end, that's the 9 numbered steps in `instructions.py`.

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
# 1. Get to the project
cd generated-agents/sazon-screener

# 2. Environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure — add your OPENROUTER_API_KEY
cp .env.example .env
$EDITOR .env

# 4. Run
DEV_MODE=true python run_adk.py
```

The server comes up on `http://localhost:8000`. `DEV_MODE=true` uses in-memory sessions — no database needed to demo.

**For an interactive chat UI**, use the ADK web UI instead (`run_adk.py` mounts the API with `web=False`):

```bash
DEV_MODE=true adk web .
```

Then pick `sazon_screener` from the agent dropdown and start typing. This is the fastest way to see the agent work end to end.

**Useful endpoints:** `GET /health` (public), `POST /run_sse` (streaming, auth required). Set `API_KEY` in `.env` to require a bearer token; leave it unset in dev and the server warns you that endpoints are open.

**Telegram:** set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET`, expose the host, and point your bot's webhook at `/gateways/telegram`.

---

## Key Design Decisions

**Why Google ADK?** The screening problem is *stateful multi-turn conversation with a structured artifact at the end* — which is exactly ADK's shape. Session state across turns is handled by the framework, so the agent never re-asks for the city it already has. `FunctionTool` derives its schema from the Python signature, so `save_screening_result`'s typed parameters *are* the output contract — the model cannot save a record missing `has_drivers_license`. And the plugin/callback lifecycle means cost tracking, tracing, and guardrails live outside agent code. Rolling this by hand means rebuilding all three.

**Why `gemini-3.7-flash`?** Three reasons, in order. Spanish quality — this agent is Spanish-first with English code-switching, and Gemini handles both natively rather than translating through English. Cost — at ~$0.375/M input tokens, 200 screenings a week is rounding error, and that matters when the whole pitch is "cheaper than a recruiter's afternoon." Latency — a candidate on Telegram expects a reply in seconds, and Flash delivers. The 1M context window is more headroom than a 9-turn interview will ever use, but it means a future RAG FAQ can stuff the whole employee handbook in without retrieval tuning.

**Why a single `LlmAgent` rather than a multi-agent graph?** The flow is linear with two decision gates. A router-plus-specialists design would add hop latency, more failure modes, and cross-agent state handoff — to model a conversation that goes one direction. One agent with a well-structured prompt and one tool handles it cleanly, and stays legible to whoever maintains it. Multi-agent is the right answer when the branches are genuinely independent workloads; here they aren't. If Grupo Sazón later adds cook and waitstaff screening with different criteria, *that's* the moment to split into role-specific sub-agents behind a router.

**Why OpenRouter?** One key, any model. Switching from Gemini to Claude or Llama is an env-var edit, not a dependency change — which matters for an assignment where the evaluator may not have the same provider account. It also gives fallback routing when a provider has a bad hour, and per-key spend caps for hard cost control.

**Why JSON files for storage?** Because the right amount of infrastructure for a screening demo is none. Files are auditable (`cat` the record), trivially diffable, easy to `jq` into aggregate stats, and they make the output contract *visible* to an evaluator instead of hidden behind a connection string. It's deliberately the wrong choice at scale: 200/week is ~10k records a year, at which point you want Postgres for queryability or a direct ATS API push for pipeline integration. The tool boundary is where that swap happens — `save_screening_result` changes, nothing else does.

---

## Sample Conversations

### 1. Happy path — qualified candidate, Madrid

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
    Las opciones son:
    • Tiempo completo
    • Medio tiempo
    • Solo fines de semana

👤  Tiempo completo

🤖  ¿Y qué horario prefieres?
    • Mañana
    • Tarde
    • Noche
    • Flexible (me adapto a cualquier horario)

👤  Prefiero mañanas, tengo clases por la tarde

🤖  Anotado, mañanas 🕐 ¿Tienes experiencia como repartidora? Cuéntame cuánto
    tiempo y en qué plataformas has trabajado (Glovo, Uber Eats, Just Eat,
    Deliveroo, etc.)

👤  Sí, estuve año y medio en Glovo

👤  Por cierto, ¿cómo va el tema del salario?

🤖  Es competitivo según el mercado local, más propinas — y las propinas son
    100% para el repartidor. Los detalles se ven en la entrevista con RRHH 😊
    Volviendo a lo nuestro: ¿cuándo podrías empezar?

👤  El 1 de septiembre

🤖  Perfecto, déjame confirmar:
    📋 Nombre: María López García
    🚗 Licencia: Sí
    📍 Ciudad: Madrid
    ⏰ Disponibilidad: Tiempo completo
    🕐 Horario: Mañana
    📦 Experiencia: 1.5 años en Glovo
    📅 Inicio: 1 de septiembre

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
    │ → María López García — APTO                             │
    └─────────────────────────────────────────────────────────┘

🤖  ¡Listo! Tu solicitud ha sido registrada. El equipo de recursos humanos
    revisará tu perfil y se pondrá en contacto contigo pronto. ¡Gracias por tu
    tiempo y mucha suerte! 🛵✨
```

Note the salary question mid-flow: the agent answers from the FAQ in two lines and returns to the interview without losing its place. That's session state doing its job.

### 2. Disqualified — no driver's license

```
🤖  ¡Hola! Soy el asistente de selección de Grupo Sazón 🛵. Estamos buscando
    repartidores para nuestra cadena de restaurantes. ¿Me puedes dar tu nombre
    completo?

👤  Javier Ruiz Moreno

🤖  ¡Encantado, Javier! ¿Tienes licencia de conducir válida?

👤  Bueno, la tengo caducada desde el año pasado, estoy a punto de renovarla

    ── Gate 2 · ambiguous ("caducada", "a punto de") is treated as NO ──

    ┌─ tool call ─────────────────────────────────────────────┐
    │ save_screening_result(                                  │
    │   full_name="Javier Ruiz Moreno",                       │
    │   has_drivers_license=False, city="",                   │
    │   availability="", preferred_schedule="",               │
    │   delivery_experience_years=None,                       │
    │   delivery_platform=None, start_date="",                │
    │   disqualified=True,                                    │
    │   disqualification_reason="Sin licencia de conducir válida",│
    │   language="es")                                        │
    │ → Javier Ruiz Moreno — DESCALIFICADO                    │
    └─────────────────────────────────────────────────────────┘

🤖  Gracias por tu interés, pero para este puesto necesitamos que tengas
    licencia de conducir válida. Te tendremos en cuenta si abrimos otras
    posiciones. ¡Mucha suerte! 👋
```

The interview stops at stage 2. No point collecting a preferred schedule from someone who can't legally take the shift — and the candidate gets a clear, non-humiliating answer in under a minute instead of a form and three days of silence.

### 3. Disqualified — outside service area (Bilbao)

```
🤖  ¡Hola! Soy el asistente de selección de Grupo Sazón 🛵. Estamos buscando
    repartidores para nuestra cadena de restaurantes. ¿Me puedes dar tu nombre
    completo?

👤  Ane Etxeberria Larrañaga

🤖  ¡Encantado, Ane! ¿Tienes licencia de conducir válida?

👤  Sí, claro

🤖  Perfecto 👍 ¿En qué ciudad vives?

👤  Vivo en Bilbao

    ── Gate 3 · "Bilbao" is not in the service-area list ──

    ┌─ tool call ─────────────────────────────────────────────┐
    │ save_screening_result(                                  │
    │   full_name="Ane Etxeberria Larrañaga",                 │
    │   has_drivers_license=True, city="Bilbao",              │
    │   availability="", preferred_schedule="",               │
    │   delivery_experience_years=None,                       │
    │   delivery_platform=None, start_date="",                │
    │   disqualified=True,                                    │
    │   disqualification_reason="Ciudad fuera de cobertura: Bilbao",│
    │   language="es")                                        │
    │ → Ane Etxeberria Larrañaga — DESCALIFICADO              │
    └─────────────────────────────────────────────────────────┘

🤖  Gracias por tu interés. Actualmente solo operamos en Madrid, Barcelona,
    Valencia, Sevilla, Ciudad de México, Guadalajara y Monterrey. Te tendremos
    en cuenta si expandimos a tu zona. ¡Mucha suerte! 👋
```

The record keeps `city="Bilbao"` verbatim rather than dropping it. Aggregate those rejections and you have an expansion heat map for free.

---

## Bonus Features

### Shipped

- **Multi-language with mid-conversation code-switching** — Spanish by default; if the candidate writes in English the agent switches for the rest of the interview and tags the record `language="en"`. Not two prompts and a language picker — one prompt, detected at runtime, which is what actually happens when a candidate in Barcelona types "hi, is this for the delivery job?"
- **Structured screening analytics** — every completed and every disqualified screening lands in `data/screenings/` as flat JSON. `jq -s 'group_by(.city)' data/screenings/*.json` is a funnel report.
- **Skill hot-reload** — `LazySkillToolset` watches `SKILL.md` mtimes and rebuilds the toolset when they change. Edit the screening process, next message uses it, no restart.
- **Telegram gateway** — the channel candidates actually use, already wired at `/gateways/telegram`.
- **Guardrails + cost tracking** — exfil and command guards on every tool call; the cost-guard plugin prices each turn against `plugins/pricing.json`.
- **Record/replay regression tests** — `tests/test_agent.py` captures golden conversations with `RECORD=true`, then replays them for free and deterministically in CI.

### Planned

- **RAG FAQ knowledge base** — candidate questions about pay, vehicle requirements, and contract terms currently answer from a static FAQ block in the prompt. Backing that with a retrievable HR document set means the answers stay right when policy changes, without a redeploy.
- **Sentiment analysis on responses** — flag hesitation and frustration in the transcript so recruiters can prioritize enthusiastic candidates and spot where the script loses people.
- **Re-engagement follow-ups** — the cron scheduler is already in the box; use it to nudge candidates who ghosted mid-interview and resume where the session left off.
- **ATS integration REST API** — replace the JSON writer with a push to the client's applicant tracking system. One tool changes.
- **Voice agent** — browser-based ElevenLabs voice for candidates who'd rather talk than type, which for a delivery-driver funnel is a meaningful fraction of them.

---

## Rubric Alignment

| Criterion | Weight | Where it lives |
|---|---|---|
| **Process Design** | 25% | `skills/default/SKILL.md` — the full stage table, service areas, disqualification paths, and an edge-case matrix (silence, ambiguity, language switch, abuse). `prompt/instructions.py` turns it into the exact conversational script, with tone and pacing rules. Both gates are designed to *save*, not discard, so rejections are data. |
| **Technical Build** | 40% | Google ADK `LlmAgent` with real session state; typed `FunctionTool` giving the model a schema it can't under-fill; LiteLLM/OpenRouter model abstraction; retry + timeout config; exfil and command guardrails as `before_tool_callback`; 17-plugin cross-cutting chain; FastAPI server with API-key auth and health checks; record/replay tests. |
| **Presentation** | 25% | Demo-ready in one command via the ADK web UI. Three annotated transcripts above cover the happy path and both gates. Output records are human-readable JSON you can open on screen. |
| **Bonus Features** | 10% | Multi-language code-switching and analytics-ready structured output shipped today; Telegram gateway live; RAG FAQ, sentiment analysis, re-engagement, ATS push, and voice scoped above. |

---

## How to Demo This in 5 Minutes

**0:00 — Start it** (do this before you hit record)

```bash
source .venv/bin/activate
DEV_MODE=true adk web .
```

Open `http://localhost:8000`, select `sazon_screener`. Have a second terminal in `data/screenings/` ready.

**0:30 — Frame the problem.** 45 locations, ~200 applications a week, two countries. A recruiter reads the same seven answers two hundred times. Show the empty `data/screenings/` directory.

**1:00 — Run the happy path.** Type as María from transcript 1. Let it flow — one question at a time, warm tone, short messages. Two things to point at out loud:
- Drop in the salary question mid-interview. The agent answers in two lines and returns to exactly where it was.
- Type one message in English and watch it switch, then switch back.

**3:00 — Show the artifact.** In the second terminal:

```bash
cat data/screenings/*.json | jq
```

The point: this isn't a chat transcript to be read later, it's a structured record. The typed tool signature is why the model can't produce a half-filled one.

**3:45 — Show a gate.** New session. Name, then "no tengo licencia." Interview stops immediately, farewell is polite, record saved as `DESCALIFICADO` with a reason. `cat` it.

**4:30 — Close on the path forward.** Same tool boundary swaps the JSON writer for an ATS push. Same agent, add the cron scheduler for re-engagement. The model is one env var — this runs on Claude or Llama without a code change.

---

## Directory Layout

```
sazon-screener/
├── run_adk.py                       # FastAPI entrypoint — auth, health, Telegram router
├── requirements.txt
├── Dockerfile / railway.json        # Container + deploy config
├── .env.example                     # OPENROUTER_API_KEY, DEV_MODE, TELEGRAM_*, …
│
├── sazon_screener/
│   ├── agent.py                     # ★ LlmAgent + LazySkillToolset + guardrail callbacks
│   ├── harness.py                   # Runner/App wiring shared by server, cron, tests
│   ├── streaming.py                 # Optional WebSocket live mode
│   │
│   ├── prompt/
│   │   └── instructions.py          # ★ System prompt — 9-step script, tone, FAQ, edge cases
│   │
│   ├── tools/
│   │   ├── save_screening.py        # ★ FunctionTool → data/screenings/*.json
│   │   ├── memory_tools.py
│   │   └── halt_tools.py
│   │
│   ├── skills/
│   │   └── default/SKILL.md         # ★ Screening process — stages, gates, output schema
│   │
│   ├── config/
│   │   ├── llm.py                   # ★ LiteLlm → OpenRouter → gemini-3.7-flash
│   │   ├── paths.py                 # Env-overridable runtime paths (volume-friendly)
│   │   ├── seed.py                  # First-boot seeding of writable surfaces
│   │   └── logging.py               # Structured logs + request IDs
│   │
│   ├── gateways/
│   │   ├── telegram.py              # Webhook → agent turn
│   │   ├── commands.py
│   │   └── transcription.py         # Voice notes → text
│   │
│   ├── plugins/                     # Cost guard, trace, context window, resilience,
│   │   ├── …                        #   cache, console logger, skill curator, …
│   │   └── pricing.json             # Per-model token pricing for cost accounting
│   │
│   ├── guardrails/                  # exfil_guard, command_guard — before_tool_callback
│   ├── memory/                      # Consolidation, profile, preload, review fork
│   ├── state/                       # Session state + query cache
│   ├── cron/                        # Scheduler + routes (future re-engagement)
│   ├── soul/SOUL.md                 # Agent identity seed
│   └── utils/                       # Circuit breaker, rate limiter, date helpers
│
├── tests/
│   └── test_agent.py                # Record/replay golden conversation tests
│
├── static/test_client.html          # Minimal browser client for streaming mode
├── memory/AGENT_MEMORY.md
└── data/screenings/                 # ← Output. Created on first completed screening.
```

★ = the six files worth reading first.

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
