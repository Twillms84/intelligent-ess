import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_time_change
from .coordinator import IntelligentESSCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# WICHTIG: Stelle sicher, dass "sensor" in dieser Liste steht!
PLATFORMS = ["sensor", "number", "switch", "button", "time"]

# Standard-Zeiten für die automatische Tages-Vorschau (morgens / abends).
DEFAULT_PREVIEW_TIMES = {"preview_time_morning": (7, 0), "preview_time_evening": (18, 30)}


def _parse_hour_minute(raw, fallback):
    """'HH:MM[:SS]' -> (hour, minute); bei Unsinn der Fallback."""
    try:
        parts = str(raw).split(":")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError, TypeError):
        return fallback


def _schedule_daily_previews(hass, entry, coordinator):
    """Registriert die geplanten Vorschau-Benachrichtigungen (morgens/abends).

    - Schlüssel nie gesetzt  -> Standardzeit (aktiv).
    - Schlüssel leer ("")     -> bewusst deaktiviert.
    - Schlüssel "HH:MM[:SS]"  -> individuelle Zeit.
    Die Abmelder hängen an entry.async_on_unload, werden also bei jedem
    Options-Reload sauber entfernt und mit den neuen Zeiten neu gesetzt.
    """
    config = {**entry.data, **entry.options}

    async def _post_scheduled_preview(now):
        try:
            await coordinator.async_request_refresh()
            await coordinator.async_post_preview()
        except Exception as ex:  # noqa: BLE001
            _LOGGER.warning("Geplante Automatik-Vorschau fehlgeschlagen: %s", ex)

    for key, fallback in DEFAULT_PREVIEW_TIMES.items():
        raw = config.get(key, "__DEFAULT__")
        if raw == "__DEFAULT__":
            hh, mm = fallback            # nie konfiguriert -> Standardzeit
        elif raw in (None, ""):
            continue                     # bewusst geleert -> aus
        else:
            hh, mm = _parse_hour_minute(raw, fallback)
        unsub = async_track_time_change(hass, _post_scheduled_preview, hour=hh, minute=mm, second=0)
        entry.async_on_unload(unsub)
        _LOGGER.debug("Tages-Vorschau geplant für %02d:%02d (%s)", hh, mm, key)

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

    # 5. Geplante Tages-Vorschau (morgens/abends) als Benachrichtigung
    _schedule_daily_previews(hass, entry, coordinator)

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