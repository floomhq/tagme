# Tagme

Screenshots, downloads, and documents pile up. Finding the right file later is a pain.

**Tagme** auto-labels your files using local OCR + vision + a small AI model. It renames files with searchable tags and writes metadata so Spotlight and any agent can find them.

```
IMG_4121.heic → 2026-06-05__finance-transactions-euro-credit__img-4121.png
```

## How it works

1. **Scan** — Every 2 minutes, the daemon scans your watch directories
2. **OCR** — Tesseract reads text in images (fast, precise)
3. **Vision** — A local vision model (Llava via Ollama) sees the image **and** the OCR text
4. **Tag** — The model cross-references pixels + text to generate 4 content tags
5. **Write** — Tags go into EXIF, xattrs, the filename, and a local SQLite DB
6. **Search** — `mdfind` or any filename search finds your files instantly

**Why hybrid?** Vision-only models ignore instructions and ramble. OCR-only misses visual context. Combining both grounds the model — it uses the text for precision and the image for context.

## Install (macOS)

### 1. Install dependencies

```bash
brew install tesseract imagemagick exiftool ollama
```

### 2. Pull the model

```bash
ollama pull llava:7b
```

**Alternatives:** `llava-phi3` (2.9GB, faster) or `llava:13b` (8GB, slower but sharper).

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

## Tag quality

**Great:** Screenshots, slides, memes, documents, receipts — anything with readable text. The hybrid approach (OCR + vision) cross-references text and layout for accurate tags.

**Okay:** Photos without text. OCR returns empty, so the model isn't called. You get a generic fallback like `['image']`.

**Weak:** Dense UI screenshots where tesseract misreads (low contrast, tiny fonts, icons mixed with text). The model may produce garbage from garbage OCR.

If tag quality feels off, the fix is usually a bigger model (`llava:13b`) or a different vision variant (`llava-phi3`). The code is solid — the model is the ceiling.

## Config options

```json
{
  "watch_dirs": ["/Users/you/Desktop", "/Users/you/Downloads"],
  "ignore_prefixes": [".", "_Desktop_Cleanup_"],
  "rename": true,
  "filename_format": "{date}__{labels}__{orig}",
  "max_labels": 4,
  "model": "llava:7b",
  "endpoint": "http://127.0.0.1:11434/api/generate",
  "timeout": 60,
  "recursive": false
}
```

| Key | Description |
|---|---|
| `watch_dirs` | Directories to scan |
| `rename` | Rename files with tags? |
| `filename_format` | `{date}__{labels}__{orig}` + extension |
| `max_labels` | 1–10 tags (default 4) |
| `model` | Ollama vision model name |
| `timeout` | Model inference timeout in seconds |
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
