"""Compatibility import; the Core Application Service owns the implementation."""
from next_whistle_core.main import app, create_app

__all__ = ["app", "create_app"]
