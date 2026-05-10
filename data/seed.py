"""Generate the synthetic e-commerce DuckDB database for governed-metrics-mcp.

Deterministic via a fixed seed. Run as:
    uv run python data/seed.py
or with a custom seed/output:
    uv run python data/seed.py --seed 7 --output data/test.duckdb
"""

import argparse
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple, TypeVar

import duckdb
from faker import Faker


# ───────────────────────── Row types ─────────────────────────

class Customer(NamedTuple):
    customer_id: int
    email: str
    first_name: str
    last_name: str
    region: str
    signup_date: date
    segment: str


class Product(NamedTuple):
    product_id: int
    sku: str
    name: str
    category: str
    cost: float
    list_price: float


class Order(NamedTuple):
    order_id: int
    customer_id: int
    order_date: date
    channel: str
    region: str
    status: str
    total_amount: float


class OrderItem(NamedTuple):
    order_item_id: int
    order_id: int
    product_id: int
    quantity: int
    unit_price: float


class Return(NamedTuple):
    return_id: int
    order_item_id: int
    order_id: int
    product_id: int
    return_date: date
    amount: float


# ───────────────────────── Configuration ─────────────────────────

DEFAULT_SEED: int = 42
DEFAULT_DB_PATH: Path = Path(__file__).parent / "metrics.duckdb"

N_CUSTOMERS: int = 5_000
N_PRODUCTS: int = 500

WINDOW_END: date = date(2026, 5, 9)
WINDOW_DAYS: int = 540  # 18 months
WINDOW_START: date = WINDOW_END - timedelta(days=WINDOW_DAYS)

REGIONS: tuple[str, ...] = ("West", "East", "Central", "South")
REGION_WEIGHTS: tuple[float, ...] = (0.30, 0.30, 0.20, 0.20)

CHANNELS: tuple[str, ...] = ("web", "mobile", "marketplace")
CHANNEL_WEIGHTS: tuple[float, ...] = (0.55, 0.30, 0.15)

CATEGORIES: tuple[str, ...] = ("apparel", "electronics", "home", "beauty", "sports")

SEGMENTS: tuple[str, ...] = ("new", "returning", "VIP")
SEGMENT_WEIGHTS: tuple[float, ...] = (0.40, 0.40, 0.20)

# Order count per customer by segment → ~50k total orders
SEGMENT_ORDER_RANGES: dict[str, tuple[int, int]] = {
    "new":       (1, 1),
    "returning": (2, 8),
    "VIP":       (20, 60),
}

# Items per order: avg ≈ 3 → ~150k total line items
ITEMS_PER_ORDER: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8)
ITEMS_PER_ORDER_WEIGHTS: tuple[float, ...] = (
    0.15, 0.22, 0.25, 0.18, 0.12, 0.05, 0.02, 0.01,
)

# Per-category return rate → ~6k total returns
RETURN_RATE_BY_CATEGORY: dict[str, float] = {
    "apparel":     0.12,
    "electronics": 0.06,
    "home":        0.04,
    "beauty":      0.02,
    "sports":      0.05,
}

# (cost_min, cost_max) per category. List price = cost × uniform(1.5, 3.0).
COST_RANGES: dict[str, tuple[float, float]] = {
    "apparel":     (5.0,  80.0),
    "electronics": (20.0, 800.0),
    "home":        (8.0,  200.0),
    "beauty":      (3.0,  60.0),
    "sports":      (10.0, 300.0),
}

# Adjective/noun pools for product naming — gives output like "Cotton Shirt"
PRODUCT_ADJECTIVES: dict[str, list[str]] = {
    "apparel":     ["Cotton", "Wool", "Silk", "Denim", "Linen", "Merino"],
    "electronics": ["Wireless", "Smart", "Portable", "HD", "Bluetooth", "USB-C"],
    "home":        ["Ceramic", "Wooden", "Modern", "Vintage", "Stainless", "Marble"],
    "beauty":      ["Organic", "Hydrating", "Anti-Aging", "Natural", "Luxury", "Botanical"],
    "sports":      ["Pro", "Performance", "Endurance", "Tactical", "Athletic", "Trail"],
}
PRODUCT_NOUNS: dict[str, list[str]] = {
    "apparel":     ["Shirt", "Pants", "Jacket", "Sweater", "Hoodie", "Dress"],
    "electronics": ["Speaker", "Headphones", "Charger", "Cable", "Earbuds", "Hub"],
    "home":        ["Lamp", "Vase", "Mug", "Pillow", "Throw", "Candle"],
    "beauty":      ["Serum", "Cream", "Lotion", "Mask", "Cleanser", "Toner"],
    "sports":      ["Backpack", "Water Bottle", "Gloves", "Mat", "Strap", "Foam Roller"],
}

ORDER_CANCEL_RATE: float = 0.03
RETURN_DELAY_DAYS_RANGE: tuple[int, int] = (3, 60)
INSERT_CHUNK_SIZE: int = 5_000

ALLOWED_TABLES: frozenset[str] = frozenset({
    "customers", "products", "orders", "order_items", "returns",
})


# ───────────────────────── Schema DDL ─────────────────────────

SCHEMA_DDL: str = """
DROP TABLE IF EXISTS returns;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

-- FK relationships are documented by column names (customer_id, order_id, etc.)
-- but NOT declared as constraints. DuckDB enforces FOREIGN KEYs with a per-row
-- hash-join lookup on insert, which makes bulk-seeding 150k+ rows take minutes
-- instead of seconds. Referential integrity is guaranteed by the seed script's
-- construction order (parents always exist before children reference them).
-- Standard pattern for analytics warehouses where data lands via batch ETL.

CREATE TABLE customers (
    customer_id   INTEGER PRIMARY KEY,
    email         VARCHAR NOT NULL,
    first_name    VARCHAR NOT NULL,
    last_name     VARCHAR NOT NULL,
    region        VARCHAR NOT NULL,
    signup_date   DATE    NOT NULL,
    segment       VARCHAR NOT NULL
);

CREATE TABLE products (
    product_id    INTEGER PRIMARY KEY,
    sku           VARCHAR NOT NULL,
    name          VARCHAR NOT NULL,
    category      VARCHAR NOT NULL,
    cost          DOUBLE  NOT NULL,
    list_price    DOUBLE  NOT NULL
);

CREATE TABLE orders (
    order_id      INTEGER PRIMARY KEY,
    customer_id   INTEGER NOT NULL,
    order_date    DATE    NOT NULL,
    channel       VARCHAR NOT NULL,
    region        VARCHAR NOT NULL,
    status        VARCHAR NOT NULL,
    total_amount  DOUBLE  NOT NULL
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL,
    product_id    INTEGER NOT NULL,
    quantity      INTEGER NOT NULL,
    unit_price    DOUBLE  NOT NULL
);

CREATE TABLE returns (
    return_id     INTEGER PRIMARY KEY,
    order_item_id INTEGER NOT NULL,
    order_id      INTEGER NOT NULL,
    product_id    INTEGER NOT NULL,
    return_date   DATE    NOT NULL,
    amount        DOUBLE  NOT NULL
);
"""


# ───────────────────────── Helpers ─────────────────────────

T = TypeVar("T")


def ramped_date(rng: random.Random, start: date, end: date) -> date:
    """Sample a date in [start, end] biased toward end via triangular(0, 1, 1).

    Triangular with mode=high yields a linear ramp peaking at end — simulates a
    business growing over the window.
    """
    if start >= end:
        return start
    fraction = rng.triangular(0.0, 1.0, 1.0)
    span_days = (end - start).days
    return start + timedelta(days=int(fraction * span_days))


def weighted_choice(
    rng: random.Random, items: tuple[T, ...], weights: tuple[float, ...]
) -> T:
    """Single weighted draw — thin wrapper over rng.choices for readability."""
    return rng.choices(items, weights=list(weights), k=1)[0]


# ───────────────────────── Generators ─────────────────────────

def generate_customers(faker: Faker, rng: random.Random) -> list[Customer]:
    """Generate N_CUSTOMERS customers with regional and segment distributions."""
    customers: list[Customer] = []
    for i in range(1, N_CUSTOMERS + 1):
        first = faker.first_name()
        last = faker.last_name()
        # Index in email guarantees uniqueness regardless of name collisions
        email = f"{first.lower()}.{last.lower()}{i}@example.com"
        customers.append(Customer(
            customer_id=i,
            email=email,
            first_name=first,
            last_name=last,
            region=weighted_choice(rng, REGIONS, REGION_WEIGHTS),
            signup_date=ramped_date(rng, WINDOW_START, WINDOW_END),
            segment=weighted_choice(rng, SEGMENTS, SEGMENT_WEIGHTS),
        ))
    return customers


def generate_products(faker: Faker, rng: random.Random) -> list[Product]:
    """Generate N_PRODUCTS products evenly distributed across CATEGORIES."""
    products: list[Product] = []
    products_per_category = N_PRODUCTS // len(CATEGORIES)
    pid = 1
    for category in CATEGORIES:
        cost_min, cost_max = COST_RANGES[category]
        adjectives = PRODUCT_ADJECTIVES[category]
        nouns = PRODUCT_NOUNS[category]
        for _ in range(products_per_category):
            cost = round(rng.uniform(cost_min, cost_max), 2)
            margin = rng.uniform(1.5, 3.0)
            list_price = round(cost * margin, 2)
            sku = f"{category[:3].upper()}-{pid:05d}"
            name = f"{rng.choice(adjectives)} {rng.choice(nouns)}"
            products.append(Product(
                product_id=pid,
                sku=sku,
                name=name,
                category=category,
                cost=cost,
                list_price=list_price,
            ))
            pid += 1
    return products


def generate_orders(rng: random.Random, customers: list[Customer]) -> list[Order]:
    """Generate orders. Per-customer count from SEGMENT_ORDER_RANGES.

    order_date is ramped on [signup_date, WINDOW_END]. Most orders are 'completed';
    a small fraction are 'cancelled' per ORDER_CANCEL_RATE. total_amount is 0.0
    here — populated by with_computed_totals() after items are generated.
    """
    orders: list[Order] = []
    next_order_id = 1
    for customer in customers:
        n_min, n_max = SEGMENT_ORDER_RANGES[customer.segment]
        n_orders = rng.randint(n_min, n_max)
        for _ in range(n_orders):
            status = "cancelled" if rng.random() < ORDER_CANCEL_RATE else "completed"
            orders.append(Order(
                order_id=next_order_id,
                customer_id=customer.customer_id,
                order_date=ramped_date(rng, customer.signup_date, WINDOW_END),
                channel=weighted_choice(rng, CHANNELS, CHANNEL_WEIGHTS),
                region=customer.region,
                status=status,
                total_amount=0.0,  # patched by with_computed_totals
            ))
            next_order_id += 1
    return orders


def generate_order_items(
    rng: random.Random, orders: list[Order], products: list[Product]
) -> list[OrderItem]:
    """Generate 1-8 line items per order via weighted distribution.

    unit_price applies a small downward jitter to list_price (simulates promotions).
    """
    items: list[OrderItem] = []
    next_item_id = 1
    for order in orders:
        n_items = weighted_choice(rng, ITEMS_PER_ORDER, ITEMS_PER_ORDER_WEIGHTS)
        # Sample without replacement so an order doesn't have the same product twice
        order_products = rng.sample(products, k=min(n_items, len(products)))
        for product in order_products:
            quantity = rng.randint(1, 3)
            jitter = rng.uniform(0.9, 1.0)  # 0-10% off list price
            unit_price = round(product.list_price * jitter, 2)
            items.append(OrderItem(
                order_item_id=next_item_id,
                order_id=order.order_id,
                product_id=product.product_id,
                quantity=quantity,
                unit_price=unit_price,
            ))
            next_item_id += 1
    return items


def with_computed_totals(orders: list[Order], items: list[OrderItem]) -> list[Order]:
    """Patch each order's total_amount with sum(quantity × unit_price) over its items."""
    totals: dict[int, float] = defaultdict(float)
    for item in items:
        totals[item.order_id] += item.quantity * item.unit_price
    return [
        order._replace(total_amount=round(totals[order.order_id], 2))
        for order in orders
    ]


def generate_returns(
    rng: random.Random,
    order_items: list[OrderItem],
    products_by_id: dict[int, Product],
    orders_by_id: dict[int, Order],
) -> list[Return]:
    """Generate returns based on per-category return rate.

    For each item in a *completed* order, sample uniform vs the category's return
    rate. return_date = order_date + uniform(RETURN_DELAY_DAYS_RANGE), capped at
    WINDOW_END (no time-traveling returns).
    """
    returns: list[Return] = []
    next_return_id = 1
    delay_min, delay_max = RETURN_DELAY_DAYS_RANGE

    for item in order_items:
        order = orders_by_id[item.order_id]
        if order.status != "completed":
            continue
        product = products_by_id[item.product_id]
        rate = RETURN_RATE_BY_CATEGORY[product.category]
        if rng.random() >= rate:
            continue
        delay = rng.randint(delay_min, delay_max)
        return_date = order.order_date + timedelta(days=delay)
        if return_date > WINDOW_END:
            continue
        returns.append(Return(
            return_id=next_return_id,
            order_item_id=item.order_item_id,
            order_id=item.order_id,
            product_id=item.product_id,
            return_date=return_date,
            amount=round(item.quantity * item.unit_price, 2),
        ))
        next_return_id += 1
    return returns


# ───────────────────────── DB ─────────────────────────

def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Drop and recreate all tables. Idempotent."""
    con.execute(SCHEMA_DDL)


def insert_rows(
    con: duckdb.DuckDBPyConnection,
    table: str,
    rows: list,
    n_columns: int,
) -> None:
    """Bulk insert via executemany with parameterized placeholders, chunked.

    Table name is interpolated into the SQL (not a parameter — placeholders only
    work for values, not identifiers), so we validate against an allowlist to
    prevent injection-like surprises if this is ever called from elsewhere.
    """
    if table not in ALLOWED_TABLES:
        raise ValueError(f"Refusing to insert into unknown table: {table!r}")
    if not rows:
        return
    placeholders = ", ".join("?" * n_columns)
    sql = f"INSERT INTO {table} VALUES ({placeholders})"
    for start in range(0, len(rows), INSERT_CHUNK_SIZE):
        chunk = rows[start:start + INSERT_CHUNK_SIZE]
        con.executemany(sql, chunk)


# ───────────────────────── Orchestrator + CLI ─────────────────────────

def seed(db_path: Path, seed_value: int = DEFAULT_SEED) -> dict[str, int]:
    """Generate all data and write to db_path. Returns row counts per table."""
    rng = random.Random(seed_value)
    faker = Faker()
    faker.seed_instance(seed_value)

    print("Generating customers...")
    customers = generate_customers(faker, rng)

    print("Generating products...")
    products = generate_products(faker, rng)

    print("Generating orders...")
    orders = generate_orders(rng, customers)

    print("Generating order items...")
    items = generate_order_items(rng, orders, products)

    print("Computing order totals...")
    orders = with_computed_totals(orders, items)

    print("Generating returns...")
    products_by_id = {p.product_id: p for p in products}
    orders_by_id = {o.order_id: o for o in orders}
    returns = generate_returns(rng, items, products_by_id, orders_by_id)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    print(f"Writing to {db_path}...")
    with duckdb.connect(str(db_path)) as con:
        create_schema(con)
        insert_rows(con, "customers",   customers, 7)
        insert_rows(con, "products",    products,  6)
        insert_rows(con, "orders",      orders,    7)
        insert_rows(con, "order_items", items,     5)
        insert_rows(con, "returns",     returns,   6)

    return {
        "customers":   len(customers),
        "products":    len(products),
        "orders":      len(orders),
        "order_items": len(items),
        "returns":     len(returns),
    }


def main() -> None:
    """Parse CLI args, run seed(), print summary."""
    parser = argparse.ArgumentParser(
        description="Generate synthetic e-commerce data into a DuckDB database.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Output DuckDB file path (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Random seed for determinism (default: {DEFAULT_SEED}).",
    )
    args = parser.parse_args()

    counts = seed(args.output, args.seed)

    print()
    print(f"Seeded {args.output}:")
    for table, count in counts.items():
        print(f"  {table:14s} {count:>7,d}")
    print()


if __name__ == "__main__":
    main()