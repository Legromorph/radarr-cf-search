# web_service.py
import os, hmac, hashlib, asyncio, ipaddress, datetime as dt
from typing import Optional, AsyncGenerator
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException, Depends
from fastapi.responses import PlainTextResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

# Importiere deine bestehenden Upgrade-Funktionen aus app.py:
from app import run_radarr_upgrade, run_sonarr_upgrade  # ggf. anpassen

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
async def events(_: None = Depends(_auth)) -> StreamingResponse:
    async def gen() -> AsyncGenerator[bytes, None]:
        # initial comment to keep connection open
        yield b": stream start\n\n"
        while True:
            msg = await EVENT_QUEUE.get()
            yield msg.encode("utf-8")
    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
