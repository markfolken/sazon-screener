# Sazón Screener

AI screening agent for Grupo Sazón — a restaurant chain hiring delivery drivers across Spain (Madrid, Barcelona, Valencia, Seville) and Mexico (Mexico City, Guadalajara, Monterrey), ~45 locations, ~200 applications/week.

Built for the **Orbio FDE Technical Assignment** on Google ADK, scaffolded with [Nuvel](https://github.com/Folken2/nuvel).

---

## Architecture

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
  · SkillToolset (mtime hot-reload)  · before/after callbacks
                │
                ▼
  LiteLLM  ──  openrouter/google/gemini-3.7-flash
  · 3 retries on transient errors  · 120s timeout
                │
                ▼
  data/screenings/<timestamp>_<name>.json
```

ADK owns the conversation: session state, tool dispatch, plugin lifecycle. LiteLLM is the model-adapter layer — swap models via env var. OpenRouter is the gateway — one API key, any model.

### Key files

| File | Role |
|---|---|
| `sazon_screener/agent.py` | `LlmAgent` — model, instruction provider, tool list, guardrail callbacks. `LazySkillToolset` re-reads `SKILL.md` on mtime change (no restart). |
| `sazon_screener/prompt/instructions.py` | Identity prompt ("Carlos") — tone, messaging style, language-switch rule. 53 lines. Screening flow lives in `SKILL.md`. |
| `sazon_screener/tools/save_screening.py` | `FunctionTool`. Typed signature → ADK derives the schema the model must fill. Writes one timestamped JSON file per screening. |
| `sazon_screener/config/llm.py` | `FAST_MODEL` / `REASONING_MODEL` as `LiteLlm` instances pointed at OpenRouter, plus retry and timeout config. |
| `run_adk.py` | FastAPI entrypoint. Mounts ADK app, API-key auth, health checks, Telegram gateway router. |

### Screening flow

Seven data-collection stages, two hard gates:

| # | Stage | Field | Validation | Gate |
|---|---|---|---|---|
| 1 | Nombre | `full_name` | Non-empty, first + last | — |
| 2 | Licencia | `has_drivers_license` | Yes/No — *No* ends the interview | **Yes** |
| 3 | Ciudad | `city` | Must be a service area; normalized to canonical name | **Yes** |
| 4 | Disponibilidad | `availability` | `full-time` / `part-time` / `weekends` | — |
| 5 | Horario | `preferred_schedule` | `morning` / `afternoon` / `evening` / `flexible` | — |
| 6 | Experiencia | `delivery_experience_years`, `delivery_platform` | Years as float (`0.5` = 6 months); platform nullable | — |
| 7 | Fecha inicio | `start_date` | Free text | — |

Wrapped by greeting at the start and summary+confirmation at the end. Both gates still call `save_screening_result` with `disqualified=True` and a reason.

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

Requirements: Python 3.11+, an OpenRouter API key.

```bash
cd generated-agents/sazon-screener

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # add your OPENROUTER_API_KEY
# edit .env

DEV_MODE=true python run_adk.py
```

Server starts on `http://localhost:8000`. `DEV_MODE=true` uses in-memory sessions (no database needed).

**Interactive web UI** (run in a separate terminal — `run_adk.py` blocks):

```bash
DEV_MODE=true adk web .
```

Pick `sazon_screener` from the agent dropdown.

**Endpoints:** `GET /health` (public), `POST /run_sse` (streaming, auth required when `API_KEY` is set).

**Telegram:** set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` in `.env`, expose the host, point your bot's webhook at `/gateways/telegram`.

---

## Evaluation

`run_convo_eval.py` drives 10 scripted candidate personas through the real agent (real model calls, real tool invocations) and writes full transcripts + tool calls to `eval_results/<scenario>.json`.

```bash
.venv/bin/python run_convo_eval.py              # all 10 scenarios
.venv/bin/python run_convo_eval.py 03-dq        # single scenario by id prefix
```

### Scenarios and results

Results below are from committed runs in `eval_results/`. Transcripts are real model output — read them directly.

| # | Scenario | Outcome | Notes |
|---|---|---|---|
| 01 | Happy path, Madrid (ES) | ✅ Clean | Saved `disqualified=false`, city="Madrid" |
| 02 | Disqualified: no license | ✅ Clean | Saved `disqualified=true`, reason="No cuenta con licencia" |
| 03 | Disqualified: Zaragoza (outside area) | ✅ Clean | Saved `disqualified=true`, reason="Outside service area (Zaragoza)" |
| 04 | Language switch mid-flow (ES→EN) | ✅ Correct behavior | Agent switches to English on turn 3, reaches summary+confirmation. Scripted turns end before final confirm — harness limitation, agent behavior is correct. |
| 05 | Ambiguous answers | ✅ Clean | Agent re-prompts with concrete options, disqualifies at license gate after 3 ambiguous replies. Saved with correct reason. |
| 06 | Job questions mid-flow | ✅ Correct behavior | Agent answers salary/hours from FAQ, returns to screening. Scripted turns end before schedule resolution — harness limitation. |
| 07 | Inappropriate input (3-strike) | ✅ Clean | Terminates on strike 3 without calling `save_screening_result`. |
| 08 | Candidate leaves for days | ✅ Clean | Agent acknowledges departure politely, holds session state, no save. |
| 09 | Candidate returns (same session as 08) | ✅ Correct behavior | Agent resumes from availability question ("¿tiempo completo, medio tiempo...?"), reaches the summary. Confirm turn missing — harness limitation. |
| 10 | Happy path, CDMX (ES-MX) | ✅ Clean | "CDMX" normalized to "Ciudad de México", "Simón" (MX slang) handled. Saved `disqualified=false`. |

**Gates verified:** Zaragoza → disqualified with service-area citation. No license → disqualified with polite farewell. 3-strike → terminates without save.

**Session resume:** scenario 08 and 09 share the same ADK session. Scenario 09 picks up where 08 left off — same candidate, same runner, state preserved across the gap. This confirms ADK session persistence works for multi-day interview flows.

Screening records land in `data/screenings/*.json` (gitignored) — one file per saved screening.

### Eval harness details

- Uses `InMemoryRunner` from ADK (no FastAPI layer, direct agent access).
- Each scenario is a list of candidate turn strings. Waits for full agent response before sending the next turn.
- Extracts `function_call` events, separates `save_screening_result` calls from infrastructure calls (`load_skill`).
- Timed: `⏱ total Xs` printed at end.

---

## Testing

```bash
# Replay mode — deterministic, no LLM calls, no cost
python -m pytest tests/ -v

# Record a golden conversation (requires OPENROUTER_API_KEY)
RECORD=true python -m pytest tests/test_agent.py -v -k test_golden_greeting
```

Record mode uses ADK's built-in `RecordingsPlugin` to capture LLM requests/responses and tool calls to YAML under `tests/recordings/<test_name>/`. Replay mode uses `ReplayPlugin` to serve those recordings deterministically. If a recording file is missing, the test skips with a message rather than failing.

**Current state:** the record/replay infrastructure is wired and functional, but no golden recordings are committed yet. Run with `RECORD=true` to generate them, then commit the YAML files for CI replay.

---

## Sample Conversation — qualified candidate (representative)

This is paraphrased from eval transcripts (01-happy-madrid, 10-happy-cdmx). It shows the full flow but is not a literal transcript of any single run. See `eval_results/` for verbatim transcripts.

```
🤖  ¡Hola! Soy Carlos, del equipo de selección de Grupo Sazón 🛵.
    ¿Me puedes dar tu nombre completo?

👤  Me llamo Javier Mendoza Ruiz

🤖  ¡Mucho gusto, Javier! ¿Tienes carné de conducir vigente?

👤  Sí, tengo el carné B

🤖  Perfecto 👍 ¿En qué ciudad vives?

👤  En Madrid, zona de Chamberí

🤖  ¡Genial! ¿Qué disponibilidad tienes: tiempo completo, medio tiempo,
    o fines de semana?

👤  Tiempo completo

🤖  ¿Y qué horario prefieres: mañana, tarde, noche, flexible?

👤  Prefiero mañanas

🤖  Anotado 🕐 ¿Tienes experiencia como repartidor?
    ¿Cuántos años y en qué plataforma?

👤  Dos años repartiendo con Glovo

    [salary question — agent answers from FAQ in 2 lines, returns to flow]

🤖  ¿Cuándo podrías empezar?

👤  El próximo lunes

🤖  Perfecto, déjame confirmar:
    📋 Javier Mendoza Ruiz · 🚗 Licencia: Sí · 📍 Madrid
    ⏰ Tiempo completo · 🕐 Mañana · 📦 2 años en Glovo · 📅 Lunes
    ¿Es correcto?

👤  Sí, todo correcto

    ┌─ tool call ─────────────────────────────────────────────┐
    │ save_screening_result(                                  │
    │   full_name="Javier Mendoza Ruiz",                      │
    │   has_drivers_license=True, city="Madrid",              │
    │   availability="full-time", preferred_schedule="morning",│
    │   delivery_experience_years=2.0,                        │
    │   delivery_platform="Glovo",                            │
    │   start_date="el próximo lunes",                        │
    │   disqualified=False, language="es")                    │
    └─────────────────────────────────────────────────────────┘

🤖  ¡Listo! Tu solicitud ha sido registrada. El equipo de RRHH
    revisará tu perfil y se pondrá en contacto. ¡Gracias! 🛵✨
```

---

## Key Design Decisions

**Why Google ADK?** The screening problem is stateful multi-turn conversation producing a structured artifact — ADK's shape. Session state across turns is handled by the framework. `FunctionTool` derives its schema from the Python signature, so `save_screening_result`'s typed parameters *are* the output contract. Plugin/callback lifecycle means cost tracking, tracing, and guardrails live outside agent code. Rolling this by hand means rebuilding all three.

**Why `openrouter/google/gemini-3.7-flash`?** Spanish quality first — this agent is Spanish-first with English code-switching, and Gemini handles both natively. Cost: at ~$0.375/M input tokens, 200 screenings/week is sub-dollar. Latency: a candidate on Telegram expects a reply in seconds, not tens of seconds. Behind OpenRouter, swapping models is an env-var edit.

**Why JSON files for screening records?** The right amount of infrastructure for a screening demo is none. Files are `cat`-able, `jq`-able, diffable, and they make the output contract visible. Deliberately the wrong choice at scale: 200/week is ~10k records/year, at which point Postgres or a direct ATS push becomes appropriate. `save_screening_result` is the swap boundary — change the implementation, nothing else changes.

**Features shipped:**
- Multi-language code-switching: Spanish by default, switches to English if candidate writes in English, tags record `language="en"`.
- Skill hot-reload: `LazySkillToolset` watches `SKILL.md` mtimes and rebuilds the toolset when they change. No restart.
- Telegram gateway: wired at `/gateways/telegram` with webhook secret validation.
- Plugin chain: cost guard (per-turn pricing), trace (JSONL), context window, guardrails (exfil/command), reflection retry, cache.
- Record/replay regression tests: ADK `RecordingsPlugin` / `ReplayPlugin`.

---

## Directory Layout

```
sazon-screener/
├── run_adk.py                       # FastAPI entrypoint — auth, health, Telegram router
├── run_convo_eval.py                # 10-scenario conversation eval harness
├── eval_results/                    # Committed eval transcripts (10 JSON files)
├── .env.example                     # OPENROUTER_API_KEY, DEV_MODE, TELEGRAM_*, …
│
├── sazon_screener/
│   ├── agent.py                     # LlmAgent + LazySkillToolset + guardrail callbacks
│   ├── prompt/instructions.py       # Identity, tone, language rules (prompt)
│   ├── tools/save_screening.py      # FunctionTool → data/screenings/*.json
│   ├── skills/sazon-screener-flow/SKILL.md  # Screening SOP: stages, gates, FAQ, output schema
│   ├── config/llm.py                # LiteLlm → OpenRouter → gemini-3.7-flash
│   ├── gateways/                    # Telegram webhook, commands, voice transcription
│   ├── plugins/                     # Cost guard, trace, context window, cache, …
│   └── guardrails/                  # exfil_guard, command_guard
│
├── tests/test_agent.py              # Record/replay golden conversation tests
├── tests/recordings/                # Golden YAML recordings (empty — run RECORD=true to populate)
└── data/screenings/                 # Output. Created on first completed screening.
```

### Prompt vs Skill separation

`instructions.py` (53 lines): identity, tone, messaging style, language-switch rule. No screening flow details.

`skills/sazon-screener-flow/SKILL.md` (86 lines): the entire SOP — 7 stages, validation rules, gates, edge cases table, FAQ table, output schema. This is the single source of truth for how the interview runs.

**Why separate:** the skill is hot-reloaded on mtime change (no restart needed to tweak a gate or FAQ entry). The prompt stays small — identity and tone rarely change, and keeping them out of the skill avoids accidental edits to the agent's personality when adjusting screening logic.

The skill directory name (`sazon-screener-flow`) must match the `name` field in SKILL.md's YAML frontmatter — this is an ADK `SkillToolset` requirement. They differ by one character and the agent won't load the skill.

---

Built by Mark Folken for the Orbio FDE Technical Assignment · Scaffolded with [Nuvel](https://github.com/Folken2/nuvel)