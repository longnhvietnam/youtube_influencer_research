#!/usr/bin/env python3
"""
YouTube Influencer Research CLI

A practical Python command-line tool for discovering and ranking YouTube channels / creators
for influencer research. It supports:

1. AI keyword / hashtag generation from a campaign brief.
2. Human review: approve, remove, and add keywords.
3. YouTube scraping through Apify Actors.
4. Channel normalization and deduplication.
5. AI-based channel scoring from a scraped or imported channel list.
6. UTF-8 friendly CSV export.

Environment variables:
    OPENAI_API_KEY          Required for AI keyword generation and AI scoring.
    OPENAI_MODEL            Optional, defaults to gpt-4.1-mini.
    APIFY_TOKEN             Required for Apify scraping/enrichment.
    APIFY_SEARCH_ACTOR_ID   Optional, defaults to streamers~youtube-scraper.
    APIFY_CHANNEL_ACTOR_ID  Optional, defaults to streamers~youtube-channel-scraper.

Examples:
    python youtube_influencer_research.py full --brief "Vietnam skincare creators for Gen Z acne care" --max-results 15
    python youtube_influencer_research.py keywords --brief "English personal finance creators in Singapore" --output keywords.json
    python youtube_influencer_research.py scrape --keywords-file keywords.json --max-results 20 --output raw_channels.csv
    python youtube_influencer_research.py score --input raw_channels.csv --brief "Vietnam skincare creators" --output ranked.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import textwrap
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import requests
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Missing dependency: requests. Install with: pip install requests") from exc


DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEFAULT_SEARCH_ACTOR_ID = os.getenv("APIFY_SEARCH_ACTOR_ID", "streamers~youtube-scraper")
DEFAULT_CHANNEL_ACTOR_ID = os.getenv("APIFY_CHANNEL_ACTOR_ID", "streamers~youtube-channel-scraper")
APIFY_BASE_URL = "https://api.apify.com/v2/acts"

KEYWORD_GROUP_LABELS = {
    "industry_keywords": "Industry / niche keywords and hashtags",
    "brand_keywords": "Well-known brand keywords and hashtags",
    "adjacent_keywords": "Adjacent-topic keywords and hashtags",
}


@dataclass
class ChannelRecord:
    channel_id: str = ""
    channel_name: str = ""
    channel_url: str = ""
    channel_handle: str = ""
    subscriber_count: Optional[int] = None
    country: str = ""
    description: str = ""
    total_videos: Optional[int] = None
    total_views: Optional[int] = None
    source_keyword: str = ""
    source_actor: str = ""
    raw_json: str = ""
    match_score: Optional[int] = None
    match_tier: str = ""
    score_reason: str = ""
    audience_country_fit: str = ""
    subscriber_fit: str = ""
    topic_fit: str = ""
    recommended_action: str = ""

    def identity_key(self) -> str:
        for value in [self.channel_id, self.channel_url, self.channel_handle, self.channel_name]:
            value = clean_str(value).lower()
            if value:
                return value
        return ""


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def clean_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def compact_json(value: Any, max_chars: int = 12000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        text = str(value)
    if len(text) > max_chars:
        return text[:max_chars] + "...<truncated>"
    return text


def parse_int(value: Any) -> Optional[int]:
    """Parse numbers such as 12,345, 1.2M, 3K, or '8.390 subscribers'."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip().lower().replace(",", "")
    if not text:
        return None
    multiplier = 1
    suffix_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([kmb])\b", text)
    if suffix_match:
        number = float(suffix_match.group(1))
        suffix = suffix_match.group(2)
        multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[suffix]
        return int(number * multiplier)
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return int(float(match.group(0)))
    except ValueError:
        return None


def normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def find_value(obj: Any, candidate_keys: Sequence[str], max_depth: int = 5) -> Any:
    """Find the first non-empty value matching any candidate key in a nested structure."""
    normalized_candidates = {normalize_key(k) for k in candidate_keys}

    def _walk(current: Any, depth: int) -> Any:
        if depth > max_depth:
            return None
        if isinstance(current, dict):
            # Exact-ish key pass first.
            for key, value in current.items():
                if normalize_key(str(key)) in normalized_candidates and value not in (None, "", [], {}):
                    return value
            # Then nested pass.
            for value in current.values():
                found = _walk(value, depth + 1)
                if found not in (None, "", [], {}):
                    return found
        elif isinstance(current, list):
            for value in current:
                found = _walk(value, depth + 1)
                if found not in (None, "", [], {}):
                    return found
        return None

    return _walk(obj, 0)


def flatten_keywords(keyword_json: Dict[str, Any]) -> List[str]:
    keywords: List[str] = []
    for group_key in KEYWORD_GROUP_LABELS:
        values = keyword_json.get(group_key, []) or []
        if isinstance(values, str):
            values = [values]
        for value in values:
            keyword = clean_str(value)
            if keyword and keyword not in keywords:
                keywords.append(keyword)
    return keywords


def read_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_parent_dir(path: str | Path) -> None:
    Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# OpenAI helpers
# ---------------------------------------------------------------------------


def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Please export OPENAI_API_KEY before using AI features.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Missing dependency: openai. Install with: pip install openai") from exc
    return OpenAI()


def extract_json_from_text(text: str) -> Any:
    """Parse JSON, allowing occasional markdown fences around model output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def chat_json(messages: List[Dict[str, str]], model: str = DEFAULT_OPENAI_MODEL, temperature: float = 0.2) -> Any:
    client = get_openai_client()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    return extract_json_from_text(content)


def generate_keywords_with_ai(brief: str, language: str = "en", model: str = DEFAULT_OPENAI_MODEL) -> Dict[str, Any]:
    system = (
        "You are an expert in influencer research and YouTube creator discovery. "
        "Suggest practical keywords and hashtags for finding YouTube channels or creators that match the brief. "
        "Return valid JSON only, with no markdown."
    )
    user = f"""
Search brief / requirement:
{brief}

Task:
- Suggest 5-10 keywords or hashtags for each group below:
  1. industry_keywords: keywords or hashtags for the industry, niche, or category.
  2. brand_keywords: keywords or hashtags for well-known brands in the industry or niche.
  3. adjacent_keywords: closely related keywords or hashtags used to expand discovery.
- Prioritize queries that are likely to surface YouTube channels or creators, not only individual videos.
- Use the language that best matches the target market in the brief, while keeping the output easy to review in English.
- Each item should be a natural search query, for example: "skincare routine Vietnam", "#acnecare", "La Roche Posay review".

Required JSON schema:
{{
  "brief_summary": "string",
  "industry_keywords": ["string"],
  "brand_keywords": ["string"],
  "adjacent_keywords": ["string"]
}}
"""
    data = chat_json(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=model,
        temperature=0.35,
    )
    for key in KEYWORD_GROUP_LABELS:
        values = data.get(key, [])
        if not isinstance(values, list):
            values = [values] if values else []
        cleaned: List[str] = []
        for value in values:
            value = clean_str(value)
            if value and value not in cleaned:
                cleaned.append(value)
        data[key] = cleaned[:10]
    data.setdefault("brief_summary", brief[:300])
    data["language"] = language
    return data


def print_keywords(keyword_json: Dict[str, Any]) -> List[Tuple[int, str, str]]:
    rows: List[Tuple[int, str, str]] = []
    counter = 1
    print("\n=== SUGGESTED KEYWORDS / HASHTAGS ===")
    for group_key, label in KEYWORD_GROUP_LABELS.items():
        print(f"\n{label}:")
        values = keyword_json.get(group_key, []) or []
        for value in values:
            print(f"  [{counter}] {value}")
            rows.append((counter, group_key, value))
            counter += 1
    print()
    return rows


def review_keywords_interactively(keyword_json: Dict[str, Any]) -> Dict[str, Any]:
    while True:
        rows = print_keywords(keyword_json)
        print("Review options:")
        print("  a / approve : approve the current list")
        print("  d 1,3,5     : delete keywords by number")
        print("  add <group> <keyword> : add a keyword to industry|brand|adjacent")
        print("  show        : display the list again")
        print("  q           : quit and use the current list")
        choice = input("Enter your choice: ").strip()
        if not choice:
            continue
        lowered = choice.lower()
        if lowered in {"a", "approve", "q", "quit"}:
            return keyword_json
        if lowered == "show":
            continue
        if lowered.startswith("d ") or lowered.startswith("delete "):
            numbers = [int(x) for x in re.findall(r"\d+", choice)]
            by_number = {n: (g, v) for n, g, v in rows}
            for number in numbers:
                if number in by_number:
                    group, value = by_number[number]
                    keyword_json[group] = [x for x in keyword_json.get(group, []) if x != value]
            continue
        if lowered.startswith("add "):
            parts = choice.split(maxsplit=2)
            if len(parts) < 3:
                print("Invalid syntax. Example: add industry skincare Vietnam")
                continue
            group_alias = parts[1].lower()
            keyword = parts[2].strip()
            group_map = {
                "industry": "industry_keywords",
                "nganh": "industry_keywords",
                "field": "industry_keywords",
                "brand": "brand_keywords",
                "brands": "brand_keywords",
                "adjacent": "adjacent_keywords",
                "related": "adjacent_keywords",
                "lienquan": "adjacent_keywords",
            }
            group_key = group_map.get(group_alias)
            if not group_key:
                print("Invalid group. Use industry, brand, or adjacent.")
                continue
            keyword_json.setdefault(group_key, [])
            if keyword and keyword not in keyword_json[group_key]:
                keyword_json[group_key].append(keyword)
            continue
        print("Choice not recognized. Please try again.")


# ---------------------------------------------------------------------------
# Apify helpers
# ---------------------------------------------------------------------------


def require_apify_token() -> str:
    token = os.getenv("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN is not set. Please export APIFY_TOKEN before scraping with Apify.")
    return token


def run_apify_actor_sync(actor_id: str, input_data: Dict[str, Any], timeout_seconds: int = 300) -> List[Dict[str, Any]]:
    token = require_apify_token()
    url = f"{APIFY_BASE_URL}/{actor_id}/run-sync-get-dataset-items"
    response = requests.post(
        url,
        params={"token": token},
        headers={"Content-Type": "application/json"},
        json=input_data,
        timeout=timeout_seconds,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Apify API error {response.status_code}: {response.text[:1000]}")
    data = response.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Some actors or proxy layers can return an object with an items/data key.
        for key in ["items", "data", "results", "datasetItems"]:
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return []


def scrape_youtube_by_keywords(
    keywords: Sequence[str],
    max_results_per_keyword: int,
    include_shorts: bool = False,
    include_streams: bool = False,
    actor_id: str = DEFAULT_SEARCH_ACTOR_ID,
    sleep_seconds: float = 0.0,
) -> List[Dict[str, Any]]:
    all_items: List[Dict[str, Any]] = []
    for i, keyword in enumerate(keywords, start=1):
        print(f"[{i}/{len(keywords)}] Scraping keyword: {keyword}")
        input_data = {
            "searchQueries": [keyword],
            "maxResults": max_results_per_keyword,
            "maxResultsShorts": max_results_per_keyword if include_shorts else 0,
            "maxResultStreams": max_results_per_keyword if include_streams else 0,
        }
        items = run_apify_actor_sync(actor_id, input_data=input_data)
        for item in items:
            if isinstance(item, dict):
                item.setdefault("_source_keyword", keyword)
                item.setdefault("_source_actor", actor_id)
                all_items.append(item)
        if sleep_seconds > 0 and i < len(keywords):
            time.sleep(sleep_seconds)
    return all_items


def enrich_channels_with_apify(
    channel_urls: Sequence[str],
    max_results: int = 10,
    actor_id: str = DEFAULT_CHANNEL_ACTOR_ID,
) -> List[Dict[str, Any]]:
    start_urls = [{"url": url} for url in channel_urls if clean_str(url)]
    if not start_urls:
        return []
    input_data = {
        "startUrls": start_urls,
        "maxResults": max_results,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
    }
    items = run_apify_actor_sync(actor_id, input_data=input_data)
    for item in items:
        if isinstance(item, dict):
            item.setdefault("_source_actor", actor_id)
    return items


# ---------------------------------------------------------------------------
# Normalization / import / export
# ---------------------------------------------------------------------------


def normalize_channel_item(item: Dict[str, Any]) -> ChannelRecord:
    # Prefer nested channel object if item is a YouTube search result of type channel.
    candidate_obj: Any = item
    if isinstance(item.get("channel"), dict):
        merged = dict(item)
        merged.update(item["channel"])
        candidate_obj = merged

    channel_id = clean_str(find_value(candidate_obj, ["channelId", "channel_id", "id", "youtubeChannelId"]))
    channel_name = clean_str(
        find_value(candidate_obj, [
            "channelName",
            "channelTitle",
            "title",
            "name",
            "author",
            "authorName",
            "ownerChannelName",
        ])
    )
    channel_url = clean_str(
        find_value(candidate_obj, [
            "channelUrl",
            "channelURL",
            "url",
            "authorUrl",
            "channelLink",
            "canonicalChannelUrl",
        ])
    )
    channel_handle = clean_str(
        find_value(candidate_obj, ["channelUsername", "username", "handle", "channelHandle", "customUrl", "vanityUrl"])
    )
    subscriber_count = parse_int(
        find_value(candidate_obj, [
            "numberOfSubscribers",
            "subscriberCount",
            "subscribers",
            "subscribersCount",
            "subscriberCountText",
            "numberOfFollowers",
            "followers",
        ])
    )
    country = clean_str(
        find_value(candidate_obj, ["channelLocation", "country", "location", "region", "channelCountry"])
    )
    description = clean_str(
        find_value(candidate_obj, [
            "channelDescription",
            "description",
            "descriptionSnippet",
            "channelDescriptionSnippet",
            "about",
            "bio",
        ])
    )
    total_videos = parse_int(
        find_value(candidate_obj, ["channelTotalVideos", "videoCount", "videos", "numberOfVideos", "videoCountText"])
    )
    total_views = parse_int(
        find_value(candidate_obj, ["channelTotalViews", "viewCount", "views", "totalViews", "viewCountText"])
    )

    # Avoid misclassifying a video URL as channel URL if there is an author/channel URL elsewhere.
    if "watch?v=" in channel_url and clean_str(find_value(item, ["authorUrl", "channelUrl", "channelLink"])):
        channel_url = clean_str(find_value(item, ["authorUrl", "channelUrl", "channelLink"]))

    return ChannelRecord(
        channel_id=channel_id,
        channel_name=channel_name,
        channel_url=channel_url,
        channel_handle=channel_handle,
        subscriber_count=subscriber_count,
        country=country,
        description=description,
        total_videos=total_videos,
        total_views=total_views,
        source_keyword=clean_str(item.get("_source_keyword", item.get("source_keyword", ""))),
        source_actor=clean_str(item.get("_source_actor", item.get("source_actor", ""))),
        raw_json=compact_json(item),
    )


def normalize_items_to_channels(items: Sequence[Dict[str, Any]]) -> List[ChannelRecord]:
    channels: List[ChannelRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        channel = normalize_channel_item(item)
        if channel.channel_name or channel.channel_url or channel.channel_id:
            channels.append(channel)
    return channels


def deduplicate_channels(channels: Sequence[ChannelRecord]) -> List[ChannelRecord]:
    seen: Dict[str, ChannelRecord] = {}
    unnamed_counter = 0
    for channel in channels:
        key = channel.identity_key()
        if not key:
            unnamed_counter += 1
            key = f"__unknown_{unnamed_counter}"
        if key not in seen:
            seen[key] = channel
        else:
            existing = seen[key]
            # Merge source keywords and keep richer metadata.
            if channel.source_keyword and channel.source_keyword not in existing.source_keyword.split(" | "):
                existing.source_keyword = " | ".join(filter(None, [existing.source_keyword, channel.source_keyword]))
            for field_name in [
                "channel_id",
                "channel_name",
                "channel_url",
                "channel_handle",
                "country",
                "description",
                "source_actor",
            ]:
                if not getattr(existing, field_name) and getattr(channel, field_name):
                    setattr(existing, field_name, getattr(channel, field_name))
            for field_name in ["subscriber_count", "total_videos", "total_views"]:
                if getattr(existing, field_name) is None and getattr(channel, field_name) is not None:
                    setattr(existing, field_name, getattr(channel, field_name))
    return list(seen.values())


def load_channels_from_file(path: str | Path) -> List[ChannelRecord]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".json":
        data = read_json(path)
        if isinstance(data, dict):
            for key in ["items", "channels", "data", "results"]:
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if not isinstance(data, list):
            raise ValueError("JSON input must be a list of channel objects or an object with items/channels/data/results.")
        return normalize_items_to_channels(data)

    # CSV / TSV path.
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","
    channels: List[ChannelRecord] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            # If file already follows our schema, preserve fields. Otherwise normalize aliases.
            normalized = normalize_channel_item(row)
            for score_field in [
                "match_score",
                "match_tier",
                "score_reason",
                "audience_country_fit",
                "subscriber_fit",
                "topic_fit",
                "recommended_action",
            ]:
                if row.get(score_field) not in (None, ""):
                    if score_field == "match_score":
                        normalized.match_score = parse_int(row.get(score_field))
                    else:
                        setattr(normalized, score_field, clean_str(row.get(score_field)))
            channels.append(normalized)
    return channels


def channel_to_row(channel: ChannelRecord) -> Dict[str, Any]:
    row = asdict(channel)
    for key, value in list(row.items()):
        if value is None:
            row[key] = ""
    return row


def export_channels_csv(channels: Sequence[ChannelRecord], output_path: str | Path) -> None:
    ensure_parent_dir(output_path)
    fieldnames = list(asdict(ChannelRecord()).keys())
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for channel in channels:
            writer.writerow(channel_to_row(channel))
    print(f"Exported {len(channels)} channels to {output_path}")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_to_tier(score: Optional[int]) -> str:
    if score is None:
        return ""
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def heuristic_score_channel(channel: ChannelRecord, brief: str, criteria: str = "") -> ChannelRecord:
    """Fallback deterministic scoring when AI is unavailable."""
    text = f"{channel.channel_name} {channel.description} {channel.country}".lower()
    brief_terms = [t.lower() for t in re.findall(r"[\w#@]+", brief) if len(t) >= 3]
    criteria_terms = [t.lower() for t in re.findall(r"[\w#@]+", criteria) if len(t) >= 3]
    all_terms = set(brief_terms + criteria_terms)
    overlap = sum(1 for term in all_terms if term in text)
    topic_score = min(55, overlap * 8)
    subscriber_score = 15
    if channel.subscriber_count is not None:
        if 5_000 <= channel.subscriber_count <= 2_000_000:
            subscriber_score = 25
        elif channel.subscriber_count > 2_000_000:
            subscriber_score = 18
        elif channel.subscriber_count < 5_000:
            subscriber_score = 10
    country_score = 10 if channel.country else 5
    score = max(0, min(100, 20 + topic_score + subscriber_score + country_score))
    channel.match_score = score
    channel.match_tier = score_to_tier(score)
    channel.topic_fit = "Heuristic: based on keyword overlap between the brief and the channel name/description."
    channel.subscriber_fit = "Heuristic: subscriber scale appears within a broadly relevant range." if channel.subscriber_count else "No subscriber data available."
    channel.audience_country_fit = "Country/location data is available." if channel.country else "No country/location data available."
    channel.score_reason = "Fallback heuristic scoring because AI is unavailable or disabled."
    channel.recommended_action = "Review manually before outreach."
    return channel


def score_channels_with_ai(
    channels: Sequence[ChannelRecord],
    brief: str,
    criteria: str = "",
    reference_channels: str = "",
    model: str = DEFAULT_OPENAI_MODEL,
    batch_size: int = 12,
) -> List[ChannelRecord]:
    system = (
        "You are an expert in influencer marketing and YouTube creator research. "
        "Evaluate how well YouTube channels match the campaign brief. "
        "Return valid JSON only, with no markdown."
    )
    scored: List[ChannelRecord] = []
    for batch_start in range(0, len(channels), batch_size):
        batch = list(channels[batch_start : batch_start + batch_size])
        payload = []
        for idx, channel in enumerate(batch):
            payload.append(
                {
                    "local_id": idx,
                    "channel_name": channel.channel_name,
                    "channel_url": channel.channel_url,
                    "subscriber_count": channel.subscriber_count,
                    "country": channel.country,
                    "description": channel.description[:1500],
                    "source_keyword": channel.source_keyword,
                    "total_videos": channel.total_videos,
                    "total_views": channel.total_views,
                }
            )
        user = f"""
Campaign/search brief:
{brief}

Additional user criteria:
{criteria or "None provided"}

Reference/ideal channels, if any:
{reference_channels or "None provided"}

Score each channel on a 0-100 scale based on:
- Topic fit: whether the name, description, and source keyword match the brief.
- Subscriber/follower fit: whether the creator size is appropriate; do not assume bigger is always better.
- Country/location fit: whether the channel location matches the target market in the brief.
- Professional and brand-safety fit when the description provides clear signals.
- If data is missing, still score the channel but clearly state uncertainty.

Channels JSON:
{json.dumps(payload, ensure_ascii=False)}

Required JSON schema:
{{
  "scores": [
    {{
      "local_id": 0,
      "match_score": 0,
      "score_reason": "short English string",
      "audience_country_fit": "string",
      "subscriber_fit": "string",
      "topic_fit": "string",
      "recommended_action": "string"
    }}
  ]
}}
"""
        try:
            result = chat_json(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                model=model,
                temperature=0.15,
            )
            scores = result.get("scores", []) if isinstance(result, dict) else []
            by_id = {int(s.get("local_id")): s for s in scores if isinstance(s, dict) and s.get("local_id") is not None}
            for idx, channel in enumerate(batch):
                score_data = by_id.get(idx, {})
                score = parse_int(score_data.get("match_score"))
                if score is None:
                    channel = heuristic_score_channel(channel, brief, criteria)
                else:
                    channel.match_score = max(0, min(100, score))
                    channel.match_tier = score_to_tier(channel.match_score)
                    channel.score_reason = clean_str(score_data.get("score_reason"))
                    channel.audience_country_fit = clean_str(score_data.get("audience_country_fit"))
                    channel.subscriber_fit = clean_str(score_data.get("subscriber_fit"))
                    channel.topic_fit = clean_str(score_data.get("topic_fit"))
                    channel.recommended_action = clean_str(score_data.get("recommended_action"))
                scored.append(channel)
        except Exception as exc:
            print(f"AI scoring batch failed, using heuristic fallback. Error: {exc}", file=sys.stderr)
            for channel in batch:
                scored.append(heuristic_score_channel(channel, brief, criteria))
    scored.sort(key=lambda c: (c.match_score if c.match_score is not None else -1), reverse=True)
    return scored


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def cmd_keywords(args: argparse.Namespace) -> None:
    keyword_json = generate_keywords_with_ai(args.brief, language=args.language, model=args.model)
    if not args.no_review:
        keyword_json = review_keywords_interactively(keyword_json)
    ensure_parent_dir(args.output)
    write_json(args.output, keyword_json)
    print_keywords(keyword_json)
    print(f"Saved keywords to {args.output}")


def load_keywords_from_args(args: argparse.Namespace) -> List[str]:
    keywords: List[str] = []
    if getattr(args, "keywords_file", None):
        data = read_json(args.keywords_file)
        if isinstance(data, list):
            keywords.extend(clean_str(x) for x in data if clean_str(x))
        elif isinstance(data, dict):
            keywords.extend(flatten_keywords(data))
        else:
            raise ValueError("keywords file must be JSON list or keyword JSON object")
    if getattr(args, "keywords", None):
        for value in args.keywords:
            for part in value.split(","):
                if clean_str(part):
                    keywords.append(clean_str(part))
    # Deduplicate preserving order.
    seen = set()
    unique = []
    for keyword in keywords:
        key = keyword.lower()
        if key not in seen:
            seen.add(key)
            unique.append(keyword)
    if not unique:
        raise ValueError("No keywords provided. Use --keywords-file or --keywords.")
    return unique


def cmd_scrape(args: argparse.Namespace) -> None:
    keywords = load_keywords_from_args(args)
    items = scrape_youtube_by_keywords(
        keywords=keywords,
        max_results_per_keyword=args.max_results,
        include_shorts=args.include_shorts,
        include_streams=args.include_streams,
        actor_id=args.actor_id,
        sleep_seconds=args.sleep,
    )
    if args.raw_output:
        ensure_parent_dir(args.raw_output)
        write_json(args.raw_output, items)
        print(f"Saved raw Apify items to {args.raw_output}")
    channels = normalize_items_to_channels(items)
    deduped = deduplicate_channels(channels)
    print(f"Raw items: {len(items)} | Normalized channels: {len(channels)} | Deduplicated channels: {len(deduped)}")
    export_channels_csv(deduped, args.output)


def cmd_score(args: argparse.Namespace) -> None:
    channels = load_channels_from_file(args.input)
    if args.enrich:
        urls = [c.channel_url for c in channels if c.channel_url]
        if urls:
            print(f"Enriching {len(urls)} channel URLs with Apify...")
            enriched_items = enrich_channels_with_apify(urls, max_results=args.enrich_max_results, actor_id=args.channel_actor_id)
            enriched_channels = deduplicate_channels(normalize_items_to_channels(enriched_items))
            # Merge enriched data into original records by identity.
            merged = {c.identity_key(): c for c in channels if c.identity_key()}
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
            channels = deduplicate_channels(channels)
    criteria = args.criteria or ""
    reference_channels = args.reference_channels or ""
    if args.no_ai:
        scored = [heuristic_score_channel(c, args.brief, criteria) for c in channels]
        scored.sort(key=lambda c: (c.match_score if c.match_score is not None else -1), reverse=True)
    else:
        scored = score_channels_with_ai(
            channels=channels,
            brief=args.brief,
            criteria=criteria,
            reference_channels=reference_channels,
            model=args.model,
            batch_size=args.batch_size,
        )
    export_channels_csv(scored, args.output)


def cmd_full(args: argparse.Namespace) -> None:
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    keywords_path = workdir / "keywords_reviewed.json"
    raw_path = workdir / "apify_raw_items.json"
    scraped_csv_path = workdir / "channels_deduped.csv"
    final_csv_path = Path(args.output) if args.output else workdir / "youtube_influencer_ranked.csv"

    print("Step 1: AI keyword / hashtag generation")
    keyword_json = generate_keywords_with_ai(args.brief, language=args.language, model=args.model)

    print("Step 2: Human review")
    if not args.no_review:
        keyword_json = review_keywords_interactively(keyword_json)
    write_json(keywords_path, keyword_json)
    keywords = flatten_keywords(keyword_json)
    print(f"Using {len(keywords)} keywords. Saved reviewed keywords to {keywords_path}")

    print("Step 3: YouTube scrape via Apify")
    items = scrape_youtube_by_keywords(
        keywords=keywords,
        max_results_per_keyword=args.max_results,
        include_shorts=args.include_shorts,
        include_streams=args.include_streams,
        actor_id=args.actor_id,
        sleep_seconds=args.sleep,
    )
    write_json(raw_path, items)
    print(f"Saved raw Apify items to {raw_path}")

    print("Step 4: Remove channel duplication")
    channels = deduplicate_channels(normalize_items_to_channels(items))
    export_channels_csv(channels, scraped_csv_path)

    print("Step 5: AI scoring / ranking")
    if not args.criteria and not args.no_review:
        args.criteria = input("Enter additional scoring criteria (press Enter for none): ").strip()
    if not args.reference_channels and not args.no_review:
        args.reference_channels = input("Enter reference/ideal channels if any (press Enter for none): ").strip()

    if args.no_ai:
        scored = [heuristic_score_channel(c, args.brief, args.criteria or "") for c in channels]
        scored.sort(key=lambda c: (c.match_score if c.match_score is not None else -1), reverse=True)
    else:
        scored = score_channels_with_ai(
            channels=channels,
            brief=args.brief,
            criteria=args.criteria or "",
            reference_channels=args.reference_channels or "",
            model=args.model,
            batch_size=args.batch_size,
        )

    print("Step 6: Export final CSV")
    export_channels_csv(scored, final_csv_path)
    print(f"Done. Final ranked CSV: {final_csv_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="YouTube influencer research CLI with AI + Apify.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Typical workflows:
              1) Full workflow:
                 python youtube_influencer_research.py full --brief "Vietnam beauty creators for acne skincare" --max-results 10

              2) Start from imported channels and score only:
                 python youtube_influencer_research.py score --input channels.csv --brief "Vietnam beauty creators" --output ranked.csv

              3) Score imported channels and enrich via Apify first:
                 python youtube_influencer_research.py score --input channels.csv --brief "..." --enrich --output ranked.csv
            """
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    common_ai = argparse.ArgumentParser(add_help=False)
    common_ai.add_argument("--model", default=DEFAULT_OPENAI_MODEL, help=f"OpenAI model, default: {DEFAULT_OPENAI_MODEL}")

    p_keywords = subparsers.add_parser("keywords", parents=[common_ai], help="Generate and review AI keyword suggestions.")
    p_keywords.add_argument("--brief", required=True, help="Search brief / niche / campaign requirement.")
    p_keywords.add_argument("--language", default="en", help="Preferred language for suggestions, default: en.")
    p_keywords.add_argument("--output", default="keywords.json", help="Output JSON path.")
    p_keywords.add_argument("--no-review", action="store_true", help="Skip interactive keyword review.")
    p_keywords.set_defaults(func=cmd_keywords)

    p_scrape = subparsers.add_parser("scrape", help="Scrape YouTube channels/videos by reviewed keywords via Apify.")
    p_scrape.add_argument("--keywords-file", help="Keyword JSON file from the keywords command.")
    p_scrape.add_argument("--keywords", nargs="*", help="Comma-separated or repeated keywords.")
    p_scrape.add_argument("--max-results", type=int, default=10, help="Max search results per keyword.")
    p_scrape.add_argument("--include-shorts", action="store_true", help="Also request Shorts results if actor supports it.")
    p_scrape.add_argument("--include-streams", action="store_true", help="Also request Streams results if actor supports it.")
    p_scrape.add_argument("--actor-id", default=DEFAULT_SEARCH_ACTOR_ID, help=f"Apify search actor ID, default: {DEFAULT_SEARCH_ACTOR_ID}")
    p_scrape.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between keyword calls.")
    p_scrape.add_argument("--raw-output", help="Optional JSON path for raw Apify items.")
    p_scrape.add_argument("--output", default="channels_deduped.csv", help="Output CSV path.")
    p_scrape.set_defaults(func=cmd_scrape)

    p_score = subparsers.add_parser("score", parents=[common_ai], help="Score imported or scraped channels.")
    p_score.add_argument("--input", required=True, help="Input CSV/JSON with channel data.")
    p_score.add_argument("--brief", required=True, help="Campaign/search brief used for scoring.")
    p_score.add_argument("--criteria", default="", help="Additional scoring criteria.")
    p_score.add_argument("--reference-channels", default="", help="Reference/ideal channels for comparison.")
    p_score.add_argument("--batch-size", type=int, default=12, help="AI scoring batch size.")
    p_score.add_argument("--no-ai", action="store_true", help="Use deterministic heuristic scoring instead of AI.")
    p_score.add_argument("--enrich", action="store_true", help="Use Apify channel actor to enrich imported URLs before scoring.")
    p_score.add_argument("--enrich-max-results", type=int, default=10, help="Max results for channel enrichment actor.")
    p_score.add_argument("--channel-actor-id", default=DEFAULT_CHANNEL_ACTOR_ID, help=f"Apify channel actor ID, default: {DEFAULT_CHANNEL_ACTOR_ID}")
    p_score.add_argument("--output", default="ranked_channels.csv", help="Output CSV path.")
    p_score.set_defaults(func=cmd_score)

    p_full = subparsers.add_parser("full", parents=[common_ai], help="Run the full 6-step workflow.")
    p_full.add_argument("--brief", required=True, help="Search brief / niche / campaign requirement.")
    p_full.add_argument("--language", default="en", help="Preferred language for keyword suggestions, default: en.")
    p_full.add_argument("--max-results", type=int, default=10, help="Max search results per keyword.")
    p_full.add_argument("--include-shorts", action="store_true", help="Also request Shorts results if actor supports it.")
    p_full.add_argument("--include-streams", action="store_true", help="Also request Streams results if actor supports it.")
    p_full.add_argument("--actor-id", default=DEFAULT_SEARCH_ACTOR_ID, help=f"Apify search actor ID, default: {DEFAULT_SEARCH_ACTOR_ID}")
    p_full.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between keyword calls.")
    p_full.add_argument("--criteria", default="", help="Additional scoring criteria.")
    p_full.add_argument("--reference-channels", default="", help="Reference/ideal channels for comparison.")
    p_full.add_argument("--batch-size", type=int, default=12, help="AI scoring batch size.")
    p_full.add_argument("--no-ai", action="store_true", help="Use heuristic scoring instead of AI scoring. AI is still needed for keywords unless --keywords-file is added in future.")
    p_full.add_argument("--no-review", action="store_true", help="Skip all interactive review prompts.")
    p_full.add_argument("--workdir", default="youtube_influencer_run", help="Working directory for intermediate files.")
    p_full.add_argument("--output", help="Final ranked CSV path. Default: <workdir>/youtube_influencer_ranked.csv")
    p_full.set_defaults(func=cmd_full)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
