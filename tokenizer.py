"""
Tokenizer utilities extracted from translator.
"""
import sqlparse


def tokenize(sql: str) -> list[tuple[str, str]]:
    """
    Simplified SQL tokenizer using sqlparse.
    Returns (token_type, token_value) pairs.
    Converts -- comments to /* */ format for SAS compatibility.
    Groups tokens into statements separated by semicolons.
    """

    parts: list[tuple[str, str]] = []
    # Parse the script into individual statements
    parsed = sqlparse.parse(sql)
    
    for stmt in parsed:
        # 1. Format the statement
        formatted_sql = sqlparse.format(
            str(stmt),
            reindent=True,
            # keyword_case='unchanged',
            strip_comments=False
        ).strip()
        
        # Skip empty strings (can happen with trailing semicolons)
        if not formatted_sql:
            continue
            
        # 2. Determine if it's a COMMENT or a STATEMENT
        # sqlparse.Statement.get_type() returns 'SELECT', 'INSERT', etc.
        # If it's just a comment, it usually returns 'UNKNOWN'
        if stmt.get_type() in ['COMMENT']:
            label = "line_comment"
        else:
            label = "statement"
            
        parts.append((label, formatted_sql))
        

    return parts
