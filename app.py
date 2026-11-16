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

from google import genai
from google.genai import types


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

            key = os.environ.get("GEMINI_API_KEY")

            if not key:
                flash("Gemini API key is not configured.")

            else:

                try:

                    client = genai.Client(api_key=key)

                    response = client.models.generate_content(
                        model=os.environ.get(
                            "GEMINI_MODEL",
                            "gemini-2.5-flash"
                        ),

                        contents=(
                            "You are NayaDrishti AI, an AI-powered "
                            "news research assistant. "
                            "Research the following news topic using "
                            "Google Search and reliable sources. "
                            "Do not invent facts. "
                            "Clearly distinguish confirmed facts "
                            "from uncertain information. "
                            "Provide a concise Nepali summary and "
                            "a concise English summary. "
                            "Include relevant source links. "
                            "Mention dates where relevant. "
                            "News topic: " + topic
                        ),

                        config=types.GenerateContentConfig(
                            tools=[
                                types.Tool(
                                    google_search=types.GoogleSearch()
                                )
                            ]
                        )
                    )

                    result = response.text or (
                        "The AI did not return any text. "
                        "Please try another topic."
                    )

                    # Add source links from Google's search results.
                    sources = []

                    if response.candidates:
                        metadata = (
                            response.candidates[0].grounding_metadata
                        )

                        if metadata and metadata.grounding_chunks:

                            for chunk in metadata.grounding_chunks:

                                if chunk.web and chunk.web.uri:

                                    source_title = (
                                        chunk.web.title or "Source"
                                    )

                                    source_url = chunk.web.uri

                                    source_line = (
                                        f"- {source_title}: {source_url}"
                                    )

                                    if source_line not in sources:
                                        sources.append(source_line)

                    if sources:
                        result += "\n\nSources:\n"
                        result += "\n".join(sources)

                except Exception:

                    app.logger.exception(
                        "Gemini research request failed"
                    )

                    flash(
                        "Gemini AI request failed. "
                        "Please check your API key, model and Render logs."
                    )

    return render_template("index.html", result=result)


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

            
                   


            
        
