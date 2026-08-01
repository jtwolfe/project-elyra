"""Unit tests: elyra.instrument.result harvest + payload helpers + redact."""

from __future__ import annotations

from pathlib import Path

from elyra.instrument.modes import Mode
from elyra.instrument.redact import (
    PLACEHOLDER,
    merge_known_values,
    redact_instrument_result,
    redact_string,
)
from elyra.instrument.result import (
    STATUS_NEEDS_HUMAN,
    collect_prompt_directed_artifacts,
    harvest_artifacts,
    make_error_payload,
    make_success_payload,
    parse_artifact_paths_from_text,
    parse_needs_human,
    plan_copies_from_parsed_paths,
    plan_copies_from_scratch,
    resolve_status_from_harvest,
    tool_result_dict,
)


def test_make_success_and_error_payloads() -> None:
    ok = make_success_payload(
        mode=Mode.DESIGN,
        run_id="r1",
        status="completed",
        summary="done",
        artifacts=[{"kind": "design_doc", "path": "/a/design.md"}],
    )
    assert ok["ok"] is True
    assert ok["mode"] == "design"
    assert ok["artifacts"][0]["kind"] == "design_doc"

    err = make_error_payload("missing_prompt", mode="prompt", hint="pass prompt")
    assert err["ok"] is False
    assert err["error_reason"] == "missing_prompt"
    assert err["hint"] == "pass prompt"

    tr = tool_result_dict(ok=False, payload=err, error_reason="missing_prompt")
    assert tr["ok"] is False
    assert tr["error_reason"] == "missing_prompt"


def test_parse_artifact_paths_from_text() -> None:
    text = (
        "Wrote design to /tmp/grok-1000/grok-design-doc-abc123.md and "
        "summary /var/tmp/grok-design-summary-xyz.md. "
        "Also /home/u/grok-review-1.md done."
    )
    paths = parse_artifact_paths_from_text(text)
    assert any("grok-design-doc-abc123.md" in p for p in paths)
    assert any("grok-design-summary-xyz.md" in p for p in paths)
    assert any("grok-review-1.md" in p for p in paths)


def test_parse_needs_human() -> None:
    text = """
## Summary
stuff

## NEEDS_HUMAN
- Should we use working or main?
- Confirm Graphite?

## Other
no
"""
    found, qs = parse_needs_human(text)
    assert found is True
    assert "Should we use working or main?" in qs
    assert any("Graphite" in q for q in qs)

    assert parse_needs_human("all good") == (False, [])


def test_harvest_prefers_prompt_directed(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    art.mkdir()
    design = art / "design.md"
    design.write_text("# Design\n", encoding="utf-8")
    (art / "summary.md").write_text("sum", encoding="utf-8")

    # stdout also mentions TMP paths — strategy 1 should win and be primary
    stdout = "see /tmp/grok-9/grok-design-doc-old.md"
    result = harvest_artifacts(
        mode=Mode.DESIGN,
        artifacts_dir=art,
        stdout_text=stdout,
        apply_copies=False,
    )
    assert result["primary_found"] is True
    assert result["error_reason"] is None
    kinds = {a["kind"] for a in result["artifacts"]}
    assert "design_doc" in kinds
    sources = {a["source"] for a in result["artifacts"]}
    assert "prompt_directed" in sources
    # Should not need pending copies when directed found
    assert not any(a.get("pending_copy") for a in result["artifacts"] if a["kind"] == "design_doc")


def test_harvest_parse_stdout_strategy(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    art.mkdir()
    # no files in artifacts yet
    stdout = "Document at /tmp/u/grok-design-doc-zz.md"
    result = harvest_artifacts(
        mode=Mode.DESIGN,
        artifacts_dir=art,
        stdout_text=stdout,
        apply_copies=False,
    )
    assert result["primary_found"] is True
    assert any(a.get("source") == "parsed_stdout" for a in result["artifacts"])
    plans = plan_copies_from_parsed_paths(
        parse_artifact_paths_from_text(stdout), art, Mode.DESIGN
    )
    assert plans[0]["dest_path"].endswith("design.md")


def test_harvest_scratch_fallback(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    art.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    src = scratch / "grok-design-doc-new.md"
    src.write_text("content", encoding="utf-8")
    result = harvest_artifacts(
        mode=Mode.DESIGN,
        artifacts_dir=art,
        stdout_text="nothing useful",
        scratch_candidates=[src],
        apply_copies=False,
    )
    assert result["primary_found"] is True
    assert any(a.get("source") == "scratch_scan" for a in result["artifacts"])

    plans = plan_copies_from_scratch([src], art, Mode.DESIGN)
    assert plans[0]["kind"] == "design_doc"
    assert plans[0]["dest_path"] == str(art / "design.md")


def test_harvest_artifact_missing(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    art.mkdir()
    result = harvest_artifacts(
        mode=Mode.DESIGN,
        artifacts_dir=art,
        stdout_text="no paths here",
    )
    assert result["primary_found"] is False
    assert result["error_reason"] == "artifact_missing"


def test_harvest_needs_human_without_artifact(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    art.mkdir()
    text = "## NEEDS_HUMAN\n- open Q\n"
    result = harvest_artifacts(
        mode=Mode.DESIGN,
        artifacts_dir=art,
        stdout_text=text,
    )
    assert result["needs_human"] is True
    assert result["open_questions"]
    # not artifact_missing when needs_human
    assert result["error_reason"] is None
    assert resolve_status_from_harvest(result) == STATUS_NEEDS_HUMAN


def test_collect_prompt_directed_skips_empty(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    art.mkdir()
    (art / "design.md").write_text("", encoding="utf-8")  # empty → skip
    assert collect_prompt_directed_artifacts(art, Mode.DESIGN) == []


def test_review_harvest_primary(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    art.mkdir()
    (art / "review.md").write_text("LGTM", encoding="utf-8")
    result = harvest_artifacts(mode=Mode.REVIEW, artifacts_dir=art)
    assert result["primary_found"] is True
    assert result["artifacts"][0]["kind"] == "review"


def test_redact_known_secrets() -> None:
    token = "xai-secret-token-abc123xyz"
    text = f"Bearer {token} in log"
    assert PLACEHOLDER in redact_string(text, [token])
    assert token not in redact_string(text, [token])

    values = merge_known_values(["a"], [token], None, [""])
    assert token in values
    assert "" not in values

    result = {
        "ok": True,
        "payload": {
            "summary": f"used {token}",
            "nested": {"t": token},
        },
        "error_reason": None,
    }
    red = redact_instrument_result(result, known_values=["other"], access_tokens=[token])
    assert token not in str(red)
    assert PLACEHOLDER in red["payload"]["summary"]


def test_apply_copies_true(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    art.mkdir()
    src = tmp_path / "grok-design-doc-x.md"
    src.write_text("# d\n", encoding="utf-8")
    result = harvest_artifacts(
        mode=Mode.DESIGN,
        artifacts_dir=art,
        stdout_text=f"path {src}",
        # strategy 2 only gives plans; apply_copies needs existing source files
        # parse may not get non-absolute without / — use absolute
        apply_copies=True,
    )
    # parse regex requires absolute path
    assert (art / "design.md").is_file() or result["primary_found"]
    # force via scratch apply
    art2 = tmp_path / "artifacts2"
    art2.mkdir()
    result2 = harvest_artifacts(
        mode=Mode.DESIGN,
        artifacts_dir=art2,
        scratch_candidates=[src],
        apply_copies=True,
    )
    assert (art2 / "design.md").read_text(encoding="utf-8") == "# d\n"
    assert result2["primary_found"] is True
