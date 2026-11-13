"""Apply declarative names while keeping durable, identity-bound originals."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
from typing import Any

import attr

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import CUSTOMIZE, DOMAIN, STORAGE_KEY, STORAGE_VERSION
from .rules import NameRules

_LOGGER = logging.getLogger(__name__)


class YamlNamesManager:
    """Own only names selected by YAML; never change entity IDs or devices."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.config_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY, atomic_writes=True)
        self._rules = NameRules()
        self._managed: dict[str, dict[str, Any]] = {}
        self._ready = False
        self._pending: set[str] = set()
        self._pending_full_scan = False
        self._task: asyncio.Task | None = None
        self._unsubscribers = []
        self._unsub_started = None

    async def async_setup(self, rules: NameRules) -> None:
        """Load originals before subscribing or changing any names."""
        data = await self._store.async_load()
        if data is not None:
            records = data.get("entries") if isinstance(data, dict) else None
            if not isinstance(records, dict) or any(
                not isinstance(key, str)
                or not isinstance(record, dict)
                or not isinstance(record.get("entity_id"), str)
                or "original_name" not in record
                or "applied_name" not in record
                or record["original_name"] is not None
                and not isinstance(record["original_name"], str)
                or record["applied_name"] is not None
                and not isinstance(record["applied_name"], str)
                for key, record in records.items()
            ):
                raise HomeAssistantError("Invalid YAML Names backup storage; refusing to rename entities")
            self._managed = records
        self._rules = rules
        self._unsubscribers.append(
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated)
        )
        self._unsubscribers.append(
            self.hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, self._stop)
        )
        if self.hass.is_running:
            self._ready = True
            await self.async_reconcile(report_missing=True)
        else:
            self._unsub_started = self.hass.bus.async_listen(
                EVENT_HOMEASSISTANT_STARTED, self._started
            )

    @callback
    def _started(self, _event) -> None:
        if self._unsub_started is not None:
            self._unsub_started()
            self._unsub_started = None
        self._ready = True
        self._schedule_reconcile()

    @callback
    def _stop(self, _event) -> None:
        self._ready = False
        if self._unsub_started is not None:
            self._unsub_started()
            self._unsub_started = None
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    @callback
    def _registry_updated(self, event) -> None:
        if not self._ready:
            return
        if event.data["action"] == "update" and {
            "name", "entity_id", "device_id", "area_id", "original_name", "has_entity_name"
        }.isdisjoint(event.data["changes"]):
            return
        entity_ids = {event.data["entity_id"]}
        if old_entity_id := event.data.get("old_entity_id"):
            entity_ids.add(old_entity_id)
        self._schedule_reconcile(entity_ids)

    @callback
    def _schedule_reconcile(self, entity_ids: set[str] | None = None) -> None:
        if entity_ids is None:
            self._pending_full_scan = True
        else:
            self._pending.update(entity_ids)
        if self._task is None or self._task.done():
            # HA starts tasks eagerly by default. A no-op startup pass can
            # finish before assignment and leave a completed task in _task.
            self._task = self.hass.async_create_task(
                self._async_process_pending(), f"{DOMAIN} registry reconciliation",
                eager_start=False,
            )

    async def _async_process_pending(self) -> None:
        try:
            while (self._pending_full_scan or self._pending) and self._ready:
                full_scan = self._pending_full_scan
                entity_ids = self._pending
                self._pending_full_scan = False
                self._pending = set()
                try:
                    await self.async_reconcile(
                        entity_ids=None if full_scan else entity_ids
                    )
                except Exception:  # A bad entity must not kill subsequent reconciliation.
                    _LOGGER.exception("Unable to reconcile YAML entity names")
        finally:
            self._task = None

    async def async_reconcile(
        self, rules: NameRules | None = None, *, report_missing: bool = False,
        entity_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        """Validate a complete change set, save originals, then apply names.

        Registry events supply entity IDs; startup and reload omit them for a
        full scan. An omitted rules argument uses the latest rules inside the lock so a
        queued registry event cannot put an old configuration back after reload.
        """
        async with self._lock:
            desired_rules = self._rules if rules is None else rules
            registry = er.async_get(self.hass)
            if entity_ids is None:
                entries = {entry.id: entry for entry in registry.entities.values()}
                previous = deepcopy(self._managed)
            else:
                entries = {
                    entry.id: entry for entity_id in sorted(entity_ids)
                    if (entry := registry.async_get(entity_id)) is not None
                }
                previous = self._managed
            # Deleted registry entries may be restored later with the same ID.
            # Keep their originals until that identity reappears.
            desired_records = (
                {key: record for key, record in previous.items() if key not in entries}
                if entity_ids is None else dict(previous)
            )
            changes = []
            restored = 0
            preserved = 0

            for registry_id, entry in entries.items():
                match = desired_rules.match(entry.entity_id)
                record = previous.get(registry_id)
                if match is None:
                    if record is not None:
                        desired_records.pop(registry_id, None)
                        if entry.name == record["applied_name"]:
                            if entry.name != record["original_name"]:
                                changes.append((entry.entity_id, entry.name, record["original_name"]))
                            restored += 1
                        else:
                            preserved += 1
                    continue

                proposed = attr.evolve(entry, name=match.name)
                if proposed.area_id is not None and proposed.device_id is not None and not proposed.entity_name:
                    raise HomeAssistantError(
                        f"Cannot set an empty name for {entry.entity_id}: it has its own area; "
                        "set the area on its device first"
                    )
                desired_records[registry_id] = {
                    "entity_id": entry.entity_id,
                    "original_name": record["original_name"] if record is not None else entry.name,
                    "applied_name": match.name,
                }
                if entry.name != match.name:
                    changes.append((entry.entity_id, entry.name, match.name))

            # Keep records being released until their restoration has completed.
            staged = {**previous, **desired_records}
            if staged != previous:
                await self._store.async_save({"entries": staged})

            old_rules = self._rules
            self._rules = desired_rules
            applied = []
            try:
                for entity_id, old_name, new_name in changes:
                    registry.async_update_entity(entity_id, name=new_name)
                    applied.append((entity_id, old_name))
            except Exception as err:
                self._rules = old_rules
                for entity_id, old_name in reversed(applied):
                    registry.async_update_entity(entity_id, name=old_name)
                await self._store.async_save({"entries": previous})
                raise HomeAssistantError(f"Name update failed; previous rules restored: {err}") from err

            self._managed = desired_records
            if desired_records != staged:
                try:
                    await self._store.async_save({"entries": desired_records})
                except Exception:
                    # The durable staged copy still contains every original.
                    _LOGGER.exception("Names applied, but backup cleanup could not be saved")

            if entity_ids is not None:
                return {"checked": len(entries), "changed": len(changes)}

            missing = []
            for entity_id in desired_rules.config.get(CUSTOMIZE, {}):
                if registry.async_get(entity_id) is None:
                    status = "unregistered" if self.hass.states.get(entity_id) is not None else "missing"
                    missing.append({"entity_id": entity_id, "status": status})
            if report_missing:
                for item in missing:
                    _LOGGER.warning("Cannot rename %s: entity is %s", item["entity_id"], item["status"])

            return {
                "managed": sum(key in entries for key in desired_records),
                "deferred_restorations": sum(key not in entries for key in desired_records),
                "changed": len(changes),
                "restored": restored,
                "preserved_external_names": preserved,
                "unresolved": missing,
            }
