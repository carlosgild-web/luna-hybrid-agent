"""The business tools answer the reference questions (PLAN_HYBRID.md section 4).

Every expected figure is either one the Q3 meeting notes report (the demo DB is
calibrated against them) or recomputed here straight from the tables.
"""

import ast
import re
import sqlite3
from pathlib import Path

import pytest

from app.agent.business_seed import seed_business_db
from app.agent.registry import ToolContext, ToolResult
from app.agent.tools import build_registry
from app.agent.tools.business import SCHEMA_SUMMARY
from app.core.config import Settings

Q2 = {"date_from": "2026-04-01", "date_to": "2026-06-30"}
Q3 = {"date_from": "2026-07-01", "date_to": "2026-09-30"}
YEAR = {"date_from": "2026-01-01", "date_to": "2026-12-31"}


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    path = tmp_path_factory.mktemp("business") / "hilda_luna.db"
    summary = seed_business_db(path)
    return path, summary


@pytest.fixture(scope="module")
def ctx(seeded) -> ToolContext:
    path, _ = seeded
    return ToolContext(Settings(business_db_path=path))


@pytest.fixture(scope="module")
def raw(seeded):
    """A plain connection for recomputing expected values independently of the tools."""
    connection = sqlite3.connect(seeded[0])
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def registry():
    return build_registry()


@pytest.fixture
def call(registry, ctx):
    def _call(name: str, **arguments) -> ToolResult:
        return registry.execute(name, arguments, ctx)

    return _call


def ok(result: ToolResult) -> dict:
    assert result.ok, result.error
    return result.content


# ---------------------------------------------------------------- inventory


def test_what_needs_restocking(call):
    """ "¿Qué productos necesitan resurtido?" (the Phase 3 acceptance question)."""
    content = ok(call("check_inventory", only_below_reorder=True))
    assert [item["sku"] for item in content["items"]] == ["BN-9001", "CH-2004", "GB-1014"]
    assert all(item["below_reorder_point"] for item in content["items"])
    shortfalls = {item["sku"]: item["units_short"] for item in content["items"]}
    assert shortfalls == {"BN-9001": 5, "CH-2004": 1600, "GB-1014": 150}
    assert content["as_of"] == "2026-11-14"


def test_stock_of_one_sku(call):
    """ "¿Cuánto stock hay del SKU CH-2004 y dónde está ubicado?" """
    [item] = ok(call("check_inventory", sku="ch-2004 "))["items"]
    assert item == {
        "sku": "CH-2004",
        "name": "Crescent Moon & Star",
        "category": "charm",
        "stock": 1900,
        "reorder_point": 3500,
        "below_reorder_point": True,
        "location": "Bay 3 - Rack A",
        "units_short": 1600,
    }


def test_whole_inventory(call):
    content = ok(call("check_inventory"))
    assert content["count"] == 13
    assert {item["category"] for item in content["items"]} == {"glass bead", "charm", "bundle"}
    assert "units_short" not in next(i for i in content["items"] if i["sku"] == "GB-1027")


def test_a_sku_that_is_fine_is_not_below_reorder(call):
    assert ok(call("check_inventory", sku="GB-1027", only_below_reorder=True))["items"] == []


@pytest.mark.parametrize("flag", [False, True])
def test_unknown_sku_lists_the_known_ones(call, flag):
    result = call("check_inventory", sku="XX-0000", only_below_reorder=flag)
    assert not result.ok
    assert result.error.startswith("No product with SKU 'XX-0000'. Known SKUs: BN-9001,")


# -------------------------------------------------------------------- sales


def test_top_5_skus_by_units_in_q3(call):
    """ "Top 5 SKUs by units in Q3" (PLAN_HYBRID.md section 5)."""
    content = ok(call("sales_report", **Q3, group_by="sku", top_n=5, order_by="units"))
    top = [(row["group"], row["units"]) for row in content["rows"]]
    assert top == [
        ("CH-2004", 4120),  # meeting notes: 4,120
        ("GB-1014", 3340),  # meeting notes: 3,340
        ("GB-1001", 3000),
        ("GB-1002", 2600),
        ("CH-2033", 2400),
    ]
    assert content["rows"][0]["name"] == "Crescent Moon & Star"
    assert content["groups_total"] == 12


def test_q3_revenue_matches_the_books(call, seeded, raw):
    _, summary = seeded
    content = ok(call("sales_report", **Q3, group_by="month"))
    books = raw.execute(
        "SELECT SUM(total_mxn), SUM(total_usd), COUNT(*) FROM pedidos "
        "WHERE fecha BETWEEN '2026-07-01' AND '2026-09-30'"
    ).fetchone()
    assert (
        content["totals"]["revenue_mxn"] == round(books[0], 2) == round(summary.q3_revenue_mxn, 2)
    )
    assert content["totals"]["revenue_usd"] == round(books[1], 2)
    assert content["totals"]["orders"] == books[2]
    assert [row["group"] for row in content["rows"]] == ["2026-07", "2026-08", "2026-09"]
    assert content["order_by"] == "date"


def test_wholesale_share_of_q3_revenue(call):
    """The notes say 61% wholesale; the calibrated DB gives 59.8%."""
    rows = ok(call("sales_report", **Q3, group_by="channel"))["rows"]
    shares = {row["group"]: row["share_of_revenue_pct"] for row in rows}
    assert shares == {"wholesale": 59.8, "retail": 40.2}


def test_terracotta_sunrise_fell_22_percent(call):
    """ "Comparativa Q2 vs Q3 por SKU (¿qué cayó? → GB-1027)" """

    def units(period):
        rows = ok(call("sales_report", **period, group_by="sku"))["rows"]
        return next(row["units"] for row in rows if row["group"] == "GB-1027")

    q2, q3 = units(Q2), units(Q3)
    assert (q2, q3) == (1800, 1404)
    assert round(100 * (q3 - q2) / q2) == -22


def test_quarters_of_the_year(call):
    content = ok(call("sales_report", **YEAR, group_by="quarter"))
    assert [row["group"] for row in content["rows"]] == ["2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"]
    assert content["notes"] == ["The data covers 2026-01-05 to 2026-11-14 only."]


@pytest.mark.parametrize(
    "group_by", ["month", "quarter", "sku", "category", "channel", "collection", "customer"]
)
def test_every_grouping_adds_up_to_the_totals(call, raw, group_by):
    content = ok(call("sales_report", **YEAR, group_by=group_by))
    rows, totals = content["rows"], content["totals"]
    assert content["groups_total"] == len(rows)
    assert sum(row["units"] for row in rows) == totals["units"]
    # each group is rounded to cents on its own
    assert sum(row["revenue_mxn"] for row in rows) == pytest.approx(totals["revenue_mxn"], abs=0.1)
    assert sum(row["share_of_revenue_pct"] for row in rows) == pytest.approx(100, abs=0.5)
    year_mxn = raw.execute("SELECT SUM(total_mxn) FROM pedidos").fetchone()[0]
    assert totals["revenue_mxn"] == round(year_mxn, 2)


def test_sales_by_collection_and_category_are_translated(call):
    collections = {
        row["group"] for row in ok(call("sales_report", **YEAR, group_by="collection"))["rows"]
    }
    assert "Permanent" in collections
    assert "Permanente" not in collections
    categories = {
        row["group"] for row in ok(call("sales_report", **YEAR, group_by="category"))["rows"]
    }
    assert categories == {"glass bead", "charm", "bundle"}


def test_biggest_customer_is_abalone(call):
    """The meeting notes name Abalone Supply Co. as the largest customer."""
    content = ok(call("sales_report", **YEAR, group_by="customer", top_n=3))
    first = content["rows"][0]
    assert first["group"] == "Abalone Supply Co."
    assert first["customer_type"] == "distributor"
    assert len(content["rows"]) == 3
    assert content["groups_total"] == 68


def test_top_n_on_months_ranks_by_revenue(call):
    content = ok(call("sales_report", **YEAR, group_by="month", top_n=2))
    revenues = [row["revenue_mxn"] for row in content["rows"]]
    assert revenues == sorted(revenues, reverse=True)
    assert content["order_by"] == "revenue"


def test_a_range_with_no_orders_says_what_the_data_covers(call):
    """ "What's our Q1 2027 revenue?" must come back as unknown, not as zero sales."""
    content = ok(
        call("sales_report", date_from="2027-01-01", date_to="2027-03-31", group_by="month")
    )
    assert content["rows"] == []
    assert content["totals"]["orders"] == 0
    assert content["notes"] == [
        "No orders in this range. The data covers 2026-01-05 to 2026-11-14."
    ]


def test_a_backwards_range_is_refused(call):
    result = call("sales_report", date_from="2026-09-30", date_to="2026-07-01", group_by="sku")
    assert not result.ok
    assert "date_from must be on or before date_to" in result.error


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({**Q3, "group_by": "week"}, "group_by"),
        ({**Q3, "group_by": "sku", "top_n": 0}, "top_n"),
        ({"date_from": "julio", "date_to": "2026-09-30", "group_by": "sku"}, "date_from"),
        ({"date_from": "2026-07-01", "group_by": "sku"}, "date_to"),
    ],
)
def test_bad_sales_arguments_name_the_field(call, arguments, field):
    result = call("sales_report", **arguments)
    assert not result.ok
    assert f"{field}: " in result.error


# ---------------------------------------------------------------- purchases


def test_purchases_under_contract_xa_2026_014(call, raw):
    """ "¿Cuánto le hemos comprado al proveedor del contrato XA-2026-014 y qué tenemos en
    tránsito?" """
    content = ok(call("supplier_purchases", supplier="XA-2026-014"))
    expected = raw.execute(
        "SELECT COUNT(*), SUM(total_mxn) FROM compras_proveedor WHERE proveedor_id = 1"
    ).fetchone()
    assert content["count"] == expected[0] == 7
    assert content["totals"]["total_mxn"] == round(expected[1], 2)
    assert {p["supplier"] for p in content["purchases"]} == {"Cristal Dorado Vidrios S.A. de C.V."}

    [in_transit] = ok(call("supplier_purchases", supplier="cristal dorado", status="in_transit"))[
        "purchases"
    ]
    assert in_transit["status"] == "in transit"
    assert in_transit["total_mxn"] == 8550.0


def test_supplier_names_match_without_accents(call):
    content = ok(call("supplier_purchases", supplier="fundicion peltre"))
    assert content["count"] == 2
    assert content["totals"]["by_supplier"] == [
        {"supplier": "Fundición Peltre del Bajío", "purchases": 2, "total_mxn": 92000.0}
    ]


@pytest.mark.parametrize(
    ("status", "count"), [("received", 10), ("received_late", 1), ("in_transit", 1)]
)
def test_purchases_by_status(call, status, count):
    assert ok(call("supplier_purchases", status=status))["count"] == count


def test_all_purchases_with_totals_per_supplier(call, raw):
    content = ok(call("supplier_purchases"))
    assert content["count"] == 11
    dates = [p["date"] for p in content["purchases"]]
    assert dates == sorted(dates)
    by_supplier = {row["supplier"]: row["total_mxn"] for row in content["totals"]["by_supplier"]}
    expected = dict(
        raw.execute(
            "SELECT pv.nombre, ROUND(SUM(cp.total_mxn), 2) FROM compras_proveedor cp "
            "JOIN proveedores pv USING (proveedor_id) GROUP BY pv.nombre"
        ).fetchall()
    )
    assert by_supplier == expected


def test_unknown_supplier_lists_the_suppliers(call):
    result = call("supplier_purchases", supplier="Acme")
    assert not result.ok
    assert result.error.startswith("No supplier matches 'Acme'. Suppliers: Cristal Dorado")


# --------------------------------------------------------------- sql_select


def test_sql_select_answers_what_the_typed_tools_cannot(call):
    """ "Clientes nuevos por trimestre" (Thistle & Thread and Casa de Cuentas in Q3)."""
    content = ok(
        call(
            "sql_select",
            query="SELECT nombre FROM clientes WHERE fecha_alta BETWEEN '2026-07-01' "
            "AND '2026-09-30' AND tipo != 'Hobbyist' ORDER BY fecha_alta;",
        )
    )
    assert content == {
        "columns": ["nombre"],
        "rows": [["Thistle & Thread"], ["Casa de Cuentas"]],
        "row_count": 2,
        "truncated": False,
    }


def test_sql_select_refuses_a_write(call):
    """ "Delete the orders table" (an agent refusal case)."""
    result = call("sql_select", query="DROP TABLE pedidos")
    assert not result.ok
    assert result.error == "Only SELECT (or WITH ... SELECT) queries are allowed"
    assert ok(call("sql_select", query="SELECT COUNT(*) FROM pedidos"))["rows"] == [[937]]


def test_sql_select_reports_a_wrong_column(call):
    result = call("sql_select", query="SELECT revenue FROM pedidos")
    assert not result.ok
    assert result.error == "SQL error: no such column: revenue"


def test_the_schema_in_the_description_matches_the_database(raw):
    """The model writes SQL from SCHEMA_SUMMARY, so it must not drift from the tables."""
    described = {
        table: [column.strip() for column in re.sub(r"\[[^\]]*\]", "", columns).split(",")]
        for table, columns in re.findall(r"- (\w+)\((.*?)\)$", SCHEMA_SUMMARY, re.S | re.M)
    }
    actual = {
        table: [row[1] for row in raw.execute(f"PRAGMA table_info({table})")]
        for (table,) in raw.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert described == actual


def test_the_coded_values_in_the_description_match_the_database(raw):
    for table, column in [
        ("productos", "categoria"),
        ("productos", "coleccion"),
        ("clientes", "tipo"),
        ("pedidos", "canal"),
        ("compras_proveedor", "estado"),
    ]:
        values = {row[0] for row in raw.execute(f"SELECT DISTINCT {column} FROM {table}")}
        listed = re.search(rf"{column} \[([^\]]*)\]", SCHEMA_SUMMARY).group(1)
        assert {v.strip(" '") for v in listed.split(",")} == values, column


# ---------------------------------------------------------------- the rules


def test_a_missing_database_says_how_to_create_it(registry, tmp_path):
    ctx = ToolContext(Settings(data_dir=tmp_path))
    for name, arguments in [
        ("check_inventory", {}),
        ("sales_report", {**Q3, "group_by": "sku"}),
        ("supplier_purchases", {}),
        ("sql_select", {"query": "SELECT 1"}),
    ]:
        result = registry.execute(name, arguments, ctx)
        assert not result.ok, name
        assert "python -m app.cli seed-business-db" in result.error


def test_tools_never_import_the_service_layer():
    """Invariant 9: tool handlers never import the service layer's writers."""
    agent = Path(__file__).parents[1] / "app" / "agent"
    forbidden = ("app.versioning", "app.ingest", "app.db", "app.jobs")
    for source in [agent / "registry.py", *sorted((agent / "tools").glob("*.py"))]:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                assert not name.startswith(forbidden), f"{source.name} imports {name}"


def test_results_are_json_for_the_model(call):
    """What the model sees is compact JSON, accents kept."""
    message = call("supplier_purchases", supplier="fundicion").to_message()
    assert message.startswith('{"count":2,')
    assert "Fundición" in message
