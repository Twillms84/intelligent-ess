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
            solar_remaining = data.get("solar_remaining", 0)
            prices = data.get("prices", [])
            
            # --- 3. EXAKTE NACHTRESERVE BERECHNEN ---
            now = dt_util.now()
            autarky_str = data.get("autarky_start_tomorrow", "08:00")
            
            # Fallback auf 8 Uhr, falls "Nicht erreicht" oder "Keine Stundenwerte"
            autarky_hour = 8 
            if ":" in autarky_str:
                try:
                    autarky_hour = int(autarky_str.split(":")[0])
                except ValueError:
                    pass
            
            # Stündlichen Bedarf bis zum Autarkiestart summieren
            night_demand_kwh = 0.0
            db = self.coordinator.profile_manager._get_db() if hasattr(self.coordinator, "profile_manager") else {}
            default_usage = float(config.get("default_usage", 0.85))

            # Smarte Hilfsfunktion zum sicheren Auslesen der Datenbank
            def get_hourly_demand(day_str, hour_str):
                hr_val = db.get(day_str, {}).get(hour_str, {})
                if isinstance(hr_val, dict) and hr_val:
                    return sum(float(v) for v in hr_val.values()) / len(hr_val)
                elif isinstance(hr_val, (float, int)):
                    return float(hr_val)
                return default_usage

            # A) Rest von heute (aktuelle Stunde bis 23 Uhr)
            today_wd = str(now.weekday())
            for h in range(now.hour, 24):
                night_demand_kwh += get_hourly_demand(today_wd, str(h))
            
            # B) Morgen bis Autarkiestart (0 Uhr bis z.B. 7 Uhr)
            tomorrow_wd = str((now + timedelta(days=1)).weekday())
            for h in range(0, autarky_hour):
                night_demand_kwh += get_hourly_demand(tomorrow_wd, str(h))
            
            night_demand_kwh = round(night_demand_kwh, 2)

            # C) Energie-Bilanz ziehen
            usable_battery_kwh = max(0.0, (soc_now - min_soc) / 100.0 * cap_kwh)
            total_available_energy = usable_battery_kwh + solar_remaining
            
            energy_balance = total_available_energy - night_demand_kwh
            
            if energy_balance < 0:
                nachtreserve_kwh = round(abs(energy_balance), 2)
                nachtreserve_aktiv = True
                
                # Überschlagsrechnung: Wann ist der Akku ca. leer?
                hours_to_autarky = 0
                if autarky_hour > now.hour:
                    hours_to_autarky = autarky_hour - now.hour
                else:
                    hours_to_autarky = (24 - now.hour) + autarky_hour
                
                hours_to_autarky = max(1, hours_to_autarky) # Verhindert Division durch 0
                avg_hourly_demand = night_demand_kwh / hours_to_autarky if night_demand_kwh > 0 else default_usage
                hours_battery_lasts = usable_battery_kwh / avg_hourly_demand if avg_hourly_demand > 0 else 99
                
                empty_time = now + timedelta(hours=hours_battery_lasts)
                empty_time_str = f"ca. {empty_time.strftime('%H:%M')} Uhr"
            else:
                nachtreserve_kwh = 0.0
                nachtreserve_aktiv = False
                empty_time_str = "Reicht bis zum Autarkiestart"

            # 2. DER DATEN-GETRIEBENE PROMPT
            prompt = (
                "Du bist 'Intelligent ESS', ein smarter KI-Energiemanager für ein Smart Home. "
                "Hier sind die harten Fakten für heute:\n\n"
                f"🔋 Batterie-Stand: {soc_now}% (Nutzbare Restkapazität: {round(usable_battery_kwh, 1)} kWh)\n"
                f"☀️ Solar-Rest für heute: {solar_remaining} kWh\n"
                f"🏠 Bedarf bis Autarkiestart morgen früh ({autarky_hour}:00 Uhr): {night_demand_kwh} kWh\n"
                f"⚠️ Analytisches Defizit: {nachtreserve_kwh} kWh (Wird Akku leerlaufen? {'JA' if nachtreserve_aktiv else 'NEIN'})\n"
                f"⏳ Prognose ohne Eingriff: Akku ist voraussichtlich um {empty_time_str} LEER.\n"
                f"📈 Strompreise: Tiefstpreis {min_p}ct (um {min_t}) | Höchstpreis {max_p}ct (um {max_t}) | Schnitt: {avg_p}ct\n"
                f"⏱️ Preisverlauf (12h): {price_summary}\n"
                f"💰 Ersparnis-Schwelle: {hold_threshold} ct\n\n"
                "DEINE AUFGABEN:\n"
                "1. AUSFÜHRLICHER NUTZER-BERICHT:\n"
                "Schreibe eine detaillierte, datenbasierte Analyse (ca. 4-6 Sätze). Nenne UNBEDINGT konkrete Zahlen "
                "(die nutzbare Restkapazität in kWh, das exakte Defizit, wichtige Uhrzeiten und Strompreise in ct). "
                "Erkläre dem Nutzer logisch und transparent deine Taktik: Wann genau kaufst du Netzstrom und für welche teure Stunde "
                "sparst du die restliche Batterie auf? Sei informativ und professionell.\n\n"
                "2. SYSTEM-LOGIK:\n"
                "- SPERR-CHECK (Arbitrage): Wenn ein Defizit besteht, MÜSSEN wir Netzstrom beziehen. Das tun wir am besten, wenn er billig ist! "
                "Setze \"hold\": \"YES\", um die Batterie in den GÜNSTIGSTEN Stunden zu sperren. "
                f"🚨 WICHTIG: Der Akku läuft voraussichtlich schon {empty_time_str} leer! Deine Sperre ('hold_start') MUSS "
                "vor diesem Zeitpunkt beginnen. Wenn du die Sperre erst in den günstigsten Stunden setzt (z.B. von 4-6 Uhr), der Akku aber "
                "vorher schon leer ist, war die Sperre sinnlos. Ziehe die Sperre also rechtzeitig vor, um Netzstrom zu nutzen, wenn er am billigsten "
                "VERFÜGBAR ist, und rette die restliche Akku-Ladung physisch in die teuerste Morgenstunde ({max_t} Uhr).\n"
                "- LADE-CHECK: Nur wenn der Tiefstpreis extrem billig ist, setze zusätzlich \"charge\": \"YES\" um {min_t} Uhr.\n\n"
                "ANTWORTE EXAKT IN DIESEM FORMAT:\n"
                "[Dein ausführlicher, zahlenbasierter Analyse-Text für den Nutzer]\n"
                "RESULT: {\n"
                "\"charge\": \"YES/NO\", \"charge_start\": \"HH:MM\", \"duration\": 2, "
                "\"hold\": \"YES/NO\", \"hold_start\": \"HH:MM\", \"hold_end\": \"HH:MM\", "
                "\"reason\": \"Kurze technische Begründung\"}"
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