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

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(__file__).resolve().parents[2]
        default = root / "data/catalog/matches.json"
        return cls(
            catalog_path=Path(os.getenv("CATALOG_PATH", str(default))),
            dataset_path=Path(os.getenv("DATASET_PATH", str(root / "data/datasets/v1.1.0"))),
            allowed_origins=tuple(
                origin.strip()
                for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
                if origin.strip()
            ),
            live_match_id=os.getenv("LIVE_MATCH_ID", "m_rvr_hrb"),
            live_instance_epoch_ms=int(os.getenv("LIVE_INSTANCE_EPOCH_MS", "1791302400000")),
            live_match_duration_ms=int(os.getenv("LIVE_MATCH_DURATION_MS", "5760000")),
        )
