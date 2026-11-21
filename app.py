import hmac
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests
from flask import (
    Flask, abort, flash, redirect, render_template, request,
    session, url_for
)

# NayaDrishti AI — Nepal-focused news research and draft dashboard.
# Publishing is manual only. AUTO_PUBLISH is intentionally not implemented.

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("nayadrishti")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")
if not app.secret_key or len(app.secret_key) < 32:
    raise RuntimeError("Set FLASK_SECRET_KEY to a random secret of at least 32 characters.")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "")
FB_GRAPH_VERSION = os.getenv("FB_GRAPH_VERSION", "v27.0")
DATABASE_PATH = os.getenv("DATABASE_PATH", "instance/nayadrishti.sqlite3")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true" if os.getenv("RENDER") else "false").lower() == "true",
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
)

NEWS_SOURCES = [
    ("BBC News Nepali", "https://feeds.bbci.co.uk/nepali/rss.xml"),
    ("Routine of Nepal Banda", "https://www.ronbpost.com/feed/"),
    ("NEB Result & News", "https://nebnews.net/feed/"),
    ("Nepal news", "https://news.google.com/rss/search?q=Nepal+news&hl=en&gl=NP&ceid=NP:en"),
    ("Nepal education and NEB", "https://news.google.com/rss/search?q=Nepal+NEB+education+results&hl=en&gl=NP&ceid=NP:en"),
    ("Nepal cricket", "https://news.google.com/rss/search?q=Nepal+cricket&hl=en&gl=NP&ceid=NP:en"),
    ("ICC Cricket", "https://www.espncricinfo.com/rss/content/story/feeds/0.xml"),
    ("Cricbuzz", "https://news.google.com/rss/search?q=site%3Acricbuzz.com+cricket&hl=en&gl=NP&ceid=NP:en"),
]

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

@contextmanager
def db_connect():
    path = Path(DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db():
    with db_connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                article_url TEXT NOT NULL UNIQUE,
                summary TEXT NOT NULL DEFAULT '',
                published TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                draft_text TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'collected',
                facebook_post_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_news_status ON news_items(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_news_created ON news_items(created_at)")

def logged_in():
    return session.get("authenticated") is True

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not logged_in():
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped

def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token

@app.context_processor
def inject_template_values():
    return {"csrf_token": csrf_token, "logged_in": logged_in}

@app.before_request
def protect_post_requests():
    if request.method == "POST" and request.endpoint not in ("login",):
        supplied = request.form.get("_csrf", "")
        expected = session.get("_csrf", "")
        if not supplied or not expected or not hmac.compare_digest(supplied, expected):
            abort(400, "Invalid or expired form token. Refresh the page and try again.")

def clean(value, limit=4000):
    text = str(value or "")
    return " ".join(text.split())[:limit]

def valid_http_url(value):
    try:
        parsed = urlparse(value)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False

def generate_bilingual_draft(title, summary, source, article_url):
    if not GROQ_API_KEY:
        return ""
    prompt = f"""Create a concise, responsible social-media news draft for a Nepal-focused news page.
Use ONLY facts present in the supplied headline and description. Do not invent names, dates, numbers,
quotes, causes, or details. If information is incomplete, say so or keep the wording cautious.
Write Nepali first, then a short English version, then 3-5 relevant hashtags.
Keep both language sections brief and neutral. Do not claim the page independently verified the story.
Include the source name and article URL at the end. Return only the post text.

Source: {source}
Headline: {title}
Description: {summary}
Article URL: {article_url}"""
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": "You are a careful bilingual Nepali-English news editor. Accuracy and attribution come first."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 450,
        },
        timeout=45,
    )
    response.raise_for_status()
    data = response.json()
    return clean(data["choices"][0]["message"]["content"], 5000)

def extract_image(entry):
    for field in ("media_content", "media_thumbnail"):
        values = entry.get(field, []) or []
        if isinstance(values, dict):
            values = [values]
        for item in values:
            if isinstance(item, dict):
                candidate = item.get("url", "")
                if valid_http_url(candidate):
                    return candidate
    for item in entry.get("enclosures", []) or []:
        if isinstance(item, dict) and str(item.get("type", "")).startswith("image/"):
            candidate = item.get("href") or item.get("url", "")
            if valid_http_url(candidate):
                return candidate
    return ""

def poll_feeds():
    added = 0
    errors = []
    for source, feed_url in NEWS_SOURCES:
        try:
            response = requests.get(
                feed_url,
                headers={"User-Agent": "NayaDrishtiAI/1.0 (+RSS reader)"},
                timeout=(5, 20),
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            for entry in feed.entries[:15]:
                title = clean(entry.get("title"), 500)
                link = clean(entry.get("link"), 2000)
                if not title or not valid_http_url(link):
                    continue
                summary = clean(entry.get("summary") or entry.get("description"), 3000)
                published = clean(entry.get("published") or entry.get("updated"), 120)
                image_url = extract_image(entry)
                with db_connect() as db:
                    cur = db.execute("""
                        INSERT OR IGNORE INTO news_items
                        (source,title,article_url,summary,published,image_url,status,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,'needs_review',?,?)
                    """, (source, title, link, summary, published, image_url, utc_now(), utc_now()))
                    is_new = cur.rowcount == 1
                if not is_new:
                    continue
                added += 1
                if GROQ_API_KEY:
                    try:
                        draft = generate_bilingual_draft(title, summary, source, link)
                        with db_connect() as db:
                            db.execute("UPDATE news_items SET draft_text=?, status='draft', updated_at=? WHERE article_url=?",
                                       (draft, utc_now(), link))
                    except Exception:
                        log.exception("AI draft generation failed for %s", link)
        except Exception as exc:
            log.warning("Feed failed (%s): %s", source, exc)
            errors.append(source)
    return {"added": added, "errors": errors}

def get_counts():
    with db_connect() as db:
        rows = db.execute("SELECT status, COUNT(*) AS n FROM news_items GROUP BY status").fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    counts["total"] = sum(counts.values())
    return counts

@app.route("/", methods=["GET"])
def index():
    return redirect(url_for("dashboard") if logged_in() else url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not ADMIN_PASSWORD:
            flash("ADMIN_PASSWORD has not been configured in your hosting environment.", "error")
            return render_template("login.html"), 503
        supplied = request.form.get("password", "")
        if hmac.compare_digest(supplied.encode(), ADMIN_PASSWORD.encode()):
            session.clear()
            session["authenticated"] = True
            session["_csrf"] = secrets.token_urlsafe(32)
            return redirect(url_for("dashboard"))
        flash("Incorrect password. Please try again.", "error")
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    init_db()
    with db_connect() as db:
        items = db.execute("""
            SELECT * FROM news_items
            ORDER BY CASE status WHEN 'draft' THEN 0 WHEN 'needs_review' THEN 1 ELSE 2 END,
                     id DESC LIMIT 60
        """).fetchall()
    return render_template("dashboard.html", items=items, counts=get_counts(),
                           groq_ready=bool(GROQ_API_KEY),
                           facebook_ready=bool(FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN),
                           sources=NEWS_SOURCES)

@app.route("/news/refresh", methods=["POST"])
@login_required
def refresh_news():
    result = poll_feeds()
    if result["errors"]:
        flash(f"Refresh finished: {result['added']} new stories; {len(result['errors'])} feeds had errors.", "warning")
    else:
        flash(f"Refresh complete: {result['added']} new stories added.", "success")
    return redirect(url_for("dashboard"))

@app.route("/drafts/<int:item_id>/regenerate", methods=["POST"])
@login_required
def regenerate_draft(item_id):
    if not GROQ_API_KEY:
        flash("Add GROQ_API_KEY in your hosting environment to generate AI drafts.", "error")
        return redirect(url_for("dashboard"))
    with db_connect() as db:
        item = db.execute("SELECT * FROM news_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        abort(404)
    try:
        draft = generate_bilingual_draft(item["title"], item["summary"], item["source"], item["article_url"])
        with db_connect() as db:
            db.execute("UPDATE news_items SET draft_text=?, status='draft', updated_at=? WHERE id=?",
                       (draft, utc_now(), item_id))
        flash("Draft regenerated. Please review it before publishing.", "success")
    except Exception:
        log.exception("Draft regeneration failed")
        flash("AI draft generation failed. Check your Groq API configuration and server logs.", "error")
    return redirect(url_for("dashboard"))

@app.route("/drafts/<int:item_id>/publish", methods=["POST"])
@login_required
def publish_draft(item_id):
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        flash("Facebook publishing is not configured. Add FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN to hosting secrets.", "error")
        return redirect(url_for("dashboard"))
    with db_connect() as db:
        item = db.execute("SELECT * FROM news_items WHERE id=?", (item_id,)).fetchone()
    if not item or not item["draft_text"]:
        abort(404)
    if item["status"] == "published":
        flash("This story has already been published.", "warning")
        return redirect(url_for("dashboard"))
    endpoint = f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{FB_PAGE_ID}/feed"
    try:
        response = requests.post(endpoint, data={
            "message": item["draft_text"],
            "link": item["article_url"],
            "access_token": FB_PAGE_ACCESS_TOKEN,
        }, timeout=30)
        response.raise_for_status()
        post_id = response.json().get("id", "")
        if not post_id:
            raise RuntimeError("Meta did not return a post ID.")
        with db_connect() as db:
            db.execute("UPDATE news_items SET status='published', facebook_post_id=?, updated_at=? WHERE id=?",
                       (post_id, utc_now(), item_id))
        flash("Facebook post published successfully.", "success")
    except Exception:
        log.exception("Facebook publishing failed")
        flash("Facebook publishing failed. Check Page permissions, token validity, and server logs.", "error")
    return redirect(url_for("dashboard"))

@app.route("/drafts/<int:item_id>/dismiss", methods=["POST"])
@login_required
def dismiss_draft(item_id):
    with db_connect() as db:
        cur = db.execute("UPDATE news_items SET status='dismissed', updated_at=? WHERE id=? AND status!='published'",
                         (utc_now(), item_id))
    if not cur.rowcount:
        abort(404)
    flash("Story dismissed.", "success")
    return redirect(url_for("dashboard"))

@app.route("/health")
def health():
    return {"status": "ok", "service": "NayaDrishti AI"}

@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message="Your form expired or was invalid. Please go back, refresh, and try again."), 400

@app.errorhandler(404)
def not_found(error):
    return render_template("error.html", code=404, message="The requested page or story was not found."), 404

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
