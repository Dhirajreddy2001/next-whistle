from contextlib import asynccontextmanager
import json
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
    RefereeQuestionRequest, RefreshSessionRequest,
)
from next_whistle_core.config import Settings
from next_whistle_core.security import hash_refresh_token, issue_access_token, read_access_token
from next_whistle_core.services.replay import player_summary, state_at, team_stats, visible_events
from next_whistle_core.services.agent_client import AgentServiceClient
from next_whistle_core.services.ask import answer_without_model, load_rule_index
from next_whistle_shared.ask import sanitize_question
from next_whistle_core.services.generation import (
    build_agent_request, fallback_response, project_root, referee_fallback, supported_question,
)
from next_whistle_core.store.datasets import Dataset, MatchData, load_dataset
from next_whistle_core.store.sessions import SQLiteSessionStore

ACCESS_TOKEN_SECONDS = 15 * 60
REFRESH_TOKEN_SECONDS = 7 * 24 * 60 * 60


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
        app.state.catalog = MatchCatalog.model_validate(app.state.dataset.catalog)
        app.state.sessions = SQLiteSessionStore(settings.session_db_path)
        app.state.session_signing_key = (settings.session_signing_key.encode() if settings.session_signing_key else secrets.token_bytes(32))
        app.state.agent_client = agent_client or AgentServiceClient(settings.agent_service_url, settings.agent_timeout_seconds)
        yield
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
        allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key"], expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = f"req_{uuid4().hex}"
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    def problem(request: Request, status: int, title: str) -> JSONResponse:
        return JSONResponse(
            status_code=status, media_type="application/problem+json",
            content={"type": "about:blank", "title": title, "status": status,
                     "instance": request.url.path,
                     "requestId": request.state.request_id},
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return problem(request, exc.status_code, str(exc.detail))

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

    @app.get("/api/live-state", response_model=LiveMatchState)
    async def live_state(response: Response):
        response.headers["Cache-Control"] = "no-store"
        current_ms = now_ms()
        cycle_number, match_ms, instance_start_ms = live_cycle(current_ms)
        return LiveMatchState(
            instance_id=f"li_{settings.live_match_id[2:]}_{cycle_number}",
            match_id=settings.live_match_id,
            server_time_ms=current_ms,
            instance_start_ms=instance_start_ms,
            match_ms=match_ms,
            duration_ms=settings.live_match_duration_ms,
            cycle_number=cycle_number,
        )

    def live_cycle(current_ms: int) -> tuple[int, int, int]:
        elapsed_ms = max(0, current_ms - settings.live_instance_epoch_ms)
        cycle_number, match_ms = divmod(elapsed_ms, settings.live_match_duration_ms)
        instance_start_ms = settings.live_instance_epoch_ms + cycle_number * settings.live_match_duration_ms
        return cycle_number, match_ms, instance_start_ms

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
        session_id = f"s_{uuid4().hex}"
        refresh_token = secrets.token_urlsafe(48)
        request.app.state.sessions.create(session_id, hash_refresh_token(refresh_token), now, now + REFRESH_TOKEN_SECONDS)
        result = session_tokens(session_id, refresh_token, now, request)
        result["limits"] = {"per_hour": 8, "per_day": 20}
        return camelize(result)

    @app.post("/api/sessions/refresh")
    async def refresh_session(body: RefreshSessionRequest, request: Request):
        now = int(time.time())
        refresh_token = secrets.token_urlsafe(48)
        session_id = request.app.state.sessions.rotate(
            hash_refresh_token(body.refresh_token), hash_refresh_token(refresh_token), now, now + REFRESH_TOKEN_SECONDS,
        )
        if session_id is None:
            raise HTTPException(401, "Invalid or expired refresh token")
        return camelize(session_tokens(session_id, refresh_token, now, request))

    def authenticated_session(request: Request) -> str:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        now = int(time.time())
        session_id = read_access_token(token, now, request.app.state.session_signing_key) if scheme.lower() == "bearer" else None
        if session_id is None or not request.app.state.sessions.is_active(session_id, now):
            raise HTTPException(401, "Invalid or expired session")
        return session_id

    @app.delete("/api/sessions/current", status_code=204)
    async def revoke_session(request: Request):
        request.app.state.sessions.revoke(authenticated_session(request))
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
            return await request.app.state.agent_client.generate(payload, visible)
        except Exception:
            # Replay and deterministic facts remain available when generation is offline.
            return fallback_response(payload, answer=fallback_answer)

    @app.post("/api/matches/{match_id}/explanations")
    async def explanation(body: GenerationRequest, request: Request, match_id: str):
        session_id = authenticated_session(request)
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
        if not request.app.state.sessions.reserve_generation(session_id, int(time.time())):
            raise HTTPException(429, "Generation limit reached for this session")
        answer = f"{payload.evidence_brief.headline}: {payload.evidence_brief.summary}"
        result = await run_agent_or_fallback(request, payload, answer)
        return camelize({
            "request_id": result.request_id, "match_id": match_id,
            "dataset_version": request.app.state.dataset.version,
            "cutoff_ms": cutoff_ms, "status": result.status,
            "source": result.source, "workflow_version": result.workflow_version,
            "answer": result.answer,
            "evidence_ids": result.evidence_ids,
        })

    @app.post("/api/matches/{match_id}/referee/questions")
    async def referee_question(body: RefereeQuestionRequest, request: Request, match_id: str):
        session_id = authenticated_session(request)
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
        if not request.app.state.sessions.reserve_generation(session_id, int(time.time())):
            raise HTTPException(429, "Generation limit reached for this session")
        base = {
            "request_id": request.state.request_id, "match_id": match_id,
            "dataset_version": request.app.state.dataset.version,
            "cutoff_ms": cutoff_ms, "event_id": body.event_id,
        }
        if question_text:
            outcome = answer_without_model(
                question=question_text, language=body.language, selected_event=event,
                visible_decisions=[row for row in visible_events(data, cutoff_ms) if row.get("decision")],
                index=load_rule_index(str(project_root()), request.app.state.dataset.rules_version),
            )
            if outcome is not None:
                return camelize({
                    **base, "status": "ok", "source": "template", "workflow_version": "ask-template-v1",
                    "scope": outcome.scope, "answer": outcome.answer, "evidence_ids": [],
                    "rule_refs": outcome.rule_refs, "redirect_event_id": outcome.redirect_event_id,
                })
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
        answer, rule_refs = referee_fallback(data, event, body.language)
        result = await run_agent_or_fallback(request, payload, answer)
        return camelize({
            **base, "status": result.status, "source": result.source,
            "workflow_version": result.workflow_version, "scope": "in_scope",
            "answer": result.answer, "evidence_ids": result.evidence_ids,
            "rule_refs": rule_refs,
        })

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
        if cutoff_ms is None:
            _, cutoff_ms, _ = live_cycle(now_ms())
        elif cutoff_ms < 0 or cutoff_ms > data.duration_ms:
            raise HTTPException(422, "cutoffMs is outside the match")
        _, shared_cutoff, _ = live_cycle(now_ms())
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
