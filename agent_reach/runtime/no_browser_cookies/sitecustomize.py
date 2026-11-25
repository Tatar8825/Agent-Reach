"""Proxy support for bili-cli, loaded from its PYTHONPATH (see sessions.session_env).

bili-cli talks to Bilibili with aiohttp in two ways, and neither honours the
user's HTTPS_PROXY / HTTP_PROXY:

- bilibili-api-python passes ``proxy=""`` on every request, and aiohttp then
  skips the proxy environment even though the session has ``trust_env=True``.
- bili-cli's own requests (subtitles, comments) open ``aiohttp.ClientSession``
  without ``trust_env``, so aiohttp ignores the proxy environment entirely.

Behind a proxy-only network every bili-cli action therefore fails with a DNS
or connection error. When a proxy is configured we hand it to
bilibili-api-python and make new aiohttp sessions read the proxy environment,
which also keeps NO_PROXY working. Without a proxy nothing changes. Any
failure here is swallowed: bili-cli must start exactly as before.
"""


def _proxy_from_env():
    import urllib.request

    proxies = urllib.request.getproxies()
    return proxies.get("https") or proxies.get("http")


def _trust_env_by_default(aiohttp) -> None:
    session = aiohttp.ClientSession
    if getattr(session, "_agent_reach_trust_env", False):
        return
    original = session.__init__

    def __init__(self, *args, trust_env=True, **kwargs):
        original(self, *args, trust_env=trust_env, **kwargs)

    session.__init__ = __init__
    setattr(session, "_agent_reach_trust_env", True)


def _apply() -> None:
    import urllib.request

    proxy = _proxy_from_env()
    if not proxy:
        return
    try:
        import aiohttp
    except ImportError:
        return
    _trust_env_by_default(aiohttp)
    if urllib.request.proxy_bypass("api.bilibili.com"):
        return
    try:
        from bilibili_api import request_settings
    except Exception:
        return
    if not request_settings.get("proxy"):
        request_settings.set_proxy(proxy)


try:
    _apply()
except Exception:
    pass
