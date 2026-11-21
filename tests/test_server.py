"""Tests of the MCP wiring: the dispatch in process, and the real server over stdio."""

import json
import os
import subprocess
import sys

import pytest

from obsidian_mcp import server


@pytest.fixture
def vault(tmp_path, monkeypatch):
    (tmp_path / "Note.md").write_text("---\nstatus: active\n---\nSee [[Other]] #topic\n", encoding="utf-8")
    (tmp_path / "Other.md").write_text("plain\n", encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
    monkeypatch.setattr(server, "get_config_path", lambda: tmp_path / ".config.json")
    return tmp_path


def test_every_listed_tool_is_dispatched(vault):
    samples = {
        "obsidian_configure": {"vault_path": str(vault)},
        "obsidian_read": {"path": "Note"},
        "obsidian_write": {"path": "W", "content": "x"},
        "obsidian_append": {"path": "W", "content": "y"},
        "obsidian_search": {"query": "plain"},
        "obsidian_info": {"path": "Note"},
        "obsidian_search_property": {"property": "status"},
        "obsidian_tags": {},
        "obsidian_backlinks": {"path": "Other"},
        "obsidian_list": {},
        "obsidian_daily": {"action": "read"},
        "obsidian_status": {},
    }
    assert set(samples) == {tool.name for tool in server.TOOLS}
    for name, arguments in samples.items():
        result = server.run_tool(name, arguments)
        assert result and not result.startswith(("Unknown tool", "No vault configured")), name


def test_the_environment_variable_beats_the_config_file(vault, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert "takes precedence" in server.run_tool("obsidian_configure", {"vault_path": str(other)})
    assert server.get_vault_path() == vault


def test_the_server_reports_the_installed_version():
    assert server.package_version() == server.version("obsidian-mcp-server") != ""


def test_no_vault_and_unknown_tool(monkeypatch, tmp_path):
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    monkeypatch.setattr(server, "get_config_path", lambda: tmp_path / "none.json")
    assert server.run_tool("obsidian_status", {}).startswith("No vault configured")
    assert server.run_tool("obsidian_nope", {}) == "Unknown tool: obsidian_nope"


def test_the_server_answers_over_stdio(vault):
    """Start the real server and speak MCP to it: initialize, list the tools, call two."""
    process = subprocess.Popen(
        [sys.executable, "-m", "obsidian_mcp.server"], env={**os.environ},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def send(message):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        process.stdin.flush()

    def answer(request_id):
        for line in process.stdout:
            message = json.loads(line)
            if message.get("id") == request_id:
                assert "error" not in message, message
                return message["result"]
        raise AssertionError("server closed: " + process.stderr.read())

    try:
        send({"id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"}}})
        hello = answer(1)
        assert hello["serverInfo"]["name"] == "obsidian-mcp" and "obsidian_backlinks" in hello["instructions"]
        send({"method": "notifications/initialized"})
        send({"id": 2, "method": "tools/list"})
        tools = {tool["name"]: tool for tool in answer(2)["tools"]}
        assert set(tools) == {tool.name for tool in server.TOOLS}
        assert tools["obsidian_backlinks"]["inputSchema"]["required"] == ["path"]
        send({"id": 3, "method": "tools/call", "params": {"name": "obsidian_backlinks", "arguments": {"path": "Other"}}})
        assert "**Note.md**" in answer(3)["content"][0]["text"]
        send({"id": 4, "method": "tools/call", "params": {"name": "obsidian_read", "arguments": {}}})
        assert "missing required argument" in answer(4)["content"][0]["text"]
        send({"id": 5, "method": "prompts/list"})
        assert {prompt["name"] for prompt in answer(5)["prompts"]} == {"session-start", "session-end", "project-checkin"}
        send({"id": 6, "method": "prompts/get", "params": {"name": "session-end", "arguments": {"project": "X"}}})
        assert "[[X/Technical Notes]]" in answer(6)["messages"][0]["content"]["text"]
    finally:
        process.stdin.close()
        process.terminate()
        process.wait(timeout=10)
