import shutil
from pathlib import Path

import pytest

from semantic_layer.errors import DefinitionError
from semantic_layer.loader import load_definitions

ROOT = Path(__file__).resolve().parents[1]


def test_definitions_load_and_are_exposed(layer):
    cat = layer.catalog()
    assert set(cat) == {"rental_activity", "payment_activity"}
    names = {m["name"] for m in cat["rental_activity"]["measures"]}
    assert "late_return_count" not in names          # proposed rule, not approved
    assert {"rental_count", "avg_rental_days"} <= names


def test_every_dimension_and_measure_has_metadata(layer):
    for model in layer.defs.models.values():
        for m in model.measures.values():
            for key in ("label", "description", "unit", "version", "validation", "change_log"):
                assert m.get(key) not in (None, ""), (model.name, m["name"], key)


def test_revenue_is_dated_by_rental_date(layer):
    assert layer.defs.models["payment_activity"].raw["time_dimension"] == "rental_date"
    assert "r" in layer.defs.models["payment_activity"].raw["always_joined"]


def test_no_blocked_column_is_exposed(layer):
    blocked = layer.defs.blocked_columns
    for model in layer.defs.models.values():
        for d in model.dimensions.values():
            for col in blocked:
                assert f".{col}" not in d["expression"], (d["name"], col)


@pytest.fixture
def broken(tmp_path):
    dest = tmp_path / "sl"
    shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns("results", "__pycache__", "*.egg-info", "tests"))
    return dest


def test_loader_rejects_undeclared_relationship(broken):
    p = broken / "definitions" / "semantic_models" / "rental_activity.yml"
    p.write_text(p.read_text().replace("relationship: rental_inventory", "relationship: nope", 1))
    with pytest.raises(DefinitionError, match="relationship"):
        load_definitions(broken)


def test_loader_rejects_unknown_table(broken):
    p = broken / "definitions" / "semantic_models" / "rental_activity.yml"
    p.write_text(p.read_text().replace("public.inventory,", "public.secret_table,", 1))
    with pytest.raises(DefinitionError, match="not a declared source"):
        load_definitions(broken)


def test_loader_rejects_missing_validation_check(broken):
    p = broken / "definitions" / "semantic_models" / "payment_activity.yml"
    p.write_text(p.read_text().replace("V02_revenue_total, X_revenue_by_rental_month_sums", "V99_missing", 1))
    with pytest.raises(DefinitionError, match="validation check"):
        load_definitions(broken)


# ---- policy (REQ-005) ----
import dataclasses

from semantic_layer import UnsupportedRequest
from semantic_layer.errors import RequestError

POLICY = Path("definitions") / "policy" / "query_policy.yml"
MODELS = ("rental_activity", "payment_activity")


def _edit(root, rel, old, new):
    p = root / rel
    text = p.read_text()
    assert old in text, old
    p.write_text(text.replace(old, new, 1))


def test_shipped_policy_values(layer):
    pol = layer.defs.policy if hasattr(layer, "defs") else load_definitions(ROOT).policy
    assert pol.as_dict() == {"max_measures_per_query": 5, "max_dimensions_per_query": 8, "default_limit": 1000,
                             "max_rows": 10000, "max_joins": 10, "max_fan_out_dimensions": 1, "version": "1.0.0"}
    with pytest.raises(dataclasses.FrozenInstanceError):
        pol.max_rows = 1


def test_policy_missing_file_rejected(broken):
    (broken / POLICY).unlink()
    with pytest.raises(DefinitionError, match="query_policy.yml"):
        load_definitions(broken)


@pytest.mark.parametrize("old,new", [
    ("max_rows: 10000", "max_rows: 0"),
    ("max_rows: 10000", "max_rows: ten"),
    ("max_rows: 10000", "max_rows: true"),
    ("max_joins: 10", "max_joins: 2.5"),
    ("max_fan_out_dimensions: 1", "max_fan_out_dimensions: -1"),
    ("default_limit: 1000", "default_limit: 20000"),
    ("max_measures_per_query: 5\n", ""),
])
def test_policy_bad_limits_rejected(broken, old, new):
    _edit(broken, POLICY, old, new)
    with pytest.raises(DefinitionError):
        load_definitions(broken)


# ---- measure tags (REQ-007, REQ-008, REQ-009) ----
def test_every_measure_declares_tag_and_allowed_dimensions(layer):
    defs = load_definitions(ROOT)
    assert set(defs.models) == set(MODELS)
    for name, model in defs.models.items():
        assert model.measures
        for m in model.measures.values():
            assert m["combinable_with"] == name
            assert set(m["allowed_dimensions"]) == set(model.dimensions)
            assert set(m["_allowed"]) == set(model.dimensions)
            assert all(v is None for v in m["_allowed"].values())


def _model_path(root, model):
    return root / "definitions" / "semantic_models" / f"{model}.yml"


@pytest.mark.parametrize("model", MODELS)
def test_missing_tag_rejected(broken, model):
    p = _model_path(broken, model)
    p.write_text(p.read_text().replace(f"    combinable_with: {model}\n", "", 1))
    with pytest.raises(DefinitionError, match=r"combinable_with") as e:
        load_definitions(broken)
    assert f"{model}.yml" in str(e.value)


def test_missing_allowed_dimensions_rejected(broken):
    p = _model_path(broken, "rental_activity")
    lines = [l for i, l in enumerate(p.read_text().split("\n"))
             if not (l.startswith("    allowed_dimensions:") and i > 60)]
    p.write_text("\n".join(lines))
    with pytest.raises(DefinitionError, match="allowed_dimensions"):
        load_definitions(broken)


@pytest.mark.parametrize("bad", ["Bad-Tag", "1x", ""])
def test_bad_tag_name_rejected(broken, bad):
    _edit(broken, "definitions/semantic_models/rental_activity.yml",
          "combinable_with: rental_activity", f'combinable_with: "{bad}"')
    with pytest.raises(DefinitionError, match="combinable_with"):
        load_definitions(broken)


def test_unknown_dimension_rejected(broken):
    _edit(broken, "definitions/semantic_models/rental_activity.yml",
          "allowed_dimensions: [rental_date,", "allowed_dimensions: [no_such_dim, rental_date,")
    with pytest.raises(DefinitionError, match="unknown dimension"):
        load_definitions(broken)


def test_grain_not_in_granularities_rejected(broken):
    _edit(broken, "definitions/semantic_models/rental_activity.yml",
          "allowed_dimensions: [rental_date,", "allowed_dimensions: [rental_date:fortnight,")
    with pytest.raises(DefinitionError, match="granularity"):
        load_definitions(broken)


def test_grain_on_categorical_rejected(broken):
    _edit(broken, "definitions/semantic_models/rental_activity.yml",
          "allowed_dimensions: [rental_date,", "allowed_dimensions: [store_id:month, rental_date,")
    with pytest.raises(DefinitionError, match="non-time"):
        load_definitions(broken)


def test_duplicate_and_mixed_grains_rejected(broken):
    f = "definitions/semantic_models/rental_activity.yml"
    _edit(broken, f, "allowed_dimensions: [rental_date,", "allowed_dimensions: [rental_date, rental_date,")
    with pytest.raises(DefinitionError, match="duplicates"):
        load_definitions(broken)


def test_mixed_plain_and_grain_rejected(broken):
    f = "definitions/semantic_models/rental_activity.yml"
    _edit(broken, f, "allowed_dimensions: [rental_date,", "allowed_dimensions: [rental_date:month, rental_date,")
    with pytest.raises(DefinitionError, match="mixes"):
        load_definitions(broken)


def test_valid_grain_is_parsed(broken):
    f = "definitions/semantic_models/rental_activity.yml"
    _edit(broken, f, "allowed_dimensions: [rental_date,", "allowed_dimensions: [rental_date:month, rental_date:week, ")
    # restore the remaining dimensions after the grained entries
    _edit(broken, f, "rental_date:week,  rental_hour,", "rental_date:week, rental_hour,")
    m = load_definitions(broken).models["rental_activity"].measures["rental_count"]
    assert m["_allowed"]["rental_date"] == frozenset({"month", "week"})
    assert m["_allowed"]["store_id"] is None


# ---- dimension metadata, versions (REQ-024) ----
def test_dimension_metadata_shipped_values():
    defs = load_definitions(ROOT)
    ints = {"rental_hour", "day_of_week", "store_id", "customer_id", "payment_customer_id", "payment_store"}
    pers = {"customer_name", "payment_customer", "payment_staff"}
    seen = set()
    for model in defs.models.values():
        for n, d in model.dimensions.items():
            seen.add(n)
            if n in ints:
                assert d["data_type"] == "integer"
            elif n == "is_returned":
                assert d["data_type"] == "boolean"
            else:
                assert d["data_type"] == ("timestamp" if d["type"] == "time" else "string")
            assert d["personal_data"] is (n in pers)
    assert ints | pers | {"is_returned"} <= seen


def test_bad_data_type_rejected(broken):
    _edit(broken, "definitions/semantic_models/rental_activity.yml", "data_type: boolean", "data_type: blob")
    with pytest.raises(DefinitionError, match="data_type"):
        load_definitions(broken)


def test_bad_personal_data_rejected(broken):
    _edit(broken, "definitions/semantic_models/rental_activity.yml", "personal_data: true", "personal_data: yes_please")
    with pytest.raises(DefinitionError, match="personal_data"):
        load_definitions(broken)


def test_versions_bumped_with_dated_change_log():
    defs = load_definitions(ROOT)
    for model in defs.models.values():
        assert model.version == "1.1.0"
        last = model.raw["change_log"][-1]
        assert str(last["version"]) == "1.1.0" and str(last["date"]) == "2026-11-10"
        for m in model.measures.values():
            want = "0.2.0" if m["name"] == "late_return_count" else "1.1.0"
            assert m["version"] == want
            assert m["change_log"][-1]["version"] == want and str(m["change_log"][-1]["date"]) == "2026-11-10"
    late = defs.models["rental_activity"].measures["late_return_count"]
    assert late["status"] == "draft" and late["expose"] is False


# ---- errors (CONTRACT 2.1) ----
def test_unsupported_request_error():
    e = UnsupportedRequest("msg", "mixed_models", {"measure_models": {"a": "m"}})
    assert isinstance(e, RequestError) and e.code == "mixed_models" and e.details == {"measure_models": {"a": "m"}}
    assert str(e) == "msg"
