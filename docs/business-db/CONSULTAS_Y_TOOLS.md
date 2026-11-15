# hilda_luna.db — Esquema, consultas sugeridas y tools para Ollama

Base de datos SQLite de prueba para practicar **function calling con Ollama**
sobre la empresa ficticia **Hilda & Luna Beadworks** (misma empresa de los
documentos del sistema RAG, para poder cruzar respuestas SQL ↔ documentos).

Generada con `python -m app.cli seed-business-db` (código en `backend/app/agent/business_seed.py`, semilla fija 2026, reproducible; ver decisión 0033). Se crea en `DATA_DIR/business/hilda_luna.db`.
Tipo de cambio fijo: **18.50 MXN/USD**. Precios de productos en USD (como el
catálogo); totales de pedidos en USD y MXN.

## 1. Esquema (8 tablas)

| Tabla | Filas | Contenido | Fuente |
|---|---|---|---|
| `productos` | 13 | 10 SKUs del catálogo + 3 bundles, precios mayoreo/menudeo USD | 01-product-catalog.pdf, 10-wholesale-pricing-sheet.md (fiel) |
| `inventario` | 13 | stock_actual, punto_reorden, ubicación (Bay 1/2/3) | Ficticio, coherente con notas Q3 (Bay 2 = Amber Hollow) |
| `clientes` | 68 | 8 mayoristas (3 reales de las notas) + 60 hobbyist | Notas Q3 + ficticio |
| `pedidos` | 937 | Ene–14 Nov 2026, canal Mayoreo/Menudeo, totales USD y MXN | Ficticio calibrado |
| `pedido_detalle` | 1,867 | Líneas por SKU con cantidad y precio | Ficticio calibrado |
| `proveedores` | 3 | Cristal Dorado Vidrios (contrato XA-2026-014) + 2 más | 02-supplier-agreement.docx |
| `compras_proveedor` | 11 | POs 2026 con precios del contrato ($285/kg boro, etc.) | 02-supplier-agreement.docx |
| `empleados` | 5 | Los 5 asistentes de la junta Q3 | 07-q3-2026-sales-meeting-notes.txt |

### Cifras calibradas contra las notas de la junta Q3 2026

| Métrica | Notas de junta | Base de datos |
|---|---|---|
| Revenue Q3 | $512,800 MXN | $519,726 MXN (+1.3%) |
| Mayoreo % del revenue | 61% | 59.8% |
| CH-2004 unidades Q3 | 4,120 | 4,120 (exacto) |
| GB-1014 unidades Q3 | 3,340 | 3,340 (exacto) |
| GB-1027 vs Q2 | −22% | −22% (1,800 → 1,404) |
| Cliente más grande | Abalone Supply Co. | Abalone Supply Co. ✓ |

Esto permite probar preguntas híbridas tipo: *"¿coinciden las ventas de
CH-2004 en la base con lo reportado en la junta Q3?"*

## 2. Tipos de consultas sugeridas (por tool)

### Inventario
1. ¿Cuánto stock hay del SKU CH-2004 y dónde está ubicado?
2. ¿Qué productos están por debajo de su punto de reorden? *(hay 3: GB-1014, CH-2004, BN-9001)*
3. Stock total por categoría / por bahía del almacén.

### Ventas y reportes
4. Ventas totales (MXN/USD) de un rango de fechas, agrupadas por mes/trimestre.
5. Top N productos por unidades o por importe en un periodo.
6. Comparativa Q2 vs Q3 por SKU (¿qué cayó? → GB-1027).
7. Mezcla mayoreo vs menudeo por trimestre.
8. Ventas por colección (Coastal Garden vs Amber Hollow vs Permanente).

### Clientes
9. Top clientes mayoristas por facturación; pedidos de un cliente específico.
10. Clientes nuevos por trimestre *(Thistle & Thread y Casa de Cuentas en Q3)*.
11. Ticket promedio por tipo de cliente.

### Compras / proveedores
12. Total comprado a Cristal Dorado Vidrios en 2026; POs en tránsito.
13. Gasto por proveedor por trimestre.

### Gráficas (script genera PNG con matplotlib)
14. Línea: ventas mensuales 2026. Barras: top 5 SKUs del trimestre.
    Pastel: mezcla de canal. Barras: stock vs punto de reorden.

## 3. Tools para Ollama (function calling)

Diseño recomendado: **pocas tools, parametrizadas** (no una por pregunta).

```python
TOOLS = [
  {
    "type": "function",
    "function": {
      "name": "consultar_inventario",
      "description": "Consulta stock de productos. Sin argumentos regresa todo el inventario; con sku filtra uno; con solo_bajo_reorden=true regresa solo productos que requieren resurtido.",
      "parameters": {
        "type": "object",
        "properties": {
          "sku": {"type": "string", "description": "SKU exacto, ej. CH-2004"},
          "solo_bajo_reorden": {"type": "boolean"}
        }
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "reporte_ventas",
      "description": "Ventas entre dos fechas (YYYY-MM-DD), agrupadas por mes, trimestre, sku, categoria, canal, coleccion o cliente. Regresa unidades e importes en USD y MXN.",
      "parameters": {
        "type": "object",
        "properties": {
          "fecha_inicio": {"type": "string"},
          "fecha_fin": {"type": "string"},
          "agrupar_por": {"type": "string",
            "enum": ["mes", "trimestre", "sku", "categoria", "canal", "coleccion", "cliente"]},
          "top_n": {"type": "integer", "description": "Limitar a los N mayores"}
        },
        "required": ["fecha_inicio", "fecha_fin", "agrupar_por"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "generar_grafica",
      "description": "Genera una gráfica PNG de ventas o inventario y regresa la ruta del archivo.",
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
      "description": "Compras a proveedores: filtra por nombre de proveedor y/o estado (Recibida, En tránsito).",
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
      "description": "Ejecuta un SELECT arbitrario sobre la base (solo lectura). Úsala solo si ninguna otra tool cubre la pregunta.",
      "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"]
      }
    }
  }
]
```

**Seguridad de `sql_libre`** (imprescindible si la expones):

```python
import re, sqlite3

def sql_libre(query: str):
    q = query.strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|with)\b", q) or ";" in q:
        return {"error": "Solo se permiten consultas SELECT"}
    cx = sqlite3.connect("file:hilda_luna.db?mode=ro", uri=True)  # solo lectura
    cx.execute("PRAGMA query_only = ON")
    try:
        cur = cx.execute(q)
        cols = [c[0] for c in cur.description]
        return {"columnas": cols, "filas": cur.fetchmany(200)}
    finally:
        cx.close()
```

## 4. SQL de referencia para cada tool

```sql
-- Productos bajo punto de reorden
SELECT i.sku, p.nombre, i.stock_actual, i.punto_reorden, i.ubicacion
FROM inventario i JOIN productos p USING(sku)
WHERE i.stock_actual < i.punto_reorden;

-- Ventas por trimestre y canal
SELECT strftime('%Y', fecha) || '-Q' || ((CAST(strftime('%m', fecha) AS INT)+2)/3) AS trimestre,
       canal, COUNT(*) pedidos, ROUND(SUM(total_mxn),2) total_mxn
FROM pedidos GROUP BY trimestre, canal ORDER BY trimestre;

-- Top 5 SKUs por unidades en Q3 2026
SELECT d.sku, pr.nombre, SUM(d.cantidad) unidades, ROUND(SUM(d.importe_usd),2) usd
FROM pedido_detalle d
JOIN pedidos p USING(pedido_id)
JOIN productos pr ON pr.sku = d.sku
WHERE p.fecha BETWEEN '2026-07-01' AND '2026-09-30'
GROUP BY d.sku ORDER BY unidades DESC LIMIT 5;

-- Caída de GB-1027: Q2 vs Q3
SELECT CASE WHEN p.fecha < '2026-07-01' THEN 'Q2' ELSE 'Q3' END trim,
       SUM(d.cantidad) unidades
FROM pedido_detalle d JOIN pedidos p USING(pedido_id)
WHERE d.sku='GB-1027' AND p.fecha BETWEEN '2026-04-01' AND '2026-09-30'
GROUP BY trim;

-- Top clientes mayoristas
SELECT c.nombre, c.tipo, COUNT(*) pedidos, ROUND(SUM(o.total_mxn),2) total_mxn
FROM pedidos o JOIN clientes c USING(cliente_id)
WHERE c.tipo != 'Hobbyist'
GROUP BY c.cliente_id ORDER BY total_mxn DESC;

-- Compras a Cristal Dorado en 2026
SELECT fecha, articulo, cantidad, unidad, total_mxn, estado
FROM compras_proveedor cp JOIN proveedores pv USING(proveedor_id)
WHERE pv.nombre LIKE 'Cristal Dorado%' ORDER BY fecha;
```

## 5. Preguntas de prueba para el agente

Fáciles (una tool):
- "¿Cuánto stock queda del dije Crescent Moon & Star?"
- "Dame las ventas de octubre 2026 en pesos."
- "¿Qué necesito resurtir?"

Intermedias (tool + razonamiento):
- "¿Cuál fue el producto más vendido del Q3 y cuánto facturó?"
- "Compárame las ventas de mayoreo vs menudeo por trimestre."
- "Grafícame las ventas mensuales del año."

Difíciles (varias tools / cruce con RAG):
- "¿Las ventas de CH-2004 en la base coinciden con lo que se dijo en la junta Q3?"
- "¿Cuánto le hemos comprado al proveedor del contrato XA-2026-014 y qué tenemos en tránsito?"
- "Terracotta Sunrise bajó en Q3, ¿cuánto exactamente y qué dice la junta sobre la causa?"
