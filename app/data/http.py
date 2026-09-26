"""统一的 HTTP 抓取入口: 带"代理不通自动改直连"兜底。

背景: HTTP_PROXY/HTTPS_PROXY 若是用户级环境变量(国内开发机很常见), 所有请求都会
走代理; 而代理软件没开、或分流规则没覆盖该域名时请求会失败。此时改直连往往能成功
(例如在莫斯科的机器根本不需要代理)。反之, 本来就该走代理的环境不受影响 ——
首次请求就成功, 不会触发重试。

只做一件事: 首次按环境请求; 失败且环境里确实配了代理时, 禁用代理重试一次。
"""
import logging
import os
import ssl
import urllib.request

import requests

log = logging.getLogger(__name__)

_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                   "http_proxy", "https_proxy", "all_proxy")

# requests 里"显式不走代理"的写法
_NO_PROXY = {"http": None, "https": None}


def _ssl_context():
    """构造 HTTPS 校验上下文, 优先用 certifi 的根证书包。

    必要性: python.org 安装的 macOS Python 若没跑过 post-install 的
    "Install Certificates.command", 标准库 urllib/ssl 的系统证书库为空,
    所有 HTTPS 请求都会报 CERTIFICATE_VERIFY_FAILED("self-signed
    certificate in certificate chain")。requests 因为自带 certifi 不受影响,
    会造成"官价能抓、MOEX 盘价(urllib)全挂"的分裂。certifi 已随 requests
    装进 venv, 让 urllib 显式用它即可, 不依赖用户系统配置。
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:      # noqa: BLE001  certifi 缺失时退回系统默认库
        return ssl.create_default_context()


def has_proxy_env() -> bool:
    """环境里是否配了代理。没配的话重试没有意义, 不做多余请求。"""
    return any(os.environ.get(k) for k in _PROXY_ENV_KEYS)


def get(url, timeout, session=None, **kwargs):
    """requests 版 GET: 代理失败时自动禁用代理重试一次。"""
    who = session or requests
    try:
        return who.get(url, timeout=timeout, **kwargs)
    except Exception as e:      # noqa: BLE001
        if not has_proxy_env():
            raise
        log.warning("带代理请求失败, 改直连重试一次 (%s): %s",
                    url.split("?")[0], e)
        return who.get(url, timeout=timeout, proxies=_NO_PROXY, **kwargs)


def open_url(url, timeout, headers=None):
    """urllib 版: 代理失败时自动禁用代理重试一次, 返回 response 对象。"""
    ctx = _ssl_context()
    try:
        return urllib.request.urlopen(_req(url, headers), timeout=timeout,
                                      context=ctx)
    except Exception as e:      # noqa: BLE001
        if not has_proxy_env():
            raise
        log.warning("带代理请求失败, 改直连重试一次 (%s): %s",
                    url.split("?")[0], e)
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx),
            urllib.request.ProxyHandler({}))
        return opener.open(_req(url, headers), timeout=timeout)


def _req(url, headers):
    return urllib.request.Request(
        url, headers=headers or {"User-Agent": "Mozilla/5.0"})
