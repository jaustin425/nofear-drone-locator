"""Loopback-only dashboard. Detection control requires explicitly selected input."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from .geocoding import AddressLookup, GeoapifyLookup
from .receiver import event
from .capture import CaptureError
from .history import HistoryStore
from starlette.concurrency import run_in_threadpool

STATIC = Path(__file__).parent / "static"


class AddressRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class ClearMapRequest(BaseModel):
    only_stale: Literal[True] = True


def create_app(replay=None, host="127.0.0.2", geocoder_url=None, geoapify_key=None, *, receiver=None):
    if (replay is None) == (receiver is None):
        raise ValueError('Choose replay or receiver')

    @asynccontextmanager
    async def lifespan(app):
        if receiver:
            receiver.begin()
        try:
            yield
        finally:
            if receiver:
                receiver.close()

    app = FastAPI(title="AetherLink Scout", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.event_clients = 0
    app.state.history = receiver.history if receiver else HistoryStore()
    app.state.geocoder = GeoapifyLookup(geoapify_key) if geoapify_key else AddressLookup(geocoder_url)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host, "localhost"])

    @app.middleware("http")
    async def local_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/api/replay")
    def replay_data():
        if receiver:
            raise HTTPException(409, 'Streaming mode: use the observation stream.')
        return replay

    @app.get("/api/health")
    def health():
        status = receiver.status() if receiver else None
        return {"mode": receiver.mode if receiver else replay["mode"], "tak_enabled": False,
                "live_capture": bool(receiver and receiver.mode == 'live_stream' and status['state'] == 'running'),
                "receiver": status,
                "address_lookup_enabled": bool(app.state.geocoder.endpoint),
                "address_provider": app.state.geocoder.provider if app.state.geocoder.endpoint else None}

    def check_local(request):
        origin = request.headers.get('origin')
        if (origin and origin != str(request.base_url).rstrip('/')) or request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403, 'Use the local dashboard.')

    @app.get('/api/receiver')
    def receiver_snapshot(request: Request):
        check_local(request)
        if not receiver:
            raise HTTPException(404, 'No streaming receiver in replay mode.')
        return receiver.snapshot()

    @app.get('/api/events')
    async def receiver_events(request: Request):
        check_local(request)
        if not receiver:
            raise HTTPException(404, 'No streaming receiver in replay mode.')
        if app.state.event_clients >= 8:
            raise HTTPException(429, 'Too many dashboard streams.')
        app.state.event_clients += 1

        async def updates():
            try:
                value = receiver.snapshot()
                yield event(value)
                cursor = value['sequence']
                session = value['status']['session_id']
                revision = value['status']['map_revision']
                while not await request.is_disconnected():
                    await asyncio.sleep(.25)
                    value = receiver.update(cursor)
                    if value['status']['session_id'] != session or value['status']['map_revision'] != revision:
                        value = receiver.snapshot()
                    yield event(value)
                    cursor = value['sequence']
                    session = value['status']['session_id']
                    revision = value['status']['map_revision']
            finally:
                app.state.event_clients -= 1

        return StreamingResponse(updates(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    async def control_receiver(request, action):
        check_local(request)
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(415, 'Detection controls require JSON from the local dashboard.')
        if not receiver:
            raise HTTPException(409, 'Detection controls are unavailable in historical replay.')
        try:
            await run_in_threadpool(action)
        except CaptureError as error:
            raise HTTPException(503, str(error)) from None
        return receiver.snapshot()

    @app.post('/api/detection/start')
    async def detection_start(request: Request):
        return await control_receiver(request, receiver.start_detection if receiver else None)

    @app.post('/api/detection/stop')
    async def detection_stop(request: Request):
        return await control_receiver(request, receiver.stop_detection if receiver else None)

    @app.get('/api/history')
    def history_list(request: Request, offset: int = Query(0, ge=0, le=1000)):
        check_local(request)
        return app.state.history.list(offset)

    @app.get('/api/history/{report_id}')
    def history_report(report_id: str, request: Request):
        check_local(request)
        value = app.state.history.get(report_id)
        if value is None:
            raise HTTPException(404, 'Saved report unavailable.')
        return value

    @app.post('/api/map/clear')
    def clear_map(body: ClearMapRequest, request: Request):
        check_local(request)
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(415, 'Map controls require JSON from the local dashboard.')
        if not receiver:
            raise HTTPException(409, 'Map clearing is available in streaming mode.')
        try:
            return receiver.clear_map(body.only_stale)
        except CaptureError as error:
            raise HTTPException(503, str(error)) from None

    @app.post("/api/address")
    async def address(body: AddressRequest, request: Request):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Address lookup must originate from the local dashboard.")
        if not app.state.geocoder.endpoint:
            raise HTTPException(503, "Configure a local or approved geocoder to enable address lookup.")
        try:
            return await app.state.geocoder.reverse(body.latitude, body.longitude)
        except Exception:
            raise HTTPException(502, "Address provider unavailable; coordinates remain usable.") from None

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
