"""
translator.py
─────────────
Pure-logic module: tokenizes a SQL string and produces a SAS PROC SQL
pass-through block.  No Flask, no argparse, no file I/O.

Public API
──────────
    translate(sql: str, conn: str) -> dict
        {
            "sas":      str,
            "warnings": list[str],
            "counts":   {"total": int, "wrapped": int, "skipped": int},
        }
"""

import re


# ── tokenizer ──────────────────────────────────────────────────────────────────

def tokenize(sql: str) -> list[tuple[str, str]]:
    """
    Produce an ordered list of (kind, text) tokens:

        'line_comment'   -- standalone  -- comment between statements
        'block_comment'  -- standalone  /* comment */ between statements
        'statement'      -- one SQL statement (inline comments absorbed)

    Inline comments (inside an already-started statement) are absorbed into
    the statement text.  '--' inline comments are converted to /* */ since
    SAS does not support -- inside execute() bodies.
    """
    tokens: list[tuple[str, str]] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(sql)

    def in_statement() -> bool:
        return bool("".join(current).strip())

    def flush_stmt() -> None:
        text = "".join(current).strip()
        if text:
            tokens.append(("statement", text))
        current.clear()

    while i < n:
        ch = sql[i]

        if not in_single and not in_double:

            # -- line comment
            if ch == "-" and i + 1 < n and sql[i + 1] == "-":
                end = sql.find("\n", i)
                end = end if end != -1 else n
                comment_text = sql[i:end]
                if in_statement():
                    body = comment_text.lstrip("-").strip()
                    current.append(f" /* {body} */")
                else:
                    tokens.append(("line_comment", comment_text))
                i = end
                continue

            # /* block comment */
            if ch == "/" and i + 1 < n and sql[i + 1] == "*":
                end = sql.find("*/", i + 2)
                end = end + 2 if end != -1 else n
                comment_text = sql[i:end]
                if in_statement():
                    current.append(comment_text)
                else:
                    flush_stmt()
                    tokens.append(("block_comment", comment_text))
                i = end
                continue

            if ch == ";":
                flush_stmt()
                i += 1
                continue

        if ch == "'" and not in_double:
            if in_single and i + 1 < n and sql[i + 1] == "'":
                current.append("''")
                i += 2
                continue
            in_single = not in_single
        elif ch == '"' and not in_single:
            if in_double and i + 1 < n and sql[i + 1] == '"':
                current.append('""')
                i += 2
                continue
            in_double = not in_double

        current.append(ch)
        i += 1

    flush_stmt()
    return tokens


# ── comment conversion ─────────────────────────────────────────────────────────

def convert_comment_to_sas(kind: str, text: str) -> str:
    """Convert a standalone SQL comment token to a SAS /* … */ comment."""
    if kind == "block_comment":
        return text
    body = text.lstrip("-").strip()
    return f"/* {body} */" if body else "/* */"


# ── statement formatting ───────────────────────────────────────────────────────

def normalize_whitespace(stmt: str) -> str:
    """Normalize whitespace in a statement."""
    stmt = re.sub(r"\n{3,}", "\n\n", stmt)
    lines = [line.rstrip() for line in stmt.splitlines()]
    return "\n".join(lines).strip()


def indent(text: str, spaces: int = 4) -> str:
    """Indent text by the specified number of spaces."""
    pad = " " * spaces
    return "\n".join(
        pad + line if line.strip() else line for line in text.splitlines()
    )


def wrap_as_execute(stmt: str, conn: str) -> str:
    """Wrap a SQL statement as a SAS execute block."""
    body = indent(normalize_whitespace(stmt))
    return f"    execute (\n{body}\n    ) by {conn};"


def wrap_as_sas_comment(stmt: str) -> str:
    """Render a SQL statement as a SAS block comment instead of executable code."""
    body = normalize_whitespace(stmt)
    body = body.replace("/*", "/ *").replace("*/", "* /")
    body = indent(body, spaces=8)
    return f"    /*\n{body}\n    */"


def wrap_select_as_dataset(stmt: str, dataset_name: str, conn: str) -> str:
    """Wrap a SELECT statement to create a dataset using pass-through syntax."""
    body = indent(normalize_whitespace(stmt), spaces=20)
    return (
        f"    create table {dataset_name} as\n"
        f"        select *\n"
        f"            from connection to {conn}\n"
        f"                (\n{body}\n"
        f"                );"
    )


# ── SAS block assembly ─────────────────────────────────────────────────────────

def build_sas_block(
    tokens: list[tuple[str, str]],
    conn_name: str,
    conn_string: str,
    wrapped_count: int,
) -> str:
    header = (
        f"/* Generated by sql-to-sas-web\n"
        f"   Connection      : {conn_name}\n"
        f"   Stmts wrapped   : {wrapped_count}\n"
        f"*/"
    )

    body_parts: list[str] = []
    pending_comments: list[str] = []

    for kind, text in tokens:
        if kind in ("line_comment", "block_comment"):
            pending_comments.append("    " + convert_comment_to_sas(kind, text))
        else:
            if pending_comments:
                body_parts.append("\n".join(pending_comments))
                pending_comments = []
            if kind == "select_comment":
                body_parts.append(wrap_as_sas_comment(text))
            elif kind.startswith("select_dataset_"):
                dataset_name = kind.split("_", 2)[2]
                body_parts.append(wrap_select_as_dataset(text, dataset_name, conn_name))
            else:
                body_parts.append(wrap_as_execute(text, conn_name))

    if pending_comments:
        body_parts.append("\n".join(pending_comments))

    body = "\n\n".join(body_parts)
    return (
        f"{header}\n\n"
        f"proc sql;\n"
        f"    {conn_string}\n\n"
        f"{body}\n\n"
        f"    disconnect from {conn_name};\n"
        f"quit;"
    )


# ── connection string builder ──────────────────────────────────────────────────

def build_connection_string(
    conn_name: str, conn_dbtype: str, conn_dsn: str,
    conn_authdomain: str, conn_type: str
) -> str:
    """Build a SAS connection string from parameters."""
    conn_parts = [conn_dbtype, "as", conn_name]
    conn_opts = []

    if conn_dsn:
        conn_opts.append(f"dsn='{conn_dsn}'")
    if conn_authdomain:
        conn_opts.append(f"authdomain='{conn_authdomain}'")
    if conn_type:
        conn_opts.append(f"connection={conn_type}")

    if conn_opts:
        return f"connect to {' '.join(conn_parts)} ({' '.join(conn_opts)});"
    return f"connect to {' '.join(conn_parts)};"


def process_select_statement(
    stmt_index: int, text: str, select_mode: str, select_counter: int
) -> tuple[int, list[str], tuple[str, str] | None]:
    """Process a SELECT statement based on the mode."""
    preview = " ".join(text.split()[:12])
    if len(text.split()) > 12:
        preview += " …"

    warnings = []
    token = None

    if select_mode == "ignore":
        warnings.append(
            f"Statement #{stmt_index} is a SELECT and will be ignored: {preview}"
        )
    elif select_mode == "dataset":
        select_counter += 1
        dataset_name = f"select_{select_counter}"
        warnings.append(
            f"Statement #{stmt_index} is a SELECT and will create "
            f"dataset '{dataset_name}': {preview}"
        )
        token = (f"select_dataset_{dataset_name}", text)
    else:  # comment mode (default)
        warnings.append(
            f"Statement #{stmt_index} is a SELECT and will be commented out "
            f"(not executable via pass-through): {preview}"
        )
        token = ("select_comment", text)

    return select_counter, warnings, token


# ── public API ─────────────────────────────────────────────────────────────────

def translate(
    sql: str, conn_name: str = "myconn", conn_dbtype: str = "oracle",
    conn_dsn: str = "", conn_authdomain: str = "", conn_type: str = "global",
    select_mode: str = "comment"
) -> dict:
    """
    Translate a SQL string into a SAS PROC SQL pass-through block.

    Parameters
    ----------
    sql             : raw SQL source text
    conn_name       : SAS connection/libname alias (e.g., 'myconn')
    conn_dbtype     : database type (e.g., 'oracle')
    conn_dsn        : DSN name (e.g., 'ABC_DSN')
    conn_authdomain : authentication domain (e.g., 'my_authdomain')
    conn_type       : connection type (e.g., 'global')
    select_mode     : how to handle SELECT statements: "ignore", "comment", or "dataset"

    Returns
    -------
    {
        "sas":      str,
        "warnings": list[str],
        "counts":   {"total": int, "wrapped": int, "skipped": int, "selected": int},
    }

    SELECT statements are handled according to select_mode:
    - "ignore": completely omitted from output
    - "comment": preserved as SAS comments
    - "dataset": executed and create datasets (SELECT_1, SELECT_2, etc.)
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
        first_word = text.split()[0].upper() if text.split() else ""

        if first_word == "SELECT":
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
