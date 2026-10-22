"""Automation Replay.

Re-run (replay) an automation using the trigger object captured in one of its
stored traces — the thing the native "Run" button throws away — or with a
custom trigger payload you pass in by hand.

v1 scope (intentionally small, no impact on existing automations):
  - no polling, no entities: a single service `automation_replay.replay`,
    activated via a (zero-config) config entry so it loads without editing
    configuration.yaml
  - it only re-injects the `trigger` variable; it does NOT restore the original
    run context, nor the world state at the time of the trace
  - meant for rare, manual use from Developer Tools -> Actions
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_REPLAY

_LOGGER = logging.getLogger(__name__)

ATTR_ENTITY_ID = "entity_id"
ATTR_RUN_ID = "run_id"
ATTR_TRIGGER = "trigger"
ATTR_SKIP_CONDITION = "skip_condition"

AUTOMATION_DOMAIN = "automation"

# The trace component stores its runs under hass.data["trace"]. Import the
# constant when available, fall back to the literal so a rename can't break us.
try:  # pragma: no cover - trivial import shim
    from homeassistant.components.trace.const import DATA_TRACE
except ImportError:  # pragma: no cover
    DATA_TRACE = "trace"

REPLAY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_RUN_ID): cv.string,
        vol.Optional(ATTR_TRIGGER): dict,
        vol.Optional(ATTR_SKIP_CONDITION, default=True): cv.boolean,
    }
)


def _get_automation_component(hass: HomeAssistant) -> Any | None:
    """Return the automation EntityComponent.

    Works across HA versions: the newer ``DATA_COMPONENT`` HassKey resolves to
    the same ``hass.data`` slot as the legacy domain key.
    """
    return hass.data.get(AUTOMATION_DOMAIN)


def _normalize_runs(bucket: Any) -> tuple[Any, Any | None]:
    """Return (runs, not_triggered) mappings for old and new trace stores.

    HA 2024+ wraps per-item traces in a ``TraceBuckets`` dataclass with ``runs``
    and ``not_triggered`` ``LimitedSizeDict``s. Older versions stored the
    run-id -> trace mapping directly.
    """
    runs = getattr(bucket, "runs", None)
    if runs is not None:
        return runs, getattr(bucket, "not_triggered", None)
    return bucket, None


def _extract_trigger(trace: Any) -> Any | None:
    """Pull the full trigger payload out of a stored trace object.

    Home Assistant records the initial automation variables — including the
    ``trigger`` object, with real ``State`` objects for ``from_state`` /
    ``to_state`` — as a dedicated trace step at path ``trigger`` or
    ``trigger/<idx>`` (its ``changed_variables``).
    """
    try:
        extended = trace.as_extended_dict()
    except Exception:  # noqa: BLE001 - defensive about trace internals
        return None

    steps = extended.get("trace") or {}

    def _trigger_in(elements: Any) -> Any | None:
        for element in elements or []:
            if isinstance(element, dict):
                trig = (element.get("changed_variables") or {}).get("trigger")
                if isinstance(trig, dict):
                    return trig
        return None

    # Preferred: the dedicated trigger step.
    for path, elements in steps.items():
        if path == "trigger" or path.startswith("trigger/"):
            trig = _trigger_in(elements)
            if trig is not None:
                return trig

    # Fallback: any step carrying a trigger in its changed variables.
    for elements in steps.values():
        trig = _trigger_in(elements)
        if trig is not None:
            return trig

    return None


def _trigger_from_trace(
    hass: HomeAssistant, item_id: str, run_id: str | None
) -> tuple[str, Any]:
    """Look up a trace and return (run_id, trigger payload)."""
    store = hass.data.get(DATA_TRACE)
    if not store:
        raise ServiceValidationError("No traces are stored yet.")

    key = f"{AUTOMATION_DOMAIN}.{item_id}"
    bucket = store.get(key)
    if bucket is None:
        raise ServiceValidationError(
            f"No traces found for '{key}'. The automation must have run at "
            "least once (and keep traces) before it can be replayed."
        )

    runs, not_triggered = _normalize_runs(bucket)

    if run_id is None:
        run_ids = list(runs)  # newest last (insertion-ordered)
        if not run_ids:
            raise ServiceValidationError(f"No run traces found for '{key}'.")
        run_id = run_ids[-1]
        trace = runs[run_id]
    else:
        trace = runs.get(run_id)
        if trace is None and not_triggered is not None:
            trace = not_triggered.get(run_id)
        if trace is None:
            available = ", ".join(list(runs)) or "none"
            raise ServiceValidationError(
                f"run_id '{run_id}' not found for '{key}'. Available runs: {available}"
            )

    trigger = _extract_trigger(trace)
    if trigger is None:
        raise ServiceValidationError(
            f"Trace '{run_id}' has no trigger data to replay."
        )
    return run_id, trigger


async def _async_replay(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Handle the automation_replay.replay service call."""
    entity_id = call.data[ATTR_ENTITY_ID]

    component = _get_automation_component(hass)
    if component is None:
        raise HomeAssistantError("The automation integration is not loaded.")

    entity = component.get_entity(entity_id)
    if entity is None:
        raise ServiceValidationError(f"Unknown automation: {entity_id}")

    custom_trigger = call.data.get(ATTR_TRIGGER)
    used_run_id: str | None = None

    if custom_trigger is not None:
        # Explicit payload wins over any trace lookup.
        trigger = custom_trigger
    else:
        item_id = entity.unique_id
        if item_id is None:
            raise ServiceValidationError(
                f"{entity_id} has no id, so its traces can't be looked up. "
                "Pass a 'trigger' payload explicitly instead."
            )
        used_run_id, trigger = _trigger_from_trace(
            hass, item_id, call.data.get(ATTR_RUN_ID)
        )

    _LOGGER.info(
        "Replaying %s (trigger source: %s), skip_condition=%s",
        entity_id,
        used_run_id or "custom payload",
        call.data[ATTR_SKIP_CONDITION],
    )

    # Exactly what automation.trigger does internally, but with a real trigger
    # payload instead of an empty one. The service call's own context is reused
    # so the replay stays traceable to whoever invoked it.
    await entity.async_trigger(
        {"trigger": trigger},
        context=call.context,
        skip_condition=call.data[ATTR_SKIP_CONDITION],
    )

    return {
        "entity_id": entity_id,
        "run_id": used_run_id,
        "trigger": trigger,
    }


def _register_service(hass: HomeAssistant) -> None:
    """Register the replay service (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_REPLAY):
        return

    async def handler(call: ServiceCall) -> ServiceResponse:
        return await _async_replay(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_REPLAY,
        handler,
        schema=REPLAY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up via YAML (`automation_replay:`), if present."""
    if DOMAIN in config:
        _register_service(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry (the normal path)."""
    _register_service(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the config entry and remove the service."""
    if hass.services.has_service(DOMAIN, SERVICE_REPLAY):
        hass.services.async_remove(DOMAIN, SERVICE_REPLAY)
    return True
