import os
import secrets
import requests
from functools import wraps
from flask import (
    Flask, render_template_string, request,
    redirect, url_for, session, flash
)
from groq import Groq

app = Flask(__name__)

# ---------------- CONFIGURATION ----------------

app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-this-password")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

FB_APP_ID = os.environ.get("FB_APP_ID")
FB_APP_SECRET = os.environ.get("FB_APP_SECRET")
APP_BASE_URL = os.environ.get(
    "APP_BASE_URL",
    "https://nayadristhi-ai.onrender.com"
).rstrip("/")

GRAPH_VERSION = "v27.0"
REDIRECT_URI = APP_BASE_URL + "/facebook/callback"

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


# ---------------- FACEBOOK API ----------------

def facebook_request(endpoint, token, params=None, method="GET"):
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{endpoint}"

    data = dict(params or {})
    data["access_token"] = token

    response = requests.request(
        method,
        url,
        params=data if method == "GET" else None,
        data=data if method != "GET" else None,
        timeout=30
    )

    result = response.json()

    if response.status_code >= 400 or "error" in result:
        message = result.get("error", {}).get("message", "Facebook API error")
        raise Exception(message)

    return result


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

        flash("Incorrect password")

    return render_template_string("""
    <h2>NayaDristhi AI Login</h2>
    {% for message in get_flashed_messages() %}
      <p style="color:red">{{ message }}</p>
    {% endfor %}
    <form method="POST">
      <input type="password" name="password" placeholder="Admin password" required>
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
    <h1>Welcome to NayaDristhi AI</h1>
    <p>Your AI assistant dashboard</p>
    <p><a href="/facebook/login">Connect Facebook</a></p>
    <p><a href="/ai">AI News Summary</a></p>
    <p><a href="/logout">Logout</a></p>
    """)


# ---------------- AI NEWS SUMMARY ----------------

@app.route("/ai", methods=["GET", "POST"])
@login_required
def ai():
    answer = ""

    if request.method == "POST":
        news = request.form.get("news", "").strip()

        if not groq_client:
            answer = "Groq API key is not configured."
        elif news:
            try:
                result = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are NayaDristhi AI. Summarize news "
                                "clearly in simple Nepali or English, "
                                "depending on the language of the input."
                            )
                        },
                        {"role": "user", "content": news}
                    ]
                )
                answer = result.choices[0].message.content
            except Exception as error:
                answer = f"AI error: {error}"

    return render_template_string("""
    <h2>NayaDristhi AI News Summary</h2>
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
        return "Facebook App ID or App Secret is not configured.", 500

    state = secrets.token_urlsafe(32)
    session["fb_oauth_state"] = state

    permissions = ",".join([
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_posts"
    ])

    url = (
        f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
        f"?client_id={FB_APP_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&state={state}"
        f"&scope={permissions}"
    )

    return redirect(url)


# ---------------- FACEBOOK CALLBACK ----------------

@app.route("/facebook/callback")
@login_required
def facebook_callback():
    error = request.args.get("error")
    if error:
        return f"Facebook login failed: {error}"

    returned_state = request.args.get("state")
    expected_state = session.pop("fb_oauth_state", None)

    if not expected_state or not secrets.compare_digest(
        returned_state or "", expected_state
    ):
        return "Invalid Facebook login state.", 400

    code = request.args.get("code")
    if not code:
        return "Facebook authorization code missing.", 400

    try:
        token_data = requests.get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/oauth/access_token",
            params={
                "client_id": FB_APP_ID,
                "client_secret": FB_APP_SECRET,
                "redirect_uri": REDIRECT_URI,
                "code": code
            },
            timeout=30
        ).json()

        if "access_token" not in token_data:
            return f"Token error: {token_data}"

        session["fb_access_token"] = token_data["access_token"]

        return redirect(url_for("facebook_pages"))

    except Exception as error:
        return f"Facebook connection error: {error}"


# ---------------- FACEBOOK PAGES ----------------

@app.route("/facebook")
@login_required
def facebook_pages():
    token = session.get("fb_access_token")

    if not token:
        return redirect(url_for("facebook_login"))

    try:
        result = facebook_request(
            "me/accounts",
            token,
            {"fields": "id,name,access_token,tasks"}
        )

        session["fb_pages"] = result.get("data", [])

        return render_template_string("""
        <h1>Your Facebook Pages</h1>

        {% for page in pages %}
          <div style="border:1px solid #ccc;padding:15px;margin:10px">
            <h3>{{ page.name }}</h3>
            <a href="/facebook/posts/{{ page.id }}">View Posts</a>
          </div>
        {% else %}
          <p>No Facebook Pages found or permission not granted.</p>
        {% endfor %}

        <a href="/facebook/logout">Disconnect Facebook</a><br>
        <a href="/dashboard">Dashboard</a>
        """, pages=session["fb_pages"])

    except Exception as error:
        return f"Facebook error: {error}"


# ---------------- VIEW PAGE POSTS ----------------

@app.route("/facebook/posts/<page_id>")
@login_required
def facebook_posts(page_id):
    pages = session.get("fb_pages", [])

    page = next(
        (p for p in pages if p.get("id") == page_id),
        None
    )

    if not page:
        return "Page not found. Please reconnect Facebook.", 404

    try:
        result = facebook_request(
            f"{page_id}/posts",
            page["access_token"],
            {"fields": "id,message,created_time,permalink_url"}
        )

        return render_template_string("""
        <h1>{{ page.name }} - Posts</h1>

        {% for post in posts %}
          <div style="border:1px solid #ccc;padding:15px;margin:10px">
            <p>{{ post.message or "No text" }}</p>
            <small>{{ post.created_time }}</small><br>
            {% if post.permalink_url %}
              <a href="{{ post.permalink_url }}" target="_blank">Open Post</a>
            {% endif %}
          </div>
        {% else %}
          <p>No posts found.</p>
        {% endfor %}

        <a href="/facebook">Back to Pages</a>
        """, page=page, posts=result.get("data", []))

    except Exception as error:
        return f"Unable to load posts: {error}"


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

        if not page:
            return "Invalid Facebook Page.", 400

        if not message:
            return "Please enter a post message.", 400

        try:
            result = facebook_request(
                f"{page_id}/feed",
                page["access_token"],
                {"message": message},
                method="POST"
            )

            return f"Post published successfully. Post ID: {result.get('id')}"

        except Exception as error:
            return f"Publishing failed: {error}"

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

    <a href="/facebook">Back to Facebook Pages</a>
    """, pages=pages)


# ---------------- DISCONNECT FACEBOOK ----------------

@app.route("/facebook/logout")
@login_required
def facebook_logout():
    session.pop("fb_access_token", None)
    session.pop("fb_pages", None)
    return redirect(url_for("dashboard"))


# ---------------- HEALTH CHECK ----------------

@app.route("/health")
def health():
    return {"status": "ok", "app": "NayaDristhi AI"}


# ---------------- RUN APPLICATION ----------------

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )



   
            
                          
                          
                

                       
                   
    
  
        
