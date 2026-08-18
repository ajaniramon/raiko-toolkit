"""Exec tools: model-chosen timeouts, partial output on kill, OS-filtered offer.

The old design hardcoded 20/25s and returned a bare "timed out" error that
dropped everything the command had already printed; TOOLS also offered both
shell tools on every OS, which confused small local models into calling the
wrong one. These tests pin the reworked contract.
"""

import os

import pytest

import tools
from tools import EXEC_DEFAULT_TIMEOUT, EXEC_MAX_TIMEOUT, TOOLS


def test_exec_timeout_clamps_and_defaults():
    assert tools._exec_timeout(None) == EXEC_DEFAULT_TIMEOUT
    assert tools._exec_timeout("nonsense") == EXEC_DEFAULT_TIMEOUT
    assert tools._exec_timeout(0) == 1
    assert tools._exec_timeout(-5) == 1
    assert tools._exec_timeout(60) == 60
    assert tools._exec_timeout(EXEC_MAX_TIMEOUT + 1) == EXEC_MAX_TIMEOUT
    assert tools._exec_timeout("45") == 45


def test_clip_output_keeps_head_and_tail():
    out = "START " + ("x" * 10000) + " END"
    clipped = tools._clip_output(out, limit=4000)
    assert len(clipped) < len(out)
    assert clipped.startswith("START ")
    assert clipped.endswith(" END")
    assert "chars truncated" in clipped


def test_run_python_timeout_returns_partial_output():
    code = 'print("partial-marker", flush=True)\nimport time; time.sleep(30)'
    out = tools.run_python(code, allow_unsafe=True, timeout=1)
    assert "partial-marker" in out
    assert "timed out after 1s" in out
    assert f"max {EXEC_MAX_TIMEOUT}s" in out


def test_run_python_timeout_without_output_says_so():
    out = tools.run_python("import time; time.sleep(30)", allow_unsafe=True, timeout=1)
    assert "timed out after 1s" in out
    assert "no output" in out


def test_run_python_model_timeout_is_honored_when_generous():
    out = tools.run_python(
        'import time; time.sleep(2); print("made-it")', allow_unsafe=True, timeout=20)
    assert out == "made-it"


def test_call_tool_forwards_timeout():
    out = tools.call_tool(
        "run_python", {"code": 'import time; time.sleep(30)', "timeout": 1})
    assert "timed out after 1s" in out


@pytest.mark.skipif(os.name != "nt", reason="Windows-only shell offer")
def test_tools_offer_only_powershell_on_windows():
    names = {t["function"]["name"] for t in TOOLS}
    assert "run_powershell" in names
    assert "run_bash" not in names


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only shell offer")
def test_tools_offer_only_bash_on_posix():
    names = {t["function"]["name"] for t in TOOLS}
    assert "run_bash" in names
    assert "run_powershell" not in names


def test_both_shells_stay_dispatchable():
    # Bench tasks and old transcripts reference both names; only the OFFER is
    # filtered by OS, the dispatch table is not.
    assert "run_powershell" in tools.DISPATCH
    assert "run_bash" in tools.DISPATCH


@pytest.mark.skipif(os.name != "nt", reason="PowerShell is Windows-only here")
def test_run_powershell_timeout_returns_partial_output():
    out = tools.run_powershell(
        'Write-Output "ps-partial"; Start-Sleep -Seconds 30', allow_unsafe=True, timeout=2)
    assert "ps-partial" in out
    assert "timed out after 2s" in out
