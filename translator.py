"""
SQL-to-SAS Translator (orchestrator)

This module preserves the original public API but delegates implementation
to smaller modules: `tokenizer`, `comments`, `formatting`, `sas_block`,
`connection`, and `select_processor`.
"""

from tokenizer import tokenize
from select_processor import process_select_statement, _is_select_statement
from connection import build_connection_string
from sas_block import build_sas_block


def translate(
    sql: str, conn_name: str = "myconn", conn_dbtype: str = "oracle",
    conn_dsn: str = "", conn_authdomain: str = "", conn_type: str = "global",
    select_mode: str = "comment"
) -> dict:
    """
    Translate a SQL string into a SAS PROC SQL pass-through block.

    Public API preserved; implementation delegated to helper modules.
    """
    conn_string = build_connection_string(
        conn_name, conn_dbtype, conn_dsn, conn_authdomain, conn_type
    )

    tokens = tokenize(sql)
    filtered_tokens: list[tuple[str, str]] = []
    warnings: list[str] = []
    total = 0
    skipped = 0
    selected = 0
    select_counter = 0
    stmt_index = 0

    for kind, text in tokens:
        if kind != "statement":
            filtered_tokens.append((kind, text))
            continue

        total += 1
        stmt_index += 1
        if _is_select_statement(text):
            selected += 1
            select_counter, stmt_warnings, token = process_select_statement(
                stmt_index, text, select_mode, select_counter
            )
            warnings.extend(stmt_warnings)
            if select_mode == "ignore":
                skipped += 1
            elif token:
                filtered_tokens.append(token)
        else:
            filtered_tokens.append((kind, text))

    wrapped = total - (skipped if select_mode != "dataset" else 0)
    sas = build_sas_block(
        filtered_tokens, conn_name=conn_name, conn_string=conn_string,
        wrapped_count=wrapped
    )

    return {
        "sas": sas,
        "warnings": warnings,
        "counts": {
            "total": total, "wrapped": wrapped, "skipped": skipped,
            "selected": selected
        },
    }
