#!/usr/bin/env python3
"""Scaffold Zotero PDF ingestion into Obsidian literature notes."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any


@dataclass(frozen=True)
class TextExtractorConfig:
    command: list[str]
    max_chars: int


@dataclass(frozen=True)
class SummarizerConfig:
    command: list[str]
    max_input_chars: int


@dataclass(frozen=True)
class WorkflowConfig:
    zotero_storage_path: Path
    obsidian_vault_path: Path
    literature_note_folder: str
    template_path: Path
    manifest_path: Path
    extractor: TextExtractorConfig
    summarizer: SummarizerConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover Zotero PDFs and scaffold Obsidian summary notes."
    )
    parser.add_argument(
        "--config",
        default="config/summarizer.toml",
        help="Path to the TOML configuration file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview discovered PDFs and output paths without writing notes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of PDFs to process.",
    )
    return parser.parse_args()


def load_config(config_path: Path) -> WorkflowConfig:
    with config_path.open("rb") as config_file:
        raw_config = tomllib.load(config_file)

    paths = raw_config["paths"]
    extractor = raw_config["text_extractor"]
    summarizer = raw_config["summarizer"]

    base_dir = config_path.parent.parent
    vault_path = expand_path(paths["obsidian_vault_path"])
    manifest_path = expand_path(paths["manifest_path"])

    return WorkflowConfig(
        zotero_storage_path=expand_path(paths["zotero_storage_path"]),
        obsidian_vault_path=vault_path,
        literature_note_folder=paths["literature_note_folder"],
        template_path=(base_dir / paths["template_path"]).resolve(),
        manifest_path=manifest_path,
        extractor=TextExtractorConfig(
            command=list(extractor["command"]),
            max_chars=int(extractor["max_chars"]),
        ),
        summarizer=SummarizerConfig(
            command=list(summarizer["command"]),
            max_input_chars=int(summarizer["max_input_chars"]),
        ),
    )


def expand_path(raw_path: str) -> Path:
    return Path(raw_path).expanduser().resolve()


def discover_pdfs(storage_path: Path) -> list[Path]:
    if not storage_path.exists():
        raise FileNotFoundError(f"Zotero storage path does not exist: {storage_path}")

    return sorted(path for path in storage_path.rglob("*.pdf") if path.is_file())


def extract_text(pdf_path: Path, config: TextExtractorConfig) -> str:
    if not config.command:
        return placeholder_text(pdf_path)

    command = [part.format(pdf=str(pdf_path)) for part in config.command]

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        return placeholder_text(pdf_path, error)

    text = result.stdout.strip()
    return text[: config.max_chars] if text else placeholder_text(pdf_path)


def summarize_pdf(pdf_path: Path, source_text: str, config: SummarizerConfig) -> str:
    prompt = build_summary_prompt(pdf_path, source_text[: config.max_input_chars])

    if not config.command:
        return fallback_summary(pdf_path)

    try:
        result = subprocess.run(
            config.command,
            input=prompt,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        return fallback_summary(pdf_path, error)

    summary = result.stdout.strip()
    return summary if summary else fallback_summary(pdf_path)


def build_summary_prompt(pdf_path: Path, source_text: str) -> str:
    return f"""Create an Obsidian literature note for this Zotero PDF.

Source PDF: {pdf_path.name}

Return Markdown with these headings:
- Core Summary
- Key Claims
- Methods And Evidence
- Quotable Passages
- Limitations
- Follow Up Questions

Source text:
{source_text}
"""


def render_note(pdf_path: Path, summary: str, template_path: Path) -> str:
    template = Template(template_path.read_text(encoding="utf-8"))
    created_at = datetime.now(UTC).isoformat(timespec="seconds")

    return template.safe_substitute(
        title=title_from_pdf(pdf_path),
        source_pdf=str(pdf_path),
        created_at=created_at,
        summary=summary,
    )


def title_from_pdf(pdf_path: Path) -> str:
    title = re.sub(r"[_-]+", " ", pdf_path.stem)
    title = re.sub(r"\s+", " ", title).strip()
    return title.title() or "Untitled Zotero PDF"


def note_path_for_pdf(pdf_path: Path, config: WorkflowConfig) -> Path:
    safe_title = slugify(title_from_pdf(pdf_path))
    note_folder = config.obsidian_vault_path / config.literature_note_folder
    return note_folder / f"{safe_title}.md"


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug or "untitled-zotero-pdf"


def placeholder_text(pdf_path: Path, error: BaseException | None = None) -> str:
    reason = f" Extraction issue: {error}" if error else ""
    return (
        f"No PDF text has been extracted yet for {pdf_path.name}."
        f"{reason} Configure [text_extractor].command to enable extraction."
    )


def fallback_summary(pdf_path: Path, error: BaseException | None = None) -> str:
    reason = f"\n\n> Summarizer issue: {error}" if error else ""
    return f"""## Core Summary

Summary pending for `{pdf_path.name}`.

## Key Claims

- Pending local summarizer integration.

## Methods And Evidence

- Pending PDF text extraction and review.

## Quotable Passages

- Pending source-bound extraction.

## Limitations

- This note was scaffolded before the summarizer was wired.

## Follow Up Questions

- What local model should be used for first-pass summaries?
- Should this PDF be embedded at note, section, or chunk level?{reason}
"""


def append_manifest_entry(manifest_path: Path, note_path: Path, pdf_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "note_path": str(note_path),
        "pdf_path": str(pdf_path),
        "status": "pending",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    with manifest_path.open("a", encoding="utf-8") as manifest_file:
        manifest_file.write(json.dumps(entry, sort_keys=True) + "\n")


def process_pdf(pdf_path: Path, config: WorkflowConfig, dry_run: bool) -> None:
    note_path = note_path_for_pdf(pdf_path, config)

    if dry_run:
        print(f"Would write: {note_path}")
        return

    note_path.parent.mkdir(parents=True, exist_ok=True)
    source_text = extract_text(pdf_path, config.extractor)
    summary = summarize_pdf(pdf_path, source_text, config.summarizer)
    note_path.write_text(render_note(pdf_path, summary, config.template_path), encoding="utf-8")
    append_manifest_entry(config.manifest_path, note_path, pdf_path)
    print(f"Wrote: {note_path}")


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config).resolve())
    pdfs = discover_pdfs(config.zotero_storage_path)

    if args.limit is not None:
        pdfs = pdfs[: args.limit]

    if not pdfs:
        print("No PDFs found in the configured Zotero storage path.")
        return 0

    for pdf_path in pdfs:
        process_pdf(pdf_path, config, args.dry_run)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ingest_zotero_pdfs failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
