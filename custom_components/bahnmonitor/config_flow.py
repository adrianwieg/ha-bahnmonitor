"""GUI to configure one recurring monitored train per entry."""
from __future__ import annotations

from datetime import time

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .api import BahnApi, BahnApiError
from .const import (
    DEFAULT_DAYS, DEFAULT_DESTINATION, DEFAULT_LINE, DEFAULT_ORIGIN,
    DEFAULT_TIME, DEFAULT_TURNAROUND, DEFAULT_WINDOW, DOMAIN,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession


def schema(defaults: dict | None = None) -> vol.Schema:
    x = defaults or {}
    return vol.Schema({
        vol.Required("name", default=x.get("name", "Pendlerzug")): str,
        vol.Required("origin", default=x.get("origin", DEFAULT_ORIGIN)): str,
        vol.Required("destination", default=x.get("destination", DEFAULT_DESTINATION)): str,
        vol.Required("line", default=x.get("line", DEFAULT_LINE)): str,
        vol.Required("departure_time", default=x.get("departure_time", DEFAULT_TIME)): str,
        vol.Required("weekdays", default=x.get("weekdays", DEFAULT_DAYS)): str,
        vol.Required("window", default=x.get("window", DEFAULT_WINDOW)): vol.All(vol.Coerce(int), vol.Range(min=5, max=90)),
        vol.Required("turnaround", default=x.get("turnaround", True)): bool,
        vol.Required("min_turn_minutes", default=x.get("min_turn_minutes", 8)): vol.All(vol.Coerce(int), vol.Range(min=0, max=30)),
        vol.Required("max_turn_minutes", default=x.get("max_turn_minutes", DEFAULT_TURNAROUND)): vol.All(vol.Coerce(int), vol.Range(min=10, max=120)),
    })


def valid_config(data: dict) -> bool:
    try:
        time.fromisoformat(data["departure_time"])
        days = [int(d.strip()) for d in data["weekdays"].split(",")]
        return bool(days) and all(0 <= d <= 6 for d in days) and data["origin"].strip().casefold() != data["destination"].strip().casefold()
    except (ValueError, KeyError):
        return False


class BahnmonitorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return BahnmonitorOptionsFlow()

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            if not valid_config(user_input):
                errors["base"] = "invalid_settings"
            else:
                api = BahnApi(async_get_clientsession(self.hass))
                try:
                    origin_id, origin_name = await api.resolve_station(user_input["origin"].strip())
                    destination_id, destination_name = await api.resolve_station(user_input["destination"].strip())
                except BahnApiError:
                    errors["base"] = "connection_error"
                else:
                    user_input.update(origin_id=origin_id, origin=origin_name, destination_id=destination_id, destination=destination_name)
                    return self.async_create_entry(title=user_input["name"], data=user_input)
        return self.async_show_form(step_id="user", data_schema=schema(user_input), errors=errors)


class BahnmonitorOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            if not valid_config(user_input):
                errors["base"] = "invalid_settings"
            else:
                api = BahnApi(async_get_clientsession(self.hass))
                try:
                    origin_id, origin_name = await api.resolve_station(user_input["origin"].strip())
                    destination_id, destination_name = await api.resolve_station(user_input["destination"].strip())
                except BahnApiError:
                    errors["base"] = "connection_error"
                else:
                    user_input.update(origin_id=origin_id, origin=origin_name, destination_id=destination_id, destination=destination_name)
                    return self.async_create_entry(data=user_input)
        defaults = {**self.config_entry.data, **self.config_entry.options, **(user_input or {})}
        return self.async_show_form(step_id="init", data_schema=schema(defaults), errors=errors)
