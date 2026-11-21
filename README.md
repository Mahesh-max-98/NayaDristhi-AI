# NayaDrishti AI

**नयाँ दृष्टि, नयाँ खबर।** — A Nepal-focused news research and bilingual drafting dashboard.

## Included
- Password-protected Flask dashboard.
- RSS collection from Nepali news, education/NEB, and cricket feeds.
- Duplicate protection using each article URL.
- Optional Groq-powered Nepali-first + English social post drafts.
- Draft review, regenerate, dismiss, and manual Facebook Page publishing.
- SQLite storage and a health-check endpoint.
- Publishing is always manual. The app never auto-publishes collected stories.

## Deploy on Render
1. Push this repository to GitHub and connect it to Render as a **Web Service**.
2. Build command: `pip install -r requirements.txt`
3. Start command: `gunicorn --workers 1 --threads 4 --timeout 120 app:app`
4. In Render → Environment, add:
   - `FLASK_SECRET_KEY`: a random secret with at least 32 characters.
   - `ADMIN_PASSWORD`: a strong, unique dashboard password.
   - `GROQ_API_KEY`: optional, for AI drafts.
5. Deploy, then open the service URL and sign in.

## Optional Facebook publishing
Set `FB_PAGE_ID` and `FB_PAGE_ACCESS_TOKEN` as private Render environment variables only after configuring the required Meta app and Page permissions. The dashboard offers a manual publish button for each draft. Verify each post before publishing. Do not put tokens in GitHub, source code, screenshots, or chat.

## Database persistence
SQLite is stored at `DATABASE_PATH` (default: `instance/nayadrishti.sqlite3`). Render's local filesystem may be ephemeral. For data that must survive redeploys, attach a persistent disk and set `DATABASE_PATH` to a location on that disk.

## Important limitations
- RSS feeds may be unavailable, change format, or omit stories; use the dashboard's **Refresh news** button to collect.
- Free hosting can sleep, so this version does not promise continuous background monitoring.
- AI drafts are not verified reporting. Review the original article, dates, names, claims, and source link before publishing.
- RSS image URLs are stored only as previews; this app does not download or republish third-party images. A public image URL does not grant reuse rights.
- Groq API usage is separate from a ChatGPT subscription and may require its own account or billing.
- Facebook publishing requires a valid Page access token and approved Meta permissions.
