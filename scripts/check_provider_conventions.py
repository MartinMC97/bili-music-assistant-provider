#!/usr/bin/env python3
"""
Local mirrors of the provider convention checks music-assistant/server runs.

The upstream repository enforces these through its own ``scripts/`` pre-commit hooks
(``check_method_order``, ``check_translatable_labels``, ``check_config_entries`` and
``check_manifests``/``check_provider_icons``).  This repository has a different layout
(``provider/bilibili`` instead of ``music_assistant/providers/bilibili``), so the rules
are re-implemented here on top of the same AST checks:

1. class method order      — public/dunder methods before private ones;
2. translatable labels     — ``BrowseFolder``/``RecommendationFolder`` may not use a
                             literal ``name`` without a ``translation_key``;
3. config entry text       — ``ConfigEntry`` label/description/action_label and
                             ``ConfigValueOption`` title must not be hardcoded strings;
4. provider manifest       — mandatory keys, domain matching the folder, icon budgets
                             (svg <= 5 KB, png <= 20 KB).

Usage::

    python scripts/check_provider_conventions.py [PATH ...]

Without arguments every Python file under ``provider/`` is scanned and the single
provider manifest/icon set is validated.  Exits non-zero when a rule is violated.
"""

# ruff: noqa: T201

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROVIDERS_ROOT = REPO_ROOT / "provider"

#: Media-item constructors whose ``name`` is a UI label that must carry a translation key.
TRANSLATABLE_CONSTRUCTORS = frozenset({"BrowseFolder", "RecommendationFolder"})

#: Hardcoded-text keywords per constructor that must come from strings.json instead.
CONFIG_TEXT_FIELDS = {
    "ConfigEntry": ("label", "description", "action_label"),
    "ConfigValueOption": ("title",),
}

#: Mandatory manifest keys mirroring ``ProviderManifest``.
MANDATORY_MANIFEST_KEYS = ("type", "domain", "name", "description", "codeowners")

#: Icon size budgets used by the upstream check.
MAX_ICON_SIZE = {".svg": 5 * 1024, ".png": 20 * 1024}


def iter_python_files() -> list[Path]:
    """Return every Python file under the provider package."""
    return sorted(PROVIDERS_ROOT.rglob("*.py"))


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _literal_text(node: ast.AST) -> str | None:
    """Return the literal text of a string constant or f-string, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value for part in node.values if isinstance(part, ast.Constant))
    return None


def _keyword(call: ast.Call, name: str) -> ast.keyword | None:
    return next((kw for kw in call.keywords if kw.arg == name), None)


def _is_private_method(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def check_method_order(tree: ast.Module, rel: str) -> list[str]:
    """Return violations for public/dunder methods defined below private ones."""
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        seen_private = False
        for child in node.body:
            if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if _is_private_method(child.name):
                seen_private = True
            elif seen_private:
                problems.append(
                    f"{rel}:{child.lineno}: '{child.name}' is defined below a private method "
                    f"in class '{node.name}' — move private methods to the bottom"
                )
    return problems


def check_translatable_labels(tree: ast.Module, rel: str) -> list[str]:
    """Return violations for folder labels that are hardcoded instead of translated."""
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in TRANSLATABLE_CONSTRUCTORS:
            continue
        keyword = _keyword(node, "name")
        if keyword is None or _literal_text(keyword.value) is None:
            continue
        if _keyword(node, "translation_key") is not None:
            continue
        problems.append(
            f"{rel}:{node.lineno}: {_call_name(node)} has a hardcoded 'name'; "
            "add a translation_key backed by strings.json"
        )
    return problems


def check_config_text(tree: ast.Module, rel: str) -> list[str]:
    """Return violations for hardcoded user-facing config entry text."""
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in CONFIG_TEXT_FIELDS:
            continue
        constructor = _call_name(node)
        for field_name in CONFIG_TEXT_FIELDS[constructor]:
            keyword = _keyword(node, field_name)
            if keyword is not None and _literal_text(keyword.value) is not None:
                problems.append(
                    f"{rel}:{node.lineno}: {constructor} hardcodes '{field_name}'; "
                    "author it in strings.json instead"
                )
    return problems


def check_manifest_and_icons() -> list[str]:
    """Return violations for provider manifests and icon size budgets."""
    problems: list[str] = []
    for manifest_path in sorted(PROVIDERS_ROOT.glob("*/manifest.json")):
        provider_dir = manifest_path.parent
        rel = manifest_path.relative_to(REPO_ROOT).as_posix()
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            problems.append(f"{rel}: invalid JSON ({err})")
            continue
        for key in MANDATORY_MANIFEST_KEYS:
            if not manifest.get(key):
                problems.append(f"{rel}: missing mandatory key '{key}'")
        if manifest.get("domain") != provider_dir.name:
            problems.append(
                f"{rel}: domain {manifest.get('domain')!r} does not match folder "
                f"'{provider_dir.name}'"
            )
        requirements = manifest.get("requirements")
        if not isinstance(requirements, list) or not all(
            isinstance(item, str) and item for item in requirements
        ):
            problems.append(f"{rel}: 'requirements' must be a list of pip requirement strings")
        for icon in sorted(provider_dir.glob("icon*")):
            budget = MAX_ICON_SIZE.get(icon.suffix.lower())
            if budget is None:
                continue
            size = icon.stat().st_size
            if size > budget:
                problems.append(f"{rel}: {icon.name} is {size} bytes (budget {budget // 1024} KB)")
    if not any(PROVIDERS_ROOT.glob("*/manifest.json")):
        problems.append(f"{PROVIDERS_ROOT}: no provider manifest found")
    return problems


def collect_violations(paths: Iterable[Path] | None = None) -> list[str]:
    """Return every convention violation for the given files (default: all provider files)."""
    problems: list[str] = []
    files = sorted(paths) if paths else iter_python_files()
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        problems.extend(check_method_order(tree, rel))
        problems.extend(check_translatable_labels(tree, rel))
        problems.extend(check_config_text(tree, rel))
    problems.extend(check_manifest_and_icons())
    return problems


def _resolve_files(args: Sequence[str]) -> list[Path]:
    """Expand file/folder arguments into Python files under the provider package."""
    files: list[Path] = []
    for arg in args:
        path = (REPO_ROOT / arg).resolve() if not Path(arg).is_absolute() else Path(arg)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
        elif path.suffix == ".py":
            files.append(path)
    return sorted(files)


def main(argv: Sequence[str] | None = None) -> int:
    """Print every convention violation; return 1 when any were found."""
    args = list(sys.argv[1:] if argv is None else argv)
    problems = collect_violations(_resolve_files(args) if args else None)
    if not problems:
        return 0
    print("Provider convention violations:", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
