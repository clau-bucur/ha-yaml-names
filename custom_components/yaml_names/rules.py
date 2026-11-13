"""Name rule selection, shared by runtime management and migration previews."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from .const import CUSTOMIZE, CUSTOMIZE_DOMAIN, CUSTOMIZE_GLOB, SECTIONS


@dataclass(frozen=True)
class NameMatch:
    """A selected rule; None is a deliberate reset, not an absent rule."""

    name: str | None
    section: str
    selector: str


class NameRules:
    """Resolve domain, then ordered globs, then exact entity rules."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        # HA's YAML loader annotates string subclasses with file/line metadata.
        # Normalize them so safe_dump and service response serialization work.
        self.config = {
            str(section): {
                str(selector): {"name": None if rule["name"] is None else str(rule["name"])}
                for selector, rule in selectors.items()
            }
            for section, selectors in (config or {}).items()
        }

    def match(self, entity_id: str) -> NameMatch | None:
        """Return the last applicable rule, including explicit null names."""
        domain = entity_id.partition(".")[0]
        result = None
        if rule := self.config.get(CUSTOMIZE_DOMAIN, {}).get(domain):
            result = NameMatch(rule["name"], CUSTOMIZE_DOMAIN, domain)
        for pattern, rule in self.config.get(CUSTOMIZE_GLOB, {}).items():
            if fnmatchcase(entity_id, pattern):
                result = NameMatch(rule["name"], CUSTOMIZE_GLOB, pattern)
        if rule := self.config.get(CUSTOMIZE, {}).get(entity_id):
            result = NameMatch(rule["name"], CUSTOMIZE, entity_id)
        return result

    def selectors(self):
        """Yield configured selectors in increasing precedence order."""
        for section in SECTIONS:
            for selector in self.config.get(section, {}):
                yield section, selector

    def selector_matches(self, section: str, selector: str, entity_id: str) -> bool:
        """Check one rule without excluding rules shadowed by other rules."""
        if section == CUSTOMIZE_DOMAIN:
            return entity_id.partition(".")[0] == selector
        if section == CUSTOMIZE_GLOB:
            return fnmatchcase(entity_id, selector)
        return entity_id == selector
