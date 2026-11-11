import pandas as pd

from analytics_client import view

MONTHLY = {"model": "payment_activity", "measures": ["total_revenue", "payment_count"], "dimensions": ["rental_date:month"]}


def frame(rows, request):
    return view.answer_frame({"columns": list(rows[0]), "rows": rows}, request)


def test_plain_escapes_markdown_and_directives():
    out = view.plain("![x](http://evil/?q=1) :red[boo] <b>hi</b> `code` **bold**")
    for raw in ("![", "](", ":red[", "<b>", "`code`", "**bold**"):
        assert raw not in out
    assert view.plain("plain words") == "plain words"


def test_time_columns_become_datetimes():
    df = frame([{"rental_date": "2025-01-01", "total_revenue": 1.5, "payment_count": 2}], MONTHLY)
    assert str(df["rental_date"].dtype).startswith("datetime64")


def test_answer_frame_survives_no_rows_and_dict_columns():
    df = view.answer_frame({"columns": [{"name": "total_revenue"}], "rows": []}, {"measures": ["total_revenue"]})
    assert df.empty and list(df.columns) == ["total_revenue"]


def test_describe_request():
    assert view.describe_request(MONTHLY) == "total_revenue, payment_count by rental_date:month"
    assert view.describe_request({"measures": ["rental_count"], "filters": [1, 2]}) == "rental_count (2 filters)"


def test_line_chart_per_measure_for_a_time_series():
    df = frame([{"rental_date": "2025-01-01", "total_revenue": 1, "payment_count": 1},
                {"rental_date": "2025-02-01", "total_revenue": 2, "payment_count": 2}], MONTHLY)
    plans = view.chart_plans(MONTHLY, df)
    assert [(p["kind"], p["x"], p["y"]) for p in plans] == [("line", "rental_date", "total_revenue"), ("line", "rental_date", "payment_count")]


def test_time_by_category_colours_the_lines_unless_too_many():
    req = {"measures": ["total_revenue"], "dimensions": ["rental_date:month", "payment_store"]}
    rows = [{"rental_date": f"2025-0{m}-01", "payment_store": s, "total_revenue": 1} for m in (1, 2) for s in (1, 2)]
    assert view.chart_plans(req, frame(rows, req))[0]["color"] == "payment_store"
    many = [{"rental_date": "2025-01-01", "payment_store": s, "total_revenue": 1} for s in range(20)]
    assert view.chart_plans(req, frame(many, req)) == []


def test_bar_chart_for_a_category_but_not_for_a_long_list():
    req = {"measures": ["rental_count"], "dimensions": ["category"]}
    few = frame([{"category": c, "rental_count": n} for n, c in enumerate("abc")], req)
    assert view.chart_plans(req, few)[0]["kind"] == "bar"
    lots = frame([{"category": str(n), "rental_count": n} for n in range(50)], req)
    assert view.chart_plans(req, lots) == []


def test_no_chart_for_a_single_number_or_empty_result():
    req = {"measures": ["total_revenue"]}
    assert view.chart_plans(req, frame([{"total_revenue": 5}], req)) == []
    assert view.chart_plans(MONTHLY, pd.DataFrame(columns=["rental_date", "total_revenue"])) == []


def test_csv_defangs_formulas_but_keeps_numbers():
    df = pd.DataFrame({"film_title": ["=HYPERLINK(\"http://x\")", "@sum", "Plain", "-2"], "n": [-1, 2, 3, 4]})
    text = view.csv_bytes(df).decode()
    assert "'=HYPERLINK" in text and "'@sum" in text and "'-2" in text and "Plain" in text
    assert text.splitlines()[1].endswith(",-1")


def test_follow_up_uses_the_latest_answered_request():
    ok = lambda m: {"result": {"status": "ready", "request": {"model": m}, "answer": {"rows": []}}}
    turns = [ok("a"), {"result": {"status": "not_supported"}}, {"error": {"message": "x"}}, ok("b"),
             {"result": {"status": "needs_clarification"}}]
    assert view.next_previous_request(turns) == {"model": "b"}
    assert view.next_previous_request([]) is None


def test_format_value_by_unit():
    assert view.format_value(None, "currency") == ""
    assert view.format_value(1234.5, "currency") == "1,234.50"
    assert view.format_value(1234, "count") == "1,234"
    assert view.format_value(1234.0, "count") == "1,234"
    assert view.format_value(12.3333, "count") == "12.33"
    assert view.format_value(5.0333, "days") == "5.03"
    assert view.format_value("x", None) == "x"
    assert view.format_value(7, "other") == "7"


def test_column_meta_and_display_frame_one_column_per_field():
    answer = {"columns": [
        {"name": "store", "role": "dimension", "label": "Store", "unit": None, "type": "string"},
        {"name": "rev", "role": "measure", "label": "Revenue", "unit": "currency", "type": "number"}]}
    meta = view.column_meta(answer)
    assert list(meta) == ["store", "rev"] and meta["rev"]["unit"] == "currency"
    df = pd.DataFrame([{"store": "A", "rev": 1234.5}, {"store": None, "rev": None}])
    out = view.display_frame(df, meta)
    assert list(out.columns) == ["Store", "Revenue"]
    assert out["Revenue"].tolist() == ["1,234.50", ""]
    assert out["Store"].tolist() == ["A", ""]


def test_chart_frame_sorts_line_plans_by_x():
    df = pd.DataFrame({"d": [3, 1, 2], "y": [30, 10, 20]})
    assert view.chart_frame(df, {"kind": "line", "x": "d", "y": "y"})["d"].tolist() == [1, 2, 3]
    assert view.chart_frame(df, {"kind": "bar", "x": "d", "y": "y"})["d"].tolist() == [3, 1, 2]
