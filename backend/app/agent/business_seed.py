"""Generates the demo business database, `hilda_luna.db` (decision 0033).

Hilda & Luna Beadworks is the fictional company of the demo documents. This
database holds its catalog, inventory, customers, orders and supplier purchases
for 2026, so the agent can answer with SQL what the documents only mention.

The base data is taken from the demo documents in `demo/hilda-luna/`:

- `01-product-catalog.pdf`: products (SKUs, USD prices)
- `10-wholesale-pricing-sheet.md`: bundles and wholesale tiers
- `02-supplier-agreement.docx`: suppliers and their purchases
- `07-q3-2026-sales-meeting-notes.txt`: employees, wholesale customers and the
  Q3 sales figures (CH-2004: 4,120 units; GB-1014: 3,340 units; Q3 revenue about
  $512,800 MXN; wholesale about 61% of revenue; GB-1027 down 22% from Q2)

The orders are made up, but generated to agree with those figures, so SQL
answers can be checked against the documents. A fixed seed makes every run
produce the same database.

The schema and the data are in Spanish, like the company. The tools translate
names for the model (PLAN_HYBRID.md section 4).
"""

import os
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

SEED = 2026
TIPO_CAMBIO = 18.50  # MXN per USD, fixed for the demo; also the fx_rate tool's stored rate

Q3_START, Q3_END = "2026-07-01", "2026-09-30"

# ---------------------------------------------------------------- productos
# (sku, nombre, categoria, atributo, precio_mayoreo_usd, precio_menudeo_usd, coleccion)
PRODUCTOS = [
    ("GB-1001", "Midnight Lagoon Round", "Cuenta de vidrio", "8mm", 0.38, 0.75, "Permanente"),
    ("GB-1002", "Desert Sand Faceted", "Cuenta de vidrio", "10mm", 0.52, 1.00, "Permanente"),
    ("GB-1014", "Opal Mist Swirl", "Cuenta de vidrio", "12mm", 0.89, 1.60, "Permanente"),
    ("GB-1027", "Terracotta Sunrise", "Cuenta de vidrio", "8mm", 0.41, 0.80, "Permanente"),
    ("GB-1033", "Deep Forest Lentil", "Cuenta de vidrio", "14mm", 1.05, 1.95, "Permanente"),
    ("CH-2004", "Crescent Moon & Star", "Dije", "Gold plate", 0.95, 1.85, "Permanente"),
    ("CH-2011", "Tiny Honeybee", "Dije", "Antique bronze", 0.80, 1.50, "Permanente"),
    ("CH-2019", "Birth Flower — Lotus", "Dije", "Rhodium", 1.10, 2.10, "Permanente"),
    ("CH-2026", "Desert Cactus", "Dije", "Gold plate", 0.90, 1.75, "Permanente"),
    ("CH-2033", "Script Initial (A-Z)", "Dije", "Gold plate", 0.70, 1.40, "Permanente"),
    # Bundles from the wholesale pricing sheet (wholesale only; boutique price)
    (
        "BN-9001",
        "Coastal Garden Starter Set",
        "Bundle",
        "200 cuentas + 50 dijes",
        142.00,
        None,
        "Coastal Garden",
    ),
    (
        "BN-9002",
        "Amber Hollow Starter Set",
        "Bundle",
        "200 cuentas + 50 dijes",
        148.00,
        None,
        "Amber Hollow",
    ),
    (
        "BN-9003",
        "Findings Essentials Kit",
        "Bundle",
        "500 argollas + 100 broches + 2 carretes",
        64.00,
        None,
        "Permanente",
    ),
]
PRECIOS = {p[0]: (p[4], p[5]) for p in PRODUCTOS}
BUNDLES = {"BN-9001", "BN-9002", "BN-9003"}

# ------------------------------------------------------------- inventario
# Agrees with the notes: Bay 2 got new shelving for Amber Hollow; GB-1027 is the
# slowest mover (high stock); CH-2004 is the best seller.
INVENTARIO = [
    # (sku, stock_actual, punto_reorden, ubicacion)
    ("GB-1001", 8200, 3000, "Bay 1 - Rack A"),
    ("GB-1002", 6400, 2500, "Bay 1 - Rack A"),
    ("GB-1014", 2850, 3000, "Bay 1 - Rack B"),  # below reorder point (best seller)
    ("GB-1027", 11600, 2000, "Bay 1 - Rack C"),  # slow mover
    ("GB-1033", 4100, 1500, "Bay 1 - Rack B"),
    ("CH-2004", 1900, 3500, "Bay 3 - Rack A"),  # below reorder point (best seller)
    ("CH-2011", 5300, 2000, "Bay 3 - Rack A"),
    ("CH-2019", 3800, 1200, "Bay 3 - Rack B"),
    ("CH-2026", 4450, 1500, "Bay 3 - Rack B"),
    ("CH-2033", 7200, 2200, "Bay 3 - Rack C"),
    ("BN-9001", 35, 40, "Bay 2 - Estante 1"),  # almost sold out (Q3 notes)
    ("BN-9002", 480, 150, "Bay 2 - Estante 2"),  # launched 2026-10-15
    ("BN-9003", 210, 80, "Bay 2 - Estante 3"),
]

# -------------------------------------------------------------- empleados
EMPLEADOS = [
    (1, "Marisol Treviño", "Compras", "Procurement Lead"),
    (2, "Danna Ríos", "Ventas", "Sales Lead"),
    (3, "Omar Castillo", "Almacén", "Warehouse Manager"),
    (4, "Lucía Fernández", "Diseño", "Design Studio Lead"),
    (5, "Bruno Salcedo", "Finanzas", "Finance Lead"),
]

# ------------------------------------------------------------- proveedores
PROVEEDORES = [
    (
        1,
        "Cristal Dorado Vidrios S.A. de C.V.",
        "Guadalajara",
        "Jalisco",
        "México",
        "Eduardo Paz",
        "Net 30 (2% dcto. a 10 días)",
        "XA-2026-014",
    ),
    (
        2,
        "Fundición Peltre del Bajío",
        "León",
        "Guanajuato",
        "México",
        "Rocío Almanza",
        "Net 30",
        "PB-2026-031",
    ),
    (
        3,
        "Empaques y Cajas Regias",
        "Monterrey",
        "Nuevo León",
        "México",
        "Héctor Garza",
        "Contado",
        "ER-2026-007",
    ),
]

# Supplier purchases (prices from the supplier agreement, in MXN)
COMPRAS = [
    # (proveedor_id, fecha, articulo, unidad, cantidad, precio_unitario_mxn, estado)
    (1, "2026-03-10", "Vara de borosilicato 5mm (clear)", "kg", 40, 285.0, "Recibida"),
    (1, "2026-04-02", "Caña soda-lime colores surtidos", "kg", 60, 190.0, "Recibida"),
    (1, "2026-05-18", "Vara de borosilicato 7mm (12 colores)", "kg", 55, 285.0, "Recibida"),
    (1, "2026-06-25", "Hoja dicroica 90x90mm", "pieza", 40, 340.0, "Recibida"),
    (1, "2026-08-04", "Caña soda-lime (tonos pastel CG)", "kg", 30, 190.0, "Recibida"),
    (1, "2026-09-15", "Vara borosilicato (tonos Amber Hollow)", "kg", 70, 285.0, "Recibida"),
    (
        2,
        "2026-07-20",
        "Fundición dijes bellota (Amber Hollow)",
        "pieza",
        6000,
        6.80,
        "Recibida con retraso",
    ),
    (2, "2026-09-28", "Fundición dijes luna/hongo (Amber Hollow)", "pieza", 8000, 6.40, "Recibida"),
    (3, "2026-02-12", "Cajas kraft 10x10 con logo", "pieza", 2000, 4.20, "Recibida"),
    (3, "2026-08-30", "Cajas kraft 10x10 con logo", "pieza", 3000, 4.05, "Recibida"),
    (1, "2026-10-22", "Caña soda-lime pastel (re-release CG 2027)", "kg", 45, 190.0, "En tránsito"),
]

# --------------------------------------------------------------- clientes
# Wholesale customers: the ones named in the Q3 notes plus made-up ones; retail made up.
CLIENTES_WS = [
    # (nombre, tipo, ciudad, estado/region, pais, fecha_alta, peso)
    ("Abalone Supply Co.", "Distribuidor", "San Diego", "CA", "EE.UU.", "2024-05-12", 30),
    ("Mercería El Hilo", "Distribuidor", "Guadalajara", "Jalisco", "México", "2024-11-03", 18),
    ("Craft Collective MX", "Distribuidor", "CDMX", "CDMX", "México", "2025-02-19", 15),
    ("Perlas del Norte", "Boutique", "Monterrey", "Nuevo León", "México", "2025-06-07", 12),
    ("Luz y Cuarzo", "Boutique", "CDMX", "CDMX", "México", "2025-09-22", 10),
    ("Bead Haven", "Boutique", "Tucson", "AZ", "EE.UU.", "2025-10-30", 8),
    ("Thistle & Thread", "Boutique", "Austin", "TX", "EE.UU.", "2026-07-14", 6),  # new in Q3
    ("Casa de Cuentas", "Boutique", "Querétaro", "Querétaro", "México", "2026-08-05", 5),  # new Q3
]
NOMBRES = [
    "Ana", "Luis", "María", "Jorge", "Sofía", "Pedro", "Lucero", "Iván", "Carmen", "Raúl",
    "Elena", "Diego", "Paola", "Hugo", "Valeria", "Mario", "Fernanda", "Tomás", "Alicia", "Noé",
]  # fmt: skip
APELLIDOS = [
    "García", "Martínez", "López", "Hernández", "Rodríguez", "Pérez", "Sánchez", "Ramírez",
    "Torres", "Flores", "Vega", "Castro", "Ortiz", "Silva", "Mendoza",
]  # fmt: skip
CIUDADES_MX = [
    ("Monterrey", "Nuevo León"),
    ("CDMX", "CDMX"),
    ("Guadalajara", "Jalisco"),
    ("Saltillo", "Coahuila"),
    ("Querétaro", "Querétaro"),
    ("Mérida", "Yucatán"),
    ("Puebla", "Puebla"),
    ("Tijuana", "Baja California"),
]
RETAIL_CUSTOMERS = 60

# ------------------------------------------------- sales targets (units)
# Q3 is calibrated against the meeting notes; the other quarters are derived.
Q3_UNIDADES = {
    "GB-1001": 3000, "GB-1002": 2600, "GB-1014": 3340, "GB-1027": 1404, "GB-1033": 1500,
    "CH-2004": 4120, "CH-2011": 2200, "CH-2019": 1400, "CH-2026": 1600, "CH-2033": 2400,
    "BN-9001": 20, "BN-9003": 30,
}  # fmt: skip
Q2_UNIDADES = {
    sku: (1800 if sku == "GB-1027" else round(u / 1.18)) for sku, u in Q3_UNIDADES.items()
}
Q2_UNIDADES["BN-9001"] = 55  # Coastal Garden strong in Q2
Q1_UNIDADES = {sku: round(u * 0.90) for sku, u in Q2_UNIDADES.items()}
Q1_UNIDADES["BN-9001"] = 70  # Coastal Garden launched in March
# Q4 so far (Oct 1 to Nov 14): Q3 pro rata, with a push from Amber Hollow
Q4_UNIDADES = {sku: round(u * 1.10 * 45 / 92) for sku, u in Q3_UNIDADES.items()}
Q4_UNIDADES["BN-9001"] = 5  # Coastal Garden almost sold out
Q4_UNIDADES["BN-9002"] = 130  # pre-orders + launch on Oct 15

TRIMESTRES = [
    ("Q1", date(2026, 1, 5), date(2026, 3, 31), Q1_UNIDADES),
    ("Q2", date(2026, 4, 1), date(2026, 6, 30), Q2_UNIDADES),
    ("Q3", date(2026, 7, 1), date(2026, 9, 30), Q3_UNIDADES),
    ("Q4", date(2026, 10, 1), date(2026, 11, 14), Q4_UNIDADES),
]

WS_FRACCION_UNIDADES = 0.67  # share of the units sold wholesale

TABLES = (
    "productos",
    "inventario",
    "clientes",
    "pedidos",
    "pedido_detalle",
    "proveedores",
    "compras_proveedor",
    "empleados",
)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE productos (
    sku                TEXT PRIMARY KEY,
    nombre             TEXT NOT NULL,
    categoria          TEXT NOT NULL,
    atributo           TEXT,                -- size or finish
    precio_mayoreo_usd REAL NOT NULL,
    precio_menudeo_usd REAL,                -- NULL: wholesale only (bundles)
    coleccion          TEXT NOT NULL
);

CREATE TABLE inventario (
    sku            TEXT PRIMARY KEY REFERENCES productos(sku),
    stock_actual   INTEGER NOT NULL,
    punto_reorden  INTEGER NOT NULL,
    ubicacion      TEXT NOT NULL,
    actualizado    TEXT NOT NULL DEFAULT '2026-11-14'
);

CREATE TABLE empleados (
    empleado_id  INTEGER PRIMARY KEY,
    nombre       TEXT NOT NULL,
    departamento TEXT NOT NULL,
    puesto       TEXT NOT NULL
);

CREATE TABLE proveedores (
    proveedor_id    INTEGER PRIMARY KEY,
    nombre          TEXT NOT NULL,
    ciudad          TEXT, estado TEXT, pais TEXT,
    contacto        TEXT,
    terminos_pago   TEXT,
    numero_contrato TEXT
);

CREATE TABLE compras_proveedor (
    compra_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id        INTEGER NOT NULL REFERENCES proveedores(proveedor_id),
    fecha               TEXT NOT NULL,
    articulo            TEXT NOT NULL,
    unidad              TEXT NOT NULL,
    cantidad            REAL NOT NULL,
    precio_unitario_mxn REAL NOT NULL,
    total_mxn           REAL NOT NULL,
    estado              TEXT NOT NULL
);

CREATE TABLE clientes (
    cliente_id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre     TEXT NOT NULL,
    tipo       TEXT NOT NULL CHECK (tipo IN ('Hobbyist','Boutique','Distribuidor')),
    ciudad     TEXT, estado TEXT, pais TEXT,
    fecha_alta TEXT NOT NULL
);

CREATE TABLE pedidos (
    pedido_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id  INTEGER NOT NULL REFERENCES clientes(cliente_id),
    fecha       TEXT NOT NULL,
    canal       TEXT NOT NULL CHECK (canal IN ('Mayoreo','Menudeo')),
    estado      TEXT NOT NULL DEFAULT 'Entregado',
    total_usd   REAL NOT NULL,
    tipo_cambio REAL NOT NULL,
    total_mxn   REAL NOT NULL
);

CREATE TABLE pedido_detalle (
    detalle_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pedido_id           INTEGER NOT NULL REFERENCES pedidos(pedido_id),
    sku                 TEXT NOT NULL REFERENCES productos(sku),
    cantidad            INTEGER NOT NULL,
    precio_unitario_usd REAL NOT NULL,
    importe_usd         REAL NOT NULL
);

CREATE INDEX idx_pedidos_fecha    ON pedidos(fecha);
CREATE INDEX idx_pedidos_cliente  ON pedidos(cliente_id);
CREATE INDEX idx_detalle_pedido   ON pedido_detalle(pedido_id);
CREATE INDEX idx_detalle_sku      ON pedido_detalle(sku);
"""


@dataclass
class SeedSummary:
    """What a seed produced, with the figures the Q3 meeting notes report."""

    path: Path
    row_counts: dict[str, int] = field(default_factory=dict)
    q3_revenue_mxn: float = 0.0
    q3_wholesale_pct: float = 0.0
    q3_units: dict[str, int] = field(default_factory=dict)


def seed_business_db(path: Path) -> SeedSummary:
    """Builds the database at `path`, replacing any file already there.

    The new database is written next to the target and then moved over it, so
    a reader never sees a half-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    rng = random.Random(SEED)
    cx = sqlite3.connect(tmp)
    try:
        cx.executescript(SCHEMA)
        _poblar_catalogos(cx)
        ws_ids, rt_ids = _poblar_clientes(cx, rng)
        _generar_pedidos(cx, rng, ws_ids, rt_ids)
        cx.commit()
        summary = _summarize(cx, path)
    finally:
        cx.close()
    os.replace(tmp, path)
    return summary


def _fecha_aleatoria(rng: random.Random, ini: date, fin: date) -> str:
    return (ini + timedelta(days=rng.randint(0, (fin - ini).days))).isoformat()


def _poblar_catalogos(cx: sqlite3.Connection) -> None:
    cx.executemany("INSERT INTO productos VALUES (?,?,?,?,?,?,?)", PRODUCTOS)
    cx.executemany(
        "INSERT INTO inventario (sku, stock_actual, punto_reorden, ubicacion) VALUES (?,?,?,?)",
        INVENTARIO,
    )
    cx.executemany("INSERT INTO empleados VALUES (?,?,?,?)", EMPLEADOS)
    cx.executemany("INSERT INTO proveedores VALUES (?,?,?,?,?,?,?,?)", PROVEEDORES)
    cx.executemany(
        "INSERT INTO compras_proveedor (proveedor_id, fecha, articulo, unidad, cantidad, "
        "precio_unitario_mxn, total_mxn, estado) VALUES (?,?,?,?,?,?,?,?)",
        [(p, f, a, u, c, pu, round(c * pu, 2), e) for p, f, a, u, c, pu, e in COMPRAS],
    )


def _poblar_clientes(cx: sqlite3.Connection, rng: random.Random):
    insert = "INSERT INTO clientes (nombre,tipo,ciudad,estado,pais,fecha_alta) VALUES (?,?,?,?,?,?)"
    ws_ids = []
    for nombre, tipo, ciudad, estado, pais, alta, peso in CLIENTES_WS:
        cur = cx.execute(insert, (nombre, tipo, ciudad, estado, pais, alta))
        ws_ids.append((cur.lastrowid, alta, peso))
    rt_ids = []
    usados = set()
    while len(rt_ids) < RETAIL_CUSTOMERS:
        nom = f"{rng.choice(NOMBRES)} {rng.choice(APELLIDOS)}"
        if nom in usados:
            continue
        usados.add(nom)
        ciudad, estado = rng.choice(CIUDADES_MX)
        alta = _fecha_aleatoria(rng, date(2024, 1, 10), date(2026, 9, 30))
        cur = cx.execute(insert, (nom, "Hobbyist", ciudad, estado, "México", alta))
        rt_ids.append((cur.lastrowid, alta))
    return ws_ids, rt_ids


def _generar_pedidos(cx, rng, ws_ids, rt_ids) -> None:
    for _trim, ini, fin, unidades in TRIMESTRES:
        pool_ws, pool_rt = {}, {}
        for sku, total in unidades.items():
            if sku in BUNDLES:
                pool_ws[sku] = total  # bundles: wholesale only
            else:
                ws = round(total * WS_FRACCION_UNIDADES)
                pool_ws[sku] = ws
                pool_rt[sku] = total - ws
        _vaciar_pool(cx, rng, pool_ws, "Mayoreo", ini, fin, ws_ids, rt_ids)
        _vaciar_pool(cx, rng, pool_rt, "Menudeo", ini, fin, ws_ids, rt_ids)


def _elegir_cliente(rng, canal, fecha, ws_ids, rt_ids):
    if canal == "Mayoreo":
        activos = [(cid, peso) for cid, alta, peso in ws_ids if alta <= fecha]
        ids, pesos = zip(*activos, strict=True)
        return rng.choices(ids, weights=pesos)[0]
    activos = [cid for cid, alta in rt_ids if alta <= fecha]
    return rng.choice(activos)


def _vaciar_pool(cx, rng, pool, canal, ini, fin, ws_ids, rt_ids) -> None:
    precio_idx = 0 if canal == "Mayoreo" else 1
    while any(v > 0 for v in pool.values()):
        fecha = _fecha_aleatoria(rng, ini, fin)
        disponibles = [s for s, v in pool.items() if v > 0]
        k = min(len(disponibles), rng.randint(2, 5) if canal == "Mayoreo" else rng.randint(1, 3))
        lineas = []
        for sku in rng.sample(disponibles, k):
            if sku in BUNDLES:
                qty = min(pool[sku], rng.randint(1, 6))
            elif canal == "Mayoreo":
                qty = min(pool[sku], rng.randint(80, 420))
            else:
                qty = min(pool[sku], rng.randint(3, 30))
            pool[sku] -= qty
            precio = PRECIOS[sku][precio_idx]
            if precio is None:  # a bundle on the retail channel: not sold there
                pool[sku] += qty
                continue
            lineas.append((sku, qty, precio, round(qty * precio, 2)))
        if not lineas:
            continue
        total_usd = round(sum(linea[3] for linea in lineas), 2)
        cliente = _elegir_cliente(rng, canal, fecha, ws_ids, rt_ids)
        cur = cx.execute(
            "INSERT INTO pedidos (cliente_id, fecha, canal, estado, total_usd, tipo_cambio, "
            "total_mxn) VALUES (?,?,?,?,?,?,?)",
            (
                cliente,
                fecha,
                canal,
                "Entregado" if fecha < "2026-11-05" else "En tránsito",
                total_usd,
                TIPO_CAMBIO,
                round(total_usd * TIPO_CAMBIO, 2),
            ),
        )
        cx.executemany(
            "INSERT INTO pedido_detalle (pedido_id, sku, cantidad, precio_unitario_usd, "
            "importe_usd) VALUES (?,?,?,?,?)",
            [(cur.lastrowid, s, q, p, imp) for s, q, p, imp in lineas],
        )


def _summarize(cx: sqlite3.Connection, path: Path) -> SeedSummary:
    revenue, wholesale_pct = cx.execute(
        "SELECT ROUND(SUM(total_mxn), 2), "
        "ROUND(100.0 * SUM(CASE WHEN canal = 'Mayoreo' THEN total_mxn END) / SUM(total_mxn), 1) "
        "FROM pedidos WHERE fecha BETWEEN ? AND ?",
        (Q3_START, Q3_END),
    ).fetchone()
    units = dict(
        cx.execute(
            "SELECT d.sku, SUM(d.cantidad) FROM pedido_detalle d JOIN pedidos p USING (pedido_id) "
            "WHERE p.fecha BETWEEN ? AND ? GROUP BY d.sku ORDER BY d.sku",
            (Q3_START, Q3_END),
        ).fetchall()
    )
    counts = {t: cx.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    return SeedSummary(
        path=path,
        row_counts=counts,
        q3_revenue_mxn=revenue,
        q3_wholesale_pct=wholesale_pct,
        q3_units=units,
    )
