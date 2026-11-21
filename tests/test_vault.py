"""Tests of the vault operations, on a small vault built in a temporary folder."""

from pathlib import Path

import pytest

from obsidian_mcp import vault as ops

NOTES = {
    "Alpha/Tasks.md": (
        "---\nstatus: active\nupdated: 2026-11-18\ntags: [project, team/core]\npriority: 3\n---\n"
        "# Alpha\n\nSee [[Beta/Overview]] and [[Gamma|the third]] for #context.\n\n"
        "```\n#not-a-tag [[Not A Link]]\n```\n\n## Open\n\nLink to [[Tasks]] by bare name.\n"
    ),
    "Beta/Tasks.md": "---\nstatus: done\nupdated: 2026-01-05\npriority: 10\n---\nNothing links here #project/beta\n",
    "Beta/Overview.md": (
        "---\nstatus: Active\ntags: overview, team/core\n---\n"
        "Back to [[Alpha/Tasks#Open]] and [md](../Alpha/Tasks.md) and [web](https://example.com/Tasks).\n"
        "Issue #123 is not a tag; `#code` is not one either.\n"
    ),
    "Gamma.md": "No frontmatter here.\n\nPoints at [[alpha/tasks]] in lowercase and [spaced](Beta/Over%20view.md).\n",
    "Beta/Over view.md": "A note with a space in its name.\n",
    "Broken.md": "---\nstatus: [unclosed\n---\nBody with [[Gamma]].\n",
    ".obsidian/Hidden.md": "---\nstatus: active\n---\n[[Gamma]] #project\n",
}


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for name, text in NOTES.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def found(result: str) -> set[str]:
    """The note paths a result lists."""
    return {line.split("**")[1] for line in result.splitlines() if line.startswith("**")}


# ---------------------------------------------------------------- parsing

def test_frontmatter_is_split_from_the_body():
    props, body = ops.split_frontmatter(NOTES["Alpha/Tasks.md"])
    assert props["status"] == "active" and props["priority"] == 3
    assert body.startswith("# Alpha")


def test_broken_or_absent_frontmatter_gives_no_properties():
    assert ops.split_frontmatter(NOTES["Broken.md"])[0] == {}
    assert ops.split_frontmatter(NOTES["Gamma.md"]) == ({}, NOTES["Gamma.md"])


def test_tags_come_from_the_property_and_the_text_but_not_from_code_or_numbers():
    assert ops.note_tags(*ops.split_frontmatter(NOTES["Alpha/Tasks.md"])) == ["project", "team/core", "context"]
    assert ops.note_tags(*ops.split_frontmatter(NOTES["Beta/Overview.md"])) == ["overview", "team/core"]


def test_links_skip_code_web_addresses_and_keep_line_numbers():
    _, body = ops.split_frontmatter(NOTES["Alpha/Tasks.md"])
    assert ops.note_links(body) == [("Beta/Overview", 3, True), ("Gamma", 3, True), ("Tasks", 11, True)]
    _, body = ops.split_frontmatter(NOTES["Beta/Overview.md"])
    assert ops.note_links(body) == [("Alpha/Tasks", 1, True), ("../Alpha/Tasks.md", 1, False)]


# ---------------------------------------------------------------- paths

def test_paths_outside_the_vault_are_refused(vault):
    assert ops.resolve_note(vault, "../outside") is None
    assert ops.resolve_folder(vault, "../..") is None
    assert ops.read(vault, "../outside") == "Error: invalid path."
    assert ops.search(vault, "x", folder="..") == "Folder not found: .."


def test_hidden_folders_are_not_notes(vault):
    assert ".obsidian/Hidden.md" not in {p.relative_to(vault).as_posix() for p in ops.notes(vault, vault)}


# ---------------------------------------------------------------- search

def test_text_search_ignores_case_and_reports_the_line(vault):
    result = ops.search(vault, "NOTHING LINKS")
    assert found(result) == {"Beta/Tasks.md"} and "(line 6)" in result


def test_regex_search(vault):
    assert found(ops.search(vault, r"^status: (active|done)$", regex=True)) == {
        "Alpha/Tasks.md", "Beta/Tasks.md", "Beta/Overview.md"}
    assert found(ops.search(vault, r"^status: active$", regex=True, case_sensitive=True)) == {"Alpha/Tasks.md"}
    assert ops.search(vault, "(", regex=True).startswith("Invalid regular expression")
    assert found(ops.search(vault, "(")) == {"Beta/Overview.md", "Gamma.md"}  # plain text: only a bracket


def test_search_respects_folder_and_limit(vault):
    assert found(ops.search(vault, "status", folder="Beta")) == {"Beta/Tasks.md", "Beta/Overview.md"}
    assert len(found(ops.search(vault, "status", max_results=1))) == 1


# ---------------------------------------------------------------- properties

@pytest.mark.parametrize("operator, value, expected", [
    ("exists", None, {"Alpha/Tasks.md", "Beta/Tasks.md", "Beta/Overview.md"}),
    ("equals", "active", {"Alpha/Tasks.md", "Beta/Overview.md"}),
    ("not_equals", "active", {"Beta/Tasks.md"}),
    ("contains", "ON", {"Beta/Tasks.md"}),
])
def test_property_search_on_status(vault, operator, value, expected):
    assert found(ops.search_property(vault, "Status", operator, value)) == expected


def test_property_search_missing_counts_only_notes_with_frontmatter(vault):
    assert found(ops.search_property(vault, "priority", "missing")) == {"Beta/Overview.md", "Broken.md"}


def test_property_search_orders_numbers_as_numbers_and_dates_as_dates(vault):
    assert found(ops.search_property(vault, "priority", "gt", 5)) == {"Beta/Tasks.md"}
    assert found(ops.search_property(vault, "priority", "lte", "3")) == {"Alpha/Tasks.md"}
    assert found(ops.search_property(vault, "updated", "gte", "2026-06-01")) == {"Alpha/Tasks.md"}
    assert found(ops.search_property(vault, "updated", "lt", "2026-06-01")) == {"Beta/Tasks.md"}


def test_property_search_matches_any_item_of_a_list(vault):
    assert found(ops.search_property(vault, "tags", "equals", "project")) == {"Alpha/Tasks.md"}
    assert found(ops.search_property(vault, "tags", "not_equals", "project")) == {"Beta/Overview.md"}


def test_property_search_refuses_bad_requests(vault):
    assert ops.search_property(vault, "status", "like", "x").startswith("Unknown operator")
    assert ops.search_property(vault, "status", "equals").startswith("Error")
    assert ops.search_property(vault, "status", "equals", "nope").startswith("No notes found")


def test_property_search_counts_past_the_limit(vault):
    result = ops.search_property(vault, "status", max_results=1)
    assert result.startswith("Found 3 notes") and "(showing 1)" in result and len(found(result)) == 1


# ---------------------------------------------------------------- tags

def test_tag_listing_counts_notes(vault):
    result = ops.tags(vault)
    assert "#team/core (2)" in result and "#project (1)" in result and "#project/beta (1)" in result
    assert "not-a-tag" not in result and "#123" not in result and "#code" not in result


def test_tag_lookup_includes_nested_tags(vault):
    assert found(ops.tags(vault, "#Project")) == {"Alpha/Tasks.md", "Beta/Tasks.md"}
    assert found(ops.tags(vault, "team")) == {"Alpha/Tasks.md", "Beta/Overview.md"}
    assert found(ops.tags(vault, "project", folder="Beta")) == {"Beta/Tasks.md"}
    assert ops.tags(vault, "absent") == "No notes found with tag #absent."


# ---------------------------------------------------------------- backlinks

def test_backlinks_by_path_wikilink_any_case_and_markdown_link(vault):
    result = ops.backlinks(vault, "Alpha/Tasks")
    assert found(result) == {"Beta/Overview.md", "Gamma.md"}
    assert "line 5: Back to [[Alpha/Tasks#Open]]" in result  # counted in the file, frontmatter included
    assert "best guess" not in result


def test_a_shared_bare_name_opens_the_note_beside_the_linking_note(vault):
    # Alpha/Tasks links to [[Tasks]]: that is itself, never Beta/Tasks.
    assert ops.backlinks(vault, "Beta/Tasks") == "No notes link to: Beta/Tasks"
    ops.write(vault, "Beta/Plan", "See [[Tasks]].")
    assert found(ops.backlinks(vault, "Beta/Tasks")) == {"Beta/Plan.md"}
    assert found(ops.backlinks(vault, "Alpha/Tasks")) == {"Beta/Overview.md", "Gamma.md"}


def test_a_shared_bare_name_with_no_note_beside_is_a_marked_guess(vault):
    ops.write(vault, "Alpha/Sub/Deep", "See [[Tasks]].")
    ops.write(vault, "Root", "See [[Tasks]].")
    near = ops.backlinks(vault, "Alpha/Tasks")
    assert "**Alpha/Sub/Deep.md**\n  line 1 (best guess)" in near and "fits 2 notes" in near
    assert "**Root.md**\n  line 1 (best guess)" in near  # no shared folder: first by path
    assert ops.backlinks(vault, "Beta/Tasks") == "No notes link to: Beta/Tasks"


def test_wikilink_resolution():
    index = {"tasks": ["alpha/tasks", "beta/tasks", "beta/old/tasks"], "gamma": ["gamma"]}
    assert ops.resolve_wikilink(index, "alpha/x", "Gamma.md") == ("gamma", False)
    assert ops.resolve_wikilink(index, "alpha/x", "Missing") == (None, False)
    assert ops.resolve_wikilink(index, "alpha/x", "Beta/Tasks") == ("beta/tasks", False)
    assert ops.resolve_wikilink(index, "beta/old/x", "Tasks") == ("beta/old/tasks", False)
    assert ops.resolve_wikilink(index, "beta/new/x", "Tasks") == ("beta/tasks", True)
    assert ops.resolve_wikilink(index, "x", "old/Tasks") == ("beta/old/tasks", False)


def test_backlinks_by_alias_and_from_broken_frontmatter(vault):
    assert found(ops.backlinks(vault, "Gamma")) == {"Alpha/Tasks.md", "Broken.md"}


def test_backlinks_through_a_markdown_link_with_an_encoded_space(vault):
    assert found(ops.backlinks(vault, "Beta/Over view")) == {"Gamma.md"}


def test_backlinks_of_an_unlinked_or_missing_note(vault):
    assert ops.backlinks(vault, "Broken") == "No notes link to: Broken"
    assert ops.backlinks(vault, "Nope") == "Note not found: Nope"


# ---------------------------------------------------------------- info

def test_info_reports_properties_tags_headings_and_links(vault):
    result = ops.info(vault, "Alpha/Tasks")
    assert "status: active" in result and "updated: 2026-11-18" in result
    assert "tags: [project, team/core]" in result
    assert "Tags: #project, #team/core, #context" in result
    assert "  Alpha\n    Open" in result
    assert "Links out:\n  Beta/Overview\n  Gamma\n  Tasks" in result
    assert "Not A Link" not in result


def test_info_on_a_plain_note(vault):
    result = ops.info(vault, "Beta/Over view")
    assert "Properties: none" in result and "Tags: none" in result and "Links out: none" in result


# ---------------------------------------------------------------- writing

def test_write_read_and_append(vault):
    assert ops.write(vault, "New/Note", "# Title\n\n## One\n\na\n\n## Two\n\nb") == "Written: New/Note"
    ops.append(vault, "New/Note", "under one", heading="One")
    ops.append(vault, "New/Note", "at the end")
    ops.append(vault, "New/Note", "fresh", heading="Three")
    assert ops.read(vault, "New/Note.md") == (
        "# Title\n\n## One\n\na\n\n\nunder one\n## Two\n\nb\nat the end\n\n## Three\n\nfresh")


def test_append_under_the_last_heading_and_to_a_new_note(vault):
    ops.write(vault, "N", "## Only\n\na")
    ops.append(vault, "N", "b", heading="Only")
    assert ops.read(vault, "N") == "## Only\n\na\nb"
    ops.append(vault, "Fresh", "text", heading="Start")
    assert ops.read(vault, "Fresh") == "## Start\n\ntext"


def test_list_folder(vault):
    assert ops.list_folder(vault) == "📁 Alpha/\n📁 Beta/\n📄 Broken.md\n📄 Gamma.md"
    assert ops.list_folder(vault, "Beta") == "📄 Over view.md\n📄 Overview.md\n📄 Tasks.md"
    assert "📄 Alpha/Tasks.md" in ops.list_folder(vault, recursive=True)
    assert ".obsidian" not in ops.list_folder(vault, recursive=True)


def test_daily_note(vault):
    assert ops.daily(vault, "Daily Notes", "%Y-%m-%d", "read", date_str="2026-11-20").startswith("No daily note")
    ops.daily(vault, "Daily Notes", "%Y-%m-%d", "append", "first", "2026-11-20")
    ops.daily(vault, "Daily Notes", "%Y-%m-%d", "append", "second", "2026-11-20")
    assert ops.read(vault, "Daily Notes/2026-11-20") == "# Friday, November 20, 2026\n\nfirst\nsecond"
    assert ops.daily(vault, "Daily Notes", "%Y-%m-%d", "read", date_str="20 Nov") == "Invalid date format. Use YYYY-MM-DD."
    assert ops.daily(vault, "Daily Notes", "%Y-%m-%d", "append").startswith("Error")
