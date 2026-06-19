#!/usr/bin/env python3
"""Tagme — OCR/vision for screenshots, text extraction for docs → searchable tags."""
import base64
import datetime as dt
import fcntl
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

os.umask(0o077)

HOME = Path.home()
CONFIG_PATH = HOME / ".config" / "file-labeler" / "config.json"
STATE_DIR = HOME / ".local" / "state" / "file-labeler"
DB_PATH = STATE_DIR / "labels.db"
LOG_PATH = STATE_DIR / "labeler.log"

DEFAULT_CONFIG = {
    "watch_dirs": [
        str(HOME / "Desktop"),
        str(HOME / "Downloads"),
        str(HOME / "Documents"),
    ],
    "ignore_prefixes": [".", "_Desktop_Cleanup_"],
    "rename": True,
    "filename_format": "{date}__{labels}__{orig}",
    "max_labels": 4,
    "enabled_since": dt.datetime.now().isoformat(timespec="seconds"),
    "model": "llava:7b",
    "endpoint": "http://127.0.0.1:11434/api/generate",
    "timeout": 30,
    "recursive": False,
    "process_docs": True,
    "doc_rename": "never",
    "doc_finder_tags": False,
    "doc_pdf_keywords": True,
}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"}
DOC_EXTS = {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".pages"}
DOC_V02_EXTS = {".pdf", ".docx", ".txt", ".md"}
SHEET_EXTS = {".csv", ".tsv", ".xls", ".xlsx"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sh", ".json", ".yml", ".yaml"}
TEMP_SUFFIXES = {".download", ".crdownload", ".part", ".tmp", ".temp", ".icloud"}
GENERIC = {"image", "text", "screenshot", "screen", "photo", "picture", "graphic", "file", "app", "website", "webpage", "ui", "page", "ocr-result", "ocr-text", "ocr-results"}
MAX_IMAGE_OCR_BYTES = 50 * 1024 * 1024
MAX_DOC_TEXT_BYTES = 10 * 1024 * 1024
MAX_DOC_EXCERPT_CHARS = 2000
GENERIC_DOC_STEMS = {
    "document", "untitled", "scan", "scanned", "file", "download",
    "new", "copy", "temp", "draft",
}
FINDER_TAG_XATTR = "com.apple.metadata:_kMDItemUserTags"
MAX_DOCX_XML_BYTES = 2 * 1024 * 1024
REQUIRED_BINARIES = {
    "magick": "/opt/homebrew/bin/magick",
    "tesseract": "/opt/homebrew/bin/tesseract",
    "exiftool": "/opt/homebrew/bin/exiftool",
    "xattr": "/usr/bin/xattr",
    "mdimport": "/usr/bin/mdimport",
}
IMAGE_PIPELINE_BINARIES = ("magick", "tesseract", "exiftool", "xattr")
DOC_PIPELINE_BINARIES = ("xattr",)


def resolve_bin(name: str) -> str:
    path = REQUIRED_BINARIES.get(name)
    if path and Path(path).exists():
        return path
    which = shutil.which(name)
    if which:
        return which
    return name


def log(msg: str) -> None:
    ts = dt.datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config() -> dict:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        try:
            user_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg.update(user_cfg)
        except Exception as e:
            log(f"warning: config load failed, using defaults: {type(e).__name__}: {e}")
    else:
        cfg["enabled_since"] = dt.datetime.now().isoformat(timespec="seconds")
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def validate_config(cfg: dict) -> dict:
    if not isinstance(cfg.get("watch_dirs"), list) or not all(isinstance(v, str) for v in cfg.get("watch_dirs", [])):
        log("warning: invalid config watch_dirs, using default")
        cfg["watch_dirs"] = DEFAULT_CONFIG["watch_dirs"]

    max_labels = cfg.get("max_labels")
    if not isinstance(max_labels, int) or isinstance(max_labels, bool):
        log("warning: invalid config max_labels, using default")
        max_labels = DEFAULT_CONFIG["max_labels"]
    clamped_max_labels = min(max(max_labels, 1), 10)
    if clamped_max_labels != max_labels:
        log(f"warning: invalid config max_labels, clamped to {clamped_max_labels}")
    cfg["max_labels"] = clamped_max_labels

    timeout = cfg.get("timeout")
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        log("warning: invalid config timeout, using default")
        timeout = DEFAULT_CONFIG["timeout"]
    clamped_timeout = min(max(timeout, 5), 300)
    if clamped_timeout != timeout:
        log(f"warning: invalid config timeout, clamped to {clamped_timeout}")
    cfg["timeout"] = clamped_timeout

    fmt = cfg.get("filename_format")
    if not isinstance(fmt, str) or not all(token in fmt for token in ("{date}", "{labels}", "{orig}")):
        log("warning: invalid config filename_format, using default")
        cfg["filename_format"] = DEFAULT_CONFIG["filename_format"]

    endpoint = cfg.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
        log("warning: invalid config endpoint, using default")
        cfg["endpoint"] = DEFAULT_CONFIG["endpoint"]

    model = cfg.get("model")
    if not isinstance(model, str) or not model.strip():
        log("warning: invalid config model, using default")
        cfg["model"] = DEFAULT_CONFIG["model"]

    enabled_since = cfg.get("enabled_since")
    if enabled_since is not None:
        try:
            dt.datetime.fromisoformat(enabled_since)
        except Exception:
            log("warning: invalid config enabled_since, resetting to now")
            cfg["enabled_since"] = dt.datetime.now().isoformat(timespec="seconds")

    rename = cfg.get("rename")
    if not isinstance(rename, bool):
        coerced = None
        if isinstance(rename, str):
            value = rename.strip().lower()
            if value in {"true", "1", "yes", "on"}:
                coerced = True
            elif value in {"false", "0", "no", "off"}:
                coerced = False
        elif isinstance(rename, int) and rename in (0, 1):
            coerced = bool(rename)
        if coerced is None:
            log("warning: invalid config rename, using default")
            cfg["rename"] = DEFAULT_CONFIG["rename"]
        else:
            log(f"warning: invalid config rename, coerced to {coerced}")
            cfg["rename"] = coerced

    if not isinstance(cfg.get("ignore_prefixes"), list) or not all(isinstance(v, str) for v in cfg.get("ignore_prefixes", [])):
        log("warning: invalid config ignore_prefixes, using default")
        cfg["ignore_prefixes"] = DEFAULT_CONFIG["ignore_prefixes"]

    recursive = cfg.get("recursive")
    if not isinstance(recursive, bool):
        log("warning: invalid config recursive, using default")
        cfg["recursive"] = DEFAULT_CONFIG["recursive"]

    process_docs = cfg.get("process_docs")
    if not isinstance(process_docs, bool):
        coerced = None
        if isinstance(process_docs, str):
            value = process_docs.strip().lower()
            if value in {"true", "1", "yes", "on"}:
                coerced = True
            elif value in {"false", "0", "no", "off"}:
                coerced = False
        elif isinstance(process_docs, int) and process_docs in (0, 1):
            coerced = bool(process_docs)
        if coerced is None:
            log("warning: invalid config process_docs, using default")
            cfg["process_docs"] = DEFAULT_CONFIG["process_docs"]
        else:
            log(f"warning: invalid config process_docs, coerced to {coerced}")
            cfg["process_docs"] = coerced

    doc_rename = cfg.get("doc_rename")
    if doc_rename not in {"never", "generic_only"}:
        log("warning: invalid config doc_rename, using default")
        cfg["doc_rename"] = DEFAULT_CONFIG["doc_rename"]

    for flag in ("doc_finder_tags", "doc_pdf_keywords"):
        value = cfg.get(flag)
        if not isinstance(value, bool):
            coerced = None
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes", "on"}:
                    coerced = True
                elif lowered in {"false", "0", "no", "off"}:
                    coerced = False
            elif isinstance(value, int) and value in (0, 1):
                coerced = bool(value)
            if coerced is None:
                log(f"warning: invalid config {flag}, using default")
                cfg[flag] = DEFAULT_CONFIG[flag]
            else:
                log(f"warning: invalid config {flag}, coerced to {coerced}")
                cfg[flag] = coerced

    return cfg


def missing_binaries(names: tuple[str, ...]) -> list[str]:
    missing = []
    for name in names:
        bin_path = resolve_bin(name)
        if not Path(bin_path).exists() and not shutil.which(bin_path):
            missing.append(name)
    return missing


def image_pipeline_ready() -> bool:
    return not missing_binaries(IMAGE_PIPELINE_BINARIES)


def doc_pipeline_ready() -> bool:
    return not missing_binaries(DOC_PIPELINE_BINARIES)


def dependencies_available(cfg: dict) -> bool:
    image_ok = image_pipeline_ready()
    doc_ok = doc_pipeline_ready()
    process_docs = cfg.get("process_docs", True)

    if image_ok or (process_docs and doc_ok):
        if process_docs and doc_ok and not image_ok:
            log(
                "warning: image pipeline unavailable "
                f"({', '.join(missing_binaries(IMAGE_PIPELINE_BINARIES))}), running docs-only mode"
            )
        return True

    missing = sorted(
        set(missing_binaries(IMAGE_PIPELINE_BINARIES) + missing_binaries(DOC_PIPELINE_BINARIES))
    )
    log(f"warning: missing required binaries, skipping scan: {', '.join(missing)}")
    return False


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE,
            inode INTEGER,
            size INTEGER,
            mtime REAL,
            fingerprint TEXT,
            labels TEXT,
            processed_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def cleanup_orphaned_rows(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT path FROM files").fetchall()
    stale = [path for (path,) in rows if not Path(path).exists()]
    for path in stale:
        conn.execute("DELETE FROM files WHERE path = ?", (path,))
    conn.commit()
    if stale:
        log(f"cleaned stale DB rows: {len(stale)}")
    return len(stale)


def fingerprint(path: Path) -> str:
    st = path.stat()
    raw = f"{st.st_size}:{int(st.st_mtime)}:{st.st_ino}".encode()
    return hashlib.sha1(raw).hexdigest()


def clean(text: str) -> str:
    text = text.lower().strip()
    chars = [ch if ch.isalnum() else "-" for ch in text]
    result = []
    for ch in chars:
        if ch == "-" and result and result[-1] == "-":
            continue
        result.append(ch)
    text = "".join(result).strip("-")
    return text[:30]


def base_label(path: Path) -> str:
    if path.name.startswith("Screenshot"):
        return "screenshot"
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DOC_EXTS:
        return "document"
    if ext in SHEET_EXTS:
        return "spreadsheet"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in CODE_EXTS:
        return "code"
    mt, _ = mimetypes.guess_type(str(path))
    if mt:
        return clean(mt.split("/")[0])
    return "file"


def name_hints(path: Path) -> list[str]:
    stem = clean(path.stem)
    bits = [b for b in stem.split("-") if len(b) > 2]
    stop = {"screenshot", "image", "final", "copy", "new", "untitled", "img"}
    return [b for b in bits if b not in stop][:2]


def parse_model_tags(resp: str) -> list[str]:
    if len(resp.split()) > 12 or "\n" in resp:
        return []
    tags = []
    for t in resp.split(","):
        if any(t[i] == "." and i > 0 and t[i - 1].isdigit() for i in range(len(t))):
            continue
        t = clean(t)
        if t and t not in GENERIC and len(t) >= 2 and not t.isdigit():
            if t[0].isdigit():
                rest = t.lstrip("0123456789")
                if not rest or rest[0] == "-":
                    continue
            tags.append(t)
    return tags[:4]


def extract_plain_text(path: Path) -> str:
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def extract_pdf_text(path: Path) -> str:
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            proc = subprocess.run(
                [pdftotext, "-q", str(path), "-"],
                check=False, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
            if proc.returncode == 0:
                return proc.stdout
            err = proc.stderr.strip()[:200] or "unknown error"
            log(f"pdftotext failed for {path.name}: exit {proc.returncode}: {err}")
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            log(f"pdftotext failed for {path.name}: {type(e).__name__}: {e}")

    textutil = shutil.which("textutil")
    if textutil:
        try:
            proc = subprocess.run(
                [textutil, "-convert", "txt", "-stdout", str(path)],
                check=False, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
            if proc.returncode == 0:
                return proc.stdout
            err = proc.stderr.strip()[:200] or "unknown error"
            log(f"textutil failed for {path.name}: exit {proc.returncode}: {err}")
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            log(f"textutil failed for {path.name}: {type(e).__name__}: {e}")
    else:
        log(f"skip PDF text for {path.name}: pdftotext/textutil not found")
    return ""


def read_zip_member_limited(zf: zipfile.ZipFile, name: str, limit: int) -> bytes:
    with zf.open(name) as handle:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ValueError(f"{name} exceeds {limit} bytes decompressed")
            chunks.append(chunk)
    return b"".join(chunks)


def extract_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = read_zip_member_limited(zf, "word/document.xml", MAX_DOCX_XML_BYTES)
        root = ET.fromstring(xml)
        texts = []
        for el in root.iter():
            if el.tag.endswith("}t") and el.text:
                texts.append(el.text)
        return " ".join(texts)
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError, ValueError) as e:
        log(f"docx extract failed for {path.name}: {type(e).__name__}: {e}")
        return ""


def extract_doc_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".txt", ".md"}:
        return extract_plain_text(path)
    if ext == ".pdf":
        return extract_pdf_text(path)
    if ext == ".docx":
        return extract_docx_text(path)
    return ""


def ocr_text(path: Path) -> str:
    try:
        with tempfile.TemporaryDirectory() as td:
            prep = Path(td) / "ocr.png"
            magick = subprocess.run(
                [
                    resolve_bin("magick"), str(path), "-resize", "250%",
                    "-colorspace", "Gray", "-auto-level",
                    "-sharpen", "0x1.1", "-contrast-stretch", "1%x1%",
                    str(prep),
                ],
                check=False, capture_output=True, timeout=30,
            )
            if magick.returncode != 0:
                err = magick.stderr.decode("utf-8", errors="replace").strip()[:200] or "unknown error"
                log(f"OCR failed for {path.name}: magick exit {magick.returncode}: {err}")
                return ""
            proc = subprocess.run(
                [resolve_bin("tesseract"), str(prep), "stdout", "--psm", "6"],
                check=False, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
            if proc.returncode != 0:
                err = proc.stderr.strip()[:200] or "unknown error"
                log(f"OCR failed for {path.name}: tesseract exit {proc.returncode}: {err}")
                return ""
            return proc.stdout
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
        log(f"OCR failed for {path.name}: {type(e).__name__}: {e}")
        return ""


def model_tags(ocr_txt: str, cfg: dict, image_path: Path | None = None) -> list[str]:
    """Ask vision model to tag an image using OCR text as grounding. Returns [] on any failure."""
    if not ocr_txt.strip():
        return []

    truncated = ocr_txt[:800].strip()
    prompt = (
        "What is this image about? OCR text from image:\n"
        f"{truncated}\n\n"
        "Return exactly 4 lowercase content tags, comma-separated, "
        "no explanations. No numbered lists.\n\nTags:"
    ) if truncated else (
        "What is this image about? Return exactly 4 lowercase content tags, "
        "comma-separated, no explanations. No numbered lists.\n\nTags:"
    )
    return _query_model_tags(prompt, cfg, image_path=image_path)


def text_model_tags(doc_txt: str, cfg: dict) -> list[str]:
    """Tag a document from extracted text only (no vision). Returns [] on any failure."""
    if not doc_txt.strip():
        return []
    truncated = doc_txt[:MAX_DOC_EXCERPT_CHARS].strip()
    prompt = (
        "What is this document about? Text excerpt:\n"
        f"{truncated}\n\n"
        "Return exactly 4 lowercase content tags, comma-separated, "
        "no explanations. No numbered lists.\n\nTags:"
    )
    return _query_model_tags(prompt, cfg, image_path=None)


def _query_model_tags(prompt: str, cfg: dict, image_path: Path | None = None) -> list[str]:
    payload: dict = {
        "model": cfg["model"],
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0},
    }
    if image_path and image_path.exists():
        try:
            with image_path.open("rb") as f:
                payload["images"] = [base64.b64encode(f.read()).decode("utf-8")]
        except OSError as e:
            log(f"model failed to read image: {type(e).__name__}: {e}")

    req = urllib.request.Request(
        cfg["endpoint"],
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("timeout", 30)) as r:
            data = json.loads(r.read().decode("utf-8"))
        return parse_model_tags(data.get("response", "").strip())
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, UnicodeDecodeError) as e:
        log(f"model failed: {type(e).__name__}: {e}")
        return []


def write_exif(path: Path, labels: list[str]) -> bool:
    if not labels:
        return True
    desc = " ".join(labels)
    # Clean up stale exiftool temp files from previous crashes
    stale = Path(str(path) + "_exiftool_tmp")
    if stale.exists():
        try:
            stale.unlink()
        except OSError:
            pass
    exif = None
    try:
        exif = subprocess.run(
            [resolve_bin("exiftool"), "-q", "-overwrite_original",
             f"-EXIF:ImageDescription={desc}", str(path)],
            check=False, capture_output=True, timeout=10,
        )
        if exif.returncode != 0:
            err = exif.stderr.decode("utf-8", errors="replace").strip()[:200] or "unknown error"
            log(f"exiftool failed for {path.name}: exit {exif.returncode}: {err}")
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
        log(f"exiftool failed for {path.name}: {type(e).__name__}: {e}")
    return exif is not None and exif.returncode == 0


def index_spotlight(path: Path) -> bool:
    proc = None
    try:
        proc = subprocess.run([resolve_bin("mdimport"), str(path)], check=False, capture_output=True, timeout=10)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()[:200] or "unknown error"
            log(f"mdimport failed for {path.name}: exit {proc.returncode}: {err}")
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
        log(f"mdimport failed for {path.name}: {type(e).__name__}: {e}")
    return proc is not None and proc.returncode == 0


def write_xattrs(path: Path, labels: list[str]) -> bool:
    payload = json.dumps(
        {
            "labels": labels,
            "labeled_at": dt.datetime.now().isoformat(timespec="seconds"),
            "tool": "tagme",
        },
        separators=(",", ":"),
    )
    try:
        labels_proc = subprocess.run(
            [resolve_bin("xattr"), "-w", "user.floomlens.labels", ",".join(labels), str(path)],
            check=False, capture_output=True, timeout=5,
        )
        if labels_proc.returncode != 0:
            err = labels_proc.stderr.decode("utf-8", errors="replace").strip()[:200] or "unknown error"
            log(f"xattr labels failed for {path.name}: exit {labels_proc.returncode}: {err}")
            return False
        json_proc = subprocess.run(
            [resolve_bin("xattr"), "-w", "user.floomlens.json", payload, str(path)],
            check=False, capture_output=True, timeout=5,
        )
        if json_proc.returncode != 0:
            err = json_proc.stderr.decode("utf-8", errors="replace").strip()[:200] or "unknown error"
            log(f"xattr json failed for {path.name}: exit {json_proc.returncode}: {err}")
            # Best-effort rollback of the labels xattr we just wrote
            try:
                subprocess.run(
                    [resolve_bin("xattr"), "-d", "user.floomlens.labels", str(path)],
                    check=False, capture_output=True, timeout=5,
                )
            except Exception:
                pass
            return False
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
        log(f"xattr failed for {path.name}: {type(e).__name__}: {e}")
        # Best-effort rollback if labels may have been written
        try:
            subprocess.run(
                [resolve_bin("xattr"), "-d", "user.floomlens.labels", str(path)],
                check=False, capture_output=True, timeout=5,
            )
        except Exception:
            pass
        return False
    return True


def already_enriched(path: Path) -> bool:
    try:
        proc = subprocess.run(
            [resolve_bin("xattr"), "-p", "user.floomlens.labels", str(path)],
            check=False, capture_output=True, text=True, timeout=5,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return False


def exiftool_field(path: Path, field: str) -> str:
    try:
        proc = subprocess.run(
            [resolve_bin("exiftool"), f"-{field}", "-s3", str(path)],
            check=False, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        pass
    return ""


def write_pdf_keywords(path: Path, labels: list[str], cfg: dict) -> bool:
    if not cfg.get("doc_pdf_keywords", True) or path.suffix.lower() != ".pdf":
        return True
    if exiftool_field(path, "PDF:Keywords") or exiftool_field(path, "Subject"):
        return True
    joined = ", ".join(labels)
    try:
        proc = subprocess.run(
            [
                resolve_bin("exiftool"), "-q", "-overwrite_original",
                f"-PDF:Keywords={joined}", f"-Subject={joined}", str(path),
            ],
            check=False, capture_output=True, timeout=10,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()[:200] or "unknown error"
            log(f"pdf keywords skipped for {path.name}: exit {proc.returncode}: {err}")
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
        log(f"pdf keywords skipped for {path.name}: {type(e).__name__}: {e}")
    return True


def write_finder_tags(path: Path, labels: list[str], cfg: dict) -> bool:
    if not cfg.get("doc_finder_tags", False):
        return True
    tag_bin = shutil.which("tag")
    if not tag_bin:
        return True
    try:
        proc = subprocess.run(
            [tag_bin, "-a", *labels, str(path)],
            check=False, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            err = proc.stderr.strip()[:200] or "unknown error"
            log(f"finder tags skipped for {path.name}: exit {proc.returncode}: {err}")
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
        log(f"finder tags skipped for {path.name}: {type(e).__name__}: {e}")
    return True


def write_doc_metadata(path: Path, labels: list[str], cfg: dict) -> bool:
    if not write_xattrs(path, labels):
        return False
    write_pdf_keywords(path, labels, cfg)
    write_finder_tags(path, labels, cfg)
    index_spotlight(path)
    return True


def should_rename_doc(path: Path, cfg: dict) -> bool:
    return cfg.get("doc_rename") == "generic_only" and is_generic_document_name(path)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 2
    while True:
        candidate = path.with_name(f"{stem}-{i}{suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def maybe_rename(path: Path, labels: list[str], cfg: dict, *, kind: str = "image") -> Path:
    if kind == "doc":
        if not should_rename_doc(path, cfg):
            return path
    elif not cfg.get("rename", True):
        return path
    date = dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    label_str = "-".join(labels[: cfg.get("max_labels", 4)]) or "file"
    label_str = label_str[:50]  # guard against absurdly long filenames
    stem = path.stem
    if "__" in stem:
        parts = stem.split("__")
        if len(parts) >= 3 and len(parts[0]) == 10 and parts[0][4] == "-" and parts[0][7] == "-" and parts[0][:4].isdigit() and parts[0][5:7].isdigit() and parts[0][8:10].isdigit():
            orig = clean(parts[-1]) or "file"
        else:
            orig = clean(stem) or "file"
    else:
        orig = clean(stem) or "file"
    suffix = path.suffix.lower()
    new_name = cfg.get("filename_format", DEFAULT_CONFIG["filename_format"]).format(
        date=date, labels=label_str, orig=orig,
    ) + suffix
    if path.name == new_name:
        return path
    target = unique_path(path.with_name(new_name))
    path.rename(target)
    return target


def should_ignore(path: Path, cfg: dict) -> bool:
    name = path.name
    for p in cfg.get("ignore_prefixes", []):
        if name.startswith(p):
            return True
    return False


def is_already_tagged(path: Path) -> bool:
    stem = path.stem
    if "__" not in stem:
        return False
    parts = stem.split("__")
    return (
        len(parts) >= 2
        and len(parts[0]) == 10
        and parts[0][4] == "-"
        and parts[0][7] == "-"
        and parts[0][:4].isdigit()
        and parts[0][5:7].isdigit()
        and parts[0][8:10].isdigit()
    )


def is_fresh_screenshot(path: Path) -> bool:
    """Return True only for images that look like fresh screenshots or generic camera/WhatsApp images."""
    if is_already_tagged(path):
        return False
    stem = path.stem
    lower = stem.lower()
    patterns = (
        "screenshot ", "screen shot ", "img_", "whatsapp image", "pxl_",
        "dsc_", "mvimg_", "image", "photo", "pic", "picture", "img-",
    )
    if any(lower.startswith(p) for p in patterns):
        return True
    if re.fullmatch(r"\d{13,}", stem):
        return True
    if re.fullmatch(r"\d{8}_\d{6}", stem):
        return True
    if re.fullmatch(r"100\d{6,}", stem):
        return True
    return False


def is_generic_document_name(path: Path) -> bool:
    stem = path.stem.lower()
    if stem in GENERIC_DOC_STEMS:
        return True
    return stem.startswith(("document", "untitled", "scan", "file ", "download"))


def is_fresh_document(path: Path) -> bool:
    """Return True only for docs with obviously worthless filenames."""
    if is_already_tagged(path) or already_enriched(path):
        return False
    return is_generic_document_name(path)


def file_kind(path: Path, cfg: dict) -> str | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS and is_fresh_screenshot(path):
        if not image_pipeline_ready():
            return None
        return "image"
    if ext in DOC_V02_EXTS and cfg.get("process_docs", True) and is_fresh_document(path):
        if not doc_pipeline_ready():
            return None
        return "doc"
    return None


def process_file(path: Path, cfg: dict, conn: sqlite3.Connection) -> tuple[bool, bool]:
    if not path.is_file() or path.is_symlink() or should_ignore(path, cfg):
        return False, False
    ext = path.suffix.lower()
    if ext in TEMP_SUFFIXES:
        return False, False

    kind = file_kind(path, cfg)
    if kind is None:
        return False, False

    enabled_since = cfg.get("enabled_since")
    if enabled_since:
        try:
            cutoff = dt.datetime.fromisoformat(enabled_since).timestamp()
            if path.stat().st_mtime < cutoff:
                return False, False
        except Exception:
            pass
    fp = fingerprint(path)
    row = conn.execute("SELECT fingerprint FROM files WHERE path = ?", (str(path),)).fetchone()
    if row and row[0] == fp:
        return False, False

    if time.time() - path.stat().st_mtime < 5:
        first_stat = path.stat()
        time.sleep(0.5)
        second_stat = path.stat()
        if first_stat.st_size != second_stat.st_size or first_stat.st_mtime != second_stat.st_mtime:
            log(f"skip {path.name}: file still changing")
            return False, False

    labels = [base_label(path)]
    ai_tags: list[str] = []

    if kind == "image":
        if path.stat().st_size > MAX_IMAGE_OCR_BYTES:
            log(f"skip OCR {path.name}: file exceeds 50MB")
        else:
            txt = ocr_text(path)
            ai_tags = model_tags(txt, cfg, image_path=path)
            if not ai_tags:
                if txt.strip():
                    log(f"model returned no tags for {path.name}")
                else:
                    log(f"OCR empty for {path.name}")
            else:
                labels.extend(ai_tags)
    else:
        if path.stat().st_size > MAX_DOC_TEXT_BYTES:
            log(f"skip doc text {path.name}: file exceeds 10MB")
            return False, False
        txt = extract_doc_text(path)
        if not txt.strip():
            return False, False
        ai_tags = text_model_tags(txt, cfg)
        if not ai_tags:
            return False, False
        labels.extend(ai_tags)

    # Deduplicate and sanitize
    uniq = []
    for l in labels:
        l = clean(l)
        if not l or l in GENERIC or l.isdigit():
            continue
        if len(l) == 4 and l[:2] in ("19", "20") and l[2:].isdigit():
            continue
        if l not in uniq:
            uniq.append(l)

    if ext in DOC_EXTS and "document" not in uniq:
        uniq.insert(0, "document")
    elif ext in VIDEO_EXTS and "video" not in uniq:
        uniq.insert(0, "video")
    elif ext in AUDIO_EXTS and "audio" not in uniq:
        uniq.insert(0, "audio")
    elif ext in SHEET_EXTS and "spreadsheet" not in uniq:
        uniq.insert(0, "spreadsheet")
    if not uniq:
        uniq = [base_label(path)]
    uniq = uniq[: cfg.get("max_labels", 4)]

    if kind == "image":
        if not write_exif(path, uniq):
            log(f"warning: metadata write failed for {path.name}, skipping rename and DB update")
            return False, True
        if not write_xattrs(path, uniq):
            log(f"warning: metadata write failed for {path.name}, skipping rename and DB update")
            try:
                subprocess.run(
                    [resolve_bin("exiftool"), "-q", "-overwrite_original",
                     "-EXIF:ImageDescription=", str(path)],
                    check=False, capture_output=True, timeout=10,
                )
            except Exception:
                pass
            return False, True
    else:
        if not write_doc_metadata(path, uniq, cfg):
            return False, True

    new_path = maybe_rename(path, uniq, cfg, kind=kind)
    metadata_failed = False

    if kind == "image":
        index_spotlight(new_path)

    st = new_path.stat()
    conn.execute(
        """
        INSERT INTO files(path, inode, size, mtime, fingerprint, labels, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
          inode=excluded.inode,
          size=excluded.size,
          mtime=excluded.mtime,
          fingerprint=excluded.fingerprint,
          labels=excluded.labels,
          processed_at=excluded.processed_at
        """,
        (
            str(new_path),
            int(st.st_ino),
            int(st.st_size),
            float(st.st_mtime),
            fingerprint(new_path),
            json.dumps(uniq),
            dt.datetime.now().isoformat(timespec="seconds"),
        ),
    )
    if str(new_path) != str(path):
        conn.execute("DELETE FROM files WHERE path = ?", (str(path),))
    log(f"{'labeled' if kind == 'image' else 'enriched'}: {new_path.name} -> {uniq}")
    return True, metadata_failed


def _acquire_lock():
    lock_path = STATE_DIR / ".flock"
    lock_path.touch(exist_ok=True)
    lock_file = lock_path.open("r+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except BlockingIOError:
        lock_file.close()
        return None


def _release_lock(lock_file) -> None:
    if lock_file is None:
        return
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def run_once() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    lock_file = _acquire_lock()
    if lock_file is None:
        log("another instance is running, skipping")
        return 0

    conn = None
    processed_count = 0
    skipped_count = 0
    error_count = 0
    metadata_fail_count = 0
    try:
        cfg = validate_config(load_config())
        conn = db()
        cleanup_orphaned_rows(conn)
        if not dependencies_available(cfg):
            return 0
        log("scan start")
        for d in cfg.get("watch_dirs", []):
            root = Path(d)
            if not root.exists() or not root.is_dir():
                continue
            children = root.rglob("*") if cfg.get("recursive", False) else root.iterdir()
            for child in children:
                if not child.is_file():
                    continue
                try:
                    processed, metadata_failed = process_file(child, cfg, conn)
                    if processed:
                        processed_count += 1
                    else:
                        skipped_count += 1
                    if metadata_failed:
                        metadata_fail_count += 1
                except (FileNotFoundError, PermissionError, OSError) as e:
                    log(f"skip {child.name}: {e}")
                    error_count += 1
                    continue
                except Exception as e:
                    log(f"skip {child.name}: unexpected {type(e).__name__}: {e}")
                    error_count += 1
                    continue
        conn.commit()
        log(
            "scan done — "
            f"processed: {processed_count}, skipped: {skipped_count}, "
            f"errors: {error_count}, metadata_failures: {metadata_fail_count}"
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _release_lock(lock_file)
    return 0


def __test__() -> int:
    global log

    assert clean(" Hello, WORLD! ") == "hello-world"
    assert clean("foo___bar   baz") == "foo-bar-baz"
    assert clean("...") == ""
    assert clean("a" * 40) == "a" * 30

    invalid_cfg = {
        "watch_dirs": "bad",
        "max_labels": 0,
        "timeout": True,
        "filename_format": "{date}-{orig}",
        "endpoint": "ftp://bad",
        "model": "",
        "enabled_since": "not-a-date",
        "rename": "maybe",
        "ignore_prefixes": "bad",
        "recursive": "yes",
    }
    real_log = log
    logged = []
    log = logged.append
    try:
        validated = validate_config(invalid_cfg)
    finally:
        log = real_log
    assert validated["watch_dirs"] == DEFAULT_CONFIG["watch_dirs"]
    assert validated["max_labels"] == 1
    assert validated["timeout"] == DEFAULT_CONFIG["timeout"]
    assert validated["filename_format"] == DEFAULT_CONFIG["filename_format"]
    assert validated["endpoint"] == DEFAULT_CONFIG["endpoint"]
    assert validated["model"] == DEFAULT_CONFIG["model"]
    dt.datetime.fromisoformat(validated["enabled_since"])
    assert validated["rename"] == DEFAULT_CONFIG["rename"]
    assert validated["ignore_prefixes"] == DEFAULT_CONFIG["ignore_prefixes"]
    assert validated["recursive"] == DEFAULT_CONFIG["recursive"]

    with tempfile.TemporaryDirectory() as td:
        original = Path(td) / "original.txt"
        renamed = Path(td) / "renamed.txt"
        original.write_text("same file\n", encoding="utf-8")
        os.utime(original, (1_700_000_000, 1_700_000_000))
        before = fingerprint(original)
        original.rename(renamed)
        after = fingerprint(renamed)
        assert before == after

        override = Path(td) / "tool"
        override.write_text("#!/bin/sh\n", encoding="utf-8")
        old = REQUIRED_BINARIES.get("__test_tool__")
        REQUIRED_BINARIES["__test_tool__"] = str(override)
        try:
            assert resolve_bin("__test_tool__") == str(override)
        finally:
            if old is None:
                REQUIRED_BINARIES.pop("__test_tool__", None)
            else:
                REQUIRED_BINARIES["__test_tool__"] = old

    assert resolve_bin("sh") == shutil.which("sh")
    assert resolve_bin("__definitely_missing_file_labeler_bin__") == "__definitely_missing_file_labeler_bin__"

    assert parse_model_tags("sales, hiring, pipeline, mcp") == ["sales", "hiring", "pipeline", "mcp"]
    assert parse_model_tags("image, screenshot, ui, page") == []
    assert parse_model_tags("too many words in this response that should definitely be rejected right now please") == []

    tagged = Path("2026-06-05__finance-receipt__document.pdf")
    assert is_already_tagged(tagged) is True
    assert is_fresh_document(tagged) is False
    assert is_fresh_document(Path("document.pdf")) is True
    assert is_fresh_document(Path("hiring-contract-2026.pdf")) is False
    assert is_fresh_document(Path("a8f3k2j1.pdf")) is False
    assert is_fresh_screenshot(Path("Screenshot 2026-06-05 at 11.54.13 AM.png")) is True
    assert should_rename_doc(Path("document.pdf"), {"doc_rename": "never"}) is False
    assert should_rename_doc(Path("document.pdf"), {"doc_rename": "generic_only"}) is True

    with tempfile.TemporaryDirectory() as td:
        note = Path(td) / "document.pdf"
        note.write_bytes(b"%PDF-1.4 placeholder")
        assert file_kind(note, {"process_docs": True}) == "doc"
        assert file_kind(Path(td) / "hiring-contract-2026.pdf", {"process_docs": True}) is None
        assert file_kind(Path("Screenshot 2026-05-01.png"), {"process_docs": True}) == "image"
        assert file_kind(note, {"process_docs": False}) is None
        assert file_kind(Path(td) / "notes.md", {"process_docs": True}) is None

        plain = Path(td) / "untitled.md"
        plain.write_text("# Hiring\nSales pipeline summary\n", encoding="utf-8")
        assert "hiring" in extract_plain_text(plain).lower()
        assert file_kind(plain, {"process_docs": True}) == "doc"

        docx = Path(td) / "document.docx"
        with zipfile.ZipFile(docx, "w") as zf:
            zf.writestr(
                "word/document.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>Quarterly revenue report</w:t></w:r></w:p></w:body>"
                "</w:document>",
            )
        assert "revenue" in extract_docx_text(docx).lower()

    print("tests passed")
    return 0


if __name__ == "__main__":
    try:
        if os.environ.get("TEST") == "1":
            sys.exit(__test__())
        sys.exit(run_once())
    except Exception as e:
        log(f"FATAL: {type(e).__name__}: {e}")
        sys.exit(1)
