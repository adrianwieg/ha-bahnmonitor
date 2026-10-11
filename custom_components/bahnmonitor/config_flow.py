"""GUI to configure a general route monitor or a recurring train."""
from __future__ import annotations

from datetime import time
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .api import BahnApi, BahnApiError, StationNotFound
from .const import (
    DEFAULT_DAYS, DEFAULT_DESTINATION, DEFAULT_LINE, DEFAULT_ORIGIN,
    DEFAULT_TIME, DEFAULT_TURNAROUND, DEFAULT_WINDOW, DOMAIN,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)


def default_turnaround_at(data: dict) -> str:
    """Automatic discovery is the default; older entries retain preferences."""
    return data.get("turnaround_at", "auto")


def schema(defaults: dict | None = None) -> vol.Schema:
    """A route with an optional line is the primary and simplest UI."""
    x = defaults or {}
    mode = x.get("monitor_mode", "train" if x.get("departure_time") else "route")
    fields = {
        vol.Required("name", default=x.get("name", "Bahnmonitor")): str,
        # Existing legacy train entries remain editable below; new entries
        # are always route monitors without a confusing mode selector.
        vol.Required("origin", default=x.get("origin", DEFAULT_ORIGIN)): str,
        vol.Required("destination", default=x.get("destination", DEFAULT_DESTINATION)): str,
        vol.Optional("line", default=x.get("line", DEFAULT_LINE)): str,
    }
    if mode == "train":
        fields.update({
            vol.Required("departure_time", default=x.get("departure_time", DEFAULT_TIME)): str,
            vol.Required("weekdays", default=x.get("weekdays", DEFAULT_DAYS)): str,
            vol.Required("window", default=x.get("window", DEFAULT_WINDOW)): vol.All(
                vol.Coerce(int), vol.Range(min=5, max=90)),
            vol.Required("history_enabled", default=x.get("history_enabled", True)): bool,
            vol.Required("turnaround_at", default=default_turnaround_at(x)): vol.In({
                "auto": "Wendebahnhöfe automatisch erkennen (empfohlen)",
                "off": "Vorleistungsprüfung deaktivieren",
                "origin": "Altmodus: Startbahnhof als Wendebahnhof",
                "destination": "Altmodus: Zielbahnhof als Wendebahnhof",
            }),
            vol.Required("min_turn_minutes", default=x.get("min_turn_minutes", 8)): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=30)),
            vol.Required("max_turn_minutes", default=x.get("max_turn_minutes", DEFAULT_TURNAROUND)): vol.All(
                vol.Coerce(int), vol.Range(min=10, max=120)),
        })
    return vol.Schema(fields)


def valid_config(data: dict) -> bool:
    """For the primary route mode, only valid station names are required."""
    try:
        origin, destination = data["origin"].strip(), data["destination"].strip()
        mode = data.get("monitor_mode", "route")
        if not origin or not destination or origin.casefold() == destination.casefold():
            return False
        if mode not in ("route", "train"):
            return False
        if mode == "route":
            return True
        if not data.get("line", "").strip():
            return False
        time.fromisoformat(data["departure_time"])
        days = [int(d.strip()) for d in data["weekdays"].split(",")]
        return (
            bool(days) and all(0 <= d <= 6 for d in days)
            and data.get("turnaround_at", "auto") in ("off", "auto", "origin", "destination")
        )
    except (ValueError, KeyError, TypeError):
        return False

class BahnmonitorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return BahnmonitorOptionsFlow()

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            user_input["monitor_mode"] = "route"
            if not valid_config(user_input):
                errors["base"] = "invalid_settings"
            else:
                api = BahnApi(async_get_clientsession(self.hass))
                try:
                    origin_id, origin_name = await api.resolve_station(user_input["origin"].strip())
                    destination_id, destination_name = await api.resolve_station(user_input["destination"].strip())
                except StationNotFound as exc:
                    _LOGGER.warning("Bahnmonitor: %s", exc)
                    errors["base"] = "station_not_found"
                except BahnApiError as exc:
                    _LOGGER.warning("Bahnmonitor: Bahnhofsdienst-Fehler bei der Einrichtung: %s", exc)
                    errors["base"] = "connection_error"
                else:
                    user_input.update(origin_id=origin_id, origin=origin_name, destination_id=destination_id, destination=destination_name)
                    return self.async_create_entry(title=user_input["name"], data=user_input)
        return self.async_show_form(step_id="user", data_schema=schema(user_input), errors=errors)


class BahnmonitorOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            user_input["monitor_mode"] = (
                self.config_entry.options.get("monitor_mode")
                or self.config_entry.data.get("monitor_mode")
                or ("train" if self.config_entry.data.get("departure_time") else "route")
            )
            if not valid_config(user_input):
                errors["base"] = "invalid_settings"
            else:
                api = BahnApi(async_get_clientsession(self.hass))
                try:
                    origin_id, origin_name = await api.resolve_station(user_input["origin"].strip())
                    destination_id, destination_name = await api.resolve_station(user_input["destination"].strip())
                except StationNotFound as exc:
                    _LOGGER.warning("Bahnmonitor: %s", exc)
                    errors["base"] = "station_not_found"
                except BahnApiError as exc:
                    _LOGGER.warning("Bahnmonitor: Bahnhofsdienst-Fehler bei der Einrichtung: %s", exc)
                    errors["base"] = "connection_error"
                else:
                    user_input.update(origin_id=origin_id, origin=origin_name, destination_id=destination_id, destination=destination_name)
                    return self.async_create_entry(data=user_input)
        defaults = {**self.config_entry.data, **self.config_entry.options, **(user_input or {})}
        return self.async_show_form(step_id="init", data_schema=schema(defaults), errors=errors)
