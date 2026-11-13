"""Manage Home Assistant entity registry names from YAML."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType

from .config import CONFIG_SCHEMA, async_read_yaml
from .const import DOMAIN
from .manager import YamlNamesManager
from .migration import build_preview
from .rules import NameRules


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up a YAML-only integration, including package-only configuration."""
    manager = YamlNamesManager(hass)
    await manager.async_setup(NameRules(config.get(DOMAIN)))
    hass.data[DOMAIN] = manager

    async def async_reload(call: ServiceCall):
        async with manager.config_lock:
            fresh_config = await async_read_yaml(hass)
            result = await manager.async_reconcile(
                NameRules(fresh_config.get(DOMAIN)), report_missing=True
            )
        hass.bus.async_fire(f"event_{DOMAIN}_reloaded", result, context=call.context)
        return result if call.return_response else None

    async def async_migration_preview(_call: ServiceCall):
        async with manager.config_lock:
            fresh_config = await async_read_yaml(hass)
            return build_preview(hass, fresh_config)

    async def async_restore(call: ServiceCall):
        async with manager.config_lock:
            result = await manager.async_reconcile(NameRules())
        return result if call.return_response else None

    async_register_admin_service(
        hass, DOMAIN, "reload", async_reload, supports_response=SupportsResponse.OPTIONAL
    )
    async_register_admin_service(
        hass, DOMAIN, "migration_preview", async_migration_preview,
        supports_response=SupportsResponse.ONLY,
    )
    async_register_admin_service(
        hass, DOMAIN, "restore", async_restore, supports_response=SupportsResponse.OPTIONAL
    )
    return True
