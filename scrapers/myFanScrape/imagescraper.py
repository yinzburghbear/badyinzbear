# imagescraper.py
import json
import os
import sys
from pathlib import Path

import requests
from stashapi import log
from mfs import (
    load_db_into_memory,
    process_row,
    get_studio_info,
    getnamefromalias,
    searchPerformers,
)

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


CONFIG = load_config()
STASH_CONNECTION = CONFIG["stash_connection"]


def query_stash_for_image_info(image_id):
    url = (
        f"{STASH_CONNECTION['scheme']}://"
        f"{STASH_CONNECTION['host']}:{STASH_CONNECTION['port']}/graphql"
    )
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if STASH_CONNECTION.get("apikey"):
        headers["ApiKey"] = STASH_CONNECTION["apikey"]

    query = {
        "query": """
        query ($id: ID!) {
          findImage(id: $id) {
            id
            title
            visual_files {
              ... on ImageFile {
                basename
              }
            }
            paths {
              image
            }
            galleries {
              folder {
                path
              }
            }
            studio {
              name
            }
          }
        }
        """,
        "variables": {"id": image_id},
    }

    response = requests.post(url, json=query, headers=headers, timeout=30)
    response.raise_for_status()

    payload = response.json()
    if payload.get("errors"):
        log.error(
            f"GraphQL errors for image {image_id}: "
            f"{json.dumps(payload['errors'], indent=2)}"
        )

    image_info = payload.get("data", {}).get("findImage")
    log.debug(
        f"GraphQL image info for {image_id}: "
        f"{json.dumps(image_info, indent=2) if image_info else 'null'}"
    )
    return image_info


def find_image_file(folder_path: str, fragment_base: str):
    folder = Path(folder_path)

    for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        image_path = folder / f"{fragment_base}{ext}"
        if image_path.exists():
            return image_path

    for file in folder.glob("*.*"):
        if fragment_base in file.stem.lower():
            return file

    return None


def build_scrape_payload(result: dict, studio_info: dict, usernames: list[str]):
    scrape = {
        "title": result.get("title", ""),
        "details": result.get("details", ""),
        "date": result.get("date", ""),
        # ScrapeParser expects url objects, not raw strings
        "urls": [u for u in (result.get("urls") or []) if u],
        "studio": studio_info or {},
        "performers": [],
    }

    for name in sorted(set(usernames)):
        clean_name = (name or "").strip().strip(".")
        if not clean_name:
            continue
        resolved_name = (getnamefromalias(clean_name) or "").strip()
        if resolved_name:
            scrape["performers"].append({"name": resolved_name})

    return scrape


def lookup_image_fragment(image_id: int):
    """
    Query Stash for the image info and use that to locate the correct user_data.db.
    Always return a dict so Bulk Image Scrape does not choke on None/EOF.
    """
    image_info = query_stash_for_image_info(image_id)
    if not image_info:
        log.error(f"No image returned from Stash for image_id={image_id}")
        return {}

    studio_name = (image_info.get("studio") or {}).get("name", "")
    if not studio_name:
        log.error(f"Missing studio for image_id={image_id}")
        return {}

    if not studio_name.endswith("(OnlyFans)"):
        log.error(
            f"Unsupported network or malformed studio for image_id={image_id}: "
            f"{studio_name}"
        )
        return {}

    username = studio_name.replace(" (OnlyFans)", "")
    network = "OnlyFans"
    log.debug(
        f"Extracted studio_name={studio_name}, username={username}, network={network}"
    )

    visual_files = image_info.get("visual_files") or []
    fragment_base = ""
    if visual_files and isinstance(visual_files[0], dict):
        fragment_base = os.path.splitext(
            visual_files[0].get("basename", "")
        )[0].lower()

    if not fragment_base:
        log.error(f"Missing usable fragment filename from Stash for image_id={image_id}")
        return {}

    galleries = image_info.get("galleries") or []
    folder_path = ""
    if galleries and isinstance(galleries[0], dict):
        folder_path = ((galleries[0].get("folder") or {}).get("path", "")) or ""

    if not folder_path:
        image_path_from_stash = ((image_info.get("paths") or {}).get("image")) or ""
        if image_path_from_stash:
            folder_path = str(Path(image_path_from_stash).parent)

    if not folder_path:
        log.error(f"Missing folder path from Stash image data for image_id={image_id}")
        return {}

    image_path = find_image_file(folder_path, fragment_base)
    log.debug(f"Resolved image_path for image_id={image_id}: {image_path}")
    if not image_path:
        log.error(
            f"Image file not found for image_id={image_id}, "
            f"fragment={fragment_base}, folder={folder_path}"
        )
        return {}

    db_path = Path(f"O:/Metadata/{network}/{username}/user_data.db")
    log.debug(f"Expected DB path for image_id={image_id}: {db_path}")
    if not db_path.exists():
        log.error(f"No database found at expected path for image_id={image_id}: {db_path}")
        return {}

    conn = load_db_into_memory(db_path)
    c = conn.cursor()

    try:
        c.execute(
            """
            SELECT filename, post_id
            FROM medias
            WHERE LOWER(
                REPLACE(
                    REPLACE(
                        REPLACE(
                            REPLACE(
                                REPLACE(filename, '.jpg', ''),
                            '.jpeg', ''),
                        '.png', ''),
                    '.gif', ''),
                '.webp', '')
            ) = ?
            AND media_type = 'Images'
            LIMIT 1;
            """,
            (fragment_base,),
        )
        row = c.fetchone()
        log.debug(f"Filename lookup row for image_id={image_id}: {row}")

        if not row:
            log.error(f"No match found in medias for image_id={image_id}, fragment={fragment_base}")
            return {}

        filename, post_id = row

        c.execute(
            """
            SELECT medias.post_id,
                   COALESCE(posts.text, stories.text, messages.text, products.text, others.text, "") as text,
                   COALESCE(posts.created_at, stories.created_at, messages.created_at, products.created_at, others.created_at) as created_at,
                   medias.link,
                   medias.linked
            FROM medias
            LEFT JOIN posts ON medias.post_id = posts.post_id
            LEFT JOIN stories ON medias.post_id = stories.post_id
            LEFT JOIN messages ON medias.post_id = messages.post_id
            LEFT JOIN products ON medias.post_id = products.post_id
            LEFT JOIN others ON medias.post_id = others.post_id
            WHERE medias.post_id = ?
            LIMIT 1
            """,
            (post_id,),
        )
        meta_row = c.fetchone()
        log.debug(f"Metadata row for image_id={image_id}: {meta_row}")

        if not meta_row:
            log.error(f"Post metadata not found for image_id={image_id}, post_id={post_id}")
            return {}

        # process_row mutates row[1], so convert tuple to list first
        meta_row = list(meta_row)

        result = process_row(meta_row, username, network, filename) or {}
        log.debug(
            f"process_row result for image_id={image_id}: "
            f"{json.dumps(result, indent=2) if result else 'null'}"
        )
        if not result:
            log.error(f"process_row returned no data for image_id={image_id}")
            return {}

        studio_info = get_studio_info(username, network) or {}
        log.debug(
            f"studio_info for image_id={image_id}: "
            f"{json.dumps(studio_info, indent=2) if studio_info else 'null'}"
        )
        if not studio_info:
            studio_info = {
                "name": f"{username} ({network})",
                "parent": {"name": f"{network} (network)"},
            }

        usernames = searchPerformers(
            {
                "title": result.get("title", ""),
                "details": result.get("details", ""),
            }
        ) or []
        log.debug(f"searchPerformers returned for image_id={image_id}: {usernames}")

        usernames.append(username)

        scrape = build_scrape_payload(result, studio_info, usernames)
        log.debug(
            f"Final scrape object for image_id={image_id}: "
            f"{json.dumps(scrape, indent=2)}"
        )
        return scrape

    except Exception as e:
        log.error(f"Error processing image_id={image_id} using {db_path}: {e}", exc_info=True)
        return {}
    finally:
        conn.close()


def main():
    """
    Read JSON from stdin and always print valid JSON to stdout.
    """
    try:
        raw = sys.stdin.read().strip()
        log.debug(f"Raw stdin: {raw}")

        fragment = json.loads(raw) if raw else {}
        image_id = fragment.get("id")

        if not image_id:
            log.error("Missing image ID in fragment.")
            print(json.dumps({}))
            return

        media = lookup_image_fragment(int(image_id)) or {}
        print(json.dumps(media))

    except Exception as e:
        log.error(f"Unhandled exception in imagescraper.py: {e}", exc_info=True)
        print(json.dumps({}))


if __name__ == "__main__":
    main()
