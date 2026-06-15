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
            
            # --- BASISWERTE & GATES (zentral im Coordinator vorberechnet) ---
            gates = data.get("gates", {}) or {}
            cap_kwh = float(config.get("battery_capacity", 15.0))
            min_soc = float(config.get("min_soc_reserve", 10.0))
            price_delta = float(config.get("price_delta_threshold", 5.0))

            soc_now = readings.get("bat_soc", 0)
            solar_remaining = data.get("solar_remaining", 0)
            prices = data.get("prices", [])
            default_usage = float(config.get("default_usage", 0.85))

            now = dt_util.now()
            autarky_str = data.get("autarky_time_tomorrow", "08:00")
            autarky_hour = 8
            if isinstance(autarky_str, str) and ":" in autarky_str:
                try:
                    autarky_hour = int(autarky_str.split(":")[0])
                except ValueError:
                    pass

            usable_battery_kwh = gates.get(
                "usable_battery", max(0.0, (soc_now - min_soc) / 100.0 * cap_kwh)
            )
            night_demand_kwh = data.get("night_demand", 0.0)
            nacht_defizit = gates.get("nacht_defizit", 0.0)
            smartcharge_allowed = gates.get("smartcharge_allowed", False)
            smarthold_allowed = gates.get("smarthold_allowed", False)
            morning_price_high = gates.get("morning_price_high", False)
            pv_day_balance = gates.get("pv_day_balance", 0.0)

            # Ueberschlag: Wann ist der Akku ohne Eingriff voraussichtlich leer?
            if nacht_defizit > 0:
                if autarky_hour > now.hour:
                    hours_to_autarky = autarky_hour - now.hour
                else:
                    hours_to_autarky = (24 - now.hour) + autarky_hour
                hours_to_autarky = max(1, hours_to_autarky)
                avg_hourly_demand = (night_demand_kwh / hours_to_autarky) if night_demand_kwh > 0 else default_usage
                hours_battery_lasts = (usable_battery_kwh / avg_hourly_demand) if avg_hourly_demand > 0 else 99
                empty_time = now + timedelta(hours=hours_battery_lasts)
                empty_time_str = f"ca. {empty_time.strftime('%H:%M')} Uhr"
            else:
                empty_time_str = "Reicht bis zum Autarkiestart"

            # 2. DER DATEN-GETRIEBENE PROMPT
            # Preis-Eckwerte aus der vorberechneten Zusammenfassung ableiten.
            ai_summary = data.get("ai_price_summary", {}) or {}
            min_p = ai_summary.get("min_price", "?")
            max_p = ai_summary.get("max_price", "?")
            min_t = ai_summary.get("min_time", "?")
            max_t = ai_summary.get("max_time", "?")
            avg_p = ai_summary.get("avg_price", "?")

            # Kompakte 12h-Preisuebersicht (Stunde=Preis in ct) fuer den Prompt.
            summary_parts = []
            for p in prices[:12]:
                try:
                    t_label = dt_util.parse_datetime(p["start_time"]).strftime("%H:%M")
                    summary_parts.append(f"{t_label}={round(p['total'] * 100, 1)}ct")
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
            price_summary = " | ".join(summary_parts) if summary_parts else "keine Preisdaten"

            gate_charge_txt = "OFFEN – Laden erlaubt" if smartcharge_allowed else "GESCHLOSSEN – NICHT laden"
            gate_hold_txt = "OFFEN – Sperre erlaubt" if smarthold_allowed else "GESCHLOSSEN – NICHT sperren"

            prompt = (
                "Du bist 'Intelligent ESS', ein KI-Energiemanager für ein Smart Home. "
                "Triff Entscheidungen ausschließlich auf Basis dieser vorberechneten Fakten.\n\n"
                "=== BASISWERTE ===\n"
                f"🔋 Batterie: {soc_now}% – nutzbar {round(usable_battery_kwh, 1)} kWh (Reserve {min_soc}%)\n"
                f"☀️ Solar-Rest heute: {solar_remaining} kWh\n"
                f"🏠 Bedarf bis Autarkiestart ({autarky_hour:02d}:00 Uhr): {night_demand_kwh} kWh\n"
                f"⚠️ Nacht-Defizit: {nacht_defizit} kWh – Akku ohne Eingriff leer um {empty_time_str}\n"
                f"📈 Preis: Tief {min_p}ct ({min_t}) | Hoch {max_p}ct ({max_t}) | Schnitt {avg_p}ct\n"
                f"⏱️ Verlauf (12h): {price_summary}\n\n"
                "=== ENTSCHEIDUNGS-GATES (verbindlich!) ===\n"
                f"SmartCharge: {gate_charge_txt}\n"
                f"   (Tages-PV-Bilanz {pv_day_balance} kWh – Laden nur sinnvoll, wenn zu wenig PV erwartet wird)\n"
                f"SmartHold: {gate_hold_txt}\n"
                f"   (Nachtreserve unzureichend: {'JA' if nacht_defizit > 0 else 'NEIN'} | "
                f"Morgenpreis hoch: {'JA' if morning_price_high else 'NEIN'})\n\n"
                "DEINE AUFGABEN:\n"
                "1. NUTZER-BERICHT (4-6 Sätze, konkrete Zahlen): Erkläre transparent, was du tust und warum. "
                "Sind beide Gates GESCHLOSSEN, begründe, warum kein Eingriff nötig ist (genug PV/Reserve).\n"
                "2. STEUERUNG – halte dich strikt an die Gates:\n"
                "- charge: Setze NUR dann 'YES', wenn SmartCharge OFFEN ist. Lege das Ladefenster in die günstigsten "
                f"Stunden (z. B. ab {min_t}).\n"
                "- hold: Setze NUR dann 'YES', wenn SmartHold OFFEN ist. Die Sperre ('hold_start') MUSS beginnen, BEVOR der "
                f"Akku leer ist ({empty_time_str}), und bis in die teuerste Morgenstunde ({max_t}) reichen, um den Akku "
                "dorthin zu retten.\n"
                "- Ist ein Gate GESCHLOSSEN, setze den jeweiligen Wert zwingend auf 'NO'.\n\n"
                "ANTWORTE EXAKT IN DIESEM FORMAT:\n"
                "[Dein Analyse-Text für den Nutzer]\n"
                "RESULT: {\n"
                "\"charge\": \"YES/NO\", \"charge_start\": \"HH:MM\", \"duration\": 2, "
                "\"hold\": \"YES/NO\", \"hold_start\": \"HH:MM\", \"hold_end\": \"HH:MM\", "
                "\"reason\": \"Kurze technische Begründung\"}"
            )

            # 3. KI-SERVICE AUFRUFEN MIT RETRY-MECHANISMUS
            max_retries = 3
            retry_delay = 5
            full_text = ""
            agent_id = config.get("conversation_agent") or "conversation.home_assistant"
            
            for attempt in range(max_retries):
                try:
                    result = await self.hass.services.async_call(
                        "conversation", "process", 
                        {"text": prompt, "agent_id": agent_id}, 
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

                # --- LADEN (Slot 1) – nur wenn SmartCharge-Gate OFFEN ist ---
                want_charge = (cmd.get("charge") == "YES") and smartcharge_allowed
                if want_charge:
                    st_time = cmd.get("charge_start", "00:00")
                    if len(st_time) == 5:
                        st_time += ":00"
                    new_opts["man_charge_s1_start"] = st_time

                    st_hour = int(st_time.split(":")[0])
                    dur = int(cmd.get("duration", 3))
                    new_opts["man_charge_s1_end"] = f"{(st_hour + dur) % 24:02d}:00:00"
                    new_opts["man_charge_s1_enabled"] = True
                    _LOGGER.info("KI setzt LADE-TIMER: %s für %s Stunden", st_time, dur)
                else:
                    # KI sagt NO oder Gate geschlossen -> Slot deaktivieren (keine Altlast).
                    new_opts["man_charge_s1_enabled"] = False
                    if cmd.get("charge") == "YES" and not smartcharge_allowed:
                        _LOGGER.info("KI wollte laden, aber SmartCharge-Gate ist geschlossen – ignoriert.")

                # --- SPERRE (Hold Slot 1) – nur wenn SmartHold-Gate OFFEN ist ---
                want_hold = (cmd.get("hold") == "YES") and smarthold_allowed
                if want_hold:
                    h_start = cmd.get("hold_start", "07:00")
                    h_end = cmd.get("hold_end", "09:00")
                    if len(h_start) == 5:
                        h_start += ":00"
                    if len(h_end) == 5:
                        h_end += ":00"
                    new_opts["man_hold_s1_start"] = h_start
                    new_opts["man_hold_s1_end"] = h_end
                    new_opts["man_hold_s1_enabled"] = True
                    _LOGGER.info("KI setzt SPERRE: %s bis %s", h_start, h_end)
                else:
                    new_opts["man_hold_s1_enabled"] = False
                    if cmd.get("hold") == "YES" and not smarthold_allowed:
                        _LOGGER.info("KI wollte sperren, aber SmartHold-Gate ist geschlossen – ignoriert.")

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