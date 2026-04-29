# file-labeler

Local automatic file labeling and renaming for macOS.

## What it does
- Watches `Desktop`, `Downloads`, `Documents`
- Labels files by type
- For images/screenshots, combines:
  - `tesseract` OCR (brand/domain/text extraction)
  - `ollama` vision (`llava:7b`) for UI context
- Renames files as:
  - `YYYY-MM-DD__labels__original.ext`

Example:
- `2026-04-28__screenshot-floom-token-setup-mcp-config__screenshot-2026-04-28-at-20-05.png`

## Setup
1. Install dependencies:
   - `brew install tesseract imagemagick ollama`
2. Pull vision model:
   - `ollama pull llava:7b`
3. Copy config:
   - `mkdir -p ~/.config/file-labeler`
   - `cp config/config.example.json ~/.config/file-labeler/config.json`
4. Install script:
   - `cp bin/auto_file_labeler.py ~/bin/auto_file_labeler.py`
   - `chmod +x ~/bin/auto_file_labeler.py`
5. Install launchd agent:
   - `cp launchd/com.federico.filelabeler.plist ~/Library/LaunchAgents/`
   - `launchctl unload ~/Library/LaunchAgents/com.federico.filelabeler.plist >/dev/null 2>&1 || true`
   - `launchctl load ~/Library/LaunchAgents/com.federico.filelabeler.plist`

## Manual run
```bash
~/bin/auto_file_labeler.py
```

## Logs and state
- Log: `~/.local/state/file-labeler/labeler.log`
- DB: `~/.local/state/file-labeler/labels.db`

## Notes
- The tool is non-destructive (rename only, no deletes).
- `enabled_since` in config controls start time for processing.
