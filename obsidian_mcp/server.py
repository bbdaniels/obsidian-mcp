#!/usr/bin/env python3
"""
Obsidian MCP Server - Provides Claude Code with read/write access to Obsidian vaults.

This file is the MCP wiring: configuration, the tool list, the prompts and the
dispatch. What each tool does to the vault is in vault.py.
"""

import json
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult, GetPromptResult, ListPromptsResult, ListToolsResult,
    Prompt, PromptArgument, PromptMessage, TextContent, Tool,
)

from . import vault as ops

# Server instructions - automatically included in Claude's context when this MCP server is connected
SERVER_INSTRUCTIONS = """You have access to the user's Obsidian vault for note-taking and documentation.

## When to use Obsidian tools

**At the START of work sessions:**
- Use obsidian_list at vault root to check for project folders relevant to the current task
- If a project folder exists, read its notes (especially Technical Notes or Overview) to pick up context from previous sessions
- Check today's daily note for any relevant context

**DURING work sessions:**
- When making significant decisions (architecture, approach, trade-offs), document them immediately
- When encountering non-obvious implementation details, note them in the project folder
- Don't wait until the end -- capture context while it's fresh

**At the END of work sessions:**
- Append a summary to today's daily note using obsidian_daily (action: append)
  - What was accomplished
  - Key decisions made
  - Any blockers or open questions
  - Link to relevant project notes (e.g., [[ProjectName/Technical Notes]])
- Update the relevant project folder notes if the project has one

**Project note organization:**
- Vault uses project folders (e.g., ProjectName/) with notes like Technical Notes.md, Overview.md
- Daily notes are in the Daily Notes/ folder with YYYY-MM-DD format
- Use obsidian_search to find related existing notes before creating new ones
- Use obsidian_info for a note's properties, tags, headings and links; obsidian_search_property, obsidian_tags and obsidian_backlinks to find notes by frontmatter, tag or incoming link
- Prefer appending to existing notes (obsidian_append with heading parameter) over creating new ones
"""


# Configuration
def get_config_path() -> Path:
    """Get the config file path."""
    return Path.home() / ".config" / "obsidian-mcp" / "config.json"


def load_config() -> dict:
    """Load configuration from file."""
    config_path = get_config_path()
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    """Save configuration to file."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def get_vault_path() -> Optional[Path]:
    """The vault path: the OBSIDIAN_VAULT_PATH environment variable, else the config file."""
    if os.environ.get("OBSIDIAN_VAULT_PATH"):
        return Path(os.environ["OBSIDIAN_VAULT_PATH"]).expanduser()
    config = load_config()
    vault_path = config.get("vault_path")
    if vault_path:
        return Path(vault_path)
    return None


def get_daily_notes_folder() -> str:
    """Get the daily notes folder (default: 'Daily Notes')."""
    config = load_config()
    return config.get("daily_notes_folder", "Daily Notes")


def get_daily_notes_format() -> str:
    """Get the daily notes date format (default: YYYY-MM-DD)."""
    config = load_config()
    return config.get("daily_notes_format", "%Y-%m-%d")


def _tool(name: str, description: str, required: tuple = (), **properties) -> Tool:
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    return Tool(name=name, description=description, input_schema=schema)


def _arg(kind: str, description: str, **extra) -> dict:
    return {"type": kind, "description": description, **extra}


FOLDER = _arg("string", "Optional: limit to this folder")

TOOLS = [
    _tool("obsidian_configure",
          "Configure the Obsidian vault path and settings. Run this first to set up your vault.",
          required=("vault_path",),
          vault_path=_arg("string", "Absolute path to your Obsidian vault folder"),
          daily_notes_folder=_arg("string", "Folder for daily notes (default: 'Daily Notes')"),
          daily_notes_format=_arg("string", "Date format for daily notes (default: '%Y-%m-%d')")),
    _tool("obsidian_read",
          "Read the contents of a note from the Obsidian vault. Returns the full markdown content.",
          required=("path",),
          path=_arg("string", "Path to the note relative to vault root (e.g., 'Projects/my-project.md' or 'Projects/my-project')")),
    _tool("obsidian_write",
          "Create or overwrite a note in the Obsidian vault. Creates parent folders if needed.",
          required=("path", "content"),
          path=_arg("string", "Path to the note relative to vault root"),
          content=_arg("string", "Full markdown content for the note")),
    _tool("obsidian_append",
          "Append content to an existing note, or create it if it doesn't exist. Prefer this over obsidian_write when adding to existing project documentation.",
          required=("path", "content"),
          path=_arg("string", "Path to the note relative to vault root"),
          content=_arg("string", "Content to append to the note"),
          heading=_arg("string", "Optional: append under this heading (creates if doesn't exist)")),
    _tool("obsidian_search",
          "Search for notes containing specific text or matching a regular expression. Returns matching file paths, line numbers and snippets. Use before creating new notes to find existing relevant documentation.",
          required=("query",),
          query=_arg("string", "Text to search for, or a Python regular expression when regex is true"),
          folder=FOLDER,
          max_results=_arg("integer", "Maximum results to return (default: 10)"),
          regex=_arg("boolean", "Treat the query as a regular expression (default: false)"),
          case_sensitive=_arg("boolean", "Match case exactly (default: false)")),
    _tool("obsidian_info",
          "Show a note's metadata without its text: frontmatter properties, tags, headings, outgoing links, size and modified time.",
          required=("path",),
          path=_arg("string", "Path to the note relative to vault root")),
    _tool("obsidian_search_property",
          "Find notes by a frontmatter property, for example status equals active, or updated gte 2026-01-01. Property names ignore case; a list property matches when any item does.",
          required=("property",),
          property=_arg("string", "Frontmatter property name (e.g., 'status')"),
          operator=_arg("string", "How to compare (default: exists)", enum=list(ops.OPERATORS)),
          value=_arg(["string", "number", "boolean"], "Value to compare with (not needed for exists or missing)"),
          folder=FOLDER,
          max_results=_arg("integer", "Maximum results to return (default: 50)")),
    _tool("obsidian_tags",
          "With no tag, list every tag in the vault with the number of notes carrying it. With a tag, list the notes that carry it or a tag nested under it. Reads both the tags property and #tags in the text.",
          tag=_arg("string", "Optional: tag to look up, with or without '#'"),
          folder=FOLDER,
          max_results=_arg("integer", "Maximum notes to return (default: 50)")),
    _tool("obsidian_backlinks",
          "List the notes that link to a note, by [[wikilink]] or Markdown link, with each linking line.",
          required=("path",),
          path=_arg("string", "Path to the note relative to vault root"),
          max_results=_arg("integer", "Maximum linking notes to return (default: 50)")),
    _tool("obsidian_list",
          "List notes and folders in the vault. Use at the start of sessions to discover project folders and available documentation.",
          folder=_arg("string", "Folder to list (default: vault root)"),
          recursive=_arg("boolean", "Include subfolders recursively (default: false)")),
    _tool("obsidian_daily",
          "Read or append to today's daily note. Creates the note if it doesn't exist. Use this at the end of work sessions to document accomplishments, decisions, and open questions.",
          required=("action",),
          action=_arg("string", "Whether to read or append to the daily note", enum=["read", "append"]),
          content=_arg("string", "Content to append (required if action is 'append')"),
          date=_arg("string", "Optional: specific date (YYYY-MM-DD format). Defaults to today.")),
    _tool("obsidian_status", "Show current configuration and vault status."),
]


def configure(arguments: dict) -> str:
    vault_path = Path(arguments["vault_path"]).expanduser()
    if not vault_path.exists():
        return f"Error: Vault path does not exist: {vault_path}"
    if not vault_path.is_dir():
        return f"Error: Vault path is not a directory: {vault_path}"
    config = load_config()
    config["vault_path"] = str(vault_path)
    for key in ("daily_notes_folder", "daily_notes_format"):
        if key in arguments:
            config[key] = arguments[key]
    save_config(config)
    note = ""
    if os.environ.get("OBSIDIAN_VAULT_PATH"):
        note = "\nNote: OBSIDIAN_VAULT_PATH is set and takes precedence over this saved path."
    return (f"Configured vault: {vault_path}\nDaily notes folder: {get_daily_notes_folder()}"
            f"\nDaily notes format: {get_daily_notes_format()}{note}")


def run_tool(name: str, arguments: dict) -> str:
    """Run one tool and return its text answer."""
    if name == "obsidian_configure":
        return configure(arguments)
    if name not in {tool.name for tool in TOOLS}:
        return f"Unknown tool: {name}"
    vault = get_vault_path()
    if not vault:
        return "No vault configured. Use obsidian_configure to set up your vault."
    a = arguments
    if name == "obsidian_status":
        count = sum(1 for _ in ops.notes(vault, vault))
        return (f"Vault: {vault}\nDaily notes folder: {get_daily_notes_folder()}"
                f"\nDaily notes format: {get_daily_notes_format()}\nTotal notes: {count}")
    if name == "obsidian_read":
        return ops.read(vault, a["path"])
    if name == "obsidian_write":
        return ops.write(vault, a["path"], a["content"])
    if name == "obsidian_append":
        return ops.append(vault, a["path"], a["content"], a.get("heading"))
    if name == "obsidian_search":
        return ops.search(vault, a["query"], a.get("folder", ""), a.get("max_results", 10),
                          a.get("regex", False), a.get("case_sensitive", False))
    if name == "obsidian_info":
        return ops.info(vault, a["path"])
    if name == "obsidian_search_property":
        return ops.search_property(vault, a["property"], a.get("operator", "exists"), a.get("value"),
                                   a.get("folder", ""), a.get("max_results", 50))
    if name == "obsidian_tags":
        return ops.tags(vault, a.get("tag", ""), a.get("folder", ""), a.get("max_results", 50))
    if name == "obsidian_backlinks":
        return ops.backlinks(vault, a["path"], a.get("max_results", 50))
    if name == "obsidian_list":
        return ops.list_folder(vault, a.get("folder", ""), a.get("recursive", False))
    return ops.daily(vault, get_daily_notes_folder(), get_daily_notes_format(),
                     a["action"], a.get("content"), a.get("date"))


def prompts() -> list[Prompt]:
    """The prompts this server offers."""
    return [
        Prompt(
            name="session-start",
            title="Session Start",
            description="Review context from Obsidian before starting work. Checks daily notes, project folders, and recent activity.",
            arguments=[
                PromptArgument(
                    name="project",
                    description="Project name or folder to check for context (optional)",
                    required=False
                )
            ]
        ),
        Prompt(
            name="session-end",
            title="Session End",
            description="Document what was accomplished during this work session. Updates daily note and project notes.",
            arguments=[
                PromptArgument(
                    name="project",
                    description="Project name or folder to update (optional)",
                    required=False
                )
            ]
        ),
        Prompt(
            name="project-checkin",
            title="Project Check-in",
            description="Review and update project documentation in Obsidian. Read existing notes and append new findings.",
            arguments=[
                PromptArgument(
                    name="project",
                    description="Project name or folder in the vault",
                    required=True
                )
            ]
        ),
    ]

def get_prompt(name: str, arguments: dict | None) -> GetPromptResult:
    """Build one prompt."""
    project = (arguments or {}).get("project", "")

    if name == "session-start":
        prompt_text = "I'm starting a work session. Please help me get context from my Obsidian vault:\n\n"
        prompt_text += "1. Read today's daily note (obsidian_daily, action: read) to see what's already been noted today\n"
        prompt_text += "2. Use obsidian_list at the vault root to see available project folders\n"
        if project:
            prompt_text += f"3. Read notes in the '{project}' project folder to pick up context from previous sessions\n"
        else:
            prompt_text += "3. If any project folder is relevant to what I'm about to work on, read its notes for context\n"
        prompt_text += "\nSummarize what you find so I can pick up where I left off."

        return GetPromptResult(
            description="Review Obsidian context before starting work",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(type="text", text=prompt_text)
                )
            ]
        )

    elif name == "session-end":
        prompt_text = "I'm wrapping up this work session. Please document what we accomplished in Obsidian:\n\n"
        prompt_text += "1. Append a summary to today's daily note (obsidian_daily, action: append) including:\n"
        prompt_text += "   - What was accomplished\n"
        prompt_text += "   - Key decisions made\n"
        prompt_text += "   - Any blockers or open questions\n"
        if project:
            prompt_text += f"   - Link to [[{project}/Technical Notes]] if relevant\n"
            prompt_text += f"2. Update notes in the '{project}' project folder with any technical details, decisions, or implementation notes from this session\n"
        else:
            prompt_text += "   - Links to any relevant project notes\n"
            prompt_text += "2. If we worked on a specific project that has a folder in the vault, update its notes too\n"
        prompt_text += "\nReview our conversation to capture the important details."

        return GetPromptResult(
            description="Document work session in Obsidian",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(type="text", text=prompt_text)
                )
            ]
        )

    elif name == "project-checkin":
        if not project:
            return GetPromptResult(
                description="Project check-in",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text="Please specify a project name. Use obsidian_list to see available project folders in the vault.")
                    )
                ]
            )

        prompt_text = f"Please review and update the '{project}' project documentation in Obsidian:\n\n"
        prompt_text += f"1. Use obsidian_list on the '{project}' folder to see existing notes\n"
        prompt_text += f"2. Read the key notes (Technical Notes, Overview, etc.)\n"
        prompt_text += f"3. Based on our current work, append any new information:\n"
        prompt_text += f"   - Code changes with file names and line references\n"
        prompt_text += f"   - Architecture decisions and their rationale\n"
        prompt_text += f"   - Non-obvious implementation details\n"
        prompt_text += f"   - Updated status of any to-do items\n"

        return GetPromptResult(
            description=f"Review and update {project} documentation",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(type="text", text=prompt_text)
                )
            ]
        )

    return GetPromptResult(
        description="Unknown prompt",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text=f"Unknown prompt: {name}")
            )
        ]
    )

async def on_list_tools(ctx, params) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)


async def on_call_tool(ctx, params) -> CallToolResult:
    try:
        text = run_tool(params.name, params.arguments or {})
    except KeyError as missing:
        text = f"Error: missing required argument {missing}."
    return CallToolResult(content=[TextContent(type="text", text=text)])


async def on_list_prompts(ctx, params) -> ListPromptsResult:
    return ListPromptsResult(prompts=prompts())


async def on_get_prompt(ctx, params) -> GetPromptResult:
    return get_prompt(params.name, params.arguments)


def package_version() -> str:
    try:
        return version("obsidian-mcp")
    except PackageNotFoundError:
        return ""


server = Server(
    "obsidian-mcp",
    version=package_version(),
    instructions=SERVER_INSTRUCTIONS,
    on_list_tools=on_list_tools,
    on_call_tool=on_call_tool,
    on_list_prompts=on_list_prompts,
    on_get_prompt=on_get_prompt,
)


async def run():
    """Run the server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """Entry point."""
    import asyncio
    asyncio.run(run())


if __name__ == "__main__":
    main()
