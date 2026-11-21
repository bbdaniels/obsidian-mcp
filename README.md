# Obsidian MCP

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that gives Claude Code read/write access to your Obsidian vault.

**[Documentation](https://www.benjaminbdaniels.com/obsidian-mcp/)** | **[PyPI](https://pypi.org/project/obsidian-mcp-server/)** | **[GitHub](https://github.com/bbdaniels/obsidian-mcp)**

## Quick Start with Claude Code

The fastest way to get up and running:

```bash
claude mcp add obsidian -- uvx obsidian-mcp-server
```

On PyPI this project is [`obsidian-mcp-server`](https://pypi.org/project/obsidian-mcp-server/). The name `obsidian-mcp` there belongs to a different project, so `uvx obsidian-mcp` or `pip install obsidian-mcp` installs that one, not this. To run the unreleased code on `main`, use `uvx --from git+https://github.com/bbdaniels/obsidian-mcp obsidian-mcp`.

Then restart Claude Code and tell it:

> "Configure obsidian vault at /path/to/your/vault"

That's it -- Claude can now read, write, and search your Obsidian notes.

## Features

- **Read & write notes** - Create, edit, and append to markdown files
- **Search** - Full-text and regular expression search across your vault
- **Properties, tags and links** - Find notes by frontmatter property, by tag, or by the notes that link to them
- **Daily notes** - Read or append to daily notes with configurable date format
- **Browse** - List files and folders in your vault
- **Secure** - Only accesses your configured vault directory

## Installation

### Via uvx (recommended -- no install needed)

If you set up via the Quick Start above, you don't need to install anything. `uvx` runs the package from PyPI in an isolated environment.

### Via pipx (persistent install)

```bash
pipx install obsidian-mcp-server
```

Then configure Claude Code:

```bash
claude mcp add obsidian -- obsidian-mcp
```

### Via pip

```bash
pip install obsidian-mcp-server
```

Then configure Claude Code:

```bash
claude mcp add obsidian -- python3 -m obsidian_mcp.server
```

### From source

```bash
git clone https://github.com/bbdaniels/obsidian-mcp.git
cd obsidian-mcp
pip install -e '.[dev]'
pytest
```

Then configure Claude Code:

```bash
claude mcp add obsidian -- obsidian-mcp
```

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
| `obsidian_search` | Search notes by text or regular expression |
| `obsidian_info` | Show a note's properties, tags, headings and outgoing links |
| `obsidian_search_property` | Find notes by a frontmatter property (`exists`, `missing`, `equals`, `not_equals`, `contains`, `gt`, `gte`, `lt`, `lte`) |
| `obsidian_tags` | List every tag with its count, or the notes carrying a tag |
| `obsidian_backlinks` | List the notes that link to a note |
| `obsidian_list` | Browse vault structure |
| `obsidian_daily` | Read/append to daily notes |

## Built-in Prompts

The server includes MCP prompts that provide structured workflows for common note-taking patterns. These show up as invocable prompts in Claude Code.

| Prompt | Description |
|--------|-------------|
| `session-start` | Review daily notes and project context before starting work |
| `session-end` | Document accomplishments, decisions, and open questions at end of session |
| `project-checkin` | Review and update a specific project's documentation |

Each prompt accepts an optional `project` argument to focus on a specific project folder.

## Automatic Instructions

When this server is connected, Claude automatically receives guidance about when and how to use the Obsidian tools -- no `CLAUDE.md` configuration needed. The built-in instructions tell Claude to:

- Check for project context at the start of sessions
- Document decisions as they're made (not just at the end)
- Update daily notes with session summaries
- Search for existing notes before creating new ones

## Example Usage

Once configured, you can ask Claude things like:

- "Search my notes for authentication patterns"
- "Read my project architecture note"
- "Append today's session summary to my daily note"
- "Create a new note at Projects/my-project/decisions.md"
- "List all notes in my Work folder"

## Configuration

Set the vault with the `OBSIDIAN_VAULT_PATH` environment variable (for example `claude mcp add obsidian -e OBSIDIAN_VAULT_PATH=/path/to/vault -- ...`), which takes precedence, or let `obsidian_configure` store it at `~/.config/obsidian-mcp/config.json`:

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

## Customizing Claude Instructions (Optional)

The server includes built-in instructions that guide Claude's note-taking behavior automatically. For additional customization, you can add to `~/.claude/CLAUDE.md`:

```markdown
## Obsidian Note-Taking

### Project Notes
- Vault uses project folders (e.g., MyProject/) with notes like Technical Notes.md, Overview.md
- Always update BOTH daily notes AND project-specific notes
- Include commit hashes and file references in technical notes
```

## License

MIT
