import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-before-deployment")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-this-password")


@app.route("/", methods=["GET", "POST"])
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    result = None

    if request.method == "POST":
        topic = request.form.get("topic", "").strip()

        if topic:
            key = os.environ.get("OPENAI_API_KEY")

            if not key:
                flash("OpenAI API key is not configured yet.")
            else:
                try:
                    client = OpenAI(api_key=key)

                    response = client.responses.create(
                        model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
                        tools=[{"type": "web_search"}],
                        input=(
                            "Research this news topic using reliable sources. "
                            "Do not invent facts. Give a concise Nepali summary and "
                            "a concise English summary, with source links. Topic: "
                            + topic
                        )
                    )

                    result = response.output_text

                except Exception:
                    app.logger.exception("OpenAI research request failed")
                    flash("AI request failed. Please check Render logs.")

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
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
