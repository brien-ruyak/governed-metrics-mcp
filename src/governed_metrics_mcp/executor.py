"""Metric executor: validates parameters, builds SQL, runs queries against DuckDB.

The executor is the bridge between the MCP tool call (metric name + params dict)
and the DuckDB database. It:
1. Looks up the metric definition from the registry
2. Applies defaults and coerces parameter types
3. Validates enum constraints
4. Assembles SQL from sql_base + applicable sql_filters
5. Executes via DuckDB's named parameter binding (never string concatenation)
6. Returns a typed MetricResult

All SQL values go through $name parameter binding. The only string assembly is
structural — appending WHERE clauses defined in the YAML. User-provided values
never touch the SQL string.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from governed_metrics_mcp.registry import MetricRegistry
from governed_metrics_mcp.schemas import (
    MetricDefinition,
    MetricParameter,
    MetricResult,
    coerce_param,
    default_date_range,
)


class ExecutionError(Exception):
    """Raised when metric execution fails due to bad parameters or SQL errors."""


class MetricExecutor:
    """Executes governed metrics against a DuckDB database.

    Usage:
        executor = MetricExecutor(registry, db_path="data/metrics.duckdb")
        result = executor.execute("order_volume.yaml", {"region": "West"})
    """

    def __init__(self, registry: MetricRegistry, db_path: str | Path) -> None:
        self._registry = registry
        self._db_path = Path(db_path)
        if not self._db_path.exists():
            raise FileNotFoundError(
                f"DuckDB database not found: {self._db_path}. "
                f"Run 'uv run python data/seed.py' to generate it."
            )

    def _connect(self) -> duckdb.DuckDBPyConnection:
        """Open a read-only connection to the DuckDB database.

        Read-only because the MCP server should never modify the data.
        New connection per query — DuckDB file-mode connections are cheap
        (no TCP, no auth, no connection pool needed).
        """
        return duckdb.connect(str(self._db_path), read_only=True)

    def execute(self, metric_name: str, params: dict[str, Any]) -> MetricResult:
        """Execute a governed metric and return structured results.

        Args:
            metric_name: Name of the metric (must exist in the registry).
            params: Parameter dict from the MCP tool call. Values are
                    coerced and validated against the metric definition.

        Returns:
            MetricResult with typed rows, column names, and query metadata.

        Raises:
            ExecutionError: If parameters are invalid or the query fails.
        """
        # 1. Look up the metric definition
        try:
            metric = self._registry.get(metric_name)
        except KeyError as e:
            raise ExecutionError(str(e)) from e

        # 2. Resolve parameters: apply defaults, coerce types, validate enums
        resolved = self._resolve_params(metric, params)

        # 3. Build SQL from base + applicable filters
        sql, bind_params = self._build_sql(metric, resolved)

        # 4. Execute against DuckDB
        try:
            conn = self._connect()
            result = conn.execute(sql, bind_params)
            columns = [desc[0] for desc in result.description]
            raw_rows = result.fetchall()
            conn.close()
        except duckdb.Error as e:
            raise ExecutionError(f"DuckDB error executing '{metric_name}': {e}") from e

        # 5. Build typed result
        rows = [dict(zip(columns, row)) for row in raw_rows]

        metadata: dict[str, Any] = {
            "row_count": len(rows),
            "filters_applied": {
                k: v for k, v in resolved.items()
                if k in metric.sql_filters and v is not None
            },
        }
        # Include time window in metadata if start/end dates were resolved
        if "start_date" in resolved and resolved["start_date"] is not None:
            metadata["start_date"] = str(resolved["start_date"])
        if "end_date" in resolved and resolved["end_date"] is not None:
            metadata["end_date"] = str(resolved["end_date"])

        return MetricResult(
            metric_name=metric_name,
            columns=columns,
            rows=rows,
            metadata=metadata,
        )

    def _resolve_params(
        self, metric: MetricDefinition, raw_params: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply defaults, coerce types, and validate enum constraints.

        Returns a dict with all parameter names as keys. Parameters not
        provided by the caller get their default value (or None if no
        default is defined).
        """
        # Build a lookup of parameter definitions by name
        param_defs = {p.name: p for p in metric.parameters}

        # Reject unknown parameters
        unknown = set(raw_params.keys()) - set(param_defs.keys())
        if unknown:
            raise ExecutionError(
                f"Unknown parameters for '{metric.name}': {', '.join(sorted(unknown))}. "
                f"Valid parameters: {', '.join(sorted(param_defs.keys()))}"
            )

        resolved: dict[str, Any] = {}
        for pdef in metric.parameters:
            if pdef.name in raw_params and raw_params[pdef.name] is not None:
                value = raw_params[pdef.name]
            elif pdef.default is not None:
                value = pdef.default
            elif pdef.required:
                raise ExecutionError(
                    f"Missing required parameter '{pdef.name}' for metric '{metric.name}'"
                )
            else:
                value = None
                resolved[pdef.name] = None
                continue

            # Coerce to declared type
            try:
                value = coerce_param(value, pdef)
            except ValueError as e:
                raise ExecutionError(str(e)) from e

            # Validate enum constraint
            if pdef.enum is not None and str(value) not in pdef.enum:
                raise ExecutionError(
                    f"Parameter '{pdef.name}': value {value!r} not in "
                    f"allowed values {pdef.enum}"
                )

            resolved[pdef.name] = value

        # Apply dynamic defaults for date parameters if not provided
        if "start_date" in param_defs and resolved.get("start_date") is None:
            start, end = default_date_range()
            resolved["start_date"] = start
            if "end_date" in param_defs and resolved.get("end_date") is None:
                resolved["end_date"] = end
        if "end_date" in param_defs and resolved.get("end_date") is None:
            resolved["end_date"] = date.today()

        return resolved

    def _build_sql(
        self, metric: MetricDefinition, resolved: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Assemble the final SQL from base + applicable filter clauses.

        Returns (sql_string, bind_params_dict). Only parameters with non-None
        values are included in the bind dict and their filter clauses appended.

        The structural SQL assembly (appending AND clauses) is safe — the
        clauses come from the YAML definition, not from user input. All user
        values go through $name parameter binding.
        """
        parts = [metric.sql_base.strip()]
        bind_params: dict[str, Any] = {}

        for pdef in metric.parameters:
            value = resolved.get(pdef.name)
            if value is None:
                continue

            # If this param has a filter clause, append it
            if pdef.name in metric.sql_filters:
                parts.append(metric.sql_filters[pdef.name])

            # Add to bind params (needed for both base SQL and filter params)
            bind_params[pdef.name] = value

        sql = "\n  ".join(parts)
        return sql, bind_params