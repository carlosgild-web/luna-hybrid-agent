# hilda_luna.db — Schema, suggested queries and tools for Ollama

*English · [Español](CONSULTAS_Y_TOOLS.md)*

> **A design record, not the current API.** These are the notes that came with the
> database generator, written before the agent existed. The tools in section 3 are the
> original proposals. What Phase 2 actually built differs (see
> [decision 0034](../decisions/0034-tool-registry-and-business-tools.md)):
>
> | Proposed here | Built | What changed |
> |---|---|---|
> | `consultar_inventario` | `check_inventory` | English arguments: `sku`, `only_below_reorder` |
> | `reporte_ventas` | `sales_report` | `date_from`, `date_to`, `group_by`, `top_n`, plus `order_by`; English group names |
> | `consultar_compras_proveedor` | `supplier_purchases` | Filters by supplier name or contract number; status `received`, `received_late`, `in_transit` |
> | `sql_libre` | `sql_select` | Read-only in three layers (decision 0033), row cap and timeout |
> | `generar_grafica` (matplotlib PNG) | `make_chart` | Returns a chart spec that the chat renders with recharts (decision 0038) |
>
> The typed tools return English keys and translate coded values (`Mayoreo` becomes
> `wholesale`). The database itself keeps the Spanish table and column names shown below.

A test SQLite database for practising **function calling with Ollama** on the
fictional company **Hilda & Luna Beadworks** (the same company as the RAG documents,
so SQL answers can be cross-checked against the documents).

Built with `python -m app.cli seed-business-db` (code in `backend/app/agent/business_seed.py`, fixed seed 2026, reproducible; see decision 0033). It is created at `DATA_DIR/business/hilda_luna.db`.
Fixed exchange rate: **18.50 MXN/USD**. Product prices are in USD (like the
catalog); order totals are in USD and MXN.

## 1. Schema (8 tables)

| Table | Rows | Contents | Source |
|---|---|---|---|
| `productos` (products) | 13 | 10 catalog SKUs + 3 bundles, wholesale/retail prices in USD | 01-product-catalog.pdf, 10-wholesale-pricing-sheet.md (faithful) |
| `inventario` (inventory) | 13 | stock_actual, punto_reorden, ubicacion (Bay 1/2/3) | Fictional, consistent with the Q3 notes (Bay 2 = Amber Hollow) |
| `clientes` (customers) | 68 | 8 wholesale accounts (3 real ones from the notes) + 60 hobbyists | Q3 notes + fictional |
| `pedidos` (orders) | 937 | Jan–14 Nov 2026, channel Mayoreo/Menudeo (wholesale/retail), totals in USD and MXN | Calibrated fiction |
| `pedido_detalle` (order lines) | 1,867 | Lines per SKU with quantity and price | Calibrated fiction |
| `proveedores` (suppliers) | 3 | Cristal Dorado Vidrios (contract XA-2026-014) + 2 more | 02-supplier-agreement.docx |
| `compras_proveedor` (supplier purchases) | 11 | 2026 POs at the contract prices ($285/kg borosilicate, etc.) | 02-supplier-agreement.docx |
| `empleados` (employees) | 5 | The 5 attendees of the Q3 meeting | 07-q3-2026-sales-meeting-notes.txt |

Column glossary: `stock_actual` = units in stock, `punto_reorden` = reorder point,
`ubicacion` = location, `nombre` = name, `tipo` = type, `fecha` = date, `canal` =
channel, `cantidad` = quantity, `importe_usd` = line amount in USD, `articulo` = item,
`unidad` = unit, `estado` = status (or state, in addresses).

### Figures calibrated against the Q3 2026 meeting notes

| Metric | Meeting notes | Database |
|---|---|---|
| Q3 revenue | $512,800 MXN | $519,726 MXN (+1.3%) |
| Wholesale % of revenue | 61% | 59.8% |
| CH-2004 units in Q3 | 4,120 | 4,120 (exact) |
| GB-1014 units in Q3 | 3,340 | 3,340 (exact) |
| GB-1027 vs Q2 | −22% | −22% (1,800 → 1,404) |
| Largest customer | Abalone Supply Co. | Abalone Supply Co. ✓ |

This makes it possible to test hybrid questions such as: *"Do the CH-2004 sales in
the database match what was reported in the Q3 meeting?"*

## 2. Suggested query types (by tool)

### Inventory
1. How much stock is there of SKU CH-2004, and where is it?
2. Which products are below their reorder point? *(there are 3: GB-1014, CH-2004, BN-9001)*
3. Total stock by category / by warehouse bay.

### Sales and reports
4. Total sales (MXN/USD) for a date range, grouped by month/quarter.
5. Top N products by units or by revenue in a period.
6. Q2 vs Q3 by SKU (what dropped? → GB-1027).
7. Wholesale vs retail mix by quarter.
8. Sales by collection (Coastal Garden vs Amber Hollow vs Permanente, the permanent line).

### Customers
9. Top wholesale customers by revenue; orders of a specific customer.
10. New customers per quarter *(Thistle & Thread and Casa de Cuentas in Q3)*.
11. Average order value by customer type.

### Purchasing / suppliers
12. Total bought from Cristal Dorado Vidrios in 2026; POs in transit.
13. Spend per supplier per quarter.

### Charts (a script generates PNGs with matplotlib)
14. Line: monthly sales 2026. Bars: top 5 SKUs of the quarter.
    Pie: channel mix. Bars: stock vs reorder point.

## 3. Tools for Ollama (function calling)

Recommended design: **a few parameterised tools** (not one per question).

```python
TOOLS = [
  {
    "type": "function",
    "function": {
      "name": "consultar_inventario",
      "description": "Looks up product stock. With no arguments returns the whole inventory; with sku filters to one; with solo_bajo_reorden=true returns only the products that need restocking.",
      "parameters": {
        "type": "object",
        "properties": {
          "sku": {"type": "string", "description": "Exact SKU, e.g. CH-2004"},
          "solo_bajo_reorden": {"type": "boolean"}
        }
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "reporte_ventas",
      "description": "Sales between two dates (YYYY-MM-DD), grouped by month, quarter, sku, category, channel, collection or customer. Returns units and amounts in USD and MXN.",
      "parameters": {
        "type": "object",
        "properties": {
          "fecha_inicio": {"type": "string"},
          "fecha_fin": {"type": "string"},
          "agrupar_por": {"type": "string",
            "enum": ["mes", "trimestre", "sku", "categoria", "canal", "coleccion", "cliente"]},
          "top_n": {"type": "integer", "description": "Limit to the N largest"}
        },
        "required": ["fecha_inicio", "fecha_fin", "agrupar_por"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "generar_grafica",
      "description": "Generates a PNG chart of sales or inventory and returns the file path.",
      "parameters": {
        "type": "object",
        "properties": {
          "tipo": {"type": "string", "enum": ["linea", "barras", "pastel"]},
          "metrica": {"type": "string",
            "enum": ["ventas_mensuales", "top_productos", "mezcla_canal", "stock_vs_reorden"]},
          "fecha_inicio": {"type": "string"},
          "fecha_fin": {"type": "string"}
        },
        "required": ["tipo", "metrica"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "consultar_compras_proveedor",
      "description": "Supplier purchases: filter by supplier name and/or status (Recibida = received, En tránsito = in transit).",
      "parameters": {
        "type": "object",
        "properties": {
          "proveedor": {"type": "string"},
          "estado": {"type": "string"}
        }
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "sql_libre",
      "description": "Runs an arbitrary SELECT on the database (read-only). Use it only if no other tool covers the question.",
      "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"]
      }
    }
  }
]
```

Argument glossary: `solo_bajo_reorden` = only below reorder point, `fecha_inicio` /
`fecha_fin` = start / end date, `agrupar_por` = group by (`mes` month, `trimestre`
quarter, `categoria` category, `canal` channel, `coleccion` collection, `cliente`
customer), `tipo` = chart type (`linea` line, `barras` bar, `pastel` pie), `metrica` =
metric, `proveedor` = supplier, `estado` = status.

**Safety for `sql_libre`** (essential if you expose it):

```python
import re, sqlite3

def sql_libre(query: str):
    q = query.strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|with)\b", q) or ";" in q:
        return {"error": "Only SELECT queries are allowed"}
    cx = sqlite3.connect("file:hilda_luna.db?mode=ro", uri=True)  # read-only
    cx.execute("PRAGMA query_only = ON")
    try:
        cur = cx.execute(q)
        cols = [c[0] for c in cur.description]
        return {"columns": cols, "rows": cur.fetchmany(200)}
    finally:
        cx.close()
```

## 4. Reference SQL for each tool

```sql
-- Products below their reorder point
SELECT i.sku, p.nombre, i.stock_actual, i.punto_reorden, i.ubicacion
FROM inventario i JOIN productos p USING(sku)
WHERE i.stock_actual < i.punto_reorden;

-- Sales by quarter and channel
SELECT strftime('%Y', fecha) || '-Q' || ((CAST(strftime('%m', fecha) AS INT)+2)/3) AS trimestre,
       canal, COUNT(*) pedidos, ROUND(SUM(total_mxn),2) total_mxn
FROM pedidos GROUP BY trimestre, canal ORDER BY trimestre;

-- Top 5 SKUs by units in Q3 2026
SELECT d.sku, pr.nombre, SUM(d.cantidad) unidades, ROUND(SUM(d.importe_usd),2) usd
FROM pedido_detalle d
JOIN pedidos p USING(pedido_id)
JOIN productos pr ON pr.sku = d.sku
WHERE p.fecha BETWEEN '2026-07-01' AND '2026-09-30'
GROUP BY d.sku ORDER BY unidades DESC LIMIT 5;

-- GB-1027 drop: Q2 vs Q3
SELECT CASE WHEN p.fecha < '2026-07-01' THEN 'Q2' ELSE 'Q3' END trim,
       SUM(d.cantidad) unidades
FROM pedido_detalle d JOIN pedidos p USING(pedido_id)
WHERE d.sku='GB-1027' AND p.fecha BETWEEN '2026-04-01' AND '2026-09-30'
GROUP BY trim;

-- Top wholesale customers
SELECT c.nombre, c.tipo, COUNT(*) pedidos, ROUND(SUM(o.total_mxn),2) total_mxn
FROM pedidos o JOIN clientes c USING(cliente_id)
WHERE c.tipo != 'Hobbyist'
GROUP BY c.cliente_id ORDER BY total_mxn DESC;

-- Purchases from Cristal Dorado in 2026
SELECT fecha, articulo, cantidad, unidad, total_mxn, estado
FROM compras_proveedor cp JOIN proveedores pv USING(proveedor_id)
WHERE pv.nombre LIKE 'Cristal Dorado%' ORDER BY fecha;
```

## 5. Test questions for the agent

Easy (one tool):
- "How much stock is left of the Crescent Moon & Star charm?"
- "Give me the October 2026 sales in pesos."
- "What do I need to restock?"

Intermediate (tool + reasoning):
- "What was the best-selling product in Q3, and how much revenue did it bring in?"
- "Compare wholesale vs retail sales by quarter."
- "Chart the monthly sales for the year."

Hard (several tools / cross-check with RAG):
- "Do the CH-2004 sales in the database match what was said in the Q3 meeting?"
- "How much have we bought from the supplier on contract XA-2026-014, and what do we have in transit?"
- "Terracotta Sunrise dropped in Q3. By exactly how much, and what does the meeting say about the cause?"
