"""Materialize protected Kubernetes service health and restart identity."""

from pathlib import Path
from typing import Any

from ..providers.service_identity import evaluate_service_identity


def materialize(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return evaluate_service_identity(run_dir, manifest)
