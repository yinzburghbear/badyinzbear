import sys
import json
import re
import os
from pathlib import Path
from datetime import datetime
import requests
import logging

# === Fixed locations ===
SCRAPER_DIR = Path("C:/Users/jason/.stash/scrapers/Yinzburghbears_Scrapers/myjsonscraper/WFDXScraper")
JSON_DIR    = Path("C:/Users/jason/.stash/scrapers/Yinzburghbears_Scrapers/myjsonscraper/XJSON")
STASH_URL   = "http://localhost:9999/graphql"

# === Debug control ===
DEBUG_DEFAULT = False
DEBUG_ENABLED = DEBUG_DEFAULT or (os.environ.get("WFDX_DEBUG", "0").strip().lower() in ("1","true","on","yes"))

# === Logging ===
LOG_PATH = SCRAPER_DIR / "WFDXScraper.log"
LOG = logging.getLogger("WFDXScraper")
LOG.setLevel(logging.DEBUG)
LOG.handlers.clear()
try:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setLevel(logging.DEBUG if DEBUG_ENABLED else logging.ERROR)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    LOG.addHandler(fh)
except Exception:
    LOG.addHandler(logging.NullHandler())

# === Tunables ===
CODE_MIN_DIGITS = 12
STUDIO_SUFFIX = " (X)"

# ------------------------- IO -------------------------

def read_stdin_payload() -> dict:
    raw = sys.stdin.read()
    if DEBUG_ENABLED:
        LOG.debug(f"Raw STDIN: {raw}")
    payload = json.loads(raw) if raw.strip() else {}
    parsed = payload.get("input", payload)
    if DEBUG_ENABLED:
        LOG.debug(f"Parsed payload: {parsed}")
    return parsed

# ------------------------- Utils -------------------------

def sanitize_name(s: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", (s or '').strip().lower())).strip("_")

def normalize_title_from_filename(filename: str) -> str:
    base = Path(filename).stem
    base = base.replace('.', ' ').replace('_', ' ').replace('-', ' ')
    return ' '.join(base.split())

def clean_tweet_text(text: str) -> str:
    return re.sub(r"https?://t\.co/\S+", "", (text or "")).strip()

def extract_digit_candidates(fragment: str) -> list[str]:
    base = Path(fragment).name.rsplit(".", 1)[0]
    base = re.sub(r"[-_\s]+\d{1,2}[-_]\d{1,2}[-_]\d{2,4}[-_\s]+\d{1,2}[-_]\d{1,2}[-_]\d{1,2}(?:\s*[APMapm]{2})?$", "", base)
    matches = list(re.finditer(r"\d+", base))
    seqs = []
    seen = set()
    for m in matches:
        s = m.group(0)
        if len(s) < 8:
            continue
        if s not in seen:
            seen.add(s)
            seqs.append(s)
    seqs.sort(key=lambda s: (-(1 if len(s) in (18, 19) else 0), -len(s), base.find(s)))
    if DEBUG_ENABLED:
        LOG.debug(f"Digit candidates from '{base}': {seqs}")
    return seqs

def status_id_from_url(url: str) -> str:
    m = re.search(r"/status/(\d+)", url or "")
    return m.group(1) if m else ""

# ------------------------- Date logic -------------------------

def parse_date(raw: str) -> str | None:
    if not raw:
        return None
    fmts = (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%a %b %d %H:%M:%S %z %Y",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
    )
    for fmt in fmts:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except:
            continue
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None

def normalize_date_token(token: str | None) -> str:
    if not token or not token.isdigit():
        return ""
    def mk(y,m,d):
        try:
            return datetime(y,m,d).strftime("%Y-%m-%d")
        except:
            return ""
    L = len(token)
    if L == 6:
        return mk(2000+int(token[0:2]), int(token[2:4]), int(token[4:6]))
    if L == 8:
        return mk(int(token[0:4]), int(token[4:6]), int(token[6:8]))
    if L == 10 or L == 11 or L == 12:
        yy = int(token[0:2]); m = int(token[2:4]); d = int(token[4:6])
        return mk(2000+yy, m, d)
    return ""

def find_date_token_excluding_code(frag: str, code_span: tuple[int,int] | None) -> str:
    runs = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\d+", frag)]
    def pick_len(L: int) -> str:
        subset = [r for r in runs if len(r[0]) == L]
        if not subset:
            return ""
        if code_span:
            right = [r for r in subset if r[1] >= code_span[1]]
            cand = right or subset
        else:
            cand = subset
        return max(cand, key=lambda r: r[1])[0]
    for L in (12, 11, 10, 8, 6):
        tok = pick_len(L)
        if tok:
            return tok
    return ""

# ------------------------- JSON access -------------------------

def json_files() -> list[Path]:
    return list(JSON_DIR.glob("*.json"))

def load_json_entries(json_path: Path) -> list[dict]:
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        entries = [e["otherPropertiesMap"] for e in data.get("links", []) if isinstance(e, dict) and "otherPropertiesMap" in e]
        if DEBUG_ENABLED:
            LOG.debug(f"Loaded {len(entries)} entries from {json_path}")
        return entries
    except Exception as e:
        if DEBUG_ENABLED:
            LOG.error(f"Failed to load {json_path}: {e}")
        return []

def find_entry_by_digits(entries: list[dict], digit_candidates: list[str], allowed_usernames: set[str] | None = None) -> dict | None:
    for e in entries:
        if allowed_usernames:
            owner = sanitize_name(str(e.get("owner_screen_name") or e.get("owner_display_name") or e.get("CustomFolderName") or ""))
            if owner not in allowed_usernames:
                continue
        cf  = str(e.get("CustomFileName") or "")
        sid = str(e.get("status_id") or "")
        furl = str(e.get("full_url") or "")
        sid_from_url = status_id_from_url(furl)
        for digits in digit_candidates:
            if digits == sid or digits == sid_from_url:
                return e
            if digits in cf:
                return e
    return None

# ------------------------- Fallback -------------------------

def build_parsed_fallback(fragment: str, ctx: dict | None = None) -> dict:
    frag = Path(fragment).stem
    code_match = re.search(rf"\d{{{CODE_MIN_DIGITS},}}", frag)
    studio_code = code_match.group(0) if code_match else None
    code_span = (code_match.start(), code_match.end()) if code_match else None
    date_token = find_date_token_excluding_code(frag, code_span)
    date_str = normalize_date_token(date_token)
    out = {
        "title": normalize_title_from_filename(fragment),
        "details": normalize_title_from_filename(fragment),
        "urls": [],
        "tags": [{"name": "[XJSON-No Match]"}]
    }
    if studio_code:
        out["code"] = studio_code
    if date_str:
        out["date"] = date_str
    if ctx:
        if performers := ctx.get("performers"):
            out["performers"] = performers
        if studio := ctx.get("studio"):
            out["studio"] = studio
    return out

# ------------------------- Metadata -------------------------

def build_metadata_from_entry(entry: dict) -> dict:
    tweet_text = clean_tweet_text(entry.get("tweet_text") or "")
    urls = []
    if entry.get("full_url"):
        urls.append(entry["full_url"])
    if entry.get("media_urls"):
        urls.extend(u for u in str(entry["media_urls"]).split(",") if u.strip())
    if isinstance(entry.get("media_details"), list):
        urls.extend(m["url"] for m in entry["media_details"] if isinstance(m, dict) and m.get("url"))
    urls_unique = list(dict.fromkeys(urls))
    meta = {
        "title": tweet_text[:64],
        "details": tweet_text,
        "urls": urls_unique,
        "tags": [{"name": "[XJSON-Matched]"}]
    }
    if date := parse_date(str(entry.get("created_at") or "")):
        meta["date"] = date
    if code := entry.get("status_id"):
        meta["code"] = str(code)
    return meta

# ------------------------- Main -------------------------

def main():
    if len(sys.argv) < 2:
        print(json.dumps({}))
        return
    command = sys.argv[1]
    input_payload = read_stdin_payload()
    fragment = str(input_payload.get("fragment") or input_payload.get("title") or "")
    digit_candidates = extract_digit_candidates(fragment)

    # Allowlist of usernames from attached performers if present
    allowed = set()
    for p in (input_payload.get("performers") or []):
        if name := p.get("name"):
            allowed.add(sanitize_name(name))

    for jf in json_files():
        entry = find_entry_by_digits(load_json_entries(jf), digit_candidates, allowed if allowed else None)
        if entry:
            result = build_metadata_from_entry(entry)
            if DEBUG_ENABLED:
                LOG.debug(f"Match from {jf.name}: {result}")
            print(json.dumps(result))
            return

    fallback = build_parsed_fallback(fragment, {
        "performers": input_payload.get("performers"),
        "studio": input_payload.get("studio")
    })
    if DEBUG_ENABLED:
        LOG.debug(f"Fallback: {fallback}")
    print(json.dumps(fallback))

if __name__ == "__main__":
    main()
