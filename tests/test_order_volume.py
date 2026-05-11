"""Tests for the order_volume.yaml governed metric.

Runs against a small deterministic test database with known data,
so expected counts are exact. This establishes the testing pattern
for the remaining four metrics.

Test database contents (see conftest.py):
    5 orders total:
      - Order 1: customer 1, 2025-06-01, web, West, completed
      - Order 2: customer 1, 2025-07-15, mobile, West, completed
      - Order 3: customer 2, 2025-06-10, web, East, completed
      - Order 4: customer 3, 2025-08-01, web, West, completed
      - Order 5: customer 3, 2025-08-05, marketplace, West, cancelled
"""

from __future__ import annotations

import pytest
from datetime import date
from pathlib import Path

from governed_metrics_mcp.executor import ExecutionError, MetricExecutor
from governed_metrics_mcp.registry import MetricRegistry


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def test_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a small test DuckDB database with known data."""
    import duckdb

    db_path = tmp_path_factory.mktemp("data") / "test.duckdb"
    conn = duckdb.connect(str(db_path))

    conn.execute("""
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY, email VARCHAR NOT NULL,
            first_name VARCHAR NOT NULL, last_name VARCHAR NOT NULL,
            region VARCHAR NOT NULL, signup_date DATE NOT NULL,
            segment VARCHAR NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY, sku VARCHAR NOT NULL,
            name VARCHAR NOT NULL, category VARCHAR NOT NULL,
            cost DOUBLE NOT NULL, list_price DOUBLE NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL,
            order_date DATE NOT NULL, channel VARCHAR NOT NULL,
            region VARCHAR NOT NULL, status VARCHAR NOT NULL,
            total_amount DOUBLE NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE order_items (
            order_item_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
            unit_price DOUBLE NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE returns (
            return_id INTEGER PRIMARY KEY, order_item_id INTEGER NOT NULL,
            order_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
            return_date DATE NOT NULL, amount DOUBLE NOT NULL
        )
    """)

    conn.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "a@test.com", "Alice", "Smith", "West", date(2025, 1, 1), "returning"),
            (2, "b@test.com", "Bob", "Jones", "East", date(2025, 3, 1), "new"),
            (3, "c@test.com", "Carol", "Lee", "West", date(2025, 6, 1), "VIP"),
        ],
    )
    conn.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "APP-001", "Blue Shirt", "apparel", 10.0, 29.99),
            (2, "ELE-001", "Wireless Mouse", "electronics", 8.0, 24.99),
        ],
    )
    conn.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 1, date(2025, 6, 1), "web", "West", "completed", 59.98),
            (2, 1, date(2025, 7, 15), "mobile", "West", "completed", 29.99),
            (3, 2, date(2025, 6, 10), "web", "East", "completed", 24.99),
            (4, 3, date(2025, 8, 1), "web", "West", "completed", 54.98),
            (5, 3, date(2025, 8, 5), "marketplace", "West", "cancelled", 29.99),
        ],
    )
    conn.executemany(
        "INSERT INTO order_items VALUES (?, ?, ?, ?, ?)",
        [
            (1, 1, 1, 2, 29.99),
            (2, 2, 1, 1, 29.99),
            (3, 3, 2, 1, 24.99),
            (4, 4, 1, 1, 29.99),
            (5, 4, 2, 1, 24.99),
            (6, 5, 1, 1, 29.99),
        ],
    )
    conn.executemany(
        "INSERT INTO returns VALUES (?, ?, ?, ?, ?, ?)",
        [(1, 1, 1, 1, date(2025, 6, 15), 29.99)],
    )
    conn.close()
    return db_path


@pytest.fixture(scope="module")
def executor(test_db_path: Path) -> MetricExecutor:
    """Create an executor pointed at the test database."""
    # Use the real YAML definitions from the repo
    definitions_dir = Path(__file__).resolve().parent.parent / "metrics" / "definitions"
    registry = MetricRegistry(definitions_dir)
    return MetricExecutor(registry, test_db_path)


# ── Full-range queries ────────────────────────────────────────

FULL_RANGE = {"start_date": "2025-01-01", "end_date": "2025-12-31"}


class TestOrderVolumeBasic:
    """Basic order_volume.yaml queries with known expected counts."""

    def test_all_completed_orders(self, executor: MetricExecutor) -> None:
        result = executor.execute("order_volume.yaml", FULL_RANGE)
        assert result.rows[0]["order_count"] == 4  # 4 completed, 1 cancelled

    def test_cancelled_orders(self, executor: MetricExecutor) -> None:
        result = executor.execute("order_volume.yaml", {**FULL_RANGE, "status": "cancelled"})
        assert result.rows[0]["order_count"] == 1

    def test_result_structure(self, executor: MetricExecutor) -> None:
        result = executor.execute("order_volume.yaml", FULL_RANGE)
        assert result.metric_name == "order_volume.yaml"
        assert result.columns == ["order_count"]
        assert result.metadata["row_count"] == 1
        assert result.metadata["start_date"] == "2025-01-01"
        assert result.metadata["end_date"] == "2025-12-31"


# ── Filter tests ──────────────────────────────────────────────


class TestOrderVolumeFilters:
    """Verify each optional filter works correctly."""

    def test_filter_by_region_west(self, executor: MetricExecutor) -> None:
        result = executor.execute("order_volume.yaml", {**FULL_RANGE, "region": "West"})
        assert result.rows[0]["order_count"] == 3  # orders 1, 2, 4

    def test_filter_by_region_east(self, executor: MetricExecutor) -> None:
        result = executor.execute("order_volume.yaml", {**FULL_RANGE, "region": "East"})
        assert result.rows[0]["order_count"] == 1  # order 3

    def test_filter_by_channel_web(self, executor: MetricExecutor) -> None:
        result = executor.execute("order_volume.yaml", {**FULL_RANGE, "channel": "web"})
        assert result.rows[0]["order_count"] == 3  # orders 1, 3, 4

    def test_filter_by_channel_mobile(self, executor: MetricExecutor) -> None:
        result = executor.execute("order_volume.yaml", {**FULL_RANGE, "channel": "mobile"})
        assert result.rows[0]["order_count"] == 1  # order 2

    def test_combined_filters(self, executor: MetricExecutor) -> None:
        result = executor.execute(
            "order_volume.yaml",
            {**FULL_RANGE, "region": "West", "channel": "web"},
        )
        assert result.rows[0]["order_count"] == 2  # orders 1, 4

    def test_filter_returns_zero(self, executor: MetricExecutor) -> None:
        result = executor.execute(
            "order_volume.yaml",
            {**FULL_RANGE, "region": "Central"},
        )
        assert result.rows[0]["order_count"] == 0

    def test_time_window_filter(self, executor: MetricExecutor) -> None:
        result = executor.execute(
            "order_volume.yaml",
            {"start_date": "2025-07-01", "end_date": "2025-08-31"},
        )
        # Completed orders in Jul-Aug: order 2 (Jul 15), order 4 (Aug 1) = 2
        assert result.rows[0]["order_count"] == 2


# ── Metadata tests ────────────────────────────────────────────


class TestOrderVolumeMetadata:
    """Verify metadata reflects the query context correctly."""

    def test_filters_applied_in_metadata(self, executor: MetricExecutor) -> None:
        result = executor.execute(
            "order_volume.yaml",
            {**FULL_RANGE, "region": "West"},
        )
        assert result.metadata["filters_applied"]["region"] == "West"
        assert result.metadata["filters_applied"]["status"] == "completed"

    def test_default_status_in_metadata(self, executor: MetricExecutor) -> None:
        """Status defaults to 'completed' even when not explicitly provided."""
        result = executor.execute("order_volume.yaml", FULL_RANGE)
        assert result.metadata["filters_applied"]["status"] == "completed"


# ── Error handling ────────────────────────────────────────────


class TestOrderVolumeErrors:
    """Verify clean error messages for invalid input."""

    def test_invalid_region_enum(self, executor: MetricExecutor) -> None:
        with pytest.raises(ExecutionError, match="not in allowed values"):
            executor.execute("order_volume.yaml", {**FULL_RANGE, "region": "Atlantis"})

    def test_unknown_parameter(self, executor: MetricExecutor) -> None:
        with pytest.raises(ExecutionError, match="Unknown parameters"):
            executor.execute("order_volume.yaml", {**FULL_RANGE, "foo": "bar"})

    def test_invalid_date_format(self, executor: MetricExecutor) -> None:
        with pytest.raises(ExecutionError, match="cannot convert"):
            executor.execute("order_volume.yaml", {"start_date": "not-a-date"})

    def test_unknown_metric(self, executor: MetricExecutor) -> None:
        with pytest.raises(ExecutionError, match="Unknown metric"):
            executor.execute("nonexistent", {})