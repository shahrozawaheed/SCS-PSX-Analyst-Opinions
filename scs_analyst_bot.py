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
        content = SENT_FILE.read_text(encoding="utf-8").strip()
        if not content:
            return set()
        
        data = json.loads(content)
        if isinstance(data, list):
            return set(str(x) for x in data)
        if isinstance(data, dict):
            return set(str(x) for x in data.get("ids", []))
    except Exception:
        logging.exception("Could not read %s", SENT_FILE)

    return set()


def save_sent(sent):
    values = list(sent)[-MAX_SENT:]
    SENT_FILE.write_text(
        json.dumps(values, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def make_id(post):
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


def extract_posts(html):
    soup = BeautifulSoup(html, "html.parser")
    posts = []

    # Scope strictly to the ASP.NET GridView container
    grid = soup.select_one("#ContentPlaceHolder1_GridView1")
    if not grid:
        logging.warning("GridView container (#ContentPlaceHolder1_GridView1) not found.")
        return []

    # Iterate over individual post tables within the grid
    tables = grid.select("table.sTable")
    if not tables:
        # Fallback if class names vary slightly but nested tables exist
        tables = grid.find_all("table")

    for tbl in tables:
        cells = tbl.find_all("td")
        if not cells:
            continue

        company, title, analyst_raw, date_raw, content_raw = "", "", "", "", ""
        pdf_url = ""

        # Strategy A: Precise grid layout extraction (5-cell structure)
        if len(cells) >= 4:
            company = clean(cells[0].get_text(" ", strip=True))
            title = clean(cells[1].get_text(" ", strip=True))
            
            meta_text = clean(cells[2].get_text(" ", strip=True))
            content_raw = clean(cells[3].get_text(" ", strip=True)) if len(cells) > 3 else ""

            # Extract Analyst name and Date/Time from metadata cell
            analyst_match = re.search(r"(?:Analyst|By)\s*[:\-]?\s*([^0-9\n|•]+)", meta_text, re.I)
            if analyst_match:
                analyst_raw = clean(analyst_match.group(1))

            date_match = re.search(
                r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}"
                r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)?",
                meta_text,
                re.I,
            )
            if date_match:
                date_raw = clean(date_match.group(0))

        # Look for the explicit PDF/Audio link inside the table
        pdf_anchor = tbl.find("a", href=re.compile(r"(\.pdf|AnalystOpinionPdf|apaudio)", re.I))
        if pdf_anchor and pdf_anchor.get("href"):
            pdf_url = urljoin(BASE_URL, pdf_anchor["href"].strip())

        # Validation: Ignore empty or header rows
        if not title or len(title) < 4:
            continue

        posts.append({
            "company": company[:150],
            "title": title[:300],
            "analyst": analyst_raw[:100],
            "date": date_raw,
            "context": content_raw[:1800],
            "url": pdf_url or SOURCE_URL,
        })

    # Deduplicate candidate records
    unique = {}
    for p in posts:
        key = (p["url"], p["title"])
        unique[key] = p

    return list(unique.values())


def send_discord(post):
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not configured.")

    title = post["title"]
    url = post["url"]
    description = post.get("context", "")

    if len(description) > 850:
        description = description[:847] + "..."

    embed = {
        "title": "📢 NEW SCS ANALYST OPINION",
        "description": f"**{title}**",
        "url": url,
        "color": 3447003,
        "fields": [],
        "footer": {"text": "SCS Trade • Analyst Opinions"},
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }

    if post.get("company"):
        embed["fields"].append({
            "name": "🏢 Company / Index",
            "value": post["company"][:1024],
            "inline": True,
        })

    if post.get("analyst"):
        embed["fields"].append({
            "name": "👤 Analyst",
            "value": post["analyst"][:1024],
            "inline": True,
        })

    if post.get("date"):
        embed["fields"].append({
            "name": "🕐 Date",
            "value": post["date"][:1024],
            "inline": True,
        })

    if description and description.lower() != title.lower():
        embed["fields"].append({
            "name": "📝 Details",
            "value": description,
            "inline": False,
        })

    pdf_label = "📄 Open PDF Report" if ".pdf" in url.lower() or "pdf" in url.lower() else "🔗 View Source Report"
    embed["fields"].append({
        "name": "Source Link",
        "value": f"[{pdf_label}]({url})",
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
    posts = extract_posts(html)

    if not posts:
        logging.warning("No analyst posts detected. Check if table layout or grid ID changed.")
        return

    logging.info("Detected %d candidate analyst post(s).", len(posts))

    posts = posts[:MAX_POSTS_PER_RUN]

    sent = load_sent()
    # Treat uninitialized/empty state as first-run seeding
    first_run = (not SENT_FILE.exists()) or (len(sent) == 0)
    new_posts = []

    for post in reversed(posts):
        post_id = make_id(post)

        if post_id in sent:
            continue

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
        logging.info("Sent alert: %s", post["title"])

    if not new_posts:
        save_sent(sent)
        logging.info("No new analyst opinions.")


if __name__ == "__main__":
    main()
