"""Custom in-process tools exposed to the agent via an SDK MCP server.

Tool names follow the SDK convention mcp__assistant__<name>.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import toolcore
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
    return _text(await toolcore.add_task(args))


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
    return _text(await toolcore.list_tasks(args))


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
    out = await toolcore.complete_task(args)
    return _text(out, is_error=out.startswith(toolcore.ERR_NO_TASK))


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
    out = await toolcore.delete_task(args)
    return _text(out, is_error=out.startswith(toolcore.ERR_NO_TASK))


@tool(
    name="due_tasks",
    description="List open tasks that are overdue or due within the next N hours (default 24).",
    input_schema={
        "type": "object",
        "properties": {"within_hours": {"type": "integer"}},
    },
)
async def due_tasks(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await toolcore.due_tasks(args))


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
    return _text(await toolcore.remember(args))


@tool(
    name="update_memory",
    description=(
        "Revise a fact already in long-term memory: replace `find` with `replace` in the "
        "given memory file. Use this when something you remembered changed (new job, moved "
        "house, project finished) so the old fact gets corrected in place instead of a "
        "contradicting line piling up next to it. `find` must match the file's exact text."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": ["profile", "preferences", "projects", "inbox"]},
            "find": {"type": "string", "description": "Exact existing text to replace"},
            "replace": {"type": "string", "description": "The corrected text"},
        },
        "required": ["category", "find", "replace"],
    },
)
async def update_memory(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await toolcore.update_memory(args))


@tool(
    name="forget_fact",
    description=(
        "Remove a fact from long-term memory: deletes memory bullet lines containing the "
        "given text (case-insensitive). Use when the user says to forget something, or when "
        "you spot a duplicate or a fact that is simply no longer true."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": ["profile", "preferences", "projects", "inbox"]},
            "text": {"type": "string", "description": "Text identifying the line(s) to remove"},
        },
        "required": ["category", "text"],
    },
)
async def forget_fact(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await toolcore.forget_fact(args))


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
    return _text(await toolcore.journal(args))


@tool(
    name="recall_chats",
    description=(
        "Full-text search the user's PAST CONVERSATIONS with you. Use it whenever the "
        "user references something you discussed before ('what did we decide about X', "
        "'that thing we talked about last week', 'you told me a command for...') or when "
        "an earlier conversation likely holds relevant context. Returns timestamped "
        "snippets with the conversation title."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Words to find in past chats"},
            "limit": {"type": "integer", "description": "Max hits (default 12)"},
        },
        "required": ["query"],
    },
)
async def recall_chats(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await toolcore.recall_chats(args))


@tool(
    name="tag_file",
    description=(
        "Write a short summary onto a file's macOS Spotlight metadata (its Finder "
        "comment) so the user can find the file later by concept, even when those "
        "words aren't in the file's text. Use after you read or summarize a dense "
        "document the user will want to resurface: a PDF, a paper, a slide deck. "
        "The summary should be a few concept-rich keywords/phrases, not prose."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to tag (~ is expanded)"},
            "summary": {"type": "string", "description": "Concept-rich keywords to make searchable"},
        },
        "required": ["path", "summary"],
    },
)
async def tag_file(args: dict[str, Any]) -> dict[str, Any]:
    out = await toolcore.tag_file(args)
    return _text(out, is_error=out.startswith(("No such file", "Could not", "tag_file only")))


@tool(
    name="think_harder",
    description=(
        "Bring in a stronger model for a hard sub-problem instead of guessing. Use when a "
        "turn needs deep multi-step reasoning, a tricky tradeoff, careful analysis, or "
        "code/logic you are not confident about. Ask ONE specific, self-contained question "
        "and include the relevant context; you get back the stronger model's answer to fold "
        "into your reply. level='sonnet' (default) for most hard turns, 'opus' only for the "
        "very hardest. Don't use it for things you can already handle well."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "the specific, self-contained question"},
            "context": {"type": "string", "description": "relevant details the stronger model needs"},
            "level": {"type": "string", "enum": ["sonnet", "opus"], "description": "default: sonnet"},
        },
        "required": ["question"],
    },
)
async def think_harder(args: dict[str, Any]) -> dict[str, Any]:
    out = await toolcore.think_harder(args)
    return _text(out, is_error=out.startswith("Unknown level "))


def build_server():
    return create_sdk_mcp_server(
        name="assistant",
        version="1.0.0",
        tools=[add_task, list_tasks, complete_task, delete_task, due_tasks,
               remember, update_memory, forget_fact, journal, recall_chats,
               tag_file, think_harder],
    )
