# Tagme

**Tagme** is an AI file auto-labeler for macOS that turns messy screenshots and downloads into searchable, context-rich filenames.

It combines **Tesseract OCR** and **Ollama vision (llava:7b)** to identify:
- source app/site (`floom`, `github`, `linkedin`)
- screen intent (`token-setup`, `dashboard`, `signin`)
- task context (`mcp-config`, `api-token`, `agent-auth`)

## Why Tagme
- Automatic desktop cleanup without deleting files
- Better screenshot search and retrieval
- Works locally with private on-device inference
- Optimized for founders, operators, and heavy screenshot workflows

## Example output
Input:
- `Screenshot 2026-04-28 at 20.05.00.png`

Output:
- `2026-04-28__screenshot-floom-token-setup-mcp-config__screenshot-2026-04-28-at-20-05.png`

## Features
- Watches `Desktop`, `Downloads`, and `Documents`
- OCR text extraction via `tesseract`
- Vision context extraction via `ollama` + `llava:7b`
- Bucketed labels: `source + screen_type + task_topic`
- SQLite state tracking and log history
- launchd automation for background scans

## Install (macOS)
1. Install dependencies:
```bash
brew install tesseract imagemagick ollama
```
2. Pull model:
```bash
ollama pull llava:7b
```
3. Install Python package locally:
```bash
pip install .
```
4. Copy config:
```bash
mkdir -p ~/.config/file-labeler
cp config/config.example.json ~/.config/file-labeler/config.json
```
5. Install launch agent:
```bash
cp launchd/com.federico.filelabeler.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.federico.filelabeler.plist >/dev/null 2>&1 || true
launchctl load ~/Library/LaunchAgents/com.federico.filelabeler.plist
```

## CLI
Run one scan:
```bash
tagme
```

## Config
Config file:
- `~/.config/file-labeler/config.json`

Main keys:
- `watch_dirs`
- `enabled_since`
- `rename`
- `ollama.model`
- `ocr.enabled`

## Logs and DB
- Log: `~/.local/state/file-labeler/labeler.log`
- State DB: `~/.local/state/file-labeler/labels.db`

## npm wrapper
This repo includes `@floomhq/tagme` for JS users. It wraps the Python CLI:
```bash
npx @floomhq/tagme
```

## PyPI
Package name:
- `tagme`

## License
MIT
