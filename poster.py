"""
LinkedIn Auto-Poster — SQL Server content.

Runs once per invocation (intended to be triggered daily by Windows Task
Scheduler). Each run:
  1. Picks the next SQL Server sub-topic from config/topics.json, avoiding
     any topic posted within the last MIN_DAYS_BEFORE_REPEAT days (tracked
     in history.json).
  2. Generates an original LinkedIn post about that topic via the OpenAI
     Chat Completions API.
  3. Publishes the post to the user's LinkedIn profile via the LinkedIn
     UGC Posts API (unless --dry-run is passed).
  4. Logs the outcome and updates history.json.

Usage:
    python poster.py              # generate and publish live
    python poster.py --dry-run    # generate and log only, no posting
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
TOPICS_FILE = BASE_DIR / "config" / "topics.json"
HISTORY_FILE = BASE_DIR / "history.json"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "poster.log"

LINKEDIN_UGC_POSTS_URL = "https://api.linkedin.com/v2/ugcPosts"
LINKEDIN_API_VERSION_HEADERS = {
    "X-Restli-Protocol-Version": "2.0.0",
    "Content-Type": "application/json",
}

MAX_POST_CHARS = 3000  # LinkedIn's character limit for shares


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("linkedin_poster")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        # Also echo to console so Task Scheduler history / manual runs show output.
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        logger.addHandler(console)
    return logger


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def pick_topic(logger: logging.Logger) -> str:
    topics_data = load_json(TOPICS_FILE, {"topics": []})
    topics: list[str] = topics_data.get("topics", [])
    if not topics:
        raise RuntimeError(f"No topics found in {TOPICS_FILE}")

    history = load_json(HISTORY_FILE, {"posts": []})
    min_days = int(os.environ.get("MIN_DAYS_BEFORE_REPEAT", "14"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=min_days)

    recently_used = set()
    for entry in history.get("posts", []):
        try:
            posted_at = datetime.fromisoformat(entry["posted_at"])
        except (KeyError, ValueError):
            continue
        if posted_at >= cutoff:
            recently_used.add(entry.get("topic"))

    eligible = [t for t in topics if t not in recently_used]
    if not eligible:
        # Every topic was used recently; fall back to least-recently-used.
        logger.warning(
            "All topics used within %s days; falling back to least-recently-used topic.",
            min_days,
        )
        last_used_at = {}
        for entry in history.get("posts", []):
            t = entry.get("topic")
            if t:
                last_used_at[t] = entry.get("posted_at", "")
        eligible = sorted(topics, key=lambda t: last_used_at.get(t, ""))
        return eligible[0]

    # Among eligible topics, prefer the one used longest ago (or never used).
    last_used_at = {}
    for entry in history.get("posts", []):
        t = entry.get("topic")
        if t:
            last_used_at[t] = entry.get("posted_at", "")
    eligible.sort(key=lambda t: last_used_at.get(t, ""))
    return eligible[0]


def generate_post(topic: str, logger: logging.Logger) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    system_prompt = (
        "You write original, engaging LinkedIn posts for a senior SQL Server / "
        "database engineer's professional audience. Style rules:\n"
        "- Plain text only (LinkedIn does not render markdown). No asterisks, "
        "no headers, no code fences.\n"
        "- Start with a short, attention-grabbing hook line (1 sentence).\n"
        "- Body: 3-6 short paragraphs or line items with genuinely useful, "
        "specific technical insight — not generic fluff.\n"
        "- End with a brief takeaway or question to invite engagement.\n"
        "- Add 3-5 relevant hashtags on the final line (e.g. #SQLServer "
        "#DatabasePerformance #DataEngineering).\n"
        f"- Keep the whole post under {MAX_POST_CHARS} characters.\n"
        "- Do not use emojis excessively; at most 1-2 if natural."
    )
    user_prompt = f"Write today's LinkedIn post about this SQL Server topic: {topic}"

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=700,
    )
    content = response.choices[0].message.content.strip()
    if len(content) > MAX_POST_CHARS:
        logger.warning(
            "Generated post exceeded %s chars (%s); truncating.",
            MAX_POST_CHARS,
            len(content),
        )
        content = content[: MAX_POST_CHARS - 1].rstrip() + "…"
    return content


def post_to_linkedin(text: str, logger: logging.Logger) -> dict:
    import requests

    access_token = os.environ["LINKEDIN_ACCESS_TOKEN"]
    person_urn = os.environ["LINKEDIN_PERSON_URN"]

    payload = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    headers = dict(LINKEDIN_API_VERSION_HEADERS)
    headers["Authorization"] = f"Bearer {access_token}"

    response = requests.post(
        LINKEDIN_UGC_POSTS_URL, headers=headers, json=payload, timeout=30
    )

    if response.status_code == 401:
        logger.error(
            "LinkedIn API returned 401 Unauthorized — the access token is "
            "likely expired or invalid. Re-run the OAuth flow described in "
            "README.md to obtain a fresh LINKEDIN_ACCESS_TOKEN."
        )
        response.raise_for_status()
    response.raise_for_status()
    return {
        "status_code": response.status_code,
        "post_id": response.headers.get("x-restli-id") or response.headers.get("X-RestLi-Id"),
    }


def record_history(topic: str, text: str, result: dict | None) -> None:
    history = load_json(HISTORY_FILE, {"posts": []})
    history.setdefault("posts", []).append(
        {
            "topic": topic,
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "content": text,
            "result": result,
        }
    )
    save_json(HISTORY_FILE, history)


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily LinkedIn SQL Server poster")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate the post and log it, but do not publish to LinkedIn.",
    )
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")
    logger = setup_logging()

    required_vars = ["OPENAI_API_KEY"] if args.dry_run else [
        "OPENAI_API_KEY",
        "LINKEDIN_ACCESS_TOKEN",
        "LINKEDIN_PERSON_URN",
    ]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        logger.error(
            "Missing required environment variable(s): %s. "
            "Copy .env.example to .env and fill them in (see README.md).",
            ", ".join(missing),
        )
        return 1

    try:
        topic = pick_topic(logger)
        logger.info("Selected topic: %s", topic)

        post_text = generate_post(topic, logger)
        logger.info("Generated post (%d chars):\n%s", len(post_text), post_text)

        if args.dry_run:
            logger.info("Dry run — skipping LinkedIn publish.")
            record_history(topic, post_text, result={"dry_run": True})
            return 0

        result = post_to_linkedin(post_text, logger)
        logger.info("Published to LinkedIn: %s", result)
        record_history(topic, post_text, result=result)
        return 0

    except Exception:
        logger.exception("Run failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
