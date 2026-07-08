# Offline AI Workflow MCP Obsidian

An offline-first workflow template for moving Zotero PDFs into an Obsidian vault
through an MCP-shaped pipeline.

This repository is intentionally a scaffold. It gives you the repo structure,
note templates, and summarizer wiring points needed to integrate a local PDF
extractor, local LLM, and embedding workflow without sending source material to
hosted services.

## Workflow Template

```text
Zotero PDF library
  -> PDF discovery
  -> Text extraction
  -> Local summarizer
  -> Obsidian literature note
  -> Embedding/index manifest
  -> MCP tools for retrieval and follow-up synthesis
```

### MCP Roles

- `zotero-library`: owns discovery of Zotero storage folders and PDF metadata.
- `pdf-text-extractor`: turns each PDF into bounded plain text chunks.
- `local-summarizer`: receives chunks and returns structured Markdown.
- `obsidian-vault-writer`: writes notes into the configured vault folder.
- `embedding-indexer`: records the note path and source PDF for later indexing.

The first pass in this repo keeps those roles as configuration boundaries. You
can later replace each boundary with a real MCP server or local command.

## Repository Layout

```text
.
├── config/
│   └── summarizer.toml
├── obsidian/
│   └── templates/
│       ├── base-note.md
│       └── pillars/
│           ├── 01-source-context.md
│           ├── 02-methods-evidence.md
│           ├── 03-claims-findings.md
│           └── 04-synthesis-actions.md
└── scripts/
    └── ingest_zotero_pdfs.py
```

## Quick Start

1. Copy `config/summarizer.toml` and update:
   - `zotero_storage_path`
   - `obsidian_vault_path`
   - `literature_note_folder`
   - local summarizer and extractor commands
2. Preview note generation:

   ```bash
   python3 scripts/ingest_zotero_pdfs.py --config config/summarizer.toml --dry-run
   ```

3. Generate notes:

   ```bash
   python3 scripts/ingest_zotero_pdfs.py --config config/summarizer.toml
   ```

## Local Summarizer Contract

The script expects a summarizer command that can read a prompt from standard
input and write Markdown to standard output.

Example shape:

```toml
[summarizer]
provider = "local-command"
command = ["ollama", "run", "llama3.1:8b"]
max_input_chars = 24000
```

Any local model runner can fit this contract if it accepts stdin and returns
Markdown.

## Obsidian Note Contract

Generated notes follow this structure:

- source metadata
- summary
- pillars
- synthesis
- follow-up prompts
- embedding/index status

The pillar templates are deliberately small so you can turn them into Obsidian
Templates, Templater snippets, or MCP prompt fragments.

## Embedding Workflow Placeholder

The scaffold writes a JSONL manifest next to generated notes. Each row is meant
to become an embedding job:

```json
{"note_path":"...","pdf_path":"...","status":"pending"}
```

Wire this into your preferred local embedding stack after the summarizer is in
place.

## Privacy Posture

- Default assumptions are offline and local.
- No network calls are made by the Python scaffold.
- External operations are explicit commands in `config/summarizer.toml`.
- The script can run in `--dry-run` mode before writing into your vault.

