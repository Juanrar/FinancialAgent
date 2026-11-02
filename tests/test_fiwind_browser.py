import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from financial_agent.fiwind.browser import (
    AgentBrowser,
    BrowserCommandError,
    BrowserTimeout,
    find_binary,
)


class FakeRun:
    def __init__(self, output="", returncode=0, raises=None):
        self.output, self.returncode, self.raises, self.calls = output, returncode, raises, []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.raises:
            raise self.raises
        kwargs["stdout"].write(self.output.encode("utf-8"))
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=self.returncode)


def _browser(monkeypatch, fake, headed=False):
    monkeypatch.setattr(subprocess, "run", fake)
    return AgentBrowser("ab", "fiwind", Path("perfil"), headed=headed)


def test_every_command_carries_profile_in_env(monkeypatch):
    monkeypatch.setenv("AGENT_BROWSER_EXECUTABLE_PATH", "/usr/bin/chromium")
    fake = FakeRun("https://panel")
    browser = _browser(monkeypatch, fake)
    browser.open("https://x")
    assert browser.run("get", "url") == "https://panel"
    assert [cmd for cmd, _ in fake.calls] == [
        ["ab", "--session", "fiwind", "open", "https://x"],
        ["ab", "--session", "fiwind", "get", "url"],
    ]
    for _, kwargs in fake.calls:
        env = kwargs["env"]
        assert env["AGENT_BROWSER_PROFILE"] == str(Path("perfil"))
        assert env["AGENT_BROWSER_EXECUTABLE_PATH"] == "/usr/bin/chromium"
        assert "AGENT_BROWSER_HEADED" not in env


def test_headed_goes_on_every_command(monkeypatch):
    fake = FakeRun()
    browser = _browser(monkeypatch, fake, headed=True)
    browser.open("https://x")
    browser.run("get", "url")
    assert [kwargs["env"]["AGENT_BROWSER_HEADED"] for _, kwargs in fake.calls] == ["1", "1"]


def test_headless_drops_inherited_headed(monkeypatch):
    monkeypatch.setenv("AGENT_BROWSER_HEADED", "1")
    fake = FakeRun()
    _browser(monkeypatch, fake).run("get", "url")
    assert "AGENT_BROWSER_HEADED" not in fake.calls[0][1]["env"]


def test_output_goes_to_file_not_pipe(monkeypatch):
    fake = FakeRun()
    _browser(monkeypatch, fake).run("get", "url")
    kwargs = fake.calls[0][1]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is not subprocess.PIPE and hasattr(kwargs["stdout"], "fileno")
    assert kwargs["stderr"] is subprocess.STDOUT and kwargs["timeout"] == 60


def test_daemon_warning_is_filtered(monkeypatch):
    out = "⚠ --profile ignored: daemon already running. Use 'agent-browser close' first.\nhttps://p"
    assert _browser(monkeypatch, FakeRun(out)).run("get", "url") == "https://p"


def test_nonzero_raises(monkeypatch):
    fake = FakeRun("✗ Element not found.", returncode=1)
    with pytest.raises(BrowserCommandError) as info:
        _browser(monkeypatch, fake).run("find", "role", "button", "click")
    assert info.value.command == ("find", "role", "button", "click")
    assert "Element not found" in info.value.output


def test_timeout_raises(monkeypatch):
    fake = FakeRun(raises=subprocess.TimeoutExpired("ab", 60))
    with pytest.raises(BrowserTimeout):
        _browser(monkeypatch, fake).run("wait", "--load", "networkidle")


def test_close_never_raises(monkeypatch):
    _browser(monkeypatch, FakeRun("x", returncode=1)).close()
    _browser(monkeypatch, FakeRun(raises=subprocess.TimeoutExpired("ab", 60))).close()


def test_find_binary(monkeypatch, tmp_path):
    exe = tmp_path / "agent-browser.CMD"
    exe.write_text("")
    assert find_binary(str(exe)) == str(exe)
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    assert find_binary(None) == "/bin/agent-browser"


def test_find_binary_configured_but_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    assert find_binary(str(tmp_path / "no-existe.CMD")) is None
