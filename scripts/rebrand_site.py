#!/usr/bin/env python3
"""Rebrand a copied web site before it goes into this (public) repository.

    python3 scripts/rebrand_site.py <copied_site_dir> <out_dir> --map <private_map.json>

The map names the original brand, so it never goes into this repository: keep it next to the task
the page was copied for (a private workspace). Its form:

    {
      "replace": [["regex", "replacement"], ...],   applied in order to every text file
      "files":   {"logo.svg": "<svg ...>", ...},       files to overwrite (logos, favicons)
      "forbid":  ["word", ...]                         must not be left anywhere (case-insensitive)
    }

Put longer phrases before the words they contain ("The Acme App" before "Acme"), and replace
identifiers everywhere they occur, so that a code the page compares (an operator code, an error
class) is renamed in the same way in the page and in its mock backend.

The script copies <copied_site_dir> to <out_dir> (replacing it), rewrites it, and exits 1 when a
forbidden word is left - the list of what and where is printed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys

TEXT_SUFFIXES = {".html", ".htm", ".js", ".mjs", ".css", ".json", ".txt", ".svg", ".webmanifest", ".map"}


def rebrand(src: pathlib.Path, dst: pathlib.Path, mapping: dict) -> list[str]:
    """Copy src to dst, apply the map, return what forbidden words are left (empty when clean)."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    rules = [(re.compile(pattern), repl) for pattern, repl in mapping.get("replace", [])]
    for path in sorted(dst.rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
            for rule, repl in rules:
                text = rule.sub(repl, text)
            path.write_text(text, encoding="utf-8", errors="surrogateescape")
    for rel, content in mapping.get("files", {}).items():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    left = []
    for path in sorted(dst.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for word in mapping.get("forbid", []):
            count = len(re.findall(re.escape(word), text, re.IGNORECASE))
            if count:
                left.append(f"{path.relative_to(dst)}: {word!r} x{count}")
    return left


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebrand a copied web site (see the module docstring).")
    parser.add_argument("src", type=pathlib.Path)
    parser.add_argument("dst", type=pathlib.Path)
    parser.add_argument(
        "--map", type=pathlib.Path, required=True, help="private JSON map, never committed here"
    )
    args = parser.parse_args()
    left = rebrand(args.src, args.dst, json.loads(args.map.read_text(encoding="utf-8")))
    if left:
        print("forbidden words left:\n  " + "\n  ".join(left))
        return 1
    print(f"rebranded into {args.dst}: no forbidden word left")
    return 0


if __name__ == "__main__":
    sys.exit(main())
