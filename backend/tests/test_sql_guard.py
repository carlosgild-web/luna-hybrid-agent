"""The `sql_select` guard: one read-only SELECT/WITH statement, and adversarial input."""

import pytest

from app.agent.business_db import open_business_db
from app.agent.business_seed import seed_business_db
from app.agent.registry import ToolError
from app.agent.tools.business import run_select
from app.agent.tools.sql_guard import (
    MAX_QUERY_CHARS,
    UnsafeQueryError,
    check_select,
    mask_literals_and_comments,
)


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    path = tmp_path_factory.mktemp("business") / "hilda_luna.db"
    seed_business_db(path)
    return path


@pytest.fixture
def cx(seeded):
    connection = open_business_db(seeded)
    yield connection
    connection.close()


# ------------------------------------------------------------- what passes


@pytest.mark.parametrize(
    ("query", "runs_as"),
    [
        ("SELECT 1", "SELECT 1"),
        ("select 1", "select 1"),
        ("  SELECT 1  ", "SELECT 1"),
        ("SELECT 1;", "SELECT 1"),
        ("SELECT 1 ;  \n", "SELECT 1"),
        ("SELECT 1; -- done", "SELECT 1"),
        ("SELECT 1 -- trailing comment", "SELECT 1"),
        ("-- leading comment\nSELECT 1", "-- leading comment\nSELECT 1"),
        ("/* why */ SELECT 1", "/* why */ SELECT 1"),
        ("WITH t AS (SELECT 1 AS x) SELECT x FROM t", "WITH t AS (SELECT 1 AS x) SELECT x FROM t"),
        ("SELECT 'a;b'", "SELECT 'a;b'"),
        ("SELECT 'DROP TABLE pedidos'", "SELECT 'DROP TABLE pedidos'"),
        ("SELECT 'it''s; fine'", "SELECT 'it''s; fine'"),
        ('SELECT "update" FROM (SELECT 1 AS "update")', None),
        ("SELECT [delete] FROM (SELECT 1 AS [delete])", None),
        ("SELECT `insert` FROM (SELECT 1 AS `insert`)", None),
        ("SELECT CASE WHEN 1 THEN 'y' ELSE 'n' END", None),
        ("SELECT replace(nombre, 'a', 'b') FROM productos", None),
        ("SELECT 1 AS update_count, 2 AS deleted", None),
        ("SELECT x'00ff'", None),
    ],
)
def test_read_only_queries_pass(query, runs_as):
    assert check_select(query) == (runs_as or query)


# ----------------------------------------------------------- what's refused


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        (";", "empty"),
        ("-- only a comment", "empty"),
        ("SELECT 1; SELECT 2", "one statement"),
        ("SELECT 1; DROP TABLE pedidos", "one statement"),
        ("SELECT 1;;", "one statement"),
        ("SELECT 1 /* ; */ ; DELETE FROM pedidos", "one statement"),
        ("SELECT 1 -- comment\n; DELETE FROM pedidos", "one statement"),
        ("SELECT 'x'; UPDATE pedidos SET total_mxn = 0", "one statement"),
        ("UPDATE pedidos SET total_mxn = 0", "Only SELECT"),
        ("INSERT INTO clientes VALUES (1)", "Only SELECT"),
        ("DELETE FROM pedidos", "Only SELECT"),
        ("DROP TABLE pedidos", "Only SELECT"),
        ("PRAGMA query_only = OFF", "Only SELECT"),
        ("pragma table_info(pedidos)", "Only SELECT"),
        ("ATTACH DATABASE 'x.db' AS x", "Only SELECT"),
        ("VACUUM INTO 'copy.db'", "Only SELECT"),
        ("REPLACE INTO clientes VALUES (1)", "Only SELECT"),
        ("BEGIN", "Only SELECT"),
        ("END", "Only SELECT"),
        ("VALUES (1)", "Only SELECT"),
        ("EXPLAIN SELECT 1", "Only SELECT"),
        ("(SELECT 1)", "Only SELECT"),
        ("-- SELECT\nDELETE FROM pedidos", "Only SELECT"),
        ("/* SELECT */ DELETE FROM pedidos", "Only SELECT"),
        ("'SELECT' DELETE FROM pedidos", "Only SELECT"),
        ("WITH t AS (SELECT 1) DELETE FROM pedidos", "DELETE is not allowed"),
        ("WITH t AS (SELECT 1) UPDATE pedidos SET estado = 'x'", "UPDATE is not allowed"),
        ("WITH t AS (SELECT 1) INSERT INTO clientes SELECT * FROM t", "INSERT is not allowed"),
        ("WITH t AS (SELECT 1) REPLACE  INTO clientes SELECT * FROM t", "REPLACE INTO is not"),
        ("SELECT load_extension('evil')", "LOAD_EXTENSION is not allowed"),
        ("SELECT 1 FROM pedidos WHERE 1 = (SELECT 1) ATTACH", "ATTACH is not allowed"),
        ("SELECT 'unterminated", "never closed"),
        ('SELECT "unterminated', "never closed"),
        ("SELECT [unterminated", "never closed"),
        ("SELECT 1 /* unterminated", "never closed"),
        ("SELECT 'a'' ; DROP TABLE pedidos", "never closed"),
        ("SELECT " + "1," * MAX_QUERY_CHARS + "1", "too long"),
    ],
)
def test_unsafe_queries_are_refused(query, reason):
    with pytest.raises(UnsafeQueryError, match=reason):
        check_select(query)


def test_the_guard_error_is_a_tool_error():
    """So the registry hands it back to the model instead of failing the run."""
    assert issubclass(UnsafeQueryError, ToolError)


def test_masking_keeps_length_and_lines():
    sql = "SELECT 'a;b', \"c;d\" -- e;f\nFROM t /* g;\nh */"
    masked = mask_literals_and_comments(sql)
    assert len(masked) == len(sql)
    assert masked.count("\n") == sql.count("\n")
    assert ";" not in masked
    assert masked.startswith("SELECT '   ', \"   \"")


# ------------------------------------------------ the connection underneath


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE pedidos SET total_mxn = 0",
        "DELETE FROM pedidos",
        "PRAGMA query_only = OFF",
        "WITH t AS (SELECT 1) DELETE FROM pedidos",
    ],
)
def test_a_write_past_the_guard_still_fails(cx, statement):
    """If the text check had a bug, the read-only connection is the next wall."""
    with pytest.raises(ToolError, match="SQL error"):
        run_select(cx, statement)
    assert cx.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0] == 937


def test_two_statements_past_the_guard_still_fail(cx):
    with pytest.raises(ToolError, match="one statement"):
        run_select(cx, "SELECT 1; SELECT 2")


def test_rows_are_capped(cx):
    result = run_select(cx, "SELECT detalle_id FROM pedido_detalle ORDER BY detalle_id")
    assert result["columns"] == ["detalle_id"]
    assert result["row_count"] == 200
    assert result["truncated"] is True
    assert result["rows"][0] == [1]


def test_exactly_the_cap_is_not_truncated(cx):
    result = run_select(cx, "SELECT detalle_id FROM pedido_detalle LIMIT 7", max_rows=7)
    assert result["row_count"] == 7
    assert result["truncated"] is False


def test_a_runaway_query_is_stopped(cx):
    endless = (
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n"
    )
    with pytest.raises(ToolError, match="longer than 0.2 s"):
        run_select(cx, endless, timeout_s=0.2)
    # the connection is usable afterwards, with no time limit left behind
    assert run_select(cx, "SELECT 1")["rows"] == [[1]]


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("SELECT precio FROM productos", "SQL error: no such column: precio"),
        ("SELECT * FROM ventas", "SQL error: no such table: ventas"),
        ("SELECT FROM productos", 'SQL error: near "FROM": syntax error'),
    ],
)
def test_sqlite_errors_come_back_readable(cx, query, message):
    with pytest.raises(ToolError) as caught:
        run_select(cx, query)
    assert str(caught.value) == message


def test_blobs_and_long_text_are_shortened(cx):
    result = run_select(cx, "SELECT x'00ff', printf('%.600c', 'a')")
    blob, text = result["rows"][0]
    assert blob == "<2 bytes>"
    assert len(text) == 501
    assert text.endswith("…")


def test_the_schema_can_be_read(cx):
    result = run_select(cx, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    assert ["pedidos"] in result["rows"]
