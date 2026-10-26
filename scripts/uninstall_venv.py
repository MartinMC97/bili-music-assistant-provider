#!/usr/bin/env python3
"""Remove the Bilibili provider from the Music Assistant package.

Run with the Music Assistant Python interpreter:

    /path/to/ma-venv/bin/python scripts/uninstall_venv.py --yes

This only removes ``music_assistant/providers/bilibili``. Music Assistant keeps
the provider configuration in its own data directory; remove the configured
instance in the UI as well (Settings -> Providers -> Bilibili -> Remove).
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import shutil


def find_music_assistant_package() -> pathlib.Path:
    """Return the installed ``music_assistant`` package directory."""
    spec = importlib.util.find_spec("music_assistant")
    if spec is None or not spec.origin:
        raise SystemExit(
            "Cannot import music_assistant with this interpreter. "
            "Use the interpreter of the Music Assistant environment."
        )
    return pathlib.Path(spec.origin).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="actually delete the provider directory (required)",
    )
    args = parser.parse_args()

    target = find_music_assistant_package() / "providers" / "bilibili"
    print(f"Target: {target}")
    if not target.exists():
        print("Provider is not installed; nothing to do.")
        return 0
    if not args.yes:
        print("Refusing to delete without --yes.")
        return 2

    shutil.rmtree(target)
    print("Provider removed. Restart Music Assistant and remove the configured instance in the UI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
