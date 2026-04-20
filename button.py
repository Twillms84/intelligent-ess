import logging
import json
from datetime import timedelta
from homeassistant.components.button import ButtonEntity
from homeassistant.util import dt as dt_util
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Setzt den Button-Eintrag auf."""
    if DOMAIN not in hass.data or entry.entry_id not in hass.data[DOMAIN]:
        _LOGGER.error("Coordinator noch nicht bereit für Button-Setup")
        return False

    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([IntelligentESSKIButton(coordinator, entry)])
    return True

class IntelligentESSKIButton(ButtonEntity):
    """KI-Strategie Button mit automatischer Fahrplan-Extraktion."""
    def __init__(self, coordinator, entry):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Intelligent ESS KI Strategie-Check"
        self._attr_unique_id = f"{entry.entry_id}_ki_button"
        self._attr_icon = "mdi:robot-vacuum-variant"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Intelligent ESS",
        }

    async def async_press(self) -> None:
        """KI-Analyse mit gezieltem Agent-Call und Datenextraktion."""
        _LOGGER.info("--- KI-STRATEGIE-CHECK START ---")
        
        now = dt_util.now()
        now_time = now.strftime("%H:%M") # Hier wird now_time definiert!
        
        data = self.coordinator.data if self.coordinator.data else {}
        readings = self.coordinator.last_readings if self.coordinator.last_readings else {}
        
        # Einstellungen aus den Options holen (behcebt NameError: config)
        options = self.entry.options
        min_soc = options.get("min_soc_reserve", 10.0)
        
        soc_now = readings.get("bat_soc", 0)
        rest_demand = round(data.get("rest_demand_daily", 0), 2)
        
        # Solar Forecast
        solar_remaining = 0
        solar_entity = options.get("solar_forecast_sensor")
        if solar_entity:
            s_state = self.hass.states.get(solar_entity)
            if s_state and s_state.state not in ["unknown", "unavailable"]:
                try:
                    solar_remaining = round(float(s_state.state), 2)
                except ValueError: pass

        # PREIS-LOGIK
        prices = data.get("prices_raw", [])
        price_now = "unbekannt"
        best_info = "Keine Preis-Prognose verfügbar."
        
        if prices:
            now = dt_util.now()
            # 1. Aktuellen Preis finden
            current_slots = [p for p in prices if dt_util.parse_datetime(p['start_time']) <= now <= (dt_util.parse_datetime(p['start_time']) + timedelta(minutes=15))]
            if current_slots:
                price_now = round(current_slots[0].get('price_per_kwh', 0) * 100, 1)
            
            # 2. Günstigsten Slot finden
            future_slots = [p for p in prices if dt_util.parse_datetime(p['start_time']) >= (now - timedelta(minutes=14))]
            if future_slots:
                cheapest = min(future_slots, key=lambda x: x.get('price_per_kwh', 999))
                t_start = dt_util.parse_datetime(cheapest['start_time']).strftime("%H:%M")
                p_val = round(cheapest['price_per_kwh'] * 100, 1)
                
                is_tomorrow = dt_util.parse_datetime(cheapest['start_time']).date() > now.date()
                tag_info = "morgen" if is_tomorrow else "heute"
                best_info = f"Günstigster Preis ({tag_info}): {p_val}ct um {t_start} Uhr."

        # EXPERTEN-PROMPT MIT JSON-RESULTAT
        prompt = (
            f"AKTUELLE ZEIT: {now_time}\n"
            f"STATUS: Akku {soc_now}%, Solar-Rest {solar_remaining}kWh, Bedarf 24h {rest_demand}kWh.\n"
            f"PREISE: Aktuell {price_now}ct, Günstigster Slot: {best_info}\n\n"
            "DEINE AUFGABE:\n"
            "1. Berechne das Defizit für die kommende Nacht.\n"
            "2. Wenn Akku + Solar nicht reichen, entscheide dich für Netz-Laden (YES).\n"
            f"3. Falls du JETZT laden willst, nutze Startzeit '{now_time}'.\n\n"
            "ANTWORTE GENAU IN DIESEM FORMAT:\n"
            "Hier deine Analyse in maximal 3 Sätzen.\n\n"
            "RESULT: {\"charge\": \"YES/NO\", \"start\": \"HH:MM\", \"reason\": \"Begründung\"}"
        )

        try:
            result = await self.hass.services.async_call(
                "conversation", "process", 
                {
                    "text": prompt,
                    "agent_id": "conversation.google_ai_conversation_2" # PRÜFEN OB KORREKT!
                },
                blocking=True, return_response=True
            )
            
            full_text = result["response"]["speech"]["plain"]["speech"]
            _LOGGER.debug("KI Roh-Antwort: %s", full_text)

            # Sichereres Splitting (Groß/Kleinschreibung ignorieren)
            import re
            parts = re.split(r"RESULT:", full_text, flags=re.IGNORECASE)
            ki_text = parts[0].strip()
            
            if len(parts) > 1:
                try:
                    data_str = parts[1].strip()
                    # Manchmal setzt die KI Markdown-Codeblöcke um das JSON
                    data_str = data_str.replace("```json", "").replace("```", "").strip()
                    cmd = json.loads(data_str)
                    
                    self.coordinator.data["ki_charge_decision"] = cmd.get("charge", "NO")
                    self.coordinator.data["ki_charge_start"] = cmd.get("start", "00:00")
                    self.coordinator.data["ki_reason"] = cmd.get("reason", "Strategie berechnet")
                except Exception as json_err:
                    _LOGGER.error("JSON Fehler: %s bei String: %s", json_err, data_str)
            else:
                _LOGGER.warning("KI hat kein 'RESULT:' geliefert. Antwort war: %s", full_text)
                ki_text = full_text # Falls kein RESULT da ist, nimm den ganzen Text

        # Notification senden
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {
                "title": "Intelligent ESS KI-Strategie",
                "message": (
                    f"🤖 {ki_text}\n\n"
                    f"---\n"
                    f"*Fahrplan: Netz-Laden {self.coordinator.data.get('ki_charge_decision', 'NO')} "
                    f"um {self.coordinator.data.get('ki_charge_start', '00:00')}*"
                ),
                "notification_id": "ess_ki_recommendation"
            }
        )