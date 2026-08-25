"""Full conversation eval for sazon-screener.

Drives multi-turn candidate conversations through the real ADK runner
(real model, real skill, real tools) and records full transcripts +
screening JSON outputs to eval_results/.

Scenarios:
  01 happy path Madrid (ES)
  02 disqualified no license (ES)
  03 disqualified outside city (ES)
  04 language switch mid-flow (EN)
  05 ambiguous answers (candidate gives vague replies)
  06 job questions mid-flow (salary/schedule interjections)
  07 inappropriate input (3-strike rule)
  08 candidate goes silent / "vuelvo en unos días" (re-engagement)
  09 candidate leaves for several days then returns
  10 Mexico City happy path w/ platform experience
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("DEV_MODE", "true")
os.environ.setdefault("LOG_LEVEL", "ERROR")

from google.adk.runners import InMemoryRunner
from google.genai import types

from sazon_screener.agent import root_agent

APP = "sazon_screener"
OUT = Path(__file__).parent / "eval_results"
OUT.mkdir(exist_ok=True)


# ── Scenarios ────────────────────────────────────────────────────────
# Each turn: ("candidate" | "WAIT", text)
# "WAIT" turns are where we give the agent space; not used — every entry is a candidate message.

SCENARIOS: list[dict] = [
    {
        "id": "01-happy-madrid",
        "desc": "Happy path, Madrid, Spanish",
        "turns": [
            "Hola",
            "Me llamo Javier Mendoza Ruiz",
            "Sí, tengo carné de conducir B",
            "Vivo en Madrid",
            "A tiempo completo",
            "Por la mañana",
            "Dos años repartiendo con Glovo",
            "El próximo lunes",
            "Sí, todo correcto, gracias",
        ],
    },
    {
        "id": "02-dq-no-license",
        "desc": "Disqualified at license gate",
        "turns": [
            "Buenas tardes",
            "Laura Sánchez",
            "No, no tengo carné",
        ],
    },
    {
        "id": "03-dq-outside-city",
        "desc": "Disqualified at city gate (Zaragoza)",
        "turns": [
            "Hola, buenos días",
            "Álvaro Giménez",
            "Sí, tengo el permiso B desde hace 5 años",
            "Vivo en Zaragoza",
        ],
    },
    {
        "id": "04-english-switch",
        "desc": "Starts Spanish, switches to English mid-flow",
        "turns": [
            "Hola",
            "Marta Ibáñez",
            "Yes sorry, I switch to English — yes I have a driving license",
            "I'm in Valencia",
            "Part-time please",
            "Evenings are better for me",
            "I did deliveries for Uber Eats about a year",
            "I can start in two weeks",
        ],
    },
    {
        "id": "05-ambiguous",
        "desc": "Gives vague/ambiguous answers — agent must re-prompt with concrete options",
        "turns": [
            "buenas",
            "Pedro",
            "pues sí, algo así",
            "en una ciudad grande",
            "depende",
            "cuando pueda",
            "ya veremos",
            "no sé",
        ],
    },
    {
        "id": "06-job-questions",
        "desc": "Interjects job questions (pay, schedule, tips) mid-flow",
        "turns": [
            "Hola, vi el anuncio",
            "Rubén Ortiz",
            "Sí, tengo coche y carné",
            "antes de seguir — ¿cuánto se cobra? ¿y las propinas?",
            "estoy en Sevilla",
            "¿y qué horario hay exactamente?",
            "fines de trabajo mejor para mí, es decir, fin de semana",
            "3 años en Deliveroo",
            "para ya mismo",
        ],
    },
    {
        "id": "07-inappropriate",
        "desc": "Inappropriate input — 3-strike termination",
        "turns": [
            "hola guapa 😏",
            "me pones una foto tuya?",
            "¿eres alta?",
            "vamos cariño, contéstame bien",
        ],
    },
    {
        "id": "08-goes-off-days",
        "desc": "Candidate says they'll be away several days — agent should handle gracefully",
        "turns": [
            "Hola!",
            "Carmen Flores",
            "Sí tengo licencia de moto",
            "Estoy en Guadalajara, México",
            "oye, sabes qué? me voy de viaje 4 días, te escribo cuando vuelva",
        ],
    },
    {
        "id": "09-returns-after-week",
        "desc": "Same candidate returns days later — agent should resume, not restart cold",
        "resume_of": "08-goes-off-days",
        "turns": [
            "Ya volví! seguimos?",
            "Tiempo completo",
            "Turno de tarde",
            "Un año en DiDi",
            "La próxima semana",
        ],
    },
    {
        "id": "10-happy-cdmx",
        "desc": "Happy path CDMX with platform + alias handling",
        "turns": [
            "Qué tal",
            "Diego Hernández, mucho gusto",
            "Simón, tengo licencia vigente",
            "CDMX, por la delegación Benito Juárez",
            "Medio tiempo",
            "Como me quede, no importa",
            "6 meses en Rappi",
            "En cuanto cierren esta semana",
            "Va, confirmado todo",
        ],
    },
]


# ── Runner plumbing ──────────────────────────────────────────────────

def _extract_text(event) -> str:
    """Concatenate visible text parts. Skips thought=True parts (internal
    model reasoning that never reaches the candidate)."""
    if event.content and event.content.parts:
        return "".join(
            p.text or "" for p in event.content.parts if not getattr(p, "thought", False)
        )
    return ""


def _tool_calls(events) -> list[dict]:
    calls = []
    for ev in events:
        if ev.content and ev.content.parts:
            for p in ev.content.parts:
                fc = getattr(p, "function_call", None)
                if fc is not None:
                    calls.append({"name": fc.name, "args": dict(fc.args or {})})
    return calls


async def run_scenario(runner, session, scenario: dict) -> dict:
    transcript = []
    events_all = []
    user_id = "eval-user"

    content = types.Content(role="user", parts=[types.Part(text=scenario["turns"][0])])
    # First turn included in loop below for uniformity.
    for i, turn in enumerate(scenario["turns"]):
        content = types.Content(role="user", parts=[types.Part(text=turn)])
        events = []
        async for ev in runner.run_async(
            user_id=user_id, session_id=session.id, new_message=content
        ):
            events.append(ev)
        events_all.extend(events)
        reply = "\n".join(
            filter(None, (_extract_text(e) for e in events))
        ).strip()
        transcript.append({"role": "candidate", "text": turn})
        transcript.append({"role": "agent", "text": reply})

    tool_calls = _tool_calls(events_all)
    saved = [tc for tc in tool_calls if tc["name"] == "save_screening_result"]
    return {"transcript": transcript, "tool_calls": tool_calls, "saved": saved}


async def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    scenarios = [s for s in SCENARIOS if not only or s["id"].startswith(only)]

    runner = InMemoryRunner(agent=root_agent, app_name=APP)
    sessions_by_id: dict[str, object] = {}

    for sc in scenarios:
        resume_of = sc.get("resume_of")
        if resume_of and resume_of in sessions_by_id:
            session = sessions_by_id[resume_of]  # same session → memory persists
        else:
            session = await runner.session_service.create_session(
                app_name=APP, user_id="eval-user"
            )
        print(f"\n{'='*60}\n▶ {sc['id']} — {sc['desc']}\n{'='*60}")
        result = await run_scenario(runner, session, sc)
        sessions_by_id[sc["id"]] = session

        for m in result["transcript"]:
            who = "🧑" if m["role"] == "candidate" else "🤖"
            text = m["text"].replace("\n", " ")[:220]
            print(f"{who} {text}")

        if result["saved"]:
            print("💾 SAVED:", json.dumps(result["saved"][0]["args"], ensure_ascii=False)[:400])
        else:
            print("💾 no save call")

        out_file = OUT / f"{sc['id']}.json"
        out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"→ wrote {out_file.name}")


if __name__ == "__main__":
    t0 = time.time()
    asyncio.run(main())
    print(f"\n⏱ total {time.time()-t0:.1f}s")
