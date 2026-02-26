import os
import tomllib
from dataclasses import dataclass, field
from typing import List

DEFAULT_CONFIG_PATH = "/etc/sentinel/config.toml"
LOCK_FILE = "/var/run/sentinel.lock"
STATE_FILE = "/var/run/sentinel.state"
SOCKET_PATH = "/var/run/sentinel.sock"

DEFAULT_CONFIG = """
[inference]
service = "ollama"
restart_delay = 3

[watchdog]
poll_interval = 5
ignored_processes = ["Xorg", "gnome-shell", "plasmashell"]

[web]
enabled = true
port = 8765
"""

@dataclass
class InferenceConfig:
    service: str = "ollama"
    restart_delay: int = 3

@dataclass
class WatchdogConfig:
    poll_interval: int = 5
    ignored_processes: List[str] = field(default_factory=lambda: ["Xorg", "gnome-shell", "plasmashell"])

@dataclass
class WebConfig:
    enabled: bool = True
    port: int = 8765

@dataclass
class Config:
    inference: InferenceConfig = field(default_factory=InferenceConfig)
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)
    web: WebConfig = field(default_factory=WebConfig)

def load_config(path: str = DEFAULT_CONFIG_PATH) -> Config:
    if not os.path.exists(path):
        return Config()
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    cfg = Config()
    if "inference" in raw:
        cfg.inference = InferenceConfig(**raw["inference"])
    if "watchdog" in raw:
        cfg.watchdog = WatchdogConfig(**raw["watchdog"])
    if "web" in raw:
        cfg.web = WebConfig(**raw["web"])
    return cfg
