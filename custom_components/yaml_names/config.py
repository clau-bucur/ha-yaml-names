"""Validate names and load YAML using Home Assistant's include/package loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import probatio as vol

from homeassistant import config as conf_util
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util.yaml import Secrets

from .const import CUSTOMIZE, CUSTOMIZE_DOMAIN, CUSTOMIZE_GLOB, DOMAIN

NAME_SCHEMA = vol.Schema({vol.Required("name"): vol.Any(str, None)})
_RULES_SCHEMA = vol.Schema(
    {
        vol.Optional(CUSTOMIZE): {cv.entity_id: NAME_SCHEMA},
        vol.Optional(CUSTOMIZE_DOMAIN): {cv.slug: NAME_SCHEMA},
        vol.Optional(CUSTOMIZE_GLOB): {vol.All(str, vol.Length(min=1)): NAME_SCHEMA},
        cv.entity_id: vol.Any(str, None, NAME_SCHEMA),
    }
)


def normalize_rules(config: dict[str, Any]) -> dict[str, Any]:
    """Convert shorthand entries to the existing internal rule structure."""
    result = {key: dict(value) for key, value in config.items()
              if key in (CUSTOMIZE, CUSTOMIZE_DOMAIN, CUSTOMIZE_GLOB)}
    for entity_id, value in config.items():
        if entity_id in (CUSTOMIZE, CUSTOMIZE_DOMAIN, CUSTOMIZE_GLOB):
            continue
        exact = result.setdefault(CUSTOMIZE, {})
        if entity_id in exact:
            raise vol.Invalid(f"Duplicate name rule for {entity_id}")
        exact[entity_id] = value if isinstance(value, dict) else {"name": value}
    return result


RULES_SCHEMA = vol.All(_RULES_SCHEMA, normalize_rules)
# The dictionary default also lets Home Assistant identify the package merge type.
CONFIG_SCHEMA = vol.Schema(
    {vol.Optional(DOMAIN, default=dict): vol.Any(None, RULES_SCHEMA)},
    extra=vol.ALLOW_EXTRA,
)


async def async_read_yaml(hass: HomeAssistant) -> dict[str, Any]:
    """Load includes and packages, rejecting partial or invalid package merges.

    The regular reload helper logs package merge errors and continues. For a
    naming manager that could accidentally release rules, so reject that reload.
    Home Assistant still performs the actual merge and its integration checks.
    """
    config = await hass.async_add_executor_job(
        conf_util.load_yaml_config_file,
        hass.config.path(conf_util.YAML_CONFIG_FILE),
        Secrets(Path(hass.config.config_dir)),
    )
    core = config.get("homeassistant", {})
    if not isinstance(core, dict):
        raise HomeAssistantError("homeassistant must be a YAML mapping")

    errors = []

    def package_error(_hass, package, integration, _config, message):
        errors.append(f"Package {package}, {integration or 'definition'}: {message}")

    try:
        await conf_util.merge_packages_config(
            hass, config, core.get("packages", {}), _log_pkg_error=package_error
        )
        if errors:
            raise HomeAssistantError("; ".join(errors))
        return CONFIG_SCHEMA(config)
    except vol.Invalid as err:
        raise HomeAssistantError(f"Invalid YAML Names configuration: {err}") from err
