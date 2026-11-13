# YAML Names

Manage the entity names introduced in Home Assistant 2026.11 from YAML. This
integration sets the entity registry's user-defined `name`, just like the entity
settings screen. It never changes entity IDs, device names, areas or states.

<!-- ha-marketplace:exclude-start -->
## Installation

1. Open **Settings > Marketplace > Add from link**.
2. Enter `https://github.com/clau-bucur/ha-yaml-names` and select **Add**.
   Connect GitHub when prompted. If asked for a type, select **Integration**.
3. Open **YAML Names** and select **Install**.
4. Add your `yaml_names:` rules to `configuration.yaml` or a Home Assistant package.
5. Restart Home Assistant to load the integration.

For manual installation, copy `custom_components/yaml_names` from this repository
into your Home Assistant configuration's `custom_components` directory, add your
rules, and restart Home Assistant.
<!-- ha-marketplace:exclude-end -->

## Configuration and packages

Requires **Home Assistant 2026.11.0 or newer**. This integration is configured
through YAML; it does not have a UI setup flow.

Put rules in `configuration.yaml`, in an included file, or in any existing
Home Assistant package:

```yaml
yaml_names:
  sensor.test_sensor_id: Test Sensor ID NEW
```

Dictionary entries are also supported:

```yaml
yaml_names:
  sensor.test_sensor_id:
    name: Test Sensor ID NEW
```

Both forms have the same behavior. Dictionary entries currently support only
`name`; more properties can be added later. Direct entries can be mixed with
the existing sections below, using distinct entity IDs. Defining the same
entity directly and under `customize` is rejected as a duplicate.

The existing format remains supported:

```yaml
yaml_names:
  customize:
    sensor.average_house_power_10:
      name: "Average house power"
  customize_glob:
    "sensor.*_battery":
      name: "Battery"
  customize_domain:
    update:
      name: "Update"
```

Exact entity rules override glob rules. Globs are applied in YAML order, with
the last matching glob winning. Domain rules have the lowest priority.
Matching is case-sensitive, as it is for Home Assistant entity IDs.

Each selector requires `name`. A string sets a custom name; `null` deliberately
clears the user override and uses the source integration's default. An empty
string `""` selects no entity-name part, like a device's main entity. Home
Assistant rejects an empty entity name if the entity also has its own area.

Home Assistant merges rules from packages using its normal dictionary merge.
Use distinct selectors across packages: duplicate non-null name definitions
are configuration errors, even if they contain the same value. A failed reload
keeps the previous active rules. Includes and secrets use Home Assistant's
normal YAML loader.

Rules apply after Home Assistant has started, to entities registered later,
and when their managed names are changed. For selected entities, YAML wins over
UI renames. Entities outside these rules keep their normal UI ownership.

Startup and reload check the full entity registry. Registry events use direct
lookups for only the affected entity IDs, including the old ID after a rename.
Repeated events are combined before processing. Ordinary sensor state changes
do not trigger checks, and there is no polling or periodic scan. Backup storage
is written only when its contents change.

## Replacing a physical device

Names are matched by entity ID, independently of the physical device's unique ID.
When a replacement entity is registered with the same entity ID, its YAML name is
reapplied automatically. Changing a replacement's entity ID to the configured ID
also triggers reconciliation. An ID with an added suffix such as `_2` does not
match an exact rule for the original ID.

Original-name backups belong to registry identities. A replacement with a new
registry identity gets its own original-name backup.

## Actions

In Developer Tools > Actions:

```yaml
action: yaml_names.reload
data: {}
```

This rereads configuration and packages, validates them, applies changed rules,
and restores names for removed rules. A restart is needed only for initial
installation or Python code updates. Reloading YAML rules needs no restart.
The action optionally returns managed/changed/restored counts and unresolved
entity IDs. It fires `event_yaml_names_reloaded` on success.

```yaml
action: yaml_names.migration_preview
data: {}
```

This action always returns a response. In scripts or automations, capture it:

```yaml
- action: yaml_names.migration_preview
  data: {}
  response_variable: naming_preview
```

The response includes `yaml` (a suggested configuration to copy),
`suggested_config` (the same data as an object), `summary`, `entities`,
`unmatched_rules` and `warnings`. Each entity has its current/proposed name,
registration status, source rule and any conflict or repeated-device warning.
Exact entity names are generated in the short `entity_id: Name` format.
Glob and domain rules retain their `customize_glob`/`customize_domain` sections.

The preview reads only `friendly_name` overrides under
`homeassistant.customize`, `customize_glob` and `customize_domain`, including
packages. It follows core's ordering and whole-selector replacement across
packages. A source integration's own `friendly_name` setting is not a legacy
customization and does not need converting by this tool.

Previewing does not rename anything or edit configuration files. Review its
output before merging it into existing `yaml_names` rules. Existing rules are
reported as conflicts when they select a different name. Remove migrated
legacy `friendly_name` attributes after applying and verifying the new rules;
otherwise old cards reading state attributes can still show the old names.

## Registry and display limitations

An entity needs a registry entry, normally created using a stable `unique_id`
in its source integration. Unregistered and missing entities are reported and
skipped. This integration does not invent registry entries or duplicate sensors.

The configured name is the **entity part**. Home Assistant and individual cards
can add device or area context. The preview shows core's composed name, while a
card may select different name parts. A legacy full name containing the device
name can therefore cause repetition; the preview flags this for review.

## Restoration and uninstalling

Original user overrides are saved before the first change in
`.storage/yaml_names.managed_names`, keyed by stable registry entry identity.
Subsequent YAML edits retain that first original, including a null override.
Deleting a rule and reloading restores it if the current name still matches
the last name applied by YAML Names. A different external name is preserved
when releasing a rule. Renamed entity IDs never cause a backup to be restored
to a different registry identity. Backups for deleted identities are retained
for restoration if those same identities reappear.

```yaml
action: yaml_names.restore
data: {}
```

This restores all available saved names and suspends management until the next
`yaml_names.reload` or Home Assistant restart. For uninstalling, run it, remove
all `yaml_names` configuration (including packages), then restart. Removing the
integration without restoration leaves persisted registry names in place.
Keep the backup file if any restorations are deferred for absent entities.

All three actions require administrator access.

## Development and validation

GitHub Actions runs hassfest and Marketplace/HACS repository validation. The HACS
check skips brand-image and README-image checks because this repository currently
has no artwork. It is intended for installation through **Add from link**, rather
than catalog submission. No automated runtime test suite is included yet.

Before releasing behavior changes, validate configuration and exercise reload,
restore, migration preview, and entity-registry recreation on a test Home
Assistant instance. Static checks do not establish runtime or dashboard behavior.

## Releases

Keep `custom_components/yaml_names/manifest.json` version and the GitHub release
tag aligned (for example, `1.0.3`). Publish a GitHub release after validation
passes. The Marketplace installs the integration directory from the repository;
no ZIP release asset is required. Update the minimum Home Assistant version in
`hacs.json` if a future release needs newer APIs.

## License

MIT. See [LICENSE](LICENSE).
