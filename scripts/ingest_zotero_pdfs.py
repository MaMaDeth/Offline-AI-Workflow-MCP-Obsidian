#!/usr/bin/env python3
"""Scaffold Zotero PDF ingestion into Obsidian literature notes."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
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
    provider: str
    command: list[str]
    model: str
    endpoint: str
    think: bool
    max_input_chars: int
    max_output_tokens: int
    timeout_seconds: int


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
            provider=summarizer["provider"],
            command=list(summarizer["command"]),
            model=summarizer.get("model", ""),
            endpoint=summarizer.get("endpoint", "http://127.0.0.1:11434/api/generate"),
            think=bool(summarizer.get("think", False)),
            max_input_chars=int(summarizer["max_input_chars"]),
            max_output_tokens=int(summarizer.get("max_output_tokens", 400)),
            timeout_seconds=int(summarizer.get("timeout_seconds", 180)),
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

    if config.provider == "ollama-api":
        return summarize_with_ollama_api(pdf_path, prompt, config)

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

    summary = clean_summary_markdown(result.stdout)
    return summary if summary else fallback_summary(pdf_path)


def summarize_with_ollama_api(
    pdf_path: Path, prompt: str, config: SummarizerConfig
) -> str:
    if not config.model:
        return fallback_summary(pdf_path, ValueError("Missing Ollama model name."))

    payload = {
        "model": config.model,
        "prompt": prompt,
        "stream": False,
        "think": config.think,
        "options": {
            "temperature": 0.2,
            "num_predict": config.max_output_tokens,
        },
    }
    request = urllib.request.Request(
        config.endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        json.JSONDecodeError,
    ) as error:
        return fallback_summary(pdf_path, error)

    summary = clean_summary_markdown(str(response_payload.get("response", "")))
    return summary if summary else fallback_summary(pdf_path)


def clean_summary_markdown(summary: str) -> str:
    clean_summary = summary.strip()
    preface_pattern = r"(?i)^here is .*?(?:markdown|note).*?:\s*"
    clean_summary = re.sub(preface_pattern, "", clean_summary, count=1)
    clean_summary = re.sub(r"(?m)^[•*]\s+", "- ", clean_summary)
    return clean_summary.strip()


def build_summary_prompt(pdf_path: Path, source_text: str) -> str:
    return f"""Create a concise Obsidian literature note for this Zotero PDF.

Source PDF: {pdf_path.name}

Return Markdown only. Keep the whole answer under 180 words.
Use exactly these level-2 headings:
## Core Summary
## Key Claims
## Methods And Evidence
## Limitations
## Follow Up Questions

Use one short paragraph for the summary and one "-" bullet for each other section.
Do not invent quotations. If direct quotations are unavailable, omit them.
Do not output code, variable names, IDs, or implementation examples.

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
    existing_entries = read_manifest_entries(manifest_path)
    filtered_entries = [
        existing_entry
        for existing_entry in existing_entries
        if not same_manifest_target(existing_entry, entry)
    ]
    filtered_entries.append(entry)

    with manifest_path.open("w", encoding="utf-8") as manifest_file:
        for manifest_entry in filtered_entries:
            manifest_file.write(json.dumps(manifest_entry, sort_keys=True) + "\n")


def read_manifest_entries(manifest_path: Path) -> list[dict[str, Any]]:
    if not manifest_path.exists():
        return []

    entries: list[dict[str, Any]] = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed_entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed_entry, dict):
            entries.append(parsed_entry)
    return entries


def same_manifest_target(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left.get("note_path") == right["note_path"] or left.get("pdf_path") == right["pdf_path"]


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
