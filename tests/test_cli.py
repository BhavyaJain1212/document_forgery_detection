"""Tests for the CLI entry point (output rules + exit codes).

Reuses the inline fixture builders from test_report.py so the CLI is exercised
end-to-end against real incremental-update PDFs.
"""

from __future__ import annotations

import json

from pdf_forgery.cli import main

from test_report import _negative, _positive


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# --------------------------------------------------------------------------- #
# Single file
# --------------------------------------------------------------------------- #

def test_no_flags_prints_summary(tmp_path, capsys):
    p = _write(tmp_path, "pos.pdf", _positive())
    rc = main([str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "HIGH" in out
    assert "before: 5,000" in out and "after:  50,000" in out


def test_json_to_stdout(tmp_path, capsys):
    p = _write(tmp_path, "pos.pdf", _positive())
    rc = main([str(p), "--json", "-"])
    out = capsys.readouterr().out
    assert rc == 0
    obj = json.loads(out)
    assert obj["scoring"]["tier"] == "high"


def test_json_to_file_suppresses_summary(tmp_path, capsys):
    p = _write(tmp_path, "pos.pdf", _positive())
    out_path = tmp_path / "report.json"
    rc = main([str(p), "--json", str(out_path)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "HIGH" not in captured.out  # summary suppressed on stdout
    obj = json.loads(out_path.read_text())
    assert obj["scoring"]["tier"] == "high"


def test_summary_flag_forces_summary_with_json(tmp_path, capsys):
    p = _write(tmp_path, "pos.pdf", _positive())
    out_path = tmp_path / "report.json"
    rc = main([str(p), "--json", str(out_path), "--summary"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "HIGH" in out
    assert out_path.exists()


# --------------------------------------------------------------------------- #
# Batch (directory)
# --------------------------------------------------------------------------- #

def test_directory_batch_json_is_array(tmp_path, capsys):
    _write(tmp_path, "a_pos.pdf", _positive())
    _write(tmp_path, "b_neg.pdf", _negative())
    rc = main([str(tmp_path), "--json", "-"])
    out = capsys.readouterr().out
    assert rc == 0
    arr = json.loads(out)
    assert isinstance(arr, list) and len(arr) == 2
    tiers = sorted(r["scoring"]["tier"] for r in arr)
    assert tiers == ["high", "inconclusive"]


def test_directory_no_recursion(tmp_path, capsys):
    _write(tmp_path, "top.pdf", _positive())
    sub = tmp_path / "sub"
    sub.mkdir()
    _write(sub, "nested.pdf", _positive())
    rc = main([str(tmp_path), "--json", "-"])
    out = capsys.readouterr().out
    assert rc == 0
    arr = json.loads(out)
    assert len(arr) == 1  # nested.pdf is not picked up


# --------------------------------------------------------------------------- #
# Exit codes (run success only, never the verdict)
# --------------------------------------------------------------------------- #

def test_high_verdict_still_exits_zero(tmp_path, capsys):
    p = _write(tmp_path, "pos.pdf", _positive())
    rc = main([str(p)])
    capsys.readouterr()
    assert rc == 0  # HIGH confidence does NOT change the exit code


def test_missing_path_exits_two(tmp_path, capsys):
    rc = main([str(tmp_path / "nope.pdf")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "no such path" in err


def test_empty_directory_exits_two(tmp_path, capsys):
    rc = main([str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "no top-level" in err
