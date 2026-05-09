# Governed Metrics MCP

An MCP server demonstrating the **governed metrics layer** pattern: an LLM accesses curated metric definitions as MCP tools instead of writing raw SQL against a database.

## Status

In development. Phase 1 (MVP) is underway.

## What's planned

- Five governed metrics for a synthetic e-commerce dataset: `order_volume`, `average_order_value`, `repeat_purchase_rate`, `return_rate`, `top_products_by_revenue`
- DuckDB-backed analytical queries via parameterized SQL
- Pydantic-validated metric parameters
- YAML metric definitions as the governance layer
- Claude Desktop integration via the official `mcp` SDK

A full README with quickstart, architecture diagram, and example interactions lands in Phase 2.

## License

MIT — see [LICENSE](LICENSE).