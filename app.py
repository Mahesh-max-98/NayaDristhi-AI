
import os
import secrets
import requests
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
    raise RuntimeError("Set FLASK_SECRET_KEY in Render environment variables.")

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

    if response.status_code >= 400 or "error" in result:
        error = result.get("error", {})
        raise Exception(
            f"{error.get('message', 'Facebook API error')} "
            f"(code: {error.get('code', 'unknown')}, "
            f"subcode: {error.get('error_subcode', 'unknown')})"
        )

    return result


def get_all_pages(endpoint, token, fields):
    """Read every page of a Graph API connection."""
    results = []
    params = {"fields": fields, "limit": 100}

    while True:
        result = facebook_request(endpoint, token, params)
        results.extend(result.get("data", []))

        next_url = result.get("paging", {}).get("next")
        if not next_url:
            break

        response = requests.get(next_url, timeout=30)

        try:
            result = response.json()
        except ValueError:
            break

        if response.status_code >= 400 or "error" in result:
            break

        # Continue with the next page using its returned URL.
        params = None
        endpoint = None

        while True:
            results.extend(result.get("data", []))
            next_url = result.get("paging", {}).get("next")
            if not next_url:
                return results

            response = requests.get(next_url, timeout=30)
            result = response.json()
            if response.status_code >= 400 or "error" in result:
                return results


def discover_facebook_pages(user_token):
    pages = {}
    diagnostics = []

    # Method 1: Pages directly available to the Facebook user.
    try:
        direct_pages = get_all_pages(
            "me/accounts",
            user_token,
            "id,name,access_token,tasks"
        )

        for page in direct_pages:
            pages[page["id"]] = page

        diagnostics.append(
            f"Direct Page lookup: {len(direct_pages)} Page(s) found."
        )

    except Exception as error:
        diagnostics.append(f"Direct Page lookup: {error}")

    # Method 2: Look through the user's Business Portfolios.
    try:
        businesses = get_all_pages(
            "me/businesses",
            user_token,
            "id,name"
        )

        diagnostics.append(
            f"Business Portfolios found: {len(businesses)}."
        )

    except Exception as error:
        businesses = []
        diagnostics.append(f"Business lookup: {error}")

    # Method 3: Check Pages owned by or assigned to each business.
    for business in businesses:
        business_id = business.get("id")
        business_name = business.get("name", "Business")

        for edge in ("owned_pages", "client_pages"):
            try:
                business_pages = get_all_pages(
                    f"{business_id}/{edge}",
                    user_token,
                    "id,name,access_token,tasks"
                )

                diagnostics.append(
                    f"{business_name} / {edge}: "
                    f"{len(business_pages)} Page(s)."
                )

                for page in business_pages:
                    pages[page["id"]] = page

            except Exception as error:
                diagnostics.append(
                    f"{business_name} / {edge}: {error}"
                )

    # Method 4: Try to obtain a Page token if one was not returned.
    for page_id, page in pages.items():
        if not page.get("access_token"):
            try:
                details = facebook_request(
                    page_id,
                    user_token,
                    {"fields": "id,name,access_token"}
                )
                page.update(details)
            except Exception as error:
                diagnostics.append(
                    f"Page {page_id} token lookup: {error}"
                )

    return list(pages.values()), diagnostics


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
    <h2>NayaDristhi AI Login</h2>
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
    <h1>Welcome to NayaDristhi AI</h1>
    <p>Your AI assistant dashboard</p>
    <p><a href="/facebook/login">Connect Facebook</a></p>
    <p><a href="/facebook">Facebook Pages</a></p>
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
                                "depending on the input language."
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
        # Use your Facebook Login for Business configuration.
        params["config_id"] = FB_CONFIG_ID
    else:
        # Standard Facebook Login fallback.
        params["scope"] = ",".join([
            "pages_show_list",
            "pages_read_engagement",
            "pages_manage_posts",
            "business_management"
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
            return (
                "Facebook token error: "
                + str(token_data.get("error", token_data)),
                400
            )

        session["fb_access_token"] = token_data["access_token"]
        session.pop("fb_pages", None)

        return redirect(url_for("facebook_pages"))

    except Exception as error:
        return f"Facebook connection error: {error}", 500


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
            {% else %}
              <p style="color:red">
                Page found, but no Page access token was returned.
              </p>
            {% endif %}
          </div>
        {% else %}
          <p style="color:red">
            No Facebook Pages were returned. See diagnostics below.
          </p>
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
        return f"Facebook error: {error}", 500


# ---------------- FACEBOOK PERMISSION DEBUG ----------------

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

        permissions = result.get("data", [])

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
        """, page=page, posts=result.get("data", []))

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

    <a href="/facebook">Back to Facebook Pages</a>
    """, pages=pages)


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
        "app": "NayaDristhi AI"
    }


# ---------------- RUN APPLICATION ----------------

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )

  
      



       

  
        


      

    
      
            

           
            
  


   
            
                          
                          
                

                       
                   
    
  
        
