import logging
import json
import asyncio
from datetime import timedelta
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Setzt die Buttons für das ESS auf."""
    if DOMAIN not in hass.data or entry.entry_id not in hass.data[DOMAIN]:
        _LOGGER.error("Coordinator noch nicht bereit für Button-Setup")
        return False

    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([
        IntelligentESSUpdateButton(coordinator, entry),
        IntelligentESSTrainAIButton(coordinator, entry),
        IntelligentESSKIButton(coordinator, entry),
    ])
    return True

class IntelligentESSBaseButton(ButtonEntity):
    """Basis-Klasse für unsere Buttons."""
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, name, icon, unique_id_suffix, category=None):
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{unique_id_suffix}"
        self._attr_entity_category = category
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Intelligent ESS",
        }

class IntelligentESSUpdateButton(IntelligentESSBaseButton):
    """Button, um sofort neue Daten von Tibber und Solar-Forecast zu laden."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "Daten jetzt aktualisieren", "mdi:update", "update_btn", EntityCategory.CONFIG)

    async def async_press(self) -> None:
        _LOGGER.info("Manuelles Update der ESS-Daten angefordert.")
        await self.coordinator.async_request_refresh()

class IntelligentESSTrainAIButton(IntelligentESSBaseButton):
    """Button, um das KI-Verbrauchsprofil sofort neu zu berechnen."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "KI-Profil neu anlernen", "mdi:brain", "train_ai_btn", EntityCategory.CONFIG)

    async def async_press(self) -> None:
        _LOGGER.info("Manuelles Neuanlernen des KI-Profils gestartet...")
        if hasattr(self.coordinator, "profile_manager"):
            await self.coordinator.profile_manager.async_update_learning_profile()
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.warning("ProfileManager im Coordinator nicht gefunden. Anlernen fehlgeschlagen.")

class IntelligentESSKIButton(IntelligentESSBaseButton):
    """KI-Strategie Button mit automatischer Fahrplan-Extraktion."""
    def __init__(self, coordinator, entry):
        # Ohne EntityCategory, damit er als primäre Aktion auf dem Dashboard erscheint
        super().__init__(coordinator, entry, "KI Strategie-Check", "mdi:robot-vacuum-variant", "ki_button", None)

    async def async_press(self) -> None:
        _LOGGER.info("--- KI-STRATEGIE-CHECK START ---")
        ki_text = "Analyse läuft..."
        
        try:
            # 1. Alle Daten laden
            config = {**self.entry.data, **self.entry.options}
            data = self.coordinator.data if self.coordinator.data else {}
            readings = self.coordinator.last_readings if hasattr(self.coordinator, 'last_readings') else {}
            
            # --- STATISCHE PARAMETER ---
            cap_kwh = config.get("battery_capacity", 15.0)
            min_soc = config.get("min_soc_reserve", 10.0)
            hold_threshold = config.get("charge_delta_threshold", 10.0)
            
            # --- DYNAMISCHE WERTE ---
            soc_now = readings.get("bat_soc", 0)
            rest_demand = round(data.get("rest_demand_daily", 0), 2)
            solar_remaining = data.get("solar_remaining", 0)
            prices = data.get("prices", [])
            
            # --- KI Zusammenfassung aus Analytics holen ---
            ai_summary = data.get("ai_price_summary", {})
            min_p = ai_summary.get("min_price", 0)
            min_t = ai_summary.get("min_time", "00:00")
            max_p = ai_summary.get("max_price", 0)
            max_t = ai_summary.get("max_time", "00:00")
            avg_p = ai_summary.get("avg_price", 0)
            
            # --- PREISE AUFBEREITEN (Für Kontext) ---
            is_15_min = len(prices) > 48
            step = 4 if is_15_min else 1 
            items_12h = 48 if is_15_min else 12 
            
            price_list = []
            if prices:
                for p in prices[:items_12h:step]:
                    try:
                        time_str = dt_util.parse_datetime(p['start_time']).strftime('%H:%M')
                        price_list.append(f"{time_str}:{round(p['total']*100,1)}ct")
                    except Exception:
                        pass
            
            price_summary = " | ".join(price_list) if price_list else "FEHLER: Keine Preisdaten gefunden!"

            # 2. DER VERBESSERTE PROMPT
            prompt = (
                f"ANALYSE-AUFTRAG INTELLIGENT ESS:\n"
                f"- Akku: {soc_now}% von {cap_kwh}kWh (Min-Reserve: {min_soc}%)\n"
                f"- Solar-Rest: {solar_remaining}kWh | Bedarf 24h: {rest_demand}kWh\n"
                f"- Preis-Eckdaten: Günstigster Preis {min_p}ct um {min_t} Uhr. Teuerster Preis {max_p}ct um {max_t} Uhr. Schnitt: {avg_p}ct.\n"
                f"- Verlauf (12h): {price_summary}\n"
                f"- Ersparnis-Limit: {hold_threshold}ct\n\n"
                "AUFGABEN & LOGIK:\n"
                "1. LADE-CHECK (Netzlade-Optimierung):\n"
                "Reicht der Akku + Solar-Rest, um den Hausbedarf durch die Nacht zu decken?\n"
                f"-> Wenn NEIN: Setze \"charge\": \"YES\". Der Startzeitpunkt MUSS {min_t} Uhr sein. Schätze die \"duration\" in Stunden, um das Defizit auszugleichen.\n\n"
                "2. SPERR-CHECK (Arbitrage / Preisspitzen-Vermeidung):\n"
                f"Ist der teuerste Preis ({max_p}ct) mindestens {hold_threshold}ct teurer als der günstigste ({min_p}ct)?\n"
                "-> Wenn JA und der Akku droht vorher leer zu laufen: Setze \"hold\": \"YES\".\n"
                f"-> WICHTIG: Setze \"hold_start\" auf eine Zeit VOR {max_t} Uhr (um den Akku aufzusparen) und \"hold_end\" EXAKT auf {max_t} Uhr.\n\n"
                "ANTWORTE EXAKT IN DIESEM FORMAT:\n"
                "Kurze Analyse.\n"
                "RESULT: {"
                "\"charge\": \"YES/NO\", \"charge_start\": \"HH:MM\", \"duration\": 2, "
                "\"hold\": \"YES/NO\", \"hold_start\": \"HH:MM\", \"hold_end\": \"HH:MM\", "
                "\"reason\": \"Kurze Begründung\"}"
            )

            # 3. KI-SERVICE AUFRUFEN MIT RETRY-MECHANISMUS
            max_retries = 3
            retry_delay = 5
            full_text = ""
            
            for attempt in range(max_retries):
                try:
                    result = await self.hass.services.async_call(
                        "conversation", "process", 
                        {"text": prompt, "agent_id": "conversation.google_ai_conversation_2"}, 
                        blocking=True, return_response=True
                    )
                    
                    current_text = result["response"]["speech"]["plain"]["speech"]
                    
                    # Abfangen der typischen Google-Limit-Meldungen
                    if "high demand" in current_text or "Sorry, I had a problem" in current_text:
                        if attempt < max_retries - 1:
                            _LOGGER.warning("Gemini API überlastet/Fehler (Versuch %s/%s). Warte %s Sekunden...", attempt + 1, max_retries, retry_delay)
                            await asyncio.sleep(retry_delay)
                            continue 
                        else:
                            full_text = current_text
                            break
                    
                    # Erfolg!
                    full_text = current_text
                    break
                    
                except Exception as inner_e:
                    if attempt < max_retries - 1:
                        _LOGGER.warning("Verbindungsfehler zur KI (Versuch %s/%s). Warte %s Sekunden...", attempt + 1, max_retries, retry_delay)
                        await asyncio.sleep(retry_delay)
                    else:
                        raise inner_e

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
                    
                    new_opts["man_charge_s1_enabled"] = True
                    _LOGGER.info("KI setzt LADE-TIMER: %s für %s Stunden", st_time, dur)

                # --- LOGIK FÜR SPERRE (Hold Slot 1) ---
                if cmd.get("hold") == "YES":
                    h_start = cmd.get("hold_start", "07:00")
                    h_end = cmd.get("hold_end", "09:00")
                    
                    if len(h_start) == 5: h_start += ":00"
                    if len(h_end) == 5: h_end += ":00"
                    
                    new_opts["man_hold_s1_start"] = h_start
                    new_opts["man_hold_s1_end"] = h_end
                    
                    new_opts["man_hold_s1_enabled"] = True
                    _LOGGER.info("KI setzt SPERRE: %s bis %s", h_start, h_end)

                new_opts["ki_reason"] = cmd.get("reason", "Strategie aktualisiert")
                self.hass.config_entries.async_update_entry(self.entry, options=new_opts)
            else:
                ki_text = full_text

        except Exception as e:
            _LOGGER.error("Fehler im KI-Button: %s", e)
            ki_text = f"Fehler bei der Analyse: {str(e)}"

        # 5. BENACHRICHTIGUNG SENDEN
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {"title": "Intelligent ESS KI", "message": f"🤖 {ki_text}", "notification_id": "ess_ki"}
        )