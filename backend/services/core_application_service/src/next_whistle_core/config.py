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
        )
