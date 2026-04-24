import logging
import json
from datetime import timedelta
from homeassistant.components.button import ButtonEntity
from homeassistant.util import dt as dt_util
from .analytics import get_solar_forecast, get_tibber_prices 
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
        _LOGGER.info("--- KI-STRATEGIE-CHECK START ---")
        ki_text = "Analyse läuft..."
        
        try:
            # 1. Alle Daten & Optionen laden
            options = self.entry.options
            data = self.coordinator.data if self.coordinator.data else {}
            readings = self.coordinator.last_readings if self.coordinator.last_readings else {}
            
            # --- STATISCHE PARAMETER AUS DEINEM CONFIG-FLOW ---
            cap_kwh = options.get("battery_capacity", 15.0)
            buffer = options.get("safety_buffer", 1.3)
            min_soc = options.get("min_soc_reserve", 10.0)
            hold_threshold = options.get("charge_delta_threshold", 10.0)
            
            # --- DYNAMISCHE WERTE ---
            soc_now = readings.get("bat_soc", 0)
            rest_demand = round(data.get("rest_demand_daily", 0), 2)
            
            # Solar-Rest & Preise (Zentral aus analytics holen)
            from .analytics import get_solar_forecast, get_tibber_prices
            solar_remaining = get_solar_forecast(self.hass, options)
            prices = get_tibber_prices(self.hass, options)
            
            # Preise aufbereiten
            price_list = [f"{dt_util.parse_datetime(p['start_time']).strftime('%H:%M')}:{round(p['total']*100,1)}ct" for p in prices[:12]]
            price_summary = " | ".join(price_list)

            # 2. DER PRÄZISE PROMPT
            prompt = (
                f"ANALYSE-AUFTRAG INTELLIGENT ESS:\n"
                f"- Akku: {soc_now}% von {cap_kwh}kWh (Min-Reserve: {min_soc}%)\n"
                f"- Solar-Rest: {solar_remaining}kWh | Bedarf 24h: {rest_demand}kWh\n"
                f"- Preise (12h): {price_summary}\n"
                f"- Ersparnis-Limit: {hold_threshold}ct\n\n"
                "AUFGABEN & LOGIK:\n"
                "1. LADE-CHECK (Netzlade-Optimierung):\n"
                "Reicht der Akku + Solar-Rest, um den Hausbedarf durch die Nacht zu decken?\n"
                "-> Wenn NEIN: Setze \"charge\": \"YES\". Suche in der Preisliste den ABSOLUT GÜNSTIGSTEN Zeitpunkt und setze diesen als \"charge_start\". Schätze die \"duration\" in Stunden, um das Defizit auszugleichen.\n\n"
                "2. SPERR-CHECK (Arbitrage / Preisspitzen-Vermeidung):\n"
                f"Finde die teuerste Preisspitze in der Liste. Ist diese mindestens {hold_threshold}ct teurer als der günstigste Preis?\n"
                "-> Wenn JA und der Akku droht vorher leer zu laufen: Setze \"hold\": \"YES\".\n"
                "-> WICHTIG: Setze \"hold_start\" auf die Zeit VOR der Spitze (um den Akku aufzusparen) und \"hold_end\" EXAKT auf den BEGINN der Preisspitze. (Beispiel: Spitze ist um 07:00 Uhr -> hold_end: \"07:00\", damit das Haus ab 07:00 Uhr den Akku nutzen kann statt teuren Netzstrom).\n\n"
                "ANTWORTE EXAKT IN DIESEM FORMAT:\n"
                "Kurze Analyse.\n"
                "RESULT: {"
                "\"charge\": \"YES/NO\", \"charge_start\": \"HH:MM\", \"duration\": 2, "
                "\"hold\": \"YES/NO\", \"hold_start\": \"HH:MM\", \"hold_end\": \"HH:MM\", "
                "\"reason\": \"Kurze Begründung mit Preisangaben\"}"
            )

            # 3. KI-SERVICE AUFRUFEN (Das fehlte in deinem Code!)
            result = await self.hass.services.async_call(
                "conversation", "process", 
                # ACHTUNG: Prüfe ob "conversation.google_ai_conversation_2" noch dein korrekter Agent ist!
                {"text": prompt, "agent_id": "conversation.google_ai_conversation_2"}, 
                blocking=True, return_response=True
            )
            
            # Die Antwort der KI extrahieren
            full_text = result["response"]["speech"]["plain"]["speech"]

            # 4. ANTWORT VERARBEITEN (JSON extrahieren)
            if "RESULT:" in full_text:
                ki_text = full_text.split("RESULT:")[0].strip()
                data_str = full_text.split("RESULT:")[1].strip().replace("```json", "").replace("```", "")
                cmd = json.loads(data_str)
                
                new_opts = dict(self.entry.options)
                
                # --- LOGIK FÜR LADEN (Slot 1) ---
                if cmd.get("charge") == "YES":
                    st_time = cmd.get("charge_start", "00:00")
                    if len(st_time) == 5: st_time += ":00"
                    new_opts["man_charge_s1_start"] = st_time
                    
                    st_hour = int(st_time.split(":")[0])
                    dur = int(cmd.get("duration", 3))
                    new_opts["man_charge_s1_end"] = f"{(st_hour + dur) % 24:02d}:00:00"
                    _LOGGER.info("KI setzt LADE-TIMER: %s für %s Stunden", st_time, dur)

                # --- LOGIK FÜR SPERRE (Hold Slot 1) ---
                if cmd.get("hold") == "YES":
                    h_start = cmd.get("hold_start", "07:00")
                    h_end = cmd.get("hold_end", "09:00")
                    
                    # Format-Fixing HH:MM:SS
                    if len(h_start) == 5: h_start += ":00"
                    if len(h_end) == 5: h_end += ":00"
                    
                    new_opts["man_hold_s1_start"] = h_start
                    new_opts["man_hold_s1_end"] = h_end
                    _LOGGER.info("KI setzt SPERRE: %s bis %s", h_start, h_end)

                new_opts["ki_reason"] = cmd.get("reason", "Strategie aktualisiert")
                self.hass.config_entries.async_update_entry(self.entry, options=new_opts)
            else:
                ki_text = full_text # Falls die KI sich nicht ans Format gehalten hat

        except Exception as e:
            _LOGGER.error("Fehler im KI-Button: %s", e)
            ki_text = f"Fehler bei der Analyse: {str(e)}"

        # 5. BENACHRICHTIGUNG SENDEN
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {"title": "Intelligent ESS", "message": f"🤖 {ki_text}", "notification_id": "ess_ki"}
        )