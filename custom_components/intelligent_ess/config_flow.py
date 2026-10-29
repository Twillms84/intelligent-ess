import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.core import callback
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class IntelligentESSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Erster Setup-Schritt."""
        if user_input is not None:
            return self.async_create_entry(title="Intelligent ESS", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                # Restliche Sensoren
                vol.Required("tibber_price_sensor"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required("tibber_export_sensor"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required("battery_soc_sensor"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="battery")),
                
                # Einheitliche PV-Forecast Namen
                vol.Required("pv_forecast_today_entity"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional("pv_forecast_tomorrow_entity"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),

                # Hardware Steuerung
                vol.Required("battery_charge_switch"): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
                vol.Required("wr_limit_entity"): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
                
                # Parameter
                vol.Required("battery_capacity", default=15.0): vol.Coerce(float),
                vol.Required("charge_delta_threshold", default=10.0): vol.Coerce(float),
                vol.Required("default_usage", default=0.85): vol.Coerce(float),
                vol.Required("min_soc_reserve", default=10.0): vol.Coerce(float),
                vol.Required("wr_lock_value", default=0): vol.Coerce(int),
                vol.Required("wr_unlock_value", default=80): vol.Coerce(int),
                
                # Smart Switches
                vol.Optional("smart_switches"): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch", multiple=True)),
                vol.Required("smart_switch_threshold", default=-1000): vol.Coerce(int),

                vol.Optional("conversation_agent", default="conversation.home_assistant"): selector.EntitySelector(selector.EntitySelectorConfig(domain="conversation")),
            })
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return IntelligentESSOptionsFlowHandler()


class IntelligentESSOptionsFlowHandler(config_entries.OptionsFlow):
    """Behandelt Änderungen an der Konfiguration (Options)."""

    async def async_step_init(self, user_input=None):
        """Haupt-Schritt des Options Flows."""
        if user_input is not None:
            # WICHTIG: In bestehende Optionen mergen, statt sie komplett zu
            # ersetzen. Sonst gingen Hauptschalter, manuelle Zeitfenster und
            # die Number-Werte (z.B. price_delta_threshold) beim Speichern verloren.
            merged = {**self.config_entry.options, **user_input}
            return self.async_create_entry(title="", data=merged)

        config = {**self.config_entry.data, **self.config_entry.options}

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                # --- ALLE VERBLIEBENEN ENTITÄTEN NACHTRÄGLICH BEARBEITBAR ---
                vol.Required("battery_charge_switch", default=config.get("battery_charge_switch", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
                vol.Required("tibber_price_sensor", default=config.get("tibber_price_sensor", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required("tibber_export_sensor", default=config.get("tibber_export_sensor", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required("battery_soc_sensor", default=config.get("battery_soc_sensor", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="battery")),
                
                # Einheitliche PV-Forecast Namen (inkl. kleinem Fallback für alte configs)
                vol.Required("pv_forecast_today_entity", default=config.get("pv_forecast_today_entity", config.get("solar_forecast_sensor", ""))): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional("pv_forecast_tomorrow_entity", default=config.get("pv_forecast_tomorrow_entity", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                
                vol.Required("wr_limit_entity", default=config.get("wr_limit_entity", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
                
                # --- PARAMETER ---
                # Hinweis: Standardverbrauch, Min. SOC, Lade-Preisvorteil und
                # Preis-Abstand sind als Number-Entitäten live regelbar und
                # daher hier NICHT mehr doppelt aufgeführt.
                vol.Required("battery_capacity", default=config.get("battery_capacity", 15.0)): vol.Coerce(float),
                vol.Required("wr_lock_value", default=config.get("wr_lock_value", 0)): vol.Coerce(int),
                vol.Required("wr_unlock_value", default=config.get("wr_unlock_value", 80)): vol.Coerce(int),
                vol.Optional("smart_switches", default=config.get("smart_switches", [])): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch", multiple=True)),
                vol.Required("smart_switch_threshold", default=config.get("smart_switch_threshold", -1000)): vol.Coerce(int),

                vol.Optional("conversation_agent", default=config.get("conversation_agent", "conversation.home_assistant")): selector.EntitySelector(selector.EntitySelectorConfig(domain="conversation")),
            })
        )