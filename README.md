# Obsidian MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that gives Claude Code read/write access to your Obsidian vault.

## Features

- **Read & write notes** - Create, edit, and append to markdown files
- **Search** - Full-text search across your vault
- **Daily notes** - Read or append to daily notes with configurable date format
- **Browse** - List files and folders in your vault
- **Secure** - Only accesses your configured vault directory

## Installation

### Via pip

```bash
pip install obsidian-mcp-server
```

### Via pipx (recommended for CLI tools)

```bash
pipx install obsidian-mcp-server
```

### From source

```bash
git clone https://github.com/bbdaniels/obsidian-mcp-server.git
cd obsidian-mcp-server
pip install -e .
```

## Configure Claude Code

Add the MCP server to Claude Code:

```bash
claude mcp add obsidian -- python3 -m obsidian_mcp.server
```

Or if installed via pipx:

```bash
claude mcp add obsidian -- obsidian-mcp
```

Then restart Claude Code.

## First-Time Setup

Once configured, tell Claude:

> "Configure obsidian vault at /path/to/your/vault"

Claude will run `obsidian_configure` to set up the vault path. Your configuration is stored at `~/.config/obsidian-mcp/config.json`.

## Available Tools

| Tool | Description |
|------|-------------|
| `obsidian_configure` | Set vault path and daily notes settings |
| `obsidian_status` | Show current configuration and vault stats |
| `obsidian_read` | Read a note's contents |
| `obsidian_write` | Create or overwrite a note |
| `obsidian_append` | Append to a note (optionally under a heading) |
| `obsidian_search` | Search notes by content |
| `obsidian_list` | Browse vault structure |
| `obsidian_daily` | Read/append to daily notes |

## Example Usage

Once configured, you can ask Claude things like:

- "Search my notes for authentication patterns"
- "Read my project architecture note"
- "Append today's session summary to my daily note"
- "Create a new note at Projects/my-project/decisions.md"
- "List all notes in my Work folder"

## Configuration

Config is stored at `~/.config/obsidian-mcp/config.json`:

```json
{
  "vault_path": "/path/to/your/vault",
  "daily_notes_folder": "Daily Notes",
  "daily_notes_format": "%Y-%m-%d"
}
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `vault_path` | (required) | Absolute path to your Obsidian vault |
| `daily_notes_folder` | `Daily Notes` | Folder for daily notes |
| `daily_notes_format` | `%Y-%m-%d` | Date format for daily note filenames |

## Recommended Claude Instructions

Add this to your `~/.claude/CLAUDE.md` to have Claude automatically take notes:

```markdown
## Obsidian Note-Taking

Use the obsidian MCP tools to document work sessions and project progress.

### Daily Notes
At the end of significant work sessions, append a summary to today's daily note using `obsidian_daily`:
- What was accomplished
- Key decisions made
- Any blockers or open questions

### Project Notes
When working on a project, check if a corresponding folder exists in Obsidian (use `obsidian_list`). If so:
- Document important decisions, architecture choices, or design rationale
- Note any non-obvious implementation details worth remembering
- Track open questions or future improvements
```

## License

MIT
