

import os
import re
import html
import secrets
import logging
import requests
import feedparser

from functools import wraps
from urllib.parse import urlencode

from flask import (
    Flask, request, redirect, url_for,
    session, flash, render_template_string
)

from groq import Groq


# =====================================================
# NAYADRISHTI AI - MAIN APPLICATION
# =====================================================

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)

# ---------------- CONFIGURATION ----------------

app.secret_key = os.environ.get("FLASK_SECRET_KEY")

if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY is missing in Render.")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-this-password")
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
    SESSION_COOKIE_SAMESITE="Lax"
)


# =====================================================
# RSS NEWS SOURCES
# =====================================================

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


def clean_text(value):
    """Remove HTML tags and clean RSS descriptions."""

    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def collect_news():
    """Collect articles from all configured RSS sources."""

    articles = []
    errors = []

    headers = {
        "User-Agent": "NayaDrishtiAI/1.0"
    }

    for source_item in NEWS_SOURCES:

        try:
            response = requests.get(
                source_item["url"],
                headers=headers,
                timeout=20
            )

            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if not feed.entries:
                errors.append(
                    source_item["name"] + ": No articles found."
                )
                continue

            for entry in feed.entries[:10]:

                title = clean_text(entry.get("title", ""))
                link = entry.get("link", "").strip()

                if not title or not link:
                    continue

                if not link.startswith(("https://", "http://")):
                    continue

                articles.append({
                    "source": source_item["name"],
                    "title": title,
                    "link": link,
                    "published": clean_text(
                        entry.get(
                            "published",
                            entry.get("updated", "Date not available")
                        )
                    ),
                    "summary": clean_text(
                        entry.get(
                            "summary",
                            "No description available."
                        )
                    )
                })

        except Exception as error:
            app.logger.warning(
                "RSS error for %s: %s",
                source_item["name"],
                str(error)
            )

            errors.append(
                source_item["name"] + ": Could not load feed."
            )

    return articles, errors


# =====================================================
# LOGIN SYSTEM
# =====================================================

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

        if secrets.compare_digest(password, ADMIN_PASSWORD):

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
                <input type="password"
                       name="password"
                       placeholder="Admin Password"
                       required>
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


# =====================================================
# DASHBOARD
# =====================================================

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
            body {
                font-family: Arial;
                background: #f3f5f8;
                padding: 25px;
            }
            .card {
                background: white;
                padding: 20px;
                margin: 15px 0;
                border-radius: 10px;
            }
            a {
                color: #0757a6;
                text-decoration: none;
            }
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


# =====================================================
# FETCH NEWS AND DISPLAY AI SUMMARY BUTTONS
# =====================================================

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
            }

            .article {
                background: white;
                padding: 20px;
                margin-bottom: 18px;
                border-radius: 10px;
                border: 1px solid #ddd;
            }

            .source {
                color: #1769aa;
                font-weight: bold;
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

            .original {
                background: #555;
            }

            .error {
                background: #ffe9e9;
                color: #b00020;
                padding: 10px;
                margin: 8px 0;
            }

            button {
                font-size: 14px;
            }
        </style>
    </head>

    <body>

        <h1>NayaDrishti AI - Latest News</h1>

        <p>
            Articles collected:
            <b>{{ articles|length }}</b>
        </p>

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

                <div class="source">
                    {{ article.source }}
                </div>

                <h3>{{ article.title }}</h3>

                <small>{{ article.published }}</small>

                <p>{{ article.summary|truncate(500) }}</p>

                <a class="button original"
                   href="{{ article.link }}"
                   target="_blank"
                   rel="noopener noreferrer">
                    Read Original Article
                </a>

                <form method="POST"
                      action="/news/summarize">

                    <input type="hidden"
                           name="title"
                           value="{{ article.title }}">

                    <input type="hidden"
                           name="news_source"
                           value="{{ article.source }}">

                    <input type="hidden"
                           name="description"
                           value="{{ article.summary }}">

                    <input type="hidden"
                           name="link"
                           value="{{ article.link }}">

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


# =====================================================
# AI NEWS SUMMARY - MAIN FEATURE
# =====================================================

@app.route("/news/summarize", methods=["POST"])
@login_required
def summarize_article():

    title = request.form.get("title", "").strip()
    news_source = request.form.get("news_source", "").strip()
    description = request.form.get("description", "").strip()
    link = request.form.get("link", "").strip()

    summary = ""
    error_message = ""

    if not title or not link:
        error_message = "Article title or original link is missing."

    elif not link.startswith(("https://", "http://")):
        error_message = "Invalid original article link."

    elif not groq_client:
        error_message = "Groq API key is not configured in Render."

    else:

        try:

            news_content = f"""
            News title: {title}

            News source: {news_source}

            News description: {description}

            Original article link: {link}
            """

            result = groq_client.chat.completions.create(

                model=GROQ_MODEL,

                messages=[
                    {
                        "role": "system",
                        "content": """
                        You are NayaDrishti AI, a professional news
                        editor for a Facebook news page.

                        Create an original, attractive Facebook-ready
                        news post based only on the information supplied.

                        Rules:

                        1. Write in simple, clear language.
                        2. Use the language of the original news.
                        3. Make the headline interesting.
                        4. Summarize the important facts.
                        5. Do not copy the original article word for word.
                        6. Never invent facts, numbers, quotes or events.
                        7. Do not claim to have independently verified
                           information.
                        8. Keep the post concise and readable.
                        9. Include the original source name.
                        10. Include the original article link.
                        11. Use a few relevant hashtags.
                        12. Do not add unsupported details.

                        Return only the finished Facebook post.
                        """
                    },
                    {
                        "role": "user",
                        "content": news_content
                    }
                ],

                temperature=0.5,
                max_tokens=800,
                timeout=60
            )

            summary = result.choices[0].message.content or ""

            if not summary.strip():
                error_message = "Groq returned an empty summary."

        except Exception as error:

            app.logger.exception("AI summary generation failed.")

            error_message = (
                "AI summary generation failed. "
                "Please check your Groq configuration and try again."
            )

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>NayaDrishti AI Summary</title>

        <meta name="viewport"
              content="width=device-width, initial-scale=1">

        <style>
            body {
                font-family: Arial;
                background: #f3f5f8;
                padding: 20px;
            }

            .container {
                background: white;
                padding: 25px;
                border-radius: 10px;
                max-width: 800px;
                margin: auto;
            }

            textarea {
                width: 100%;
                box-sizing: border-box;
                padding: 15px;
                min-height: 280px;
                font-family: Arial;
                font-size: 15px;
                line-height: 1.6;
            }

            input, button {
                padding: 12px;
                margin: 8px 0;
            }

            .button {
                background: #1769aa;
                color: white;
                border: none;
                cursor: pointer;
                text-decoration: none;
                display: inline-block;
                border-radius: 5px;
            }

            .error {
                color: #b00020;
                background: #ffe9e9;
                padding: 12px;
            }

            .success {
                color: #126b35;
            }
        </style>
    </head>

    <body>

        <div class="container">

            <h1>NayaDrishti AI</h1>

            <h2>AI News Summary</h2>

            {% if error_message %}

                <div class="error">
                    {{ error_message }}
                </div>

            {% else %}

                <p class="success">
                    AI summary generated successfully.
                </p>

            {% endif %}

            <h3>{{ title }}</h3>

            <p>
                <b>News source:</b> {{ news_source }}
            </p>

            <h3>Facebook Post Draft</h3>

            <form method="POST" action="/facebook/publish">

                <label>Generated post</label>

                <textarea name="message"
                          required>{{ summary }}

Source: {{ news_source }}
Original article: {{ link }}</textarea>

                <br>

                <button class="button" type="submit">
                    Continue to Facebook Publishing
                </button>

            </form>

            <p>
                <a href="{{ link }}"
                   target="_blank"
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
        error_message=error_message
    )


# =====================================================
# MANUAL AI SUMMARY PAGE
# =====================================================

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
                            "content": """
                            You are NayaDrishti AI.

                            Summarize news in simple Nepali or English,
                            depending on the input language.

                            Create original content.
                            Do not invent facts.
                            Preserve important information.
                            """
                        },
                        {
                            "role": "user",
                            "content": news_text
                        }
                    ],
                    temperature=0.5,
                    max_tokens=800,
                    timeout=60
                )

                answer = result.choices[0].message.content or ""

            except Exception:

                app.logger.exception("Manual AI summary failed.")

                answer = (
                    "AI summary failed. Check your Groq configuration "
                    "and Render logs."
                )

    return render_template_string("""
    <h1>NayaDrishti AI Summary</h1>

    <form method="POST">

        <textarea name="news"
                  rows="10"
                  style="width:90%"
                  placeholder="Paste news here..."
                  required></textarea>

        <br><br>

        <button type="submit">Generate Summary</button>

    </form>

    <h3>AI Summary</h3>

    <pre style="white-space:pre-wrap">{{ answer }}</pre>

    <a href="/dashboard">Back to Dashboard</a>
    """, answer=answer)


# =====================================================
# FACEBOOK API
# =====================================================

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


def get_all_pages(endpoint, token, fields):

    results = []

    params = {
        "fields": fields,
        "limit": 100
    }

    next_url = None
    visited_urls = set()

    for _ in range(50):

        if next_url:

            if next_url in visited_urls:
                raise Exception("Repeated Facebook pagination URL.")

            visited_urls.add(next_url)

            response = requests.get(
                next_url,
                timeout=30
            )

            result = response.json()

            if response.status_code >= 400 or "error" in result:
                raise Exception("Facebook pagination failed.")

        else:

            result = facebook_request(
                endpoint,
                token,
                params
            )

        data = result.get("data")

        if not isinstance(data, list):
            raise Exception("Facebook returned an invalid Page list.")

        results.extend(
            item for item in data
            if isinstance(item, dict)
        )

        next_url = result.get("paging", {}).get("next")

        if not next_url:
            return results

        params = None

    raise Exception("Facebook pagination safety limit reached.")


def discover_facebook_pages(user_token):

    pages = get_all_pages(
        "me/accounts",
        user_token,
        "id,name,access_token,tasks"
    )

    diagnostics = [
        f"Direct Page lookup: {len(pages)} Page(s) found."
    ]

    return pages, diagnostics


# =====================================================
# FACEBOOK LOGIN
# =====================================================

@app.route("/facebook/login")
@login_required
def facebook_login():

    if not FB_APP_ID or not FB_APP_SECRET:
        return "Facebook App ID or App Secret is missing.", 500

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


# =====================================================
# FACEBOOK CALLBACK
# =====================================================

@app.route("/facebook/callback")
@login_required
def facebook_callback():

    error = request.args.get("error")

    if error:
        return "Facebook login failed. Please try again.", 400

    returned_state = request.args.get("state")

    expected_state = session.pop("fb_oauth_state", None)

    if not expected_state or not secrets.compare_digest(
        returned_state or "",
        expected_state
    ):
        return "Invalid Facebook login state.", 400

    code = request.args.get("code")

    if not code:
        return "Facebook authorization code missing.", 400

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
            return "Facebook token exchange failed.", 400

        session["fb_access_token"] = token_data["access_token"]

        session.pop("fb_pages", None)

        return redirect(url_for("facebook_pages"))

    except Exception:

        app.logger.exception("Facebook callback failed.")

        return "Facebook connection failed.", 500


# =====================================================
# FACEBOOK PAGES
# =====================================================

@app.route("/facebook")
@login_required
def facebook_pages():

    token = session.get("fb_access_token")

    if not token:
        return redirect(url_for("facebook_login"))

    try:

        pages, diagnostics = discover_facebook_pages(token)

        session["fb_pages"] = pages

        return render_template_string("""
        <h1>Your Facebook Pages</h1>

        {% for page in pages %}

            <div style="border:1px solid #ccc;padding:15px;margin:10px">

                <h3>{{ page.name }}</h3>

                <p>Page ID: {{ page.id }}</p>

                {% if page.access_token %}

                    <p style="color:green">
                        Page access token available
                    </p>

                    <a href="/facebook/posts/{{ page.id }}">
                        View Posts
                    </a>

                    |

                    <a href="/facebook/publish">
                        Publish Post
                    </a>

                {% else %}

                    <p style="color:red">
                        Page access token unavailable.
                    </p>

                {% endif %}

            </div>

        {% else %}

            <p>No Facebook Pages found.</p>

        {% endfor %}

        <h3>Diagnostics</h3>

        {% for item in diagnostics %}
            <p>{{ item }}</p>
        {% endfor %}

        <p><a href="/facebook/debug">Permission Details</a></p>

        <p><a href="/facebook/logout">Disconnect Facebook</a></p>

        <p><a href="/dashboard">Dashboard</a></p>

        """, pages=pages, diagnostics=diagnostics)

    except Exception:

        app.logger.exception("Facebook Page retrieval failed.")

        return "Unable to retrieve Facebook Pages. Check Render logs.", 500


# =====================================================
# FACEBOOK PERMISSION DEBUG
# =====================================================

@app.route("/facebook/debug")
@login_required
def facebook_debug():

    token = session.get("fb_access_token")

    if not token:
        return redirect(url_for("facebook_login"))

    try:

        result = facebook_request(
            "me/permissions",
            token
        )

        permissions = result.get("data") or []

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

    except Exception:

        app.logger.exception("Facebook permission check failed.")

        return "Permission check failed.", 500


# =====================================================
# VIEW FACEBOOK POSTS
# =====================================================

@app.route("/facebook/posts/<page_id>")
@login_required
def facebook_posts(page_id):

    pages = session.get("fb_pages", [])

    page = next(
        (p for p in pages if p.get("id") == page_id),
        None
    )

    if not page or not page.get("access_token"):
        return "Page or Page access token not found.", 404

    try:

        result = facebook_request(
            f"{page_id}/posts",
            page["access_token"],
            {
                "fields": "id,message,created_time,permalink_url",
                "limit": 25
            }
        )

        return render_template_string("""
        <h1>{{ page.name }} - Posts</h1>

        {% for post in posts %}

            <div style="border:1px solid #ccc;padding:15px;margin:10px">

                <p>{{ post.message or "No text" }}</p>

                <small>{{ post.created_time }}</small>

                {% if post.permalink_url %}

                    <br>

                    <a href="{{ post.permalink_url }}"
                       target="_blank">
                        Open Post
                    </a>

                {% endif %}

            </div>

        {% else %}

            <p>No posts found.</p>

        {% endfor %}

        <a href="/facebook">Back to Pages</a>

        """, page=page, posts=result.get("data") or [])

    except Exception:

        app.logger.exception("Facebook post retrieval failed.")

        return "Unable to load Facebook posts.", 500


# =====================================================
# PUBLISH FACEBOOK POST
# =====================================================

@app.route("/facebook/publish", methods=["GET", "POST"])
@login_required
def facebook_publish():

    pages = session.get("fb_pages", [])

    if not pages:
        return redirect(url_for("facebook_pages"))

    if request.method == "POST":

        page_id = request.form.get("page_id", "")
        message = request.form.get("message", "").strip()

        page = next(
            (p for p in pages if p.get("id") == page_id),
            None
        )

        if not page or not page.get("access_token"):
            return "Invalid Page or missing Page access token.", 400

        if not message:
            return "Please enter a post message.", 400

        try:

            result = facebook_request(
                f"{page_id}/feed",
                page["access_token"],
                {
                    "message": message
                },
                method="POST"
            )

            return render_template_string("""
            <h1 style="color:green">
                Facebook Post Published Successfully!
            </h1>

            <p>Post ID: {{ post_id }}</p>

            <a href="/facebook">Back to Facebook Pages</a>

            <br><br>

            <a href="/dashboard">Dashboard</a>

            """, post_id=result.get("id"))

        except Exception:

            app.logger.exception("Facebook publishing failed.")

            return "Publishing failed. Check Render logs.", 500

    return render_template_string("""
    <h1>Publish Facebook Post</h1>

    <form method="POST">

        <label>Select Facebook Page</label>

        <br><br>

        <select name="page_id" required>

            {% for page in pages %}

                {% if page.access_token %}

                    <option value="{{ page.id }}">
                        {{ page.name }}
                    </option>

                {% endif %}

            {% endfor %}

        </select>

        <br><br>

        <textarea name="message"
                  rows="12"
                  cols="55"
                  placeholder="Write your Facebook post..."
                  required></textarea>

        <br><br>

        <button type="submit">
            Publish Facebook Post
        </button>

    </form>

    <p><a href="/facebook">Back to Facebook</a></p>

    """, pages=pages)


# =====================================================
# DISCONNECT FACEBOOK
# =====================================================

@app.route("/facebook/logout")
@login_required
def facebook_logout():

    session.pop("fb_access_token", None)
    session.pop("fb_pages", None)
    session.pop("fb_oauth_state", None)

    return redirect(url_for("dashboard"))


# =====================================================
# HEALTH CHECK
# =====================================================

@app.route("/health")
def health():

    return {
        "status": "ok",
        "app": "NayaDrishti AI",
        "groq_configured": bool(groq_client)
    }


# =====================================================
# RUN APPLICATION
# =====================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
