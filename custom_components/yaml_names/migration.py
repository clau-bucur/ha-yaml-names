"""Build a read-only migration preview of legacy friendly_name customizations."""

from __future__ import annotations

from typing import Any

import attr
import probatio as vol
import yaml

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er

from .const import CUSTOMIZE, CUSTOMIZE_DOMAIN, CUSTOMIZE_GLOB, DOMAIN, SECTIONS
from .rules import NameRules

_ATTRIBUTES_SCHEMA = vol.Schema(
    {vol.Optional("friendly_name"): cv.string}, extra=vol.ALLOW_EXTRA
)
_LEGACY_SCHEMA = vol.Schema(
    {
        vol.Optional(CUSTOMIZE): {cv.entity_id: _ATTRIBUTES_SCHEMA},
        vol.Optional(CUSTOMIZE_DOMAIN): {cv.string: _ATTRIBUTES_SCHEMA},
        vol.Optional(CUSTOMIZE_GLOB): {cv.string: _ATTRIBUTES_SCHEMA},
    }
)


def legacy_rules(config: dict[str, Any]):
    """Match core's package replacement semantics before extracting names.

    Updating an existing selector replaces its whole attribute dictionary, not
    just friendly_name. Extracting names before that replacement is incorrect.
    """
    core = config.get("homeassistant", {})
    merged = {section: {} for section in SECTIONS}
    sources = {}
    warnings = []
    root = {section: core[section] for section in SECTIONS if section in core}
    layers = [("homeassistant", root)]
    layers.extend(
        (f"package {package}.homeassistant", value["homeassistant"])
        for package, value in core.get("packages", {}).items()
        if isinstance(value, dict) and value.get("homeassistant") is not None
    )
    for source, layer in layers:
        try:
            layer = _LEGACY_SCHEMA(layer)
        except vol.Invalid as err:
            warnings.append(f"Skipped invalid {source} customization, as core does: {err}")
            continue
        for section in SECTIONS:
            for selector, attributes in layer.get(section, {}).items():
                merged[section][selector] = attributes
                sources[(section, selector)] = f"{source}.{section}.{selector}"
    converted = {
        section: {
            selector: {"name": attributes["friendly_name"]}
            for selector, attributes in values.items()
            if "friendly_name" in attributes
        }
        for section, values in merged.items()
    }
    return NameRules({key: value for key, value in converted.items() if value}), sources, warnings


def build_preview(hass: HomeAssistant, config: dict[str, Any]) -> dict[str, Any]:
    """Return proposed YAML and per-entity results without changing any registry."""
    legacy, sources, warnings = legacy_rules(config)
    current_rules = NameRules(config.get(DOMAIN))
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    known_ids = set(registry.entities) | set(hass.states.async_entity_ids())
    candidate_ids = known_ids | set(legacy.config.get(CUSTOMIZE, {}))
    rows = []

    for entity_id in sorted(candidate_ids):
        match = legacy.match(entity_id)
        if match is None:
            continue
        entry = registry.async_get(entity_id)
        state = hass.states.get(entity_id)
        existing = current_rules.match(entity_id)
        row_warnings = []
        if existing is not None and existing.name != match.name:
            row_warnings.append("Conflicts with an existing yaml_names rule; review before merging")
        if entry is None:
            status = "unregistered" if state is not None else "missing"
            proposed_display_name = None
            current_display_name = state.attributes.get("friendly_name") if state is not None else None
            current_entity_name = None
            row_warnings.append(
                "No entity registry entry: add a stable unique_id in the source integration where supported"
            )
        else:
            status = "ready"
            current_entity_name = entry.entity_name
            current_display_name = er.async_get_full_entity_name(hass, entry)
            proposed = attr.evolve(entry, name=match.name)
            proposed_display_name = er.async_get_full_entity_name(hass, proposed)
            if proposed.area_id is not None and proposed.device_id is not None and not proposed.entity_name:
                status = "invalid"
                row_warnings.append("An entity without its own name cannot have its own area")
            device = device_registry.async_get(entry.device_id) if entry.device_id else None
            device_name = (device.name_by_user or device.name) if device else None
            if device_name and match.name and (
                match.name.casefold() == device_name.casefold()
                or match.name.casefold().startswith(f"{device_name.casefold()} ")
            ):
                row_warnings.append("Legacy name already contains the device name; composed displays may repeat it")

        rows.append(
            {
                "entity_id": entity_id,
                "status": status,
                "source": sources[(match.section, match.selector)],
                "current_entity_name": current_entity_name,
                "current_display_name": current_display_name,
                "proposed_entity_name": match.name,
                "proposed_display_name": proposed_display_name,
                "conflict": existing is not None and existing.name != match.name,
                "warnings": row_warnings,
            }
        )

    unmatched = [
        {"section": section, "selector": selector, "source": sources[(section, selector)]}
        for section, selector in legacy.selectors()
        if not any(legacy.selector_matches(section, selector, entity_id) for entity_id in known_ids)
    ]
    # Exact entity names use the short format; globs/domains keep their sections.
    suggested_names = {
        entity_id: rule["name"]
        for entity_id, rule in legacy.config.get(CUSTOMIZE, {}).items()
    }
    suggested_names.update({
        section: values for section, values in legacy.config.items()
        if section != CUSTOMIZE
    })
    suggested_config = {DOMAIN: suggested_names}
    return {
        "applies_changes": False,
        "summary": {
            "rules": sum(1 for _ in legacy.selectors()),
            "entities": len(rows),
            "ready": sum(row["status"] == "ready" for row in rows),
            "unregistered": sum(row["status"] == "unregistered" for row in rows),
            "missing": sum(row["status"] == "missing" for row in rows),
            "invalid": sum(row["status"] == "invalid" for row in rows),
            "conflicts": sum(row["conflict"] for row in rows),
            "unmatched_rules": len(unmatched),
        },
        "suggested_config": suggested_config,
        "yaml": yaml.safe_dump(suggested_config, sort_keys=False, allow_unicode=True),
        "entities": rows,
        "unmatched_rules": unmatched,
        "warnings": warnings,
        "display_note": "Display names are core-composed previews; individual cards can choose different name parts.",
    }
