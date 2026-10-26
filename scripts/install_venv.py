#!/usr/bin/env python3
"""Install the Bilibili provider into the Music Assistant package.

Run this with the Python interpreter of the Music Assistant environment, e.g.:

    /path/to/ma-venv/bin/python scripts/install_venv.py

The script never downloads anything. It copies ``provider/bilibili`` into the
``music_assistant/providers`` directory of the interpreter that runs it, so it
also works for source checkouts and virtualenv installations.

Music Assistant upgrades/reinstalls overwrite the package directory; re-run this
script afterwards.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import shutil
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "provider" / "bilibili"


def find_music_assistant_package() -> pathlib.Path:
    """Return the installed ``music_assistant`` package directory."""
    spec = importlib.util.find_spec("music_assistant")
    if spec is None or not spec.origin:
        raise SystemExit(
            "Cannot import music_assistant with this interpreter.\n"
            "Run the script with the Python interpreter used by Music Assistant, e.g.\n"
            "  /path/to/ma-venv/bin/python scripts/install_venv.py"
        )
    return pathlib.Path(spec.origin).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=pathlib.Path,
        default=DEFAULT_SOURCE,
        help="provider source directory (default: provider/bilibili)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing providers/bilibili directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the target path without copying anything",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    if not (source / "manifest.json").is_file():
        raise SystemExit(f"Not a provider directory (manifest.json missing): {source}")

    package_dir = find_music_assistant_package()
    target = package_dir / "providers" / "bilibili"

    print(f"Music Assistant package: {package_dir}")
    print(f"Source:                  {source}")
    print(f"Target:                  {target}")

    if args.dry_run:
        print("Dry run: nothing copied.")
        return 0

    if target.exists():
        if not args.force:
            raise SystemExit(
                f"{target} already exists. Re-run with --force to replace it."
            )
        shutil.rmtree(target)

    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    print("Provider installed.")
    print()
    print("Next steps:")
    print("  1. Restart Music Assistant.")
    print("  2. In Music Assistant: Settings -> Providers -> Add Provider -> Bilibili.")
    print("  3. QR login additionally needs the optional dependency:")
    print(f"       {sys.executable} -m pip install segno==1.6.6")
    print("     Without it, choose 'Paste SESSDATA cookie' or 'Continue anonymously'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
