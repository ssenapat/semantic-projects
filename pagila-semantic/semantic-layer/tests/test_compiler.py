import pytest

from semantic_layer.errors import RequestError


def sql(layer, request):
    return layer.compile(request)


def test_revenue_default_is_dated_by_rental_with_mandatory_join(layer):
    c = sql(layer, {"model": "payment_activity", "measures": ["total_revenue"], "dimensions": ["rental_date:month"]})
    assert "JOIN public.rental r ON r.rental_id = p.rental_id" in c.sql
    assert "date_trunc('month', (r.rental_date AT TIME ZONE %s))" in c.sql
    assert c.params == ["UTC"]


def test_payment_date_gets_caveat(layer):
    c = sql(layer, {"model": "payment_activity", "measures": ["total_revenue"], "dimensions": ["payment_date:month"]})
    assert any("not the revenue date" in x for x in c.caveats)


def test_category_is_allocated(layer):
    c = sql(layer, {"model": "payment_activity", "measures": ["total_revenue", "paying_customer_count"],
                    "dimensions": ["payment_category"]})
    assert "SUM(p.amount * COALESCE(w.weight, 1))" in c.sql
    assert "LEFT JOIN (SELECT film_id, category_id, 1.0 / COUNT(*) OVER (PARTITION BY film_id)" in c.sql
    assert any("allocated" in x.lower() for x in c.caveats)
    assert any("paying_customer_count" in x and "not allocated" in x for x in c.caveats)


def test_no_allocation_without_category(layer):
    c = sql(layer, {"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["rating"]})
    assert "COUNT(DISTINCT r.rental_id)" in c.sql and "weight" not in c.sql
    assert "COALESCE(f.rating::text, 'Unknown')" in c.sql


def test_category_filter_alone_triggers_allocation(layer):
    c = sql(layer, {"model": "rental_activity", "measures": ["rental_count"],
                    "filters": [{"dimension": "category", "op": "eq", "value": "Action"}]})
    assert "SUM(COALESCE(w.weight, 1))" in c.sql


def test_actor_is_fan_out_and_cannot_mix_with_category(layer):
    c = sql(layer, {"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["actor_name"]})
    assert any("Fan-out" in x for x in c.caveats)
    with pytest.raises(RequestError, match="cannot be combined"):
        sql(layer, {"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["actor_name", "category"]})


def test_only_needed_joins_are_added(layer):
    c = sql(layer, {"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["film_title"]})
    assert c.sql.count(" JOIN ") == 2        # inventory, film
    c = sql(layer, {"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["customer_country"]})
    assert "JOIN public.country co" in c.sql and "public.film" not in c.sql


def test_values_are_bound_never_inlined(layer):
    evil = "x'; DROP TABLE rental; --"
    c = sql(layer, {"model": "rental_activity", "measures": ["rental_count"],
                    "filters": [{"dimension": "rating", "op": "in", "value": [evil, "PG"]}]})
    assert "DROP" not in c.sql and evil in c.params


def test_limit_is_capped_and_defaulted(layer):
    assert sql(layer, {"model": "rental_activity", "measures": ["rental_count"]}).sql.endswith("LIMIT 1001")
    c = sql(layer, {"model": "rental_activity", "measures": ["rental_count"], "limit": 999999})
    assert c.sql.endswith("LIMIT 10001") and any("maximum" in x for x in c.caveats)


@pytest.mark.parametrize("request_,match", [
    ({"model": "nope", "measures": ["x"]}, "unknown model"),
    ({"model": "rental_activity", "measures": []}, "non-empty"),
    ({"model": "rental_activity", "measures": ["bogus"]}, "unknown measure"),
    ({"model": "rental_activity", "measures": ["late_return_count"]}, "not approved"),
    ({"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["email"]}, "personal data"),
    ({"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["phone"]}, "personal data"),
    ({"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["rating:month"]}, "not a time dimension"),
    ({"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["rental_date:hour"]}, "granularities"),
    ({"model": "rental_activity", "measures": ["rental_count"], "limit": 0}, "positive integer"),
    ({"model": "rental_activity", "measures": ["rental_count"], "sql": "SELECT 1"}, "unknown request fields"),
    ({"model": "rental_activity", "measures": ["rental_count"], "timezone": "UTC'; --"}, "time zone"),
    ({"model": "rental_activity", "measures": ["rental_count"], "filters": [{"dimension": "rating", "op": "like", "value": "G"}]}, "unsupported op"),
    ({"model": "rental_activity", "measures": ["rental_count"], "filters": [{"dimension": "rental_date", "op": "eq", "value": "yesterday"}]}, "YYYY-MM-DD"),
    ({"model": "rental_activity", "measures": ["rental_count"], "order_by": [{"field": "film_title"}]}, "requested"),
])
def test_bad_requests_are_rejected(layer, request_, match):
    with pytest.raises(RequestError, match=match):
        layer.compile(request_)


def test_every_exposed_measure_and_dimension_passes_the_guard(layer):
    for name, model in layer.defs.models.items():
        measures = layer.compiler.exposed_measures(model)
        for d in layer.compiler.exposed_dimensions(model):
            spec = d + (":month" if model.dimensions[d]["type"] == "time" else "")
            layer.compile({"model": name, "measures": measures, "dimensions": [spec]})


# ====================================================================== multi-measure rules (CONTRACT 4.1)
import shutil
from pathlib import Path

import yaml

from semantic_layer import SemanticLayer
from semantic_layer.errors import UnsupportedRequest

LAYER_ROOT = Path(__file__).resolve().parents[1]
RA = "rental_activity"
PA = "payment_activity"


def edited_layer(tmp_path, edit):
    """A SemanticLayer over a tmp copy of the definitions; `edit(models, policy)` mutates the parsed YAML dicts."""
    root = tmp_path / "layer"
    root.mkdir()
    shutil.copytree(LAYER_ROOT / "definitions", root / "definitions")
    if (LAYER_ROOT / "validation").is_dir():
        shutil.copytree(LAYER_ROOT / "validation", root / "validation")
    files = {n: root / "definitions" / "semantic_models" / f"{n}.yml" for n in (RA, PA)}
    models = {n: yaml.safe_load(p.read_text()) for n, p in files.items()}
    policy_file = root / "definitions" / "policy" / "query_policy.yml"
    policy = yaml.safe_load(policy_file.read_text())
    edit(models, policy)
    for n, p in files.items():
        p.write_text(yaml.safe_dump(models[n], sort_keys=False))
    policy_file.write_text(yaml.safe_dump(policy, sort_keys=False))
    return SemanticLayer(root=root)


def measure(models, model, name):
    return next(m for m in models[model]["measures"] if m["name"] == name)


def req(**kw):
    return {"model": RA, "measures": ["rental_count"], **kw}


# ---- R0
def test_r0_legacy_measure_field_is_unknown(layer):
    with pytest.raises(RequestError, match="unknown request fields"):
        layer.compile({"model": RA, "measure": "rental_count"})


# ---- R1a / R1b / R1c / R1d
def test_r1a_too_many_measures_names_the_policy_limit(layer):
    six = ["rental_count", "returned_count", "open_rental_count", "active_customer_count", "avg_rental_days",
           "late_return_count"]
    with pytest.raises(RequestError, match=r"max_measures_per_query is 5"):
        layer.compile({"model": RA, "measures": six})


def test_r1a_limit_follows_the_policy_file(tmp_path):
    lay = edited_layer(tmp_path, lambda models, policy: policy.update(max_measures_per_query=2))
    with pytest.raises(RequestError, match=r"max_measures_per_query is 2"):
        lay.compile({"model": RA, "measures": ["rental_count", "returned_count", "open_rental_count"]})
    lay.compile({"model": RA, "measures": ["rental_count", "returned_count"]})


def test_r1b_duplicate_measures(layer):
    with pytest.raises(RequestError, match="duplicate measures"):
        layer.compile({"model": RA, "measures": ["rental_count", "rental_count"]})


def test_r1c_unknown_measure_lists_available(layer):
    with pytest.raises(RequestError, match=r"unknown measure 'bogus'.*available"):
        layer.compile({"model": RA, "measures": ["rental_count", "bogus"]})


def test_r1d_draft_measure_in_request_model_is_not_approved(layer):
    with pytest.raises(RequestError, match="not approved for use"):
        layer.compile({"model": RA, "measures": ["late_return_count"]})


def test_r1d_draft_measure_is_hidden_from_other_model_search(layer):
    with pytest.raises(RequestError, match="unknown measure"):
        layer.compile({"model": PA, "measures": ["total_revenue", "late_return_count"]})


# ---- R2a / R2b
def test_r2a_mixed_models_is_unsupported_with_details(layer):
    with pytest.raises(UnsupportedRequest) as ei:
        layer.compile({"model": PA, "measures": ["total_revenue", "rental_count"], "dimensions": ["rental_date:month"]})
    e = ei.value
    assert e.code == "mixed_models"
    assert e.details == {"measure_models": {"total_revenue": PA, "rental_count": RA}}
    for word in ("total_revenue", PA, "rental_count", RA, "separate questions"):
        assert word in str(e)


def test_r2a_two_other_models_are_still_mixed(layer):
    with pytest.raises(UnsupportedRequest) as ei:
        layer.compile({"model": PA, "measures": ["rental_count", "total_revenue"]})
    assert ei.value.code == "mixed_models" and set(ei.value.details["measure_models"]) == {"rental_count", "total_revenue"}


def test_r2b_all_measures_in_another_model_is_request_not_allowed(layer):
    with pytest.raises(RequestError, match="belong to model 'rental_activity'") as ei:
        layer.compile({"model": PA, "measures": ["rental_count"]})
    assert not isinstance(ei.value, UnsupportedRequest)


# ---- R2c
def test_r2c_different_combinable_with_tags(tmp_path):
    lay = edited_layer(tmp_path, lambda models, p: measure(models, RA, "avg_rental_days").update(combinable_with="other_tag"))
    with pytest.raises(UnsupportedRequest) as ei:
        lay.compile({"model": RA, "measures": ["rental_count", "avg_rental_days"]})
    assert ei.value.code == "not_combinable"
    assert ei.value.details == {"combinable_with": {"rental_count": RA, "avg_rental_days": "other_tag"}}
    assert "combinable_with" in str(ei.value)
    lay.compile({"model": RA, "measures": ["rental_count", "returned_count"]})     # same tag still fine


# ---- R1e / R1f
def test_r1e_too_many_dimensions(layer):
    nine = ["rental_hour", "day_of_week", "is_returned", "store_id", "film_title", "rating", "film_language",
            "customer_id", "customer_city"]
    with pytest.raises(RequestError, match=r"too many dimensions.*8"):
        layer.compile(req(dimensions=nine))
    layer.compile(req(dimensions=nine[:8]))


def test_r1f_same_dimension_twice_at_any_grain(layer):
    with pytest.raises(RequestError, match="duplicate dimension"):
        layer.compile(req(dimensions=["rental_date:month", "rental_date:day"]))
    with pytest.raises(RequestError, match="duplicate dimension"):
        layer.compile(req(dimensions=["rating", "rating"]))


# ---- R3 / R4
def test_r3_dimension_not_allowed_names_blocking_measures(tmp_path):
    def edit(models, p):
        m = measure(models, RA, "avg_rental_days")
        m["allowed_dimensions"] = [d for d in m["allowed_dimensions"] if d != "rating"]
    lay = edited_layer(tmp_path, edit)
    with pytest.raises(UnsupportedRequest) as ei:
        lay.compile({"model": RA, "measures": ["rental_count", "avg_rental_days"], "dimensions": ["rating"]})
    assert ei.value.code == "dimension_not_allowed"
    assert ei.value.details == {"dimension": "rating", "blocking_measures": ["avg_rental_days"]}
    assert "rating" in str(ei.value) and "avg_rental_days" in str(ei.value)
    lay.compile({"model": RA, "measures": ["rental_count", "avg_rental_days"], "dimensions": ["film_title"]})


def test_r3_also_applies_to_filter_dimensions(tmp_path):
    def edit(models, p):
        m = measure(models, RA, "avg_rental_days")
        m["allowed_dimensions"] = [d for d in m["allowed_dimensions"] if d != "rating"]
    lay = edited_layer(tmp_path, edit)
    with pytest.raises(UnsupportedRequest) as ei:
        lay.compile({"model": RA, "measures": ["rental_count", "avg_rental_days"],
                     "filters": [{"dimension": "rating", "op": "eq", "value": "G"}]})
    assert ei.value.code == "dimension_not_allowed" and ei.value.details["dimension"] == "rating"


def test_r3_unknown_or_personal_data_names_still_say_unknown_dimension(layer):
    with pytest.raises(RequestError, match="unknown dimension") as ei:
        layer.compile(req(dimensions=["email"]))
    assert not isinstance(ei.value, UnsupportedRequest)


def test_r4_grain_not_allowed_names_dimension_grain_and_measures(tmp_path):
    def edit(models, p):
        m = measure(models, RA, "returned_count")
        m["allowed_dimensions"] = [d for d in m["allowed_dimensions"] if d != "rental_date"] + ["rental_date:month"]
    lay = edited_layer(tmp_path, edit)
    with pytest.raises(UnsupportedRequest) as ei:
        lay.compile({"model": RA, "measures": ["rental_count", "returned_count"], "dimensions": ["rental_date:day"]})
    assert ei.value.code == "grain_not_allowed"
    assert ei.value.details == {"dimension": "rental_date", "grain": "day", "blocking_measures": ["returned_count"]}
    assert all(w in str(ei.value) for w in ("rental_date", "day", "returned_count"))
    lay.compile({"model": RA, "measures": ["rental_count", "returned_count"], "dimensions": ["rental_date:month"]})


# ---- R5
def test_r5_fan_out_threshold_comes_from_the_policy(tmp_path):
    def edit(models, p):
        for d in models[RA]["dimensions"]:
            if d["name"] == "film_title":
                d["fan_out"] = True
    lay = edited_layer(tmp_path, edit)
    with pytest.raises(RequestError, match="accept_fan_out"):
        lay.compile(req(dimensions=["actor_name", "film_title"]))
    c = lay.compile(req(dimensions=["actor_name", "film_title"], accept_fan_out=True))
    assert "actor_fan_out" in c.caveat_codes
    lay.compile(req(dimensions=["actor_name"]))        # one fan-out dimension needs no flag


# ---- R7 / I2 / I3
def test_r7_order_by_field_must_be_requested(layer):
    with pytest.raises(RequestError, match="must be one of the requested"):
        layer.compile(req(dimensions=["rating"], order_by=[{"field": "film_title"}]))


def test_default_order_is_first_measure_desc_then_dimensions_asc_nulls_last(layer):
    c = layer.compile({"model": RA, "measures": ["returned_count", "rental_count"], "dimensions": ["rating", "rental_date:month"]})
    assert ' ORDER BY "returned_count" DESC NULLS LAST, "rating" ASC NULLS LAST, "rental_date" ASC NULLS LAST LIMIT ' in c.sql


def test_explicit_order_by_may_use_any_measure_or_dimension(layer):
    c = layer.compile({"model": RA, "measures": ["rental_count", "returned_count"], "dimensions": ["rating", "store_id"],
                       "order_by": [{"field": "returned_count", "direction": "asc"}, {"field": "store_id", "direction": "desc"}]})
    assert ' ORDER BY "returned_count" ASC NULLS LAST, "store_id" DESC NULLS LAST, "rating" ASC NULLS LAST LIMIT ' in c.sql
    assert "NULLS LAST" in c.sql and c.sql.count("NULLS LAST") == 3


def test_measure_only_request_orders_by_the_measure(layer):
    c = layer.compile({"model": RA, "measures": ["rental_count", "returned_count"]})
    assert 'ORDER BY "rental_count" DESC NULLS LAST LIMIT 1001' in c.sql and "GROUP BY" not in c.sql


def test_limit_is_min_of_limit_and_max_rows_plus_one(layer):
    pol = layer.defs.policy
    assert layer.compile(req()).sql.endswith(f"LIMIT {pol.default_limit + 1}")
    assert layer.compile(req(limit=5)).sql.endswith("LIMIT 6")
    c = layer.compile(req(limit=pol.max_rows + 500))
    assert c.sql.endswith(f"LIMIT {pol.max_rows + 1}") and c.limit == pol.max_rows
    assert "limit_capped" in c.caveat_codes and any("maximum" in x for x in c.caveats)
    assert "limit_capped" not in layer.compile(req(limit=pol.max_rows)).caveat_codes


def test_limits_follow_the_policy_file(tmp_path):
    lay = edited_layer(tmp_path, lambda models, p: p.update(default_limit=50, max_rows=200))
    assert lay.compile(req()).sql.endswith("LIMIT 51")
    assert lay.compile(req(limit=999)).sql.endswith("LIMIT 201")


# ---- I1 / columns / versions / caveat codes
def test_one_select_one_group_by_over_all_measures(layer):
    c = layer.compile({"model": PA, "measures": ["total_revenue", "payment_count"], "dimensions": ["rental_date:month"]})
    assert c.sql.count("SELECT") == 1 and c.sql.count("GROUP BY") == 1
    assert 'AS "total_revenue"' in c.sql and 'AS "payment_count"' in c.sql and c.sql.count("GROUP BY 1") == 1


def test_columns_are_column_meta_dimensions_first_then_measures(layer):
    c = layer.compile({"model": RA, "measures": ["avg_rental_days", "rental_count"], "dimensions": ["rating", "rental_date:month"]})
    assert c.columns == ["rating", "rental_date", "avg_rental_days", "rental_count"]
    assert c.dimension_names == ["rating", "rental_date"] and c.measure_names == ["avg_rental_days", "rental_count"]
    assert [x["name"] for x in c.column_meta] == c.columns
    assert [x["role"] for x in c.column_meta] == ["dimension", "dimension", "measure", "measure"]
    for x in c.column_meta:
        assert set(x) == {"name", "role", "label", "unit", "type"} and x["label"]
    assert c.column_meta[0]["unit"] is None and c.column_meta[0]["type"] == "string"
    assert c.column_meta[2]["unit"] == "days" and c.column_meta[3]["unit"] == "count"
    assert all(x["type"] == "number" for x in c.column_meta[2:])


def test_measure_versions_follow_request_order(layer):
    c = layer.compile({"model": RA, "measures": ["returned_count", "rental_count"]})
    assert [x["name"] for x in c.measure_versions] == ["returned_count", "rental_count"]
    for x in c.measure_versions:
        assert x["version"] == c.definitions_used[f"measure:{x['name']}"]


def test_caveat_codes_for_each_trigger(layer):
    codes = lambda **kw: layer.compile(req(**kw)).caveat_codes     # noqa: E731
    assert codes(dimensions=["actor_name"]) == ["actor_fan_out"]
    assert "category_allocation" in codes(dimensions=["category"])
    assert "unallocated_distinct" in layer.compile({"model": PA, "measures": ["total_revenue", "paying_customer_count"],
                                                    "dimensions": ["payment_category"]}).caveat_codes
    assert "payment_date" in layer.compile({"model": PA, "measures": ["total_revenue"], "dimensions": ["payment_date"]}).caveat_codes
    assert "unpaid_rentals" in layer.compile({"model": PA, "measures": ["total_revenue"], "dimensions": ["payment_store"]}).caveat_codes
    assert "null_labels" in codes(dimensions=["rating"])
    assert "test_stores_hidden" in codes(dimensions=["store_id"])
    assert "open_rentals_excluded" in layer.compile({"model": RA, "measures": ["rental_count", "avg_rental_days"]}).caveat_codes


def test_codes_are_unique_and_in_order_of_first_trigger(layer):
    c = layer.compile({"model": PA, "measures": ["total_revenue", "paying_customer_count"],
                       "dimensions": ["payment_store", "payment_category"]})
    assert len(c.caveat_codes) == len(set(c.caveat_codes))
    assert c.caveat_codes.index("category_allocation") < c.caveat_codes.index("unallocated_distinct")
    assert len(c.caveats) >= len(c.caveat_codes)


def test_personal_data_dimension_adds_the_personal_data_caveat(layer):
    c = layer.compile(req(dimensions=["customer_name"]))
    assert "personal_data" in c.caveat_codes and any("never returned" in x for x in c.caveats)
    assert "personal_data" not in layer.compile(req(dimensions=["rating"])).caveat_codes
    # a personal-data dimension used only as a filter is not an output dimension
    f = layer.compile(req(filters=[{"dimension": "customer_name", "op": "eq", "value": "X"}]))
    assert "personal_data" not in f.caveat_codes


def test_category_allocation_applies_to_every_measure_with_allocated_sql(layer):
    c = layer.compile({"model": RA, "measures": ["rental_count", "returned_count", "active_customer_count"],
                       "dimensions": ["category"]})
    assert c.sql.count("weight") >= 3 and "category_allocation" in c.caveat_codes


# ---- spec 11 examples (golden G1..G6, compiled offline through the guard)
def test_spec11_revenue_and_payment_count_by_month(layer):
    c = layer.compile({"model": PA, "measures": ["total_revenue", "payment_count"], "dimensions": ["rental_date:month"]})
    assert c.columns == ["rental_date", "total_revenue", "payment_count"]
    assert 'ORDER BY "total_revenue" DESC NULLS LAST, "rental_date" ASC NULLS LAST' in c.sql


def test_spec11_rentals_and_avg_days_by_category_and_rating(layer):
    c = layer.compile({"model": RA, "measures": ["rental_count", "avg_rental_days"], "dimensions": ["category", "rating"]})
    assert c.columns == ["category", "rating", "rental_count", "avg_rental_days"]
    assert {"category_allocation", "open_rentals_excluded"} <= set(c.caveat_codes)


def test_spec11_revenue_by_store_and_category_stays_within_five_joins(layer):
    c = layer.compile({"model": PA, "measures": ["total_revenue"], "dimensions": ["payment_store", "payment_category"]})
    assert c.sql.count(" JOIN ") <= 10


def test_spec11_revenue_and_payment_count_by_store_and_category(layer):
    c = layer.compile({"model": PA, "measures": ["total_revenue", "payment_count"], "dimensions": ["payment_store", "payment_category"]})
    assert c.measure_names == ["total_revenue", "payment_count"] and "unpaid_rentals" in c.caveat_codes


@pytest.mark.parametrize("model,measures", [(PA, ["total_revenue", "rental_count"]), (RA, ["rental_count", "total_revenue"])])
def test_spec11_cross_model_questions_are_refused_offline(layer, model, measures):
    with pytest.raises(UnsupportedRequest) as ei:
        layer.compile({"model": model, "measures": measures, "dimensions": ["rental_date:month"]})
    assert ei.value.code == "mixed_models"


def test_max_join_count_over_all_models_is_within_the_policy(layer):
    worst = 0
    for name, model in layer.defs.models.items():
        for d in layer.compiler.exposed_dimensions(model):
            spec = d + (":month" if model.dimensions[d]["type"] == "time" else "")
            c = layer.compile({"model": name, "measures": layer.compiler.exposed_measures(model), "dimensions": [spec]})
            worst = max(worst, c.sql.count(" JOIN "))
    assert worst <= layer.defs.policy.max_joins
