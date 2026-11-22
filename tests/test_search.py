"""Tests of the ranked full text index behind plain-word search, on an invented vault."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from obsidian_mcp import search as index
from obsidian_mcp import vault as ops

PLAN = (
    "---\nstatus: active\nowner: nobody\n---\n"
    "# Garden plan\n\nIntro about soil.\n\n"
    "## Tomatoes\n\nTomatoes need sun and water. Tomatoes, tomatoes, tomatoes.\n\n"
    "### Watering\n\nWater the tomatoes at dawn.\n\n"
    "## Old layout\n\nSuperseded 2026-03-01 by [[Garden/Plan#New layout]].\n\n"
    "The beds ran north to south, tomatoes by the fence, tomatoes by the gate, tomatoes everywhere.\n\n"
    "## New layout\n\nThe beds run east to west.\n\n#### Deep detail\n\nStill the new layout.\n\n"
    "```\n## Not a heading\n```\n"
)

NOTES = {
    "Garden/Plan.md": PLAN,
    "Kitchen/Recipes.md": "Pasta with tomatoes and basil.\n\n## Sauce\n\nSimmer slowly.\n",
    "Lighthouse.md": "## Keeper\n\nThe keeper logs the weather.\n",
}


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for name, text in NOTES.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def hits(result: str) -> list[str]:
    """Each result's first line, without the bold marks, in the order given."""
    return [line.replace("**", "") for line in result.splitlines() if line.startswith("**")]


def touch(path: Path, text: str) -> None:
    """Rewrite a note and move its modification time on, as a later save would."""
    before = path.stat().st_mtime if path.exists() else 0
    path.write_text(text, encoding="utf-8")
    os.utime(path, (before + 10, before + 10))


# ---------------------------------------------------------------- sections

def test_a_note_splits_into_sections_at_second_and_third_level_headings():
    found = index.sections(PLAN)
    assert [s.heading for s in found] == ["", "Tomatoes", "Watering", "Old layout", "New layout"]
    assert [s.line for s in found] == [5, 9, 13, 17, 23]
    assert "status" not in found[0].text and "Garden plan" in found[0].text
    assert "#### Deep detail" in found[4].text and "## Not a heading" in found[4].text
    assert [s.superseded for s in found] == ["", "", "", "2026-03-01", ""]


def test_a_note_with_no_text_still_has_one_row():
    assert [(s.heading, s.text) for s in index.sections("---\na: 1\n---\n")] == [("", "")]
    assert [s.heading for s in index.sections("## Only\n\nbody\n")] == ["Only"]


def test_a_marker_counts_only_as_the_first_line_of_its_section():
    late = "## Notes\n\nSome text.\n\nSuperseded 2026-01-01 by [[A#B]].\n"
    assert index.sections(late)[0].superseded == ""


# ---------------------------------------------------------------- queries

@pytest.mark.parametrize("query, expected", [
    ("tomatoes basil", '"tomatoes" "basil"'),
    ('"sun and water" dawn', '"sun and water" "dawn"'),
    ("keeper OR NOT lamp*", '"keeper" "OR" "NOT" "lamp*"'),
    ('say "hi', '"say" "hi"'),
    ('a"b', '"a""b"'),
    ("( - :", ""),
])
def test_a_plain_query_becomes_required_quoted_terms(query, expected):
    assert index.match_expression(query) == expected


# ---------------------------------------------------------------- ranking

def test_search_ranks_sections_and_puts_superseded_ones_last(vault):
    result = ops.search(vault, "tomatoes")
    order = hits(result)
    assert order[0] == "Garden/Plan.md > Tomatoes (line 11)"
    assert order[-1] == "Garden/Plan.md > Old layout (line 21), superseded: 2026-03-01"
    assert len(order) == 4 and "Kitchen/Recipes.md (line 1)" in order
    assert result.startswith("Found 4 sections, ranked")


def test_every_word_is_required_and_a_phrase_is_kept(vault):
    assert hits(ops.search(vault, "tomatoes basil")) == ["Kitchen/Recipes.md (line 1)"]
    assert hits(ops.search(vault, '"sun and water"')) == ["Garden/Plan.md > Tomatoes (line 11)"]
    assert hits(ops.search(vault, '"water and sun"')) == []


def test_the_title_path_and_heading_are_searchable(vault):
    assert hits(ops.search(vault, "lighthouse")) == ["Lighthouse.md > Keeper (line 1)"]
    assert hits(ops.search(vault, "kitchen sauce")) == ["Kitchen/Recipes.md > Sauce (line 3)"]


def test_operators_are_words_not_syntax(vault):
    assert hits(ops.search(vault, "keeper OR basil")) == []
    for query in ("keeper*", "-keeper", "keeper:", "(keeper", "keeper)"):
        assert hits(ops.search(vault, query)) == ["Lighthouse.md > Keeper (line 3)"], query


def test_frontmatter_is_not_searched_and_the_answer_says_so(vault):
    result = ops.search(vault, "nobody")
    assert result.startswith("No notes found matching: nobody") and "frontmatter" in result


def test_search_respects_folder_and_limit(vault):
    assert {h.split(" ")[0] for h in hits(ops.search(vault, "tomatoes", folder="Garden"))} == {"Garden/Plan.md"}
    result = ops.search(vault, "tomatoes", max_results=2)
    assert len(hits(result)) == 2 and "showing 2" in result


# ---------------------------------------------------------------- the index

def test_the_index_lives_in_the_hidden_folder_and_is_not_itself_a_note(vault):
    ops.search(vault, "keeper")
    assert (vault / ".obsidian" / "mcp-search-index.db").exists()
    with sqlite3.connect(vault / ".obsidian" / "mcp-search-index.db") as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_the_index_follows_an_edit_a_deletion_and_a_new_note(vault):
    db = index.open_index(vault)
    try:
        assert index.update(db, vault) == 3
        assert index.update(db, vault) == 0
    finally:
        db.close()
    assert hits(ops.search(vault, "basil"))

    touch(vault / "Lighthouse.md", "## Keeper\n\nThe keeper trims the lamp.\n")
    (vault / "Kitchen" / "Recipes.md").unlink()
    touch(vault / "Harbor.md", "Basil grows by the harbor wall.\n")

    db = index.open_index(vault)
    try:
        assert index.update(db, vault) == 3
        assert db.execute("SELECT count(*) FROM notes").fetchone()[0] == 3
    finally:
        db.close()
    assert hits(ops.search(vault, "lamp")) == ["Lighthouse.md > Keeper (line 3)"]
    assert hits(ops.search(vault, "weather")) == []
    assert hits(ops.search(vault, "basil")) == ["Harbor.md (line 1)"]


def test_a_damaged_index_is_rebuilt(vault):
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "mcp-search-index.db").write_bytes(b"not a database at all " * 200)
    assert hits(ops.search(vault, "keeper")) == ["Lighthouse.md > Keeper (line 3)"]


def test_an_index_of_another_schema_version_is_rebuilt(vault):
    ops.search(vault, "keeper")
    with sqlite3.connect(vault / ".obsidian" / "mcp-search-index.db") as db:
        db.execute("PRAGMA user_version = 999")
    assert hits(ops.search(vault, "keeper")) == ["Lighthouse.md > Keeper (line 3)"]


def test_a_held_write_lock_neither_blocks_nor_breaks_a_search(vault, monkeypatch):
    ops.search(vault, "keeper")
    monkeypatch.setattr(index, "LOCK_WAIT_SECONDS", 0.2)
    other = sqlite3.connect(vault / ".obsidian" / "mcp-search-index.db", isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        assert hits(ops.search(vault, "keeper")) == ["Lighthouse.md > Keeper (line 3)"]
        touch(vault / "Lighthouse.md", "## Keeper\n\nThe keeper trims the lamp.\n")
        result = ops.search(vault, "keeper")
        assert "1 changed note" in result and hits(result) == ["Lighthouse.md > Keeper (line 3)"]
    finally:
        other.rollback()
        other.close()
    assert hits(ops.search(vault, "lamp")) == ["Lighthouse.md > Keeper (line 3)"]


def test_two_sessions_building_at_once_agree(vault):
    command = [sys.executable, "-m", "obsidian_mcp.search", "--vault", str(vault), "tomatoes"]
    runs = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outputs = [run.communicate(timeout=60) for run in runs]
    assert all(run.returncode == 0 for run in runs), outputs
    assert outputs[0][0] == outputs[1][0] and hits(outputs[0][0])[0] == "Garden/Plan.md > Tomatoes (line 11)"


# ---------------------------------------------------------------- fallbacks and entry

def test_without_fts5_search_falls_back_to_the_scan_and_says_so(vault, monkeypatch):
    monkeypatch.setattr(index, "FTS_MODULE", "no_such_module_here")
    result = ops.search(vault, "keeper")
    assert result.startswith("Note: the full text index is unavailable")
    assert "unranked scan in path order" in result and "**Lighthouse.md** (line 1)" in result


def test_regex_and_case_sensitive_queries_keep_the_scan(vault):
    assert hits(ops.search(vault, r"^owner: \w+$", regex=True)) == ["Garden/Plan.md (line 3)"]
    assert hits(ops.search(vault, "Keeper", case_sensitive=True)) == ["Lighthouse.md (line 1)"]
    assert not (vault / ".obsidian").exists()


def test_the_command_line_gives_the_tool_answer(vault):
    expected = ops.search(vault, "tomatoes basil", max_results=5)
    env = {**os.environ, "OBSIDIAN_VAULT_PATH": str(vault)}
    run = subprocess.run([sys.executable, "-m", "obsidian_mcp.search", "--max-results", "5", "tomatoes", "basil"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert run.returncode == 0 and run.stdout == expected + "\n"
    missing = subprocess.run([sys.executable, "-m", "obsidian_mcp.search", "x"], capture_output=True, text=True,
                             env={**os.environ, "OBSIDIAN_VAULT_PATH": "", "HOME": str(vault)}, timeout=60)
    assert missing.returncode == 2 and "No vault configured" in missing.stderr
