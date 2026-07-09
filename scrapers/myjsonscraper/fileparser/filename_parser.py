#!/usr/bin/env python3
import sys, json, os, re
from datetime import datetime

# -------------------------
# CLI/env options helper
# -------------------------
def _strip_quotes(s: str) -> str:
    s = s.strip()
    if (len(s) >= 2) and ((s[0] == s[-1]) and s[0] in ("'", '"')):
        return s[1:-1]
    return s

def get_kv_arg(key: str, default=None):
    """Read foo=bar from argv (any position) or FOO env var. Strips wrapping quotes."""
    prefix = key + "="
    for a in sys.argv[1:]:
        if a.startswith(prefix):
            return _strip_quotes(a.split("=", 1)[1])
    v = os.environ.get(key.upper(), default)
    return _strip_quotes(v) if isinstance(v, str) else v

# Tunables
CODE_MIN_DIGITS = int(get_kv_arg("codeMinDigits", "12"))
STUDIO_SUFFIX   = get_kv_arg("studioSuffix", " (X)")
DEBUG           = str(get_kv_arg("debug", "0")).lower() in ("1", "true", "yes", "y")

DEFAULT_STASH_URL = os.environ.get("STASH_URL") or "http://localhost:9999/graphql"
STASH_API_KEY     = os.environ.get("STASH_API_KEY")  # optional

# Known media extensions (for safe stripping)
KNOWN_EXTS = {
    "jpg","jpeg","png","webp","gif","bmp","tif","tiff","heic","heif","avif",
    "mp4","mov","m4v","mkv","webm","avi","wmv","mpg","mpeg","ts","mts","flv"
}

# Platform prefixes to drop once at the start (any of . _ space -)
PLATFORM_PREFIXES = {
    "x", "𝕏", "of", "onlyfans", "twitter", "tw", "ig", "insta",
    "reddit", "r", "tiktok", "tt", "fansly", "fans", "tele", "telegram",
    "coomer"
}

# -------------------------
# IO helpers
# -------------------------
def read_stdin():
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
        if not raw:
            return {}
        return json.loads(raw)
    except Exception:
        return {}

def write(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()

def eprint(*args):
    print(*args, file=sys.stderr)

# -------------------------
# Mode / fragment helpers
# -------------------------
def get_mode_from_cli() -> str | None:
    for a in sys.argv[1:]:
        if a in ("imageByFragment", "sceneByFragment"):
            return a
    return None

def get_fragment_from_cli(mode: str | None) -> str:
    """
    Return the first non-option token AFTER the mode.
    Skips unsubstituted placeholders ({...}) and any key=value flags (debug=1, studioSuffix=" (X)", etc.).
    """
    if not mode:
        return ""
    argv = sys.argv[1:]
    if mode not in argv:
        return ""
    idx = argv.index(mode)
    for tok in argv[idx + 1:]:
        t = (tok or "").strip()
        if not t:
            continue
        if t.startswith("{"):                 # e.g. {query}
            continue
        if "=" in t:                          # key=value option
            continue
        if t in ("imageByFragment", "sceneByFragment"):
            continue
        return t
    return ""

def first_str(*candidates):
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return ""

def strip_ext_to_basename(p: str) -> str:
    """
    Return basename without a REAL extension.
    Keep numeric trailing segments like .201001131528 (that's a date, not an ext).
    """
    base = os.path.basename(p or "")
    if "." not in base:
        return base
    stem, ext = base.rsplit(".", 1)
    ext_l = ext.lower()
    # Only strip if it's a typical media extension OR purely alphabetic 2–5 chars
    if ext_l in KNOWN_EXTS or re.fullmatch(r"[a-z]{2,5}", ext_l):
        return stem
    return base

def extract_possible_ids(req: dict):
    """Try to extract a single image/scene id from stdin JSON."""
    inp = req.get("input") or {}
    candidates = [req.get("id"), inp.get("id")]
    # arrays
    for key in ("image_ids", "scene_ids", "ids", "selected", "file_ids"):
        v = req.get(key) or inp.get(key)
        if isinstance(v, list) and v:
            candidates.append(v[0])
    # typed objects
    for key in ("image", "scene"):
        obj = req.get(key) or inp.get(key)
        if isinstance(obj, dict) and obj.get("id"):
            candidates.append(obj["id"])
    # args
    args = req.get("args") or {}
    for key in ("id", "image_id", "scene_id"):
        if args.get(key):
            candidates.append(args.get(key))
    # normalize
    for c in candidates:
        s = str(c).strip()
        if s.isdigit():
            return int(s)
    return None

# -------------------------
# GraphQL helpers (image/scene fragment fallback)
# -------------------------
def gql(query: str, variables: dict | None = None, url: str | None = None) -> dict:
    url = url or DEFAULT_STASH_URL
    payload = json.dumps({"query": query, "variables": variables or {}}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if STASH_API_KEY:
        headers["ApiKey"] = STASH_API_KEY
    try:
        import requests  # type: ignore
        resp = requests.post(url, data=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as ex_req:
        try:
            import urllib.request
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as ex_url:
            eprint(f"filename_parser: GraphQL request failed: {ex_url or ex_req}")
            return {}

def fetch_image_fragment_by_id(image_id: int) -> str:
    # Uses visual_files -> ImageFile.basename (per your schema)
    q = """
    query($id: ID!){
      findImage(id: $id){
        id
        title
        visual_files { __typename ... on ImageFile { basename } }
        paths { image }
        galleries { folder { path } }
      }
    }
    """
    resp = gql(q, {"id": image_id})
    if not resp or resp.get("errors"):
        eprint(f"filename_parser: findImage errors: {resp.get('errors') if resp else 'no response'}")
        return ""
    data = (resp.get("data") or {}).get("findImage") or {}
    vf = data.get("visual_files") or []
    if isinstance(vf, list):
        for f in vf:
            if isinstance(f, dict) and f.get("__typename") == "ImageFile":
                b = f.get("basename")
                if b:
                    return strip_ext_to_basename(b)
    t = first_str(data.get("title"))
    if t:
        return strip_ext_to_basename(t)
    p = ((data.get("paths") or {}).get("image") or "")
    if p:
        return strip_ext_to_basename(p)
    return ""

def fetch_scene_fragment_by_id(scene_id: int) -> str:
    q = """
    query($id: ID!){
      findScene(id: $id){
        id
        title
        files { __typename ... on VideoFile { basename } }
        paths { screenshot }
        url
      }
    }
    """
    resp = gql(q, {"id": scene_id})
    if not resp or resp.get("errors"):
        eprint(f"filename_parser: findScene errors: {resp.get('errors') if resp else 'no response'}")
        return ""
    data = (resp.get("data") or {}).get("findScene") or {}
    files = data.get("files") or []
    if isinstance(files, list):
        for f in files:
            if isinstance(f, dict) and f.get("__typename") == "VideoFile":
                b = f.get("basename")
                if b:
                    return strip_ext_to_basename(b)
    t = first_str(data.get("title"), data.get("url"), (data.get("paths") or {}).get("screenshot"))
    if t:
        return strip_ext_to_basename(t)
    return ""

def get_fragment(req: dict, mode: str | None) -> str:
    """
    1) CLI: token after the mode (skips key=value options)
    2) JSON stdin: various fields depending on Stash version
    3) GraphQL by selected id
    4) Normalize to basename (smart ext stripping)
    """
    frag = get_fragment_from_cli(mode)
    if not frag:
        inp = req.get("input") or {}
        frag = first_str(
            req.get("query"),
            req.get("fragment"),
            (req.get("args") or {}).get("query"),
            (req.get("args") or {}).get("fragment"),
            inp.get("query"),
            inp.get("fragment"),
            inp.get("name"),
            inp.get("title"),
            inp.get("filename"),
            inp.get("basename"),
            inp.get("file_path"),
            inp.get("path"),
        )
    if not frag:
        target_id = extract_possible_ids(req)
        if target_id is not None:
            frag = fetch_image_fragment_by_id(target_id) if mode == "imageByFragment" else fetch_scene_fragment_by_id(target_id)
    return strip_ext_to_basename(frag) if frag else ""

# -------------------------
# Date parsing
# -------------------------
def normalize_date(token: str | None) -> str:
    """
    Return YYYY-MM-DD from common tail formats:
      - 12: prefer YYYYMMDDhhmm (if 2000<=YYYY<=2099 and valid MD), else YYMMDDhhmmss
      - 11: if endswith '0' -> treat as 10-digit YYMMDDhhmm; else YYMMDDhhmm[s]
      - 10: YYMMDDhhmm
      - 8 : YYYYMMDD
    Any invalid combo -> ''.
    """
    if not token or not token.isdigit():
        return ""

    def mk(y, m, d) -> str:
        try:
            return datetime(y, m, d).strftime("%Y-%m-%d")
        except Exception:
            return ""

    L = len(token)

    if L == 8:
        # YYYYMMDD
        y = int(token[0:4]); m = int(token[4:6]); d = int(token[6:8])
        return mk(y, m, d)

    if L == 10:
        # YYMMDDhhmm -> date = YYMMDD
        yy = int(token[0:2]); m = int(token[2:4]); d = int(token[4:6])
        return mk(2000 + yy, m, d)

    if L == 11:
        if token.endswith("0"):
            t = token[:-1]  # -> 10-digit
            yy = int(t[0:2]); m = int(t[2:4]); d = int(t[4:6])
            return mk(2000 + yy, m, d)
        # YYMMDDhhmm[s] -> use YYMMDD
        yy = int(token[0:2]); m = int(token[2:4]); d = int(token[4:6])
        return mk(2000 + yy, m, d)

    if L == 12:
        # Try YYYY first
        y4 = int(token[0:4]); m4 = int(token[4:6]); d4 = int(token[6:8])
        if 2000 <= y4 <= 2099:
            v = mk(y4, m4, d4)
            if v:
                return v
        # Fallback: YYMMDD...
        yy = int(token[0:2]); m = int(token[2:4]); d = int(token[4:6])
        return mk(2000 + yy, m, d)

    return ""

def find_date_token_excluding_code(frag: str, code_span: tuple[int,int] | None) -> str:
    """
    Collect all digit runs with positions.
    Prefer rightmost 12, then 11, then 10, then 8-digit runs.
    When choosing, prefer runs that start AFTER the studio-code span.
    """
    runs = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\d+", frag)]
    def pick_len(L: int) -> str:
        subset = [r for r in runs if len(r[0]) == L]
        if not subset:
            return ""
        # Prefer to the right of the code (if we know where code ends)
        if code_span:
            right = [r for r in subset if r[1] >= code_span[1]]
            cand = right or subset
        else:
            cand = subset
        # Rightmost by start index
        return max(cand, key=lambda r: r[1])[0]
    for L in (12, 11, 10, 8):
        tok = pick_len(L)
        if tok:
            return tok
    return ""

# -------------------------
# Username cleanup
# -------------------------
def strip_platform_prefix(s: str) -> str:
    """Remove one leading platform tag like X/𝕏/of/onlyfans/etc. with . _ space or - after it."""
    if not s:
        return s
    m = re.match(rf"(?i)^(?:{'|'.join(map(re.escape, PLATFORM_PREFIXES))})[\s._-]+", s)
    return s[m.end():] if m else s

def clean_username(username_raw: str) -> str:
    """
    Clean the substring before the code into a final username.
    - Trim boundary separators (space, dot, underscore, dash)
    - Drop ONE leading platform prefix regardless of separator
    - If dots remain, take the LAST meaningful dot-segment
    - Preserve internal underscores (e.g., 'monty___burns')
    """
    if not username_raw:
        return ""
    u = username_raw.strip()
    u = re.sub(r"^[\s._-]+", "", u)
    u = re.sub(r"[\s._-]+$", "", u)
    u = strip_platform_prefix(u)
    if "." in u:
        segments = [s for s in u.split(".") if s]
        for seg in reversed(segments):
            if re.search(r"\w", seg):
                u = seg
                break
    u = u.strip().strip("._-")
    return u

# -------------------------
# Parse + build
# -------------------------
def parse_from_fragment(fragment: str):
    frag = fragment.strip()

    # Studio code: first >= CODE_MIN_DIGITS run (record its span)
    code_match = re.search(rf"\d{{{CODE_MIN_DIGITS},}}", frag)
    studio_code = code_match.group(0) if code_match else None
    code_span = (code_match.start(), code_match.end()) if code_match else None

    # Username is everything before that code, cleaned
    # username = clean_username(frag[:code_match.start()]) if code_match else clean_username(frag)
    username = ""

    # Date token (robust finder) – prefer runs to the RIGHT of the code
    date_token = find_date_token_excluding_code(frag, code_span)
    date_str = normalize_date(date_token)

    if DEBUG:
        eprint(f"[filename_parser] fragment='{frag}' user='{username}' code='{studio_code or ''}' "
               f"raw_date='{date_token or ''}' date='{date_str or '<none>'}'")

    return {
        "username": username,
        "studio_code": studio_code,
        "date_string": date_str,  # '' if none/invalid
    }

def build_flat_payload(parsed: dict, original_fragment: str) -> dict:
    title = original_fragment
    code = parsed.get("studio_code") or ""
    date_str = parsed.get("date_string") or ""
    username = parsed.get("username") or ""
    studio_name = f"{username}{STUDIO_SUFFIX}" if username else ""

    out = {
        "title": title,
        "details": title,   # mirror title into details/description
        "code": code,
    }
    if date_str:
        out["date"] = date_str
    if username:
        out["performers"] = [{"name": username}]
    if studio_name:
        out["studio"] = {"name": studio_name}
    return out

# -------------------------
# Main
# -------------------------
def main():
    req = read_stdin()
    mode = get_mode_from_cli() or (req.get("args") or {}).get("mode") or req.get("mode") or ""
    fragment = get_fragment(req, mode)

    if not fragment:
        eprint("filename_parser: no fragment found even after GraphQL lookup; no updates.")
        return write({})  # empty = no changes

    parsed = parse_from_fragment(fragment)
    item = build_flat_payload(parsed, fragment)
    return write(item)

if __name__ == "__main__":
    main()
