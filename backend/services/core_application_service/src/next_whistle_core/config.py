import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    catalog_path: Path
    dataset_path: Path
    allowed_origins: tuple[str, ...] = ("http://localhost:5173",)
    live_match_id: str = "m_rvr_hrb"
    live_instance_epoch_ms: int = 1_791_302_400_000
    live_match_duration_ms: int = 5_760_000
    session_db_path: Path = Path(".local/sessions.sqlite3")
    session_signing_key: str | None = None
    agent_service_url: str = "http://127.0.0.1:8088"
    agent_timeout_seconds: float = 8.0
    environment: str = "local"
    store_backend: str = "sqlite"  # "sqlite" for local work, "cosmos" in production
    cosmos_endpoint: str | None = None
    cosmos_database: str = "nextwhistle"
    cosmos_container: str = "state"
    cosmos_key: str | None = None  # secret; leave unset to use a managed identity
    generation_enabled: bool = True
    limit_session_hour: int = 8
    limit_session_day: int = 20
    limit_ip_hour: int = 20
    limit_ip_sessions_hour: int = 10
    limit_read_minute: int = 600
    gen_concurrency: int = 2
    gen_queue: int = 4
    allowance_budget_units: int = 2_000_000
    allowance_reserve_units: int = 40_000
    output_weight: int = 4
    ip_hash_key: str | None = None
    trust_proxy: bool = False
    max_body_bytes: int = 32_768
    workflow_version: str = "agents-v1-p1.1.0"  # part of every cache key; bump when prompts change

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(__file__).resolve().parents[5]
        dataset = Path(os.getenv("DATASET_PATH", str(root / "data/datasets/v1.2.0")))
        default = dataset / "catalog.json"
        return cls(
            catalog_path=Path(os.getenv("CATALOG_PATH", str(default))),
            dataset_path=dataset,
            allowed_origins=tuple(
                origin.strip()
                for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
                if origin.strip()
            ),
            live_match_id=os.getenv("LIVE_MATCH_ID", "m_rvr_hrb"),
            live_instance_epoch_ms=int(os.getenv("LIVE_INSTANCE_EPOCH_MS", "1791302400000")),
            live_match_duration_ms=int(os.getenv("LIVE_MATCH_DURATION_MS", "5760000")),
            session_db_path=Path(os.getenv("SESSION_DB_PATH", str(root / ".local/sessions.sqlite3"))),
            session_signing_key=os.getenv("SESSION_SIGNING_KEY"),
            agent_service_url=os.getenv("AGENT_SERVICE_URL", "http://127.0.0.1:8088").rstrip("/"),
            agent_timeout_seconds=float(os.getenv("AGENT_TIMEOUT_SECONDS", "8")),
            environment=os.getenv("APP_ENV", "local").lower(),
            store_backend=os.getenv("STORE_BACKEND", "sqlite").lower(),
            cosmos_endpoint=os.getenv("COSMOS_ENDPOINT"),
            cosmos_database=os.getenv("COSMOS_DATABASE", "nextwhistle"),
            cosmos_container=os.getenv("COSMOS_CONTAINER", "state"),
            cosmos_key=os.getenv("COSMOS_KEY"),
            generation_enabled=os.getenv("GENERATION_ENABLED", "true").lower() != "false",
            limit_session_hour=int(os.getenv("LIMIT_SESSION_HOUR", "8")),
            limit_session_day=int(os.getenv("LIMIT_SESSION_DAY", "20")),
            limit_ip_hour=int(os.getenv("LIMIT_IP_HOUR", "20")),
            limit_ip_sessions_hour=int(os.getenv("LIMIT_IP_SESSIONS_HOUR", "10")),
            limit_read_minute=int(os.getenv("LIMIT_READ_MINUTE", "600")),
            gen_concurrency=int(os.getenv("GEN_CONCURRENCY", "2")),
            gen_queue=int(os.getenv("GEN_QUEUE", "4")),
            allowance_budget_units=int(os.getenv("ALLOWANCE_BUDGET_UNITS", "2000000")),
            allowance_reserve_units=int(os.getenv("ALLOWANCE_RESERVE_UNITS", "40000")),
            output_weight=int(os.getenv("OUTPUT_WEIGHT", "4")),
            ip_hash_key=os.getenv("IP_HASH_KEY"),
            trust_proxy=os.getenv("TRUST_PROXY", "false").lower() == "true",
            max_body_bytes=int(os.getenv("MAX_BODY_BYTES", "32768")),
            workflow_version=os.getenv("WORKFLOW_VERSION", "agents-v1-p1.1.0"),
        )
