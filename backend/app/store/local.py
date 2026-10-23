from pathlib import Path

from app.api.schemas import MatchCatalog


def load_catalog(path: Path) -> MatchCatalog:
    """Load and validate spoiler-free catalog metadata, not replay datasets."""
    catalog = MatchCatalog.model_validate_json(path.read_text(encoding="utf-8"))
    ids = [match.match_id for match in catalog.matches]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate match IDs in catalog")
    return catalog
