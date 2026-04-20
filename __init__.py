import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from .coordinator import IntelligentESSCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# WICHTIG: Stelle sicher, dass "sensor" in dieser Liste steht!
PLATFORMS = ["sensor", "number", "switch", "button"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Setzt die Integration über die UI auf."""
    
    # 1. Coordinator erstellen
    coordinator = IntelligentESSCoordinator(hass, entry)
    
    # 2. In hass.data speichern
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    # 3. Den ersten Datensatz holen (Initialisierung)
    await coordinator.async_config_entry_first_refresh()

    # 4. Plattformen (Sensoren etc.) laden
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True

async def update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Reagiert auf Options-Änderungen."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Entlädt die Integration."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok