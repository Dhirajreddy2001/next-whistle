import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    catalog_path: Path
    allowed_origins: tuple[str, ...] = ("http://localhost:5173",)

    @classmethod
    def from_env(cls) -> "Settings":
        default = Path(__file__).resolve().parents[2] / "data/catalog/matches.json"
        return cls(
            catalog_path=Path(os.getenv("CATALOG_PATH", str(default))),
            allowed_origins=tuple(
                origin.strip()
                for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
                if origin.strip()
            ),
        )
