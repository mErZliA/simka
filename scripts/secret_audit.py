#!/usr/bin/env python3
"""Report likely Telegram credentials without ever printing their values."""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

TOKEN_RE = re.compile(rb"(?m)^\s*BOT_TOKEN\s*=\s*['\"]?([0-9]{5,}:[A-Za-z0-9_-]{20,})")
HASH_RE = re.compile(rb"(?m)^\s*API_HASH\s*=\s*['\"]?([0-9a-fA-F]{32})\b")


def findings(data: bytes) -> list[str]:
    result = []
    if TOKEN_RE.search(data):
        result.append("BOT_TOKEN")
    if HASH_RE.search(data):
        result.append("API_HASH")
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    found = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or any(
            part in {".venv", "node_modules", "backups", "logs", "__pycache__"}
            for part in path.parts
        ):
            continue
        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as archive:
                    for member in archive.namelist():
                        if member.endswith((".env", ".env.example")):
                            keys = findings(archive.read(member))
                            if keys:
                                found.append(f"{path.relative_to(root)}!{member}: {', '.join(keys)}")
            elif path.name == ".env" or path.name.startswith(".env."):
                keys = findings(path.read_bytes())
                if keys:
                    found.append(f"{path.relative_to(root)}: {', '.join(keys)}")
        except (OSError, zipfile.BadZipFile):
            continue
    if found:
        print("Possible credentials found (values intentionally redacted):")
        for item in sorted(set(found)):
            print("  -", item)
        print("Rotate exposed keys and remove the containing artifact from future releases.")
        return 1
    print("No Telegram token/API hash patterns found in env files or archives.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
