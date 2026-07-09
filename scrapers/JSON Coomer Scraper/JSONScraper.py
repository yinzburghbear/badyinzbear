import sys
import json
from pathlib import Path
import os
import requests
import re
import html
import hashlib

# -------------------------
# Config & logging
# -------------------------
try:
    import config  # type: ignore
    stashconfig = config.stashconfig if hasattr(config, "stashconfig") else {}
except ImportError:
    stashconfig = {}

try:
    import stashapi.log as log
except ImportError:
    class log:
        @staticmethod
        def debug(msg):
            print(msg, file=sys.stderr)

SCRIPT_DIR = Path(__file__).resolve().parent
JSON_DIR = SCRIPT_DIR / "OFJSON"

STASH_URL = stashconfig.get("STASH_URL", "http://localhost:9999/graphql")

success_tag = "[JSON: Match]"
failure_tag = "[JSON: No Match]"

KEYWORD_TAGS = {
    "JOI": "Jerk Off Instruction",
    "CEI": "Cum Eating Instruction",
    "Livestream": "Stream started at",
}

# -------------------------
# Utilities
# -------------------------
def json_path_exists(username: str) -> bool:
    return (JSON_DIR / f"{username}.json").exists()

def _gql(query: str, variables: dict):
    try:
        resp = requests.post(STASH_URL, json={"query": query, "variables": variables})
        if resp.status_code != 200:
            log.debug(f"GraphQL HTTP error: {resp.status_code} - {resp.text}")
            return None
        data = resp.json()
        if "errors" in data and data["errors"]:
            log.debug(f"GraphQL errors: {data['errors']}")
            return None
        return data.get("data")
    except Exception as e:
        log.debug(f"GraphQL request failed: {e}")
        return None

# -------------------------
# Performer/fragment helpers
# -------------------------
def fetch_username_from_performer_id(performer_id):
    data = _gql(
        """
        query($id: ID!) {
            findPerformer(id: $id) {
                name
                aliases
            }
        }
        """,
        {"id": performer_id},
    )
    if not data:
        return None
    performer = data.get("findPerformer")
    if performer:
        name = performer.get("name")
        aliases = performer.get("aliases", [])
        if name and json_path_exists(name):
            return name
        for alias in aliases:
            if json_path_exists(alias):
                return alias
    return None

def fetch_fragment_metadata_by_image_id(item_id, is_scene=False):
    """
    Try a couple of schema shapes to avoid validation errors.
    We specifically avoid querying paths { files } which caused 422s before.
    """
    queries = []
    if is_scene:
        # Prefer scenes.files { path } if available
        queries.append(
            """
            query($id: ID!) {
              findScene(id: $id) {
                id
                performers { name alias_list id }
                studio { name }
                galleries { folder { path } }
                files { path }
              }
            }
            """
        )
        # Minimal fallback
        queries.append(
            """
            query($id: ID!) {
              findScene(id: $id) {
                id
                performers { name alias_list id }
                studio { name }
                galleries { folder { path } }
              }
            }
            """
        )
    else:
        # Images sometimes expose files { path }
        queries.append(
            """
            query($id: ID!) {
              findImage(id: $id) {
                id
                performers { name alias_list id }
                studio { name }
                galleries { folder { path } }
                files { path }
              }
            }
            """
        )

    for q in queries:
        data = _gql(q, {"id": item_id})
        if data:
            return data.get("findScene" if is_scene else "findImage")
    return None

def extract_username_from_gallery_path(enriched):
    try:
        galleries = enriched.get("galleries", [])
        if not galleries:
            return None
        for gallery in galleries:
            folder = gallery.get("folder", {})
            path = folder.get("path", "")
            log.debug(f"Checking gallery folder path: {path}")

            # e.g. O:\Onlyfans\maxscott2022\maxscott2022 - Images
            m = re.search(r"Onlyfans[\\/]+([^\\/]+)", path, re.IGNORECASE)
            if m:
                username = m.group(1)
                log.debug(f"Extracted username from folder path: {username}")
                if json_path_exists(username):
                    return username
    except Exception as e:
        log.debug(f"Failed to extract from gallery path: {e}")
    return None
# def extract_username_from_gallery_path(enriched):
#     try:
#         galleries = enriched.get("galleries", [])
#         if not galleries:
#             return None
#         for gallery in galleries:
#             folder = gallery.get("folder", {})
#             path = folder.get("path", "")
#             log.debug(f"Checking gallery folder path: {path}")
#             # e.g., ...\Onlyfans\<username>\...
#             m = re.search(r"Onlyfans\([^\]+)", path, re.IGNORECASE)
#             if m:
#                 username = m.group(1)
#                 log.debug(f"Extracted username from folder path: {username}")
#                 if json_path_exists(username):
#                     return username
#     except Exception as e:
#         log.debug(f"Failed to extract from gallery path: {e}")
#     return None

def extract_username_from_studio(enriched):
    try:
        studio = enriched.get("studio")
        if studio and isinstance(studio, dict):
            name = studio.get("name", "")
            if name:
                cleaned = re.sub(r"\s*\(OnlyFans\)$", "", name).strip()
                log.debug(f"Extracted studio name: {name}, cleaned: {cleaned}")
                if json_path_exists(cleaned):
                    return cleaned
    except Exception as e:
        log.debug(f"Failed to extract from studio name: {e}")
    return None

def get_username_from_fragment(fragment, is_scene):
    # Check URLs first
    urls = list(fragment.get("urls") or [])
    if fragment.get("url"):
        urls.append(fragment["url"])
    for u in urls:
        if not u:
            continue
        m = re.search(r"/user/([^/]+)/post/", u)
        if m and json_path_exists(m.group(1)):
            return m.group(1)
        m2 = re.search(r"onlyfans\.com/([0-9]+)/([^/?#]+)", u, re.IGNORECASE)
        if m2 and json_path_exists(m2.group(2)):
            return m2.group(2)

    # Tags (performers/studios/tags with alias fallbacks)
    for tag_group in ("performers", "studios", "tags"):
        tags = fragment.get(tag_group)
        if tags and isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, dict):
                    name = tag.get("name")
                    if name and json_path_exists(name):
                        return name
                    for alias in tag.get("aliases", []) + tag.get("alias_list", []):
                        if json_path_exists(alias):
                            return alias

    # Performer IDs via GraphQL
    performer_ids = fragment.get("performer_ids")
    log.debug(f"Performer IDs in fragment: {performer_ids}")
    if performer_ids and isinstance(performer_ids, list):
        for pid in performer_ids:
            log.debug(f"Attempting to resolve performer ID: {pid}")
            resolved = fetch_username_from_performer_id(pid)
            if resolved:
                return resolved

    # Enriched fallback
    if "id" in fragment:
        log.debug(f"Attempting GraphQL metadata fetch for fragment ID: {fragment['id']} (is_scene={is_scene})")
        enriched = fetch_fragment_metadata_by_image_id(fragment["id"], is_scene=is_scene)
        if enriched:
            performers = enriched.get("performers", [])
            for performer in performers:
                name = performer.get("name")
                if name and json_path_exists(name):
                    return name
                for alias in performer.get("alias_list", []):
                    if json_path_exists(alias):
                        return alias
            fallback = extract_username_from_gallery_path(enriched)
            if fallback:
                return fallback
            studio_fallback = extract_username_from_studio(enriched)
            if studio_fallback:
                return studio_fallback

    return None

def load_posts(fragment, is_scene):
    username = get_username_from_fragment(fragment, is_scene)
    if not username:
        log.debug("No valid username or alias found in fragment — stopping.")
        return []
    path = JSON_DIR / f"{username}.json"
    log.debug(f"Found username in fragment: {username}")
    log.debug(f"Attempting to load JSON from: {path}")
    if not path.exists():
        log.debug(f"JSON file not found for {username}: {path}")
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.debug(f"Error reading file {path}: {e}")
        return []

# -------------------------
# Text & tag utilities
# -------------------------
def clean_text(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()

def detect_resolution_tags(post):
    tags = []
    width = post.get("file", {}).get("width")
    height = post.get("file", {}).get("height")
    if not width or not height:
        return tags
    try:
        width = int(width)
        height = int(height)
        if width >= 3840:
            tags.append({"Name": "4K"})
        elif width >= 2560:
            tags.append({"Name": "1440p"})
        elif width >= 1920:
            tags.append({"Name": "1080p"})
        if width > height:
            tags.append({"Name": "Landscape"})
        elif height > width:
            tags.append({"Name": "Portrait"})
        ratio = round(width / height, 2)
        if 1.75 <= ratio <= 1.79:
            tags.append({"Name": "16:9"})
        elif 1.3 <= ratio <= 1.35:
            tags.append({"Name": "4:3"})
    except Exception as e:
        log.debug(f"Error determining resolution tags: {e}")
    return tags

def keyword_tags(post):
    tags = []
    title = post.get("title", "")
    for pattern, tag in KEYWORD_TAGS.items():
        try:
            if re.search(pattern, title, flags=re.IGNORECASE):
                tags.append({"Name": tag})
        except re.error:
            # If pattern is not a valid regex, fall back to simple containment
            if pattern.lower() in title.lower():
                tags.append({"Name": tag})
    return tags

def build_tags(post, matched: bool):
    tags = detect_resolution_tags(post) + keyword_tags(post)
    tags.append({"Name": success_tag if matched else failure_tag})
    return tags

# -------------------------
# SHA256 helpers
# -------------------------
def _compute_sha256_local(file_path: str) -> str | None:
    if not file_path:
        return None
    try:
        hasher = hashlib.sha256()
        with open(file_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        log.debug(f"Local SHA256 compute failed for {file_path}: {e}")
        return None

def _paths_from_enriched(enriched) -> list[str]:
    paths = []
    if not enriched:
        return paths
    # scene.files[].path
    for f in (enriched.get("files") or []):
        p = f.get("path")
        if p:
            paths.append(p)
    # image.file.path
    f = enriched.get("file") or {}
    p = f.get("path")
    if p:
        paths.append(p)
    return paths

def get_sha256_for_fragment(fragment, is_scene: bool) -> str | None:
    """
    Order:
      1) fragment.files[].path
      2) GraphQL -> files/file path
      3) fragment['path']
    """
    frag_files = fragment.get("files")
    if isinstance(frag_files, list):
        for f in frag_files:
            p = (f or {}).get("path")
            if p and os.path.exists(p):
                h = _compute_sha256_local(p)
                if h:
                    log.debug(f"Got SHA256 via fragment.files path: {h}")
                    return h

    frag_id = fragment.get("id")
    if frag_id:
        enriched = fetch_fragment_metadata_by_image_id(frag_id, is_scene=is_scene)
        for p in _paths_from_enriched(enriched):
            if os.path.exists(p):
                h = _compute_sha256_local(p)
                if h:
                    log.debug(f"Got SHA256 via GraphQL-assisted path: {h}")
                    return h

    p = fragment.get("path")
    if p and os.path.exists(p):
        h = _compute_sha256_local(p)
        if h:
            log.debug(f"Got SHA256 via fragment.path: {h}")
            return h

    log.debug("Unable to determine SHA256 for fragment")
    return None

# -------------------------
# Matching helpers
# -------------------------
def find_post_by_hash(posts, sha256_hex: str | None):
    if not sha256_hex:
        return None
    needle = sha256_hex.lower()
    for post in posts:
        # main file
        f = post.get("file") or {}
        path = (f.get("path") or "").lower()
        if needle and needle in path:
            return post
        # attachments
        for att in (post.get("attachments") or []):
            apath = (att.get("path") or "").lower()
            if needle and needle in apath:
                return post
    return None

def find_post_by_filename(posts, filename):
    normalized = filename.lower().replace(".jpeg", ".jpg")
    for post in posts:
        if "file" in post:
            f = post["file"]
            name = f.get("name", "").lower().replace(".jpeg", ".jpg")
            path = f.get("path", "").lower().replace(".jpeg", ".jpg")
            if path.endswith(normalized) or name == normalized:
                return post
        for att in (post.get("attachments") or []):
            name = att.get("name", "").lower().replace(".jpeg", ".jpg")
            path = att.get("path", "").lower().replace(".jpeg", ".jpg")
            if path.endswith(normalized) or name == normalized:
                return post
    return None

# -------------------------
# Mappers
# -------------------------
def map_scene(post):
    title = clean_text(post.get("title", "")) or f"{post.get('user', '')} - {post.get('published', '')[:10]}"
    cleaneddetails = clean_text(post.get("content", "")) or f"{post.get('user', '')} - {post.get('published', '')[:10]} - https://coomer.st/{post['service']}/user/{post['user']}/post/{post['id']}"
    return {
        "title": title,
        "date": post.get("published", "")[:10] if post.get("published") else "",
        "details": cleaneddetails,
        "studio": {"Name": post.get("user", "")},
        "performers": [{"Name": post.get("user", "")}],
        "tags": build_tags(post, matched=True),
        "code": "",
        "director": "",
        "movies": [],
        "URLs": [
            f"https://onlyfans.com/{post['id']}/{post['user']}",
            f"https://coomer.st/{post['service']}/user/{post['user']}/post/{post['id']}",
        ],
    }

def map_image(post, image_id):
    title = clean_text(post.get("title", "")) or f"{post.get('user', '')} - {post.get('published', '')[:10]}"
    cleaneddetails = clean_text(post.get("content", "")) or f"{post.get('user', '')} - {post.get('published', '')[:10]} - https://coomer.st/{post['service']}/user/{post['user']}/post/{post['id']}"
    return {
        "Title": title,
        "Date": post.get("published", "")[:10] if post.get("published") else "",
        "Details": cleaneddetails,
        "Studio": {"Name": post.get("user", "")},
        "Code": str(image_id or ""),
        "Performers": [{"Name": post.get("user", "")}],
        "Tags": build_tags(post, matched=True),
        "URLs": [
            f"https://onlyfans.com/{post['id']}/{post['user']}",
            f"https://coomer.st/{post['service']}/user/{post['user']}/post/{post['id']}",
        ],
    }

# -------------------------
# Main
# -------------------------
def handle_fragment(fragment, is_scene=True):
    # A) Global HASH-FIRST across all JSONs
    sha256_hex = get_sha256_for_fragment(fragment, is_scene=is_scene)
    if sha256_hex:
        for json_file in JSON_DIR.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    posts = json.load(f)
                post = find_post_by_hash(posts, sha256_hex)
                if post:
                    log.debug(f"Hash match found in {json_file.name}")
                    if is_scene or str(fragment.get("path", "")).lower().endswith(".mp4"):
                        print(json.dumps(map_scene(post)))
                    else:
                        print(json.dumps(map_image(post, fragment.get("id"))))
                    return
            except Exception as e:
                log.debug(f"Error reading file {json_file}: {e}")

    # B) Fallback: username-based JSON, then filename
    posts = load_posts(fragment, is_scene)
    if not posts:
        log.debug("No posts available after username resolution")
        if is_scene or str(fragment.get("path", "")).lower().endswith(".mp4"):
            print(json.dumps({"tags": [{"Name": failure_tag}]}))
        else:
            print(json.dumps({"Tags": [{"Name": failure_tag}]}))
        return

    # Try filename match
    filename = os.path.basename(fragment.get("path") or fragment.get("title") or "").lower().replace(".jpeg", ".jpg")
    post = find_post_by_filename(posts, filename) if filename else None

    if not post:
        log.debug("No match found (hash and filename)")
        if is_scene or str(fragment.get("path", "")).lower().endswith(".mp4"):
            print(json.dumps({"tags": [{"Name": failure_tag}]}))
        else:
            print(json.dumps({"Tags": [{"Name": failure_tag}]}))
        return

    if is_scene or str(fragment.get("path", "")).lower().endswith(".mp4"):
        print(json.dumps(map_scene(post)))
    else:
        print(json.dumps(map_image(post, fragment.get("id"))))

def log_fragment_summary(fragment):
    summary = [
        f"Fragment keys: {list(fragment.keys())}",
        f"Performers: {fragment.get('performers')}",
        f"Studios: {fragment.get('studios')}",
        f"Tags: {fragment.get('tags')}",
    ]
    log.debug("---- Fragment Metadata Debug ----\n" + "\n".join(summary))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        log.debug("No action provided.")
        sys.exit(1)
    mode = sys.argv[1]
    data = json.loads(sys.stdin.read())
    log_fragment_summary(data)
    if mode == "sceneByFragment":
        fragment = data["files"][0]
        fragment["id"] = data.get("id")
        handle_fragment(fragment, is_scene=True)
    elif mode == "imageByFragment":
        handle_fragment(data, is_scene=False)
    else:
        log.debug(f"Unknown mode: {mode}")
        sys.exit(1)
