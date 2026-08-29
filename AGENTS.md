# Sazón Screener — AGENTS.md

Guidance for AI coding agents working in this repository.

## Overview

ADK-based agent that screens delivery-driver candidates for Grupo Sazón (45+ locations, Spain/Mexico). Stateful multi-turn conversation → structured JSON output.

## Project structure

```
sazon-screener/
├── run_adk.py                    # FastAPI entrypoint: auth, health, Telegram gateway
├── run_convo_eval.py             # 10-scenario conversation eval harness (real LLM)
├── Dockerfile                    # Railway deployment
├── railway.json                  # Railway config (health at /health, docker build)
├── .env.example                  # OPENROUTER_API_KEY, DEV_MODE, TELEGRAM_*, …
├── requirements.txt              # nuvel-cli >= 0.3.0 (traces/dashboard)
│
├── sazon_screener/
│   ├── agent.py                  # LlmAgent + LazySkillToolset + guardrail callbacks
│   ├── prompt/instructions.py    # Identity "Carlos" — tone, language rules (53 lines)
│   ├── tools/save_screening.py   # FunctionTool → data/screenings/*.json
│   ├── skills/sazon-screener-flow/SKILL.md  # Screening SOP (86 lines)
│   ├── config/llm.py             # LiteLlm → OpenRouter models
│   ├── config/paths.py           # Path resolution helpers
│   ├── config/seed.py            # Seed data / test fixtures
│   ├── gateways/                 # Telegram webhook, commands, voice transcription
│   ├── plugins/                  # CostGuard, TracePlugin, ContextWindow, Cache, resilience
│   ├── guardrails/               # exfil_guard, command_guard, command_safety
│   ├── memory/                   # OrgMemoryService (profile, consolidation, review)
│   ├── cron/                     # Cron scheduler, delivery, isolation, routes
│   ├── callbacks/                # ADK callback handlers
│   ├── state/                    # Query cache, session memory
│   ├── streaming.py              # Gemini Live API (WebSocket voice)
│   └── soul/SOUL.md              # Agent identity / soul
│
├── tests/test_agent.py           # Record/replay golden conversation tests
├── eval_results/                 # Committed eval transcripts (10 JSON files)
├── memory/                       # Root-level memory files (AGENT_MEMORY.md, etc.)
└── static/test_client.html       # Voice test client (mic → WebSocket)
```

## How to run

```bash
# Setup (uv preferred)
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env  # add OPENROUTER_API_KEY

# Start the agent server
DEV_MODE=true python run_adk.py

# ADK web UI (separate terminal)
DEV_MODE=true adk web .

# Trace dashboard (separate terminal)
nuvel dashboard
```

## Key architecture decisions

- **ADK owns conversation state** — session, tool dispatch, plugin lifecycle
- **LiteLLM** is the model-adapter layer; swap models via env var
- **OpenRouter** is the gateway — one key, any model
- **Prompt vs Skill separation** — identity lives in `instructions.py`, screening flow in `SKILL.md`. Skills hot-reload on mtime change.
- **Skill directory name MUST match SKILL.md frontmatter `name`** — ADK SkillToolset requirement. Off by one char → silent no-load.
- **JSON files for output** — right amount for demo scale. `save_screening_result` is the swap boundary.
- **TracePlugin** writes JSONL to `./traces/` — browsable via `nuvel dashboard` or `nuvel traces`.

## Common commands

```bash
# Run all 10 eval scenarios
.venv/bin/python run_convo_eval.py

# Single scenario
.venv/bin/python run_convo_eval.py 03-dq

# Replay tests (no LLM cost)
python -m pytest tests/ -v

# Record golden conversation (requires OPENROUTER_API_KEY)
RECORD=true python -m pytest tests/test_agent.py -v -k test_golden_greeting

# Diagnose install
nuvel doctor

# Trace dashboard
nuvel dashboard
nuvel traces list
```

## Conventions

- Python 3.11+
- No external DB dependency in DEV_MODE (in-memory sessions)
- `data/screenings/` and `traces/` gitignored
- Model pin: `openrouter/google/gemini-3.7-flash` default, override via `FAST_MODEL`/`REASONING_MODEL` env vars
- Spanish default, English code-switch, tags `language` field in output