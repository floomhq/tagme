#!/usr/bin/env python3
import base64
import datetime as dt
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

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
    "write_finder_comment": False,
    "filename_format": "{date}__{labels}__{orig}",
    "max_labels": 4,
    "enabled_since": dt.datetime.now().isoformat(timespec="seconds"),
    "ollama": {
        "enabled": True,
        "model": "llava:7b",
        "endpoint": "http://127.0.0.1:11434/api/generate",
        "timeout_seconds": 20,
    },
    "ocr": {
        "enabled": True,
    },
}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"}
DOC_EXTS = {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".pages"}
SHEET_EXTS = {".csv", ".tsv", ".xls", ".xlsx"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sh", ".json", ".yml", ".yaml"}
GENERIC_LABELS = {
    "image",
    "text",
    "screenshot",
    "screen",
    "computer-screen",
    "display",
    "user-interface",
    "interface",
    "software",
    "website",
    "webpage",
    "app",
    "application",
    "page",
    "ui",
}
DOMAIN_STOP = {"www", "http", "https", "com", "net", "org", "dev", "io", "ai", "app", "co"}
SCREEN_TYPE_HINTS = [
    "signin",
    "login",
    "dashboard",
    "settings",
    "profile",
    "pricing",
    "search-results",
    "error",
    "token-setup",
    "install",
    "onboarding",
]
TOPIC_HINTS = [
    "mcp-config",
    "api-token",
    "workspace",
    "agent-auth",
    "waitlist",
    "billing",
    "analytics",
    "leads",
    "resume",
]
PERSON_STOP = {"federico", "de", "ponte"}
SOURCE_PRIORITY = ["floom", "openai", "github", "linkedin", "google", "claude", "chatgpt", "notion", "slack"]


def log(msg: str) -> None:
    ts = dt.datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def ensure_dirs() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        cfg = DEFAULT_CONFIG.copy()
        cfg["enabled_since"] = dt.datetime.now().isoformat(timespec="seconds")
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return cfg
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_CONFIG


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


def fingerprint(path: Path) -> str:
    st = path.stat()
    raw = f"{path}:{st.st_size}:{int(st.st_mtime)}:{st.st_ino}".encode()
    return hashlib.sha1(raw).hexdigest()


def cleaned_token(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:30]


def base_labels_from_ext(path: Path) -> list[str]:
    ext = path.suffix.lower()
    if path.name.startswith("Screenshot"):
        return ["screenshot"]
    if ext in IMAGE_EXTS:
        return ["image"]
    if ext in DOC_EXTS:
        return ["document"]
    if ext in SHEET_EXTS:
        return ["spreadsheet"]
    if ext in AUDIO_EXTS:
        return ["audio"]
    if ext in VIDEO_EXTS:
        return ["video"]
    if ext in CODE_EXTS:
        return ["code"]
    mt, _ = mimetypes.guess_type(str(path))
    if mt:
        return [cleaned_token(mt.split("/")[0])]
    return ["file"]


def name_hints(path: Path) -> list[str]:
    stem = cleaned_token(path.stem)
    bits = [b for b in stem.split("-") if len(b) > 2]
    stop = {"screenshot", "image", "final", "copy", "new", "untitled", "img"}
    out = [b for b in bits if b not in stop][:2]
    return out


def ollama_available(endpoint: str, timeout: int) -> bool:
    req = urllib.request.Request(endpoint.replace("/api/generate", "/api/tags"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def ollama_image_labels(path: Path, cfg: dict) -> list[str]:
    try:
        img_data = base64.b64encode(path.read_bytes()).decode("ascii")
        prompt = (
            "You are labeling desktop files for search.\n"
            "Return ONLY comma-separated tags, no prose.\n"
            "Rules:\n"
            "- 4 to 7 tags, all lowercase.\n"
            "- prioritize specific identifiers: app/site/brand/logo names when visible.\n"
            "- include concrete UI context (e.g. login-form, dashboard, pricing-page, error-modal).\n"
            "- avoid generic tags like image, screenshot, text, ui, website, app.\n"
            "- if no brand is visible, infer likely context (e.g. terminal, code-editor, browser-tab).\n"
        )
        payload = {
            "model": cfg["model"],
            "prompt": prompt,
            "images": [img_data],
            "stream": False,
        }
        req = urllib.request.Request(
            cfg["endpoint"],
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=cfg.get("timeout_seconds", 20)) as r:
            data = json.loads(r.read().decode("utf-8"))
        resp = data.get("response", "")
        tokens = [cleaned_token(t) for t in re.split(r"[,\n]", resp) if cleaned_token(t)]
        return tokens[:5]
    except Exception:
        return []


def ocr_extract(path: Path) -> tuple[list[str], str]:
    try:
        with tempfile.TemporaryDirectory() as td:
            prep = Path(td) / "ocr.png"
            subprocess.run(
                [
                    "magick",
                    str(path),
                    "-resize",
                    "250%",
                    "-colorspace",
                    "Gray",
                    "-auto-level",
                    "-sharpen",
                    "0x1.1",
                    "-contrast-stretch",
                    "1%x1%",
                    str(prep),
                ],
                check=False,
                capture_output=True,
            )
            proc = subprocess.run(
                ["tesseract", str(prep), "stdout", "--psm", "6"],
                check=False,
                capture_output=True,
                text=True,
            )
            txt = proc.stdout.lower()
    except Exception:
        return [], ""

    found = []
    # domain-based tokens (high signal for origin/source)
    for m in re.findall(r"\b([a-z0-9-]+\.(?:ai|app|dev|com|io|org|net))\b", txt):
        core = m.split(".")[0]
        if core and core not in DOMAIN_STOP:
            found.append(core)
    # product/brand-ish tokens (letters only, 4-24 chars)
    for m in re.findall(r"\b[a-z][a-z0-9-]{3,24}\b", txt):
        if m in DOMAIN_STOP:
            continue
        if m in {"token", "install", "workspace", "dashboard", "login", "signin", "password"}:
            continue
        found.append(m)
    uniq = []
    for t in found:
        t = cleaned_token(t)
        if t and t not in uniq:
            uniq.append(t)
    return uniq[:12], txt


def classify_buckets(path: Path, ocr_labels: list[str], vlm_labels: list[str], ocr_text: str) -> list[str]:
    merged = []
    for t in ocr_labels + vlm_labels:
        t = cleaned_token(t)
        if t and t not in merged:
            merged.append(t)

    source = ""
    for p in SOURCE_PRIORITY:
        if p in merged or re.search(rf"\b{re.escape(p)}\b", ocr_text):
            source = p
            break
    for t in merged:
        if source:
            break
        if t in GENERIC_LABELS:
            continue
        if t in PERSON_STOP:
            continue
        if t in SCREEN_TYPE_HINTS or t in TOPIC_HINTS:
            continue
        if len(t) >= 4 and not t.isdigit():
            source = t
            break

    screen_type = ""
    for t in merged:
        if t in SCREEN_TYPE_HINTS:
            screen_type = t
            break
    if not screen_type:
        text = f"{'-'.join(merged)} {ocr_text}"
        if "token" in text or "credential" in text:
            screen_type = "token-setup"
        elif "signin" in text or "log in" in text or "login" in text:
            screen_type = "signin"
        elif "error" in text:
            screen_type = "error"
        elif "dashboard" in text:
            screen_type = "dashboard"
        elif "install" in text:
            screen_type = "install"
        elif path.name.startswith("Screenshot"):
            screen_type = "app-screen"

    task_topic = ""
    for t in merged:
        if t in TOPIC_HINTS:
            task_topic = t
            break
    if not task_topic:
        text = f"{'-'.join(merged)} {ocr_text}"
        if "mcp" in text:
            task_topic = "mcp-config"
        elif "api" in text and ("token" in text or "bearer" in text):
            task_topic = "api-token"
        elif "workspace" in text:
            task_topic = "workspace"
        elif "agent" in text:
            task_topic = "agent-auth"
        elif "token" in text:
            task_topic = "agent-auth"
        elif "login" in text or "signin" in text:
            task_topic = "access"

    out = []
    if path.name.startswith("Screenshot"):
        out.append("screenshot")
    elif path.suffix.lower() in IMAGE_EXTS:
        out.append("image")
    if source:
        out.append(source)
    if screen_type:
        out.append(screen_type)
    if task_topic:
        out.append(task_topic)
    return out[:4]


def set_finder_comment(path: Path, labels: list[str]) -> None:
    comment = f"labels: {', '.join(labels)}"
    script = f'''
    tell application "Finder"
      try
        set comment of (POSIX file "{str(path).replace('"', '\\"')}") to "{comment.replace('"', '\\"')}"
      end try
    end tell
    '''
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def write_image_metadata(path: Path, labels: list[str]) -> None:
    # Store machine-readable labels in extended attributes for agent/tool discovery.
    payload = json.dumps(
        {
            "labels": labels,
            "labeled_at": dt.datetime.now().isoformat(timespec="seconds"),
            "tool": "floomlens",
        },
        separators=(",", ":"),
    )
    subprocess.run(["xattr", "-w", "user.floomlens.labels", ",".join(labels), str(path)], check=False, capture_output=True)
    subprocess.run(["xattr", "-w", "user.floomlens.json", payload, str(path)], check=False, capture_output=True)


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


def maybe_rename(path: Path, labels: list[str], cfg: dict) -> Path:
    if not cfg.get("rename", True):
        return path
    date = dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    label_str = "-".join(labels[: cfg.get("max_labels", 4)]) or "file"
    orig = cleaned_token(path.stem) or "file"
    suffix = path.suffix.lower()
    new_name = cfg.get("filename_format", DEFAULT_CONFIG["filename_format"]).format(
        date=date,
        labels=label_str,
        orig=orig,
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


def process_file(path: Path, cfg: dict, conn: sqlite3.Connection, can_ollama: bool) -> None:
    if not path.is_file() or should_ignore(path, cfg):
        return
    enabled_since = cfg.get("enabled_since")
    if enabled_since:
        try:
            cutoff = dt.datetime.fromisoformat(enabled_since).timestamp()
            if path.stat().st_mtime < cutoff:
                return
        except Exception:
            pass
    fp = fingerprint(path)
    row = conn.execute("SELECT fingerprint FROM files WHERE path = ?", (str(path),)).fetchone()
    if row and row[0] == fp:
        return

    labels = []
    labels.extend(base_labels_from_ext(path))
    labels.extend(name_hints(path))

    ocr_labels = []
    ocr_text = ""
    vlm_labels = []
    if path.suffix.lower() in IMAGE_EXTS and cfg.get("ocr", {}).get("enabled", True):
        ocr_labels, ocr_text = ocr_extract(path)
        labels.extend(ocr_labels)
    if can_ollama and path.suffix.lower() in IMAGE_EXTS:
        vlm_labels = ollama_image_labels(path, cfg["ollama"])
        labels.extend(vlm_labels)

    # de-dup, sanitize
    uniq = []
    for l in labels:
        l = cleaned_token(l)
        if not l:
            continue
        if l in GENERIC_LABELS:
            continue
        # avoid timestamp-ish tokens from screenshot names
        if re.fullmatch(r"(19|20)\d{2}", l):
            continue
        if l.isdigit():
            continue
        if l not in uniq:
            uniq.append(l)
    # For images/screenshots, replace free-form tags with bucketed labels.
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        uniq = classify_buckets(path, ocr_labels, vlm_labels, ocr_text)
    elif ext in DOC_EXTS and "document" not in uniq:
        uniq.insert(0, "document")
    elif ext in VIDEO_EXTS and "video" not in uniq:
        uniq.insert(0, "video")
    elif ext in AUDIO_EXTS and "audio" not in uniq:
        uniq.insert(0, "audio")
    elif ext in SHEET_EXTS and "spreadsheet" not in uniq:
        uniq.insert(0, "spreadsheet")
    uniq = uniq[: cfg.get("max_labels", 4)]

    new_path = maybe_rename(path, uniq, cfg)

    if new_path.suffix.lower() in IMAGE_EXTS:
        write_image_metadata(new_path, uniq)

    if cfg.get("write_finder_comment", True):
        set_finder_comment(new_path, uniq)

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
    conn.commit()
    log(f"labeled: {new_path.name} -> {uniq}")


def run_once() -> int:
    cfg = load_config()
    conn = db()
    can_ollama = False
    if cfg.get("ollama", {}).get("enabled", True):
        can_ollama = ollama_available(
            cfg["ollama"].get("endpoint", DEFAULT_CONFIG["ollama"]["endpoint"]),
            int(cfg["ollama"].get("timeout_seconds", 20)),
        )
    log(f"scan start (ollama={'on' if can_ollama else 'off'})")
    for d in cfg.get("watch_dirs", []):
        root = Path(d)
        if not root.exists() or not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_file():
                process_file(child, cfg, conn, can_ollama)
    log("scan done")
    return 0


if __name__ == "__main__":
    sys.exit(run_once())
