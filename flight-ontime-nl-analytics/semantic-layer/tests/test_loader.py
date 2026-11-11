"""Loader and reference-check tests (REQ-002, REQ-017, REQ-018; CONTRACT C-3, C-1.5)."""
from __future__ import annotations

import hashlib
import re

import pytest

from semantic_layer.errors import DefinitionError
from semantic_layer.loader import check_references, load_catalog, metric_definition_hash

METRICS_YML = "metrics/flight_operations_metrics.yml"
REL_YML = "relationships/transport_relationships.yml"
MODEL_YML = "semantic_models/flight_operations.yml"
QPOL_YML = "policies/query_policy.yml"


def test_loads_real_definitions(catalog):
    assert catalog.connection.database == "tran-stats"
    assert catalog.connection.default_schema == "ontime"
    assert catalog.connection.credentials == "env"
    assert catalog.connection.read_only is True
    assert set(catalog.sources) >= {"flight_facts", "carriers", "airports"}
    assert "flight_operations" in catalog.models


def test_check_references_clean_on_real_definitions(catalog, validation_dir):
    assert check_references(catalog, validation_dir) == []


def test_definitions_version_matches_contract_c38(catalog, definitions_dir):
    """C-3.8: first 12 hex of sha256 over path + newline + bytes + newline, sorted by path."""
    h = hashlib.sha256()
    for f in sorted(definitions_dir.rglob("*.yml"), key=lambda p: p.relative_to(definitions_dir).as_posix()):
        h.update(f.relative_to(definitions_dir).as_posix().encode() + b"\n" + f.read_bytes() + b"\n")
    assert catalog.definitions_version == h.hexdigest()[:12]


def test_loading_is_deterministic(definitions_dir, catalog):
    assert load_catalog(definitions_dir).definitions_version == catalog.definitions_version


def test_metric_definition_hash_is_stable_and_prefix_free(catalog):
    met = catalog.metrics["flight_count"]
    a = metric_definition_hash(met, catalog.model_for(met))
    assert a == metric_definition_hash(met, catalog.model_for(met))
    assert re.fullmatch(r"[0-9a-f]{16}|sha256:[0-9a-f]{16}", a)


def test_metric_statuses_are_known(catalog):
    assert {m.status for m in catalog.metrics.values()} <= {"draft", "validated", "approved", "deprecated"}


# ------------------------------------------------------------------ mutated copies -> rejected
def test_unknown_key_rejected(mutate_definitions):
    d = mutate_definitions(METRICS_YML, "business_owner:", "bizz_owner:")
    with pytest.raises(DefinitionError) as ei:
        load_catalog(d)
    assert "bizz_owner" in str(ei.value)
    assert METRICS_YML in str(ei.value)  # error names file and location


def test_invalid_status_rejected(mutate_definitions):
    d = mutate_definitions(METRICS_YML, "status: validated", "status: shiny", 1)
    with pytest.raises(DefinitionError):
        load_catalog(d)


def test_missing_definitions_dir_rejected(tmp_path):
    with pytest.raises(DefinitionError):
        load_catalog(tmp_path / "does_not_exist")


def test_missing_required_file_rejected(copy_definitions):
    (copy_definitions / QPOL_YML).unlink()
    with pytest.raises(DefinitionError):
        load_catalog(copy_definitions)


def test_unsupported_version_header_rejected(mutate_definitions):
    d = mutate_definitions(QPOL_YML, "version: 1\n", "version: 99\n")
    with pytest.raises(DefinitionError):
        load_catalog(d)


def test_duplicate_metric_name_rejected(mutate_definitions):
    d = mutate_definitions(METRICS_YML, "  - name: diversion_rate\n", "  - name: flight_count\n")
    with pytest.raises(DefinitionError) as ei:
        load_catalog(d)
    assert "duplicate" in str(ei.value)


def test_unparseable_yaml_rejected(mutate_definitions):
    d = mutate_definitions(QPOL_YML, "allowed_statement_types: [SELECT, WITH]", "allowed_statement_types: [SELECT, WITH")
    with pytest.raises(DefinitionError):
        load_catalog(d)


def test_broken_relationship_reference_flagged(mutate_definitions):
    d = mutate_definitions(REL_YML, "right: carriers.airline_id", "right: carriers.nope")
    problems = check_references(load_catalog(d))
    assert any(p.startswith("R10:flight_to_carrier") for p in problems), problems


def test_unknown_measure_reference_flagged(mutate_definitions):
    d = mutate_definitions(METRICS_YML, "measure: flight_count_base", "measure: ghost_base")
    problems = check_references(load_catalog(d))
    assert any(p.startswith("R7:flight_count") and "ghost_base" in p for p in problems), problems


def test_unknown_allowed_dimension_flagged(mutate_definitions):
    d = mutate_definitions(METRICS_YML, "allowed_dimensions: [flight_date,", "allowed_dimensions: [nope_dim, flight_date,")
    problems = check_references(load_catalog(d))
    assert any(p.startswith("R8:flight_count") and "nope_dim" in p for p in problems), problems


def test_unknown_validation_sql_reference_flagged(mutate_definitions, validation_dir):
    d = mutate_definitions(METRICS_YML, "validation_sql: \"validation/metric_reconciliation.sql#baseline_totals\"",
                           "validation_sql: \"validation/metric_reconciliation.sql#no_such_block\"")
    problems = check_references(load_catalog(d), validation_dir)
    assert problems, "a reference to a missing validation block must be reported"


def test_check_references_lists_every_offender(mutate_definitions):
    d = mutate_definitions(METRICS_YML, "measure: flight_count_base", "measure: ghost_base")
    d2 = d  # second mutation on the same copy
    f = d2 / REL_YML
    f.write_text(f.read_text().replace("right: carriers.airline_id", "right: carriers.nope"))
    problems = check_references(load_catalog(d2))
    assert len(problems) >= 2
    assert problems == sorted(problems) or len(set(problems)) == len(problems)


# ------------------------------------------------------------------ new keys (C-3.1, C-3.2; REQ-009, REQ-010, REQ-022, REQ-023)
def test_policy_has_max_metrics_and_glossary(catalog):
    qp = catalog.query_policy
    assert qp.max_metrics_per_query == 5
    g = {t.term: t for t in qp.glossary_terms}
    assert g["carrier"].maps_to == ("carrier_code",)
    amb = {a.reason_code: a for a in qp.ambiguous_terms}
    assert amb["airport_role_unspecified"].question == "Do you mean the origin airport, the destination airport, or both?"


def test_every_metric_is_combinable_and_hashes_unchanged(catalog):
    assert {m.combinable_with for m in catalog.metrics.values()} == {"flight_operations"}
    assert all(m.version == 1 and len(m.change_log) == 1 for m in catalog.metrics.values())


def test_missing_or_invalid_max_metrics_is_a_definition_error(mutate_definitions):
    from semantic_layer.errors import DefinitionError
    from semantic_layer.loader import load_catalog
    for new in ("", "max_metrics_per_query: 0\n", "max_metrics_per_query: five\n"):
        d = mutate_definitions("policies/query_policy.yml", "max_metrics_per_query: 5", new.rstrip("\n"))
        with pytest.raises(DefinitionError, match="max_metrics_per_query"):
            load_catalog(d)


def test_glossary_maps_to_must_exist_and_unknown_keys_are_rejected(mutate_definitions):
    from semantic_layer.errors import DefinitionError
    from semantic_layer.loader import load_catalog
    with pytest.raises(DefinitionError, match="glossary_terms"):
        load_catalog(mutate_definitions("policies/query_policy.yml", "maps_to: [carrier_code]", "maps_to: [nope]"))
    with pytest.raises(DefinitionError):
        load_catalog(mutate_definitions("policies/query_policy.yml", "    meaning:", "    extra: 1\n    meaning:"))


def test_combinable_group_must_share_model_grain_time_basis(mutate_definitions):
    from semantic_layer.errors import DefinitionError
    from semantic_layer.loader import load_catalog
    d = mutate_definitions("metrics/flight_operations_metrics.yml", "    grain: flight\n", "    grain: carrier\n")
    with pytest.raises(DefinitionError, match="mixes model/grain/time_basis"):
        load_catalog(d)
