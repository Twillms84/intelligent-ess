import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.core import callback

_LOGGER = logging.getLogger(__name__)
DOMAIN = "intelligent_ess"

class IntelligentESSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="Intelligent ESS", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                # Sensoren (PV jetzt Multiple)
                vol.Required("pv_production_sensor"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy", multiple=True)),
                vol.Required("grid_consumption_sensor"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy")),
                vol.Required("grid_export_sensor"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy")),
                vol.Required("bat_charge_sensor"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy")),
                vol.Required("bat_discharge_sensor"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy")),
                vol.Required("tibber_price_sensor"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required("tibber_export_sensor"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required("battery_soc_sensor"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="battery")),
                vol.Required("solar_forecast_sensor"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                
                # Hardware Steuerung
                vol.Required("battery_charge_switch"): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
                vol.Required("wr_limit_entity"): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
                
                # Parameter (Saubere Validierung)
                vol.Required("battery_capacity", default=15.0): vol.Coerce(float),
                vol.Required("charge_delta_threshold", default=10.0): vol.Coerce(float),
                vol.Required("sun_yield_threshold", default=20.0): vol.Coerce(float),
                vol.Required("safety_buffer", default=1.3): vol.Coerce(float),
                vol.Required("default_usage", default=0.85): vol.Coerce(float),
                vol.Required("min_soc_reserve", default=10.0): vol.Coerce(float),
                vol.Required("wr_lock_value", default=0): vol.Coerce(int),
                vol.Required("wr_unlock_value", default=80): vol.Coerce(int),
                
                # Smart Switches
                vol.Optional("smart_switches"): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch", multiple=True)),
                vol.Required("smart_switch_threshold", default=-1000): vol.Coerce(int),
            })
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return IntelligentESSOptionsFlowHandler(config_entry)


class IntelligentESSOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._cfg = config_entry 

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Merge data und options für die Anzeige der aktuellen Werte
        config = {**self._cfg.data, **self._cfg.options}

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                # --- ALLE ENTITÄTEN NACHTRÄGLICH BEARBEITBAR ---
                vol.Required("pv_production_sensor", default=config.get("pv_production_sensor", [])): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy", multiple=True)),
                vol.Required("grid_consumption_sensor", default=config.get("grid_consumption_sensor", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy")),
                vol.Required("grid_export_sensor", default=config.get("grid_export_sensor", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy")),
                vol.Required("bat_charge_sensor", default=config.get("bat_charge_sensor", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy")),
                vol.Required("bat_discharge_sensor", default=config.get("bat_discharge_sensor", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="energy")),
                vol.Required("battery_charge_switch", default=config.get("battery_charge_switch", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
                vol.Required("tibber_price_sensor", default=config.get("tibber_price_sensor", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required("tibber_export_sensor", default=config.get("tibber_export_sensor", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required("battery_soc_sensor", default=config.get("battery_soc_sensor", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="battery")),
                vol.Required("solar_forecast_sensor", default=config.get("solar_forecast_sensor", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required("wr_limit_entity", default=config.get("wr_limit_entity", "")): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
                
                # --- PARAMETER ---
                vol.Required("default_usage", default=config.get("default_usage", 0.85)): vol.Coerce(float),
                vol.Required("sun_yield_threshold", default=config.get("sun_yield_threshold", 20.0)): vol.Coerce(float),
                vol.Required("safety_buffer", default=config.get("safety_buffer", 1.3)): vol.Coerce(float),
                vol.Required("battery_capacity", default=config.get("battery_capacity", 15.0)): vol.Coerce(float),
                vol.Required("charge_delta_threshold", default=config.get("charge_delta_threshold", 10.0)): vol.Coerce(float),
                vol.Required("min_soc_reserve", default=config.get("min_soc_reserve", 10.0)): vol.Coerce(float),
                vol.Required("wr_lock_value", default=config.get("wr_lock_value", 0)): vol.Coerce(int),
                vol.Required("wr_unlock_value", default=config.get("wr_unlock_value", 80)): vol.Coerce(int),
                vol.Optional("smart_switches", default=config.get("smart_switches", [])): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch", multiple=True)),
                vol.Required("smart_switch_threshold", default=config.get("smart_switch_threshold", -1000)): vol.Coerce(int),
            })
        )