from translator import translate


def test_leading_comment_and_trailing_select_are_split():
    sql = """
    -- My SQL code
create temp table def as 
select x,y,z from table_01;

select * from def;
"""

    result = translate(sql)

    assert result["counts"]["total"] == 2
    assert result["counts"]["selected"] == 1
    assert "/* My SQL code */" in result["sas"]
    assert "execute (" in result["sas"]
    assert "select *" in result["sas"]
    assert result["warnings"]
