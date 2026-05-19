"""Offline validation for the Streamlit YouTube Influencer Research app."""

from __future__ import annotations

import importlib
from pathlib import Path

import pandas as pd

import streamlit_app
import youtube_influencer_research as core


REQUIRED_ATTRIBUTES = [
    "apply_streamlit_secrets",
    "channels_to_dataframe",
    "score_channels",
    "keyword_builder_tab",
    "scrape_tab",
    "import_and_score_tab",
    "full_workflow_tab",
]


def main() -> None:
    for module_name in ["streamlit", "pandas", "requests", "openai"]:
        importlib.import_module(module_name)

    for attr in REQUIRED_ATTRIBUTES:
        assert hasattr(streamlit_app, attr), f"Missing Streamlit app attribute: {attr}"

    sample_path = Path("sample_channels.csv")
    channels = core.load_channels_from_file(sample_path)
    assert channels, "Sample channel import returned no channels"

    scored = streamlit_app.score_channels(
        channels=channels,
        brief="English-speaking skincare education creators for acne product influencer research.",
        criteria="Prefer creators with practical skincare routines and clear product review content.",
        reference_channels="",
        use_ai=False,
        model=core.DEFAULT_OPENAI_MODEL,
        batch_size=5,
    )
    assert scored, "Offline heuristic scoring returned no channels"
    assert scored[0].match_score is not None, "Top scored channel has no score"

    df = streamlit_app.channels_to_dataframe(scored)
    assert isinstance(df, pd.DataFrame), "channels_to_dataframe did not return a DataFrame"
    assert "match_score" in df.columns, "Output DataFrame is missing match_score"
    df.to_csv("streamlit_validation_ranked.csv", index=False, encoding="utf-8-sig")

    print("streamlit_validation_ok")
    print(f"channels_loaded={len(channels)}")
    print(f"channels_scored={len(scored)}")
    print(f"top_score={scored[0].match_score}")


if __name__ == "__main__":
    main()
