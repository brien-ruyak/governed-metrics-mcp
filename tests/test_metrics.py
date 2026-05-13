"""Tests for the four non-order_volume governed metrics.

Runs against a small deterministic test database. Each test class covers
one metric with exact expected values derived from the test data below.

Test database contents:
    3 customers:
      - Customer 1: Alice, West, returning, signed up 2025-01-01
      - Customer 2: Bob, East, new, signed up 2025-03-01
      - Customer 3: Carol, West, VIP, signed up 2025-06-01

    4 products:
      - Product 1: Blue Shirt, apparel, cost=10, price=29.99
      - Product 2: Wireless Mouse, electronics, cost=8, price=24.99
      - Product 3: Scented Candle, home, cost=3, price=14.99
      - Product 4: Running Shoes, sports, cost=25, price=79.99

    6 orders (5 completed, 1 cancelled):
      - Order 1: customer 1, 2025-06-01, web, West, completed, 59.98
      - Order 2: customer 1, 2025-07-15, mobile, West, completed, 29.99
      - Order 3: customer 2, 2025-06-10, web, East, completed, 24.99
      - Order 4: customer 3, 2025-08-01, web, West, completed, 94.98
      - Order 5: customer 3, 2025-08-05, marketplace, West, cancelled, 29.99
      - Order 6: customer 2, 2025-09-01, web, East, completed, 14.99

    8 order_items:
      - Item 1: order 1, product 1 (apparel), qty=2, price=29.99 → 59.98
      - Item 2: order 2, product 1 (apparel), qty=1, price=29.99 → 29.99
      - Item 3: order 3, product 2 (electronics), qty=1, price=24.99 → 24.99
      - Item 4: order 4, product 1 (apparel), qty=1, price=29.99 → 29.99
      - Item 5: order 4, product 4 (sports), qty=1, price=64.99 → 64.99
      - Item 6: order 5, product 1 (apparel), qty=1, price=29.99 → 29.99
      - Item 7: order 6, product 3 (home), qty=1, price=14.99 → 14.99
      - Item 8: order 1, product 2 (electronics), qty=0, price=24.99 → 0.00
        (zero-qty item to test edge; won't affect revenue)

    2 returns:
      - Return 1: order_item 1, order 1, product 1 (apparel), 2025-06-15, 29.99
      - Return 2: order_item 3, order 3, product 2 (electronics), 2025-06-25, 24.99
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest

from governed_metrics_mcp.executor import ExecutionError, MetricExecutor
from governed_metrics_mcp.registry import MetricRegistry


# ── Fixtures ──────────────────────────────────────────────────

FULL_RANGE = {"start_date": "2025-01-01", "end_date": "2025-12-31"}


@pytest.fixture(scope="module")
def test_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a test DuckDB database with known data covering all metrics."""
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
            (3, "HOM-001", "Scented Candle", "home", 3.0, 14.99),
            (4, "SPO-001", "Running Shoes", "sports", 25.0, 79.99),
        ],
    )
    conn.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 1, date(2025, 6, 1), "web", "West", "completed", 59.98),
            (2, 1, date(2025, 7, 15), "mobile", "West", "completed", 29.99),
            (3, 2, date(2025, 6, 10), "web", "East", "completed", 24.99),
            (4, 3, date(2025, 8, 1), "web", "West", "completed", 94.98),
            (5, 3, date(2025, 8, 5), "marketplace", "West", "cancelled", 29.99),
            (6, 2, date(2025, 9, 1), "web", "East", "completed", 14.99),
        ],
    )
    conn.executemany(
        "INSERT INTO order_items VALUES (?, ?, ?, ?, ?)",
        [
            (1, 1, 1, 2, 29.99),  # order 1: 2x Blue Shirt = 59.98
            (2, 2, 1, 1, 29.99),  # order 2: 1x Blue Shirt = 29.99
            (3, 3, 2, 1, 24.99),  # order 3: 1x Wireless Mouse = 24.99
            (4, 4, 1, 1, 29.99),  # order 4: 1x Blue Shirt = 29.99
            (5, 4, 4, 1, 64.99),  # order 4: 1x Running Shoes = 64.99
            (6, 5, 1, 1, 29.99),  # order 5 (cancelled): 1x Blue Shirt
            (7, 6, 3, 1, 14.99),  # order 6: 1x Scented Candle = 14.99
            (8, 1, 2, 0, 24.99),  # order 1: 0x Mouse (edge case)
        ],
    )
    conn.executemany(
        "INSERT INTO returns VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 1, 1, 1, date(2025, 6, 15), 29.99),  # return from order 1, apparel
            (2, 3, 3, 2, date(2025, 6, 25), 24.99),  # return from order 3, electronics
        ],
    )
    conn.close()
    return db_path


@pytest.fixture(scope="module")
def executor(test_db_path: Path) -> MetricExecutor:
    """Create an executor pointed at the test database."""
    definitions_dir = Path(__file__).resolve().parent.parent / "metrics" / "definitions"
    registry = MetricRegistry(definitions_dir)
    return MetricExecutor(registry, test_db_path)


# ═══════════════════════════════════════════════════════════════
# average_order_value
# ═══════════════════════════════════════════════════════════════


class TestAverageOrderValue:
    """AOV = AVG(total_amount) for completed orders.

    5 completed orders: 59.98, 29.99, 24.99, 94.98, 14.99
    Mean = 224.93 / 5 = 44.99 (rounded to 2 decimal places)
    """

    def test_overall_aov(self, executor: MetricExecutor) -> None:
        result = executor.execute("average_order_value.yaml", FULL_RANGE)
        assert result.rows[0]["avg_order_value"] == pytest.approx(44.99, abs=0.01)
        assert result.rows[0]["order_count"] == 5

    def test_aov_by_region_west(self, executor: MetricExecutor) -> None:
        # West completed orders: 59.98, 29.99, 94.98 → avg = 61.65
        result = executor.execute(
            "average_order_value.yaml", {**FULL_RANGE, "region": "West"}
        )
        assert result.rows[0]["avg_order_value"] == pytest.approx(61.65, abs=0.01)
        assert result.rows[0]["order_count"] == 3

    def test_aov_by_segment_vip(self, executor: MetricExecutor) -> None:
        # VIP completed orders: customer 3 → order 4 (94.98)
        result = executor.execute(
            "average_order_value.yaml", {**FULL_RANGE, "segment": "VIP"}
        )
        assert result.rows[0]["avg_order_value"] == pytest.approx(94.98, abs=0.01)
        assert result.rows[0]["order_count"] == 1

    def test_aov_by_channel_web(self, executor: MetricExecutor) -> None:
        # Web completed: orders 1 (59.98), 3 (24.99), 4 (94.98), 6 (14.99)
        # avg = 194.94 / 4 = 48.74
        result = executor.execute(
            "average_order_value.yaml", {**FULL_RANGE, "channel": "web"}
        )
        assert result.rows[0]["avg_order_value"] == pytest.approx(48.74, abs=0.01)
        assert result.rows[0]["order_count"] == 4

    def test_aov_time_window(self, executor: MetricExecutor) -> None:
        # Jul-Aug completed: order 2 (29.99), order 4 (94.98) → avg = 62.49
        result = executor.execute(
            "average_order_value.yaml",
            {"start_date": "2025-07-01", "end_date": "2025-08-31"},
        )
        assert result.rows[0]["avg_order_value"] == pytest.approx(62.49, abs=0.01)

    def test_aov_invalid_segment(self, executor: MetricExecutor) -> None:
        with pytest.raises(ExecutionError, match="not in allowed values"):
            executor.execute(
                "average_order_value.yaml", {**FULL_RANGE, "segment": "premium"}
            )

    def test_aov_result_structure(self, executor: MetricExecutor) -> None:
        result = executor.execute("average_order_value.yaml", FULL_RANGE)
        assert result.metric_name == "average_order_value.yaml"
        assert "avg_order_value" in result.columns
        assert "order_count" in result.columns


# ═══════════════════════════════════════════════════════════════
# return_rate
# ═══════════════════════════════════════════════════════════════


class TestReturnRate:
    """Return rate = returned_orders / total_orders * 100.

    5 completed orders total. 2 orders had returns (order 1, order 3).
    Overall return rate = 2/5 * 100 = 40.0%
    """

    def test_overall_return_rate(self, executor: MetricExecutor) -> None:
        result = executor.execute("return_rate.yaml", FULL_RANGE)
        assert result.rows[0]["return_rate"] == pytest.approx(40.0, abs=0.01)
        assert result.rows[0]["returned_orders"] == 2
        assert result.rows[0]["total_orders"] == 5

    def test_return_rate_by_region_west(self, executor: MetricExecutor) -> None:
        # West completed: orders 1, 2, 4. Returns: order 1.
        # Rate = 1/3 * 100 = 33.33
        result = executor.execute(
            "return_rate.yaml", {**FULL_RANGE, "region": "West"}
        )
        assert result.rows[0]["return_rate"] == pytest.approx(33.33, abs=0.01)
        assert result.rows[0]["returned_orders"] == 1
        assert result.rows[0]["total_orders"] == 3

    def test_return_rate_by_region_east(self, executor: MetricExecutor) -> None:
        # East completed: orders 3, 6. Returns: order 3.
        # Rate = 1/2 * 100 = 50.0
        result = executor.execute(
            "return_rate.yaml", {**FULL_RANGE, "region": "East"}
        )
        assert result.rows[0]["return_rate"] == pytest.approx(50.0, abs=0.01)

    def test_return_rate_category_apparel(self, executor: MetricExecutor) -> None:
        # Orders containing apparel items: 1, 2, 4 (and 5 cancelled).
        # Completed apparel orders: 1, 2, 4 → 3 orders
        # Apparel returns: return 1 (product 1, order 1) → 1 returned order
        # Rate = 1/3 * 100 = 33.33
        result = executor.execute(
            "return_rate.yaml", {**FULL_RANGE, "category": "apparel"}
        )
        assert result.rows[0]["return_rate"] == pytest.approx(33.33, abs=0.01)
        assert result.rows[0]["total_orders"] == 3

    def test_return_rate_category_electronics(self, executor: MetricExecutor) -> None:
        # Orders containing electronics items: 1 (item 8, qty=0), 3.
        # Both orders have returns (order 1 → apparel return, order 3 → electronics).
        # Rate = 2/2 * 100 = 100.0
        result = executor.execute(
            "return_rate.yaml", {**FULL_RANGE, "category": "electronics"}
        )
        assert result.rows[0]["return_rate"] == pytest.approx(100.0, abs=0.01)
        assert result.rows[0]["total_orders"] == 2

    def test_return_rate_no_returns_category(self, executor: MetricExecutor) -> None:
        # Home category: order 6 has a candle. No returns for home products.
        # Rate = 0/1 * 100 = 0.0
        result = executor.execute(
            "return_rate.yaml", {**FULL_RANGE, "category": "home"}
        )
        assert result.rows[0]["return_rate"] == pytest.approx(0.0, abs=0.01)
        assert result.rows[0]["total_orders"] == 1

    def test_return_rate_time_window(self, executor: MetricExecutor) -> None:
        # Aug-Sep completed: orders 4, 6. No returns for either.
        # Rate = 0/2 = 0.0
        result = executor.execute(
            "return_rate.yaml",
            {"start_date": "2025-08-01", "end_date": "2025-09-30"},
        )
        assert result.rows[0]["return_rate"] == pytest.approx(0.0, abs=0.01)

    def test_return_rate_result_structure(self, executor: MetricExecutor) -> None:
        result = executor.execute("return_rate.yaml", FULL_RANGE)
        assert result.metric_name == "return_rate.yaml"
        assert set(result.columns) == {"return_rate", "returned_orders", "total_orders"}


# ═══════════════════════════════════════════════════════════════
# repeat_purchase_rate
# ═══════════════════════════════════════════════════════════════


class TestRepeatPurchaseRate:
    """Repeat purchase rate for customer cohorts.

    First orders by customer (completed only):
      - Customer 1: 2025-06-01 (cohort 2025-06)
      - Customer 2: 2025-06-10 (cohort 2025-06)
      - Customer 3: 2025-08-01 (cohort 2025-08)

    Repeat orders:
      - Customer 1: second order on 2025-07-15 (44 days after first)
      - Customer 2: second order on 2025-09-01 (83 days after first)
      - Customer 3: only 1 completed order (order 5 is cancelled)
    """

    def test_cohort_june_90day(self, executor: MetricExecutor) -> None:
        # June cohort: customers 1 and 2. Both repeat within 90 days.
        # Customer 1: 44 days → yes. Customer 2: 83 days → yes.
        # Rate = 2/2 * 100 = 100.0
        result = executor.execute(
            "repeat_purchase_rate.yaml", {"cohort_month": "2025-06"}
        )
        assert result.rows[0]["repeat_rate"] == pytest.approx(100.0, abs=0.01)
        assert result.rows[0]["cohort_size"] == 2
        assert result.rows[0]["repeat_customers"] == 2

    def test_cohort_june_30day(self, executor: MetricExecutor) -> None:
        # 30-day window: customer 1 repeats at 44 days (no), customer 2 at 83 days (no)
        # Rate = 0/2 = 0.0
        result = executor.execute(
            "repeat_purchase_rate.yaml",
            {"cohort_month": "2025-06", "repeat_window_days": 30},
        )
        assert result.rows[0]["repeat_rate"] == pytest.approx(0.0, abs=0.01)
        assert result.rows[0]["cohort_size"] == 2

    def test_cohort_june_60day(self, executor: MetricExecutor) -> None:
        # 60-day window: customer 1 at 44 days (yes), customer 2 at 83 days (no)
        # Rate = 1/2 * 100 = 50.0
        result = executor.execute(
            "repeat_purchase_rate.yaml",
            {"cohort_month": "2025-06", "repeat_window_days": 60},
        )
        assert result.rows[0]["repeat_rate"] == pytest.approx(50.0, abs=0.01)
        assert result.rows[0]["repeat_customers"] == 1

    def test_cohort_august(self, executor: MetricExecutor) -> None:
        # August cohort: customer 3 only. No repeat (order 5 is cancelled).
        # Rate = 0/1 = 0.0
        result = executor.execute(
            "repeat_purchase_rate.yaml", {"cohort_month": "2025-08"}
        )
        assert result.rows[0]["repeat_rate"] == pytest.approx(0.0, abs=0.01)
        assert result.rows[0]["cohort_size"] == 1

    def test_empty_cohort(self, executor: MetricExecutor) -> None:
        # No first purchases in January → cohort_size = 0
        result = executor.execute(
            "repeat_purchase_rate.yaml", {"cohort_month": "2025-01"}
        )
        assert result.rows[0]["cohort_size"] == 0

    def test_cohort_month_required(self, executor: MetricExecutor) -> None:
        with pytest.raises(ExecutionError, match="Missing required parameter"):
            executor.execute("repeat_purchase_rate.yaml", {})

    def test_result_structure(self, executor: MetricExecutor) -> None:
        result = executor.execute(
            "repeat_purchase_rate.yaml", {"cohort_month": "2025-06"}
        )
        assert result.metric_name == "repeat_purchase_rate.yaml"
        assert set(result.columns) == {"repeat_rate", "cohort_size", "repeat_customers"}


# ═══════════════════════════════════════════════════════════════
# top_products_by_revenue
# ═══════════════════════════════════════════════════════════════


class TestTopProductsByRevenue:
    """Top products ranked by revenue from completed orders.

    Revenue from completed order items (qty * unit_price):
      - Blue Shirt: (2*29.99) + (1*29.99) + (1*29.99) = 119.96
      - Running Shoes: 1*64.99 = 64.99
      - Wireless Mouse: (1*24.99) + (0*24.99) = 24.99
      - Scented Candle: 1*14.99 = 14.99
    """

    def test_all_products_ranked(self, executor: MetricExecutor) -> None:
        result = executor.execute(
            "top_products_by_revenue.yaml", {**FULL_RANGE, "limit": 10}
        )
        assert len(result.rows) == 4
        # Verify ordering
        assert result.rows[0]["product_name"] == "Blue Shirt"
        assert result.rows[1]["product_name"] == "Running Shoes"
        assert result.rows[2]["product_name"] == "Wireless Mouse"
        assert result.rows[3]["product_name"] == "Scented Candle"

    def test_revenue_values(self, executor: MetricExecutor) -> None:
        result = executor.execute(
            "top_products_by_revenue.yaml", {**FULL_RANGE, "limit": 10}
        )
        assert result.rows[0]["revenue"] == pytest.approx(119.96, abs=0.01)
        assert result.rows[1]["revenue"] == pytest.approx(64.99, abs=0.01)
        assert result.rows[2]["revenue"] == pytest.approx(24.99, abs=0.01)
        assert result.rows[3]["revenue"] == pytest.approx(14.99, abs=0.01)

    def test_limit_parameter(self, executor: MetricExecutor) -> None:
        result = executor.execute(
            "top_products_by_revenue.yaml", {**FULL_RANGE, "limit": 2}
        )
        assert len(result.rows) == 2
        assert result.rows[0]["product_name"] == "Blue Shirt"
        assert result.rows[1]["product_name"] == "Running Shoes"

    def test_default_limit_is_10(self, executor: MetricExecutor) -> None:
        # With only 4 products, default limit of 10 returns all 4
        result = executor.execute("top_products_by_revenue.yaml", FULL_RANGE)
        assert len(result.rows) == 4

    def test_category_filter(self, executor: MetricExecutor) -> None:
        result = executor.execute(
            "top_products_by_revenue.yaml",
            {**FULL_RANGE, "category": "apparel", "limit": 10},
        )
        assert len(result.rows) == 1
        assert result.rows[0]["product_name"] == "Blue Shirt"
        assert result.rows[0]["category"] == "apparel"

    def test_time_window_filter(self, executor: MetricExecutor) -> None:
        # Aug only: order 4 has Blue Shirt (29.99) + Running Shoes (64.99)
        result = executor.execute(
            "top_products_by_revenue.yaml",
            {"start_date": "2025-08-01", "end_date": "2025-08-31", "limit": 10},
        )
        assert len(result.rows) == 2
        assert result.rows[0]["product_name"] == "Running Shoes"
        assert result.rows[1]["product_name"] == "Blue Shirt"

    def test_units_sold_column(self, executor: MetricExecutor) -> None:
        result = executor.execute(
            "top_products_by_revenue.yaml", {**FULL_RANGE, "limit": 10}
        )
        # Blue Shirt: qty 2+1+1 = 4 units
        assert result.rows[0]["units_sold"] == 4

    def test_result_structure(self, executor: MetricExecutor) -> None:
        result = executor.execute("top_products_by_revenue.yaml", FULL_RANGE)
        assert result.metric_name == "top_products_by_revenue.yaml"
        assert set(result.columns) == {
            "product_name", "category", "revenue", "units_sold"
        }
        assert result.metadata["row_count"] == len(result.rows)