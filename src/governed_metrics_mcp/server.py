"""MCP server entrypoint: exposes governed metrics as MCP tools.

This is the main entry point for the governed-metrics-mcp server. On startup it:
1. Loads metric definitions from YAML via the MetricRegistry
2. Creates a MetricExecutor connected to the DuckDB database
3. Registers each metric as an MCP tool with a dynamically-built function signature
4. Runs the server on stdio transport (for Claude Desktop)

The key design choice: tool registration is driven entirely by YAML definitions.
Adding a new metric means adding a YAML file — no Python code changes needed.
The server reads the YAML, builds a function with the right typed parameters,
and registers it with FastMCP. FastMCP inspects the function signature to
generate the JSON Schema that the LLM sees as the tool's input spec.
"""

from __future__ import annotations

import inspect
import logging
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from governed_metrics_mcp.executor import MetricExecutor
from governed_metrics_mcp.registry import MetricRegistry
from governed_metrics_mcp.schemas import MetricDefinition


logger = logging.getLogger(__name__)

# Resolve paths relative to the repo root (two levels up from this file in src/)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DEFINITIONS_DIR = _REPO_ROOT / "metrics" / "definitions"
_DEFAULT_DB_PATH = _REPO_ROOT / "data" / "metrics.duckdb"


# Map YAML type strings to Python type annotations for the tool signature.
# Optional params get `type | None` — FastMCP renders these as optional in
# the JSON Schema the LLM sees.
_TYPE_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "date": str,  # dates arrive as ISO strings from the LLM
}


def _make_tool_handler(
    metric: MetricDefinition, executor: MetricExecutor
) -> tuple[Any, str]:
    """Create a typed handler function for a metric, driven by its YAML definition.

    Returns (handler_function, description). The handler has a dynamically-built
    signature matching the metric's parameters — FastMCP introspects this to
    generate the tool's JSON Schema.

    How this works (interview-relevant):
        FastMCP uses inspect.signature() to discover a tool function's parameters
        and their type annotations. We build an inspect.Signature with Parameter
        objects matching the YAML definition, then attach it to a closure via
        __signature__. FastMCP sees typed parameters and generates the right schema.

        This is the mechanism that makes "add a YAML → get an MCP tool" work
        without writing a new Python function per metric.
    """
    # Build inspect.Parameter objects from the metric's YAML parameters
    sig_params: list[inspect.Parameter] = []
    for p in metric.parameters:
        python_type = _TYPE_MAP[p.type]
        # All params are optional (with None default) or have an explicit default
        annotation = python_type | None  # type: ignore[operator]
        default = p.default  # None if no default in YAML

        sig_params.append(
            inspect.Parameter(
                name=p.name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )

    # The actual handler — a closure that captures the metric name and executor
    def handler(**kwargs: Any) -> str:
        # Strip None values so the executor only sees explicitly provided params
        provided = {k: v for k, v in kwargs.items() if v is not None}
        result = executor.execute(metric.name, provided)
        return result.model_dump_json(indent=2)

    # Attach the dynamic signature so FastMCP can introspect it
    handler.__signature__ = inspect.Signature(parameters=sig_params)  # type: ignore[attr-defined]
    handler.__name__ = metric.name
    handler.__qualname__ = metric.name
    handler.__doc__ = metric.description

    return handler, metric.description


def create_server(
    definitions_dir: Path | None = None,
    db_path: Path | None = None,
) -> FastMCP:
    """Create and configure the MCP server with all governed metrics registered.

    Separated from the module-level server creation so tests can call it with
    custom paths (test DB, test YAML directory).
    """
    definitions_dir = definitions_dir or _DEFAULT_DEFINITIONS_DIR
    db_path = db_path or _DEFAULT_DB_PATH

    registry = MetricRegistry(definitions_dir)
    executor = MetricExecutor(registry, db_path)

    server = FastMCP(
        name="governed-metrics",
        instructions=(
            "This server provides governed e-commerce metrics. Each tool "
            "returns a specific business metric computed from the database. "
            "Use the tool descriptions to pick the right metric for the "
            "user's question. All parameters are optional unless noted — "
            "the server applies sensible defaults (e.g., last 90 days for "
            "time windows, 'completed' for order status)."
        ),
    )

    # Register each metric as an MCP tool
    for metric_def in registry.all_definitions():
        handler, description = _make_tool_handler(metric_def, executor)
        server.add_tool(handler, name=metric_def.name.removesuffix(".yaml"), description=description)
        logger.info(f"Registered tool: {metric_def.name}")

    logger.info(
        f"Server ready with {len(registry)} metric(s): "
        f"{', '.join(registry.list_metrics())}"
    )

    return server


# Module-level server instance — created when the module is imported.
# This is what the MCP entry point runs.
server = create_server()


def main() -> None:
    """Run the MCP server on stdio transport."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()