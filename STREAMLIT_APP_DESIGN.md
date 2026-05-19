# Streamlit Version Design

## Objective

This Streamlit version converts the existing YouTube Influencer Research CLI into a web application that can be uploaded to a GitHub repository and deployed on Streamlit Community Cloud. The web app reuses the core logic from `youtube_influencer_research.py` instead of duplicating scraping, normalization, deduplication, scoring, and CSV export behavior.

## Deployment Options Considered

| Approach | Tradeoffs | Cost | Setup Complexity |
|---|---|---|---|
| Streamlit Community Cloud | Best fit for the user's request. Simple upload from GitHub, easy UI, secrets management, and fast iteration. Long-running jobs may hit platform limits, so large Apify runs should be batched. | Streamlit Community Cloud has a free tier; API usage still depends on OpenAI and Apify billing. | Low. Requires GitHub repository, `requirements.txt`, `streamlit_app.py`, and configured secrets. |
| Desktop Tkinter app | Works locally without web hosting and was already implemented, but cannot be directly uploaded to streamlit.io. | No hosting cost; API usage still applies. | Low for local users, but unsuitable for hosted browser access. |
| Custom web app/backend | More scalable for multi-user roles, background queues, and database persistence, but requires a larger implementation and hosting setup. | Hosting and API usage costs apply. | Medium to high. |

## Streamlit User Flow

The app provides four tabs. The **Keyword Builder** tab generates AI keyword and hashtag suggestions from a campaign brief. The user can then edit the generated lists directly in text areas before saving them into session state. The **Scrape & Deduplicate** tab runs Apify scraping for the selected keywords, normalizes Apify output into channel records, removes duplicates, and provides downloadable CSV and JSON outputs. The **Import & Score** tab allows users to upload an existing CSV or JSON list of channels and score it immediately, which supports the alternative workflow where the user starts from step 5. The **Full Workflow** tab connects keyword generation, scraping, deduplication, scoring, and CSV export into one guided flow.

## Secrets and Environment Variables

Streamlit Community Cloud exposes secrets through `st.secrets`. The app maps these secrets into environment variables at runtime so the existing core module can keep using `os.getenv`.

| Secret Name | Required For | Notes |
|---|---|---|
| `OPENAI_API_KEY` | AI keyword generation and AI scoring | If omitted, deterministic heuristic scoring still works when the user disables AI scoring. |
| `APIFY_TOKEN` | YouTube scraping and optional enrichment | Required for Apify API calls. |
| `OPENAI_MODEL` | Optional model override | Defaults to `gpt-4.1-mini` in the core script. |
| `APIFY_SEARCH_ACTOR_ID` | Optional Apify search actor override | Defaults to `streamers~youtube-scraper`. |
| `APIFY_CHANNEL_ACTOR_ID` | Optional Apify channel actor override | Defaults to `streamers~youtube-channel-scraper`. |

## Files Added for Streamlit

| File | Purpose |
|---|---|
| `streamlit_app.py` | Main Streamlit web application. |
| `requirements.txt` | Python dependencies for Streamlit Community Cloud. |
| `.streamlit/config.toml` | Basic Streamlit server/theme configuration. |
| `.streamlit/secrets.toml.example` | Example local secrets file; should not be committed as real secrets. |
| `README_STREAMLIT_DEPLOY.md` | Deployment guide for GitHub and streamlit.io. |

## Validation Scope

The Streamlit version should pass Python syntax checks, core module import checks, and an offline scoring workflow using `sample_channels.csv` with heuristic scoring. Live Apify scraping cannot be validated without an `APIFY_TOKEN`, and live AI generation requires an `OPENAI_API_KEY`.
