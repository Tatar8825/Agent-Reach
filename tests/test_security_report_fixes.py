"""Fixes from the 1.6.1 private security report."""

import os

import pytest

from agent_reach.channels import bilibili
from agent_reach.runtime import cdp, sessions, tools
from agent_reach.runtime.result import ReachError


@pytest.mark.parametrize("target", [
    "file:///etc/hosts#b23.tv/x",
    "http://127.0.0.1:8080/admin?b23.tv/x",
    "https://b23.tv.evil.example/x",
    "https://evil.example/b23.tv/x",
])
def test_b23_short_link_check_never_fetches_other_hosts(monkeypatch, target):
    opened = []
    monkeypatch.setattr(bilibili.urllib.request, "urlopen", lambda *a, **k: opened.append(a) or None)
    with pytest.raises(ReachError):
        bilibili._bvid(target, 1)
    assert opened == []


@pytest.mark.parametrize("target", ["b23.tv/abc", "https://b23.tv/abc"])
def test_b23_short_link_is_still_resolved(monkeypatch, target):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return "https://www.bilibili.com/video/BV1xx411c7mD"

    seen = []
    monkeypatch.setattr(bilibili.urllib.request, "urlopen", lambda req, **k: seen.append(req.full_url) or Resp())
    assert bilibili._bvid(target, 1) == "BV1xx411c7mD"
    assert seen == ["https://b23.tv/abc"]


def test_bili_cli_cannot_import_browser_cookie3(tmp_path, monkeypatch):
    import subprocess
    import sys

    monkeypatch.setenv("HOME", str(tmp_path))
    env = sessions.session_env("bili-cli")
    probe = "import browser_cookie3"
    result = subprocess.run([sys.executable, "-c", probe], env={**os.environ, **env}, capture_output=True, text=True)
    assert result.returncode != 0 and "own browser cookies" in result.stderr
    assert "PYTHONPATH" not in sessions.session_env("other-tool")


def test_cdp_only_connects_to_our_loopback_port(monkeypatch):
    monkeypatch.setattr(cdp, "_json", lambda path: {"webSocketDebuggerUrl": "ws://10.0.0.9:9999/devtools/browser/x"})
    seen = []

    def fake_connect(addr, timeout):
        seen.append(addr)
        raise OSError("stop")

    monkeypatch.setattr(cdp.socket, "create_connection", fake_connect)
    assert cdp.call("Browser.getVersion") is None
    ours = cdp.urlparse(cdp.CDP_URL)
    assert seen == [(ours.hostname, ours.port)]


def test_cloakbrowser_install_keeps_a_compatible_playwright(monkeypatch, tmp_path):
    tool = tools.TOOLS["cloakbrowser"]
    monkeypatch.setattr(tools, "tool_dir", lambda name: tmp_path / name)
    py = tmp_path / tool.name / "venv" / ("Scripts/python.exe" if tools._WIN else "bin/python")
    py.parent.mkdir(parents=True)
    py.write_text("")
    calls = []
    monkeypatch.setattr(tools, "_run", lambda argv, **kwargs: calls.append(argv) or "")
    tools._install_pip(tool)
    assert "cloakbrowser==0.6.3" in calls[0] and "playwright<1.65.0" in calls[0]


@pytest.mark.parametrize("playwright, reinstall", [("1.65.2", True), ("1.64.1", False)])
def test_existing_cloakbrowser_copy_is_repaired_only_when_broken(monkeypatch, tmp_path, playwright, reinstall):
    monkeypatch.setattr(tools, "tool_dir", lambda name: tmp_path / name)
    site = tmp_path / "cloakbrowser" / "venv" / ("Lib" if tools._WIN else "lib/python3.13") / "site-packages"
    (site / f"playwright-{playwright}.dist-info").mkdir(parents=True)
    monkeypatch.setattr(tools, "_private_bin", lambda tool, c: tmp_path)  # exists
    entry = {"owner": tools.OURS, "version": "0.6.3"}
    monkeypatch.setattr(tools.state, "load", lambda: {"tools": {"cloakbrowser": entry}})
    installed = []
    monkeypatch.setattr(tools, "_install", lambda tool, **k: installed.append(tool.name))
    monkeypatch.setattr(tools, "record", lambda name, version, **k: {"tool": name})
    monkeypatch.setattr(tools, "_private_version", lambda tool: "0.6.3")
    tools.ensure("cloakbrowser")
    assert installed == (["cloakbrowser"] if reinstall else [])


_FAKE_AIOHTTP = '''
class ClientSession:
    def __init__(self, *args, trust_env=False, **kwargs):
        self.trust_env = trust_env
'''

_FAKE_BILIBILI_API = '''
class _Settings:
    def __init__(self):
        self.values = {"proxy": ""}

    def get(self, key):
        return self.values[key]

    def set_proxy(self, proxy):
        self.values["proxy"] = proxy


request_settings = _Settings()
'''

_PROXY_PROBE = (
    "import aiohttp, bilibili_api;"
    "print(repr(bilibili_api.request_settings.get('proxy')));"
    "print(aiohttp.ClientSession().trust_env, aiohttp.ClientSession(trust_env=False).trust_env)"
)


def _run_bili_cli_python(tmp_path, monkeypatch, **proxy_env):
    import subprocess
    import sys

    fakes = tmp_path / "fakes"
    (fakes / "bilibili_api").mkdir(parents=True)
    (fakes / "aiohttp.py").write_text(_FAKE_AIOHTTP, encoding="utf-8")
    (fakes / "bilibili_api" / "__init__.py").write_text(_FAKE_BILIBILI_API, encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    env.update(sessions.session_env("bili-cli"))
    env["PYTHONPATH"] = os.pathsep.join([env["PYTHONPATH"], str(fakes)])
    env.update(proxy_env)
    result = subprocess.run([sys.executable, "-c", _PROXY_PROBE], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.split()


def test_bili_cli_uses_the_configured_proxy(tmp_path, monkeypatch):
    out = _run_bili_cli_python(tmp_path, monkeypatch, HTTPS_PROXY="http://127.0.0.1:7890")
    assert out == ["'http://127.0.0.1:7890'", "True", "False"]


def test_bili_cli_without_proxy_is_unchanged(tmp_path, monkeypatch):
    assert _run_bili_cli_python(tmp_path, monkeypatch) == ["''", "False", "False"]


def test_bili_cli_respects_no_proxy_for_bilibili(tmp_path, monkeypatch):
    out = _run_bili_cli_python(tmp_path, monkeypatch, HTTPS_PROXY="http://127.0.0.1:7890", NO_PROXY="bilibili.com")
    assert out[0] == "''"
