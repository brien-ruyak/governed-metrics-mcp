# How to Add a New Metric

Adding a governed metric means adding a single YAML file to `metrics/definitions/`. No Python code changes. The server discovers it on startup.

## Step 1: Create the YAML file

Copy an existing definition and modify it. Here's an annotated template:

```yaml
# metrics/definitions/your_metric_name.yaml

name: your_metric_name.yaml
# ↑ Must match the filename. This becomes the MCP tool name.

description: >-
  Clear, LLM-readable description of what this metric measures and when to
  use it. Include example questions the LLM should match to this tool.
  Write for an LLM audience: "Use this metric to answer questions like
  'What is our X?' or 'How does Y compare across Z?'"

parameters:
  - name: start_date
    type: date                    # str | int | float | date
    description: Start of the time window (YYYY-MM-DD). Defaults to 90 days ago.
    required: false               # true = LLM must provide it

  - name: category
    type: str
    description: "Filter by product category."
    required: false
    enum: [apparel, electronics, home, beauty, sports]
    # ↑ Executor rejects values not in this list

  - name: limit
    type: int
    description: Maximum number of results to return. Defaults to 10.
    required: false
    default: 10                   # Used when the LLM doesn't provide a value

sql_base: >-
  SELECT column_a, column_b
  FROM your_table
  WHERE order_date >= $start_date
    AND order_date <= $end_date

sql_filters:
  category: "AND category = $category"
  # ↑ Key = parameter name. Appended to sql_base only when the param is provided.
  #   Values use $param_name for DuckDB parameter binding — never string concat.

sql_suffix: >-
  GROUP BY column_a
  ORDER BY column_b DESC
  LIMIT $limit
# ↑ Optional. Appended after sql_base + filters. Use for GROUP BY, ORDER BY, LIMIT.
#   Can contain $param placeholders for bound values.

output_columns:
  - column_a
  - column_b
# ↑ Documents the columns the query returns. Used for result typing.
```

## Key rules

- **`name` must match the filename.** The registry uses this as the lookup key.
- **`description` is product copy, not a code comment.** The LLM reads it to decide which tool to call. Include 2-3 example questions. Vague descriptions = wrong tool selection.
- **All values go through `$param` binding.** Never put user-controlled values directly in SQL strings. The executor binds them via DuckDB's parameterized query API.
- **`sql_filters` are structural SQL, not user input.** The filter clause strings are defined in YAML (trusted), and the values within them are bound parameters (safe).
- **`required: true` parameters have no default.** The executor raises a clear error if they're missing.
- **`enum` constraints are validated before SQL runs.** Bad values never reach DuckDB.

## Existing metrics for reference

| Metric | Parameters | Pattern |
|---|---|---|
| `order_volume` | Time window, region, channel, status | Simple aggregate + optional filters |
| `average_order_value` | Time window, region, channel, segment | Aggregate with JOIN (customers table) |
| `return_rate` | Time window, region, category | CTE + `sql_suffix` for closing the CTE |
| `repeat_purchase_rate` | Cohort month, repeat window days | Multi-CTE, `required: true` param |
| `top_products_by_revenue` | Time window, category, limit | Multi-table JOIN, `sql_suffix` with GROUP BY + LIMIT |

## Step 2: Write a test

Add a test in `tests/test_metrics.py` following the existing pattern:

```python
def test_your_metric_basic(executor):
    """Verify your_metric_name returns expected results against seed data."""
    result = executor.execute("your_metric_name.yaml", {
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
    })
    assert result.metric_name == "your_metric_name.yaml"
    assert len(result.rows) > 0
    # Assert against known values from the deterministic seed data
```

Run with `uv run pytest tests/test_metrics.py -v -k your_metric_name`.

## Step 3: Restart the server

The registry loads YAML files at startup. After adding a new definition:

1. Restart Claude Desktop (or re-run the MCP server)
2. The new metric appears as a tool automatically
3. Ask Claude a question that matches your metric's description