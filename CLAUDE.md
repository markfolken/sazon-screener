# Sazón Screener — CLAUDE.md

## Quick start

```bash
uv venv && source .venv/bin/activate && uv pip install -r requirements.txt
cp .env.example .env  # add OPENROUTER_API_KEY
DEV_MODE=true python run_adk.py          # agent on :8000
DEV_MODE=true adk web .                  # ADK web UI on :8001
nuvel dashboard                           # traces on :8765
```

## Project rules

- **Don't touch `data/screenings/` or `traces/`** — they're gitignored runtime output
- **Don't edit the skill directory name** unless you also fix the `name` frontmatter in `SKILL.md` — they must match or the skill silently won't load
- **Prompt vs Skill**: `instructions.py` = identity/tone, `SKILL.md` = screening flow logic. Edits to the screening flow go in the skill, not the prompt
- **Python 3.11+**, no external DB in DEV_MODE
- **Model**: `openrouter/google/gemini-3.7-flash`. Override via `FAST_MODEL` env var
- **Tests use record/replay**: `RECORD=true` to capture, no env to replay (deterministic, zero cost)

## Key gotchas

- **ADK SkillToolset bug**: skill dir name must exactly match `name` in `SKILL.md` frontmatter or the toolset is silently skipped every turn
- **No LiteLLM caching**: fresh model call on every turn unless the agent's context window plugin caches it
- **Voice requires `GOOGLE_API_KEY`** (not OpenRouter) — Gemini Live API talks to Google directly
- **`nuvel doctor`** before debugging mysterious import/routing issues

## Test / Eval

```bash
python -m pytest tests/ -v                      # replay mode (free)
.venv/bin/python run_convo_eval.py 03-dq         # single eval scenario
.venv/bin/python run_convo_eval.py               # all 10 scenarios
nuvel traces errors --recent 10                  # recent errors from last run
```

## File map

| File | Purpose |
|------|---------|
| `run_adk.py` | FastAPI entrypoint — auth, health, routes |
| `sazon_screener/agent.py` | LlmAgent definition + plugin chain |
| `sazon_screener/prompt/instructions.py` | Identity, tone, language rules |
| `sazon_screener/skills/sazon-screener-flow/SKILL.md` | Screening SOP |
| `sazon_screener/tools/save_screening.py` | Output writer (JSON) |
| `sazon_screener/config/llm.py` | LiteLlm config, model names, retries |
| `sazon_screener/plugins/trace_plugin.py` | JSONL trace writer |
| `sazon_screener/streaming.py` | Voice (Gemini Live API) |
| `run_convo_eval.py` | 10-scenario eval harness |
| `tests/test_agent.py` | Record/replay tests |