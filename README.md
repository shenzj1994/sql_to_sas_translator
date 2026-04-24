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

### SELECT statements are skipped

`PROC SQL` pass-through `execute()` cannot return result sets — only statements that perform an action (DDL, DML) are valid. Any statement whose first keyword is `SELECT` is silently dropped from the output, and a warning is shown in the UI.

```sql
-- This will be skipped with a warning:
SELECT COUNT(*) FROM staging.ev_sessions;

-- This will be wrapped normally:
INSERT INTO staging.ev_sessions SELECT ...;
```

Note that `INSERT INTO ... SELECT ...` is kept — the filter applies only to statements that begin with `SELECT`, not to `SELECT` appearing inside another statement.

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
translate(sql: str, conn: str) -> dict
# {
#   "sas":      str,
#   "warnings": list[str],
#   "counts":   {"total": int, "wrapped": int, "skipped": int},
# }
```

This means it can be imported and tested independently of Flask, or reused in other contexts (e.g. a CLI wrapper).

## Running locally

```bash
pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

The `conn` field defaults to `conn` — change it to match your SAS libname or connection alias (e.g. `greenplum`, `mydb`).

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
