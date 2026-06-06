"""How the sidecar subprocess is spawned.

White-box on purpose. The bug this pins produced no error, no log and no partial
output -- every save-reading tool simply hung until its 180 s timeout, and only when
the server was launched as a real MCP server over stdio. It is invisible to every
other test in this suite, because they call the functions directly from a process
whose stdin is a terminal.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.save import projection as proj


def test_sidecar_never_inherits_the_servers_stdin(monkeypatch):
    """The MCP server's stdin IS the client's JSON-RPC pipe.

    ``subprocess.run(capture_output=True)`` redirects stdout and stderr but leaves
    stdin inherited, so the sidecar was handed the protocol stream. Anything that
    touches it blocks for ever, and worse, could consume bytes the client sent to the
    server. Every save tool hung at exactly the 180 s ceiling.
    """
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=b'{"ok": true}', stderr=b"")

    monkeypatch.setattr(proj.subprocess, "run", fake_run)
    proj._run_sidecar(["--list", "somewhere"])

    assert seen.get("stdin") is subprocess.DEVNULL, (
        "sidecar must get DEVNULL, never the inherited MCP protocol pipe"
    )
    assert seen.get("capture_output") is True


def test_sidecar_runs_a_python_interpreter_not_the_console_script(monkeypatch):
    """sys.executable must be the interpreter. If it ever resolved to the
    satisfactory-mcp console script, the sidecar would spawn a SECOND MCP server that
    waits on stdin and emits no JSON -- the same silent hang by a different route."""
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=b"{}", stderr=b"")

    monkeypatch.setattr(proj.subprocess, "run", fake_run)
    proj._run_sidecar(["--header-only"])

    interpreter = seen["cmd"][0].lower()
    assert "python" in interpreter, interpreter
    assert "satisfactory-mcp" not in interpreter
    # ``-m``, not a constructed file path: the child resolves the extractor through the
    # same import machinery this process used, so it cannot run a stale copy.
    assert seen["cmd"][1:3] == ["-m", "satisfactory_mcp.core.saveio.extract"]
    assert seen["cmd"][3:] == ["--header-only"]


def test_the_child_is_pointed_at_this_checkouts_source(monkeypatch):
    """The child needs two packages -- the extractor and ``pioneersav`` -- and ``-m``
    only helps if it resolves them from the tree this process is running from.

    An inherited PYTHONPATH naming an older checkout would otherwise decide it, silently,
    and the symptom would be a projection built by code nobody is looking at.
    """
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"{}", stderr=b"")

    monkeypatch.setenv("PYTHONPATH", "C:/somewhere/else")
    monkeypatch.setattr(proj.subprocess, "run", fake_run)
    proj._run_sidecar(["--header-only"])

    env = seen["env"]
    src = Path(config.__file__).resolve().parent.parent
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(src)
    # Merged over the real environment, not a replacement for it: the extractor is an
    # ordinary Python program and wants the same PATH and TEMP as everyone else.
    assert "C:/somewhere/else" in env["PYTHONPATH"]
    assert set(os.environ) <= set(env)


def test_a_timeout_is_reported_as_a_save_error_not_a_hang(monkeypatch):
    """A stuck sidecar must surface as a readable message, so the next person sees
    'sidecar timed out' rather than a client-side timeout with no explanation."""

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(proj.subprocess, "run", fake_run)
    with pytest.raises(proj.SaveError, match="timed out"):
        proj._run_sidecar(["--list", "somewhere"])


def test_empty_sidecar_output_reports_the_exit_code_and_stderr(monkeypatch):
    """Silence from the sidecar is the failure mode that cost the most time to
    diagnose, so it must name the exit code and carry stderr through."""

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 9, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(proj.subprocess, "run", fake_run)
    with pytest.raises(proj.SaveError, match="exit 9"):
        proj._run_sidecar(["--list", "somewhere"])
