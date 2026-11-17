
import os
import secrets
import requests
import feedparser

from functools import wraps
from urllib.parse import urlencode

from flask import (
    Flask, request, redirect, url_for,
    session, flash, render_template_string
)
from groq import Groq

app = Flask(__name__)

# ---------------- CONFIGURATION ----------------

app.secret_key = os.environ.get("FLASK_SECRET_KEY")

if not app.secret_key:
    raise RuntimeError("Set FLASK_SECRET_KEY in Render.")

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

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax"
)

# ---------------- RSS NEWS SOURCES ----------------

NEWS_SOURCES = [
    {
        "name": "Routine of Nepal Banda",
        "url": "https://www.ronbpost.com/feed/"
    },
    {
        "name": "ICC",
        "url": "https://www.icc-cricket.com/index?feed=rss2"
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


def collect_news():
    """Fetch recent articles from the configured RSS feeds."""

    articles = []
    errors = []

    headers = {
        "User-Agent": "NayaDrishtiAI/1.0 (RSS news reader)"
    }

    for source in NEWS_SOURCES:
        try:
            response = requests.get(
                source["url"],
                headers=headers,
                timeout=15
            )

            response.raise_for_status()

            parsed_feed = feedparser.parse(response.content)

            if not parsed_feed.entries:
                errors.append(
                    f"{source['name']}: Feed opened, "
                    "but no articles were found."
                )
                continue

            for entry in parsed_feed.entries[:10]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()

                if not title or not link:
                    continue

                articles.append({
                    "source": source["name"],
                    "title": title,
                    "link": link,
                    "published": entry.get(
                        "published",
                        entry.get("updated", "Date not available")
                    ),
                    "summary": entry.get(
                        "summary",
                        "No description available."
                    )
                })

        except requests.RequestException as error:
            errors.append(
                f"{source['name']}: Could not connect to feed."
            )

        except Exception:
            errors.append(
                f"{source['name']}: Could not read this feed."
            )

    return articles, errors


# ---------------- FACEBOOK API ----------------

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
        raise Exception(
            f"Facebook returned HTTP {response.status_code} "
            "with an unreadable response."
        )

    if not isinstance(result, dict):
        raise Exception("Facebook returned an unexpected response format.")

    if response.status_code >= 400 or "error" in result:
        error = result.get("error", {})
        raise Exception(
            f"{error.get('message', 'Facebook API error')} "
            f"(code: {error.get('code', 'unknown')}, "
            f"subcode: {error.get('error_subcode', 'unknown')})"
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

    for page_number in range(50):

        if next_url:
            if next_url in visited_urls:
                raise Exception("Facebook returned repeated pagination URLs.")

            visited_urls.add(next_url)

            response = requests.get(
                next_url,
                timeout=30
            )

            try:
                result = response.json()
            except ValueError:
                raise Exception("Facebook pagination returned invalid JSON.")

            if not isinstance(result, dict):
                raise Exception("Unexpected Facebook pagination response.")

            if response.status_code >= 400 or "error" in result:
                error = result.get("error", {})
                raise Exception(
                    f"{error.get('message', 'Pagination error')} "
                    f"(code: {error.get('code', 'unknown')})"
                )

        else:
            result = facebook_request(
                endpoint,
                token,
                params
            )

        data = result.get("data")

        if data is None:
            raise Exception(
                "Facebook returned data=null instead of a Page list. "
                "Check Page access and API permissions."
            )

        if not isinstance(data, list):
            raise Exception(
                "Facebook returned an unexpected data format: "
                + type(data).__name__
            )

        results.extend(
            item for item in data if isinstance(item, dict)
        )

        next_url = result.get("paging", {}).get("next")

        if not next_url:
            return results

        params = None

    raise Exception("Stopped after 50 pagination requests for safety.")


def discover_facebook_pages(user_token):
    diagnostics = []

    pages = get_all_pages(
        "me/accounts",
        user_token,
        "id,name,access_token,tasks"
    )

    diagnostics.append(
        f"Direct Page lookup: {len(pages)} Page(s) found."
    )

    valid_pages = []

    for page in pages:
        if not page.get("id") or not page.get("name"):
            diagnostics.append(
                "A Page response was missing its ID or name."
            )
            continue

        valid_pages.append(page)

        if not page.get("access_token"):
            diagnostics.append(
                f"{page['name']}: Facebook did not return a Page token."
            )

    diagnostics.append(
        "Business Portfolio lookup skipped; direct Page access is being tested."
    )

    return valid_pages, diagnostics


# ---------------- LOGIN ----------------

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
    <h2>NayaDrishti AI Login</h2>

    {% for message in get_flashed_messages() %}
      <p style="color:red">{{ message }}</p>
    {% endfor %}

    <form method="POST">
      <input type="password" name="password"
             placeholder="Admin password" required>
      <button type="submit">Login</button>
    </form>
    """)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- DASHBOARD ----------------

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template_string("""
    <h1>Welcome to NayaDrishti AI</h1>
    <p>Your AI assistant dashboard</p>

    <p><a href="/news">Fetch News</a></p>
    <p><a href="/facebook/login">Connect Facebook</a></p>
    <p><a href="/facebook">Facebook Pages</a></p>
    <p><a href="/ai">AI News Summary</a></p>
    <p><a href="/logout">Logout</a></p>
    """)


# ---------------- FETCH NEWS ----------------

@app.route("/news")
@login_required
def news():
    articles, errors = collect_news()

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
      <title>NayaDrishti AI - News</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        body {
          font-family: Arial, sans-serif;
          margin: 25px;
          background: #f4f6f9;
        }
        .article {
          background: white;
          padding: 15px;
          margin-bottom: 15px;
          border-radius: 8px;
          border: 1px solid #ddd;
        }
        .error {
          color: #b00020;
          background: #fff0f0;
          padding: 10px;
          margin: 8px 0;
        }
        a {
          color: #0757a6;
        }
        button {
          padding: 10px 18px;
          cursor: pointer;
        }
      </style>
    </head>
    <body>

      <h1>NayaDrishti AI - Latest News</h1>

      <p>Articles collected: <b>{{ articles|length }}</b></p>

      <p>
        <a href="/news">
          <button type="button">Refresh News</button>
        </a>
      </p>

      <h2>Feed status</h2>

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
          <small><b>{{ article.source }}</b></small>

          <h3>{{ article.title }}</h3>

          <p>{{ article.published }}</p>

          <p>{{ article.summary|striptags|truncate(500) }}</p>

          <a href="{{ article.link }}"
             target="_blank" rel="noopener noreferrer">
             Read Original Article
          </a>
        </div>
      {% else %}
        <p>No articles were collected. Check the feed status above.</p>
      {% endfor %}

      <p><a href="/dashboard">Back to Dashboard</a></p>

    </body>
    </html>
    """, articles=articles, errors=errors)


# ---------------- AI NEWS SUMMARY ----------------

@app.route("/ai", methods=["GET", "POST"])
@login_required
def ai():
    answer = ""

    if request.method == "POST":
        news_text = request.form.get("news", "").strip()

        if not groq_client:
            answer = "Groq API key is not configured."

        elif news_text:
            try:
                result = groq_client.chat.completions.create(
                    model="openai/gpt-oss-120b",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are NayaDrishti AI. Summarize news "
                                "clearly in simple Nepali or English, "
                                "depending on the input language. "
                                "Do not invent facts."
                            )
                        },
                        {"role": "user", "content": news_text}
                    ]
                )

                answer = result.choices[0].message.content

            except Exception as error:
                answer = f"AI error: {error}"

    return render_template_string("""
    <h2>NayaDrishti AI News Summary</h2>

    <form method="POST">
      <textarea name="news" rows="10" cols="60"
        placeholder="Paste your news here..." required></textarea><br>
      <button type="submit">Summarize</button>
    </form>

    <h3>Summary</h3>
    <pre style="white-space:pre-wrap">{{ answer }}</pre>

    <a href="/dashboard">Back to dashboard</a>
    """, answer=answer)


# ---------------- FACEBOOK LOGIN ----------------

@app.route("/facebook/login")
@login_required
def facebook_login():
    if not FB_APP_ID or not FB_APP_SECRET:
        return "FB_APP_ID or FB_APP_SECRET is missing in Render.", 500

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


# ---------------- FACEBOOK CALLBACK ----------------

@app.route("/facebook/callback")
@login_required
def facebook_callback():
    error = request.args.get("error")

    if error:
        description = request.args.get(
            "error_description", error
        )
        return f"Facebook login failed: {description}", 400

    returned_state = request.args.get("state")
    expected_state = session.pop("fb_oauth_state", None)

    if not expected_state or not secrets.compare_digest(
        returned_state or "", expected_state
    ):
        return "Invalid Facebook login state. Please reconnect.", 400

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

        if response.status_code >= 400 or "access_token" not in token_data:
            return "Facebook token exchange failed.", 400

        session["fb_access_token"] = token_data["access_token"]
        session.pop("fb_pages", None)

        return redirect(url_for("facebook_pages"))

    except Exception:
        return "Facebook connection failed. Please try again.", 500


# ---------------- FACEBOOK PAGES ----------------

@app.route("/facebook")
@login_required
def facebook_pages():
    token = session.get("fb_access_token")

    if not token:
        return redirect(url_for("facebook_login"))

    try:
        pages, diagnostics = discover_facebook_pages(token)

        session["fb_pages"] = pages
        session["fb_diagnostics"] = diagnostics

        return render_template_string("""
        <h1>Your Facebook Pages</h1>

        {% for page in pages %}
          <div style="border:1px solid #ccc;padding:15px;margin:10px">
            <h3>{{ page.name }}</h3>
            <p>Page ID: {{ page.id }}</p>

            {% if page.access_token %}
              <p style="color:green">Page access token available</p>
              <a href="/facebook/posts/{{ page.id }}">View Posts</a>
              |
              <a href="/facebook/publish">Publish Post</a>
            {% else %}
              <p style="color:red">
                Page found, but no Page access token was returned.
              </p>
            {% endif %}
          </div>
        {% else %}
          <p style="color:red">No Facebook Pages were returned.</p>
        {% endfor %}

        <h3>Facebook API diagnostics</h3>

        {% for item in diagnostics %}
          <p>{{ item }}</p>
        {% endfor %}

        <p><a href="/facebook/debug">Permission details</a></p>
        <p><a href="/facebook/logout">Disconnect Facebook</a></p>
        <p><a href="/dashboard">Dashboard</a></p>
        """, pages=pages, diagnostics=diagnostics)

    except Exception as error:
        return render_template_string("""
        <h2>Facebook Page retrieval failed</h2>
        <p style="color:red">{{ error }}</p>
        <a href="/facebook/debug">Check permissions</a><br>
        <a href="/dashboard">Dashboard</a>
        """, error=str(error)), 500


# ---------------- FACEBOOK PERMISSION DEBUG ----------------

@app.route("/facebook/debug")
@login_required
def facebook_debug():
    token = session.get("fb_access_token")

    if not token:
        return redirect(url_for("facebook_login"))

    try:
        result = facebook_request("me/permissions", token)
        permissions = result.get("data") or []

        return render_template_string("""
        <h1>Facebook Permission Details</h1>

        {% for permission in permissions %}
          <p>
            <b>{{ permission.permission }}</b> :
            {{ permission.status }}
          </p>
        {% endfor %}

        <a href="/facebook">Back to Facebook Pages</a>
        """, permissions=permissions)

    except Exception as error:
        return f"Permission check failed: {error}", 500


# ---------------- VIEW PAGE POSTS ----------------

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
            <small>{{ post.created_time }}</small><br>

            {% if post.permalink_url %}
              <a href="{{ post.permalink_url }}" target="_blank">
                Open Post
              </a>
            {% endif %}
          </div>
        {% else %}
          <p>No posts found.</p>
        {% endfor %}

        <a href="/facebook">Back to Pages</a>
        """, page=page, posts=result.get("data") or [])

    except Exception as error:
        return f"Unable to load posts: {error}", 500


# ---------------- PUBLISH FACEBOOK POST ----------------

@app.route("/facebook/publish", methods=["GET", "POST"])
@login_required
def facebook_publish():
    pages = session.get("fb_pages", [])

    if not pages:
        return redirect(url_for("facebook_pages"))

    if request.method == "POST":
        page_id = request.form.get("page_id")
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
                {"message": message},
                method="POST"
            )

            return (
                "Post published successfully. "
                f"Post ID: {result.get('id')} "
                '<p><a href="/facebook">Back to Pages</a></p>'
            )

        except Exception as error:
            return f"Publishing failed: {error}", 500

    return render_template_string("""
    <h1>Publish Facebook Post</h1>

    <form method="POST">
      <label>Select Page</label><br>

      <select name="page_id" required>
        {% for page in pages %}
          <option value="{{ page.id }}">{{ page.name }}</option>
        {% endfor %}
      </select><br><br>

      <textarea name="message" rows="8" cols="50"
        placeholder="Write your Facebook post..." required></textarea><br>

      <button type="submit">Publish Post</button>
    </form>

    <a href="/facebook">Back to Pages</a>
    """ , pages=pages)


# ---------------- DISCONNECT FACEBOOK ----------------

@app.route("/facebook/logout")
@login_required
def facebook_logout():
    session.pop("fb_access_token", None)
    session.pop("fb_pages", None)
    session.pop("fb_diagnostics", None)
    session.pop("fb_oauth_state", None)

    return redirect(url_for("dashboard"))


# ---------------- HEALTH CHECK ----------------

@app.route("/health")
def health():
    return {
        "status": "ok",
        "app": "NayaDrishti AI"
    }


# ---------------- RUN APPLICATION ----------------

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
