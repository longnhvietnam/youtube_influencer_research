# YouTube Influencer Research - Streamlit Cloud Deployment Guide

This package contains a Streamlit-ready version of the YouTube Influencer Research tool. It can be uploaded to a GitHub repository and deployed on **Streamlit Community Cloud** with `streamlit_app.py` as the entry point. Streamlit Community Cloud deploys apps from GitHub repositories, while app secrets can be configured in the Streamlit app settings rather than hard-coded in source files.[^1] [^2]

## Files Included

| File | Purpose |
|---|---|
| `streamlit_app.py` | Main Streamlit web application. |
| `youtube_influencer_research.py` | Core reusable logic for AI keyword generation, Apify scraping, channel normalization, deduplication, scoring, and CSV export. |
| `requirements.txt` | Python dependencies used by Streamlit Cloud. |
| `.streamlit/config.toml` | Basic Streamlit server and theme configuration. |
| `.streamlit/secrets.toml.example` | Example secrets file for local development. Do not commit real secrets. |
| `.gitignore` | Prevents local secrets and generated outputs from being committed. |
| `sample_channels.csv` | Sample input for testing the import-and-score workflow. |

## Recommended Deployment Path

The requested deployment target is Streamlit Community Cloud, and the package is built around that target. A local Streamlit run is still useful for validation before pushing to GitHub.

| Option | Best For | Pros | Limits |
|---|---|---|---|
| Streamlit Community Cloud | Sharing a browser-based research tool quickly | GitHub-based deployment, built-in secrets management, simple UI iteration | Very large scraping jobs should be batched to avoid long-running app sessions |
| Local Streamlit | Private testing before deployment | Fast debugging, no public app URL required | Requires local Python setup and does not give teammates hosted access |
| Custom backend web app | Multi-user production workflow | More control over auth, queues, database persistence, and background jobs | Requires more engineering and hosting setup |

## Local Test

Create a virtual environment if desired, install dependencies, then run the app locally.

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

For local secrets, copy the example file and insert your own API keys.

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

The local `.streamlit/secrets.toml` file is intentionally ignored by Git. It should not be committed because it contains private API keys.

## Streamlit Community Cloud Deployment Steps

First, create a new GitHub repository and upload the package files. The repository should contain `streamlit_app.py`, `youtube_influencer_research.py`, `requirements.txt`, `.streamlit/config.toml`, and any sample files you want to keep. Streamlit Community Cloud can deploy directly from a GitHub repository and uses a selected app file as the entry point.[^1]

Second, go to [Streamlit Community Cloud](https://streamlit.io/cloud), create a new app, select your GitHub repository, choose the target branch, and set the main file path to:

```text
streamlit_app.py
```

Third, configure app secrets in the Streamlit app settings. Use the format below.

```toml
OPENAI_API_KEY = "sk-your-openai-key"
APIFY_TOKEN = "apify_api_your-token"

# Optional overrides
OPENAI_MODEL = "gpt-4.1-mini"
APIFY_SEARCH_ACTOR_ID = "streamers~youtube-scraper"
APIFY_CHANNEL_ACTOR_ID = "streamers~youtube-channel-scraper"
```

Streamlit exposes configured secrets to the app through `st.secrets`, and this app maps those values into environment variables at runtime so the existing core module can use them.[^2]

## App Workflow

The web app is organized into four tabs. The **Keyword Builder** tab uses AI to suggest keywords and hashtags from a campaign brief. The **Scrape & Deduplicate** tab runs Apify scraping for the reviewed keywords and deduplicates discovered channels. The **Import & Score** tab lets you upload a CSV or JSON channel list from another source and score it directly. The **Full Workflow** tab connects reviewed keywords, scraping, deduplication, scoring, and CSV export into a single guided flow.

| Workflow | Required Secret | Output |
|---|---|---|
| Generate keyword suggestions | `OPENAI_API_KEY` | Reviewed keyword JSON |
| Scrape YouTube via Apify | `APIFY_TOKEN` | Raw Apify JSON and deduplicated channel CSV |
| Score imported channels with AI | `OPENAI_API_KEY` | Ranked CSV |
| Score imported channels without AI | None | Heuristic ranked CSV |
| Enrich imported channels via Apify | `APIFY_TOKEN` | Enriched and ranked CSV |

## Input CSV Schema

The import workflow accepts CSV files that contain any reasonable channel fields. The core parser attempts to normalize common aliases. For the best results, use the columns below.

| Column | Description |
|---|---|
| `channel_name` | YouTube channel or creator name. |
| `channel_url` | Channel URL. |
| `channel_handle` | YouTube handle if available. |
| `subscriber_count` | Subscriber count as a number or readable text. |
| `country` | Country or location if available. |
| `description` | Channel description or bio. |
| `source_keyword` | Keyword that led to this channel, if known. |

## Operational Notes

Large scraping jobs can take time because the app calls Apify once per keyword. For Streamlit Community Cloud, it is safer to start with a small `max results per keyword` value, inspect the output, then run additional batches as needed. API costs and rate limits are controlled by the connected OpenAI and Apify accounts, not by the Streamlit app code.

## References

[^1]: [Streamlit Docs - Deploy your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app)
[^2]: [Streamlit Docs - Secrets management](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
