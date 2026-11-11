"""CONTRACT section 8 as pytest: response shapes (additionalProperties false) and the C-4.1 limit agreement."""
from __future__ import annotations

import copy

import contract_check as cc


def test_static_models_and_paths_conform():
    assert cc.check_static() == []


def test_responses_match_contract_shapes():
    assert cc.check_responses() == []


def test_max_metrics_per_query_values_agree():
    """C-4.1: parser, query, retriever and the catalog policy all say 5; the compiler has no MAX_METRICS."""
    assert cc.check_max_metrics() == []


def test_shapes_close_every_object():
    def objects(node, path="root"):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                yield path, node
            for k, v in node.items():
                yield from objects(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from objects(v, f"{path}[{i}]")

    for shape in (*cc.PARSE_SHAPES.values(), *cc.QUERY_SHAPES.values(), cc.ASK_SHAPE):
        for path, obj in objects(shape):
            assert obj["additionalProperties"] is False, path


def test_validator_rejects_an_extra_field_and_the_legacy_field():
    good = {"outcome": "semantic_request", "catalog_version": "v", "confidence": 0.9, "clarification": None,
            "unsupported": None, "semantic_request": {"metrics": ["flight_count"]}}
    assert cc._validate("good", good, cc.PARSE_SHAPES["semantic_request"]) == []
    extra = copy.deepcopy(good)
    extra["semantic_request"]["surprise"] = 1
    assert cc._validate("extra", extra, cc.PARSE_SHAPES["semantic_request"])
    legacy = copy.deepcopy(good)
    legacy["semantic_request"] = {"metric": "flight_count"}  # legacy-rejection
    assert cc._validate("legacy", legacy, cc.PARSE_SHAPES["semantic_request"])
    too_many = copy.deepcopy(good)
    too_many["semantic_request"]["metrics"] = ["a", "b", "c", "d", "e", "f"]
    assert cc._validate("six", too_many, cc.PARSE_SHAPES["semantic_request"])
