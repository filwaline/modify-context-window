"""Interactively update context_window values in Codex model JSON files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()
error_console = Console(stderr=True)


IDENTIFIER_FIELDS = ("slug", "id", "name", "model", "display_name")
DISPLAY_FIELDS = ("display_name", "description")
CONTEXT_FIELDS = ("context_window", "max_context_window")
NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


@dataclass(frozen=True)
class ModelEntry:
    path: Path
    line: int
    identifier: str
    identifiers: tuple[tuple[str, str], ...]
    display_name: str | None
    description: str | None
    context_line: int | None
    context_value: int | float | None
    max_line: int | None
    max_value: int | float | None


class JsonScanner:
    """A small positional scanner used to preserve original line numbers."""

    def __init__(self, text: str, path: Path) -> None:
        self.text = text
        self.path = path
        self.pos = 0
        self.entries: list[ModelEntry] = []

    def error(self, message: str) -> ValueError:
        line = self.line_at(self.pos)
        return ValueError(f"{self.path}:{line}: {message}")

    def line_at(self, offset: int) -> int:
        return self.text.count("\n", 0, offset) + 1

    def skip_whitespace(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def peek(self) -> str:
        if self.pos >= len(self.text):
            raise self.error("unexpected end of input")
        return self.text[self.pos]

    def expect(self, char: str) -> None:
        if self.peek() != char:
            raise self.error(f"expected {char!r}, found {self.text[self.pos]!r}")
        self.pos += 1

    def parse(self) -> list[ModelEntry]:
        self.parse_value()
        self.skip_whitespace()
        if self.pos != len(self.text):
            raise self.error("trailing data after JSON value")
        return self.entries

    def parse_value(self) -> Any:
        self.skip_whitespace()
        char = self.peek()
        if char == "{":
            return self.parse_object()
        if char == "[":
            return self.parse_array()
        if char == '"':
            return self.parse_string()
        if self.text.startswith("true", self.pos):
            self.pos += 4
            return True
        if self.text.startswith("false", self.pos):
            self.pos += 5
            return False
        if self.text.startswith("null", self.pos):
            self.pos += 4
            return None
        return self.parse_number()

    def parse_object(self) -> dict[str, Any]:
        self.expect("{")
        result: dict[str, Any] = {}
        relevant: dict[str, tuple[int, int | float]] = {}
        identifiers: dict[str, str] = {}
        metadata: dict[str, str] = {}
        self.skip_whitespace()

        if self.peek() == "}":
            self.pos += 1
            self.record_model(result, relevant, identifiers, metadata)
            return result

        while True:
            self.skip_whitespace()
            key = self.parse_string()
            self.skip_whitespace()
            self.expect(":")
            self.skip_whitespace()
            value_start = self.pos
            value = self.parse_value()

            if key in IDENTIFIER_FIELDS and isinstance(value, str) and value.strip():
                identifiers[key] = value.strip()
            if key in DISPLAY_FIELDS and isinstance(value, str) and value.strip():
                metadata[key] = value.strip()
            if (
                key in CONTEXT_FIELDS
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                relevant[key] = (self.line_at(value_start), value)

            self.skip_whitespace()
            char = self.peek()
            if char == ",":
                self.pos += 1
                continue
            if char == "}":
                self.pos += 1
                break
            raise self.error(f"expected ',' or '}}', found {char!r}")

        self.record_model(result, relevant, identifiers, metadata)
        return result

    def parse_array(self) -> list[Any]:
        self.expect("[")
        result: list[Any] = []
        self.skip_whitespace()
        if self.peek() == "]":
            self.pos += 1
            return result

        while True:
            result.append(self.parse_value())
            self.skip_whitespace()
            char = self.peek()
            if char == ",":
                self.pos += 1
                continue
            if char == "]":
                self.pos += 1
                return result
            raise self.error(f"expected ',' or ']', found {char!r}")

    def parse_string(self) -> str:
        if self.peek() != '"':
            raise self.error("expected string")
        try:
            value, end = json.decoder.scanstring(self.text, self.pos + 1)
        except ValueError as exc:
            raise self.error(f"invalid string: {exc}") from exc
        self.pos = end
        return value

    def parse_number(self) -> int | float:
        match = NUMBER_RE.match(self.text, self.pos)
        if not match:
            raise self.error("invalid JSON value")
        raw = match.group(0)
        self.pos = match.end()
        return float(raw) if any(char in raw for char in ".eE") else int(raw)

    def record_model(
        self,
        result: dict[str, Any],
        relevant: dict[str, tuple[int, int | float]],
        identifiers: dict[str, str],
        metadata: dict[str, str],
    ) -> None:
        if not relevant:
            return
        context = relevant.get("context_window")
        maximum = relevant.get("max_context_window")
        if context is None and maximum is None:
            return
        identifier = next(
            (identifiers[field] for field in IDENTIFIER_FIELDS if field in identifiers),
            "<unnamed>",
        )
        self.entries.append(
            ModelEntry(
                path=self.path,
                line=(context or maximum)[0],
                identifier=identifier,
                identifiers=tuple(identifiers.items()),
                display_name=metadata.get("display_name"),
                description=metadata.get("description"),
                context_line=context[0] if context else None,
                context_value=context[1] if context else None,
                max_line=maximum[0] if maximum else None,
                max_value=maximum[1] if maximum else None,
            )
        )


def find_json_files(folder: Path) -> list[Path]:
    files = [
        path
        for path in folder.rglob("*.json")
        if path.is_file() and "model" in path.name.lower()
    ]
    return sorted(files, key=lambda path: str(path))


def scan_file(path: Path) -> list[ModelEntry]:
    try:
        text = path.read_text(encoding="utf-8")
        json.loads(text)
        return JsonScanner(text, path).parse()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        error_console.print(f"Skipping invalid JSON: {path} ({exc})")
        return []


def unique_models(entries: list[ModelEntry]) -> list[tuple[str, ModelEntry]]:
    unique: dict[str, ModelEntry] = {}
    for entry in entries:
        unique.setdefault(entry.identifier, entry)
    return sorted(unique.items(), key=lambda item: item[0].casefold())


def select_model(entries: list[ModelEntry]) -> ModelEntry | None:
    models = unique_models(entries)
    table = Table(title="Available Models", show_lines=True)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Model", style="bold")
    table.add_column("Display Name")
    table.add_column("Description")
    table.add_column("Context Window", justify="right")
    table.add_column("Max Context Window", justify="right")
    for number, (identifier, entry) in enumerate(models, start=1):
        table.add_row(
            str(number),
            identifier,
            entry.display_name or "-",
            entry.description or "-",
            str(entry.context_value),
            str(entry.max_value),
        )
    console.print(table)
    try:
        raw = input("Select a model number (or q to cancel): ").strip()
    except EOFError:
        console.print()
        return None
    if raw.lower() in {"q", "quit", "exit"}:
        return None
    try:
        index = int(raw)
        if not 1 <= index <= len(models):
            raise ValueError
    except ValueError:
        error_console.print("Invalid selection.")
        return None
    return models[index - 1][1]


def prompt_context_window(entry: ModelEntry) -> int | None:
    try:
        raw = input("New context_window (positive integer): ").strip()
    except EOFError:
        console.print()
        return None
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
    except ValueError:
        error_console.print("Context window must be a positive integer.")
        return None
    if entry.max_value is not None and value > entry.max_value:
        console.print(f"Warning: {value} exceeds max_context_window={entry.max_value}.")
    return value


def replace_field_line(
    path: Path, line_number: int, field: str, new_value: int
) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not 1 <= line_number <= len(lines):
        raise RuntimeError(f"{path}: line {line_number} is out of range")
    line = lines[line_number - 1]
    replacement = rf"\g<1>{new_value}"
    updated, count = re.subn(
        rf'("{field}"\s*:\s*)[^,\s}}]+',
        replacement,
        line,
        count=1,
    )
    if count != 1:
        raise RuntimeError(
            f"{path}:{line_number}: could not find {field} value on this line"
        )
    lines[line_number - 1] = updated
    path.write_text("".join(lines), encoding="utf-8")


def create_backup(path: Path, timestamp: str) -> Path:
    backup = path.with_name(f"{path.name}.bak.{timestamp}")
    suffix = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak.{timestamp}.{suffix}")
        suffix += 1
    backup.write_bytes(path.read_bytes())
    return backup


def confirm(label: str) -> bool:
    try:
        answer = input(f"Modify {label}? [y/N] ").strip().lower()
    except EOFError:
        console.print()
        return False
    return answer in {"y", "yes"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="folder to search recursively")
    parser.add_argument(
        "--backup",
        action="store_true",
        help="create timestamped backups before modifying files",
    )
    args = parser.parse_args()

    folder = args.folder
    if not folder.is_dir():
        parser.error(f"not a directory: {folder}")

    files = find_json_files(folder)
    if not files:
        console.print("No JSON files with 'model' in the filename were found.")
        return 1

    entries: list[ModelEntry] = []
    for path in files:
        entries.extend(scan_file(path))
    if not entries:
        console.print(
            "No model objects with context_window or max_context_window were found."
        )
        return 1

    selected = select_model(entries)
    if selected is None:
        console.print("No model selected.")
        return 1

    new_value = prompt_context_window(selected)
    if new_value is None:
        console.print("No context window value provided.")
        return 1

    matching_entries = [
        entry for entry in entries if entry.identifier == selected.identifier
    ]
    if not matching_entries:
        matching_entries = [selected]

    selected_table = Table(title="Selected Model", show_lines=True)
    selected_table.add_column("Field", style="bold")
    selected_table.add_column("Value")
    selected_table.add_row("Model", selected.identifier)
    selected_table.add_row("Display Name", selected.display_name or "-")
    selected_table.add_row("Description", selected.description or "-")
    selected_table.add_row("New Context Window", str(new_value))
    console.print(selected_table)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backed_up: set[Path] = set()
    modified: list[tuple[Path, ModelEntry]] = []

    for entry in matching_entries:
        if entry.context_line is None or entry.context_value is None:
            error_console.print(
                f"Skipping {entry.path}:{entry.line}: model has no numeric context_window."
            )
            continue
        if entry.max_line is None or entry.max_value is None:
            error_console.print(
                f"Skipping {entry.path}:{entry.line}: model has no numeric "
                f"max_context_window to update."
            )
            continue
        if not confirm(f"{entry.path}:{entry.context_line}"):
            continue
        if args.backup and entry.path not in backed_up:
            backup = create_backup(entry.path, timestamp)
            backed_up.add(entry.path)
            console.print(f"Created backup: {backup}")
        replace_field_line(entry.path, entry.context_line, "context_window", new_value)
        replace_field_line(entry.path, entry.max_line, "max_context_window", new_value)
        modified.append((entry.path, entry))

    console.print()
    if not modified:
        error_console.print("No files were modified.")
        return 1
    summary = Table(title="Modified Lines", show_lines=True)
    summary.add_column("File", style="bold")
    summary.add_column("Line", justify="right")
    summary.add_column("Model")
    summary.add_column("Field")
    summary.add_column("Change", justify="right")
    previous_path: Path | None = None
    for path, entry in modified:
        file_label = str(path) if path != previous_path else ""
        previous_path = path
        summary.add_row(
            file_label,
            str(entry.context_line),
            entry.identifier,
            "context_window",
            f"{entry.context_value} -> {new_value}",
        )
        summary.add_row(
            "",
            str(entry.max_line),
            entry.identifier,
            "max_context_window",
            f"{entry.max_value} -> {new_value}",
        )
    console.print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
