#!/usr/bin/env python3
"""
Obsidian MCP Server - Provides Claude Code with read/write access to Obsidian vaults.
"""

import os
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Initialize server
server = Server("obsidian-mcp")

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
    """Get the configured vault path."""
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


def resolve_note_path(note_path: str) -> Optional[Path]:
    """Resolve a note path relative to the vault."""
    vault = get_vault_path()
    if not vault:
        return None

    # Add .md extension if not present
    if not note_path.endswith(".md"):
        note_path = note_path + ".md"

    full_path = vault / note_path

    # Security: ensure path is within vault
    try:
        full_path.resolve().relative_to(vault.resolve())
    except ValueError:
        return None

    return full_path


# Tool definitions
@server.list_tools()
async def list_tools():
    """List available tools."""
    return [
        Tool(
            name="obsidian_configure",
            description="Configure the Obsidian vault path and settings. Run this first to set up your vault.",
            inputSchema={
                "type": "object",
                "properties": {
                    "vault_path": {
                        "type": "string",
                        "description": "Absolute path to your Obsidian vault folder"
                    },
                    "daily_notes_folder": {
                        "type": "string",
                        "description": "Folder for daily notes (default: 'Daily Notes')"
                    },
                    "daily_notes_format": {
                        "type": "string",
                        "description": "Date format for daily notes (default: '%Y-%m-%d')"
                    }
                },
                "required": ["vault_path"]
            }
        ),
        Tool(
            name="obsidian_read",
            description="Read the contents of a note from the Obsidian vault. Returns the full markdown content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the note relative to vault root (e.g., 'Projects/my-project.md' or 'Projects/my-project')"
                    }
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="obsidian_write",
            description="Create or overwrite a note in the Obsidian vault. Creates parent folders if needed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the note relative to vault root"
                    },
                    "content": {
                        "type": "string",
                        "description": "Full markdown content for the note"
                    }
                },
                "required": ["path", "content"]
            }
        ),
        Tool(
            name="obsidian_append",
            description="Append content to an existing note, or create it if it doesn't exist.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the note relative to vault root"
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to append to the note"
                    },
                    "heading": {
                        "type": "string",
                        "description": "Optional: append under this heading (creates if doesn't exist)"
                    }
                },
                "required": ["path", "content"]
            }
        ),
        Tool(
            name="obsidian_search",
            description="Search for notes containing specific text. Returns matching file paths and snippets.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for (case-insensitive)"
                    },
                    "folder": {
                        "type": "string",
                        "description": "Optional: limit search to this folder"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return (default: 10)"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="obsidian_list",
            description="List notes and folders in the vault. Use to browse vault structure.",
            inputSchema={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "Folder to list (default: vault root)"
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Include subfolders recursively (default: false)"
                    }
                }
            }
        ),
        Tool(
            name="obsidian_daily",
            description="Read or append to today's daily note. Creates the note if it doesn't exist.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "append"],
                        "description": "Whether to read or append to the daily note"
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to append (required if action is 'append')"
                    },
                    "date": {
                        "type": "string",
                        "description": "Optional: specific date (YYYY-MM-DD format). Defaults to today."
                    }
                },
                "required": ["action"]
            }
        ),
        Tool(
            name="obsidian_status",
            description="Show current configuration and vault status.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """Handle tool calls."""

    if name == "obsidian_configure":
        vault_path = Path(arguments["vault_path"]).expanduser()
        if not vault_path.exists():
            return [TextContent(type="text", text=f"Error: Vault path does not exist: {vault_path}")]
        if not vault_path.is_dir():
            return [TextContent(type="text", text=f"Error: Vault path is not a directory: {vault_path}")]

        config = load_config()
        config["vault_path"] = str(vault_path)

        if "daily_notes_folder" in arguments:
            config["daily_notes_folder"] = arguments["daily_notes_folder"]
        if "daily_notes_format" in arguments:
            config["daily_notes_format"] = arguments["daily_notes_format"]

        save_config(config)
        return [TextContent(type="text", text=f"Configured vault: {vault_path}\nDaily notes folder: {config.get('daily_notes_folder', 'Daily Notes')}\nDaily notes format: {config.get('daily_notes_format', '%Y-%m-%d')}")]

    elif name == "obsidian_status":
        config = load_config()
        vault = get_vault_path()
        if not vault:
            return [TextContent(type="text", text="No vault configured. Use obsidian_configure to set up your vault.")]

        note_count = sum(1 for _ in vault.rglob("*.md"))
        return [TextContent(type="text", text=f"Vault: {vault}\nDaily notes folder: {get_daily_notes_folder()}\nDaily notes format: {get_daily_notes_format()}\nTotal notes: {note_count}")]

    elif name == "obsidian_read":
        path = resolve_note_path(arguments["path"])
        if not path:
            return [TextContent(type="text", text="Error: No vault configured or invalid path.")]
        if not path.exists():
            return [TextContent(type="text", text=f"Note not found: {arguments['path']}")]

        content = path.read_text(encoding="utf-8")
        return [TextContent(type="text", text=content)]

    elif name == "obsidian_write":
        path = resolve_note_path(arguments["path"])
        if not path:
            return [TextContent(type="text", text="Error: No vault configured or invalid path.")]

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["content"], encoding="utf-8")
        return [TextContent(type="text", text=f"Written: {arguments['path']}")]

    elif name == "obsidian_append":
        path = resolve_note_path(arguments["path"])
        if not path:
            return [TextContent(type="text", text="Error: No vault configured or invalid path.")]

        content_to_add = arguments["content"]
        heading = arguments.get("heading")

        if path.exists():
            existing = path.read_text(encoding="utf-8")

            if heading:
                # Find heading and append after it
                heading_pattern = rf"(^#{1,6}\s+{re.escape(heading)}\s*$)"
                match = re.search(heading_pattern, existing, re.MULTILINE)

                if match:
                    # Find the end of the heading's section (next heading or EOF)
                    heading_level = existing[match.start():match.end()].count('#')
                    next_heading = re.search(rf"^#{{1,{heading_level}}}\s+", existing[match.end():], re.MULTILINE)

                    if next_heading:
                        insert_pos = match.end() + next_heading.start()
                        new_content = existing[:insert_pos] + "\n" + content_to_add + "\n" + existing[insert_pos:]
                    else:
                        new_content = existing + "\n" + content_to_add
                else:
                    # Heading not found, add it
                    new_content = existing + f"\n\n## {heading}\n\n{content_to_add}"
            else:
                new_content = existing + "\n" + content_to_add
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            if heading:
                new_content = f"## {heading}\n\n{content_to_add}"
            else:
                new_content = content_to_add

        path.write_text(new_content, encoding="utf-8")
        return [TextContent(type="text", text=f"Appended to: {arguments['path']}")]

    elif name == "obsidian_search":
        vault = get_vault_path()
        if not vault:
            return [TextContent(type="text", text="Error: No vault configured.")]

        query = arguments["query"].lower()
        folder = arguments.get("folder", "")
        max_results = arguments.get("max_results", 10)

        search_path = vault / folder if folder else vault
        if not search_path.exists():
            return [TextContent(type="text", text=f"Folder not found: {folder}")]

        results = []
        for md_file in search_path.rglob("*.md"):
            if md_file.name.startswith("."):
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                if query in content.lower():
                    rel_path = md_file.relative_to(vault)
                    # Extract snippet around match
                    idx = content.lower().find(query)
                    start = max(0, idx - 50)
                    end = min(len(content), idx + len(query) + 50)
                    snippet = content[start:end].replace("\n", " ").strip()
                    if start > 0:
                        snippet = "..." + snippet
                    if end < len(content):
                        snippet = snippet + "..."
                    results.append(f"**{rel_path}**\n  {snippet}")

                    if len(results) >= max_results:
                        break
            except Exception:
                continue

        if not results:
            return [TextContent(type="text", text=f"No notes found matching: {query}")]

        return [TextContent(type="text", text=f"Found {len(results)} results:\n\n" + "\n\n".join(results))]

    elif name == "obsidian_list":
        vault = get_vault_path()
        if not vault:
            return [TextContent(type="text", text="Error: No vault configured.")]

        folder = arguments.get("folder", "")
        recursive = arguments.get("recursive", False)

        list_path = vault / folder if folder else vault
        if not list_path.exists():
            return [TextContent(type="text", text=f"Folder not found: {folder}")]

        items = []

        if recursive:
            for item in sorted(list_path.rglob("*")):
                if item.name.startswith("."):
                    continue
                rel_path = item.relative_to(vault)
                if item.is_dir():
                    items.append(f"📁 {rel_path}/")
                elif item.suffix == ".md":
                    items.append(f"📄 {rel_path}")
        else:
            for item in sorted(list_path.iterdir()):
                if item.name.startswith("."):
                    continue
                if item.is_dir():
                    items.append(f"📁 {item.name}/")
                elif item.suffix == ".md":
                    items.append(f"📄 {item.name}")

        if not items:
            return [TextContent(type="text", text="No notes or folders found.")]

        return [TextContent(type="text", text="\n".join(items))]

    elif name == "obsidian_daily":
        vault = get_vault_path()
        if not vault:
            return [TextContent(type="text", text="Error: No vault configured.")]

        action = arguments["action"]
        date_str = arguments.get("date")

        if date_str:
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                return [TextContent(type="text", text="Invalid date format. Use YYYY-MM-DD.")]
        else:
            date = datetime.now()

        daily_folder = get_daily_notes_folder()
        date_format = get_daily_notes_format()
        filename = date.strftime(date_format) + ".md"
        daily_path = vault / daily_folder / filename

        if action == "read":
            if not daily_path.exists():
                return [TextContent(type="text", text=f"No daily note for {date.strftime('%Y-%m-%d')}. It will be created when you append to it.")]
            content = daily_path.read_text(encoding="utf-8")
            return [TextContent(type="text", text=content)]

        elif action == "append":
            content_to_add = arguments.get("content")
            if not content_to_add:
                return [TextContent(type="text", text="Error: content required for append action.")]

            daily_path.parent.mkdir(parents=True, exist_ok=True)

            if daily_path.exists():
                existing = daily_path.read_text(encoding="utf-8")
                new_content = existing + "\n" + content_to_add
            else:
                # Create with title
                title = date.strftime("%A, %B %d, %Y")
                new_content = f"# {title}\n\n{content_to_add}"

            daily_path.write_text(new_content, encoding="utf-8")
            return [TextContent(type="text", text=f"Appended to daily note: {daily_folder}/{filename}")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


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
