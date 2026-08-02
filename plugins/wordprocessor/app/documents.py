"""Document storage: one plain-text file per story.

Lives outside plugins/wordprocessor/ deliberately — scripts/plugin-install.sh
does `rm -rf` on a plugin's own install directory on every update (see
CLAUDE.md's Plugin System section), so anything saved inside this plugin's
own tree would be destroyed the next time it's redeployed. DOCUMENTS_DIR is a
sibling path instead; scripts/install.sh's rsync --delete step excludes it
for the same reason (a fresh repo checkout doesn't know about a child's
saved stories and would otherwise wipe them on every re-run).

Retention policy (how long documents persist, whether they ever leave the
machine) is still an open decision — see pre-build-decisions.md and
TODO.md's Phase 12 notes. This module only gives documents a durable,
plugin-update-safe home; it does not resolve that question.
"""

import os
from datetime import datetime
from pathlib import Path

DOCUMENTS_DIR = Path(os.environ.get("CLASSPAD_DOCUMENTS_DIR", "/opt/classpad/documents/wordprocessor"))

DEFAULT_TITLE = "New Story"


class DocMeta:
    def __init__(self, path, title, mtime):
        self.path = path
        self.title = title
        self.mtime = mtime


def _ensure_dir(documents_dir):
    documents_dir.mkdir(parents=True, exist_ok=True)


def create_document(documents_dir=None):
    """Create and return the path to a new, empty document.

    Written to disk immediately (not just held in memory) so a story has a
    real backing file from the moment "New Story" is clicked — a SIGKILL a
    second later loses nothing, because there was never a window where the
    document existed only in memory.
    """
    documents_dir = Path(documents_dir) if documents_dir is not None else DOCUMENTS_DIR
    _ensure_dir(documents_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = documents_dir / f"story-{stamp}.txt"
    suffix = 2
    while path.exists():
        path = documents_dir / f"story-{stamp}-{suffix}.txt"
        suffix += 1
    path.write_text("")
    return path


def write_document(path, text):
    """Atomic write: readers (list_documents/read_document) never observe a
    partially-written file, and a SIGKILL mid-flush can corrupt only the
    in-flight temp file, never the document itself.
    """
    tmp_path = path.with_suffix(".txt.tmp")
    tmp_path.write_text(text)
    os.replace(tmp_path, path)


def read_document(path):
    return path.read_text()


def delete_document(path):
    path.unlink(missing_ok=True)


def parse_content(text):
    """Split raw file content into (title, author, body).

    Line 1 is always the title and line 2 is always the author — both may be
    empty strings — with the story itself starting at line 3. This is the
    inverse of compose_content(); title/author never contain "\\n" (they're
    edited through single-line fields), so the two round-trip exactly.
    """
    lines = text.split("\n")
    title = lines[0] if len(lines) > 0 else ""
    author = lines[1] if len(lines) > 1 else ""
    body = "\n".join(lines[2:]) if len(lines) > 2 else ""
    return title, author, body


def compose_content(title, author, body):
    return f"{title}\n{author}\n{body}"


def _title_from_content(text):
    title, author, body = parse_content(text)
    title = title.strip()
    author = author.strip()
    if title and author:
        return f"{title} by {author}"
    if title:
        return title
    if author:
        return f"by {author}"
    # No title/author set (or this file predates that scheme) — fall back to
    # the first non-blank line of the story itself, same as the old behaviour.
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return DEFAULT_TITLE


def list_documents(documents_dir=None):
    documents_dir = Path(documents_dir) if documents_dir is not None else DOCUMENTS_DIR
    if not documents_dir.exists():
        return []
    docs = []
    for path in documents_dir.glob("*.txt"):
        try:
            text = path.read_text()
            mtime = path.stat().st_mtime
        except OSError:
            continue
        docs.append(DocMeta(path, _title_from_content(text), mtime))
    docs.sort(key=lambda d: d.mtime, reverse=True)
    return docs
