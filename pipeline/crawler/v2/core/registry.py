"""학교 설정 로딩 + 전략 → 어댑터 디스패치."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "schools"

# strategy 이름 → 어댑터 모듈 (pipeline/crawler/v2/adapters/*.py)
STRATEGY_MODULES = {
    "k2web": "k2web",
    "rss": "rss",
    "mjujob": "mjujob",
    "international_php": "international_php",
    "gnuboard": "gnuboard",
    "intern": "intern",
    "pyxis": "pyxis",
    "ddingdong": "ddingdong",
    "bizdemo_php": "bizdemo_php",
    "arch": "arch",
}


def load_school(school_id):
    cfg = json.loads((CONFIG_DIR / f"{school_id}.json").read_text(encoding="utf-8"))
    extra_files = [cfg.get("department_registry")] + list(cfg.get("extra_registries", []))
    for name in extra_files:
        if name and (CONFIG_DIR / name).exists():
            extra = json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))
            cfg["channels"].extend(extra.get("channels", []))
    return cfg


def allowed_hosts(cfg):
    hosts = {"www.mju.ac.kr"}
    for ch in cfg["channels"]:
        if not ch.get("enabled"):
            continue
        host = ch.get("params", {}).get("host")
        if host:
            hosts.add(host)
        url = ch.get("params", {}).get("url")
        if url:
            from urllib.parse import urlparse

            hosts.add(urlparse(url).netloc)
    return hosts


def get_adapter(strategy):
    """어댑터 모듈의 collect 함수 반환. 미구현이면 None."""
    mod_name = STRATEGY_MODULES.get(strategy)
    if not mod_name:
        return None
    try:
        mod = importlib.import_module(f"pipeline.crawler.v2.adapters.{mod_name}")
    except ImportError:
        return None
    return getattr(mod, "collect", None)
