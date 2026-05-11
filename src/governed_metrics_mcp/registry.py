"""Metric registry: loads YAML definitions and exposes them for lookup.

On startup, the registry scans metrics/definitions/ for .yaml files, validates
each against MetricDefinition (catching config errors before the server starts),
and stores them in a name-keyed dict. The MCP server and executor both use this
as the single source of truth for what metrics exist and what they accept.

Separation from the executor is deliberate — the registry knows about metric
*definitions*, the executor knows about *running queries*. Different reasons
to change.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from governed_metrics_mcp.schemas import MetricDefinition


# Default location: metrics/definitions/ relative to the repo root.
# Resolved at import time from this file's location in src/governed_metrics_mcp/.
_DEFAULT_DEFINITIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "metrics" / "definitions"
)


class MetricRegistry:
    """Loads and stores governed metric definitions from YAML files.

    Usage:
        registry = MetricRegistry()          # loads from default path
        metric = registry.get("order_volume.yaml")  # returns MetricDefinition or raises
        names = registry.list_metrics()      # returns list of registered names
    """

    def __init__(self, definitions_dir: Path | None = None) -> None:
        self._definitions_dir = definitions_dir or _DEFAULT_DEFINITIONS_DIR
        self._metrics: dict[str, MetricDefinition] = {}
        self._load()

    def _load(self) -> None:
        """Scan the definitions directory for .yaml files and validate each."""
        if not self._definitions_dir.is_dir():
            raise FileNotFoundError(
                f"Metric definitions directory not found: {self._definitions_dir}"
            )

        yaml_files = sorted(self._definitions_dir.glob("*.yaml"))
        if not yaml_files:
            raise ValueError(
                f"No .yaml files found in {self._definitions_dir}"
            )

        for path in yaml_files:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            # Pydantic validates the YAML structure. If the YAML is malformed
            # (missing fields, wrong types), this raises ValidationError with
            # a precise message — fail fast at startup, not at query time.
            metric = MetricDefinition.model_validate(raw)

            if metric.name in self._metrics:
                raise ValueError(
                    f"Duplicate metric name '{metric.name}' in {path.name} "
                    f"(already loaded from another file)"
                )

            self._metrics[metric.name] = metric

    def get(self, name: str) -> MetricDefinition:
        """Look up a metric by name. Raises KeyError if not found."""
        try:
            return self._metrics[name]
        except KeyError:
            available = ", ".join(sorted(self._metrics.keys()))
            raise KeyError(
                f"Unknown metric '{name}'. Available metrics: {available}"
            ) from None

    def list_metrics(self) -> list[str]:
        """Return sorted list of registered metric names."""
        return sorted(self._metrics.keys())

    def all_definitions(self) -> list[MetricDefinition]:
        """Return all registered MetricDefinitions, sorted by name."""
        return [self._metrics[name] for name in self.list_metrics()]

    def __len__(self) -> int:
        return len(self._metrics)

    def __contains__(self, name: str) -> bool:
        return name in self._metrics