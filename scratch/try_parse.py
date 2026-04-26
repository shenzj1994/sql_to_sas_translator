import sqlparse

test_sql_text = """
-- My SQL code
create temp table abc as 
select x,y,z from table_01;
-- My comments
with abc as (select 1 as col1) 
select * 
from abc;
"""

stmts = sqlparse.parse(test_sql_text)
print(stmts)
for s in stmts:
    print(f'=== Statement type: {s.get_type()} ===')
    for t in s.flatten():
        print(f'Token: {t} (type: {t.ttype})')
    # print(f'Tokens: {s.tokens}')