#!/usr/bin/env python3
"""Recover a Music Assistant ``library.db`` that was migrated by 2.11 and is
now opened by a 2.10.x image.

Why this is needed
------------------
Music Assistant's library database schema version is:

* 2.10.x  -> ``DB_SCHEMA_VERSION = 58``, ``favorite`` is a column on every media
  table (``tracks``, ``albums``, ``artists``, ``playlists``, ``radios``,
  ``audiobooks``, ``podcasts``, ``genres``).
* 2.11.x  -> ``DB_SCHEMA_VERSION = 71``, favorites moved to a separate
  ``favorites`` table and the per-table ``favorite`` columns were removed.

There is **no downgrade migration**.  Running a 2.10.x image on a database that
2.11 has already migrated fails while creating the ``*_favorite_idx`` indexes::

    sqlite3.OperationalError: no such column: favorite

Worse, the failing start already rewrote the stored schema version to 58, so the
next start skips the (no-op) migration and crashes in the same place again, which
results in an endless restart loop.

What this script does
---------------------
``--check``   Print the stored schema version and which media tables miss the
              ``favorite`` column.  Read-only.
``--repair``  Re-add ``favorite BOOLEAN NOT NULL DEFAULT 0`` to those tables so
              a 2.10.x image can start again.  Existing rows survive; the old
              favorite flags (now living in the 2.11 ``favorites`` table) are
              not copied back, so favorite markers start out empty.
``--reset``   Move ``library.db`` (and its ``-wal``/``-shm`` siblings) aside and
              let Music Assistant create a fresh database.  The music library,
              play history and local playlists are lost and will be rescanned;
              settings, logins and provider accounts are kept.

Always stop Music Assistant before running ``--repair``/``--reset``, and keep
the backup file produced by ``--repair`` until you have verified the instance
starts again.

Usage (inside the published image, data dir mounted at ``/data``)::

    docker run --rm --entrypoint /app/venv/bin/python \\
      -v /path/to/ma/data:/data \\
      -v "$PWD/scripts:/scripts:ro" \\
      ghcr.io/martinmc97/music-assistant:2.10.11-bili-dcf5c60 \\
      /scripts/repair-library-schema.py --check /data/library.db

    docker run --rm --entrypoint /app/venv/bin/python \\
      -v /path/to/ma/data:/data \\
      -v "$PWD/scripts:/scripts:ro" \\
      ghcr.io/martinmc97/music-assistant:2.10.11-bili-dcf5c60 \\
      /scripts/repair-library-schema.py --repair /data/library.db
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

#: Media tables that carry a `favorite` column in the 2.10.x schema.
MEDIA_TABLES = (
    "tracks",
    "albums",
    "artists",
    "playlists",
    "radios",
    "audiobooks",
    "podcasts",
    "genres",
)

#: Schema versions we know about; shown in --check output for context.
KNOWN_VERSIONS = {58: "2.10.x", 71: "2.11.x"}


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})")]


def _schema_version(con: sqlite3.Connection) -> int | None:
    try:
        row = con.execute("SELECT value FROM settings WHERE key = 'version'").fetchone()
    except sqlite3.DatabaseError:
        return None
    if not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def check(path: Path) -> int:
    """Print a schema report and return a process exit code."""
    if not path.exists():
        print(f"ERROR: {path} does not exist")
        return 1
    con = _connect(path)
    try:
        version = _schema_version(con)
        print(f"database: {path}")
        print(
            f"schema version: {version}"
            + (f" ({KNOWN_VERSIONS[version]})" if version in KNOWN_VERSIONS else "")
        )
        missing = [t for t in MEDIA_TABLES if "favorite" not in _columns(con, t)]
        for table in MEDIA_TABLES:
            print(f"  {table:<12} favorite={'favorite' in _columns(con, table)}")
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        print(f"2.11 favorites table present: {'favorites' in tables}")
        if missing:
            print()
            print("=> FAIL: 2.10.x cannot open this database, favorite missing on:")
            print("   " + ", ".join(missing))
            print("   Fix with --repair (keeps data) or --reset (fresh library).")
            return 2
        print()
        print("=> OK: this database matches the 2.10.x schema.")
        return 0
    finally:
        con.close()


def repair(path: Path) -> int:
    """Re-add the missing favorite columns, keeping a backup first."""
    if not path.exists():
        print(f"ERROR: {path} does not exist")
        return 1
    backup = path.with_name(f"{path.name}.pre-repair-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    print(f"backup: {backup}")

    con = _connect(path)
    try:
        version = _schema_version(con)
        print(f"schema version before repair: {version}")
        if version not in (None, 58):
            print(
                f"WARNING: stored version is {version}, not 58. The failing 2.10.x start"
                " usually rewrites it to 58 before crashing; if this database was migrated"
                " by a newer release on purpose, prefer staying on that release instead."
            )
        changed = 0
        for table in MEDIA_TABLES:
            if "favorite" in _columns(con, table):
                print(f"  {table:<12} already has favorite")
                continue
            con.execute(
                f"ALTER TABLE {table} ADD COLUMN favorite BOOLEAN NOT NULL DEFAULT 0"
            )
            changed += 1
            print(f"  {table:<12} favorite added")
        if version != 58:
            con.execute("UPDATE settings SET value = '58' WHERE key = 'version'")
            print("schema version set to 58")
        con.commit()
        print()
        if changed:
            print(f"=> repaired {changed} table(s); start the 2.10.x image again.")
        else:
            print("=> nothing to repair; this database already has the 2.10.x columns.")
        return 0
    finally:
        con.close()


def reset(path: Path) -> int:
    """Move the library database (and siblings) aside so MA recreates it."""
    if not path.exists():
        print(f"ERROR: {path} does not exist")
        return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    moved = []
    for suffix in ("", "-wal", "-shm"):
        candidate = path.with_name(path.name + suffix)
        if not candidate.exists():
            continue
        target = candidate.with_name(f"{candidate.name}.reset-{stamp}")
        candidate.rename(target)
        moved.append(str(target))
    print("moved aside:")
    for item in moved:
        print(f"  {item}")
    print()
    print("=> start Music Assistant now; it creates a fresh library.db (schema 58).")
    print("   Library items will be rescanned, local playlists are gone.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the requested action."""
    parser = argparse.ArgumentParser(
        description="Inspect / repair a Music Assistant library.db schema.",
        epilog=(
            "Typical use: a 2.10.x image fails with "
            "'sqlite3.OperationalError: no such column: favorite' because the data "
            "directory was previously migrated by a 2.11.x (beta) image."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="report the schema state")
    group.add_argument("--repair", action="store_true", help="re-add the favorite columns")
    group.add_argument(
        "--reset", action="store_true", help="move library.db aside and start fresh"
    )
    parser.add_argument(
        "database",
        nargs="?",
        default="/data/library.db",
        help="path to library.db (default: /data/library.db)",
    )
    args = parser.parse_args(argv)
    path = Path(args.database)

    if args.check:
        return check(path)
    if args.repair:
        return repair(path)
    return reset(path)


if __name__ == "__main__":
    sys.exit(main())
