# Tagme

Screenshots, downloads, and documents pile up. Finding the right file later is a pain.

**Tagme** auto-labels your files using local OCR + a tiny AI model. It renames files with searchable tags and writes metadata so Spotlight and any agent can find them.

```
IMG_4121.heic → 2026-06-05__finance-transactions-euro-credit__img-4121.png
```

## How it works

1. **Scan** — Every 2 minutes, the daemon scans your watch directories
2. **OCR** — Tesseract reads any text in images
3. **Tag** — A local 1.5B model (Qwen via Ollama) turns that text into 4 content tags
4. **Write** — Tags go into EXIF, xattrs, the filename, and a local SQLite DB
5. **Search** — `mdfind` or any filename search finds your files instantly

## Install (macOS)

### 1. Install dependencies

```bash
brew install tesseract imagemagick exiftool ollama
```

### 2. Pull the model

```bash
ollama pull qwen2.5:1.5b
```

### 3. Copy the script

```bash
cp bin/auto_file_labeler.py ~/bin/auto_file_labeler.py
chmod +x ~/bin/auto_file_labeler.py
```

### 4. Configure

```bash
mkdir -p ~/.config/file-labeler
cp config/config.example.json ~/.config/file-labeler/config.json
# Edit paths and settings to taste
```

### 5. Install the LaunchAgent

```bash
cp launchd/com.federico.filelabeler.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.federico.filelabeler.plist
```

## Search your files

**Spotlight** (uses EXIF `ImageDescription`):
```bash
mdfind "kMDItemDescription == '*finance*'"
```

**Filename** (works everywhere):
```bash
find ~/Desktop -name '*finance*'
```

**xattrs** (programmatic):
```bash
xattr -p user.floomlens.labels ~/Desktop/2026-06-05__finance*.png
```

## What gets stored

| Location | Content | Durability |
|---|---|---|
| **Filename** | `date__tag1-tag2-tag3__original.ext` | ✅ Survives everything |
| **EXIF** | `ImageDescription` with tags | ✅ Spotlight-indexed |
| **xattrs** | `user.floomlens.labels` + `.json` | ⚠️ macOS/APFS only |
| **SQLite** | Path, fingerprint, labels | ⚠️ Local DB only |

The filename is your portable, durable tag. EXIF is great for Spotlight. xattrs are a bonus for macOS-native workflows.

## How good are the tags?

**Good:** Screenshots, slides, memes, documents, receipts — anything with readable text.

**Weak:** Photos, art, illustrations without text. OCR reads letters, not pixels. If there's no text, you get a generic fallback like `['image']`.

Want magic computer-vision tagging of any image? You'd need a vision model (GPT-4o, Llava, etc.) — this setup is intentionally zero-cost and offline.

## Config options

```json
{
  "watch_dirs": ["/Users/you/Desktop", "/Users/you/Downloads"],
  "ignore_prefixes": [".", "_Desktop_Cleanup_"],
  "rename": true,
  "filename_format": "{date}__{labels}__{orig}",
  "max_labels": 4,
  "model": "qwen2.5:1.5b",
  "endpoint": "http://127.0.0.1:11434/api/generate",
  "timeout": 30,
  "recursive": false
}
```

| Key | Description |
|---|---|
| `watch_dirs` | Directories to scan |
| `rename` | Rename files with tags? |
| `filename_format` | `{date}__{labels}__{orig}` + extension |
| `max_labels` | 1–10 tags (default 4) |
| `model` | Ollama model name |
| `recursive` | Scan subdirectories? |

## Run manually

```bash
~/bin/auto_file_labeler.py
```

Run tests:
```bash
TEST=1 ~/bin/auto_file_labeler.py
```

## License

MIT
