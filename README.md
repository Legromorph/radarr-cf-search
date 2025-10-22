# 🎬 Polishrr

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

```
2025-10-07 14:23:12 [INFO] Starting Radarr upgrade process
2025-10-07 14:23:12 [INFO] Tagged movie 'Inception' with 'upgrade-cf'
2025-10-07 14:23:12 [INFO] Triggered Radarr search command.
```


You can adjust the log level via the `LOG_LEVEL` environment variable (e.g. `DEBUG`, `INFO`, `WARNING`).

---

## 🐳 Docker installation

You can run **Polishrr** using Docker with the following configuration:

```yaml
services:
  polishrr:
    image: ghcr.io/legromorph/polishrr:latest
    container_name: polishrr
    environment:
      - CRON_SCHEDULE=0 * * * *   # default: run at every full hour
      - TZ=America/Los_Angeles    # set your desired timezone
    volumes:
      - /path/to/config:/config   # place your .env file here
```

## ▶️ Run the Script

# Automatic Run

The script runs automatically based on the **CRON_SCHEDULE** environment variable.

Default: 0 * * * * → runs every full hour

You can customize this value to any valid cron expression.

# Manual Run

If you want to run the script manually inside the container:
```bash
docker exec -it polishrr python app.py
```

Or, if running locally:
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

## 🌐 New: Polishrr Web Dashboard (v1.0)

Polishrr now includes a **modern, browser-based dashboard** for full visibility and control — no command line required.

---

### ✨ Main Features

#### 🧭 Overview
- Displays current **upgrade summaries** for Radarr and Sonarr.  
- Shows **below-cutoff** and **eligible items** in real time.

#### ⚙️ Manual Control
- Start upgrades manually for **Radarr**, **Sonarr**, or both.  
- Trigger **single upgrades** or **force upgrades** directly from the interface.

#### 🔄 Live Updates
- Real-time logs via **Server-Sent Events (SSE)**.  
- Instant feedback when upgrades start, finish, or fail.

#### 📋 Download & Upgrade Queues
- See all **active downloads**, **tagged**, and **eligible items**.  
- Clean, sortable tables with **clickable column headers**:  
  - Click on **Name** or **Status** to sort ascending or descending.  
  - Sorting preferences are remembered automatically.

#### ⚙️ Settings Management
- Adjust cron schedules, enable/disable Radarr or Sonarr processing, and set limits directly in the UI.  
- Save and test configuration changes instantly.

#### 💅 Modern Design
- Fully responsive HTML/CSS interface.  
- Styled with a **minimal dark theme**.  
- Built using **Vanilla JS + FastAPI backend** — fast, lightweight, and local.

---

### 🔒 Security
- Access protected by a **Bearer token** (`POLISHRR_TOKEN` environment variable).  
- Optional **IP allowlist** (`ALLOWED_IPS`) for restricted network access.  
- Tokens are securely compared using constant-time checks.

---

### 📄 License
This project is released under the **MIT License**.  
You are free to use, modify, and share it.

---

### 🤖 About This Project
This project’s code — including parts of the backend and web dashboard — was **mostly generated with AI assistance** and then **reviewed, analyzed, and refined by the author** for correctness, performance, and maintainability.


