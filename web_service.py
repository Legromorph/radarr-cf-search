# web_service.py
import os, hmac, hashlib, asyncio, ipaddress, datetime as dt
from typing import Optional, AsyncGenerator
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException, Depends, Body
from fastapi.responses import PlainTextResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import traceback

# Importiere deine bestehenden Upgrade-Funktionen aus app.py:
from app import (
    run_radarr_upgrade,
    run_sonarr_upgrade,
    get_upgrade_status,
    get_recent_upgrades,
    get_download_queue,
    upgrade_single_item,
    force_upgrade_single_item,
    get_eligible_items
)

app = FastAPI(title="Polishrr Web Service", version="1.0")

POLISHRR_TOKEN = os.environ.get("POLISHRR_TOKEN", "")
ALLOWED_IPS = [ip.strip() for ip in os.environ.get("ALLOWED_IPS", "").split(",") if ip.strip()]
RUN_LOCK = asyncio.Lock()
LAST_STATUS = {"started": None, "finished": None, "running": False, "last_result": None}
EVENT_QUEUE: "asyncio.Queue[str]" = asyncio.Queue()

def _ct_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())

def _client_allowed(ip: str) -> bool:
    if not ALLOWED_IPS:
        return True
    try:
        ip_addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for net in ALLOWED_IPS:
        try:
            if ip_addr in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            if ip == net:
                return True
    return False

async def _auth(request: Request):
    # IP allowlist
    client_ip = request.headers.get("x-forwarded-for", request.client.host)
    if not _client_allowed(client_ip):
        raise HTTPException(status_code=403, detail="Forbidden")
    # Bearer token
    auth = request.headers.get("authorization", "")
    if not POLISHRR_TOKEN:
        raise HTTPException(status_code=503, detail="Service token not configured")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth.split(" ", 1)[1].strip()
    if not _ct_equals(token, POLISHRR_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid token")

class TriggerBody(BaseModel):
    target: Optional[str] = "both"  # "radarr" | "sonarr" | "both"

async def _run_and_stream(target: str):
    await EVENT_QUEUE.put(f"event:info\ndata: run_start {target} {dt.datetime.utcnow().isoformat()}Z\n\n")
    try:
        if target in ("radarr", "both"):
            await EVENT_QUEUE.put("event:info\ndata: starting radarr\n\n")
            run_radarr_upgrade()  # synchron – läuft im Threadpool von FastAPI-BackgroundTasks okay
            await EVENT_QUEUE.put("event:info\ndata: finished radarr\n\n")
        if target in ("sonarr", "both"):
            await EVENT_QUEUE.put("event:info\ndata: starting sonarr\n\n")
            run_sonarr_upgrade()
            await EVENT_QUEUE.put("event:info\ndata: finished sonarr\n\n")
        await EVENT_QUEUE.put("event:done\ndata: ok\n\n")
        return {"ok": True}
    except Exception as e:
        await EVENT_QUEUE.put(f"event:error\ndata: {type(e).__name__}: {e}\n\n")
        return {"ok": False, "error": str(e)}
    


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"

@app.get("/api/status")
async def status(_: None = Depends(_auth)):
    return LAST_STATUS

@app.get("/api/upgrade-summary")
async def upgrade_summary(_: None = Depends(_auth)):
    return get_upgrade_status()

@app.post("/api/trigger")
async def trigger(body: TriggerBody, background: BackgroundTasks, request: Request, _: None = Depends(_auth)):
    if RUN_LOCK.locked():
        raise HTTPException(status_code=409, detail="Run already in progress")
    LAST_STATUS.update({"started": dt.datetime.utcnow().isoformat()+"Z", "finished": None, "running": True, "last_result": None})
    async def _job():
        async with RUN_LOCK:
            res = await _run_and_stream(body.target or "both")
            LAST_STATUS.update({"finished": dt.datetime.utcnow().isoformat()+"Z", "running": False, "last_result": res})
    background.add_task(_job)
    return {"accepted": True}

@app.get("/api/events")
async def events() -> StreamingResponse:
    async def gen() -> AsyncGenerator[bytes, None]:
        yield b": stream start\n\n"
        while True:
            msg = await EVENT_QUEUE.get()
            yield msg.encode("utf-8")
    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@app.get("/api/eligible")
async def eligible(_: None = Depends(_auth)):
    return get_upgrade_status(detailed=True) 

@app.get("/api/recent-upgrades")
async def recent_upgrades(_: None = Depends(_auth)):
    return get_recent_upgrades()

@app.get("/api/download-queue")
async def download_queue(tagged: bool = False, eligible: bool = False, _: None = Depends(_auth)):
    if eligible:
        return get_eligible_items()
    return get_download_queue(tagged_only=tagged)
    
@app.post("/api/upgrade-item")
async def upgrade_item(body: dict = Body(...), _: None = Depends(_auth)):
    target = body.get("target")
    item_id = body.get("id")
    if not target or not item_id:
        raise HTTPException(status_code=400, detail="Missing target or id")

    try:
        result = upgrade_single_item(target, int(item_id))
        return result
    except Exception as e:
        import logging
        logging.exception(f"❌ upgrade_item failed for {target} id={item_id}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/force-upgrade-item")
async def force_upgrade_item(body: dict = Body(...), _: None = Depends(_auth)):
    target = body.get("target")
    item_id = body.get("id")
    if not target or not item_id:
        raise HTTPException(status_code=400, detail="Missing target or id")
    try:
        result = force_upgrade_single_item(target, int(item_id))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    


# Static mount
app.mount("/static", StaticFiles(directory="/app/static"), name="static")
app.mount("/assets", StaticFiles(directory="/app/assets"), name="assets")

@app.get("/", response_class=HTMLResponse)
async def root():
    try:
        with open("/app/static/status.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Polishrr</h1><p>No static page found.</p>"
