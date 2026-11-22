"""Vault operations: everything the server does to an Obsidian vault.

Each public function takes the vault root and plain arguments and returns the
text the tool answers with. Nothing here knows about MCP, so it is tested
directly (tests/test_vault.py).
"""

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import quote, unquote

import yaml

FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.DOTALL)
FENCE = re.compile(r"^(```|~~~).*?^\1[ \t]*$", re.DOTALL | re.MULTILINE)
INLINE_CODE = re.compile(r"`[^`\n]*`")
# A tag starts a line or follows whitespace, and is not all digits.
INLINE_TAG = re.compile(r"(?:^|(?<=\s))#([A-Za-z0-9_][\w/-]*)", re.MULTILINE)
WIKILINK = re.compile(r"!?\[\[([^\]\|\n]+?)(?:\|[^\]\n]*)?\]\]")
MD_LINK = re.compile(r"(?<!\!)\[[^\]\n]*\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

OPERATORS = ("exists", "missing", "equals", "not_equals", "contains", "gt", "gte", "lt", "lte")


# ---------------------------------------------------------------- paths

def resolve_note(vault: Path, note_path: str) -> Optional[Path]:
    """A note path inside the vault, with .md added; None when it escapes."""
    if not note_path.endswith(".md"):
        note_path += ".md"
    return _inside(vault, vault / note_path)


def resolve_folder(vault: Path, folder: str) -> Optional[Path]:
    """A folder inside the vault ('' is the root); None when it escapes."""
    return _inside(vault, vault / folder if folder else vault)


def _inside(vault: Path, path: Path) -> Optional[Path]:
    try:
        path.resolve().relative_to(vault.resolve())
    except ValueError:
        return None
    return path


def notes(vault: Path, folder: Path) -> Iterator[Path]:
    """Every note under a folder in path order, skipping hidden files and folders (.obsidian, .git, .trash).

    Hidden folders are pruned from the walk rather than filtered after it, so a large .git costs nothing.
    """
    if any(part.startswith(".") for part in folder.relative_to(vault).parts):
        return
    found = []
    for top, folders, files in os.walk(folder):
        folders[:] = [name for name in folders if not name.startswith(".")]
        found += [os.path.join(top, name) for name in files if name.endswith(".md") and not name.startswith(".")]
    for path in sorted(found, key=lambda path: path.split(os.sep)):  # the order of Path objects, by part
        yield Path(path)


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------- parsing

def split_frontmatter(text: str) -> tuple[dict, str]:
    """The note's properties and its body. Unparseable frontmatter gives no properties."""
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    try:
        props = yaml.load(match.group(1), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    except yaml.YAMLError:
        props = None
    return (props if isinstance(props, dict) else {}), text[match.end():]


def _prose(body: str) -> str:
    """The body without code, where a '#' or '[[' is not a tag or a link."""
    return INLINE_CODE.sub("", FENCE.sub("", body))


def note_tags(props: dict, body: str) -> list[str]:
    """Tags from the `tags` property and from #tags in the text, without '#', in first-seen order."""
    raw = props.get("tags", props.get("tag"))
    if isinstance(raw, str):
        found = re.split(r"[,\s]+", raw)
    elif isinstance(raw, list):
        found = [str(t) for t in raw if t is not None]
    else:
        found = []
    found = [t.strip().lstrip("#") for t in found]
    found += [t for t in INLINE_TAG.findall(_prose(body)) if not t.isdigit()]
    return list(dict.fromkeys(t for t in found if t))


def note_links(body: str) -> list[tuple[str, int, bool]]:
    """Links in the text to other files: (target, line number, whether it is a wikilink).

    Markdown links to web addresses and to anchors in the same note are left out.
    """
    prose = _mask_code(body)
    links = []
    for pattern, wiki in ((WIKILINK, True), (MD_LINK, False)):
        for match in pattern.finditer(prose):
            target = match.group(1).strip()
            if not wiki:
                if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                    continue  # a web address or an anchor in the same note
                target = unquote(target)
            target = target.split("#", 1)[0].split("^", 1)[0].strip()
            if target:
                links.append((target, prose.count("\n", 0, match.start()) + 1, wiki))
    return sorted(links, key=lambda link: link[1])


def _mask_code(body: str) -> str:
    """The body with code blanked out but every line still in place."""
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))
    return INLINE_CODE.sub(blank, FENCE.sub(blank, body))


def note_index(vault: Path) -> dict[str, list[str]]:
    """Every note's vault path without .md, in lowercase, listed under its lowercase name."""
    index: dict[str, list[str]] = {}
    for path in notes(vault, vault):
        key = path.relative_to(vault).as_posix()[:-3].lower()
        index.setdefault(key.rsplit("/", 1)[-1], []).append(key)
    return index


def resolve_wikilink(index: dict[str, list[str]], source_key: str, target: str) -> tuple[Optional[str], bool]:
    """The note a wikilink written in `source_key` opens, as Obsidian resolves it, and whether that was a guess.

    A target names a note by its full path or by any ending of it, down to the
    bare name. When several notes fit, the one in the linking note's own folder
    wins, then the one sharing the most leading folders with it; that last
    choice is reported as a guess, since Obsidian's own tie-break is not documented.
    """
    key = target[:-3] if target.lower().endswith(".md") else target
    key = key.strip("/").lower()
    fits = [note for note in index.get(key.rsplit("/", 1)[-1], [])
            if note == key or note.endswith("/" + key)]
    if len(fits) <= 1:
        return (fits[0] if fits else None), False
    if key in fits and "/" in key:
        return key, False  # a full path from the vault root
    folder = source_key.rsplit("/", 1)[0].split("/") if "/" in source_key else []
    beside = "/".join(folder + [key])
    if beside in fits:
        return beside, False

    def shared(note: str) -> int:
        count = 0
        for ours, theirs in zip(folder, note.split("/")[:-1]):
            if ours != theirs:
                break
            count += 1
        return count

    return min(fits, key=lambda note: (-shared(note), note.count("/"), note)), True


def _markdown_link_points_to(vault: Path, source: Path, target: str, note: Path) -> bool:
    """A Markdown link is a path relative to the linking note, or to the vault."""
    resolved = str(note.resolve())
    return any(str((base / target).resolve()) in (resolved, resolved[:-3]) for base in (source.parent, vault))


# ---------------------------------------------------------------- tools

def search(vault: Path, query: str, folder: str = "", max_results: int = 10,
           regex: bool = False, case_sensitive: bool = False) -> str:
    """A plain-word query is answered from the ranked index of sections (search.py).

    A regular expression, a case-sensitive query, or one with no word in it is
    answered by scanning the files in path order, as is any query where the
    index cannot be used, and then the answer says why.
    """
    root = resolve_folder(vault, folder)
    if root is None or not root.exists():
        return f"Folder not found: {folder}"
    note = ""
    if not regex and not case_sensitive:
        from . import search as index
        within = root.relative_to(vault).as_posix()
        try:
            answer = index.ranked(vault, query, "" if within == "." else within, max_results)
        except index.IndexUnavailable as why:
            answer = None
            note = f"Note: the full text index is unavailable ({why}), so this is an unranked scan in path order.\n\n"
        if answer is not None:
            return answer
    return note + _scan(vault, root, query, max_results, regex, case_sensitive)


def _scan(vault: Path, root: Path, query: str, max_results: int, regex: bool, case_sensitive: bool) -> str:
    """Every note under root in path order, with its first match of a literal text or a regular expression."""
    try:
        pattern = re.compile(query if regex else re.escape(query),
                             (0 if case_sensitive else re.IGNORECASE) | re.MULTILINE)
    except re.error as error:
        return f"Invalid regular expression: {error}"

    results = []
    for path in notes(vault, root):
        content = _read(path)
        match = pattern.search(content) if content is not None else None
        if not match:
            continue
        start, end = max(0, match.start() - 50), min(len(content), match.end() + 50)
        snippet = content[start:end].replace("\n", " ").strip()
        snippet = ("..." if start > 0 else "") + snippet + ("..." if end < len(content) else "")
        line = content.count("\n", 0, match.start()) + 1
        results.append(f"**{path.relative_to(vault)}** (line {line})\n  {snippet}")
        if len(results) >= max_results:
            break
    if not results:
        return f"No notes found matching: {query}"
    return f"Found {len(results)} results:\n\n" + "\n\n".join(results)


def info(vault: Path, note_path: str) -> str:
    path = resolve_note(vault, note_path)
    if path is None:
        return "Error: invalid path."
    content = _read(path) if path.exists() else None
    if content is None:
        return f"Note not found: {note_path}"
    props, body = split_frontmatter(content)
    stat = path.stat()
    lines = [f"**{path.relative_to(vault)}**",
             f"Modified: {datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M}",
             f"Size: {stat.st_size} bytes, {len(body.split())} words"]
    lines.append("\nProperties:" if props else "\nProperties: none")
    lines += [f"  {key}: {_show(value)}" for key, value in props.items()]
    tags = note_tags(props, body)
    lines.append("\nTags: " + (", ".join("#" + t for t in tags) if tags else "none"))
    headings = HEADING.findall(_mask_code(body))
    lines.append("\nHeadings:" if headings else "\nHeadings: none")
    lines += [f"  {'  ' * (len(marks) - 1)}{title}" for marks, title in headings]
    targets = list(dict.fromkeys(target for target, _, _ in note_links(body)))
    lines.append("\nLinks out:" if targets else "\nLinks out: none")
    lines += [f"  {target}" for target in targets]
    return "\n".join(lines)


def search_property(vault: Path, prop: str, operator: str = "exists", value: Any = None,
                    folder: str = "", max_results: int = 50) -> str:
    if operator not in OPERATORS:
        return f"Unknown operator: {operator}. Use one of: {', '.join(OPERATORS)}."
    if operator not in ("exists", "missing") and value is None:
        return f"Error: operator '{operator}' needs a value."
    root = resolve_folder(vault, folder)
    if root is None or not root.exists():
        return f"Folder not found: {folder}"

    wanted = prop.lower()
    results, total = [], 0
    for path in notes(vault, root):
        content = _read(path)
        if content is None or not content.startswith("---"):
            continue
        props, _ = split_frontmatter(content)
        key = next((k for k in props if str(k).lower() == wanted), None)
        if operator == "missing":
            hit = key is None
        else:
            hit = key is not None and (operator == "exists" or _compare(props[key], operator, value))
        if hit:
            total += 1
            if len(results) < max_results:
                shown = "" if key is None else f"\n  {key}: {_show(props[key])}"
                results.append(f"**{path.relative_to(vault)}**{shown}")
    if not results:
        return f"No notes found where {_describe(prop, operator, value)}."
    more = f" (showing {len(results)})" if total > len(results) else ""
    return f"Found {total} notes where {_describe(prop, operator, value)}{more}:\n\n" + "\n\n".join(results)


def _describe(prop: str, operator: str, value: Any) -> str:
    return f"{prop} {operator}" + ("" if operator in ("exists", "missing") else f" {value}")


def _show(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_show(v) for v in value) + "]"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _compare(actual: Any, operator: str, value: Any) -> bool:
    """A list property matches when any of its items does (all of them for not_equals)."""
    if isinstance(actual, list):
        if operator == "not_equals":
            return all(_compare(item, operator, value) for item in actual)
        return any(_compare(item, operator, value) for item in actual)
    if operator == "contains":
        return str(value).lower() in _show(actual).lower()
    left, right = _comparable(actual), _comparable(value)
    if type(left) is not type(right):
        left, right = _show(actual).strip().lower(), str(value).strip().lower()
    if operator == "equals":
        return left == right
    if operator == "not_equals":
        return left != right
    return {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[operator]


def _comparable(value: Any) -> Any:
    """A number as a float and anything else as lowercase text (ISO dates order correctly as text)."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return float(value)
    text = _show(value).strip().lower()
    try:
        return float(text)
    except ValueError:
        return text


def tags(vault: Path, tag: str = "", folder: str = "", max_results: int = 50) -> str:
    """With no tag, every tag and how many notes carry it; with one, those notes.

    A tag also matches its nested tags: `project` finds `project/alpha`.
    """
    root = resolve_folder(vault, folder)
    if root is None or not root.exists():
        return f"Folder not found: {folder}"
    wanted = tag.strip().lstrip("#").lower().rstrip("/")
    counts: dict[str, int] = {}
    carriers = []
    for path in notes(vault, root):
        content = _read(path)
        if content is None or "#" not in content and "tag" not in content:
            continue
        found = note_tags(*split_frontmatter(content))
        if not wanted:
            for name in found:
                counts[name] = counts.get(name, 0) + 1
            continue
        matched = [t for t in found if t.lower() == wanted or t.lower().startswith(wanted + "/")]
        if matched:
            carriers.append(f"**{path.relative_to(vault)}**  " + " ".join("#" + t for t in matched))
    if not wanted:
        if not counts:
            return "No tags found."
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
        return f"{len(ordered)} tags:\n\n" + "\n".join(f"#{name} ({count})" for name, count in ordered)
    if not carriers:
        return f"No notes found with tag #{wanted}."
    more = f" (showing {max_results})" if len(carriers) > max_results else ""
    return f"Found {len(carriers)} notes with tag #{wanted}{more}:\n\n" + "\n".join(carriers[:max_results])


def backlinks(vault: Path, note_path: str, max_results: int = 50) -> str:
    """Notes that link to a note, by wikilink or Markdown link, with the linking lines."""
    path = resolve_note(vault, note_path)
    if path is None:
        return "Error: invalid path."
    if not path.exists():
        return f"Note not found: {note_path}"
    note_key = path.relative_to(vault).as_posix()[:-3]
    name = path.stem.lower()
    encoded = quote(name).lower()  # as a Markdown link writes a name with spaces
    index = note_index(vault)

    results, guesses = [], 0
    for source in notes(vault, vault):
        if source == path:
            continue
        content = _read(source)
        lowered = content.lower() if content is not None else ""
        if name not in lowered and encoded not in lowered:  # cheap test before parsing links
            continue
        _, body = split_frontmatter(content)
        offset = content.count("\n", 0, len(content) - len(body))
        body_lines = body.split("\n")
        source_key = source.relative_to(vault).as_posix()[:-3].lower()
        hits = []
        for target, line, wiki in note_links(body):
            guess = False
            if wiki:
                resolved, guess = resolve_wikilink(index, source_key, target)
                if resolved != note_key.lower():
                    continue
            elif not _markdown_link_points_to(vault, source, target, path):
                continue
            guesses += guess
            hits.append(f"  line {line + offset}{' (best guess)' if guess else ''}: {body_lines[line - 1].strip()[:200]}")
        if hits:
            results.append(f"**{source.relative_to(vault)}**\n" + "\n".join(dict.fromkeys(hits)))
    if not results:
        return f"No notes link to: {note_key}"
    more = f" (showing {max_results})" if len(results) > max_results else ""
    note = (f"\n\n{guesses} links marked best guess name '{path.stem}' in a way that fits "
            f"{len(index[name])} notes; this is the nearest one to the linking note.") if guesses else ""
    return f"{len(results)} notes link to {note_key}{more}:\n\n" + "\n\n".join(results[:max_results]) + note


def read(vault: Path, note_path: str) -> str:
    path = resolve_note(vault, note_path)
    if path is None:
        return "Error: invalid path."
    if not path.exists():
        return f"Note not found: {note_path}"
    return path.read_text(encoding="utf-8")


def write(vault: Path, note_path: str, content: str) -> str:
    path = resolve_note(vault, note_path)
    if path is None:
        return "Error: invalid path."
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"Written: {note_path}"


def append(vault: Path, note_path: str, content: str, heading: Optional[str] = None) -> str:
    path = resolve_note(vault, note_path)
    if path is None:
        return "Error: invalid path."
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        match = re.search(rf"^(#{{1,6}})\s+{re.escape(heading)}\s*$", existing, re.MULTILINE) if heading else None
        if match:
            # The section ends at the next heading of the same or a higher level, or at the end.
            following = re.search(rf"^#{{1,{len(match.group(1))}}}\s+", existing[match.end():], re.MULTILINE)
            if following:
                at = match.end() + following.start()
                new = existing[:at] + "\n" + content + "\n" + existing[at:]
            else:
                new = existing + "\n" + content
        elif heading:
            new = existing + f"\n\n## {heading}\n\n{content}"
        else:
            new = existing + "\n" + content
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = f"## {heading}\n\n{content}" if heading else content
    path.write_text(new, encoding="utf-8")
    return f"Appended to: {note_path}"


def list_folder(vault: Path, folder: str = "", recursive: bool = False) -> str:
    root = resolve_folder(vault, folder)
    if root is None or not root.exists():
        return f"Folder not found: {folder}"
    items = []
    for item in sorted(root.rglob("*") if recursive else root.iterdir()):
        relative = item.relative_to(vault if recursive else root)
        if any(part.startswith(".") for part in item.relative_to(vault).parts):
            continue
        if item.is_dir():
            items.append(f"📁 {relative}/")
        elif item.suffix == ".md":
            items.append(f"📄 {relative}")
    return "\n".join(items) if items else "No notes or folders found."


def daily(vault: Path, folder: str, date_format: str, action: str,
          content: Optional[str] = None, date_str: Optional[str] = None) -> str:
    if date_str:
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return "Invalid date format. Use YYYY-MM-DD."
    else:
        day = datetime.now()
    filename = day.strftime(date_format) + ".md"
    path = vault / folder / filename
    if action == "read":
        if not path.exists():
            return f"No daily note for {day:%Y-%m-%d}. It will be created when you append to it."
        return path.read_text(encoding="utf-8")
    if action == "append":
        if not content:
            return "Error: content required for append action."
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            new = path.read_text(encoding="utf-8") + "\n" + content
        else:
            new = f"# {day:%A, %B %d, %Y}\n\n{content}"
        path.write_text(new, encoding="utf-8")
        return f"Appended to daily note: {folder}/{filename}"
    return f"Unknown action: {action}"
