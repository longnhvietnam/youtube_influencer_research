"""Streamlit Community Cloud app for YouTube Influencer Research.

This app is a web UI wrapper around `youtube_influencer_research.py`.
It supports keyword generation, keyword review, Apify scraping, channel
deduplication, imported-channel scoring, and CSV export.
"""

from __future__ import annotations

import copy
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import pandas as pd
import streamlit as st

import youtube_influencer_research as core


APP_TITLE = "YouTube Influencer Research"
APP_SUBTITLE = "AI-assisted YouTube channel discovery, scoring, and CSV export."


# ---------------------------------------------------------------------------
# Streamlit / environment helpers
# ---------------------------------------------------------------------------


def apply_streamlit_secrets() -> None:
    """Expose Streamlit secrets as environment variables for the core module."""
    secret_names = [
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "APIFY_TOKEN",
        "APIFY_SEARCH_ACTOR_ID",
        "APIFY_CHANNEL_ACTOR_ID",
    ]
    try:
        secrets = st.secrets
    except Exception:
        secrets = {}

    for name in secret_names:
        value = None
        try:
            if name in secrets:
                value = secrets[name]
        except Exception:
            value = None
        if value and not os.getenv(name):
            os.environ[name] = str(value)


def secret_available(name: str) -> bool:
    return bool(os.getenv(name))


def init_session_state() -> None:
    defaults = {
        "keyword_json": None,
        "reviewed_keywords": [],
        "raw_apify_items": [],
        "scraped_channels": [],
        "imported_channels": [],
        "scored_channels": [],
        "last_status": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def parse_lines(text: str) -> List[str]:
    seen = set()
    values: List[str] = []
    for line in text.splitlines():
        item = line.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            values.append(item)
    return values


def channels_to_dataframe(channels: Sequence[core.ChannelRecord]) -> pd.DataFrame:
    rows = [core.channel_to_row(channel) for channel in channels]
    return pd.DataFrame(rows)


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def json_to_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def keyword_json_to_edit_text(keyword_json: Dict[str, Any], group_key: str) -> str:
    values = keyword_json.get(group_key, []) or []
    if isinstance(values, str):
        values = [values]
    return "\n".join(str(value) for value in values if str(value).strip())


def save_uploaded_file(uploaded_file) -> Path:
    suffix = Path(uploaded_file.name).suffix or ".csv"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(uploaded_file.getvalue())
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


def merge_enriched_channels(
    original_channels: Sequence[core.ChannelRecord],
    enriched_channels: Sequence[core.ChannelRecord],
) -> List[core.ChannelRecord]:
    channels = list(copy.deepcopy(original_channels))
    merged = {channel.identity_key(): channel for channel in channels if channel.identity_key()}
    for enriched in enriched_channels:
        key = enriched.identity_key()
        if key in merged:
            original = merged[key]
            for field_name in [
                "channel_id",
                "channel_name",
                "channel_url",
                "channel_handle",
                "country",
                "description",
                "source_actor",
            ]:
                if not getattr(original, field_name) and getattr(enriched, field_name):
                    setattr(original, field_name, getattr(enriched, field_name))
            for field_name in ["subscriber_count", "total_videos", "total_views"]:
                if getattr(original, field_name) is None and getattr(enriched, field_name) is not None:
                    setattr(original, field_name, getattr(enriched, field_name))
        else:
            channels.append(enriched)
    return core.deduplicate_channels(channels)


def score_channels(
    channels: Sequence[core.ChannelRecord],
    brief: str,
    criteria: str,
    reference_channels: str,
    use_ai: bool,
    model: str,
    batch_size: int,
) -> List[core.ChannelRecord]:
    channels_copy = list(copy.deepcopy(channels))
    if use_ai:
        return core.score_channels_with_ai(
            channels=channels_copy,
            brief=brief,
            criteria=criteria,
            reference_channels=reference_channels,
            model=model,
            batch_size=batch_size,
        )
    scored = [core.heuristic_score_channel(channel, brief, criteria) for channel in channels_copy]
    scored.sort(key=lambda c: (c.match_score if c.match_score is not None else -1), reverse=True)
    return scored


def render_channels_table(channels: Sequence[core.ChannelRecord], title: str, filename: str) -> None:
    if not channels:
        st.info("No channels to display yet.")
        return
    df = channels_to_dataframe(channels)
    st.subheader(title)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button(
        label="Download CSV",
        data=dataframe_to_csv_bytes(df),
        file_name=filename,
        mime="text/csv",
    )


# ---------------------------------------------------------------------------
# UI sections
# ---------------------------------------------------------------------------


def render_sidebar() -> None:
    with st.sidebar:
        st.header("Configuration")
        st.caption("Set secrets in Streamlit Community Cloud or environment variables locally.")
        status_df = pd.DataFrame(
            [
                {"Secret": "OPENAI_API_KEY", "Available": secret_available("OPENAI_API_KEY")},
                {"Secret": "APIFY_TOKEN", "Available": secret_available("APIFY_TOKEN")},
                {"Secret": "OPENAI_MODEL", "Available": secret_available("OPENAI_MODEL")},
                {"Secret": "APIFY_SEARCH_ACTOR_ID", "Available": secret_available("APIFY_SEARCH_ACTOR_ID")},
                {"Secret": "APIFY_CHANNEL_ACTOR_ID", "Available": secret_available("APIFY_CHANNEL_ACTOR_ID")},
            ]
        )
        st.dataframe(status_df, use_container_width=True, hide_index=True)
        st.divider()
        st.markdown(
            "**Tip.** You can still score imported channels without an OpenAI key by disabling AI scoring. "
            "Apify scraping requires `APIFY_TOKEN`."
        )
        st.divider()
        st.markdown("**Current session state**")
        st.write(f"Reviewed keywords: {len(st.session_state.reviewed_keywords)}")
        st.write(f"Raw Apify items: {len(st.session_state.raw_apify_items)}")
        st.write(f"Scraped channels: {len(st.session_state.scraped_channels)}")
        st.write(f"Imported channels: {len(st.session_state.imported_channels)}")
        st.write(f"Scored channels: {len(st.session_state.scored_channels)}")


def keyword_builder_tab() -> None:
    st.header("1. AI Keyword Builder")
    st.write(
        "Describe the kind of YouTube channels or creators you want to find. "
        "The app will suggest industry keywords, brand keywords, and adjacent-topic keywords."
    )

    brief = st.text_area(
        "Campaign / search brief",
        value="Vietnam beauty creators for acne skincare products targeting Gen Z",
        height=120,
    )
    col_a, col_b = st.columns([1, 1])
    with col_a:
        language = st.text_input("Preferred output language", value="en")
    with col_b:
        model = st.text_input("OpenAI model", value=os.getenv("OPENAI_MODEL", core.DEFAULT_OPENAI_MODEL))

    if st.button("Generate keyword suggestions", type="primary"):
        if not brief.strip():
            st.error("Please enter a campaign/search brief.")
        elif not secret_available("OPENAI_API_KEY"):
            st.error("OPENAI_API_KEY is not configured. Add it in Streamlit secrets to use AI keyword generation.")
        else:
            with st.spinner("Generating keyword suggestions..."):
                try:
                    st.session_state.keyword_json = core.generate_keywords_with_ai(
                        brief=brief.strip(),
                        language=language.strip() or "en",
                        model=model.strip() or core.DEFAULT_OPENAI_MODEL,
                    )
                    st.success("Keyword suggestions generated. Review and edit them below.")
                except Exception as exc:
                    st.error(f"Keyword generation failed: {exc}")

    keyword_json = st.session_state.keyword_json
    if keyword_json:
        st.subheader("Review keyword groups")
        edited: Dict[str, List[str]] = {}
        for group_key, label in core.KEYWORD_GROUP_LABELS.items():
            edited[group_key] = parse_lines(
                st.text_area(
                    label,
                    value=keyword_json_to_edit_text(keyword_json, group_key),
                    height=150,
                    key=f"edit_{group_key}",
                )
            )

        if st.button("Save reviewed keywords"):
            updated = dict(keyword_json)
            for group_key, values in edited.items():
                updated[group_key] = values
            st.session_state.keyword_json = updated
            st.session_state.reviewed_keywords = core.flatten_keywords(updated)
            st.success(f"Saved {len(st.session_state.reviewed_keywords)} reviewed keywords.")

        if st.session_state.reviewed_keywords:
            st.write("Reviewed keyword list:")
            st.code("\n".join(st.session_state.reviewed_keywords), language="text")

        st.download_button(
            label="Download reviewed keywords JSON",
            data=json_to_bytes(st.session_state.keyword_json),
            file_name="keywords_reviewed.json",
            mime="application/json",
        )


def scrape_tab() -> None:
    st.header("2. Scrape & Deduplicate")
    st.write(
        "Use reviewed keywords to run the Apify YouTube scraper, normalize returned items into channel records, "
        "and remove duplicate channels."
    )

    default_keywords = "\n".join(st.session_state.reviewed_keywords)
    keywords_text = st.text_area("Keywords / hashtags, one per line", value=default_keywords, height=180)

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        max_results = st.number_input("Max results per keyword", min_value=1, max_value=100, value=10, step=1)
    with col_b:
        include_shorts = st.checkbox("Include Shorts", value=False)
    with col_c:
        include_streams = st.checkbox("Include Streams", value=False)

    actor_id = st.text_input("Apify search actor ID", value=os.getenv("APIFY_SEARCH_ACTOR_ID", core.DEFAULT_SEARCH_ACTOR_ID))
    sleep_seconds = st.number_input("Sleep seconds between keyword calls", min_value=0.0, max_value=30.0, value=0.0, step=0.5)

    if st.button("Run Apify scrape", type="primary"):
        keywords = parse_lines(keywords_text)
        if not keywords:
            st.error("Please provide at least one keyword.")
        elif not secret_available("APIFY_TOKEN"):
            st.error("APIFY_TOKEN is not configured. Add it in Streamlit secrets before scraping.")
        else:
            with st.spinner("Running Apify scrape and deduplication..."):
                try:
                    items = core.scrape_youtube_by_keywords(
                        keywords=keywords,
                        max_results_per_keyword=int(max_results),
                        include_shorts=include_shorts,
                        include_streams=include_streams,
                        actor_id=actor_id.strip() or core.DEFAULT_SEARCH_ACTOR_ID,
                        sleep_seconds=float(sleep_seconds),
                    )
                    channels = core.normalize_items_to_channels(items)
                    deduped = core.deduplicate_channels(channels)
                    st.session_state.raw_apify_items = items
                    st.session_state.scraped_channels = deduped
                    st.success(
                        f"Scrape completed. Raw items: {len(items)}. Normalized channels: {len(channels)}. "
                        f"Deduplicated channels: {len(deduped)}."
                    )
                except Exception as exc:
                    st.error(f"Apify scrape failed: {exc}")

    if st.session_state.raw_apify_items:
        st.download_button(
            label="Download raw Apify JSON",
            data=json_to_bytes(st.session_state.raw_apify_items),
            file_name="apify_raw_items.json",
            mime="application/json",
        )

    render_channels_table(st.session_state.scraped_channels, "Deduplicated scraped channels", "channels_deduped.csv")


def import_and_score_tab() -> None:
    st.header("3. Import & Score")
    st.write(
        "Start from a CSV/JSON channel list collected elsewhere, or score the channels scraped in the current session. "
        "This supports the workflow where you skip keyword generation and scraping."
    )

    uploaded = st.file_uploader("Upload CSV or JSON channel list", type=["csv", "tsv", "tab", "json"])
    if uploaded and st.button("Load uploaded channels"):
        try:
            temp_path = save_uploaded_file(uploaded)
            channels = core.load_channels_from_file(temp_path)
            st.session_state.imported_channels = core.deduplicate_channels(channels)
            st.success(f"Loaded {len(st.session_state.imported_channels)} deduplicated channels from upload.")
        except Exception as exc:
            st.error(f"Failed to load uploaded file: {exc}")

    source = st.radio(
        "Channel source for scoring",
        options=["Uploaded/imported channels", "Scraped channels from this session"],
        horizontal=True,
    )
    channels = (
        st.session_state.imported_channels
        if source == "Uploaded/imported channels"
        else st.session_state.scraped_channels
    )
    render_channels_table(channels, "Channels selected for scoring", "channels_selected_for_scoring.csv")

    st.subheader("Scoring settings")
    brief = st.text_area(
        "Campaign / search brief for scoring",
        value="Vietnam beauty creators for acne skincare products targeting Gen Z",
        height=100,
        key="score_brief",
    )
    criteria = st.text_area(
        "Additional scoring criteria",
        value="Prefer authentic educational content, skincare routine videos, and mid-sized creators with clear audience fit.",
        height=90,
    )
    reference_channels = st.text_area(
        "Reference / ideal channels, if any",
        value="",
        height=70,
    )

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        use_ai = st.checkbox("Use AI scoring", value=secret_available("OPENAI_API_KEY"))
    with col_b:
        batch_size = st.number_input("AI batch size", min_value=1, max_value=30, value=12, step=1)
    with col_c:
        model = st.text_input("Scoring model", value=os.getenv("OPENAI_MODEL", core.DEFAULT_OPENAI_MODEL))

    with st.expander("Optional Apify enrichment for imported channel URLs"):
        enrich = st.checkbox("Enrich channel data before scoring", value=False)
        enrich_max_results = st.number_input("Enrichment max results", min_value=1, max_value=50, value=10, step=1)
        channel_actor_id = st.text_input(
            "Apify channel actor ID",
            value=os.getenv("APIFY_CHANNEL_ACTOR_ID", core.DEFAULT_CHANNEL_ACTOR_ID),
        )

    if st.button("Score and rank channels", type="primary"):
        if not channels:
            st.error("No channels available. Upload a file or run scraping first.")
        elif not brief.strip():
            st.error("Please provide a scoring brief.")
        elif use_ai and not secret_available("OPENAI_API_KEY"):
            st.error("OPENAI_API_KEY is not configured. Disable AI scoring or add the key in Streamlit secrets.")
        elif enrich and not secret_available("APIFY_TOKEN"):
            st.error("APIFY_TOKEN is required for enrichment. Disable enrichment or add the token in Streamlit secrets.")
        else:
            with st.spinner("Scoring channels..."):
                try:
                    scoring_channels = list(copy.deepcopy(channels))
                    if enrich:
                        urls = [channel.channel_url for channel in scoring_channels if channel.channel_url]
                        if urls:
                            enriched_items = core.enrich_channels_with_apify(
                                urls,
                                max_results=int(enrich_max_results),
                                actor_id=channel_actor_id.strip() or core.DEFAULT_CHANNEL_ACTOR_ID,
                            )
                            enriched_channels = core.deduplicate_channels(core.normalize_items_to_channels(enriched_items))
                            scoring_channels = merge_enriched_channels(scoring_channels, enriched_channels)
                    scored = score_channels(
                        channels=scoring_channels,
                        brief=brief.strip(),
                        criteria=criteria.strip(),
                        reference_channels=reference_channels.strip(),
                        use_ai=use_ai,
                        model=model.strip() or core.DEFAULT_OPENAI_MODEL,
                        batch_size=int(batch_size),
                    )
                    st.session_state.scored_channels = scored
                    st.success(f"Scored {len(scored)} channels.")
                except Exception as exc:
                    st.error(f"Scoring failed: {exc}")

    render_channels_table(st.session_state.scored_channels, "Ranked channels", "ranked_channels.csv")


def full_workflow_tab() -> None:
    st.header("4. Full Workflow")
    st.write(
        "Run a complete web workflow after reviewing keywords. For best results, generate and save reviewed keywords "
        "in the first tab, then run this workflow."
    )

    default_keywords = "\n".join(st.session_state.reviewed_keywords)
    brief = st.text_area(
        "Campaign / search brief",
        value="Vietnam beauty creators for acne skincare products targeting Gen Z",
        height=100,
        key="full_brief",
    )
    keywords_text = st.text_area("Reviewed keywords to use", value=default_keywords, height=160, key="full_keywords")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        max_results = st.number_input("Max results per keyword", min_value=1, max_value=100, value=10, step=1, key="full_max")
    with col_b:
        use_ai = st.checkbox("Use AI scoring", value=secret_available("OPENAI_API_KEY"), key="full_ai")
    with col_c:
        batch_size = st.number_input("AI batch size", min_value=1, max_value=30, value=12, step=1, key="full_batch")

    criteria = st.text_area("Additional scoring criteria", value="", height=80, key="full_criteria")
    actor_id = st.text_input("Apify search actor ID", value=os.getenv("APIFY_SEARCH_ACTOR_ID", core.DEFAULT_SEARCH_ACTOR_ID), key="full_actor")
    model = st.text_input("OpenAI model", value=os.getenv("OPENAI_MODEL", core.DEFAULT_OPENAI_MODEL), key="full_model")

    if st.button("Run full workflow", type="primary"):
        keywords = parse_lines(keywords_text)
        if not brief.strip():
            st.error("Please provide a campaign/search brief.")
        elif not keywords:
            st.error("Please provide reviewed keywords.")
        elif not secret_available("APIFY_TOKEN"):
            st.error("APIFY_TOKEN is required for scraping. Add it in Streamlit secrets.")
        elif use_ai and not secret_available("OPENAI_API_KEY"):
            st.error("OPENAI_API_KEY is required for AI scoring. Disable AI scoring or add the key.")
        else:
            try:
                with st.spinner("Step 1/4: Scraping YouTube via Apify..."):
                    items = core.scrape_youtube_by_keywords(
                        keywords=keywords,
                        max_results_per_keyword=int(max_results),
                        include_shorts=False,
                        include_streams=False,
                        actor_id=actor_id.strip() or core.DEFAULT_SEARCH_ACTOR_ID,
                        sleep_seconds=0.0,
                    )
                with st.spinner("Step 2/4: Normalizing and deduplicating channels..."):
                    channels = core.deduplicate_channels(core.normalize_items_to_channels(items))
                with st.spinner("Step 3/4: Scoring and ranking channels..."):
                    scored = score_channels(
                        channels=channels,
                        brief=brief.strip(),
                        criteria=criteria.strip(),
                        reference_channels="",
                        use_ai=use_ai,
                        model=model.strip() or core.DEFAULT_OPENAI_MODEL,
                        batch_size=int(batch_size),
                    )
                st.session_state.raw_apify_items = items
                st.session_state.scraped_channels = channels
                st.session_state.scored_channels = scored
                st.success(
                    f"Full workflow completed. Raw items: {len(items)}. Deduplicated channels: {len(channels)}. "
                    f"Scored channels: {len(scored)}."
                )
            except Exception as exc:
                st.error(f"Full workflow failed: {exc}")

    render_channels_table(st.session_state.scored_channels, "Final ranked channels", "youtube_influencer_ranked.csv")


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    apply_streamlit_secrets()
    init_session_state()

    st.title(APP_TITLE)
    st.caption(APP_SUBTITLE)
    st.markdown(
        "This app helps influencer researchers discover YouTube channels, remove duplicates, "
        "score fit against a campaign brief, and export a UTF-8 friendly CSV."
    )

    render_sidebar()

    tab_keywords, tab_scrape, tab_score, tab_full = st.tabs(
        ["Keyword Builder", "Scrape & Deduplicate", "Import & Score", "Full Workflow"]
    )
    with tab_keywords:
        keyword_builder_tab()
    with tab_scrape:
        scrape_tab()
    with tab_score:
        import_and_score_tab()
    with tab_full:
        full_workflow_tab()


if __name__ == "__main__":
    main()
