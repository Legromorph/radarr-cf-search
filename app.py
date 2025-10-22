from __future__ import annotations

# ============================================================
# High-quality, modular Radarr/Sonarr backend
# - Robust HTTP client with retries/timeouts
# - Clear separation into functions/classes
# - Far fewer redundant API calls (parallelized where helpful)
# - Defensive error handling + structured logging
# - Type hints & dataclasses for maintainability
# - Drop-in replacement for original script
# ============================================================

import os
import json
import time
import math
import random
import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Iterable, Tuple
from collections import defaultdict

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

# Load .env as early as possible
load_dotenv(dotenv_path="/config/.env")


# ------------------------------------------------------------
# Helpers: environment parsing
# ------------------------------------------------------------
def get_env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def get_env_int(key: str, default: int = 0) -> int:
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except ValueError:
        return default


def get_env_str(key: str, default: str = "") -> str:
    val = os.getenv(key)
    return str(val).strip() if val is not None else default


# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------
LOG_FILE = f"/app/runtime/output_{time.strftime('%Y-%m-%d')}.log"
LOG_LEVEL = get_env_str("LOG_LEVEL", "INFO").upper()

logging.Formatter.converter = time.localtime
logging.basicConfig(
    filename=LOG_FILE,
    encoding="utf-8",
    format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger("arr-backend")


# ------------------------------------------------------------
# Global constants & settings
# ------------------------------------------------------------
API_PATH = "/api/v3/"
UPGRADE_TAG = get_env_str("UPGRADE_TAG", "upgrade-cf")
SETTINGS_FILE = "/app/config/settings.json"

# Maintains the IDs/titles that were upgraded/tagged in the most recent run
RECENT_UPGRADES: Dict[str, List[dict]] = {"radarr": [], "sonarr": []}

# HTTP tuning
DEFAULT_TIMEOUT = float(get_env_str("HTTP_TIMEOUT_SECONDS", "15"))
MAX_RETRIES = get_env_int("HTTP_MAX_RETRIES", 3)
BACKOFF_FACTOR = float(get_env_str("HTTP_BACKOFF_FACTOR", "0.5"))
MAX_WORKERS = max(2, get_env_int("MAX_PARALLEL_REQUESTS", 8))


# ------------------------------------------------------------
# Dataclasses for configuration
# ------------------------------------------------------------
@dataclass(frozen=True)
class RadarrConfig:
    enabled: bool
    base_url: str
    api_key: str
    num_to_upgrade: int


@dataclass(frozen=True)
class SonarrConfig:
    enabled: bool
    base_url: str
    api_key: str
    num_to_upgrade: int


@dataclass(frozen=True)
class AppConfig:
    radarr: RadarrConfig
    sonarr: SonarrConfig
    tag_name: str = UPGRADE_TAG
    api_path: str = API_PATH


def load_app_config() -> AppConfig:
    return AppConfig(
        radarr=RadarrConfig(
            enabled=get_env_bool("PROCESS_RADARR"),
            base_url=get_env_str("RADARR_URL"),
            api_key=get_env_str("RADARR_API_KEY"),
            num_to_upgrade=get_env_int("NUM_MOVIES_TO_UPGRADE", 1),
        ),
        sonarr=SonarrConfig(
            enabled=get_env_bool("PROCESS_SONARR"),
            base_url=get_env_str("SONARR_URL"),
            api_key=get_env_str("SONARR_API_KEY"),
            num_to_upgrade=get_env_int("NUM_EPISODES_TO_UPGRADE", 1),
        ),
        tag_name=get_env_str("UPGRADE_TAG", "upgrade-cf"),
        api_path=get_env_str("ARR_API_PATH", API_PATH),
    )


# ------------------------------------------------------------
# Resilient HTTP client
# ------------------------------------------------------------
class HttpClient:
    def __init__(self, headers: Optional[Dict[str, str]] = None) -> None:
        self.session: Session = requests.Session()
        retries = Retry(
            total=MAX_RETRIES,
            connect=MAX_RETRIES,
            read=MAX_RETRIES,
            status=MAX_RETRIES,
            backoff_factor=BACKOFF_FACTOR,
            allowed_methods=frozenset({"GET", "POST", "PUT", "DELETE"}),
            status_forcelist=(429, 500, 502, 503, 504),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=50)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update(headers or {})

    def _request(self, method: str, url: str, **kwargs) -> Any:
        timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
        resp: Response = self.session.request(method, url, timeout=timeout, **kwargs)
        # Raises HTTPError if 4xx/5xx
        resp.raise_for_status()
        if not resp.text:
            return {}
        try:
            return resp.json()
        except ValueError:
            # Sometimes some endpoints return plain text; fallback to text
            return resp.text

    def get(self, url: str, **kwargs) -> Any:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> Any:
        return self._request("POST", url, **kwargs)

    def put(self, url: str, **kwargs) -> Any:
        return self._request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs) -> Any:
        return self._request("DELETE", url, **kwargs)


# ------------------------------------------------------------
# Base Arr client (shared helpers)
# ------------------------------------------------------------
class ArrClient:
    def __init__(self, base_url: str, api_key: str, api_path: str) -> None:
        if not base_url:
            raise ValueError("Base URL must not be empty.")
        if not api_key:
            raise ValueError("API key must not be empty.")

        self.base = base_url.rstrip("/")
        self.api_path = api_path if api_path.startswith("/") else f"/{api_path}"
        self.client = HttpClient(headers={"Authorization": api_key})

    # URL builder
    def _url(self, *parts: str) -> str:
        joined = "/".join(p.strip("/") for p in parts if p)
        return f"{self.base}{self.api_path}{joined}"

    # Common helpers
    def ensure_tag(self, label: str) -> int:
        tags = self.client.get(self._url("tag"))
        if isinstance(tags, dict) and "records" in tags:
            tags = tags["records"]
        match = next((t["id"] for t in tags if t.get("label") == label), None)
        if match is not None:
            return int(match)
        created = self.client.post(self._url("tag"), json={"label": label})
        tag_id = int(created["id"])
        logger.info("Created new tag '%s' with id=%s", label, tag_id)
        return tag_id

    def quality_profiles_cutoff_scores(self) -> Dict[int, int]:
        profiles = self.client.get(self._url("qualityprofile"))
        return {int(p["id"]): int(p.get("cutoffFormatScore", 0)) for p in profiles}

    def queue(self) -> List[dict]:
        res = self.client.get(self._url("queue"))
        if isinstance(res, dict) and "records" in res:
            return list(res["records"])
        if isinstance(res, list):
            return res
        logger.warning("Queue returned unexpected payload type: %s", type(res))
        return []

    def command(self, name: str, **payload) -> Any:
        return self.client.post(self._url("command"), json={"name": name, **payload})


# ------------------------------------------------------------
# Radarr client
# ------------------------------------------------------------
class Radarr(ArrClient):
    def movies(self) -> List[dict]:
        return self.client.get(self._url("movie"))

    def movie(self, movie_id: int) -> dict:
        return self.client.get(self._url("movie", str(movie_id)))

    def movie_file(self, file_id: int) -> dict:
        return self.client.get(self._url("moviefile", str(file_id)))

    def update_movie(self, movie: dict) -> dict:
        return self.client.put(self._url("movie", str(movie["id"])), json=movie)

    def delete_movie_file(self, file_id: int) -> None:
        self.client.delete(self._url("moviefile", str(file_id)))

    def search_movies(self, movie_ids: Iterable[int]) -> Any:
        return self.command("MoviesSearch", movieIds=list(movie_ids))


# ------------------------------------------------------------
# Sonarr client
# ------------------------------------------------------------
class Sonarr(ArrClient):
    def series_list(self) -> List[dict]:
        return self.client.get(self._url("series"))

    def series(self, series_id: int) -> dict:
        return self.client.get(self._url("series", str(series_id)))

    def update_series(self, series: dict) -> dict:
        return self.client.put(self._url("series", str(series["id"])), json=series)

    def episode_file_list(self, series_id: int) -> List[dict]:
        return self.client.get(self._url("episodefile"), params={"seriesId": series_id})

    def episode(self, episode_id: int) -> dict | List[dict]:
        return self.client.get(self._url("episode", str(episode_id)))

    def delete_episode_file(self, file_id: int) -> None:
        self.client.delete(self._url("episodefile", str(file_id)))

    def search_episodes(self, episode_ids: Iterable[int]) -> Any:
        return self.command("EpisodeSearch", episodeIds=list(episode_ids))


# ------------------------------------------------------------
# Performance helpers
# ------------------------------------------------------------
def parallel_map(func, items: Iterable[Any], max_workers: int = MAX_WORKERS) -> List[Any]:
    """Parallel map with bounded threads + graceful failures."""
    results: List[Any] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(func, item): item for item in items}
        for fut in as_completed(future_map):
            try:
                results.append(fut.result())
            except Exception as e:
                logger.warning("Parallel task failed for %r: %s", future_map[fut], e)
    return results


# ------------------------------------------------------------
# Radarr logic
# ------------------------------------------------------------
def collect_radarr_upgrade_candidates(rad: Radarr, tag_id: int) -> Dict[int, Dict[str, Any]]:
    """Return movieId -> info for items below cutoff and not tagged."""
    q_scores = rad.quality_profiles_cutoff_scores()
    movies = [m for m in rad.movies() if m.get("monitored") and m.get("movieFileId")]

    # Fetch current scores in parallel (bottleneck in original)
    def fetch_score(m: dict) -> Tuple[int, int, str]:
        mf = rad.movie_file(int(m["movieFileId"]))
        return int(m["id"]), int(mf.get("customFormatScore", 0)), str(m.get("title", ""))

    fetched: List[Tuple[int, int, str]] = parallel_map(fetch_score, movies)

    candidates: Dict[int, Dict[str, Any]] = {}
    tagged = 0
    for m in movies:
        mid = int(m["id"])
        profile_id = int(m.get("qualityProfileId"))
        cutoff = int(q_scores.get(profile_id, 0))
        is_tagged = tag_id in m.get("tags", [])
        if is_tagged:
            tagged += 1
        tup = next((t for t in fetched if t[0] == mid), None)
        if not tup:
            continue
        _, current, title = tup
        if current < cutoff and not is_tagged:
            candidates[mid] = {
                "title": title,
                "currentScore": current,
                "requiredScore": cutoff,
            }

    logger.info(
        "Radarr: movies=%s below_cutoff_unTagged=%s already_tagged=%s",
        len(movies), len(candidates), tagged
    )
    return candidates


def run_radarr_upgrade(cfg: AppConfig) -> None:
    if not cfg.radarr.enabled:
        logger.info("Radarr disabled (PROCESS_RADARR=False)")
        return

    logger.info("Starting Radarr upgrade cycle...")
    rad = Radarr(cfg.radarr.base_url, cfg.radarr.api_key, cfg.api_path)
    tag_id = rad.ensure_tag(cfg.tag_name)

    movies = rad.movies()
    if not movies:
        logger.info("Radarr returned 0 movies.")
        return

    # If all movies have the tag, remove it to restart the cycle
    if all(tag_id in (m.get("tags", []) or []) for m in movies):
        logger.info("All movies have the upgrade tag. Removing to restart cycle...")
        for m in movies:
            m["tags"] = [t for t in m.get("tags", []) if t != tag_id]
            rad.update_movie(m)
        logger.info("Upgrade tag removed from all movies.")
        return

    candidates = collect_radarr_upgrade_candidates(rad, tag_id)
    if not candidates:
        logger.info("No Radarr movies found for upgrade.")
        return

    k = min(cfg.radarr.num_to_upgrade, len(candidates))
    selected_ids = random.sample(list(candidates.keys()), k=k)
    logger.info("Radarr selected movie IDs for upgrade: %s", selected_ids)

    RECENT_UPGRADES["radarr"].clear()
    for mid in selected_ids:
        m = rad.movie(mid)
        m["tags"] = sorted(set(m.get("tags", [])) | {tag_id})
        rad.update_movie(m)
        RECENT_UPGRADES["radarr"].append({"id": mid, "title": candidates[mid]["title"]})
        logger.info("Tagged movie '%s' with '%s'", candidates[mid]["title"], cfg.tag_name)

    rad.search_movies(selected_ids)
    logger.info("Triggered Radarr MoviesSearch.")


# ------------------------------------------------------------
# Sonarr logic
# ------------------------------------------------------------
def collect_sonarr_upgrade_candidates(son: Sonarr, tag_id: int) -> Dict[int, Dict[str, Any]]:
    """
    Return episodeId -> info for episodes below cutoff where the SERIES is not tagged.
    To reduce calls, we fetch all episode files per series and compute locally.
    """
    q_scores = son.quality_profiles_cutoff_scores()
    series_list = son.series_list()
    candidates: Dict[int, Dict[str, Any]] = {}

    for serie in series_list:
        profile_id = int(serie.get("qualityProfileId"))
        cutoff = int(q_scores.get(profile_id, 0))
        if int(serie.get("statistics", {}).get("episodeFileCount", 0)) == 0:
            continue

        is_series_tagged = tag_id in (serie.get("tags", []) or [])
        if is_series_tagged:
            # If the series is already tagged for upgrade, skip adding more
            continue

        series_id = int(serie["id"])
        try:
            episode_files = son.episode_file_list(series_id)
        except Exception as e:
            logger.warning("Failed to fetch episode files for series %s: %s", series_id, e)
            continue

        for epf in episode_files:
            current = int(epf.get("customFormatScore", 0))
            if current < cutoff:
                # Sonarr doesn't expose episodeNumber/seasonNumber on episodefile in all versions
                # We'll probe episode endpoint by episodeFileId to find an "episode"
                # In many deployments `GET /episode?episodeFileId=...` returns a list; here we emulate with /episode/<id> path fallback from caller side.
                # We can't rely on that being cheap, so we avoid it here. We'll still mark candidate using episodefile id.
                candidates[int(epf["id"])] = {
                    "title": f"{serie.get('title', 'Series')} (EpisodeFile {epf['id']})",
                    "seriesId": series_id,
                    "currentScore": current,
                    "requiredScore": cutoff,
                }

    logger.info(
        "Sonarr: series=%s below_cutoff_unTagged_episodeFiles=%s",
        len(series_list), len(candidates)
    )
    return candidates


def run_sonarr_upgrade(cfg: AppConfig) -> None:
    if not cfg.sonarr.enabled:
        logger.info("Sonarr disabled (PROCESS_SONARR=False)")
        return

    logger.info("Starting Sonarr upgrade cycle...")
    son = Sonarr(cfg.sonarr.base_url, cfg.sonarr.api_key, cfg.api_path)
    tag_id = son.ensure_tag(cfg.tag_name)

    candidates = collect_sonarr_upgrade_candidates(son, tag_id)
    if not candidates:
        logger.info("No Sonarr episodes found for upgrade.")
        return

    k = min(cfg.sonarr.num_to_upgrade, len(candidates))
    selected_ids = random.sample(list(candidates.keys()), k=k)
    logger.info("Sonarr selected episodeFile-based IDs for upgrade: %s", selected_ids)

    RECENT_UPGRADES["sonarr"].clear()
    for ep_id in selected_ids:
        series_id = int(candidates[ep_id]["seriesId"])
        serie = son.series(series_id)
        serie["tags"] = sorted(set(serie.get("tags", [])) | {tag_id})
        son.update_series(serie)

        RECENT_UPGRADES["sonarr"].append({
            "id": ep_id,
            "title": candidates[ep_id]["title"],
            "seriesId": series_id,
        })
        logger.info("Tagged series '%s' for episodeFile %s with '%s'", serie.get("title"), ep_id, UPGRADE_TAG)

    # Instruct Sonarr to search by episodes: we need episode IDs, not episodeFile IDs.
    # We best-effort map episodeFile -> episodeId via GET /episode?episodeFileId=...
    def episode_ids_for_episodefile(eid: int) -> List[int]:
        try:
            res = son.client.get(son._url("episode"), params={"episodeFileId": eid})
            if isinstance(res, list) and res:
                return [int(x["id"]) for x in res]
            if isinstance(res, dict) and "id" in res:
                return [int(res["id"])]
        except Exception as e:
            logger.warning("Failed to resolve episode IDs for episodeFile %s: %s", eid, e)
        return []

    all_episode_ids: List[int] = []
    for epf_id in selected_ids:
        all_episode_ids.extend(episode_ids_for_episodefile(epf_id))
    all_episode_ids = sorted(set(all_episode_ids))

    if all_episode_ids:
        son.search_episodes(all_episode_ids)
        logger.info("Triggered Sonarr EpisodeSearch for %s episodes.", len(all_episode_ids))
    else:
        logger.info("Could not resolve any episode IDs from episodeFile IDs; skipping EpisodeSearch.")


# ------------------------------------------------------------
# Status & queue helpers (public API)
# ------------------------------------------------------------
def get_upgrade_status(detailed: bool = False) -> dict:
    """
    Returns how many movies/episodes are below cutoff and how many are upgradeable.
    """
    cfg = load_app_config()
    status = {
        "radarr": {"total_below_cutoff": 0, "eligible_for_upgrade": 0, "items": []},
        "sonarr": {"total_below_cutoff": 0, "eligible_for_upgrade": 0, "items": []},
    }
    logger.info("Collecting upgrade statistics... detailed=%s", detailed)

    # Radarr
    try:
        if cfg.radarr.enabled:
            rad = Radarr(cfg.radarr.base_url, cfg.radarr.api_key, cfg.api_path)
            tag_id = rad.ensure_tag(cfg.tag_name)
            q_scores = rad.quality_profiles_cutoff_scores()
            movies = [m for m in rad.movies() if m.get("monitored") and m.get("movieFileId")]

            def fetch_tuple(m: dict) -> Tuple[int, str, int, int, bool]:
                mf = rad.movie_file(int(m["movieFileId"]))
                score = int(mf.get("customFormatScore", 0))
                cutoff = int(q_scores.get(int(m["qualityProfileId"]), 0))
                tagged = tag_id in (m.get("tags", []) or [])
                return int(m["id"]), str(m.get("title", "")), score, cutoff, tagged

            fetched = parallel_map(fetch_tuple, movies)
            for mid, title, score, cutoff, tagged in fetched:
                if score < cutoff:
                    status["radarr"]["total_below_cutoff"] += 1
                    if not tagged:
                        status["radarr"]["eligible_for_upgrade"] += 1
                if detailed:
                    status["radarr"]["items"].append({
                        "id": mid,
                        "title": title,
                        "score": score,
                        "cutoff": cutoff,
                        "tagged": tagged,
                    })
            logger.info(
                "Radarr stats: below=%s eligible=%s",
                status["radarr"]["total_below_cutoff"], status["radarr"]["eligible_for_upgrade"]
            )
        else:
            logger.info("Radarr disabled.")
    except Exception as e:
        logger.exception("Error fetching Radarr stats:")
        status["radarr_error"] = str(e)

    # Sonarr
    try:
        if cfg.sonarr.enabled:
            son = Sonarr(cfg.sonarr.base_url, cfg.sonarr.api_key, cfg.api_path)
            tag_id = son.ensure_tag(cfg.tag_name)
            q_scores = son.quality_profiles_cutoff_scores()
            series_list = son.series_list()

            for serie in series_list:
                profile_id = int(serie.get("qualityProfileId"))
                cutoff = int(q_scores.get(profile_id, 0))
                series_tagged = tag_id in (serie.get("tags", []) or [])
                if int(serie.get("statistics", {}).get("episodeFileCount", 0)) == 0:
                    continue

                episode_files = son.episode_file_list(int(serie["id"]))
                for epf in episode_files:
                    score = int(epf.get("customFormatScore", 0))
                    if score < cutoff:
                        status["sonarr"]["total_below_cutoff"] += 1
                        if not series_tagged:
                            status["sonarr"]["eligible_for_upgrade"] += 1
                        if detailed:
                            status["sonarr"]["items"].append({
                                "id": int(epf["id"]),
                                "series": serie.get("title"),
                                "episodeFileId": int(epf["id"]),
                                "score": score,
                                "cutoff": cutoff,
                                "tagged": series_tagged,
                            })

            logger.info(
                "Sonarr stats: below=%s eligible=%s",
                status["sonarr"]["total_below_cutoff"], status["sonarr"]["eligible_for_upgrade"]
            )
        else:
            logger.info("Sonarr disabled.")
    except Exception as e:
        logger.exception("Error fetching Sonarr stats:")
        status["sonarr_error"] = str(e)

    return status


def get_download_queue(tagged_only: bool = False) -> dict:
    """
    Return current Radarr & Sonarr download queues with status info.
    If tagged_only=True, only returns the recently tagged items cache (fast path).
    """
    if tagged_only:
        return {
            "radarr": RECENT_UPGRADES.get("radarr", []),
            "sonarr": RECENT_UPGRADES.get("sonarr", []),
        }

    cfg = load_app_config()
    data = {"radarr": [], "sonarr": []}

    # RADARR
    try:
        if cfg.radarr.enabled:
            rad = Radarr(cfg.radarr.base_url, cfg.radarr.api_key, cfg.api_path)
            items = rad.queue()
            for item in items:
                if not isinstance(item, dict):
                    continue
                data["radarr"].append({
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "protocol": item.get("protocol"),
                    "size": round(float(item.get("size", 0)) / (1024 ** 3), 2),
                    "sizeleft": round(float(item.get("sizeleft", 0)) / (1024 ** 3), 2),
                    "timeleft": item.get("timeleft"),
                    "errorMessage": item.get("errorMessage"),
                    "indexer": item.get("indexer"),
                    "downloadId": item.get("downloadId"),
                })
    except Exception as e:
        logger.exception("Radarr queue fetch failed:")
        data["radarr_error"] = str(e)

    # SONARR
    try:
        if cfg.sonarr.enabled:
            son = Sonarr(cfg.sonarr.base_url, cfg.sonarr.api_key, cfg.api_path)
            tag_id = son.ensure_tag(cfg.tag_name)
            items = son.queue()

            series_cache: Dict[int, dict] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                series_id = item.get("seriesId")
                if not series_id:
                    continue

                if series_id not in series_cache:
                    try:
                        series_cache[series_id] = son.series(int(series_id))
                    except Exception as e:
                        logger.warning("Failed to fetch Sonarr series %s: %s", series_id, e)
                        series_cache[series_id] = {"title": f"Series {series_id}", "tags": []}

                serie = series_cache[series_id]
                if tagged_only and tag_id not in (serie.get("tags", []) or []):
                    continue

                # Format SxxExx if available
                season = item.get("seasonNumber")
                ep_obj = item.get("episode") if isinstance(item.get("episode"), dict) else {}
                epnum = ep_obj.get("episodeNumber")
                ep_label = "-"
                try:
                    if season is not None and epnum is not None:
                        ep_label = f"S{int(season):02d}E{int(epnum):02d}"
                    elif season is not None:
                        ep_label = f"S{int(season):02d}"
                except Exception:
                    ep_label = f"S{season}E{epnum}" if epnum is not None else f"S{season}"

                data["sonarr"].append({
                    "series": serie.get("title", "-"),
                    "episode": ep_label,
                    "status": item.get("status", "-"),
                    "protocol": item.get("protocol", "-"),
                    "size": round(float(item.get("size", 0)) / (1024 ** 3), 2),
                    "sizeleft": round(float(item.get("sizeleft", 0)) / (1024 ** 3), 2),
                    "timeleft": item.get("timeleft", "-"),
                    "indexer": item.get("indexer", "-"),
                    "downloadId": item.get("downloadId"),
                })
            logger.info("Fetched %s Sonarr queue items", len(data["sonarr"]))
    except Exception as e:
        logger.exception("Sonarr queue fetch failed:")
        data["sonarr_error"] = str(e)

    return data


def get_eligible_items() -> dict:
    """
    Return Radarr & Sonarr items that are below cutoff and NOT tagged for upgrade.
    """
    cfg = load_app_config()
    out = {"radarr": [], "sonarr": []}

    # Radarr
    try:
        if cfg.radarr.enabled:
            rad = Radarr(cfg.radarr.base_url, cfg.radarr.api_key, cfg.api_path)
            tag_id = rad.ensure_tag(cfg.tag_name)
            q_scores = rad.quality_profiles_cutoff_scores()
            movies = [m for m in rad.movies() if m.get("monitored") and m.get("movieFileId")]

            def fetch_tuple(m: dict) -> Tuple[int, str, int, int, bool]:
                mf = rad.movie_file(int(m["movieFileId"]))
                score = int(mf.get("customFormatScore", 0))
                cutoff = int(q_scores.get(int(m["qualityProfileId"]), 0))
                tagged = tag_id in (m.get("tags", []) or [])
                return int(m["id"]), str(m.get("title", "")), score, cutoff, tagged

            for mid, title, score, cutoff, tagged in parallel_map(fetch_tuple, movies):
                if score < cutoff and not tagged:
                    out["radarr"].append({
                        "id": mid,
                        "title": title,
                        "status": f"Score {score} / {cutoff}",
                        "score": score,
                        "cutoff": cutoff,
                    })
    except Exception as e:
        logger.exception("Eligible Radarr fetch failed:")
        out["radarr_error"] = str(e)

    # Sonarr
    try:
        if cfg.sonarr.enabled:
            son = Sonarr(cfg.sonarr.base_url, cfg.sonarr.api_key, cfg.api_path)
            tag_id = son.ensure_tag(cfg.tag_name)
            q_scores = son.quality_profiles_cutoff_scores()
            series_list = son.series_list()

            for serie in series_list:
                if int(serie.get("statistics", {}).get("episodeFileCount", 0)) == 0:
                    continue
                series_tagged = tag_id in (serie.get("tags", []) or [])
                if series_tagged:
                    continue

                cutoff = int(q_scores.get(int(serie.get("qualityProfileId")), 0))
                for epf in son.episode_file_list(int(serie["id"])):
                    score = int(epf.get("customFormatScore", 0))
                    if score < cutoff:
                        out["sonarr"].append({
                            "id": int(epf["id"]),
                            "series": serie.get("title"),
                            "episode": f"EpisodeFile {int(epf['id'])}",
                            "status": f"Score {score} / {cutoff}",
                            "score": score,
                            "cutoff": cutoff,
                        })
    except Exception as e:
        logger.exception("Eligible Sonarr fetch failed:")
        out["sonarr_error"] = str(e)

    return out


def get_recent_upgrades() -> dict:
    """Return items that were tagged by the last runs."""
    return RECENT_UPGRADES


# ------------------------------------------------------------
# Direct upgrade actions
# ------------------------------------------------------------
def upgrade_single_item(target: str, item_id: int):
    """
    Tag and trigger search for a single Radarr/Sonarr item.
    For Sonarr, item_id should be an EPISODE ID (not episodeFileId).
    """
    cfg = load_app_config()
    if target == "radarr":
        rad = Radarr(cfg.radarr.base_url, cfg.radarr.api_key, cfg.api_path)
        tag_id = rad.ensure_tag(cfg.tag_name)
        movie = rad.movie(int(item_id))
        movie["tags"] = sorted(set(movie.get("tags", [])) | {tag_id})
        rad.update_movie(movie)
        rad.search_movies([int(item_id)])
        logger.info("Triggered upgrade for Radarr movie '%s' (id=%s)", movie.get("title"), item_id)
        return {"ok": True}

    if target == "sonarr":
        son = Sonarr(cfg.sonarr.base_url, cfg.sonarr.api_key, cfg.api_path)
        tag_id = son.ensure_tag(cfg.tag_name)
        episode = son.episode(int(item_id))
        # Sonarr can return list or single dict
        if isinstance(episode, list) and episode:
            episode = episode[0]
        series_id = int(episode.get("seriesId") or 0)
        if not series_id:
            raise ValueError(f"No seriesId found for episode {item_id}")
        serie = son.series(series_id)
        serie["tags"] = sorted(set(serie.get("tags", [])) | {tag_id})
        son.update_series(serie)
        son.search_episodes([int(item_id)])
        logger.info("Triggered upgrade for Sonarr episode id=%s (series '%s')", item_id, serie.get("title"))
        return {"ok": True}

    raise ValueError("Invalid target (expected 'radarr' or 'sonarr').")


def force_upgrade_single_item(target: str, item_id: int):
    """
    Delete existing file and trigger forced search for a single item.
    For Sonarr, item_id should be an EPISODE ID.
    """
    cfg = load_app_config()

    if target == "radarr":
        rad = Radarr(cfg.radarr.base_url, cfg.radarr.api_key, cfg.api_path)
        movie = rad.movie(int(item_id))
        file_id = movie.get("movieFileId")
        if file_id:
            try:
                rad.delete_movie_file(int(file_id))
                logger.info("Deleted movie file for Radarr movie id=%s", item_id)
            except Exception as e:
                logger.warning("Failed deleting Radarr file %s: %s", file_id, e)
        rad.search_movies([int(item_id)])
        return {"ok": True}

    if target == "sonarr":
        son = Sonarr(cfg.sonarr.base_url, cfg.sonarr.api_key, cfg.api_path)
        episode = son.episode(int(item_id))
        if isinstance(episode, list) and episode:
            episode = episode[0]
        file_id = episode.get("episodeFileId")
        if file_id:
            try:
                son.delete_episode_file(int(file_id))
                logger.info("Deleted episode file for Sonarr episode id=%s", item_id)
            except Exception as e:
                logger.warning("Failed deleting Sonarr episode file %s: %s", file_id, e)
        son.search_episodes([int(item_id)])
        return {"ok": True}

    raise ValueError("Invalid target (expected 'radarr' or 'sonarr').")


# ------------------------------------------------------------
# Settings persistence
# ------------------------------------------------------------
def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load settings file %s: %s", SETTINGS_FILE, e)
    return {
        "cron": "*/5 * * * *",
        "process_radarr": True,
        "process_sonarr": True,
        "num_movies": 1,
        "num_episodes": 1,
        "force_enabled": False,
    }


def save_settings(cfg: dict):
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error("Failed to save settings: %s", e)
        raise


# ------------------------------------------------------------
# Main entrypoint
# ------------------------------------------------------------
def main() -> None:
    cfg = load_app_config()
    try:
        run_radarr_upgrade(cfg)
    except Exception as e:
        logger.exception("Radarr upgrade cycle failed:")

    try:
        run_sonarr_upgrade(cfg)
    except Exception as e:
        logger.exception("Sonarr upgrade cycle failed:")


if __name__ == "__main__":
    main()
