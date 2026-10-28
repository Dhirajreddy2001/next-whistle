from contextlib import asynccontextmanager
import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from next_whistle_core.api.schemas import (
    GenerationRequest, LiveMatchState, MatchCatalog,
    RefereeQuestionRequest, RefreshSessionRequest, Schedule,
)
from next_whistle_core.config import Settings
from next_whistle_core.security import hash_refresh_token, issue_access_token, read_access_token
from next_whistle_core.services.schedule import build_schedule, playable_for_slot, slot_of, window_offset_ms
from next_whistle_core.services.replay import player_summary, state_at, team_stats, visible_events
from next_whistle_core.services.agent_client import AgentServiceClient
from next_whistle_core.services.ask import answer_without_model, load_rule_index
from next_whistle_core.services.narration import explain_fallback, score_at
from next_whistle_shared.ask import sanitize_question
from next_whistle_core.services.generation import (
    build_agent_request, build_law_request, fallback_response, project_root, referee_fallback, supported_question,
)
from next_whistle_core.store.datasets import Dataset, MatchData, load_dataset
from next_whistle_core.limits import Busy, GenerationGate, SlidingWindowLimiter, client_ip, hash_ip
from next_whistle_core.store.factory import open_stores
from next_whistle_core.store.ops import CACHE_CUTOFF_WINDOW_MS

ACCESS_TOKEN_SECONDS = 15 * 60
REFRESH_TOKEN_SECONDS = 7 * 24 * 60 * 60


def load_signing_key(settings: Settings) -> bytes:
    """A configured key is required in production. Locally a key is created once and reused, so restarts keep sessions."""
    if settings.session_signing_key:
        if settings.environment == "production" and len(settings.session_signing_key) < 32:
            raise RuntimeError("SESSION_SIGNING_KEY must be at least 32 characters in production")
        return settings.session_signing_key.encode()
    if settings.environment == "production":
        raise RuntimeError("SESSION_SIGNING_KEY is required in production")
    path = settings.session_db_path.with_name("session.key")
    if path.is_file():
        return path.read_bytes()
    key = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key)
    return key


IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Resource-Policy": "same-site",
}


class ApiError(HTTPException):
    """An HTTP error that also carries a stable machine-readable code (LLD section 13)."""

    def __init__(self, status: int, title: str, code: str, headers: dict[str, str] | None = None):
        super().__init__(status, title, headers=headers)
        self.code = code


def retry_after(seconds: int) -> dict[str, str]:
    return {"Retry-After": str(max(1, int(seconds)))}


def camelize(value):
    if isinstance(value, list):
        return [camelize(item) for item in value]
    if isinstance(value, dict):
        return {key.split("_")[0] + "".join(part.title() for part in key.split("_")[1:]): camelize(item) for key, item in value.items()}
    return value


def create_app(
    settings: Settings | None = None,
    now_ms: Callable[[], int] | None = None,
    agent_client=None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.dataset = load_dataset(settings.dataset_path)
        app.state.catalog = MatchCatalog.model_validate({
            **app.state.dataset.catalog,
            "matches": [{**row, "duration_ms": app.state.dataset.matches[row["match_id"]].duration_ms} for row in app.state.dataset.catalog["matches"]],
        })
        app.state.session_signing_key = load_signing_key(settings)
        app.state.ip_hash_key = (settings.ip_hash_key or hashlib.sha256(app.state.session_signing_key + b"|ip").hexdigest()).encode()
        app.state.sessions, app.state.ops, closers = await open_stores(settings)
        app.state.gate = GenerationGate(settings.gen_concurrency, settings.gen_queue)
        app.state.read_limiter = SlidingWindowLimiter()
        app.state.agent_client = agent_client or AgentServiceClient(settings.agent_service_url, settings.agent_timeout_seconds)
        yield
        for close in closers:
            await close()
        app.state.catalog = None
        app.state.dataset = None

    app = FastAPI(
        title="Next Whistle", version="0.1.0", lifespan=lifespan,
        openapi_url="/api/openapi.json", docs_url="/api/docs", redoc_url=None,
    )
    app.state.catalog = None
    app.state.dataset = None
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.allowed_origins),
        allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key"], expose_headers=["X-Request-Id", "Retry-After", "Idempotent-Replay"],
    )

    def problem(request: Request, status: int, title: str, code: str | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
        content = {"type": "about:blank", "title": title, "status": status, "instance": request.url.path,
                   "requestId": getattr(request.state, "request_id", None)}
        if code:
            content["code"] = code
        return JSONResponse(status_code=status, media_type="application/problem+json", content=content, headers=headers)

    def ip_key(request: Request) -> str:
        return hash_ip(client_ip(request, settings.trust_proxy), request.app.state.ip_hash_key)

    @app.middleware("http")
    async def guard_and_harden(request: Request, call_next):
        request.state.request_id = f"req_{uuid4().hex}"
        response = None
        if request.method in {"POST", "PUT", "PATCH"}:
            length = request.headers.get("content-length")
            if length is None and request.headers.get("transfer-encoding"):
                response = problem(request, 411, "A Content-Length header is required", "length_required")
            elif length is not None and (not length.isdigit() or int(length) > settings.max_body_bytes):
                response = problem(request, 413, "Request body is too large", "payload_too_large")
        elif request.method == "GET" and request.url.path.startswith("/api/") and getattr(request.app.state, "read_limiter", None):
            allowed, wait = request.app.state.read_limiter.allow(ip_key(request), settings.limit_read_minute, 60, time.monotonic())
            if not allowed:
                response = problem(request, 429, "Too many requests. Slow down and retry shortly.", "rate_limited", retry_after(wait))
        if response is None:
            response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not request.url.path.startswith("/api/docs") and request.url.path != "/api/openapi.json":
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if settings.environment == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return problem(request, exc.status_code, str(exc.detail), getattr(exc, "code", None), getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return problem(request, 400, "Invalid request")

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        if app.state.catalog is None:
            raise HTTPException(503, "Catalog not loaded")
        return {"status": "ready"}

    @app.get("/api/matches", response_model=MatchCatalog)
    async def matches():
        if app.state.catalog is None:
            raise HTTPException(503, "Catalog not loaded")
        return JSONResponse(
            content=app.state.catalog.model_dump(mode="json", by_alias=True),
            headers={"Cache-Control": "public, max-age=300"},
        )

    def live_slot(current_ms: int) -> tuple[int, int, int, str]:
        """The slot now, how far into the match the live window is (it opens after half time), when the slot started and which playable match is live in it."""
        slot = slot_of(current_ms, settings.live_instance_epoch_ms, settings.live_match_duration_ms)
        start_ms = settings.live_instance_epoch_ms + slot * settings.live_match_duration_ms
        return slot, window_offset_ms(slot) + current_ms - start_ms, start_ms, playable_for_slot(slot, [match.match_id for match in app.state.catalog.matches])

    @app.get("/api/schedule", response_model=Schedule)
    async def schedule_endpoint(response: Response):
        if app.state.dataset is None:
            raise HTTPException(503, "Catalog not loaded")
        response.headers["Cache-Control"] = "no-store"
        schedule = build_schedule(
            now_ms(), epoch_ms=settings.live_instance_epoch_ms, duration_ms=settings.live_match_duration_ms,
            matches=app.state.dataset.catalog["matches"], history=list(app.state.dataset.history),
        )
        for item in schedule["items"]:
            if item["kind"] == "result":
                continue
            data = app.state.dataset.matches[item["match_id"]]
            goals = score_at(data, item["live_match_ms"] if item["kind"] == "live" else data.duration_ms)  # the live card shows the score so far, a replay its final score
            item["score"] = {"home": goals[item["home"]["team_id"]], "away": goals[item["away"]["team_id"]]}
        return Schedule.model_validate(schedule)

    @app.get("/api/live-state", response_model=LiveMatchState)
    async def live_state(response: Response):
        if app.state.catalog is None:
            raise HTTPException(503, "Catalog not loaded")
        response.headers["Cache-Control"] = "no-store"
        current_ms = now_ms()
        cycle_number, match_ms, instance_start_ms, live_match_id = live_slot(current_ms)
        return LiveMatchState(
            instance_id=f"li_{live_match_id[2:]}_{cycle_number}",
            match_id=live_match_id,
            server_time_ms=current_ms,
            instance_start_ms=instance_start_ms,
            match_ms=match_ms,
            duration_ms=settings.live_match_duration_ms,
            cycle_number=cycle_number,
        )

    def session_tokens(session_id: str, refresh_token: str, now: int, request: Request) -> dict:
        access_token, access_expiry = issue_access_token(session_id, now, request.app.state.session_signing_key, ACCESS_TOKEN_SECONDS)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": datetime.fromtimestamp(access_expiry, timezone.utc).isoformat(),
            "refresh_expires_at": datetime.fromtimestamp(now + REFRESH_TOKEN_SECONDS, timezone.utc).isoformat(),
        }

    @app.post("/api/sessions", status_code=201)
    async def create_session(request: Request):
        now = int(time.time())
        allowed, wait = await request.app.state.ops.consume("session_issue", ip_key(request), settings.limit_ip_sessions_hour, 3600, now)
        if not allowed:
            raise ApiError(429, "Too many sessions from this network. Try again later.", "rate_limited", retry_after(wait))
        session_id = f"s_{uuid4().hex}"
        refresh_token = secrets.token_urlsafe(48)
        await request.app.state.sessions.create(session_id, hash_refresh_token(refresh_token), now, now + REFRESH_TOKEN_SECONDS)
        result = session_tokens(session_id, refresh_token, now, request)
        result["limits"] = {"per_hour": settings.limit_session_hour, "per_day": settings.limit_session_day}
        return camelize(result)

    @app.post("/api/sessions/refresh")
    async def refresh_session(body: RefreshSessionRequest, request: Request):
        now = int(time.time())
        refresh_token = secrets.token_urlsafe(48)
        session_id = await request.app.state.sessions.rotate(
            hash_refresh_token(body.refresh_token), hash_refresh_token(refresh_token), now, now + REFRESH_TOKEN_SECONDS,
        )
        if session_id is None:
            raise HTTPException(401, "Invalid or expired refresh token")
        return camelize(session_tokens(session_id, refresh_token, now, request))

    async def authenticated_session(request: Request) -> str:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        now = int(time.time())
        session_id = read_access_token(token, now, request.app.state.session_signing_key) if scheme.lower() == "bearer" else None
        if session_id is None or not await request.app.state.sessions.is_active(session_id, now):
            raise HTTPException(401, "Invalid or expired session")
        return session_id

    @app.delete("/api/sessions/current", status_code=204)
    async def revoke_session(request: Request):
        await request.app.state.sessions.revoke(await authenticated_session(request))
        return Response(status_code=204)

    def match_data(request: Request, match_id: str) -> MatchData:
        dataset: Dataset | None = request.app.state.dataset
        if dataset is None or match_id not in dataset.matches:
            raise HTTPException(404, "Match not found")
        return dataset.matches[match_id]

    def checked_cutoff(data: MatchData, cutoff_ms: int) -> int:
        if cutoff_ms < 0 or cutoff_ms > data.duration_ms:
            raise HTTPException(422, "cutoffMs is outside the match")
        return cutoff_ms

    @app.get("/api/matches/{match_id}/squads")
    async def squads(request: Request, match_id: str):
        data = match_data(request, match_id)
        teams = [{"team_id": side["team_id"], "players": [
            {key: player[key] for key in ("player_id", "name", "shirt", "position", "starter")}
            for player in side["squad"]
        ]} for side in (data.match["home"], data.match["away"])]
        return camelize({"teams": teams, "dataset_version": request.app.state.dataset.version})

    @app.get("/api/matches/{match_id}/replay")
    async def replay(request: Request, match_id: str, cutoff_ms: int = Query(alias="cutoffMs"), from_ms: int | None = Query(default=None, alias="fromMs")):
        data = match_data(request, match_id)
        cutoff_ms = checked_cutoff(data, cutoff_ms)
        if from_ms is not None and (from_ms < 0 or from_ms > cutoff_ms):
            raise HTTPException(422, "fromMs must be between zero and cutoffMs")
        all_visible = visible_events(data, cutoff_ms)
        if from_ms is None:
            chosen = all_visible[-30:]
            has_more = len(all_visible) > len(chosen)
        else:
            delta = [event for event in all_visible if event["available_ms"] > from_ms]
            if len(delta) > 500:
                raise HTTPException(413, "Replay delta exceeds 500 events")
            chosen, has_more = delta, False
        snapshot = next((item for item in reversed(data.snapshots) if item["available_ms"] <= cutoff_ms), None)
        result = {
            "dataset_version": request.app.state.dataset.version,
            "cutoff_ms": cutoff_ms,
            "evidence_cutoff_ms": cutoff_ms,
            "state": state_at(data, cutoff_ms),
            "events": chosen,
            "has_more": has_more,
            "stats": team_stats(data, cutoff_ms),
            "snapshot": snapshot,
        }
        return camelize(result)

    @app.get("/api/matches/{match_id}/timeline")
    async def timeline(request: Request, match_id: str, cutoff_ms: int = Query(alias="cutoffMs")):
        data = match_data(request, match_id)
        cutoff_ms = checked_cutoff(data, cutoff_ms)
        rows = visible_events(data, cutoff_ms)
        return camelize({"dataset_version": request.app.state.dataset.version, "cutoff_ms": cutoff_ms, "events": rows})

    @app.get("/api/matches/{match_id}/clips")
    async def clips(request: Request, match_id: str, cutoff_ms: int = Query(alias="cutoffMs")):
        """Replay windows for key moments. A clip is listed only once every event it shows is available at the cutoff."""
        data = match_data(request, match_id)
        cutoff_ms = checked_cutoff(data, cutoff_ms)
        available = {event["event_id"]: event["available_ms"] for event in data.events}
        rows = json.loads((data.folder / "clips.json").read_text(encoding="utf-8"))
        shown = [
            {"clip_id": row["clip_id"], "kind": row["kind"], "from_ms": row["from_ms"], "to_ms": row["to_ms"], "focus_event_ids": row["focus_event_ids"]}
            for row in rows if all(available.get(event_id, cutoff_ms + 1) <= cutoff_ms for event_id in row["focus_event_ids"])
        ]
        return camelize({"dataset_version": request.app.state.dataset.version, "cutoff_ms": cutoff_ms, "clips": shown})

    @app.get("/api/matches/{match_id}/stats")
    async def stats(request: Request, match_id: str, cutoff_ms: int = Query(alias="cutoffMs")):
        data = match_data(request, match_id)
        return camelize({"dataset_version": request.app.state.dataset.version, **team_stats(data, checked_cutoff(data, cutoff_ms))})

    @app.get("/api/matches/{match_id}/players")
    async def players(request: Request, match_id: str, cutoff_ms: int = Query(alias="cutoffMs")):
        data = match_data(request, match_id)
        cutoff_ms = checked_cutoff(data, cutoff_ms)
        result = [player_summary(data, player["player_id"], cutoff_ms) for side in (data.match["home"], data.match["away"]) for player in side["squad"]]
        return camelize({"dataset_version": request.app.state.dataset.version, "cutoff_ms": cutoff_ms, "players": result})

    @app.get("/api/matches/{match_id}/players/{player_id}")
    async def player(request: Request, match_id: str, player_id: str, cutoff_ms: int = Query(alias="cutoffMs")):
        data = match_data(request, match_id)
        summary = player_summary(data, player_id, checked_cutoff(data, cutoff_ms))
        if summary is None:
            raise HTTPException(404, "Player not found")
        return camelize({"dataset_version": request.app.state.dataset.version, **summary})

    @app.get("/api/matches/{match_id}/evidence/{evidence_id}")
    async def evidence(request: Request, match_id: str, evidence_id: str, cutoff_ms: int = Query(alias="cutoffMs")):
        data = match_data(request, match_id)
        cutoff_ms = checked_cutoff(data, cutoff_ms)
        event = next((item for item in data.events if item["event_id"] == evidence_id), None)
        if event is not None:
            if event["available_ms"] > cutoff_ms:
                raise HTTPException(404, "Evidence not visible at cutoff")
            return camelize({"dataset_version": request.app.state.dataset.version, "cutoff_ms": cutoff_ms, "kind": "event", "record": event})
        snapshot = next((item for item in data.snapshots if item["snapshot_id"] == evidence_id), None)
        if snapshot is None or snapshot["available_ms"] > cutoff_ms:
            raise HTTPException(404, "Evidence not found")
        return camelize({"dataset_version": request.app.state.dataset.version, "cutoff_ms": cutoff_ms, "kind": "snapshot", "record": snapshot})

    @app.get("/api/matches/{match_id}/decisions")
    async def decisions(request: Request, match_id: str, cutoff_ms: int = Query(alias="cutoffMs")):
        data = match_data(request, match_id)
        cutoff_ms = checked_cutoff(data, cutoff_ms)
        snapshots = {item["snapshot_id"]: item for item in data.snapshots if item["available_ms"] <= cutoff_ms}
        rows = []
        for event in visible_events(data, cutoff_ms):
            decision = event.get("decision")
            if not decision:
                continue
            geometry = snapshots.get(decision.get("geometry_snapshot_id"))
            rows.append({"event_id": event["event_id"], "match_ms": event["match_ms"], "decision_type": decision["decision_type"], "recorded_ruling": decision["recorded_ruling"], "team_id": event["team_id"], "player_id": event["player_id"], "geometry": geometry})
        return camelize({"dataset_version": request.app.state.dataset.version, "cutoff_ms": cutoff_ms, "decisions": rows})

    async def run_agent_or_fallback(request: Request, payload, fallback_answer: str):
        try:
            data = match_data(request, payload.match_id)
            visible = {row["event_id"] for row in visible_events(data, payload.cutoff_ms)}
            result = await asyncio.wait_for(
                request.app.state.agent_client.generate(payload, visible), timeout=settings.agent_timeout_seconds + 2,
            )
            if result.status == "fallback":
                # The agent could not produce a verified answer; show the Core's deterministic text, never unverified model text.
                return fallback_response(payload, answer=fallback_answer).model_copy(update={"usage": result.usage})
            return result
        except Exception:
            # Replay and deterministic facts remain available when generation is offline.
            return fallback_response(payload, answer=fallback_answer)

    async def run_with_brief_cache(request: Request, payload, key: str | None, event_bound: bool, fallback_answer: str):
        result = await run_agent_or_fallback(request, await attach_cached_brief(request, payload, key, event_bound), fallback_answer)
        await remember_brief(request, key, result)
        return result

    def spent_units(result) -> int:
        usage = getattr(result, "usage", None)
        return (usage.input_tokens + usage.output_tokens * settings.output_weight) if usage else 0

    async def check_generation_limits(request: Request, session_id: str, weight: int) -> None:
        """Per-IP then per-session limits; a refusal carries Retry-After so clients can back off."""
        now = int(time.time())
        allowed, wait = await request.app.state.ops.consume("generation_ip", ip_key(request), settings.limit_ip_hour, 3600, now, weight)
        if not allowed:
            raise ApiError(429, "Too many generations from this network. Try again later.", "rate_limited", retry_after(wait))
        allowed, wait = await request.app.state.sessions.reserve_generation(session_id, now, settings.limit_session_hour, settings.limit_session_day, weight)
        if not allowed:
            raise ApiError(429, "Generation limit reached for this session", "rate_limited", retry_after(wait))

    async def generation_flow(request: Request, session_id: str, cutoff_ms: int, *, body: dict, weight: int, produce,
                              uses_model: bool, cache_key: str | None, time_independent: bool = False) -> JSONResponse:
        """Idempotency -> cache -> kill switch -> limits -> spend allowance -> concurrency gate -> settle -> cache."""
        ops = request.app.state.ops
        now = int(time.time())
        idempotency_key = request.headers.get("idempotency-key")
        if idempotency_key is not None:
            if not IDEMPOTENCY_KEY.match(idempotency_key):
                raise ApiError(400, "Idempotency-Key must be 8-64 letters, digits, dashes or underscores", "invalid_idempotency_key")
            request_hash = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
            state, stored = await ops.idempotency_begin(idempotency_key, session_id, request_hash, now)
            if state == "conflict":
                raise ApiError(422, "This Idempotency-Key was already used for a different request", "idempotency_conflict")
            if state == "in_progress":
                raise ApiError(409, "This request is already being processed", "idempotency_in_progress", retry_after(1))
            if state == "done":
                return JSONResponse(stored, headers={"Idempotent-Replay": "true", "Cache-Control": "no-store"})
        try:
            payload = await generate_once(request, session_id, cutoff_ms, weight, produce, uses_model, cache_key, now, time_independent)
        except BaseException:
            if idempotency_key is not None:
                await ops.idempotency_fail(idempotency_key)
            raise
        if idempotency_key is not None:
            await ops.idempotency_done(idempotency_key, payload)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def generate_once(request, session_id, cutoff_ms, weight, produce, uses_model, cache_key, now, time_independent=False) -> dict:
        ops = request.app.state.ops
        # A Law does not change as the match goes on, so its answers are stored at time 0 and valid for every viewer.
        stored_at, window = (0, None) if time_independent else (cutoff_ms, CACHE_CUTOFF_WINDOW_MS)
        if cache_key is not None:
            hit = await ops.cache_get(cache_key, stored_at, now, window)
            if hit is not None:
                return {**hit, "requestId": request.state.request_id, "cached": True}  # cache hits cost nothing
        if not settings.generation_enabled:
            answer, _ = await produce(False)
            return {**answer, "generationEnabled": False}
        await check_generation_limits(request, session_id, weight)
        if not uses_model:
            answer, _ = await produce(False)
            return answer
        request_id = request.state.request_id
        if not await ops.reserve(request_id, settings.allowance_reserve_units, now):
            raise ApiError(429, "The shared AI allowance is used up for now. Recorded facts are still available.", "allowance_exhausted", retry_after(3600))
        try:
            async with request.app.state.gate.slot():
                answer, units = await produce(True)
        except Busy:
            await ops.release(request_id)
            raise ApiError(503, "The assistant is busy. Please try again in a moment.", "busy", retry_after(5)) from None
        except BaseException:
            await ops.release(request_id)
            raise
        await ops.settle(request_id, units)
        # Never cache an answer that cites no evidence: whatever it says, it cannot be checked, and a repeat would serve it for free.
        if cache_key is not None and answer.get("status") == "ok" and answer.get("source") != "template" and (answer.get("evidenceIds") or answer.get("ruleRefs")):
            await ops.cache_put(cache_key, stored_at, answer, now)
        return answer

    def brief_key(intent: str, request: Request, match_id: str, event_id: str | None, question_id: str | None) -> str:
        """The evidence brief is level-free and language-free, so neither is part of its key."""
        dataset = request.app.state.dataset
        return "|".join(["brief", intent, match_id, event_id or "-", question_id or "-", dataset.version, dataset.rules_version, settings.workflow_version])

    async def attach_cached_brief(request: Request, payload, key: str | None, event_bound: bool):
        """Reuse a brief computed earlier (never later than the viewer's time) so the agent only has to write the answer."""
        if key is None:
            return payload
        hit = await request.app.state.ops.cache_get(key, payload.cutoff_ms, int(time.time()), None if event_bound else CACHE_CUTOFF_WINDOW_MS)
        if not hit:
            return payload
        try:
            return type(payload).model_validate({**payload.model_dump(mode="json", by_alias=True), "cachedBrief": hit["brief"], "briefCutoffMs": hit["cutoff_ms"]})
        except Exception:
            return payload

    async def remember_brief(request: Request, key: str | None, result) -> None:
        if key is not None and result.status == "ok" and result.evidence_brief is not None and result.evidence_brief.facts and not result.brief_cached and result.brief_cutoff_ms is not None:
            brief = result.evidence_brief.model_dump(mode="json", by_alias=True)
            await request.app.state.ops.cache_put(key, result.brief_cutoff_ms, {"brief": brief, "cutoff_ms": result.brief_cutoff_ms}, int(time.time()))

    def cache_key(kind: str, request: Request, match_id: str, event_id: str | None, level: str, language: str, extra: str = "") -> str:
        dataset = request.app.state.dataset
        return "|".join([kind, match_id, event_id or "-", level, language, extra, dataset.version, dataset.rules_version, settings.workflow_version])

    @app.post("/api/matches/{match_id}/explanations")
    async def explanation(body: GenerationRequest, request: Request, match_id: str):
        session_id = await authenticated_session(request)
        data = match_data(request, match_id)
        cutoff_ms = checked_cutoff(data, body.cutoff_ms)
        if body.intent not in {"explain", "compare", "recap", "decision"}:
            raise HTTPException(422, "Unsupported explanation intent")
        payload = build_agent_request(
            request_id=request.state.request_id, intent=body.intent,
            match_id=match_id, dataset_version=request.app.state.dataset.version,
            data=data, cutoff_ms=cutoff_ms, event_id=body.event_id,
            language=body.language, level=body.level,
        )
        fallback_answer = explain_fallback(data, cutoff_ms, body.language, next((row for row in data.events if row["event_id"] == body.event_id), None) if body.event_id else None)
        brief_cache_key = brief_key(body.intent, request, match_id, body.event_id, None)

        async def produce(use_agent: bool):
            result = (await run_with_brief_cache(request, payload, brief_cache_key, body.event_id is not None, fallback_answer)) if use_agent else fallback_response(payload, answer=fallback_answer)
            return camelize({
                "request_id": result.request_id, "match_id": match_id,
                "dataset_version": request.app.state.dataset.version,
                "cutoff_ms": cutoff_ms, "status": result.status,
                "source": result.source, "workflow_version": result.workflow_version,
                "answer": result.answer, "evidence_ids": result.evidence_ids,
            }), spent_units(result)

        return await generation_flow(
            request, session_id, cutoff_ms, body=body.model_dump(mode="json"), weight=1, produce=produce, uses_model=True,
            cache_key=cache_key(f"explain.{body.intent}", request, match_id, body.event_id, body.level, body.language),
        )

    @app.post("/api/matches/{match_id}/referee/questions")
    async def referee_question(body: RefereeQuestionRequest, request: Request, match_id: str):
        session_id = await authenticated_session(request)
        data = match_data(request, match_id)
        cutoff_ms = checked_cutoff(data, body.cutoff_ms)
        event = None
        if body.event_id:
            event = next((row for row in data.events if row["event_id"] == body.event_id), None)
            if event is None or event["available_ms"] > cutoff_ms:
                raise HTTPException(404, "Decision evidence not visible at cutoff")
        question_text = sanitize_question(body.question_text) if body.question_text else None
        if body.question_text and not question_text:
            raise HTTPException(422, "Question is empty")
        base = {
            "request_id": request.state.request_id, "match_id": match_id,
            "dataset_version": request.app.state.dataset.version,
            "cutoff_ms": cutoff_ms, "event_id": body.event_id,
        }
        # Free-text questions are never cached, so they weigh double against the session limit.
        weight = 2 if question_text else 1
        request_body = body.model_dump(mode="json")
        if question_text:
            outcome = answer_without_model(
                question=question_text, language=body.language, selected_event=event,
                visible_decisions=[row for row in visible_events(data, cutoff_ms) if row.get("decision")],
                index=load_rule_index(str(project_root()), request.app.state.dataset.rules_version), level=body.level,
            )
            if outcome is not None and outcome.scope == "general_law" and outcome.rules:
                law_payload = build_law_request(
                    request_id=request.state.request_id, match_id=match_id, dataset_version=request.app.state.dataset.version,
                    cutoff_ms=cutoff_ms, question_text=question_text, language=body.language, level=body.level, rules=outcome.rules,
                )
                dataset = request.app.state.dataset
                shape = outcome.topic or "q:" + hashlib.sha1(question_text.lower().strip().encode()).hexdigest()[:16]
                law_key = "|".join(["law", shape, body.level, body.language, dataset.rules_version, settings.workflow_version])

                async def produce_law(use_agent: bool):
                    result = (await run_agent_or_fallback(request, law_payload, outcome.answer)) if use_agent else fallback_response(law_payload, answer=outcome.answer)
                    return camelize({
                        **base, "status": result.status, "source": result.source, "workflow_version": result.workflow_version,
                        "scope": "general_law", "answer": result.answer, "evidence_ids": [], "rule_refs": outcome.rule_refs,
                    }), spent_units(result)

                return await generation_flow(request, session_id, cutoff_ms, body=request_body, weight=weight, produce=produce_law,
                                             uses_model=True, cache_key=law_key, time_independent=True)
            if outcome is not None:
                async def produce_template(_use_agent: bool):
                    return camelize({
                        **base, "status": "ok", "source": "template", "workflow_version": "ask-template-v1",
                        "scope": outcome.scope, "answer": outcome.answer, "evidence_ids": [],
                        "rule_refs": outcome.rule_refs, "redirect_event_id": outcome.redirect_event_id,
                    }), 0

                return await generation_flow(request, session_id, cutoff_ms, body=request_body, weight=weight,
                                             produce=produce_template, uses_model=False, cache_key=None)
        if event is None or not event.get("decision"):
            raise HTTPException(422, "Ask the Ref is available for recorded decisions")
        question_text = question_text or supported_question(data, body.event_id, body.question_id, body.language)
        payload = build_agent_request(
            request_id=request.state.request_id, intent="referee_question",
            match_id=match_id, dataset_version=request.app.state.dataset.version,
            data=data, cutoff_ms=cutoff_ms, event_id=body.event_id,
            question_id=body.question_id, question_text=question_text,
            language=body.language, level=body.level,
            history=[{"question": turn.question, "answer_headline": turn.answer_headline} for turn in body.history],
        )
        fallback_text, rule_refs = referee_fallback(data, event, body.language, body.level)
        # Only suggested questions share a brief; free-text questions shape the analysis, so they never do.
        brief_cache_key = None if body.question_text else brief_key("referee_question", request, match_id, body.event_id, body.question_id)

        async def produce(use_agent: bool):
            result = (await run_with_brief_cache(request, payload, brief_cache_key, True, fallback_text)) if use_agent else fallback_response(payload, answer=fallback_text)
            return camelize({
                **base, "status": result.status, "source": result.source,
                "workflow_version": result.workflow_version, "scope": "in_scope",
                "answer": result.answer, "evidence_ids": result.evidence_ids,
                "rule_refs": rule_refs,
            }), spent_units(result)

        return await generation_flow(
            request, session_id, cutoff_ms, body=request_body, weight=weight, produce=produce, uses_model=True,
            cache_key=None if body.question_text else cache_key("referee", request, match_id, body.event_id, body.level, body.language, body.question_id or ""),
        )

    def tracking_manifest(match_id: str) -> tuple[dict, Path]:
        dataset: Dataset | None = app.state.dataset
        if dataset is None or match_id not in dataset.matches:
            raise HTTPException(404, "Match not found")
        path = dataset.matches[match_id].folder / "tracking" / "manifest.json"
        if not path.is_file():
            raise HTTPException(404, "Tracking not found")
        return json.loads(path.read_text()), path

    @app.get("/api/matches/{match_id}/tracking/manifest")
    async def get_tracking_manifest(match_id: str):
        _, path = tracking_manifest(match_id)
        return FileResponse(path, media_type="application/json", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/api/matches/{match_id}/tracking/chunks/{chunk_index}")
    async def get_tracking_chunk(match_id: str, chunk_index: int, cutoff_ms: int | None = Query(default=None, alias="cutoffMs"), from_ms: int | None = Query(default=None, alias="fromMs")):
        manifest, manifest_path = tracking_manifest(match_id)
        chunks = manifest.get("chunks", [])
        if chunk_index < 0 or chunk_index >= len(chunks):
            raise HTTPException(404, "Tracking chunk not found")
        chunk = chunks[chunk_index]
        data = app.state.dataset.matches[match_id]
        _, live_ms, _, live_match_id = live_slot(now_ms())
        shared_cutoff = live_ms if match_id == live_match_id else data.duration_ms  # only the live match is held back; replays are over
        if cutoff_ms is None:
            cutoff_ms = shared_cutoff
        elif cutoff_ms < 0 or cutoff_ms > data.duration_ms:
            raise HTTPException(422, "cutoffMs is outside the match")
        cutoff_ms = min(cutoff_ms, shared_cutoff)
        if from_ms is not None and (from_ms < 0 or from_ms > cutoff_ms):
            raise HTTPException(422, "fromMs must be between zero and cutoffMs")
        if chunk["from_ms"] > cutoff_ms:
            raise HTTPException(404, "Tracking not available at cutoff")
        path = manifest_path.parent.parent / chunk["file"]
        if not path.is_file():
            raise HTTPException(404, "Tracking chunk not found")
        if chunk["to_ms"] > cutoff_ms or from_ms is not None:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["frames"] = [frame for frame in payload.get("frames", []) if (from_ms is None or frame[0] > from_ms) and frame[0] <= cutoff_ms]
            return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})
        return FileResponse(path, media_type="application/json", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    return app


app = create_app()
