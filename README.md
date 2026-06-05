# Tagme — Auto-Label & Organize Your Files with Local AI

Your Desktop and Downloads folder are a mess. Screenshots pile up with names like `IMG_4121.heic`. Documents sit as `document.pdf`. Finding that one receipt, screenshot, or invoice later is nearly impossible.

**Tagme** is a local macOS daemon that automatically labels your files using OCR + vision AI. It reads text in images, understands what's inside, and renames files with searchable tags — so Spotlight, Finder, and any AI agent can find them in seconds.

```
IMG_4121.heic
→ 2026-06-05__finance-transactions-euro-credit__img-4121.png

Screenshot 2026-06-05 at 11.54.13 AM.png
→ 2026-06-05__pipeline-summary-sales-hiring__screenshot.png
```

**No cloud. No API keys. No subscriptions.** Everything runs locally on your Mac via Ollama.

---

## What Tagme Does

| Problem | Before Tagme | After Tagme |
|---|---|---|
| Screenshots | `Screenshot 2026-06-05...png` | `2026-06-05__berlin-office-remote__screenshot.png` |
| Receipts | `IMG_4268.heic` | `2026-06-05__finance-transactions-euro-credit__img-4268.png` |
| Documents | `document.pdf` | `2026-06-05__document__document.pdf` |
| Finding files | Scroll forever | `mdfind "kMDItemDescription == '*finance*'"` |

Tagme is a **file organizer**, **auto tagger**, **screenshot manager**, and **document classifier** rolled into one.

---

## How It Works

1. **Scan** — A LaunchAgent runs every 2 minutes, watching your Desktop, Downloads, and Documents
2. **OCR** — Tesseract extracts all readable text from images (fast, precise)
3. **Vision** — A local vision model (Llava via Ollama) *sees* the image layout *and* reads the OCR text
4. **Tag** — The model cross-references pixels + text to generate 4 accurate content tags
5. **Write** — Tags go into the **filename** (portable), **EXIF metadata** (Spotlight-searchable), **xattrs** (programmatic), and a local SQLite database
6. **Search** — Find files instantly via Spotlight, Finder, terminal, or any AI agent

**Why hybrid?** Vision-only models ramble and ignore instructions. OCR-only misses visual context. Combining both grounds the model — it uses text for precision and the image for layout context.

---

## Install (macOS)

### 1. Install dependencies

```bash
brew install tesseract imagemagick exiftool ollama
```

### 2. Pull the vision model

```bash
ollama pull llava:7b
```

**Alternatives:** `llava-phi3` (2.9GB, faster) or `llava:13b` (8GB, sharper but slower).

### 3. Download Tagme

```bash
curl -o ~/bin/tagme https://raw.githubusercontent.com/floomhq/tagme/main/bin/auto_file_labeler.py
chmod +x ~/bin/tagme
```

### 4. Configure

```bash
mkdir -p ~/.config/file-labeler
curl -o ~/.config/file-labeler/config.json https://raw.githubusercontent.com/floomhq/tagme/main/config/config.example.json
# Edit paths and settings to taste
```

### 5. Install the LaunchAgent

```bash
curl -o ~/Library/LaunchAgents/com.federico.filelabeler.plist https://raw.githubusercontent.com/floomhq/tagme/main/launchd/com.federico.filelabeler.plist
launchctl load ~/Library/LaunchAgents/com.federico.filelabeler.plist
```

---

## Search Your Files

### Spotlight (uses EXIF `ImageDescription`)
```bash
mdfind "kMDItemDescription == '*finance*'"
```

### Finder / Terminal (uses filename)
```bash
find ~/Desktop -name '*finance*'
find ~/Downloads -name '*rocketlist*'
```

### xattrs (programmatic access)
```bash
xattr -p user.floomlens.labels ~/Desktop/2026-06-05__finance*.png
```

---

## Use Cases

### Screenshot Organization
Tired of `Screenshot 2026-06-05 at 11.54.13 AM.png`? Tagme reads the text on screen and renames it to something useful like `2026-06-05__pipeline-summary-sales-hiring__screenshot.png`. Search "sales" or "hiring" and find it instantly.

### Receipt & Invoice Management
Photos of receipts get renamed with the merchant, amount, and date. No more scrolling through `IMG_4121.heic` to find your Uber receipt from March.

### Document Classification
PDFs and text files get labeled by content type. A hiring contract becomes `2026-06-05__document__hiring-contract.pdf`. A spreadsheet of leads becomes `2026-06-05__spreadsheet__sales-leads.csv`.

### AI Agent File Access
Any AI assistant (Claude, ChatGPT, Kimi) can find your files by name because the tags are literally in the filename. No special tools needed.

---

## What Gets Stored Where

| Location | Content | Survives Email? | Survives Cloud? | Survives Reformat? |
|---|---|---|---|---|
| **Filename** | `date__tag1-tag2-tag3__original.ext` | ✅ Yes | ✅ Yes | ✅ Yes |
| **EXIF** | `ImageDescription` with tags | ⚠️ Sometimes | ⚠️ Sometimes | ❌ No |
| **xattrs** | `user.floomlens.labels` + `.json` | ❌ No | ❌ No | ❌ No |
| **SQLite DB** | Path, fingerprint, labels | ❌ No | ❌ No | ❌ No |

**The filename is your durable, portable tag.** It survives email, cloud sync, USB drives, and format conversions. EXIF is great for Spotlight. xattrs and SQLite are local bonuses.

---

## Tag Quality

| File Type | Quality | Why |
|---|---|---|
| Screenshots with text | ✅ Excellent | OCR reads everything; vision provides layout context |
| Documents & PDFs | ✅ Excellent | Text is dense and readable |
| Receipts & invoices | ✅ Good | Numbers and merchant names are clear |
| Photos without text | ⚠️ Generic | OCR returns empty; falls back to `['image']` |
| Dense UI with tiny fonts | ⚠️ Mixed | Tesseract may misread; model tags the garbled text |

If tag quality feels off, try a bigger model (`llava:13b`) or a lighter variant (`llava-phi3`). The code is solid — the model is the ceiling.

---

## Config Options

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
| `watch_dirs` | Directories to scan for new files |
| `rename` | Rename files with generated tags? |
| `filename_format` | Pattern: `{date}__{labels}__{orig}` + extension |
| `max_labels` | 1–10 tags per file (default 4) |
| `model` | Ollama vision model name |
| `timeout` | Model inference timeout in seconds |
| `recursive` | Scan subdirectories? |

---

## Run Manually

```bash
~/bin/tagme
```

Run built-in tests:
```bash
TEST=1 ~/bin/tagme
```

---

## FAQ

**Q: Does it work on Apple Silicon?**  
A: Yes. Tested on M2 Macs. Ollama uses the GPU for inference.

**Q: Can I use a different model?**  
A: Yes. Edit `~/.config/file-labeler/config.json` and change `model`. Any Ollama vision model works.

**Q: Will it re-tag files already processed?**  
A: No. It fingerprints files by content (size + mtime + inode), not path. Renamed files are not reprocessed.

**Q: Does it delete or move files?**  
A: No. It only renames and writes metadata. Original files are never deleted.

**Q: Can I disable renaming and only write metadata?**  
A: Yes. Set `"rename": false` in config.

**Q: What about privacy?**  
A: Everything is local. No cloud, no API keys, no data leaves your Mac.

---

## License

MIT
