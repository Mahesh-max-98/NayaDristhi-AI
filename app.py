import os

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash
)

from groq import Groq
from groq import APIError, AuthenticationError, RateLimitError


app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "change-this-secret"
)

ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    "change-this-password"
)


@app.route("/", methods=["GET", "POST"])
def index():

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    result = None

    if request.method == "POST":

        topic = request.form.get("topic", "").strip()

        if not topic:
            flash("Please enter a news topic.")

        else:

            key = os.environ.get("GROQ_API_KEY")

            if not key:
                flash("Groq API key is not configured.")

            else:

                try:

                    client = Groq(api_key=key)

                    response = client.chat.completions.create(

                        model=os.environ.get(
                            "GROQ_MODEL",
                            "openai/gpt-oss-20b"
                        ),

                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You are NayaDrishti AI, an "
                                    "AI-powered news research assistant. "
                                    "Research news topics carefully. "
                                    "Use reliable and recent sources. "
                                    "Never invent facts, dates, quotes "
                                    "or source links. "
                                    "Clearly separate verified facts "
                                    "from uncertain information. "
                                    "Provide a concise Nepali summary "
                                    "and a concise English summary. "
                                    "Include source links and publication "
                                    "dates whenever available."
                                )
                            },
                            {
                                "role": "user",
                                "content": (
                                    "Research this news topic using "
                                    "current web information. "
                                    "Find reliable sources and explain "
                                    "the important developments. "
                                    "News topic: " + topic
                                )
                            }
                        ],

                        tools=[
                            {"type": "browser_search"}
                        ],

                        tool_choice="auto",

                        temperature=1,

                        max_completion_tokens=4096,

                        reasoning_effort="low",

                        citation_options="enabled"
                    )

                    result = (
                        response.choices[0].message.content
                        or "No research result was returned. "
                           "Please try again."
                    )

                except AuthenticationError:

                    app.logger.exception(
                        "Groq authentication failed"
                    )

                    flash(
                        "Groq API key is invalid. "
                        "Please check GROQ_API_KEY in Render."
                    )

                except RateLimitError:

                    app.logger.exception(
                        "Groq rate limit reached"
                    )

                    flash(
                        "Groq API limit reached. "
                        "Please wait and try again."
                    )

                except APIError:

                    app.logger.exception(
                        "Groq API request failed"
                    )

                    flash(
                        "Groq service returned an error. "
                        "Please check Render logs."
                    )

                except Exception:

                    app.logger.exception(
                        "Unexpected Groq research error"
                    )

                    flash(
                        "Research failed unexpectedly. "
                        "Please check Render logs."
                    )

    return render_template(
        "index.html",
        result=result
    )


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        if request.form.get("password") == ADMIN_PASSWORD:

            session["logged_in"] = True

            return redirect(url_for("index"))

        flash("Incorrect password.")

    return render_template("login.html")


@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


@app.route("/health")
def health():

    return {"status": "ok"}


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )



            
        
