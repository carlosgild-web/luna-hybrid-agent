"""The business tools: inventory, sales, supplier purchases, and `sql_select`.

Pure functions over the read-only business database (agent invariant 9): each
call opens its own read-only connection and closes it. The database is in
Spanish, like the company; the typed tools answer with English keys and
translate coded values (channel, category, status), while proper names stay as
they are. `sql_select` returns the raw rows.

Money comes back rounded to cents, in USD and in MXN at the rate stored on each
order, so the model never has to do the arithmetic (agent invariant 12).
"""

import sqlite3
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any, Literal

from pydantic import Field, model_validator

from app.agent.business_db import BusinessDBMissingError, open_business_db
from app.agent.registry import Tool, ToolArgs, ToolContext, ToolError
from app.agent.tools.sql_guard import check_select

SQL_MAX_ROWS = 200
SQL_TIMEOUT_S = 3.0
SQL_MAX_CELL_CHARS = 500

CATEGORY = {"Cuenta de vidrio": "glass bead", "Dije": "charm", "Bundle": "bundle"}
CHANNEL = {"Mayoreo": "wholesale", "Menudeo": "retail"}
COLLECTION = {"Permanente": "Permanent"}
CUSTOMER_TYPE = {"Hobbyist": "hobbyist", "Boutique": "boutique", "Distribuidor": "distributor"}
PURCHASE_STATUS = {
    "Recibida": "received",
    "Recibida con retraso": "received late",
    "En tránsito": "in transit",
}


@contextmanager
def _db(ctx: ToolContext) -> Iterator[sqlite3.Connection]:
    try:
        cx = open_business_db(ctx.settings.business_db_file)
    except BusinessDBMissingError as exc:
        raise ToolError(str(exc)) from exc
    try:
        yield cx
    finally:
        cx.close()


def _money(value: float | None) -> float:
    return round(value or 0.0, 2)


# ---------------------------------------------------------------- inventory


class CheckInventoryArgs(ToolArgs):
    sku: str | None = Field(None, description="One exact SKU, e.g. CH-2004. Leave out for all.")
    only_below_reorder: bool = Field(
        False, description="true: only the products whose stock is below the reorder point"
    )


def check_inventory(args: CheckInventoryArgs, ctx: ToolContext) -> dict[str, Any]:
    sql = (
        "SELECT i.sku, p.nombre, p.categoria, i.stock_actual, i.punto_reorden, i.ubicacion,"
        " i.actualizado FROM inventario i JOIN productos p ON p.sku = i.sku"
    )
    where, params = [], []
    sku = args.sku.upper() if args.sku else None
    if sku:
        where.append("i.sku = ?")
        params.append(sku)
    if args.only_below_reorder:
        where.append("i.stock_actual < i.punto_reorden")
    if where:
        sql += " WHERE " + " AND ".join(where)
    with _db(ctx) as cx:
        rows = cx.execute(sql + " ORDER BY i.sku", params).fetchall()
        known = [r[0] for r in cx.execute("SELECT sku FROM inventario ORDER BY sku")]
    if sku and sku not in known:
        raise ToolError(f"No product with SKU {sku!r}. Known SKUs: {', '.join(known)}")

    items = []
    for r in rows:
        below = r["stock_actual"] < r["punto_reorden"]
        item = {
            "sku": r["sku"],
            "name": r["nombre"],
            "category": CATEGORY.get(r["categoria"], r["categoria"]),
            "stock": r["stock_actual"],
            "reorder_point": r["punto_reorden"],
            "below_reorder_point": below,
            "location": r["ubicacion"],
        }
        if below:
            item["units_short"] = r["punto_reorden"] - r["stock_actual"]
        items.append(item)
    return {
        "as_of": rows[0]["actualizado"] if rows else None,
        "count": len(items),
        "items": items,
    }


# -------------------------------------------------------------------- sales

GroupBy = Literal["month", "quarter", "sku", "category", "channel", "collection", "customer"]

_QUARTER = "strftime('%Y', p.fecha) || '-Q' || ((CAST(strftime('%m', p.fecha) AS INTEGER) + 2) / 3)"
# group_by -> (SQL for the group, SQL for its name or NULL)
_GROUPS: dict[str, tuple[str, str]] = {
    "month": ("strftime('%Y-%m', p.fecha)", "NULL"),
    "quarter": (_QUARTER, "NULL"),
    "sku": ("d.sku", "pr.nombre"),
    "category": ("pr.categoria", "NULL"),
    "channel": ("p.canal", "NULL"),
    "collection": ("pr.coleccion", "NULL"),
    "customer": ("c.cliente_id", "c.nombre"),
}
_GROUP_LABELS = {"category": CATEGORY, "channel": CHANNEL, "collection": COLLECTION}

# A line's MXN is its share of the order's stored MXN total, so every grouping adds
# up to the books exactly (the meeting notes' figures come from `pedidos.total_mxn`).
_SALES_FROM = """
    COUNT(DISTINCT p.pedido_id) AS orders,
    SUM(d.cantidad) AS units,
    SUM(d.importe_usd) AS usd,
    SUM(d.importe_usd * p.total_mxn / p.total_usd) AS mxn
FROM pedido_detalle d
JOIN pedidos p ON p.pedido_id = d.pedido_id
JOIN productos pr ON pr.sku = d.sku
JOIN clientes c ON c.cliente_id = p.cliente_id
WHERE p.fecha BETWEEN ? AND ?
"""


class SalesReportArgs(ToolArgs):
    date_from: date = Field(description="First day, YYYY-MM-DD (inclusive)")
    date_to: date = Field(description="Last day, YYYY-MM-DD (inclusive)")
    group_by: GroupBy = Field(description="How to split the sales")
    top_n: int | None = Field(None, ge=1, le=100, description="Keep only the N largest groups")
    order_by: Literal["revenue", "units"] | None = Field(
        None,
        description="Rank groups by revenue or by units. Default: months and quarters in "
        "date order, everything else by revenue",
    )

    @model_validator(mode="after")
    def _range(self) -> "SalesReportArgs":
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


def sales_report(args: SalesReportArgs, ctx: ToolContext) -> dict[str, Any]:
    key_sql, name_sql = _GROUPS[args.group_by]
    type_sql = "c.tipo" if args.group_by == "customer" else "NULL"
    period = (args.date_from.isoformat(), args.date_to.isoformat())
    with _db(ctx) as cx:
        grouped = cx.execute(
            f"SELECT {key_sql} AS grp, {name_sql} AS name, {type_sql} AS customer_type, "
            f"{_SALES_FROM} GROUP BY grp",
            period,
        ).fetchall()
        total = cx.execute(f"SELECT {_SALES_FROM}", period).fetchone()
        first, last = cx.execute("SELECT MIN(fecha), MAX(fecha) FROM pedidos").fetchone()

    total_mxn = total["mxn"] or 0.0
    rows = [_sales_row(args.group_by, r, total_mxn) for r in grouped]
    order_by = args.order_by
    if order_by is None and (args.group_by not in {"month", "quarter"} or args.top_n):
        order_by = "revenue"
    if order_by == "units":
        rows.sort(key=lambda row: (-row["units"], row["group"]))
    elif order_by == "revenue":
        rows.sort(key=lambda row: (-row["revenue_mxn"], str(row["group"])))
    else:
        rows.sort(key=lambda row: row["group"])
    groups_total = len(rows)
    if args.top_n:
        rows = rows[: args.top_n]

    notes = []
    if not grouped:
        notes.append(f"No orders in this range. The data covers {first} to {last}.")
    elif period[0] < first or period[1] > last:
        notes.append(f"The data covers {first} to {last} only.")
    return {
        "date_from": period[0],
        "date_to": period[1],
        "group_by": args.group_by,
        "order_by": order_by or "date",
        "groups_total": groups_total,
        "rows": rows,
        "totals": {
            "orders": total["orders"],
            "units": total["units"] or 0,
            "revenue_usd": _money(total["usd"]),
            "revenue_mxn": _money(total_mxn),
        },
        "notes": notes,
    }


def _sales_row(group_by: str, r: sqlite3.Row, total_mxn: float) -> dict[str, Any]:
    group = r["grp"]
    row: dict[str, Any] = {"group": _GROUP_LABELS.get(group_by, {}).get(group, group)}
    if group_by == "sku":
        row["name"] = r["name"]
    elif group_by == "customer":
        row = {
            "group": r["name"],
            "customer_id": group,
            "customer_type": CUSTOMER_TYPE.get(r["customer_type"], r["customer_type"]),
        }
    row.update(
        orders=r["orders"],
        units=r["units"],
        revenue_usd=_money(r["usd"]),
        revenue_mxn=_money(r["mxn"]),
        share_of_revenue_pct=round(100 * r["mxn"] / total_mxn, 1) if total_mxn else 0.0,
    )
    return row


# ---------------------------------------------------------------- purchases

_STATUS_FILTER = {
    "received": ("Recibida", "Recibida con retraso"),
    "received_late": ("Recibida con retraso",),
    "in_transit": ("En tránsito",),
}


class SupplierPurchasesArgs(ToolArgs):
    supplier: str | None = Field(
        None,
        description="Part of the supplier's name or its contract number (e.g. XA-2026-014). "
        "Leave out for all suppliers.",
    )
    status: Literal["received", "received_late", "in_transit"] | None = Field(
        None, description="received includes the ones received late"
    )


def _fold(text: str) -> str:
    """Lowercase without accents, so 'fundicion' finds 'Fundición'."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def supplier_purchases(args: SupplierPurchasesArgs, ctx: ToolContext) -> dict[str, Any]:
    with _db(ctx) as cx:
        suppliers = cx.execute(
            "SELECT proveedor_id, nombre, numero_contrato FROM proveedores ORDER BY proveedor_id"
        ).fetchall()
        if args.supplier:
            needle = _fold(args.supplier)
            suppliers = [
                s
                for s in suppliers
                if needle in _fold(s["nombre"]) or needle in _fold(s["numero_contrato"] or "")
            ]
            if not suppliers:
                names = [r[0] for r in cx.execute("SELECT nombre FROM proveedores ORDER BY 1")]
                raise ToolError(
                    f"No supplier matches {args.supplier!r}. Suppliers: {'; '.join(names)}"
                )
        ids = [s["proveedor_id"] for s in suppliers]
        statuses = _STATUS_FILTER[args.status] if args.status else tuple(PURCHASE_STATUS)
        rows = cx.execute(
            "SELECT cp.compra_id, cp.fecha, pv.nombre, pv.numero_contrato, cp.articulo,"
            " cp.cantidad, cp.unidad, cp.precio_unitario_mxn, cp.total_mxn, cp.estado"
            " FROM compras_proveedor cp JOIN proveedores pv ON pv.proveedor_id = cp.proveedor_id"
            f" WHERE cp.proveedor_id IN ({','.join('?' * len(ids))})"
            f" AND cp.estado IN ({','.join('?' * len(statuses))})"
            " ORDER BY cp.fecha, cp.compra_id",
            [*ids, *statuses],
        ).fetchall()

    purchases = [
        {
            "purchase_id": r["compra_id"],
            "date": r["fecha"],
            "supplier": r["nombre"],
            "contract": r["numero_contrato"],
            "item": r["articulo"],
            "quantity": r["cantidad"],
            "unit": r["unidad"],
            "unit_price_mxn": _money(r["precio_unitario_mxn"]),
            "total_mxn": _money(r["total_mxn"]),
            "status": PURCHASE_STATUS.get(r["estado"], r["estado"]),
        }
        for r in rows
    ]
    by_supplier: dict[str, dict[str, Any]] = {}
    for p in purchases:
        entry = by_supplier.setdefault(p["supplier"], {"purchases": 0, "total_mxn": 0.0})
        entry["purchases"] += 1
        entry["total_mxn"] = _money(entry["total_mxn"] + p["total_mxn"])
    return {
        "count": len(purchases),
        "purchases": purchases,
        "totals": {
            "total_mxn": _money(sum(p["total_mxn"] for p in purchases)),
            "by_supplier": [{"supplier": name, **v} for name, v in by_supplier.items()],
        },
    }


# --------------------------------------------------------------- sql_select

SCHEMA_SUMMARY = """\
SQLite, tables and columns in Spanish. Dates are TEXT 'YYYY-MM-DD'.
- productos(sku, nombre, categoria ['Cuenta de vidrio','Dije','Bundle'], atributo, \
precio_mayoreo_usd, precio_menudeo_usd, coleccion ['Permanente','Coastal Garden','Amber Hollow'])
- inventario(sku, stock_actual, punto_reorden, ubicacion, actualizado)
- clientes(cliente_id, nombre, tipo ['Hobbyist','Boutique','Distribuidor'], ciudad, estado, \
pais, fecha_alta)
- pedidos(pedido_id, cliente_id, fecha, canal ['Mayoreo','Menudeo'], estado, total_usd, \
tipo_cambio, total_mxn)
- pedido_detalle(detalle_id, pedido_id, sku, cantidad, precio_unitario_usd, importe_usd)
- proveedores(proveedor_id, nombre, ciudad, estado, pais, contacto, terminos_pago, \
numero_contrato)
- compras_proveedor(compra_id, proveedor_id, fecha, articulo, unidad, cantidad, \
precio_unitario_mxn, total_mxn, estado ['Recibida','Recibida con retraso','En tránsito'])
- empleados(empleado_id, nombre, departamento, puesto)"""


class SqlSelectArgs(ToolArgs):
    query: str = Field(description="One SQLite SELECT (or WITH ... SELECT) statement")


def sql_select(args: SqlSelectArgs, ctx: ToolContext) -> dict[str, Any]:
    query = check_select(args.query)
    with _db(ctx) as cx:
        return run_select(cx, query)


def run_select(
    cx: sqlite3.Connection,
    query: str,
    *,
    max_rows: int = SQL_MAX_ROWS,
    timeout_s: float = SQL_TIMEOUT_S,
) -> dict[str, Any]:
    """Runs a checked query with a row cap and a time limit; SQLite errors become a
    `ToolError` the model can read (a wrong column name, say) and fix."""
    deadline = time.monotonic() + timeout_s
    cx.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    try:
        cursor = cx.execute(query)
        fetched = cursor.fetchmany(max_rows + 1)
    except sqlite3.OperationalError as exc:
        if str(exc) == "interrupted":
            raise ToolError(f"The query took longer than {timeout_s:g} s and was stopped") from exc
        raise ToolError(f"SQL error: {exc}") from exc
    except (sqlite3.DatabaseError, sqlite3.ProgrammingError, sqlite3.Warning) as exc:
        raise ToolError(f"SQL error: {exc}") from exc
    finally:
        cx.set_progress_handler(None, 0)

    columns = [column[0] for column in cursor.description or ()]
    rows = [[_cell(value) for value in row] for row in fetched[:max_rows]]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": len(fetched) > max_rows,
    }


def _cell(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, str) and len(value) > SQL_MAX_CELL_CHARS:
        return value[:SQL_MAX_CELL_CHARS] + "…"
    return value


# ------------------------------------------------------------- declarations


def business_tools() -> list[Tool]:
    return [
        Tool(
            name="check_inventory",
            description=(
                "Current stock of the products: units in stock, reorder point and "
                "warehouse location. With no arguments, every product; with sku, one "
                "product; with only_below_reorder=true, the products that need restocking."
            ),
            args=CheckInventoryArgs,
            handler=check_inventory,
        ),
        Tool(
            name="sales_report",
            description=(
                "Sales between two dates (inclusive), grouped by month, quarter, sku, "
                "category, channel (wholesale/retail), collection or customer. Returns "
                "orders, units and revenue in USD and MXN per group, each group's share of "
                "revenue, and the totals. Quarters: Q1 = Jan-Mar, Q2 = Apr-Jun, "
                "Q3 = Jul-Sep, Q4 = Oct-Dec."
            ),
            args=SalesReportArgs,
            handler=sales_report,
        ),
        Tool(
            name="supplier_purchases",
            description=(
                "Purchase orders placed with suppliers: date, item, quantity, price, total "
                "in MXN and status (received, received late, in transit), with totals per "
                "supplier. Filter by supplier name or contract number, and by status."
            ),
            args=SupplierPurchasesArgs,
            handler=supplier_purchases,
        ),
        Tool(
            name="sql_select",
            description=(
                "Run one read-only SQL SELECT on the business database and get up to "
                f"{SQL_MAX_ROWS} rows. Use it only when check_inventory, sales_report and "
                "supplier_purchases can't answer the question.\n" + SCHEMA_SUMMARY
            ),
            args=SqlSelectArgs,
            handler=sql_select,
        ),
    ]
