"""Pydantic v2 models for governed metric definitions, parameters, and results.

These models serve three roles:
1. Validate YAML metric definitions at server startup (catch config errors early)
2. Validate parameters the LLM sends at query time (reject bad input before SQL)
3. Structure the response so the LLM gets typed data, not raw tuples

The MetricDefinition schema IS the governance contract — it defines what queries
are possible. The LLM never sees raw SQL or table schemas, only the tool
definitions that Pydantic generates from these models.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------- Parameter and definition schemas (loaded from YAML) ----------


class MetricParameter(BaseModel):
    """Schema for a single parameter a metric accepts.

    Loaded from YAML. The executor uses these to validate and coerce the
    values the LLM provides before they reach DuckDB.
    """

    name: str = Field(description="Parameter name, used as the $name in SQL binding")
    type: Literal["str", "int", "float", "date"] = Field(
        description="Expected Python type — executor coerces to this before binding"
    )
    description: str = Field(
        description="Human-readable description shown in the MCP tool schema"
    )
    required: bool = Field(
        default=False,
        description="Whether the parameter must be provided by the caller",
    )
    default: Any = Field(
        default=None,
        description="Default value if the parameter is not provided",
    )
    enum: list[str] | None = Field(
        default=None,
        description="If set, constrains valid values to this list",
    )


class MetricDefinition(BaseModel):
    """A complete governed metric definition, loaded from a YAML file.

    Each YAML file in metrics/definitions/ maps to one MetricDefinition.
    The MCP server exposes each as a tool — the name becomes the tool name,
    the description becomes the tool description, and the parameters become
    the tool's input schema.
    """

    name: str = Field(description="Metric identifier, used as the MCP tool name")
    description: str = Field(
        description="Tool description — written for an LLM audience so it can "
        "match natural-language questions to the right metric"
    )
    parameters: list[MetricParameter] = Field(
        description="Parameters the metric accepts, with types and constraints"
    )
    sql_base: str = Field(
        description="Core SQL query with $param placeholders for required params"
    )
    sql_filters: dict[str, str] = Field(
        default_factory=dict,
        description="Optional filter clauses: param_name → 'AND column = $param_name'",
    )
    output_columns: list[str] = Field(
        description="Column names in the query result, in order"
    )


# ---------- Result schema (returned by executor) ----------


class MetricResult(BaseModel):
    """Structured response from executing a governed metric.

    Contains the query results plus metadata about what was queried —
    filters applied, time window, row count. The LLM uses the metadata
    to frame its answer ("In the West region, between Jan 1 and Mar 31...").
    """

    metric_name: str
    columns: list[str]
    rows: list[dict[str, Any]]
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Query context: filters applied, time window, row count",
    )


# ---------- Helpers ----------


# Maps YAML type strings to Python types — used by the executor for coercion.
TYPE_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "date": date,
}


def coerce_param(value: Any, param: MetricParameter) -> Any:
    """Coerce a parameter value to its declared type.

    Handles the common case where the LLM sends a date as a string
    ("2025-01-01") or a number as a string ("10"). Returns the coerced
    value or raises ValueError with a clear message.
    """
    if value is None:
        return None

    target_type = TYPE_MAP[param.type]

    if isinstance(value, target_type):
        return value

    try:
        if param.type == "date" and isinstance(value, str):
            return date.fromisoformat(value)
        return target_type(value)
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"Parameter '{param.name}': cannot convert {value!r} to {param.type}"
        ) from e


def default_date_range() -> tuple[date, date]:
    """Return (90 days ago, today) — the fallback time window for metrics."""
    today = date.today()
    return today - timedelta(days=90), today