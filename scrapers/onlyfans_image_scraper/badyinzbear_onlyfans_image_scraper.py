import sys
import json
import sqlite3
import os
from pathlib import Path
import datetime
import re

LOG_PATH = Path(__file__).with_name("debug.log")

def log(msg):
    timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} {msg}\n")

def extract_username(path):
    # Expects path like O:/OnlyFans/{username}/{username - Images}/filename
    try:
        parts = Path(path).parts
        if "OnlyFans" in parts:
            idx = parts.index("OnlyFans")
            return parts[idx + 1]
    except Exception as e:
        log(f"⚠️ Failed to extract username: {e}")
    return None

def get_db_path(username):
    return os.path.join("O:/Metadata/Onlyfans", username, "user_data.db")

def clean_text(text):
    # Strip HTML tags, excessive whitespace, and stray entities
    clean = re.sub(r"<[^>]+>", "", text or "")
    clean = re.sub(r"&[a-z]+;", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean

def find_image_path_from_fragment(fragment):
    base_path = Path("O:/OnlyFans")
    for username_dir in base_path.iterdir():
        if not username_dir.is_dir():
            continue
        images_folder = username_dir / f"{username_dir.name} - Images"
        if not images_folder.exists():
            continue
        for file in images_folder.glob("*"):
            if fragment in file.stem:
                return str(file)
    return ""

def run():
    raw = sys.stdin.read()
    log(f"🧪 Raw stdin: {raw}")
    args = json.loads(raw or "{}")

    fragment = args.get("fragment") or Path(args.get("title", "")).stem
    path = args.get("path", "")
    log(f"Received fragment={fragment}, path={path}")

    if not path:
        path = find_image_path_from_fragment(fragment)
        log(f"🔍 Fallback found path: {path}")

    username = extract_username(path)
    log(f"Extracted username: {username}")

    if not username or not path:
        log("❌ Could not extract username or path is missing.")
        print("{}")
        return

    db_path = get_db_path(username)
    log(f"DB path: {db_path}")

    if not os.path.exists(db_path):
        log("❌ DB not found.")
        print("{}")
        return

    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row

        query = """
            SELECT m.filename, m.created_at, p.text
            FROM medias m
            JOIN posts p ON m.post_id = p.id
            JOIN profiles pr ON p.model_id = pr.user_id
            WHERE m.filename = ? COLLATE NOCASE
        """
        row = None
        for ext in ["jpg", "jpeg", "png", "webp", "gif"]:
            full_filename = f"{fragment}.{ext}"
            log(f"🔍 Trying match with filename: {full_filename}")
            row = db.execute(query, (full_filename,)).fetchone()
            if row:
                break

        db.close()

        if not row:
            log("⚠️ No metadata match found.")
            print("{}")
            return

        title = os.path.splitext(row["filename"])[0]
        date = row["created_at"][:10] if row["created_at"] else ""
        details = clean_text(row["text"])

        log(f"✅ Match found: title={title}, date={date}")
        log(f"Details preview: {details[:80]}...")

        output = {
            "title": title,
            "details": details,
            "date": date
        }

        print(json.dumps(output))

    except Exception as e:
        log(f"💥 Uncaught error: {e}")
        print("{}")

if __name__ == "__main__":
    try:
        mode = sys.argv[1] if len(sys.argv) > 1 else "unknown"
        if mode != "imageByFragment":
            log(f"Exiting — mode '{mode}' not supported")
            print("{}")
            sys.exit(0)
        run()
    except Exception as e:
        log(f"💥 Script-level error: {e}")
        print("{}")
