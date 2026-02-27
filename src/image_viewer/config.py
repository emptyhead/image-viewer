"""Configuration loading for image-viewer.

Config file is stored at ~/.config/image-viewer/config.toml.
CLI arguments override config file values.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .models import AppConfig
from .sorting import SORT_NAMES

CONFIG_DIR = Path.home() / ".config" / "image-viewer"
CONFIG_FILE = CONFIG_DIR / "config.toml"

_DEFAULT_CONFIG_TOML = """\
[defaults]
recursive = true
sort = "unviewed"
thumbnail_size = 200
slideshow_time = 5.0
slideshow_order = "forward"
loop = false
fullscreen = true
rating_multiplier = 0.5

[appearance]
highlight_color = "#4a90d9"
unviewed_indicator = "border"
"""


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file. Uses stdlib tomllib (Python 3.11+) or tomli fallback."""
    if sys.version_info >= (3, 11):
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    else:
        try:
            import tomli  # type: ignore
            with open(path, "rb") as f:
                return tomli.load(f)
        except ImportError:
            # Manual minimal TOML parser for simple key=value pairs
            return _parse_simple_toml(path)


def _parse_simple_toml(path: Path) -> dict[str, Any]:
    """Very minimal TOML parser for simple key=value pairs under [sections]."""
    result: dict[str, Any] = {}
    current_section: dict[str, Any] = result
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section_name = line[1:-1].strip()
                current_section = {}
                result[section_name] = current_section
            elif "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Parse value types
                if value.lower() == "true":
                    current_section[key] = True
                elif value.lower() == "false":
                    current_section[key] = False
                elif value.startswith('"') and value.endswith('"'):
                    current_section[key] = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    current_section[key] = value[1:-1]
                else:
                    try:
                        if "." in value:
                            current_section[key] = float(value)
                        else:
                            current_section[key] = int(value)
                    except ValueError:
                        current_section[key] = value
    return result


def ensure_config_dir() -> None:
    """Create the config directory and default config file if they don't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(_DEFAULT_CONFIG_TOML)


def load_config() -> AppConfig:
    """Load configuration from the config file, returning an AppConfig with defaults."""
    ensure_config_dir()
    config = AppConfig()

    if CONFIG_FILE.exists():
        try:
            data = _load_toml(CONFIG_FILE)
        except Exception as e:
            print(f"Warning: Could not parse config file {CONFIG_FILE}: {e}", file=sys.stderr)
            return config

        defaults = data.get("defaults", {})
        appearance = data.get("appearance", {})

        if "recursive" in defaults:
            config.recursive = bool(defaults["recursive"])
        if "sort" in defaults:
            sort_val = str(defaults["sort"])
            if sort_val in SORT_NAMES:
                config.sort = sort_val
        if "thumbnail_size" in defaults:
            config.thumbnail_size = int(defaults["thumbnail_size"])
        if "slideshow_time" in defaults:
            config.slideshow_time = float(defaults["slideshow_time"])
        if "slideshow_order" in defaults:
            order = str(defaults["slideshow_order"])
            if order in ("forward", "backward", "random"):
                config.slideshow_order = order
        if "loop" in defaults:
            config.loop = bool(defaults["loop"])
        if "fullscreen" in defaults:
            config.fullscreen = bool(defaults["fullscreen"])
        if "rating_multiplier" in defaults:
            config.rating_multiplier = float(defaults["rating_multiplier"])

        if "highlight_color" in appearance:
            config.highlight_color = str(appearance["highlight_color"])
        if "unviewed_indicator" in appearance:
            indicator = str(appearance["unviewed_indicator"])
            if indicator in ("border", "dot", "none"):
                config.unviewed_indicator = indicator

    return config


def save_config(config: AppConfig) -> None:
    """Save configuration to the config file."""
    ensure_config_dir()
    
    # Build TOML content
    lines = [
        "[defaults]",
        f"recursive = {str(config.recursive).lower()}",
        f'sort = "{config.sort}"',
        f"thumbnail_size = {config.thumbnail_size}",
        f"slideshow_time = {config.slideshow_time}",
        f'slideshow_order = "{config.slideshow_order}"',
        f"loop = {str(config.loop).lower()}",
        f"fullscreen = {str(config.fullscreen).lower()}",
        f"rating_multiplier = {config.rating_multiplier}",
        "",
        "[appearance]",
        f'highlight_color = "{config.highlight_color}"',
        f'unviewed_indicator = "{config.unviewed_indicator}"',
        "",
    ]
    
    CONFIG_FILE.write_text("\n".join(lines))
