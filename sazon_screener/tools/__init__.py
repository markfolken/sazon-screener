"""
Tools for the agent.

Composes the tool surface from feature bundles. Bundles activated by
scaffold flags (--persona, --with-composio) are rendered in here; the
others are absent.
"""

from .memory_tools import memory_tool_list
from .halt_tools import halt_tool_list
from .save_screening import save_screening_tool
from .mark_stage import mark_stage_tool
from .schedule_followup import schedule_followup_tool
from ..cron.tools import cronjob_tool_list


def get_tools() -> list:
    """Return the list of tools available to the agent."""
    tools: list = []
    tools.extend(memory_tool_list)
    tools.extend(halt_tool_list)
    tools.append(save_screening_tool)
    tools.extend(cronjob_tool_list)
    tools.append(mark_stage_tool)
    tools.append(schedule_followup_tool)
    return tools