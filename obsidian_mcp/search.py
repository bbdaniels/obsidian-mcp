"""The ranked full text index behind plain-word search.

One SQLite FTS5 table holds one row per section of every note: the text before
the first `## ` or `### ` heading, then each such heading with its text up to
the next one. Frontmatter is left out of the text; the note's path and title
(its file name) are columns of every row, so a query may name them. Rows are
ranked by BM25, and a section whose first line is a supersession marker,
`Superseded YYYY-MM-DD by [[Note#Heading]].`, is ranked after every live one.

The index is a cache, rebuilt from the notes whenever it is missing, damaged or
of another schema: `<vault>/.obsidian/mcp-search-index.db`. Each search first
brings it up to date, comparing every note's modification time and size with
what was indexed, and indexes again only the notes that differ. SQLite's own
locking and a write-ahead log let several sessions search at once: readers never
wait, one writer updates at a time, and a search that cannot get the write lock
in time answers from the index as it stands and says so.

`python -m obsidian_mcp.search "words"` gives the obsidian_search tool's answer
to a session that has no connector.
"""

import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import NamedTuple, Optional

from . import vault as ops

INDEX_FILE = Path(".obsidian") / "mcp-search-index.db"
SCHEMA_VERSION = 1
FTS_MODULE = "fts5"
TOKENIZER = "porter unicode61 remove_diacritics 2"
LOCK_WAIT_SECONDS = 30.0
WEIGHTS = (2.0, 6.0, 3.0, 1.0)  # BM25 weights of the columns path, title, heading and text
SNIPPET_TOKENS = 24

SECTION_HEADING = re.compile(r"^#{2,3}[ \t]+(\S.*?)[ \t]*$", re.MULTILINE)
MARKER = re.compile(r"Superseded (\d{4}-\d{2}-\d{2}) by \[\[[^\]\n]+\]\]\.")
TERM = re.compile(r'"([^"]*)"?|(\S+)')


class IndexUnavailable(Exception):
    """The index cannot be used here; the caller scans instead."""


class Section(NamedTuple):
    heading: str      # "" for the text before the first heading
    line: int         # the heading's line in the file; for the text before any heading, the first body line
    text: str         # the text under the heading, up to the next section
    superseded: str   # the marker's date, or ""


# ---------------------------------------------------------------- sections

def sections(content: str) -> list[Section]:
    """A note's sections, split at `## ` and `### ` headings outside code; at least one."""
    _, body = ops.split_frontmatter(content)
    offset = content.count("\n", 0, len(content) - len(body))
    headings = list(SECTION_HEADING.finditer(ops._mask_code(body)))
    found = []
    first = headings[0].start() if headings else len(body)
    if body[:first].strip():
        found.append(Section("", offset + 1, body[:first].rstrip(), ""))
    for number, match in enumerate(headings):
        end = headings[number + 1].start() if number + 1 < len(headings) else len(body)
        text = body[match.end():end].rstrip()
        lead = next((line.strip() for line in text.splitlines() if line.strip()), "")
        marker = MARKER.fullmatch(lead)
        found.append(Section(body[match.start(1):match.end(1)], offset + body.count("\n", 0, match.start()) + 1,
                             text, marker.group(1) if marker else ""))
    return found or [Section("", offset + 1, "", "")]


# ---------------------------------------------------------------- queries

def terms(query: str) -> list[str]:
    """The query's words and quoted phrases, leaving out any with no word character."""
    found = [word or phrase for phrase, word in TERM.findall(query)]
    return [term for term in found if re.search(r"\w", term)]


def match_expression(query: str) -> str:
    """An FTS5 query requiring every word and quoted phrase, with operators and punctuation taken as text."""
    return " ".join('"' + term.replace('"', '""') + '"' for term in terms(query))


def _first_hit(text: str, query_terms: list[str]) -> Optional[int]:
    """Where the earliest term occurs in the text, ignoring case and punctuation between words."""
    starts = []
    for term in query_terms:
        words = re.findall(r"\w+", term)
        match = re.search(r"\b" + r"\W+".join(map(re.escape, words)), text, re.IGNORECASE)
        if match:
            starts.append(match.start())
    return min(starts) if starts else None


# ---------------------------------------------------------------- the database

def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=LOCK_WAIT_SECONDS, isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA synchronous = NORMAL")
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            _create(db)
        db.execute("SELECT rowid FROM sections LIMIT 0")  # fails here when this SQLite lacks the module
    except BaseException:
        db.close()
        raise
    return db


def _create(db: sqlite3.Connection) -> None:
    """Make the tables, unless another process made them while this one waited for the lock."""
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            db.execute("DROP TABLE IF EXISTS sections")
            db.execute("DROP TABLE IF EXISTS notes")
            db.execute("CREATE TABLE notes (path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER,"
                       " first_row INTEGER, last_row INTEGER)")
            db.execute(f"CREATE VIRTUAL TABLE sections USING {FTS_MODULE}(path, title, heading, text,"
                       f" line UNINDEXED, superseded UNINDEXED, tokenize = '{TOKENIZER}')")
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def open_index(vault: Path) -> sqlite3.Connection:
    """A connection to the vault's index, rebuilding a database that cannot be read.

    Switching a new database to the write-ahead log can report it locked at once,
    without waiting, while another process does the same; that is retried until
    LOCK_WAIT_SECONDS have passed.
    """
    path = vault / INDEX_FILE
    try:
        path.parent.mkdir(exist_ok=True)
    except OSError as error:
        raise IndexUnavailable(f"cannot create {INDEX_FILE.parent}: {error.strerror}") from error
    deadline, rebuilt = time.monotonic() + LOCK_WAIT_SECONDS, False
    while True:
        try:
            return _connect(path)
        except sqlite3.OperationalError as error:
            if "no such module" in str(error):
                raise IndexUnavailable(f"this Python's SQLite {sqlite3.sqlite_version} has no {FTS_MODULE.upper()}") from error
            if "locked" not in str(error) or time.monotonic() > deadline:
                raise IndexUnavailable(f"cannot open {INDEX_FILE}: {error}") from error
            time.sleep(0.05)
        except sqlite3.DatabaseError as error:
            if rebuilt:
                raise IndexUnavailable(f"cannot rebuild {INDEX_FILE}: {error}") from error
            _remove(path)
            rebuilt = True


def _remove(path: Path) -> None:
    """Delete a database with its write-ahead log and shared memory files."""
    for leftover in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        leftover.unlink(missing_ok=True)


def _on_disk(vault: Path) -> dict[str, tuple[int, int]]:
    """Every note's vault path with its modification time in nanoseconds and its size."""
    root = Path(os.path.abspath(vault))
    found, prefix = {}, len(root.as_posix().rstrip("/")) + 1
    for path in ops.notes(root, root):
        try:
            stat = path.stat()
        except OSError:
            continue
        found[path.as_posix()[prefix:]] = (stat.st_mtime_ns, stat.st_size)  # Path.relative_to is slow here
    return found


def _indexed(db: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    return {path: (mtime, size) for path, mtime, size in db.execute("SELECT path, mtime_ns, size FROM notes")}


def update(db: sqlite3.Connection, vault: Path) -> int:
    """Index again every note that is new, changed or gone since the last update; the number of them.

    Raises sqlite3.OperationalError when another process holds the write lock past LOCK_WAIT_SECONDS.
    """
    disk = _on_disk(vault)
    if _indexed(db) == disk:
        return 0
    db.execute("BEGIN IMMEDIATE")
    try:
        known = _indexed(db)  # read again under the lock: another process may have done the work
        stale = [path for path in known if disk.get(path) != known[path]]
        fresh = [path for path in disk if known.get(path) != disk[path]]
        for path in stale:
            first, last = db.execute("SELECT first_row, last_row FROM notes WHERE path = ?", (path,)).fetchone()
            db.execute("DELETE FROM sections WHERE rowid BETWEEN ? AND ?", (first, last))
            db.execute("DELETE FROM notes WHERE path = ?", (path,))
        row = (db.execute("SELECT max(last_row) FROM notes").fetchone()[0] or 0) + 1
        for path in fresh:
            content = ops._read(vault / path) or ""
            title = path.rsplit("/", 1)[-1][:-3]
            parts = sections(content)
            db.executemany("INSERT INTO sections (rowid, path, title, heading, text, line, superseded)"
                           " VALUES (?, ?, ?, ?, ?, ?, ?)",
                           [(row + n, path, title, s.heading, s.text, s.line, s.superseded)
                            for n, s in enumerate(parts)])
            db.execute("INSERT INTO notes VALUES (?, ?, ?, ?, ?)", (path, *disk[path], row, row + len(parts) - 1))
            row += len(parts)
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    return len(set(stale) | set(fresh))


# ---------------------------------------------------------------- search

def ranked(vault: Path, query: str, folder: str = "", max_results: int = 10) -> Optional[str]:
    """The answer to a plain-word query from the index, or None when the query has no words to look up.

    `folder` is a vault path ('' for the whole vault). Raises IndexUnavailable when the index cannot be used.
    """
    expression = match_expression(query)
    if not expression:
        return None
    for attempt in (1, 2):
        db = open_index(vault)
        try:
            return _answer(db, vault, query, expression, folder, max_results)
        except sqlite3.OperationalError as error:
            raise IndexUnavailable(f"{INDEX_FILE}: {error}") from error
        except sqlite3.DatabaseError as error:
            if attempt == 2:
                raise IndexUnavailable(f"{INDEX_FILE}: {error}") from error
            _remove(vault / INDEX_FILE)  # damaged after it opened: build it again
        finally:
            db.close()
    raise AssertionError("unreachable")


def _answer(db: sqlite3.Connection, vault: Path, query: str, expression: str, folder: str, max_results: int) -> str:
    note = ""
    try:
        update(db, vault)
    except sqlite3.OperationalError as error:
        if "locked" not in str(error) and "busy" not in str(error):
            raise
        known, disk = _indexed(db), _on_disk(vault)
        waiting = len({path for path in disk if known.get(path) != disk[path]} | (set(known) - set(disk)))
        note = (f"Note: another session is updating the index, so these results may miss "
                f"{waiting} changed note{'s' if waiting != 1 else ''}.\n\n")

    where, parameters = "sections MATCH ?", [expression]
    if folder:
        where += " AND (path = ? OR substr(path, 1, ?) = ?)"
        parameters += [folder, len(folder) + 1, folder + "/"]
    total = db.execute(f"SELECT count(*) FROM sections WHERE {where}", parameters).fetchone()[0]
    if not total:
        where_said = f" in {folder}" if folder else ""
        return (f"{note}No notes found matching: {query}\n(Searched note text, titles, paths and headings{where_said}"
                " for every word; frontmatter is not searched: use obsidian_search_property for it, or regex: true"
                " for a literal scan of the whole file.)")
    weights = ", ".join(map(str, WEIGHTS))
    rows = db.execute(
        f"SELECT path, heading, line, superseded, text,"
        f" snippet(sections, 3, '', '', '...', {SNIPPET_TOKENS}) FROM sections WHERE {where}"
        f" ORDER BY superseded != '', bm25(sections, {weights}), path, line LIMIT ?",
        parameters + [max(1, max_results)]).fetchall()

    query_terms = terms(query)
    results = []
    for path, heading, line, superseded, text, snippet in rows:
        hit = _first_hit(text, query_terms)
        at = line + (text.count("\n", 0, hit) if hit is not None else 0)
        head = f"**{path}**" + (f" > {heading}" if heading else "") + f" (line {at})"
        head += f", superseded: {superseded}" if superseded else ""
        snippet = " ".join(snippet.split())
        results.append(head + (f"\n  {snippet}" if snippet else ""))
    shown = f", showing {len(results)}" if total > len(results) else ""
    return (f"{note}Found {total} section{'s' if total != 1 else ''}, ranked by relevance with superseded ones"
            f" last{shown}:\n\n" + "\n\n".join(results))


# ---------------------------------------------------------------- command line

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m obsidian_mcp.search",
        description="Search an Obsidian vault and print what the obsidian_search tool answers.")
    parser.add_argument("query", nargs="+", help="Words to find (all required); quote a phrase to keep it")
    parser.add_argument("--vault", help="The vault (default: OBSIDIAN_VAULT_PATH, else the saved configuration)")
    parser.add_argument("--folder", default="", help="Limit to this folder")
    parser.add_argument("--max-results", type=int, default=10, help="Maximum results (default: 10)")
    parser.add_argument("--regex", action="store_true", help="Treat the query as a Python regular expression")
    parser.add_argument("--case-sensitive", action="store_true", help="Match case exactly (scans the files)")
    args = parser.parse_args(argv)

    if args.vault:
        vault = Path(args.vault).expanduser()
    else:
        from .server import get_vault_path  # the server's own rule: the environment variable, else the saved configuration
        vault = get_vault_path()
    if not vault:
        print("No vault configured: pass --vault, or set OBSIDIAN_VAULT_PATH.", file=sys.stderr)
        return 2
    print(ops.search(vault, " ".join(args.query), args.folder, args.max_results, args.regex, args.case_sensitive))
    return 0


if __name__ == "__main__":
    sys.exit(main())
