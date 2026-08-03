"""The exec tools must return non-ASCII output verbatim.

These pin a bug that only showed up on machines whose paths or command output
contain non-ASCII: `subprocess.run(text=True)` decodes with the PARENT's locale
(cp1252 on Windows) while the child encodes with its own rules, so "Ramón" came
back as "RamÃ³n" or "Ram¢n". The tools now force UTF-8 on both sides, which is
why these assertions hold no matter what PYTHONIOENCODING the caller inherited.
"""

import shutil
import sys

import pytest

import tools


ACCENTS = "Ramón áéíóú ñÑ"
CJK = "你好"

# The ambient value leaks into the child's stdio encoding, so both settings must
# behave identically — that difference is exactly what used to break.
AMBIENT = [None, "utf-8", "utf-8:surrogateescape", "cp1252"]


@pytest.mark.parametrize("pythonioencoding", AMBIENT)
def test_run_python_returns_non_ascii_unchanged(monkeypatch, pythonioencoding):
    if pythonioencoding is None:
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    else:
        monkeypatch.setenv("PYTHONIOENCODING", pythonioencoding)
    out = tools.run_python(f'print("{ACCENTS} {CJK}")', allow_unsafe=True)
    assert out == f"{ACCENTS} {CJK}"


@pytest.mark.parametrize("pythonioencoding", AMBIENT)
def test_run_python_reports_a_non_ascii_cwd_unchanged(monkeypatch, tmp_path,
                                                     pythonioencoding):
    """The original report: a session rooted in a path with an accent.

    Left unparametrized this passes even unfixed, because with PYTHONIOENCODING
    unset both sides happen to agree on cp1252 — which is what made the failure
    look intermittent.
    """
    if pythonioencoding is None:
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    else:
        monkeypatch.setenv("PYTHONIOENCODING", pythonioencoding)
    project = tmp_path / "proyecto-ñ"
    project.mkdir()
    with tools.use_base_dir(project):
        out = tools.run_python("import os;print(os.getcwd())", allow_unsafe=True)
    assert out == str(project)


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell is Windows-only here")
def test_run_powershell_returns_non_ascii_unchanged():
    # PowerShell writes redirected output in the OEM codepage (cp850), where
    # "ó" is 0xa2 — read as cp1252 that is "¢", so this used to be "Ram¢n".
    out = tools.run_powershell(f'Write-Output "{ACCENTS}"', allow_unsafe=True)
    assert out == ACCENTS


@pytest.mark.skipif(
    not (shutil.which("bash") or shutil.which("zsh") or shutil.which("sh")),
    reason="no POSIX shell available",
)
def test_run_bash_returns_non_ascii_unchanged():
    out = tools.run_bash(f"printf '%s' '{ACCENTS}'", allow_unsafe=True)
    assert out == ACCENTS
