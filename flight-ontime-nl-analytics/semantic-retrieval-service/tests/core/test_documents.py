import json

from semantic_retrieval_service.app.services.document_normalizer import normalize_text


def test_one_document_per_type_and_unique_ids(loaded):
    ids = [d.document_id for d in loaded.documents]
    assert len(ids) == len(set(ids))
    types = {d.document_type for d in loaded.documents}
    assert types == {"metric", "dimension", "glossary", "ambiguity_rule", "unavailable_concept"}


def test_document_fields(loaded):
    d = next(d for d in loaded.documents if d.canonical_name == "cancelled_flight_count")
    assert d.domain == "flight_operations" == d.semantic_model
    assert d.status == "validated" and d.searchable_text
    assert "cancellations" in d.synonyms
    assert set(d.llm_safe_payload["allowed_dimensions"]) <= {x.canonical_name for x in loaded.documents if x.document_type == "dimension"}


def test_ambiguity_rule_actions(loaded):
    rules = {d.canonical_name: d.llm_safe_payload for d in loaded.documents if d.document_type in ("ambiguity_rule", "unavailable_concept")}
    assert rules["delay_direction_unspecified"]["required_action"] == "clarify"
    assert rules["airport_role_unspecified"]["required_action"] == "clarify"
    assert rules["refers_to_unavailable_data"]["required_action"] == "reject"
    assert rules["mapping_not_approved"]["required_action"] == "reject"
    assert all(a in {"departure_delay_rate", "arrival_delay_rate", "departure_delayed_flight_count", "arrival_delayed_flight_count"}
               for a in rules["delay_direction_unspecified"]["alternatives"])


def test_normalize_text():
    assert normalize_text("  Cancelled,  FLIGHTS!! ") == "cancelled flights"


def test_no_sql_in_safe_payloads(loaded):
    text = json.dumps([d.llm_safe_payload for d in loaded.documents]).lower()
    for bad in ("ontime", "select ", "count(*)", "fl_date", "join "):
        assert bad not in text
