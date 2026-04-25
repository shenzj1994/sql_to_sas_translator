# sql-to-sas-passthrough

A small web app that translates a plain SQL script into a runnable SAS `PROC SQL` pass-through block. Paste SQL on the left, get SAS on the right.

## What it does

SAS pass-through lets you send native SQL directly to a remote database (Greenplum, PostgreSQL, etc.) via a named connection. The syntax wraps each statement like this:

```sas
proc sql;
    connect to greenplum;

    execute (
        CREATE TABLE staging.ev_sessions (
            session_id  BIGINT NOT NULL,
            energy_kwh  NUMERIC(10,4)
        )
    ) by greenplum;

    disconnect from greenplum;
quit;
```

Writing this by hand for a long SQL script is tedious and error-prone. This tool does it automatically.

## Translation rules

### SELECT statement handling

`PROC SQL` pass-through `execute()` cannot return result sets — only statements that perform an action (DDL, DML) are valid. The tool provides three modes for handling SELECT statements:

**1. As comment (default)** — Any statement whose first keyword is `SELECT` is preserved as a SAS comment, and a warning is shown in the UI.

```sql
-- This will be commented out with a warning:
SELECT COUNT(*) FROM staging.ev_sessions;

-- This will be wrapped normally:
INSERT INTO staging.ev_sessions SELECT ...;
```

The SELECT appears in the generated SAS output as readable commented text, so SAS ignores it while a human reviewer can still see it. Note that `INSERT INTO ... SELECT ...` is wrapped normally — the filter applies only to statements that begin with `SELECT`, not to `SELECT` appearing inside another statement.

**2. As dataset** — SELECT statements are executed and create datasets (named `select_1`, `select_2`, etc.) using SAS pass-through syntax:

```sql
SELECT empid, lastname, firstname, salary
FROM employees
WHERE salary > 75000;
```

becomes:

```sas
    create table select_1 as
        select *
            from connection to myconn
                (
                SELECT empid, lastname, firstname, salary
                FROM employees
                WHERE salary > 75000
                );
```

**3. Ignore** — SELECT statements are completely omitted from the output.

### Comments are converted to SAS comments, not discarded

SQL comments are preserved in the output as SAS `/* ... */` comments. Two cases are handled differently depending on where the comment appears.

**Standalone comments** — comments that appear between statements are emitted as SAS block comments immediately before the `execute()` block that follows them:

```sql
-- Load from raw source
INSERT INTO staging.ev_sessions ...;
```

becomes:

```sas
    /* Load from raw source */

    execute (
        INSERT INTO staging.ev_sessions ...
    ) by greenplum;
```

**Inline comments** — comments that appear inside a statement body are absorbed into the statement and converted to `/* ... */` syntax. This is necessary because SAS does not support `--` comments inside an `execute()` body:

```sql
INSERT INTO staging.ev_sessions (
    session_id,   -- primary key
    energy_kwh    /* measured in kWh */
) ...;
```

becomes:

```sas
    execute (
        INSERT INTO staging.ev_sessions (
            session_id,    /* primary key */
            energy_kwh    /* measured in kWh */
        ) ...
    ) by greenplum;
```

Block comments (`/* ... */`) that appear inline are kept as-is since they are already valid SAS syntax.

## Project structure

```
sql_to_sas_web/
├── app.py            Flask app — HTTP routes only
├── translator.py     Pure translation logic — no Flask dependency
├── templates/
│   └── index.html    Single-page UI
└── requirements.txt
```

`translator.py` is intentionally kept framework-free. It exposes one public function:

```python
translate(
    sql: str,
    conn_name: str = "myconn",
    conn_dbtype: str = "oracle",
    conn_dsn: str = "",
    conn_authdomain: str = "",
    conn_type: str = "global",
    select_mode: str = "comment"
) -> dict
# {
#   "sas":      str,
#   "warnings": list[str],
#   "counts":   {
#       "total": int,
#       "wrapped": int,
#       "skipped": int,
#       "selected": int
#   },
# }
```

Parameters:
- `sql` — Raw SQL source text
- `conn_name` — SAS connection/libname alias
- `conn_dbtype` — Database type (oracle, postgres, etc.)
- `conn_dsn` — Data source name
- `conn_authdomain` — Authentication domain
- `conn_type` — Connection type (global, local, etc.)
- `select_mode` — How to handle SELECT statements: `"ignore"`, `"comment"`, or `"dataset"`

This means it can be imported and tested independently of Flask, or reused in other contexts (e.g. a CLI wrapper).

## Running locally

```bash
pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

### Connection parameters

The web UI provides customizable connection parameters:

- **SELECT mode** — Choose how to handle SELECT statements: `as comment` (default), `as dataset`, or `ignore`
- **alias** — SAS connection/libname alias (e.g., `myconn`, `greenplum`)
- **dbtype** — Database type (e.g., `oracle`, `postgres`, `greenplum`)
- **dsn** — Data source name (e.g., `ABC_DSN`)
- **authdomain** — Authentication domain for credentials
- **conntype** — Connection type (e.g., `global`, `local`)

These parameters are used to build the SAS `connect to` statement. For example:

```sas
connect to oracle as myconn (dsn='ABC_DSN' authdomain='my_domain' connection=global);
```

**Keyboard shortcut:** `Ctrl+Enter` / `Cmd+Enter` in the SQL pane triggers translation.

## Deploying to Render (free tier)

1. Push the project to a GitHub repository.
2. Create a new **Web Service** on [render.com](https://render.com) and connect the repo.
3. Set the following:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn app:app`
4. Deploy.

Render's free tier spins down after inactivity — the first request after idle takes around 30 seconds. For a lightweight internal tool this is usually acceptable.

`gunicorn` is included in `requirements.txt`. It is used instead of Flask's built-in development server because Flask's server is single-threaded and not suitable for handling concurrent requests.

## Limitations

- No authentication or rate limiting — intended for internal or personal use.
- Input size is not capped — very large SQL files may be slow to process in the browser.
- The tokenizer handles standard SQL string literals (`'...'`, `"..."`) and escaped quotes (`''`, `""`), but does not handle dollar-quoted strings (`$$...$$`) used in PostgreSQL procedural code.
