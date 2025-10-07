# 🎬 Radarr & Sonarr Auto-Upgrader

This Python script automatically identifies and upgrades movies and TV episodes in **Radarr** and **Sonarr** based on their **Custom Format Scores**.  

It helps you keep your media library at the highest possible quality — without manual effort.

---

## 🚀 How It Works

### 🔹 Radarr
- The script fetches all movies from your Radarr library.  
- For each movie, it checks:
  - Is the movie **monitored**?  
  - Does it have an existing **file**?  
  - Is its **custom format score** below the required `cutoffFormatScore`?  
- If so, the movie is marked for upgrade and a **search command** is triggered in Radarr.  
- Movies that already have the **upgrade tag** (default: `upgrade-cf`) are **skipped**.  
- Once **all movies** are tagged, the script removes the tag from every movie — restarting the upgrade cycle.

### 🔹 Sonarr
- Works similarly to Radarr, but on the **episode** level:  
  - Episodes with a lower quality score than required are tagged and queued for search.  
  - Series already tagged are skipped.  
  - When all series are tagged, all tags are cleared to restart the process.

---

## ⚙️ Configuration

The script reads all settings from a `.env` file (expected path: `/config/.env`).

Example configuration:

```env
# General settings
LOG_LEVEL=INFO
UPGRADE_TAG=upgrade-cf

# Radarr
PROCESS_RADARR=true
RADARR_URL=http://localhost:7878
RADARR_API_KEY=your_radarr_api_key
NUM_MOVIES_TO_UPGRADE=2

# Sonarr
PROCESS_SONARR=true
SONARR_URL=http://localhost:8989
SONARR_API_KEY=your_sonarr_api_key
NUM_EPISODES_TO_UPGRADE=3
```

## 🧩 Process Overview

1. Load all environment variables.  
2. Ensure the upgrade tag (`UPGRADE_TAG`) exists in Radarr/Sonarr.  
3. Identify all upgrade candidates (below cutoff score).  
4. Randomly select a few and mark them with the upgrade tag.  
5. Trigger search commands to fetch better versions.  
6. Skip already tagged items.  
7. If **everything** is tagged → remove all tags → restart cycle.  

---

## 🪵 Logging

All events are logged to `/config/output_YYYY-MM-DD.log`.  
Example log output:

```2025-10-07 14:23:12 [INFO] Starting Radarr upgrade process
2025-10-07 14:23:12 [INFO] Tagged movie 'Inception' with 'upgrade-cf'
2025-10-07 14:23:12 [INFO] Triggered Radarr search command.
```


You can adjust the log level via the `LOG_LEVEL` environment variable (e.g. `DEBUG`, `INFO`, `WARNING`).

---

## 🖥️ Planned Feature: Web Interface

A **web interface** is planned to make the tool more user-friendly.  
It will allow you to:

- View real-time upgrade status  
- Start or pause processes manually  
- Browse logs and quality statistics  

---

## ▶️ Run the Script

```bash
python app.py
```

By default, both Radarr and Sonarr processes run:
```python
run_radarr_upgrade()
run_sonarr_upgrade()
```

To disable one, modify your .env:
```env
PROCESS_RADARR=true
PROCESS_SONARR=false
```

---
📄 License

This project is released under the MIT License.
You are free to use, modify, and share it.