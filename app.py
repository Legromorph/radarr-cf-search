from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import os
import requests
import random
import logging

# ----------------------
# Logger
# ----------------------
logger = logging.getLogger(__name__)
logging.basicConfig(
    filename='/config/output.log',
    encoding='utf-8',
    format='%(asctime)s %(message)s',
    datefmt='%m/%d/%Y %I:%M:%S %p',
    level=logging.INFO
)

# ----------------------
# Load .env
# ----------------------
load_dotenv(dotenv_path="/config/.env")

# ----------------------
# Radarr variables
# ----------------------
process_radarr_str = os.getenv("PROCESS_RADARR")
PROCESS_RADARR = process_radarr_str.lower() == "true" if process_radarr_str else False
RADARR_API_KEY = os.getenv("RADARR_API_KEY")
RADARR_URL = os.getenv("RADARR_URL")
NUM_MOVIES_TO_UPGRADE = int(os.getenv("NUM_MOVIES_TO_UPGRADE"))
logger.debug(f"Radarr API Key: {RADARR_API_KEY}, URL: {RADARR_URL}, Num to Upgrade: {NUM_MOVIES_TO_UPGRADE}")

MOVIE_ENDPOINT = "movie"
MOVIEFILE_ENDPOINT = "moviefile/"
API_PATH = "/api/v3/"
QUALITY_PROFILE_ENDPOINT = "qualityprofile"
COMMAND_ENDPOINT = "command"

# ----------------------
# Sonarr variables
# ----------------------
process_sonarr_str = os.getenv("PROCESS_SONARR")
PROCESS_SONARR = process_sonarr_str.lower() == "true" if process_sonarr_str else False
SONARR_API_KEY = os.getenv("SONARR_API_KEY")
SONARR_URL = os.getenv("SONARR_URL")
NUM_EPISODES_TO_UPGRADE = int(os.getenv("NUM_EPISODES_TO_UPGRADE"))
logger.debug(f"Sonarr API Key: {SONARR_API_KEY}, URL: {SONARR_URL}, Num to Upgrade: {NUM_EPISODES_TO_UPGRADE}")

SERIES_ENDPOINT = "series"
EPISODEFILE_ENDPOINT = "episodefile"
EPISODE_ENDPOINT = "episode"

# ----------------------
# Upgrade Tag
# ----------------------
UPGRADE_TAG = os.getenv("UPGRADE_TAG", "upgrade-de")

# ----------------------
# Radarr functions
# ----------------------
if PROCESS_RADARR:
    radarr_headers = {'Authorization': RADARR_API_KEY}
    quality_to_formats = {}
    movie_files = {}

    def get_radarr_quality_cutoff_scores():
        url = RADARR_URL + API_PATH + QUALITY_PROFILE_ENDPOINT
        for q in requests.get(url, headers=radarr_headers).json():
            quality_to_formats[q["id"]] = q["cutoffFormatScore"]

    def get_movies():
        logger.info("Querying Movies API")
        url = RADARR_URL + API_PATH + MOVIE_ENDPOINT
        return requests.get(url, headers=radarr_headers).json()

    def get_movie_files(movies):
        logger.info("Querying MovieFiles API")
        for movie in movies:
            is_monitored = str(movie["monitored"]).lower() == "true"
            if movie.get("movieFileId", 0) > 0 and is_monitored:
                url = RADARR_URL + API_PATH + MOVIEFILE_ENDPOINT + str(movie["movieFileId"])
                movie_file = requests.get(url, headers=radarr_headers).json()
                profile_id = movie["qualityProfileId"]
                if movie_file["customFormatScore"] < quality_to_formats[profile_id]:
                    movie_files[movie["id"]] = {
                        "title": movie["title"],
                        "customFormatScore": movie_file["customFormatScore"],
                        "wantedCustomFormatScore": quality_to_formats[profile_id]
                    }
        return movie_files

    def add_tag_to_movie(movie_id, tag_name):
        # Get or create tag
        url = RADARR_URL + API_PATH + "tag"
        tags = requests.get(url, headers=radarr_headers).json()
        tag_id = next((t["id"] for t in tags if t["label"] == tag_name), None)
        if not tag_id:
            tag_id = requests.post(url, headers=radarr_headers, json={"label": tag_name}).json()["id"]

        # Update movie with tag
        movie_url = RADARR_URL + API_PATH + "movie/" + str(movie_id)
        movie = requests.get(movie_url, headers=radarr_headers).json()
        movie["tags"] = movie.get("tags", []) + [tag_id]
        requests.put(movie_url, headers=radarr_headers, json=movie)

    # Execute Radarr upgrade
    logger.info("Querying Radarr Quality Custom Format Cutoff Scores")
    get_radarr_quality_cutoff_scores()
    movies = get_movies()
    movie_files = get_movie_files(movies)
    random_keys = list(set(random.choices(list(movie_files.keys()), k=NUM_MOVIES_TO_UPGRADE)))

    data = {"name": "MoviesSearch", "movieIds": random_keys}
    logger.info(f"Keys to search: {random_keys}")
    for key in random_keys:
        logger.info(f"Starting search for {movie_files[key]['title']}")
        add_tag_to_movie(key, UPGRADE_TAG)

    SEARCH_MOVIES_POST_API_CALL = RADARR_URL + API_PATH + COMMAND_ENDPOINT
    requests.post(SEARCH_MOVIES_POST_API_CALL, headers=radarr_headers, json=data)

# ----------------------
# Sonarr functions
# ----------------------
if PROCESS_SONARR:
    sonarr_headers = {'Authorization': SONARR_API_KEY}
    quality_to_formats = {}
    episode_files = {}

    def get_sonarr_quality_cutoff_scores():
        url = SONARR_URL + API_PATH + QUALITY_PROFILE_ENDPOINT
        for q in requests.get(url, headers=sonarr_headers).json():
            quality_to_formats[q["id"]] = q["cutoffFormatScore"]

    def get_series():
        logger.info("Querying Series API")
        url = SONARR_URL + API_PATH + SERIES_ENDPOINT
        return requests.get(url, headers=sonarr_headers).json()

    def get_episode_files(series_list):
        logger.info("Querying EpisodeFiles API")
        for serie in series_list:
            profile_id = serie["qualityProfileId"]
            if serie["statistics"]["episodeFileCount"] > 0:
                url = SONARR_URL + API_PATH + EPISODEFILE_ENDPOINT + f"?seriesId={serie['id']}"
                episodes = requests.get(url, headers=sonarr_headers).json()
                for episode in episodes:
                    if episode["customFormatScore"] < quality_to_formats[profile_id]:
                        ep_url = SONARR_URL + API_PATH + EPISODE_ENDPOINT + f"?episodeFileId={episode['id']}"
                        ep_data = requests.get(ep_url, headers=sonarr_headers).json()
                        if str(ep_data[0]["monitored"]).lower() == "true":
                            episode_files[ep_data[0]["id"]] = {
                                "title": ep_data[0]["title"],
                                "seriesId": serie["id"],
                                "customFormatScore": episode["customFormatScore"],
                                "wantedCustomFormatScore": quality_to_formats[profile_id]
                            }
        return episode_files

    def add_tag_to_series(series_id, tag_name):
        url = SONARR_URL + API_PATH + "tag"
        tags = requests.get(url, headers=sonarr_headers).json()
        tag_id = next((t["id"] for t in tags if t["label"] == tag_name), None)
        if not tag_id:
            tag_id = requests.post(url, headers=sonarr_headers, json={"label": tag_name}).json()["id"]

        series_url = SONARR_URL + API_PATH + "series/" + str(series_id)
        series_data = requests.get(series_url, headers=sonarr_headers).json()
        if tag_id not in series_data.get("tags", []):
            series_data["tags"] = series_data.get("tags", []) + [tag_id]
        requests.put(series_url, headers=sonarr_headers, json=series_data)

    # Execute Sonarr upgrade
    logger.info("Querying Sonarr Quality Custom Format Cutoff Scores")
    get_sonarr_quality_cutoff_scores()
    series_list = get_series()
    episode_files = get_episode_files(series_list)
    random_keys = list(set(random.choices(list(episode_files.keys()), k=NUM_EPISODES_TO_UPGRADE)))

    data = {"name": "EpisodeSearch", "episodeIds": random_keys}
    logger.info(f"Keys to search: {random_keys}")
    for key in random_keys:
        logger.info(f"Starting search for {episode_files[key]['title']}")
        add_tag_to_series(episode_files[key]["seriesId"], UPGRADE_TAG)

    SEARCH_EPISODES_POST_API_CALL = SONARR_URL + API_PATH + COMMAND_ENDPOINT
    requests.post(SEARCH_EPISODES_POST_API_CALL, headers=sonarr_headers, json=data)
