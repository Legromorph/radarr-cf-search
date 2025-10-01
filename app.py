from __future__ import annotations
import os
import random
import logging
import time
from typing import Dict, Any

import requests
from requests import Response
from dotenv import load_dotenv

load_dotenv(dotenv_path="/config/.env")


# ----------------------
# Environment helpers
# ----------------------

def get_env_bool(key: str, default: bool = False) -> bool:
    """Safely read a boolean from environment variables."""
    val = os.getenv(key)
    return val.lower() == "true" if val else default


def get_env_int(key: str, default: int = 0) -> int:
    """Safely read an integer from environment variables."""
    val = os.getenv(key)
    return int(val) if val and val.isdigit() else default


def get_env_str(key: str, default: str = "") -> str:
    """Safely read a string from environment variables."""
    val = os.getenv(key)
    return val.strip() if val else default


# ----------------------
# Logger
# ----------------------
LOG_FILE = f"/config/output_{time.strftime('%Y-%m-%d')}.log"
LOG_LEVEL = get_env_str("LOG_LEVEL", "INFO").upper()

logging.Formatter.converter = time.localtime

logging.basicConfig(
    filename=LOG_FILE,
    encoding="utf-8",
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=getattr(logging, LOG_LEVEL, logging.INFO)
)
logger = logging.getLogger(__name__)


# ----------------------
# Load environment variables
# ----------------------

API_PATH = "/api/v3/"
UPGRADE_TAG = get_env_str("UPGRADE_TAG", "upgrade-cf")


# ----------------------
# Common API helpers
# ----------------------
def api_get(url: str, headers: Dict[str, str]) -> Any:
    """Wrapper for GET requests with error handling."""
    resp: Response = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def api_post(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Any:
    """Wrapper for POST requests with error handling."""
    resp: Response = requests.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()


def api_put(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Any:
    """Wrapper for PUT requests with error handling."""
    resp: Response = requests.put(url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()


def ensure_tag_exists(base_url: str, headers: Dict[str, str], tag_name: str) -> int:
    """Ensure tag exists, return its ID."""
    tag_url = f"{base_url}{API_PATH}tag"
    tags = api_get(tag_url, headers)
    tag_id = next((t["id"] for t in tags if t["label"] == tag_name), None)

    if tag_id is None:
        tag_id = api_post(tag_url, headers, {"label": tag_name})["id"]
        logger.info(f"Created new tag '{tag_name}' with ID {tag_id}")

    return tag_id


# ----------------------
# Radarr functions
# ----------------------
def run_radarr_upgrade():
    radarr_enabled = get_env_bool("PROCESS_RADARR")
    if not radarr_enabled:
        return

    base_url = os.getenv("RADARR_URL", "")
    headers = {"Authorization": os.getenv("RADARR_API_KEY", "")}
    num_to_upgrade = get_env_int("NUM_MOVIES_TO_UPGRADE", 1)

    logger.info("Starting Radarr upgrade process")

    quality_profiles = api_get(f"{base_url}{API_PATH}qualityprofile", headers)
    quality_scores = {q["id"]: q["cutoffFormatScore"] for q in quality_profiles}

    movies = api_get(f"{base_url}{API_PATH}movie", headers)
    upgrade_candidates: Dict[int, Dict[str, Any]] = {}

    for movie in movies:
        if not movie.get("monitored") or not movie.get("movieFileId"):
            continue

        file_data = api_get(f"{base_url}{API_PATH}moviefile/{movie['movieFileId']}", headers)
        profile_id = movie["qualityProfileId"]

        if file_data["customFormatScore"] < quality_scores[profile_id]:
            upgrade_candidates[movie["id"]] = {
                "title": movie["title"],
                "currentScore": file_data["customFormatScore"],
                "requiredScore": quality_scores[profile_id],
            }
        
    logger.info(f"Count of movies to upgrade: {len(upgrade_candidates)}")

    if not upgrade_candidates:
        logger.info("No Radarr movies found for upgrade.")
        return


    selected_ids = random.sample(list(upgrade_candidates.keys()), k=min(num_to_upgrade, len(upgrade_candidates)))
    logger.info(f"Selected movies for upgrade: {selected_ids}")

    tag_id = ensure_tag_exists(base_url, headers, UPGRADE_TAG)

    for movie_id in selected_ids:
        movie_url = f"{base_url}{API_PATH}movie/{movie_id}"
        movie_data = api_get(movie_url, headers)
        movie_data["tags"] = list(set(movie_data.get("tags", [])) | {tag_id})
        api_put(movie_url, headers, movie_data)
        logger.info(f"Tagged movie '{upgrade_candidates[movie_id]['title']}' with '{UPGRADE_TAG}'")

    api_post(f"{base_url}{API_PATH}command", headers, {"name": "MoviesSearch", "movieIds": selected_ids})
    logger.info("Triggered Radarr search command.")


# ----------------------
# Sonarr functions
# ----------------------
def run_sonarr_upgrade():
    sonarr_enabled = get_env_bool("PROCESS_SONARR")
    if not sonarr_enabled:
        return

    base_url = os.getenv("SONARR_URL", "")
    headers = {"Authorization": os.getenv("SONARR_API_KEY", "")}
    num_to_upgrade = get_env_int("NUM_EPISODES_TO_UPGRADE", 1)

    logger.info("Starting Sonarr upgrade process")

    quality_profiles = api_get(f"{base_url}{API_PATH}qualityprofile", headers)
    quality_scores = {q["id"]: q["cutoffFormatScore"] for q in quality_profiles}

    series_list = api_get(f"{base_url}{API_PATH}series", headers)
    upgrade_candidates: Dict[int, Dict[str, Any]] = {}

    for serie in series_list:
        profile_id = serie["qualityProfileId"]
        if serie["statistics"]["episodeFileCount"] == 0:
            continue

        episodes = api_get(f"{base_url}{API_PATH}episodefile?seriesId={serie['id']}", headers)
        for ep in episodes:
            if ep["customFormatScore"] < quality_scores[profile_id]:
                ep_data = api_get(f"{base_url}{API_PATH}episode?episodeFileId={ep['id']}", headers)
                episode = ep_data[0]
                if not episode.get("monitored"):
                    continue

                upgrade_candidates[episode["id"]] = {
                    "title": episode["title"],
                    "seriesId": serie["id"],
                    "currentScore": ep["customFormatScore"],
                    "requiredScore": quality_scores[profile_id],
                }
    logger.info(f"Count of episodes to upgrade: {len(upgrade_candidates)}")

    if not upgrade_candidates:
        logger.info("No Sonarr episodes found for upgrade.")
        return

    selected_ids = random.sample(list(upgrade_candidates.keys()), k=min(num_to_upgrade, len(upgrade_candidates)))
    logger.info(f"Selected episodes for upgrade: {selected_ids}")

    tag_id = ensure_tag_exists(base_url, headers, UPGRADE_TAG)

    for ep_id in selected_ids:
        series_id = upgrade_candidates[ep_id]["seriesId"]
        series_url = f"{base_url}{API_PATH}series/{series_id}"
        series_data = api_get(series_url, headers)
        series_data["tags"] = list(set(series_data.get("tags", [])) | {tag_id})
        api_put(series_url, headers, series_data)
        logger.info(f"Tagged series for episode '{upgrade_candidates[ep_id]['title']}' with '{UPGRADE_TAG}'")

    api_post(f"{base_url}{API_PATH}command", headers, {"name": "EpisodeSearch", "episodeIds": selected_ids})
    logger.info("Triggered Sonarr search command.")


# ----------------------
# Main
# ----------------------
if __name__ == "__main__":
    run_radarr_upgrade()
    run_sonarr_upgrade()
