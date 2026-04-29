# Tagme

Screenshots are amazing.

Until you need to find one.

So I built **TAGME**:
a local Mac app that auto-labels your screenshots.

![Tagme launch visual](docs/tagme-launch.png)

## How it works
1. You take a screenshot
2. A small local AI model understands what's inside
3. It writes searchable labels into the file metadata

Now Spotlight gets smarter.
And your desktop agents can find the right screenshot in seconds.

It's open source on GitHub.
Setup takes one prompt.

## What Tagme adds
- Native labels in metadata (`user.tagme.labels`, `user.tagme.json`)
- OCR + local vision (`tesseract` + `ollama`/`llava:7b`)
- Consumer-friendly classification: source + screen type + task topic
- Automatic filename normalization for easier retrieval

Example output:
- `2026-04-28__source-floom__type-token-setup__topic-mcp-config__screenshot-2026-04-28-at-20-05.png`

## Install (macOS)
1. Install dependencies:
```bash
brew install tesseract imagemagick ollama
```
2. Pull model:
```bash
ollama pull llava:7b
```
3. Install package locally:
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
```bash
tagme
```

## npm
```bash
npx @floomhq/tagme
```

## License
MIT
