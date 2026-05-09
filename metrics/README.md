# Metric Definitions

Governed metric YAML definitions live in `definitions/`. Each YAML file declares a single metric — its name, parameters with types and validation rules, the SQL template, and the expected output schema.

The MCP server loads these definitions on startup and exposes each as a tool. A "how to add a new metric" walkthrough is coming in Phase 2.