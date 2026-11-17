
import os
import re
import html
import secrets
import logging
import requests
import feedparser

from functools import wraps
from urllib.parse import urlencode, urljoin, urlparse

from flask import (
    Flask, request, redirect, url_for,
    session, flash, render_template_string
)

from groq import Groq


# =========================================================
# NAYADRISHTI AI
# =========================================================

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

app.secret_key = os.environ.get("FLASK_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY is missing in Render.")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

FB_APP_ID = os.environ.get("FB_APP_ID")
FB_APP_SECRET = os.environ.get("FB_APP_SECRET")
FB_CONFIG_ID = os.environ.get("FB_CONFIG_ID", "").strip()

APP_BASE_URL = os.environ.get(
    "APP_BASE_URL",
    "https://nayadristhi-ai.onrender.com"
).rstrip("/")

GRAPH_VERSION = "v27.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"
REDIRECT_URI = APP_BASE_URL + "/facebook/callback"

GROQ_MODEL = "openai/gpt-oss-120b"
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_NAME="nayadrishti_session"
)


# =========================================================
# NEWS SOURCES
# =========================================================

NEWS_SOURCES = [
    {
        "name": "Routine of Nepal Banda",
        "url": "https://www.ronbpost.com/feed/"
    },
    {
        "name": "ICC",
        "url": "https://news.google.com/rss/search?q=site%3Aicc-cricket.com%2Fnews&hl=en&gl=NP&ceid=NP%3Aen"
    },
    {
        "name": "ESPNcricinfo",
        "url": "https://www.espncricinfo.com/rss/content/story/feeds/0.xml"
    },
    {
        "name": "ESPN FC",
        "url": "https://www.espnfc.com/rss"
    },
    {
        "name": "NEB News",
        "url": "https://nebnews.net/feed/"
    }
]


# =========================================================
# GENERAL HELPERS
# =========================================================

def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def valid_http_url(value):
    try:
        parsed = urlparse(value or "")
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def is_http_image_url(value):
    if not value:
        return False
    return valid_http_url(value)


def extract_image_from_html(fragment, base_url):
    if not fragment:
        return None

    patterns = [
        r'<img[^>]+src=["\']([^"\']+)["\']',
        r'<img[^>]+data-src=["\']([^"\']+)["\']'
    ]

    for pattern in patterns:
        match = re.search(pattern, fragment, re.I)
        if match:
            image_url = urljoin(base_url, html.unescape(match.group(1)))
            if is_http_image_url(image_url):
                return image_url

    return None


def extract_article_image(entry, article_url):
    # RSS media content
    for media in entry.get("media_content", []) or []:
        image_url = media.get("url")
        if is_http_image_url(image_url):
            return image_url

    # RSS thumbnail
    for media in entry.get("media_thumbnail", []) or []:
        image_url = media.get("url")
        if is_http_image_url(image_url):
            return image_url

    # Image enclosure
    for enclosure in entry.get("enclosures", []) or []:
        image_url = enclosure.get("href") or enclosure.get("url")
        media_type = enclosure.get("type", "")
        if is_http_image_url(image_url) and (
            media_type.startswith("image/") or not media_type
        ):
            return image_url

    # Image inside RSS content or summary
    for content in entry.get("content", []) or []:
        image_url = extract_image_from_html(
            content.get("value", ""),
            article_url
        )
        if image_url:
            return image_url

    image_url = extract_image_from_html(
        entry.get("summary", ""),
        article_url
    )
    if image_url:
        return image_url

    # Try article page metadata
    try:
        response = requests.get(
            article_url,
            headers={"User-Agent": "Mozilla/5.0 NayaDrishtiAI"},
            timeout=8,
            stream=True
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            response.close()
            return None

        chunks = []
        total = 0
        for chunk in response.iter_content(8192):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= 200000:
                break
        response.close()

        page_html = b"".join(chunks).decode(
            response.encoding or "utf-8",
            errors="ignore"
        )

        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image'
        ]

        for pattern in patterns:
            match = re.search(pattern, page_html, re.I)
            if match:
                image_url = urljoin(
                    article_url,
                    html.unescape(match.group(1))
                )
                if is_http_image_url(image_url):
                    return image_url

    except Exception:
        app.logger.info("Could not extract article image: %s", article_url)

    return None


def collect_news():
    articles = []
    errors = []

    headers = {"User-Agent": "Mozilla/5.0 NayaDrishtiAI/1.0"}

    for source in NEWS_SOURCES:
        try:
            response = requests.get(
                source["url"],
                headers=headers,
                timeout=12
            )
            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if not feed.entries:
                errors.append(source["name"] + ": No articles found.")
                continue

            for entry in feed.entries[:8]:
                title = clean_text(entry.get("title", ""))
                link = entry.get("link", "").strip()

                if not title or not valid_http_url(link):
                    continue

                summary = clean_text(
                    entry.get("summary", "No description available.")
                )

                image_url = extract_article_image(entry, link)

                articles.append({
                    "source": source["name"],
                    "title": title,
                    "link": link,
                    "published": clean_text(
                        entry.get(
                            "published",
                            entry.get("updated", "Date not available")
                        )
                    ),
                    "summary": summary,
                    "image_url": image_url
                })

        except Exception as error:
            app.logger.warning(
                "News feed failed for %s: %s",
                source["name"],
                str(error)
            )
            errors.append(source["name"] + ": Could not load feed.")

    # Remove duplicate article links
    unique = []
    seen = set()

    for article in articles:
        if article["link"] not in seen:
            seen.add(article["link"])
            unique.append(article)

    return unique, errors


# =========================================================
# LOGIN
# =========================================================

def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")

        if ADMIN_PASSWORD and secrets.compare_digest(
            password, ADMIN_PASSWORD
        ):
            session.clear()
            session["logged_in"] = True
            return redirect(url_for("dashboard"))

        flash("Incorrect password.")

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
      <title>NayaDrishti AI Login</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        body {
          font-family: Arial;
          background: #eef2f7;
          display: flex;
          justify-content: center;
          align-items: center;
          min-height: 90vh;
        }
        .box {
          background: white;
          padding: 30px;
          border-radius: 12px;
          width: 320px;
          box-shadow: 0 3px 15px #ccc;
        }
        input, button {
          box-sizing: border-box;
          width: 100%;
          padding: 12px;
          margin-top: 12px;
        }
        button {
          background: #1769aa;
          color: white;
          border: none;
          cursor: pointer;
        }
      </style>
    </head>
    <body>
      <div class="box">
        <h2>NayaDrishti AI</h2>
        <p>Admin Login</p>
        {% for message in get_flashed_messages() %}
          <p style="color:red">{{ message }}</p>
        {% endfor %}
        <form method="POST">
          <input type="password" name="password"
                 placeholder="Admin Password" required>
          <button type="submit">Login</button>
        </form>
      </div>
    </body>
    </html>
    """)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
      <title>NayaDrishti AI Dashboard</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        body {font-family:Arial;background:#f3f5f8;padding:25px}
        .card {background:white;padding:20px;margin:15px 0;border-radius:10px}
        a {color:#0757a6;text-decoration:none}
      </style>
    </head>
    <body>
      <h1>NayaDrishti AI</h1>
      <p>Your AI-powered news publishing assistant.</p>
      <div class="card">
        <h3>News Management</h3>
        <a href="/news">Fetch Latest News</a>
      </div>
      <div class="card">
        <h3>AI News Summary</h3>
        <a href="/ai">Open AI Summary</a>
      </div>
      <div class="card">
        <h3>Facebook Management</h3>
        <a href="/facebook/login">Connect Facebook</a><br><br>
        <a href="/facebook">View Facebook Pages</a><br><br>
        <a href="/facebook/publish">Publish Facebook Post</a>
      </div>
      <p><a href="/logout">Logout</a></p>
    </body>
    </html>
    """)

# =========================================================
# NEWS PAGE
# =========================================================

@app.route("/news")
@login_required
def news():
    articles, errors = collect_news()

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
      <title>NayaDrishti AI - Latest News</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        body {
          font-family: Arial, sans-serif;
          background: #f3f5f8;
          margin: 20px;
          color: #222;
        }
        .article {
          background: white;
          padding: 20px;
          margin-bottom: 18px;
          border-radius: 10px;
          border: 1px solid #ddd;
        }
        .source {color:#1769aa;font-weight:bold}
        .article-image {
          width: 100%;
          max-width: 650px;
          max-height: 360px;
          object-fit: cover;
          border-radius: 8px;
          display: block;
          margin: 12px 0;
        }
        .button {
          display: inline-block;
          padding: 11px 16px;
          margin-top: 10px;
          background: #1769aa;
          color: white;
          border-radius: 5px;
          border: none;
          text-decoration: none;
          cursor: pointer;
        }
        .original {background:#555}
        .error {
          background:#ffe9e9;
          color:#b00020;
          padding:10px;
          margin:8px 0;
        }
        .notice {
          background:#fff8df;
          padding:10px;
          border-radius:6px;
          font-size:13px;
        }
      </style>
    </head>
    <body>
      <h1>NayaDrishti AI - Latest News</h1>
      <p>Articles collected: <b>{{ articles|length }}</b></p>
      <a class="button" href="/news">Refresh News</a>

      <h2>Feed Status</h2>
      {% if errors %}
        {% for error in errors %}
          <div class="error">{{ error }}</div>
        {% endfor %}
      {% else %}
        <p>All feeds returned articles.</p>
      {% endif %}

      <h2>Latest Articles</h2>

      {% for article in articles %}
        <div class="article">
          <div class="source">{{ article.source }}</div>
          <h3>{{ article.title }}</h3>

          {% if article.image_url %}
            <img class="article-image"
                 src="{{ article.image_url }}"
                 alt="Article image"
                 loading="lazy">
            <div class="notice">
              Image preview only. Confirm you have permission
              to reuse an image before publishing it.
            </div>
          {% endif %}

          <small>{{ article.published }}</small>
          <p>{{ article.summary|truncate(500) }}</p>

          <a class="button original"
             href="{{ article.link }}"
             target="_blank"
             rel="noopener noreferrer">
            Read Original Article
          </a>

          <form method="POST" action="/news/summarize">
            <input type="hidden" name="title"
                   value="{{ article.title }}">
            <input type="hidden" name="news_source"
                   value="{{ article.source }}">
            <input type="hidden" name="description"
                   value="{{ article.summary }}">
            <input type="hidden" name="link"
                   value="{{ article.link }}">
            <input type="hidden" name="image_url"
                   value="{{ article.image_url or '' }}">
            <button class="button" type="submit">
              Generate AI Summary
            </button>
          </form>
        </div>
      {% else %}
        <p>No news articles found.</p>
      {% endfor %}

      <p><a href="/dashboard">Back to Dashboard</a></p>
    </body>
    </html>
    """, articles=articles, errors=errors)


# =========================================================
# GENERATE AI NEWS SUMMARY
# =========================================================

@app.route("/news/summarize", methods=["POST"])
@login_required
def summarize_article():
    title = request.form.get("title", "").strip()
    news_source = request.form.get("news_source", "").strip()
    description = request.form.get("description", "").strip()
    link = request.form.get("link", "").strip()
    image_url = request.form.get("image_url", "").strip()

    summary = ""
    error_message = ""

    if not title or not valid_http_url(link):
        error_message = "Article title or original link is invalid."
    elif not groq_client:
        error_message = "Groq API key is not configured in Render."
    else:
        try:
            result = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are NayaDrishti AI, a professional "
                            "news editor for a Facebook news page. "
                            "Create an original, attractive Facebook "
                            "post based only on supplied information. "
                            "Use simple, clear language. Preserve "
                            "important facts, names, dates and figures. "
                            "Do not invent facts or unsupported claims. "
                            "Do not copy the article word for word. "
                            "Write an interesting headline and concise "
                            "summary. Include the source name and "
                            "original article link. Add a few relevant "
                            "hashtags. Return only the finished post."
                        )
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Source: {news_source}\n"
                            f"Title: {title}\n"
                            f"Description: {description[:5000]}\n"
                            f"Original link: {link}"
                        )
                    }
                ],
                temperature=0.4,
                max_tokens=800,
                timeout=60
            )

            summary = (
                result.choices[0].message.content or ""
            ).strip()

            if not summary:
                error_message = "Groq returned an empty summary."

        except Exception:
            app.logger.exception("AI summary generation failed.")
            error_message = (
                "AI summary generation failed. "
                "Check your Groq configuration and Render logs."
            )

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
      <title>NayaDrishti AI - Post Preview</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        body {
          font-family:Arial;
          background:#f3f5f8;
          padding:20px;
        }
        .container {
          background:white;
          padding:25px;
          border-radius:10px;
          max-width:800px;
          margin:auto;
        }
        textarea {
          width:100%;
          box-sizing:border-box;
          padding:15px;
          min-height:280px;
          font-family:Arial;
          font-size:15px;
          line-height:1.6;
        }
        .button {
          background:#1769aa;
          color:white;
          border:0;
          padding:12px 18px;
          cursor:pointer;
          border-radius:5px;
        }
        .error {
          color:#b00020;
          background:#ffe9e9;
          padding:12px;
        }
        .preview-image {
          width:100%;
          max-width:650px;
          max-height:360px;
          object-fit:cover;
          border-radius:8px;
        }
        .notice {
          background:#fff8df;
          padding:12px;
          border-radius:6px;
        }
      </style>
    </head>
    <body>
      <div class="container">
        <h1>NayaDrishti AI</h1>
        <h2>Facebook Post Preview</h2>

        {% if error_message %}
          <div class="error">{{ error_message }}</div>
        {% else %}
          <p style="color:green">
            AI summary generated successfully.
          </p>
        {% endif %}

        <h3>{{ title }}</h3>
        <p><b>News source:</b> {{ news_source }}</p>

        {% if image_url %}
          <img class="preview-image"
               src="{{ image_url }}" alt="Article image">
          <p class="notice">
            This is an image preview. Only publish it if you
            have permission or a suitable license to reuse it.
          </p>
        {% endif %}

        <form method="POST" action="/facebook/publish">
          <label><b>Review and edit your Facebook post</b></label>
          <textarea name="message" required>{{ summary }}

Source: {{ news_source }}
Original article: {{ link }}</textarea>

          <input type="hidden" name="image_url"
                 value="{{ image_url }}">

          <br><br>
          <button class="button" type="submit">
            Continue to Facebook Publishing
          </button>
        </form>

        <p>
          <a href="{{ link }}" target="_blank"
             rel="noopener noreferrer">
            Read Original Article
          </a>
        </p>
        <p><a href="/news">Back to News</a></p>
        <p><a href="/dashboard">Dashboard</a></p>
      </div>
    </body>
    </html>
    """,
        title=title,
        news_source=news_source,
        summary=summary,
        link=link,
        image_url=image_url,
        error_message=error_message
    )


# =========================================================
# MANUAL AI SUMMARY
# =========================================================

@app.route("/ai", methods=["GET", "POST"])
@login_required
def ai():
    answer = ""

    if request.method == "POST":
        news_text = request.form.get("news", "").strip()

        if not groq_client:
            answer = "Groq API key is not configured."
        elif not news_text:
            answer = "Please enter news text."
        else:
            try:
                result = groq_client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are NayaDrishti AI. Summarize "
                                "news in simple language. Do not "
                                "invent facts. Preserve important "
                                "information."
                            )
                        },
                        {"role": "user", "content": news_text}
                    ],
                    temperature=0.4,
                    max_tokens=800,
                    timeout=60
                )
                answer = (
                    result.choices[0].message.content or ""
                )
            except Exception:
                app.logger.exception("Manual AI summary failed.")
                answer = "AI summary failed. Check Render logs."

    return render_template_string("""
    <h1>NayaDrishti AI Summary</h1>
    <form method="POST">
      <textarea name="news" rows="10"
        style="width:90%"
        placeholder="Paste news here..." required></textarea>
      <br><br>
      <button type="submit">Generate Summary</button>
    </form>
    <h3>AI Summary</h3>
    <pre style="white-space:pre-wrap">{{ answer }}</pre>
    <a href="/dashboard">Back to Dashboard</a>
    """, answer=answer)


# =========================================================
# FACEBOOK API HELPERS
# =========================================================

def facebook_request(endpoint, token, params=None, method="GET"):
    url = f"{GRAPH_URL}/{endpoint.lstrip('/')}"
    data = dict(params or {})
    data["access_token"] = token

    response = requests.request(
        method,
        url,
        params=data if method == "GET" else None,
        data=data if method != "GET" else None,
        timeout=30
    )

    try:
        result = response.json()
    except ValueError:
        raise Exception("Facebook returned an invalid response.")

    if response.status_code >= 400 or "error" in result:
        error = result.get("error", {})
        raise Exception(
            error.get("message", "Facebook API error")
        )

    return result


def get_facebook_pages(user_token):
    result = facebook_request(
        "me/accounts",
        user_token,
        {"fields": "id,name,access_token,tasks", "limit": 100}
    )

    pages = result.get("data", [])
    next_url = result.get("paging", {}).get("next")
    page_count = 1

    while next_url and page_count < 10:
        response = requests.get(next_url, timeout=30)
        data = response.json()

        if response.status_code >= 400 or "error" in data:
            raise Exception("Facebook Page list could not be loaded.")

        pages.extend(data.get("data", []))
        next_url = data.get("paging", {}).get("next")
        page_count += 1

    return pages


def get_selected_page(page_id):
    """
    Fetch Page details and Page access token directly from
    Facebook each time. Do not store Page tokens or Page lists
    in the browser session.
    """
    user_token = session.get("fb_access_token")

    if not user_token:
        raise Exception("Facebook is not connected. Reconnect first.")

    pages = get_facebook_pages(user_token)

    for page in pages:
        if str(page.get("id")) == str(page_id):
            if not page.get("access_token"):
                raise Exception(
                    "Facebook did not provide a Page access token. "
                    "Check Page permissions."
                )
            return page

    raise Exception(
        "The selected Facebook Page was not found. "
        "Reconnect Facebook and select the Page again."
    )

# =========================================================
# FACEBOOK LOGIN
# =========================================================

@app.route("/facebook/login")
@login_required
def facebook_login():
    if not FB_APP_ID or not FB_APP_SECRET:
        return "Facebook App ID or App Secret is missing in Render.", 500

    state = secrets.token_urlsafe(32)
    session["fb_oauth_state"] = state

    params = {
        "client_id": FB_APP_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "response_type": "code"
    }

    if FB_CONFIG_ID:
        params["config_id"] = FB_CONFIG_ID
    else:
        params["scope"] = ",".join([
            "pages_show_list",
            "pages_read_engagement",
            "pages_manage_posts"
        ])

    login_url = (
        f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth?"
        + urlencode(params)
    )

    return redirect(login_url)


# =========================================================
# FACEBOOK CALLBACK
# =========================================================

@app.route("/facebook/callback")
@login_required
def facebook_callback():
    if request.args.get("error"):
        return "Facebook login was cancelled or failed.", 400

    returned_state = request.args.get("state")
    expected_state = session.pop("fb_oauth_state", None)

    if not expected_state or not secrets.compare_digest(
        returned_state or "",
        expected_state
    ):
        return "Invalid Facebook login state. Please reconnect.", 400

    code = request.args.get("code")
    if not code:
        return "Facebook authorization code is missing.", 400

    try:
        response = requests.get(
            f"{GRAPH_URL}/oauth/access_token",
            params={
                "client_id": FB_APP_ID,
                "client_secret": FB_APP_SECRET,
                "redirect_uri": REDIRECT_URI,
                "code": code
            },
            timeout=30
        )

        token_data = response.json()

        if response.status_code >= 400 or not token_data.get("access_token"):
            app.logger.error(
                "Facebook token exchange response: %s",
                token_data.get("error", {}).get("message", "Unknown error")
            )
            return "Facebook token exchange failed. Check Render logs.", 400

        # Store only the user access token.
        # Never store the full Page list or Page tokens in the session.
        session["fb_access_token"] = token_data["access_token"]

        return redirect(url_for("facebook_pages"))

    except Exception:
        app.logger.exception("Facebook callback failed.")
        return "Facebook connection failed. Check Render logs.", 500


# =========================================================
# FACEBOOK PAGE LIST
# =========================================================

@app.route("/facebook")
@login_required
def facebook_pages():
    user_token = session.get("fb_access_token")

    if not user_token:
        return redirect(url_for("facebook_login"))

    try:
        pages = get_facebook_pages(user_token)

        # Pass Page information to the template only.
        # Do not save the Page list in the session.
        safe_pages = [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "has_token": bool(p.get("access_token"))
            }
            for p in pages
        ]

        return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
          <title>Facebook Pages - NayaDrishti AI</title>
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <style>
            body {font-family:Arial;background:#f3f5f8;padding:20px}
            .card {
              background:white;padding:18px;margin:12px 0;
              border-radius:10px;border:1px solid #ddd;
            }
            a,button {
              display:inline-block;padding:10px 14px;
              background:#1769aa;color:white;text-decoration:none;
              border:0;border-radius:5px;cursor:pointer;
            }
            .good {color:green}
            .bad {color:#b00020}
          </style>
        </head>
        <body>
          <h1>Your Facebook Pages</h1>

          {% for page in pages %}
            <div class="card">
              <h3>{{ page.name }}</h3>
              <p>Page ID: {{ page.id }}</p>

              {% if page.has_token %}
                <p class="good">Page access token available</p>
                <form method="GET" action="/facebook/publish">
                  <input type="hidden" name="page_id"
                         value="{{ page.id }}">
                  <button type="submit">Publish to this Page</button>
                </form>
                <p>
                  <a href="/facebook/posts/{{ page.id }}">
                    View Page Posts
                  </a>
                </p>
              {% else %}
                <p class="bad">Page access token unavailable.</p>
              {% endif %}
            </div>
          {% else %}
            <p>No Facebook Pages found for this account.</p>
          {% endfor %}

          <p><a href="/facebook/debug">Permission Details</a></p>
          <p><a href="/facebook/logout">Disconnect Facebook</a></p>
          <p><a href="/dashboard">Dashboard</a></p>
        </body>
        </html>
        """, pages=safe_pages)

    except Exception as error:
        app.logger.exception("Facebook Page retrieval failed.")
        return (
            "Unable to retrieve Facebook Pages: " + str(error),
            500
        )


# =========================================================
# FACEBOOK PERMISSION CHECK
# =========================================================

@app.route("/facebook/debug")
@login_required
def facebook_debug():
    token = session.get("fb_access_token")

    if not token:
        return redirect(url_for("facebook_login"))

    try:
        result = facebook_request("me/permissions", token)
        permissions = result.get("data", [])

        return render_template_string("""
        <h1>Facebook Permission Details</h1>
        {% for permission in permissions %}
          <p>
            <b>{{ permission.permission }}</b>:
            {{ permission.status }}
          </p>
        {% endfor %}
        <a href="/facebook">Back to Facebook Pages</a>
        """, permissions=permissions)

    except Exception as error:
        app.logger.exception("Facebook permission check failed.")
        return "Permission check failed: " + str(error), 500


# =========================================================
# VIEW FACEBOOK POSTS
# =========================================================

@app.route("/facebook/posts/<page_id>")
@login_required
def facebook_posts(page_id):
    try:
        page = get_selected_page(page_id)

        result = facebook_request(
            f"{page_id}/posts",
            page["access_token"],
            {
                "fields": "id,message,created_time,permalink_url",
                "limit": 25
            }
        )

        return render_template_string("""
        <h1>{{ page_name }} - Posts</h1>

        {% for post in posts %}
          <div style="border:1px solid #ccc;padding:15px;margin:10px">
            <p>{{ post.message or "No text" }}</p>
            <small>{{ post.created_time }}</small>
            {% if post.permalink_url %}
              <br>
              <a href="{{ post.permalink_url }}" target="_blank">
                Open Post
              </a>
            {% endif %}
          </div>
        {% else %}
          <p>No posts found.</p>
        {% endfor %}

        <a href="/facebook">Back to Facebook Pages</a>
        """,
            page_name=page.get("name", "Facebook Page"),
            posts=result.get("data", [])
        )

    except Exception as error:
        app.logger.exception("Facebook post retrieval failed.")
        return "Unable to load Facebook posts: " + str(error), 500


# =========================================================
# FACEBOOK PUBLISHING
# =========================================================

@app.route("/facebook/publish", methods=["GET", "POST"])
@login_required
def facebook_publish():

    user_token = session.get("fb_access_token")

    if not user_token:
        return redirect(url_for("facebook_login"))

    # Load the current Pages from Facebook each time.
    try:
        pages = get_facebook_pages(user_token)
    except Exception as error:
        app.logger.exception("Could not load Pages for publishing.")
        return (
            "Could not load Facebook Pages. Please reconnect. "
            + str(error),
            500
        )

    available_pages = [
        {
            "id": page.get("id"),
            "name": page.get("name")
        }
        for page in pages
        if page.get("id") and page.get("access_token")
    ]

    if not available_pages:
        return (
            "No Page access token is available. "
            "Reconnect Facebook and check Page permissions.",
            400
        )

    if request.method == "POST":
        page_id = request.form.get("page_id", "").strip()
        message = request.form.get("message", "").strip()

        if not page_id:
            return "Please select a Facebook Page.", 400

        if not message:
            return "Please enter a post message.", 400

        # Validate that the selected Page belongs to this account
        # and retrieve its current Page access token.
        try:
            page = get_selected_page(page_id)
        except Exception as error:
            app.logger.exception("Selected Page could not be verified.")
            return (
                "Facebook Page verification failed: " + str(error),
                400
            )

        try:
            # Text-only publishing.
            # Article images are shown in the preview but are not
            # automatically republished without image-use permission.
            result = facebook_request(
                f"{page_id}/feed",
                page["access_token"],
                {"message": message},
                method="POST"
            )

            return render_template_string("""
            <!DOCTYPE html>
            <html>
            <head>
              <title>Post Published</title>
              <meta name="viewport" content="width=device-width, initial-scale=1">
            </head>
            <body style="font-family:Arial;padding:25px">
              <h1 style="color:green">
                Facebook Post Published Successfully!
              </h1>
              <p>Page: {{ page_name }}</p>
              <p>Post ID: {{ post_id }}</p>
              <p>
                <a href="https://www.facebook.com/{{ post_id }}"
                   target="_blank">
                  Try opening your Facebook post
                </a>
              </p>
              <p><a href="/facebook">Back to Facebook Pages</a></p>
              <p><a href="/dashboard">Dashboard</a></p>
            </body>
            </html>
            """,
                page_name=page.get("name", "Facebook Page"),
                post_id=result.get("id", "Not returned")
            )

        except Exception as error:
            app.logger.exception("Facebook publishing failed.")
            return (
                "Facebook could not publish the post: " + str(error),
                500
            )

    selected_page_id = request.args.get("page_id", "").strip()

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Publish Facebook Post</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        body {
          font-family:Arial;background:#f3f5f8;padding:20px;
        }
        .card {
          max-width:750px;margin:auto;background:white;
          padding:25px;border-radius:10px;
        }
        textarea,select {
          width:100%;box-sizing:border-box;padding:12px;
          margin-top:10px;
        }
        textarea {min-height:260px}
        button {
          background:#1877f2;color:white;border:0;
          padding:13px 20px;border-radius:5px;cursor:pointer;
        }
      </style>
    </head>
    <body>
      <div class="card">
        <h1>Publish Facebook Post</h1>

        <form method="POST">
          <label>Select Facebook Page</label>
          <select name="page_id" required>
            <option value="">Choose your Page</option>
            {% for page in pages %}
              <option value="{{ page.id }}"
                {% if page.id == selected_page_id %}selected{% endif %}>
                {{ page.name }}
              </option>
            {% endfor %}
          </select>

          <br><br>

          <label>Facebook Post Message</label>
          <textarea name="message"
            placeholder="Write your Facebook post..."
            required>{{ draft }}</textarea>

          <br><br>

          <button type="submit">Publish Facebook Post</button>
        </form>

        <p><a href="/facebook">Back to Facebook Pages</a></p>
        <p><a href="/dashboard">Dashboard</a></p>
      </div>
    </body>
    </html>
    """,
        pages=available_pages,
        selected_page_id=selected_page_id,
        draft=request.args.get("message", "")
    )


# =========================================================
# DISCONNECT FACEBOOK
# =========================================================

@app.route("/facebook/logout")
@login_required
def facebook_logout():
    session.pop("fb_access_token", None)
    session.pop("fb_oauth_state", None)
    return redirect(url_for("dashboard"))


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route("/health")
def health():
    return {
        "status": "ok",
        "app": "NayaDrishti AI",
        "groq_configured": bool(groq_client),
        "facebook_connected": bool(session.get("fb_access_token"))
    }


# =========================================================
# RUN APPLICATION
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )




   
