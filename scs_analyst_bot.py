import os
import re
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://www.scstrade.com/AnalystOpinionMain.aspx"
BASE_URL = "https://www.scstrade.com/"
SENT_FILE = Path("sent_scs_analyst_posts.json")
MAX_SENT = int(os.getenv("MAX_SENT", "500"))

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
SEND_EXISTING_ON_FIRST_RUN = os.getenv("SEND_EXISTING_ON_FIRST_RUN", "false").lower() == "true"
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "10"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def load_sent():
    if not SENT_FILE.exists():
        return set()

    try:
        data = json.loads(SENT_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(str(x) for x in data)
        if isinstance(data, dict):
            return set(str(x) for x in data.get("ids", []))
    except Exception:
        logging.exception("Could not read %s", SENT_FILE)

    return set()


def save_sent(sent):
    # Keep newest-ish IDs without letting the file grow forever.
    values = list(sent)[-MAX_SENT:]
    SENT_FILE.write_text(
        json.dumps(values, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def make_id(post):
    # Prefer a direct PDF/page link. Fall back to the post's
    # identifying fields so the same post is not resent.
    identity = "|".join([
        post.get("url", ""),
        post.get("title", ""),
        post.get("company", ""),
        post.get("date", ""),
        post.get("analyst", ""),
    ])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def fetch_page():
    r = requests.get(
        SOURCE_URL,
        headers=HEADERS,
        timeout=30,
        allow_redirects=True,
    )
    r.raise_for_status()
    if not r.encoding:
        r.encoding = r.apparent_encoding
    return r.text


def likely_post_link(a):
    href = (a.get("href") or "").strip()
    text = clean(a.get_text(" ", strip=True))

    if not href or href.lower().startswith(("javascript:", "#", "mailto:")):
        return False

    h = href.lower()
    # SCS analyst reports commonly expose PDFs/audio/report links.
    return (
        ".pdf" in h
        or "apaudio" in h
        or "analyst" in h
        or "opinion" in h
        or len(text) > 12
    )


def extract_posts(html):
    soup = BeautifulSoup(html, "html.parser")
    posts = []

    # Strategy 1: detect links around analyst-report content.
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        href = urljoin(BASE_URL, a["href"])

        if not likely_post_link(a):
            continue

        parent = a.parent
        context = clean(parent.get_text(" ", strip=True)) if parent else title

        # Skip obvious navigation/footer links.
        if any(x in title.lower() for x in [
            "contact us", "privacy policy", "disclaimer",
            "home", "login", "register", "about us",
        ]):
            continue

        if len(title) < 8:
            continue

        posts.append({
            "title": title[:300],
            "url": href,
            "context": context[:1800],
            "company": "",
            "analyst": "",
            "date": "",
            "content": "",
        })

    # Strategy 2: if the page exposes headings/table rows, capture them.
    for row in soup.find_all(["tr", "article", "li", "div"]):
        txt = clean(row.get_text(" ", strip=True))
        low = txt.lower()

        if not txt or len(txt) < 20 or len(txt) > 2500:
            continue

        if not any(k in low for k in ["analyst", "opinion", "research", "report"]):
            continue

        links = row.find_all("a", href=True)
        if not links:
            continue

        for a in links:
            title = clean(a.get_text(" ", strip=True))
            if len(title) >= 8:
                posts.append({
                    "title": title[:300],
                    "url": urljoin(BASE_URL, a["href"]),
                    "context": txt[:1800],
                    "company": "",
                    "analyst": "",
                    "date": "",
                    "content": "",
                })

    # Deduplicate candidate records.
    unique = {}
    for p in posts:
        key = (p["url"], p["title"])
        unique[key] = p

    return list(unique.values())


def enrich(post):
    text = post.get("context", "")

    # Conservative extraction. We don't invent fields when the site
    # doesn't expose them in a predictable pattern.
    date_match = re.search(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}"
        r"(?:\s+\d{1,2}:\d{2}\s*(?:AM|PM)?)?",
        text,
        re.I,
    )
    if date_match:
        post["date"] = clean(date_match.group(0))

    analyst_match = re.search(
        r"(?:Analyst|By|Author)\s*[:\-]\s*([^|•]+)",
        text,
        re.I,
    )
    if analyst_match:
        post["analyst"] = clean(analyst_match.group(1))[:120]

    return post


def send_discord(post):
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not configured.")

    title = post["title"]
    url = post["url"] or SOURCE_URL
    description = post.get("context", "")

    if len(description) > 850:
        description = description[:847] + "..."

    embed = {
        "title": "📢 New SCS Analyst Opinion",
        "description": f"**{title}**",
        "url": url,
        "color": 3447003,
        "fields": [],
        "footer": {"text": "SCS Trade • Analyst Opinions"},
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }

    if post.get("company"):
        embed["fields"].append({
            "name": "Company / Index",
            "value": post["company"][:1024],
            "inline": True,
        })

    if post.get("analyst"):
        embed["fields"].append({
            "name": "Analyst",
            "value": post["analyst"][:1024],
            "inline": True,
        })

    if post.get("date"):
        embed["fields"].append({
            "name": "Date",
            "value": post["date"][:1024],
            "inline": True,
        })

    if description and description != title:
        embed["fields"].append({
            "name": "Details",
            "value": description,
            "inline": False,
        })

    embed["fields"].append({
        "name": "Source",
        "value": f"[Open Analyst Opinions]({SOURCE_URL})",
        "inline": False,
    })

    payload = {
        "username": "SCS Analyst Alerts",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }

    r = requests.post(
        DISCORD_WEBHOOK_URL,
        json=payload,
        timeout=30,
    )

    if r.status_code == 429:
        retry_after = r.json().get("retry_after", 1)
        raise RuntimeError(f"Discord rate limited. Retry after {retry_after}s.")

    r.raise_for_status()


def main():
    logging.info("Checking SCS Analyst Opinions: %s", SOURCE_URL)

    html = fetch_page()
    posts = [enrich(p) for p in extract_posts(html)]

    if not posts:
        raise RuntimeError(
            "No analyst posts were detected. The SCS page structure may have changed."
        )

    logging.info("Detected %d candidate post(s).", len(posts))

    # The page normally puts newest content first. Keep only a manageable
    # number of candidates per run.
    posts = posts[:MAX_POSTS_PER_RUN]

    sent = load_sent()
    first_run = not SENT_FILE.exists()
    new_posts = []

    for post in reversed(posts):
        post_id = make_id(post)

        if post_id in sent:
            continue

        # On the first run, seed existing records instead of flooding Discord.
        if first_run and not SEND_EXISTING_ON_FIRST_RUN:
            sent.add(post_id)
            continue

        new_posts.append((post_id, post))

    if first_run and not SEND_EXISTING_ON_FIRST_RUN:
        save_sent(sent)
        logging.info(
            "First run complete. Seeded %d existing post(s); no Discord messages sent.",
            len(sent),
        )
        return

    for post_id, post in new_posts:
        send_discord(post)
        sent.add(post_id)
        save_sent(sent)
        logging.info("Sent: %s", post["title"])

    if not new_posts:
        save_sent(sent)
        logging.info("No new analyst opinions.")


if __name__ == "__main__":
    main()
