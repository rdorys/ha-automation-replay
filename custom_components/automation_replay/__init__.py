"""Automation Replay.

Re-run (replay) an automation using the trigger object captured in one of its
stored traces — the thing the native "Run" button throws away — or with a
custom trigger payload you pass in by hand.

v1 scope (intentionally small, no impact on existing automations):
  - no entity, no config entry: a single service `automation_replay.replay`
  - it only re-injects the `trigger` variable; it does NOT restore the original
    run context, nor the world state at the time of the trace
  - meant for rare, manual use from Developer Tools -> Actions
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "automation_replay"
SERVICE_REPLAY = "replay"

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


def _extract_trigger(trace: Any) -> Any | None:
    """Pull the trigger payload out of a stored trace object."""
    try:
        extended = trace.as_extended_dict()
    except Exception:  # noqa: BLE001 - be defensive about trace internals
        extended = {}

    # Preferred: the run variables captured for this trace.
    variables = extended.get("variables")
    if isinstance(variables, dict) and variables.get("trigger") is not None:
        return variables["trigger"]

    # Fallback 1: a dict trigger at the root (not the short string description).
    root_trigger = extended.get("trigger")
    if isinstance(root_trigger, dict):
        return root_trigger

    # Fallback 2: read the private attribute directly.
    private_vars = getattr(trace, "_variables", None)
    if isinstance(private_vars, dict) and private_vars.get("trigger") is not None:
        return private_vars["trigger"]

    return None


def _trigger_from_trace(
    hass: HomeAssistant, item_id: str, run_id: str | None
) -> tuple[str, Any]:
    """Look up a trace and return (run_id, trigger payload)."""
    store = hass.data.get(DATA_TRACE)
    if not store:
        raise ServiceValidationError("No traces are stored yet.")

    key = f"{AUTOMATION_DOMAIN}.{item_id}"
    traces = store.get(key)
    if not traces:
        raise ServiceValidationError(
            f"No traces found for '{key}'. The automation must have run at "
            "least once (and keep traces) before it can be replayed."
        )

    if run_id is None:
        run_id = list(traces)[-1]  # newest, insertion-ordered
        trace = traces[run_id]
    else:
        trace = traces.get(run_id)
        if trace is None:
            available = ", ".join(traces) or "none"
            raise ServiceValidationError(
                f"run_id '{run_id}' not found for '{key}'. Available: {available}"
            )

    trigger = _extract_trigger(trace)
    if trigger is None:
        raise ServiceValidationError(
            f"Trace '{run_id}' has no trigger data to replay."
        )
    return run_id, trigger


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the replay service."""

    async def _async_replay(call: ServiceCall) -> ServiceResponse:
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

        # Exactly what automation.trigger does internally, but with a real
        # trigger payload instead of an empty one. The service call's own
        # context is reused so the replay stays traceable to whoever invoked it.
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

    hass.services.async_register(
        DOMAIN,
        SERVICE_REPLAY,
        _async_replay,
        schema=REPLAY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True
