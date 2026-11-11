"""Validator tests that need no database (REQ-002, REQ-017, REQ-018, REQ-033; CONTRACT C-1, C-4, C-5.3).

Offline checks run on the real definitions and on mutated temporary copies. Live checks are
exercised against a fake connection only. Planted secrets are assembled at runtime so that this
file itself contains no credential literal (invariant I9).
"""
from __future__ import annotations

import json
import re
import shutil

import pytest

from semantic_layer import validator as v
from semantic_layer.loader import load_catalog
from semantic_layer.pg_client import ReadOnlyConnection

METRICS_YML = "metrics/flight_operations_metrics.yml"
REL_YML = "relationships/transport_relationships.yml"
MODEL_YML = "semantic_models/flight_operations.yml"
DATA_POLICY_YML = "policies/data_policy.yml"


# ------------------------------------------------------------------ baseline: real definitions pass
def test_references_pass_on_real_definitions(catalog, validation_dir):
    r = v.run_references(catalog, validation_dir)
    assert (r.name, r.status, r.offenders) == ("references", "pass", [])


def test_policies_pass_on_real_definitions(catalog):
    r = v.run_policies(catalog)
    assert r.status == "pass", r.offenders


def test_sql_readonly_pass_on_real_validation_sql(validation_dir):
    r = v.run_sql_readonly(validation_dir)
    assert r.status == "pass", r.offenders


def test_metric_catalog_and_versions_pass(catalog, metric_doc):
    cat, ver = v.run_metric_catalog(catalog, metric_doc)
    assert cat.status == "pass", cat.offenders
    assert ver.status == "pass", ver.offenders


# ------------------------------------------------------------------ mutated copies are rejected
def test_missing_mandatory_attribute_fails_metric_catalog(mutate_definitions, tmp_path):  # REQ-018
    d = mutate_definitions(METRICS_YML, '    business_owner: "<team_or_role>"\n', "", 1)
    cat = load_catalog(d)
    doc = tmp_path / "doc.md"
    from conftest import render_metric_doc
    doc.write_text(render_metric_doc(cat), encoding="utf-8")
    res, _ = v.run_metric_catalog(cat, doc)
    assert res.status == "fail"
    assert "flight_count:missing_attribute:business_owner" in res.offenders


def test_missing_reconciliation_baseline_fails(mutate_definitions, tmp_path):
    d = mutate_definitions(METRICS_YML, "reconciliation: {baseline: 544003,", "reconciliation: {baseline: null,")
    cat = load_catalog(d)
    from conftest import render_metric_doc
    doc = tmp_path / "doc.md"
    doc.write_text(render_metric_doc(cat), encoding="utf-8")
    res, _ = v.run_metric_catalog(cat, doc)
    assert res.status == "fail" and "flight_count:missing_attribute:reconciliation" in res.offenders


def test_metric_missing_from_doc_fails(catalog, tmp_path):
    from conftest import render_metric_doc
    doc = tmp_path / "doc.md"
    doc.write_text(render_metric_doc(catalog).replace("## cancellation_rate", "## cancellation_rate_x"))
    res, _ = v.run_metric_catalog(catalog, doc)
    assert res.status == "fail"
    assert "cancellation_rate:missing_heading" in res.offenders
    assert "cancellation_rate_x:heading_is_not_a_metric" in res.offenders


def test_doc_status_must_match_yaml(catalog, tmp_path):
    from conftest import render_metric_doc
    doc = tmp_path / "doc.md"
    text = render_metric_doc(catalog)
    doc.write_text(text.replace("- Status: draft", "- Status: validated", 1))
    res, _ = v.run_metric_catalog(catalog, doc)
    assert res.status == "fail" and any(":doc_mismatch:" in o for o in res.offenders)


def test_missing_doc_file_fails(catalog, tmp_path):
    res, _ = v.run_metric_catalog(catalog, tmp_path / "absent.md")
    assert res.status == "fail" and any(o.endswith(":file_not_found") for o in res.offenders)


def test_changed_expression_without_version_bump_fails(mutate_definitions, metric_doc):  # REQ-018/034
    d = mutate_definitions(METRICS_YML, "no NULL handling required.", "NULLs are handled differently now.")
    cat = load_catalog(d)
    _, ver = v.run_metric_catalog(cat, metric_doc)
    assert ver.status == "fail"
    assert "metric_changed_without_version_bump:flight_count" in ver.offenders


def test_broken_reference_fails_references_check(mutate_definitions, validation_dir):  # REQ-017
    d = mutate_definitions(REL_YML, "right: carriers.airline_id", "right: carriers.nope")
    r = v.run_references(load_catalog(d), validation_dir)
    assert r.status == "fail" and any("flight_to_carrier" in o for o in r.offenders)


def test_unknown_measure_fails_references_check(mutate_definitions, validation_dir):
    d = mutate_definitions(METRICS_YML, "measure: flight_count_base", "measure: ghost_base")
    r = v.run_references(load_catalog(d), validation_dir)
    assert r.status == "fail" and any("ghost_base" in o for o in r.offenders)


def test_prohibited_column_name_fails_policy_check(mutate_definitions):  # REQ-002
    d = mutate_definitions(MODEL_YML, "fl_date", "ssn_number")
    r = v.run_policies(load_catalog(d))
    assert r.status == "fail"
    assert any(o.startswith("prohibited_column:flight_facts.ssn_number") for o in r.offenders)


def test_prohibited_pattern_hits_other_column_names(mutate_definitions):
    d = mutate_definitions(MODEL_YML, "fl_date", "passenger_flag")
    r = v.run_policies(load_catalog(d))
    assert r.status == "fail" and any("passenger_flag" in o for o in r.offenders)


def test_weakened_query_policy_fails(mutate_definitions):
    d = mutate_definitions("policies/query_policy.yml", "max_result_rows: 1000", "max_result_rows: 100000")
    r = v.run_policies(load_catalog(d))
    assert r.status == "fail"


def test_write_statement_in_validation_sql_fails(tmp_path):
    sql = tmp_path / "bad.sql"
    sql.write_text("-- @check: evil\nDELETE FROM ontime.flight;\n")
    r = v.run_sql_readonly(tmp_path)
    assert r.status == "fail" and r.offenders


def test_nondeterministic_function_in_validation_sql_fails(tmp_path):
    (tmp_path / "bad.sql").write_text("-- @check: clock\nSELECT now() AS t;\n")
    r = v.run_sql_readonly(tmp_path)
    assert r.status == "fail"


def test_empty_sql_dir_fails(tmp_path):
    assert v.run_sql_readonly(tmp_path).status == "fail"


# ------------------------------------------------------------------ validation SQL parsing
def test_parse_validation_blocks(validation_dir):
    blocks = v.parse_validation_sql(validation_dir / "metric_reconciliation.sql")
    names = [b.name for b in blocks]
    assert "baseline_totals" in names
    base = next(b for b in blocks if b.name == "baseline_totals")
    assert base.expect["flights"] == 544003 and base.expect["on_time_arrival_rate"] == 0.7922
    assert base.tolerance == 0.0001
    assert base.severity in {"error", "warn"}


def test_every_validation_block_declares_an_expectation(validation_dir):
    for f in sorted(validation_dir.glob("*.sql")):
        for b in v.parse_validation_sql(f):
            assert b.expect is not None or b.expect_rows is not None, f"{f.name}:{b.name}"


def test_parse_rejects_missing_expectation(tmp_path):
    p = tmp_path / "x.sql"
    p.write_text("-- @check: nothing\nSELECT 1 AS a;\n")
    with pytest.raises(v.ValidationSqlError):
        v.parse_validation_sql(p)


# ------------------------------------------------------------------ secrets scan (C-5.3, I9)
def test_secret_scan_clean_on_this_project():
    from conftest import ROOT
    r = v.check_secrets(ROOT)
    assert r.status == "pass", r.offenders


def test_secret_scan_flags_dsn_and_env_assignments(tmp_path):
    (tmp_path / "a.txt").write_text("url = " + "postgres" + "ql://user:" + "hunter22" + "@host/db\n")
    (tmp_path / "b.sh").write_text("export " + "PGPASS" + "WORD=" + "hunter22\n")
    (tmp_path / "c.py").write_text("password = '" + "hunter22" + "'\n")
    r = v.check_secrets(tmp_path)
    assert r.status == "fail"
    assert {o.rsplit(":", 1)[1] for o in r.offenders} == {"S1", "S2", "S3"}
    assert "hunter22" not in json.dumps(r.details + r.offenders)  # values are never printed


def test_secret_scan_flags_literal_env_password_value(tmp_path):
    (tmp_path / "notes.md").write_text("the value is s3cr3t-value-xyz here\n")
    r = v.check_secrets(tmp_path, env_password="s3cr3t-value-xyz")
    assert r.status == "fail" and r.offenders == ["notes.md:1:S4"]
    assert "s3cr3t-value-xyz" not in json.dumps(r.offenders + r.details)


def test_secret_scan_placeholders_are_fine(tmp_path):
    (tmp_path / "README.md").write_text("export " + "PGPASSWORD" + "=<your password>\nread -rs " + "PGPASSWORD\n")
    assert v.check_secrets(tmp_path).status == "pass"


def test_secret_scan_skips_venv_and_binary(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "x.txt").write_text("password = '" + "hunter22" + "'\n")
    (tmp_path / "blob.bin").write_bytes(b"\0\1\2password = 'hunter22'")
    assert v.check_secrets(tmp_path).status == "pass"


def test_secret_scan_missing_root(tmp_path):
    assert v.check_secrets(tmp_path / "nope").status == "fail"


# ------------------------------------------------------------------ CLI exit codes (C-1.2)
def run_cli(capsys, *args):
    code = v.main(list(args))
    out = capsys.readouterr()
    return code, out


def test_cli_offline_checks_exit_0_with_json_report(capsys, definitions_dir, validation_dir):
    code, out = run_cli(capsys, "--definitions", str(definitions_dir), "--check-references",
                        "--check-policies", "--check-sql-readonly", "--sql-dir", str(validation_dir), "--json")
    assert code == 0
    report = json.loads(out.out)
    assert report["ok"] is True
    assert {c["name"] for c in report["checks"]} >= {"load_definitions", "policies", "references", "sql_readonly"}
    assert report["definitions_version"] == load_catalog(definitions_dir).definitions_version


def test_cli_text_output_has_pass_lines(capsys, definitions_dir):
    code, out = run_cli(capsys, "--definitions", str(definitions_dir), "--check-policies")
    assert code == 0 and "PASS" in out.out


def test_cli_failing_check_exit_1(capsys, mutate_definitions):
    d = mutate_definitions(MODEL_YML, "fl_date", "ssn_number")
    code, out = run_cli(capsys, "--definitions", str(d), "--check-policies")
    assert code == 1 and "FAIL" in out.out


def test_cli_bad_definitions_exit_1_or_2(capsys, mutate_definitions):
    d = mutate_definitions(METRICS_YML, "business_owner:", "bizz_owner:")
    code, _ = run_cli(capsys, "--definitions", str(d), "--check-policies")
    assert code in (1, 2)


def test_cli_no_checks_selected_exit_2(capsys, definitions_dir):
    code, out = run_cli(capsys, "--definitions", str(definitions_dir))
    assert code == 2 and "no checks selected" in out.err


def test_cli_live_check_requires_connection_flag(capsys, definitions_dir):
    code, out = run_cli(capsys, "--definitions", str(definitions_dir), "--check-live-schema")
    assert code == 2 and "--connection env" in out.err


def test_cli_live_check_without_password_exit_3_and_names_variable(capsys, definitions_dir, monkeypatch):
    for var in ("TRANSPORT_STATS_PG_USER", "TRANSPORT_STATS_PG_PASSWORD", "PGUSER", "PGPASSWORD"):
        monkeypatch.delenv(var, raising=False)
    code, out = run_cli(capsys, "--definitions", str(definitions_dir), "--check-live-schema",
                        "--connection", "env")
    assert code == 3 and "TRANSPORT_STATS_PG_PASSWORD" in out.err


# ------------------------------------------------------------------ live checks with a fake connection
def baseline_responder(catalog):
    """Answers each compiled reconciliation query with the metric's recorded baseline."""
    def respond(sql, params):
        name = re.findall(r'AS "([a-z_]+)"', sql)[-1]
        return [name], [(catalog.metrics[name].reconciliation.baseline,)]
    return respond


def test_reconcile_passes_when_live_equals_baseline(catalog, fake_connection):
    conn = ReadOnlyConnection(fake_connection(baseline_responder(catalog)))
    results = v.run_reconcile(conn, catalog)
    assert results and all(r.status in ("pass", "skip") for r in results), [(r.name, r.status, r.details) for r in results]


def test_reconcile_fails_outside_tolerance(catalog, fake_connection):
    base = baseline_responder(catalog)

    def drifted(sql, params):
        cols, rows = base(sql, params)
        if cols == ["flight_count"]:
            return cols, [(rows[0][0] + 1,)]
        return cols, rows

    results = v.run_reconcile(ReadOnlyConnection(fake_connection(drifted)), catalog)
    bad = [r for r in results if r.status == "fail"]
    assert [r.name for r in bad] == ["reconcile:flight_count"]
    assert "expected 544003" in bad[0].details[0]


def test_reconcile_uses_only_read_only_sql(catalog, fake_connection):
    fc = fake_connection(baseline_responder(catalog))
    v.run_reconcile(ReadOnlyConnection(fc), catalog)
    from semantic_layer.policy import assert_read_only_sql
    assert fc.executed
    for sql, _ in fc.executed:
        assert_read_only_sql(sql, catalog.query_policy)
    assert fc.rollbacks == len(fc.executed)


def test_validation_sql_runs_blocks_on_fake_connection(validation_dir, fake_connection):
    blocks = {b.sql.strip(): b for f in sorted(validation_dir.glob("*.sql")) for b in v.parse_validation_sql(f)}

    def respond(sql, params):
        b = blocks.get(sql.strip())
        if b is None:
            return ["x"], [(1,)]
        if b.expect is not None:
            return list(b.expect), [tuple(b.expect.values())]
        return ["x"], [(1,)] * (b.expect_rows or 0)

    results = v.run_validation_sql(ReadOnlyConnection(fake_connection(respond)), validation_dir)
    assert results
    failing = [(r.name, r.details) for r in results if r.status == "fail"]
    assert failing == [], failing


def test_validation_sql_database_error_is_reported_not_raised(validation_dir, fake_connection):
    conn = ReadOnlyConnection(fake_connection(fail_with=RuntimeError("relation does not exist")))
    results = v.run_validation_sql(conn, validation_dir)
    assert results and all(r.status in ("fail", "warn", "skip") for r in results)


def test_session_check_fails_when_not_read_only(fake_connection):
    def respond(sql, params):
        return (["transaction_read_only", "default_transaction_read_only", "statement_timeout",
                 "idle_in_transaction_session_timeout"], [("off", "off", "0", "0")])
    r = v.run_session_check(ReadOnlyConnection(fake_connection(respond)))
    assert r.status == "fail"


def test_session_check_passes_when_read_only(fake_connection):
    def respond(sql, params):
        return (["transaction_read_only", "default_transaction_read_only", "statement_timeout",
                 "idle_in_transaction_session_timeout"], [("on", "on", "30s", "1min")])
    r = v.run_session_check(ReadOnlyConnection(fake_connection(respond)))
    assert r.status == "pass", r.details
