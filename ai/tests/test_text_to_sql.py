from types import SimpleNamespace

import pytest

from ai.text_to_sql import agent
from ai.text_to_sql.executor import QueryResult
from ai.text_to_sql.guard import UnsafeSQL, check_sql
from ai.text_to_sql.schema import render


@pytest.mark.parametrize("sql", [
    "select make, sum(complaints) from vehicle_complaint_summary group by 1 order by 2 desc limit 10;",
    "select * from gold.recall_coverage where has_recall = false",
    "select * from VEHICLE_SAFETY.GOLD.COMPONENT_TRENDS",
    "with t as (select * from component_trends) select * from t",
    "select f.value::string from recall_coverage, lateral flatten(campaign_numbers) f",
])
def test_check_sql_allows_selects_on_gold_analytics_tables(sql):
    assert check_sql(sql) == sql.strip().rstrip(";")


@pytest.mark.parametrize("sql, reason", [
    ("select * from silver.complaints", "not available"),
    ("select * from rag_documents", "not available"),
    ("select * from vehicle_safety.bronze.load_log", "not available"),
    ("select * from silver.recall_coverage", "GOLD tables only"),
    ("delete from recall_coverage", "Only SELECT"),
    ("select 1; drop table recall_coverage", "exactly one"),
    ("create table x as select * from recall_coverage", "Only SELECT"),
    ("select * into x from recall_coverage", "Only SELECT"),
    ("select * from table(result_scan(last_query_id()))", "not allowed"),
    ("select system$whitelist()", "not allowed"),
    ("select snowflake.cortex.complete('mistral-7b', make) from recall_coverage", "not allowed"),
    ("select ai_complete('mistral-7b', 'hi')", "not allowed"),
    ("selec * from", "parse"),
])
def test_check_sql_rejects_writes_other_tables_and_blocked_functions(sql, reason):
    with pytest.raises(UnsafeSQL, match=reason):
        check_sql(sql)


def test_schema_render_uses_dbt_descriptions_and_lists_only_allowed_tables():
    text = render({"vehicle_complaint_summary": {"severe_share": "FLOAT", "new_col": "NUMBER"}},
                  ["AIR BAGS", "ENGINE"], ("1995-01-01", "2026-10-01"))
    assert "severe_share FLOAT: Share of this vehicle's complaints" in text
    assert "- new_col NUMBER" in text  # columns missing from the YAML still show up
    assert "rag_documents" not in text and "TABLE recall_coverage" in text
    assert "AIR BAGS, ENGINE" in text and "1995-01-01 to 2026-10-01" in text


class FakeClient:
    """Plays back canned responses and records the requests."""

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


def response(*blocks, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5)
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage)


def tool_use(name, **args):
    return SimpleNamespace(type="tool_use", id=f"tu_{name}", name=name, input=args)


def text(t):
    return SimpleNamespace(type="text", text=t)


def test_ask_sends_errors_back_and_returns_the_last_successful_query(monkeypatch):
    calls = []

    def fake_run(conn, sql):
        calls.append(sql)
        if "bad" in sql:
            raise RuntimeError("invalid identifier 'BAD'")
        return QueryResult(sql, ["make", "complaints"], [("FORD", 10)])

    monkeypatch.setattr(agent, "run_query", fake_run)
    client = FakeClient([
        response(tool_use("run_sql", sql="select bad from recall_coverage"), stop="tool_use"),
        response(tool_use("run_sql", sql="select make, complaints from recall_coverage"), stop="tool_use"),
        response(text("Ford has 10 complaints.")),
    ])
    usage = []
    result = agent.ask(client, None, "q", "schema", on_usage=lambda *a: usage.append(a))
    assert result.answer == "Ford has 10 complaints." and result.stop == "end_turn"
    assert result.result.sql == "select make, complaints from recall_coverage"
    assert [s.ok for s in result.steps] == [False, True] and len(usage) == 3
    error_turn = client.requests[1]["messages"][2]["content"][0]  # first tool_result turn
    assert error_turn["is_error"] and "invalid identifier" in error_turn["content"]


def test_ask_blocks_unsafe_sql_without_running_it(monkeypatch):
    monkeypatch.setattr(agent, "run_query", lambda *a: pytest.fail("unsafe SQL reached Snowflake"))
    client = FakeClient([
        response(tool_use("run_sql", sql="drop table recall_coverage"), stop="tool_use"),
        response(text("I can only read data.")),
    ])
    result = agent.ask(client, None, "q", "schema")
    assert result.result is None and not result.steps[0].ok


def test_ask_stops_after_the_call_limit(monkeypatch):
    monkeypatch.setattr(agent, "run_query", lambda conn, sql: QueryResult(sql, ["x"], [(1,)]))
    loop = response(tool_use("run_sql", sql="select 1 from recall_coverage"), stop="tool_use")
    client = FakeClient([loop] * agent.MAX_MODEL_CALLS)
    result = agent.ask(client, None, "q", "schema")
    assert result.stop == "max_calls" and len(client.requests) == agent.MAX_MODEL_CALLS


def test_results_match_ignores_column_names_order_and_extra_columns():
    from ai.evals.run_sql_eval import results_match

    expected = (["make", "complaints"], [("FORD", 10), ("TESLA", 7)])
    assert results_match(*expected, ["n", "brand", "extra"], [(7, "tesla", 1), (10, "FORD", 2)])
    assert not results_match(*expected, ["make", "complaints"], [("FORD", 10)])  # missing row
    assert not results_match(*expected, ["make", "complaints"], [("FORD", 10), ("TESLA", 8)])  # wrong number
    assert not results_match(*expected, ["make"], [("FORD",), ("TESLA",)])  # missing column


def test_results_match_allows_rounding_and_percentages():
    from ai.evals.run_sql_eval import results_match

    assert results_match(["severe_share"], [(0.2188,)], ["pct"], [(21.9,)])
    assert results_match(["severe_share"], [(0.2188,)], ["share"], [(0.219,)])
    assert not results_match(["severe_share"], [(0.2188,)], ["share"], [(0.25,)])
    shares = [(0.1550,), (0.1538,), (0.1515,)]
    assert results_match(["severe_share"], shares, ["pct"], [(15.2,), (15.4,), (15.5,)])  # close values, any order
    assert results_match(["has_recall", "n"], [(False, 0)], ["has_recall", "n"], [(False, 0)])
    assert results_match(["has_recall"], [(False,)], ["has_recall"], [(0,)])  # 0/1 for a yes/no is the same answer
    assert not results_match(["has_recall"], [(False,)], ["has_recall"], [(True,)])
