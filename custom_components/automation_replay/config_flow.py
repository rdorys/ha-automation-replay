"""Config flow for Automation Replay.

The integration has nothing to configure — the flow exists only so the
integration can be set up as a config entry (and thus loaded without a
`configuration.yaml` entry). Single instance.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class AutomationReplayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a single-instance config flow for Automation Replay."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the single config entry (no options to collect)."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Automation Replay", data={})
