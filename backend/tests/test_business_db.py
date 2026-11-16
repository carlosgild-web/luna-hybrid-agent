"""The demo business database: seeded and reproducible, and read-only for the agent."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app import cli
from app.agent.business_db import BusinessDBMissingError, business_db, open_business_db
from app.agent.business_seed import TIPO_CAMBIO, seed_business_db
from app.core.config import Settings

EXPECTED_ROWS = {
    "productos": 13,
    "inventario": 13,
    "clientes": 68,
    "pedidos": 937,
    "pedido_detalle": 1867,
    "proveedores": 3,
    "compras_proveedor": 11,
    "empleados": 5,
}

Q3 = ("2026-07-01", "2026-09-30")
Q2 = ("2026-04-01", "2026-06-30")


@pytest.fixture(scope="module")
def seeded(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("business") / "hilda_luna.db"
    seed_business_db(path)
    return path


@pytest.fixture
def cx(seeded):
    connection = open_business_db(seeded)
    yield connection
    connection.close()


def _units(cx, sku: str, period: tuple[str, str]) -> int:
    return cx.execute(
        "SELECT SUM(d.cantidad) FROM pedido_detalle d JOIN pedidos p USING (pedido_id) "
        "WHERE d.sku = ? AND p.fecha BETWEEN ? AND ?",
        (sku, *period),
    ).fetchone()[0]


def _content_hash(path: Path) -> str:
    with sqlite3.connect(path) as raw:
        return hashlib.sha256("\n".join(raw.iterdump()).encode()).hexdigest()


# ---------------------------------------------------------------- the seed


def test_seed_is_reproducible(tmp_path, seeded):
    again = tmp_path / "again.db"
    summary = seed_business_db(again)
    assert summary.row_counts == EXPECTED_ROWS
    assert _content_hash(again) == _content_hash(seeded)


def test_seed_replaces_an_existing_file_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "hilda_luna.db"
    path.write_bytes(b"not a database")
    seed_business_db(path)
    assert seed_business_db(path).row_counts == EXPECTED_ROWS
    assert [p.name for p in tmp_path.iterdir()] == ["hilda_luna.db"]


def test_row_counts(cx):
    for table, expected in EXPECTED_ROWS.items():
        assert cx.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == expected, table


def test_q3_matches_the_meeting_notes(cx):
    """The figures in `07-q3-2026-sales-meeting-notes.txt`, so hybrid answers can be checked."""
    assert _units(cx, "CH-2004", Q3) == 4120
    assert _units(cx, "GB-1014", Q3) == 3340
    assert (_units(cx, "GB-1027", Q2), _units(cx, "GB-1027", Q3)) == (1800, 1404)  # -22%

    revenue, wholesale = cx.execute(
        "SELECT SUM(total_mxn), SUM(CASE WHEN canal = 'Mayoreo' THEN total_mxn END) "
        "FROM pedidos WHERE fecha BETWEEN ? AND ?",
        Q3,
    ).fetchone()
    assert round(revenue) == 519_726  # notes: about $512,800 MXN
    assert round(100 * wholesale / revenue, 1) == 59.8  # notes: about 61%

    top_customer = cx.execute(
        "SELECT c.nombre FROM pedidos o JOIN clientes c USING (cliente_id) "
        "WHERE c.tipo != 'Hobbyist' GROUP BY c.cliente_id ORDER BY SUM(o.total_mxn) DESC LIMIT 1"
    ).fetchone()[0]
    assert top_customer == "Abalone Supply Co."


def test_three_products_need_restocking(cx):
    rows = cx.execute("SELECT sku FROM inventario WHERE stock_actual < punto_reorden ORDER BY sku")
    assert [r["sku"] for r in rows] == ["BN-9001", "CH-2004", "GB-1014"]


def test_order_totals_add_up(cx):
    """Every order's total is the sum of its lines, converted at the stored rate.

    Compared unrounded, to half a cent: SQLite's ROUND and Python's round break
    ties differently.
    """
    mismatches = cx.execute(
        "SELECT COUNT(*) FROM pedidos p JOIN (SELECT pedido_id, SUM(importe_usd) s "
        "FROM pedido_detalle GROUP BY pedido_id) d USING (pedido_id) "
        "WHERE ABS(p.total_usd - d.s) > 0.0051 OR ABS(p.total_mxn - p.total_usd * ?) > 0.0051",
        (TIPO_CAMBIO,),
    ).fetchone()[0]
    assert mismatches == 0
    rates = [r[0] for r in cx.execute("SELECT DISTINCT tipo_cambio FROM pedidos")]
    assert rates == [TIPO_CAMBIO]


# ----------------------------------------------------------- read-only access

WRITE_ATTEMPTS = [
    "INSERT INTO productos VALUES ('XX-0001', 'x', 'x', 'x', 1, 1, 'x')",
    "UPDATE inventario SET stock_actual = 0",
    "DELETE FROM pedidos",
    "DROP TABLE pedidos",
    "CREATE TABLE notas (x TEXT)",
    "CREATE TEMP TABLE notas (x TEXT)",
    "ALTER TABLE productos ADD COLUMN x TEXT",
    "CREATE INDEX idx_x ON productos(nombre)",
    "REPLACE INTO empleados VALUES (1, 'x', 'x', 'x')",
    "PRAGMA query_only = OFF",
    "PRAGMA journal_mode = DELETE",
    "VACUUM",
    "REINDEX",
    "BEGIN IMMEDIATE",
    "ATTACH DATABASE ':memory:' AS scratch",
    "WITH x AS (SELECT 1) DELETE FROM pedidos",
]


@pytest.mark.parametrize("statement", WRITE_ATTEMPTS)
def test_write_attempts_raise_and_change_nothing(seeded, cx, statement):
    before = _content_hash(seeded)
    with pytest.raises(sqlite3.DatabaseError):
        cx.execute(statement)
    assert _content_hash(seeded) == before
    assert cx.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0] == 937


def test_pragma_query_only_stays_on(cx):
    """Each layer holds on its own: the authorizer even refuses to read the pragma."""
    with pytest.raises(sqlite3.DatabaseError):
        cx.execute("PRAGMA query_only")


def test_reads_work(cx):
    row = cx.execute(
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3) "
        "SELECT p.nombre, i.stock_actual, (SELECT COUNT(*) FROM n) AS tres, UPPER(p.sku) AS sku "
        "FROM productos p JOIN inventario i USING (sku) WHERE p.sku = ?",
        ("CH-2004",),
    ).fetchone()
    assert dict(row) == {
        "nombre": "Crescent Moon & Star",
        "stock_actual": 1900,
        "tres": 3,
        "sku": "CH-2004",
    }


def test_missing_database_says_how_to_create_it(tmp_path):
    with pytest.raises(BusinessDBMissingError, match="seed-business-db"):
        open_business_db(tmp_path / "nope.db")
    assert not (tmp_path / "nope.db").exists(), "opening must never create the file"


# ------------------------------------------------------------ config and CLI


def test_default_path_is_under_data_dir(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.business_db_file == tmp_path / "business" / "hilda_luna.db"


def test_business_db_path_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("BUSINESS_DB_PATH", str(tmp_path / "elsewhere.db"))
    assert Settings(_env_file=None).business_db_file == tmp_path / "elsewhere.db"


def test_cli_seeds_a_fresh_data_dir(monkeypatch, tmp_path, capsys):
    """Phase 1's "done when": a fresh DATA_DIR gets the calibrated DB, and writes raise."""
    settings = Settings(_env_file=None, data_dir=tmp_path / "fresh")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    assert cli.main(["seed-business-db"]) == 0
    out = capsys.readouterr().out
    assert f"created  {settings.business_db_file}" in out
    assert "CH-2004    4,120 units" in out

    with business_db(settings) as cx:
        assert _units(cx, "CH-2004", Q3) == 4120
        with pytest.raises(sqlite3.DatabaseError):
            cx.execute("DELETE FROM pedidos")

    assert cli.main(["seed-business-db"]) == 0
    assert "replaced" in capsys.readouterr().out
