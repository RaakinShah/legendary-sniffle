"""Custom in-process tools exposed to the agent via an SDK MCP server.

Tool names follow the SDK convention mcp__assistant__<name>.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import memory, tasks
from .util import text_result as _text


@tool(
    name="add_task",
    description=(
        "Add a task or reminder to the user's task list. Call this whenever the user "
        "mentions something they need to do, a deadline, or asks to be reminded."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short description of the task"},
            "due": {
                "type": "string",
                "description": "Due date/time in ISO 8601, e.g. 2026-06-12 or 2026-06-12T15:00",
            },
            "notes": {"type": "string", "description": "Extra details"},
            "priority": {"type": "string", "enum": ["low", "normal", "high"]},
        },
        "required": ["title"],
    },
)
async def add_task(args: dict[str, Any]) -> dict[str, Any]:
    t = tasks.add(
        title=args["title"],
        due=args.get("due"),
        notes=args.get("notes", ""),
        priority=args.get("priority", "normal"),
    )
    return _text(f"Added task: {t.render()}")


@tool(
    name="list_tasks",
    description=(
        "List the user's tasks. Call this when the user asks what's on their plate, "
        "what's due, or before planning their day."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["open", "done", "all"], "description": "Default: open"},
        },
    },
)
async def list_tasks(args: dict[str, Any]) -> dict[str, Any]:
    items = tasks.list_tasks(status=args.get("status", "open"))
    if not items:
        return _text("No tasks found.")
    return _text("\n".join(t.render() for t in items))


@tool(
    name="complete_task",
    description="Mark a task done by its numeric id (shown by list_tasks).",
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "integer"}},
        "required": ["task_id"],
    },
)
async def complete_task(args: dict[str, Any]) -> dict[str, Any]:
    try:
        t = tasks.complete(int(args["task_id"]))
    except KeyError as exc:
        return _text(str(exc), is_error=True)
    return _text(f"Completed: {t.render()}")


@tool(
    name="delete_task",
    description="Delete a task permanently by its numeric id.",
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "integer"}},
        "required": ["task_id"],
    },
)
async def delete_task(args: dict[str, Any]) -> dict[str, Any]:
    try:
        tasks.delete(int(args["task_id"]))
    except KeyError as exc:
        return _text(str(exc), is_error=True)
    return _text(f"Deleted task #{args['task_id']}.")


@tool(
    name="due_tasks",
    description="List open tasks that are overdue or due within the next N hours (default 24).",
    input_schema={
        "type": "object",
        "properties": {"within_hours": {"type": "integer"}},
    },
)
async def due_tasks(args: dict[str, Any]) -> dict[str, Any]:
    items = tasks.due_soon(within_hours=int(args.get("within_hours", 24)))
    if not items:
        return _text("Nothing due in that window.")
    return _text("\n".join(t.render() for t in items))


@tool(
    name="remember",
    description=(
        "Save a fact about the user to long-term memory. Call this whenever you learn "
        "something durable: their preferences, people in their life, routines, goals. "
        "Category 'profile' for who they are, 'preferences' for how they like things, "
        "'projects' for current work, 'inbox' for anything else."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "fact": {"type": "string", "description": "One self-contained sentence"},
            "category": {"type": "string", "enum": ["profile", "preferences", "projects", "inbox"]},
        },
        "required": ["fact"],
    },
)
async def remember(args: dict[str, Any]) -> dict[str, Any]:
    path = memory.remember(args["fact"], args.get("category", "inbox"))
    return _text(f"Remembered (saved to {path}).")


@tool(
    name="journal",
    description=(
        "Append a timestamped entry to today's journal. Use it to log notable events, "
        "decisions, or how the user's day is going, so future sessions have context."
    ),
    input_schema={
        "type": "object",
        "properties": {"entry": {"type": "string"}},
        "required": ["entry"],
    },
)
async def journal(args: dict[str, Any]) -> dict[str, Any]:
    path = memory.journal(args["entry"])
    return _text(f"Logged (saved to {path}).")


def build_server():
    return create_sdk_mcp_server(
        name="assistant",
        version="1.0.0",
        tools=[add_task, list_tasks, complete_task, delete_task, due_tasks, remember, journal],
    )
