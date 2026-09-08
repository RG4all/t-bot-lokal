#!/usr/bin/env python3
"""Doku-Waechter fuer t-bot-lokal.

Prueft Markdown-Bestand des Repositories:
  1. Alle lokalen Links zeigen auf existierende Dateien.
  2. Alle Anker (#...) existieren als Ueberschrift des Zieldokuments
     (GitHub-Slug-Algorithmus, inklusive Multi-Hyphen-Verhalten).
  3. Keine verwaisten Dokumente (mindestens ein anderes Dokument muss
     verlinken; README.md/CHANGELOG.md ausgenommen).
  4. Namenskonventionen der docs-Kategorien.
  5. Keine Deep-Links auf CHANGELOG-Release-Anker (fragile GitHub-Slugs).
  6. Hinweise (ohne Fehler): hartcodierte Versionszahlen in README-Dateien,
     wortgleiche Absaetze in mehreren Pflege-Dokumenten.

Exit-Code: 1 bei Fehlern, 0 wenn nur Hinweise. Aufruf: python3 scripts/check_docs.py
"""

from __future__ import annotations

import os
import re
import sys

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "static", "__pycache__"}
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$")
VERSION_RE = re.compile(r"(?<![\w.])\d+\.\d+\.\d+(?![\w.])")

# docs-Wurzeldateien und Kategorie-Erlaubnismuster
DOCS_ROOT_ALLOWED = {"README.md", "CHANGELOG.md"}
TICKET_RE = re.compile(r"^(SEC|BUG|PERF|CODE)-\d{2}-[a-z0-9]+(-[a-z0-9]+)*\.md$")
ADR_RE = re.compile(r"^ADR-\d{4}-[a-z0-9]+(-[a-z0-9]+)*\.md$")
SCREAMING_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]*\.md$")


def gh_slug(heading: str) -> str:
    """GitHub-kompatibler Heading-Slug: lower, Markup-/Satzzeichen raus,
    Leerzeichen -> Bindestrich (keine Laengenkompression)."""
    text = re.sub(r"[*`\[\]()!_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    return text.replace(" ", "-")


def strip_code_fences(lines: list[str]) -> list[bool]:
    """Flag pro Zeile: True = liegt in einem ```-Codeblock (Links ignorieren)."""
    inside, flags = False, []
    for line in lines:
        if line.lstrip().startswith("```"):
            inside = not inside
            flags.append(True)
            continue
        flags.append(inside)
    return flags


def md_files(root: str) -> list[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        found.extend(
            os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/")
            for f in sorted(filenames)
            if f.endswith(".md")
        )
    return found


def headings_of(path: str) -> set[str] | None:
    if not os.path.exists(path):
        return None
    result: set[str] = set()
    in_fence = False
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = HEADING_RE.match(line)
            if match:
                result.add(gh_slug(match.group(1)))
    return result


def main() -> int:
    root = os.getcwd()
    if not os.path.exists(os.path.join(root, "docs")):
        print("FALSCHES VERZEICHNIS: von der Repository-Wurzel aus aufrufen.", file=sys.stderr)
        return 2
    files = md_files(root)
    contents: dict[str, str] = {}
    for rel in files:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as handle:
            contents[rel] = handle.read()
    errors: list[str] = []
    notes: list[str] = []
    referenced: dict[str, int] = {rel: 0 for rel in files}

    for rel, text in contents.items():
        base = os.path.dirname(rel)
        lines = text.splitlines()
        in_code = strip_code_fences(lines)
        for no, line in enumerate(lines, start=1):
            if in_code[no - 1]:
                continue
            for match in LINK_RE.finditer(line):
                link = match.group(1)
                if link.startswith(("http://", "https://", "mailto:")):
                    continue
                target, _, anchor = link.partition("#")
                if target:
                    resolved = os.path.normpath(os.path.join(base, target)).replace(os.sep, "/")
                    if resolved not in contents and not os.path.exists(os.path.join(root, resolved)):
                        errors.append(f"{rel}:{no}: Zieldatei fehlt: {link}")
                        continue
                    if resolved in referenced:
                        referenced[resolved] += 1
                    if resolved.endswith("CHANGELOG.md") and anchor:
                        errors.append(
                            f"{rel}:{no}: Deep-Link auf CHANGELOG-Anker ist nicht portabel: {link}"
                        )
                    anchor_base = os.path.join(root, resolved) if resolved else os.path.join(root, rel)
                else:
                    anchor_base = os.path.join(root, rel)
                if anchor and (not target or resolved.endswith(".md")):
                    available = headings_of(anchor_base)
                    if available is not None and gh_slug(anchor) not in available:
                        errors.append(f"{rel}:{no}: Anker nicht gefunden: {link}")

    for rel, count in referenced.items():
        name = os.path.basename(rel)
        if count == 0 and not re.match(r"^(README|CHANGELOG)\.md$", name):
            errors.append(f"VERWAIST (von keinem Dokument referenziert): {rel}")

    for rel in files:
        parts = rel.split("/")
        if parts[0] != "docs":
            continue
        fname = parts[-1]
        if len(parts) == 2 and fname not in DOCS_ROOT_ALLOWED:
            errors.append(f"STRUKTUR: docs/-Wurzel enthaelt nur README.md/CHANGELOG.md, sonst: {rel}")
        elif parts[1] in {"manual", "operations", "findings", "security", "adr", "archive"} and not (
            TICKET_RE.match(fname) or ADR_RE.match(fname) or SCREAMING_RE.match(fname) or fname in {"README.md", "TEMPLATE.md"}
        ):
            errors.append(f"NAME: unerwartetes Namensmuster: {rel}")

    # Hinweise (nicht fehlerhaft): Versionen in README-Prosa, Duplikat-Absaetze.
    for rel in ("README.md", "docs/README.md"):
        prose = re.sub(r"```.*?```", "", contents[rel], flags=re.DOTALL)
        prose = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", prose)
        prose = re.sub(r"`[^`]*`", " ", prose)
        for match in VERSION_RE.finditer(prose):
            notes.append(f"HINWEIS: Versionszahl hartcodiert in {rel}: {match.group(0)}")
    para_map: dict[str, list[str]] = {}
    for rel, text in contents.items():
        if rel.startswith("docs/archive/") or rel.endswith("CHANGELOG.md"):
            continue
        for para in re.split(r"\n\s*\n", text):
            norm = re.sub(r"\s+", " ", para).strip()
            if len(norm) >= 160 and not norm.startswith(("#", ">", "|", "-")):
                para_map.setdefault(norm, []).append(rel)
    for norm, locs in para_map.items():
        if len(locs) > 1:
            notes.append(
                f"HINWEIS: wortgleicher Absatz in {', '.join(sorted(locs))}: {norm[:60]}..."
            )

    for line in errors:
        print(line)
    for line in notes:
        print(line, file=sys.stderr)
    print(f"check_docs: {len(files)} Dateien, {len(errors)} Fehler, {len(notes)} Hinweise.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
