import logging
import json
import os
import asyncio
from datetime import timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .scheduler import calculate_strategy
# WICHTIG: Hier 'async_get_raw_states' importieren!
from .analytics import update_forecasts_and_finances, async_get_raw_states, get_tibber_prices, get_solar_forecast
from .profile_manager import ProfileManager
from .logic_engine import ESSLogicEngine
from .strategy import evaluate_strategy

_LOGGER = logging.getLogger(__name__)

class IntelligentESSCoordinator(DataUpdateCoordinator):

    def __init__(self, hass, entry):
        super().__init__(
            hass, 
            _LOGGER, 
            name=DOMAIN, 
            update_interval=timedelta(minutes=1)
        )
        self.entry = entry
        self.last_readings = {}
        self.savings_path = hass.config.path("intelligent_ess_savings.json")
        
        # 1. Config aus data und options zusammenführen
        config = dict(entry.data)
        if hasattr(entry, "options"):
            config.update(entry.options)
            
        # 2. Den neuen ProfileManager mit hass und config starten (storage_path ist weg!)
        self.profile_manager = ProfileManager(hass, config)
        
        self._savings_loaded = False
        self._last_learning_date = None # Merkt sich, wann die KI zuletzt trainiert wurde
        self._update_cycles = 0         # Zähler für das Speichern der Savings
        self._switch_timers = {}        # Einschalt-Zeitpunkte der Smart-Switches
        self._hold_latch_until = None   # Anti-Flatter-Latch für autonomes SmartHold

        self.data = {
            "house_kw": 0.0,
            "net_watt": 0.0,
            "strat": "NORMAL",
            "strat_msg": "Initialisierung...",
            "rest_demand_daily": 0.0,
            "forecast_current_hour": 0.0,
            "forecast_next_hour": 0.0,
            "morning_reserve": 0.0,
            "fahrplan": "Warte auf Daten...",
            "savings": {"total": 0.0, "solar": 0.0, "hold": 0.0, "load": 0.0}
        }

    async def _async_update_data(self):
        try:
            # 1. DATEI BEIM START LADEN (Finanzielle Ersparnisse)
            if not self._savings_loaded:
                await self.hass.async_add_executor_job(self._load_savings)
                self._savings_loaded = True

            # 2. KI-PROFIL TRAINIEREN (1x täglich oder beim ersten Start)
            current_date = dt_util.now().date()
            if self._last_learning_date != current_date:
                _LOGGER.debug("Starte KI-Profil-Training aus Long-Term-Statistics...")
                await self.profile_manager.async_update_learning_profile()
                self._last_learning_date = current_date

            # 3. DATEN-AKQUISE
            config = {**self.entry.data, **self.entry.options}
            # WICHTIG: Hier await nutzen!
            current = await async_get_raw_states(self.hass, config)
            if not current: 
                return self.data
                
            # 4. DELTA-BERECHNUNG (Für Analytics/Finanzen)
            deltas = {}
            house_kwh = 0.0
            
            if self.last_readings:
                # Deltas der fortlaufenden Zähler ermitteln
                deltas = {k: current[k] - self.last_readings[k] for k in current if k in self.last_readings}
                
                # Plausibilitäts-Check (Ausreißer filtern, wie vorher im ProfileManager)
                if not any(v < -0.001 or v > 1.2 for v in deltas.values()):
                    house_kwh = max(0, (
                        deltas.get("pv", 0) + deltas.get("grid_in", 0) + 
                        deltas.get("bat_dis", 0) - deltas.get("grid_out", 0) - 
                        deltas.get("bat_chg", 0)
                    ))
                    self.data["house_kw"] = round(house_kwh * 60, 3) # Fürs Frontend hochrechnen

                    # Netto-Netzleistung (W): positiv = Bezug, negativ = Einspeisung/Ueberschuss
                    grid_delta_kwh = deltas.get("grid_in", 0) - deltas.get("grid_out", 0)
                    self.data["net_watt"] = round(grid_delta_kwh * 60000, 1)
                else:
                    _LOGGER.warning("Unplausible Zähler-Deltas erkannt. Überspringe Finanzen für diese Minute.")
                    deltas = {} 
                    house_kwh = 0.0

            # 5. ANALYTICS (Finanzen & Restbedarf-Vorhersagen aus neuem ProfileManager)
            analytics_results = await update_forecasts_and_finances(
                self.hass, 
                self.profile_manager, 
                config, 
                deltas, 
                house_kwh, 
                self.data["savings"], 
                self.data.get("strat")
            )
            self.data.update(analytics_results)

            # --- NEU: Tagesprofil für das Dashboard laden ---
            now = dt_util.now()
            daily_profile = self.profile_manager.get_full_day_profile(now, config.get("default_usage", 0.85))
            self.data["daily_profile"] = daily_profile
            self.data["expected_daily_total"] = round(sum(daily_profile), 2)
            # ------------------------------------------------

            # 5b. STRATEGIE-GATES (deterministisch) berechnen
            gates = self._evaluate_gates(config, current, now)
            self.data["gates"] = gates

            # 6. STRATEGIE & STEUERUNG
            # Hauptschalter "Ladeautomatik aktiv" ist eine harte Leitplanke:
            # ist er AUS, unternimmt die Integration NICHTS von selbst.
            auto_on = bool(config.get("auto_charge_enabled", False))

            if not auto_on:
                # ===== MASTER-AUS: keine Eingriffe, Hardware in Normalzustand =====
                self._hold_latch_until = None
                self.data["ai_charge_profile"] = [0] * 24
                self.data.update({
                    "strat": "NORMAL",
                    "strat_msg": "Ladeautomatik AUS – keine Eingriffe",
                    "discharge_lock_active": False,
                    "fahrplan": "Automatik deaktiviert",
                })
                await self._release_all(config)
            else:
                # ===== AUTOMATIK AN =====
                # KI-Lade-Fahrplan (24h 0/1-Raster) aus Preisen, Bedarf und PV ableiten
                ai_profile = self.profile_manager.calculate_best_profile(self.data, config)
                # SmartCharge-Gate: Nur laden, wenn prognostiziert zu wenig PV kommt.
                if not gates["smartcharge_allowed"]:
                    ai_profile = [0] * 24
                self.data["ai_charge_profile"] = ai_profile

                strat, msg, lock_needed = calculate_strategy(config, self.hass.states, ai_profile)

                # Autonomes SmartHold nur, wenn keine hoehere Prioritaet (manuelles
                # Laden/Sperren, KI-Timer) aktiv ist – mit Anti-Flatter-Latch.
                if strat == "NORMAL":
                    strat, msg, lock_needed = self._apply_autonomous_hold(gates, now, strat, msg, lock_needed)
                else:
                    self._hold_latch_until = None

                self.data.update({
                    "strat": strat,
                    "strat_msg": msg,
                    "discharge_lock_active": lock_needed,
                    "fahrplan": f"Status: {strat} | Bedarf: {self.data.get('rest_demand_daily', 0)}kWh"
                })

                # 7. HARDWARE-STEUERUNG
                await self._handle_hardware_control(config, lock_needed, strat)

            # 8. REGELMÄSSIGES SPEICHERN (Alle 15 Minuten)
            self._update_cycles += 1
            if self._update_cycles >= 15:
                self._update_cycles = 0
                await self.hass.async_add_executor_job(self._save_savings_to_disk)

            self.last_readings = current
            return self.data

        except Exception as e:
            _LOGGER.error("Fehler im Coordinator: %s", e)
            raise UpdateFailed(f"Update fehlgeschlagen: {e}")

    # --- HELPER METHODEN ---
    async def _set_wr_limit(self, entity_id, target, reason):
        """Setzt das Wechselrichter-Limit zuverlässig.

        Re-assert nur bei echter Abweichung; bei nicht verfügbarer Entität oder
        Schreibfehler wird gewarnt (kein stilles Verschlucken) und einmal
        wiederholt – entscheidend für eine verlässliche Entladesperre.
        """
        ent_state = self.hass.states.get(entity_id)
        if not ent_state or ent_state.state in ('unknown', 'unavailable', 'none'):
            _LOGGER.warning(
                "WR-Limit-Entität %s nicht verfügbar – Sperre/Freigabe konnte NICHT gesetzt werden!",
                entity_id,
            )
            return

        try:
            current_val = float(ent_state.state)
        except (ValueError, TypeError):
            current_val = None

        # Schon korrekt eingestellt -> nichts tun (verhindert Service-Spam).
        if current_val is not None and abs(current_val - target) <= 0.1:
            return

        for attempt in range(2):
            try:
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": entity_id, "value": target},
                    blocking=True,
                )
                _LOGGER.info("WR-Limit -> %s (%s)", target, reason)
                return
            except Exception as ex:
                if attempt == 0:
                    await asyncio.sleep(1)
                else:
                    _LOGGER.warning("WR-Limit %s konnte nicht gesetzt werden: %s", entity_id, ex)

    async def _handle_hardware_control(self, config, lock_needed, strat):
        # 1. Haupt-Limit (Sperre/Freigabe)
        limit_entity = config.get("wr_limit_entity")
        if limit_entity:
            lock_value = float(config.get("wr_lock_value", 0.0))
            unlock_value = float(config.get("wr_unlock_value", 80.0))
            target_limit = lock_value if lock_needed else unlock_value
            await self._set_wr_limit(limit_entity, target_limit, "Sperre" if lock_needed else strat)

        # 2. Spezifische Lade-Steuerung (Falls aktiv)
        charge_control_entity = config.get("battery_charge_switch")
        if charge_control_entity:
            action = "turn_on" if strat == "LADEN" else "turn_off"
            await self.hass.services.async_call(
                "switch", action, {"entity_id": charge_control_entity}
            )

        # 3. Smart-Switches: Ueberschuss-Verbraucher gestaffelt schalten
        await self._handle_smart_switches(config)

    async def _release_all(self, config):
        """Master-AUS: Wechselrichter freigeben, Laden aus, Smart-Switches aus."""
        limit_entity = config.get("wr_limit_entity")
        if limit_entity:
            unlock_value = float(config.get("wr_unlock_value", 80.0))
            await self._set_wr_limit(limit_entity, unlock_value, "Automatik AUS – Freigabe")

        charge_control_entity = config.get("battery_charge_switch")
        if charge_control_entity:
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": charge_control_entity}
            )

        # Von uns zugeschaltete Überschuss-Verbraucher wieder abschalten.
        for s in (config.get("smart_switches") or []):
            if s in self._switch_timers:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": s}
                )
        self._switch_timers = {}

    def _apply_autonomous_hold(self, gates, now, strat, msg, lock_needed):
        """Autonome Entladesperre mit Anti-Flatter-Latch.

        Greift das Gate, wird die Sperre bis zur Morgen-Peak-Stunde "gelatcht",
        damit sie nicht minütlich an/aus springt, wenn Werte um die Schwelle
        wackeln. Solange Akku-Energie da ist, bleibt die Sperre bis dahin aktiv.
        """
        if gates.get("smarthold_allowed", False):
            self._hold_latch_until = self._compute_latch_end(now)
            return (
                "HOLD",
                (f"SmartHold: Nachtreserve fehlt ({gates.get('nacht_defizit')} kWh), "
                 "Morgenpreis hoch – Akku wird bis zum Morgen-Peak gespart."),
                True,
            )

        # Gate aktuell zu, aber noch innerhalb des Latch-Fensters und Akku vorhanden?
        if self._hold_latch_until is not None:
            battery_left = gates.get("usable_battery", 0) or 0
            if now < self._hold_latch_until and battery_left > 0.05:
                until = dt_util.as_local(self._hold_latch_until).strftime("%H:%M")
                return "HOLD", f"SmartHold gehalten (Anti-Flatter-Schutz bis {until} Uhr)", True
            self._hold_latch_until = None

        return strat, msg, lock_needed

    def _compute_latch_end(self, now):
        """Ende des SmartHold-Latch = Morgen-Peak-Stunde (max. 10h ab jetzt)."""
        summary = self.data.get("ai_price_summary", {}) or {}
        peak_hour = None
        mt = summary.get("max_time")
        if mt:
            try:
                peak_hour = int(str(mt).split(":")[0])
            except (ValueError, IndexError):
                peak_hour = None

        if peak_hour is None:
            end = now + timedelta(hours=3)
        else:
            end = now.replace(hour=peak_hour, minute=59, second=0, microsecond=0)
            if end <= now:
                end = end + timedelta(days=1)

        return min(end, now + timedelta(hours=10))

    # --- VORSCHAU (Dry-Run, ohne Nebenwirkungen) ---
    @staticmethod
    def _hours_to_windows(hours):
        """Macht aus [2,3,4,22,23] -> '02:00–05:00, 22:00–00:00'."""
        if not hours:
            return ""
        hours = sorted(hours)
        parts = []
        start = prev = hours[0]
        for h in hours[1:]:
            if h == prev + 1:
                prev = h
            else:
                parts.append(f"{start:02d}:00–{(prev + 1) % 24:02d}:00")
                start = prev = h
        parts.append(f"{start:02d}:00–{(prev + 1) % 24:02d}:00")
        return ", ".join(parts)

    def build_preview_text(self):
        """Erzeugt einen Klartext-Plan dessen, was die Automatik tun würde.

        Liest nur self.data – verändert nichts.
        """
        data = self.data or {}
        config = {**self.entry.data, **self.entry.options}
        gates = data.get("gates", {}) or {}
        summary = data.get("ai_price_summary", {}) or {}
        auto_on = bool(config.get("auto_charge_enabled", False))

        # Lade-Fahrplan als Vorschau berechnen (unabhängig vom Hauptschalter).
        preview_cfg = dict(config)
        preview_cfg["auto_charge_enabled"] = True
        profile = self.profile_manager.calculate_best_profile(data, preview_cfg)
        if not gates.get("smartcharge_allowed"):
            profile = [0] * 24
        charge_windows = self._hours_to_windows([h for h, v in enumerate(profile) if v == 1])

        head = "AN" if auto_on else "AUS (Vorschau – so würde die Automatik bei Aktivierung handeln)"
        lines = [
            f"Automatik: {head}",
            "",
            f"🔋 Akku nutzbar: {gates.get('usable_battery', '?')} kWh  |  ☀️ PV-Rest heute: {data.get('solar_remaining', '?')} kWh",
            f"🏠 Bedarf bis Autarkiestart: {data.get('night_demand', '?')} kWh",
            f"📈 Preis: Tief {summary.get('min_price', '?')}ct ({summary.get('min_time', '?')}) | "
            f"Hoch {summary.get('max_price', '?')}ct ({summary.get('max_time', '?')})",
            "",
        ]

        # SmartCharge
        if gates.get("smartcharge_allowed"):
            if charge_windows:
                lines.append(f"⚡ SmartCharge: AKTIV – geplantes Laden: {charge_windows}")
            else:
                lines.append("⚡ SmartCharge: offen, aber aktuell kein günstiges Fenster mit Restbedarf.")
        else:
            lines.append(
                f"⚡ SmartCharge: AUS – genug PV erwartet (Tages-PV-Bilanz {gates.get('pv_day_balance', '?')} kWh)."
            )

        # SmartHold
        if gates.get("smarthold_allowed"):
            lines.append(
                f"🔒 SmartHold: AKTIV – Entladesperre, Akku wird für den teuren Morgen gespart "
                f"(Defizit {gates.get('nacht_defizit', '?')} kWh)."
            )
        else:
            grund = "Nachtreserve reicht" if not gates.get("reserve_insufficient") else "Morgenpreis nicht hoch genug"
            lines.append(f"🔒 SmartHold: AUS – {grund}.")

        lines += [
            "",
            f"➡️ Aktueller Zustand: {data.get('strat', '?')} – {data.get('strat_msg', '')}",
            "",
            "ℹ️ Vorschau – es wurde nichts verändert.",
        ]
        return "\n".join(lines)

    async def async_post_preview(self):
        """Baut die Vorschau und zeigt sie als persistente Benachrichtigung."""
        try:
            text = self.build_preview_text()
        except Exception as ex:
            _LOGGER.warning("Vorschau konnte nicht erstellt werden: %s", ex)
            text = f"Vorschau konnte nicht erstellt werden: {ex}"
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {
                "title": "Intelligent ESS – Automatik-Vorschau",
                "message": text,
                "notification_id": "ess_preview",
            },
        )

    async def _handle_smart_switches(self, config):
        switches = config.get("smart_switches") or []
        if not switches:
            return

        threshold = float(config.get("smart_switch_threshold", -1000))
        net_watt = self.data.get("net_watt", 0.0)

        actions = ESSLogicEngine.smart_switch_control(
            net_watt, threshold, self._switch_timers, switches, self.hass.states
        )

        for entity_id, action in actions:
            await self.hass.services.async_call(
                "switch", action, {"entity_id": entity_id}
            )
            if action == "turn_on":
                self._switch_timers[entity_id] = dt_util.utcnow().timestamp()
            else:
                self._switch_timers.pop(entity_id, None)
            _LOGGER.info("Smart-Switch %s -> %s (Netto: %s W)", entity_id, action, net_watt)

    def _evaluate_gates(self, config, current, now):
        """Sammelt die Basiswerte und berechnet die SmartCharge/SmartHold-Gates."""
        # Autarkie-Stunde aus der Analytics-Berechnung ableiten (Fallback 8 Uhr).
        autarky_str = self.data.get("autarky_time_tomorrow", "08:00")
        autarky_hour = 8
        if isinstance(autarky_str, str) and ":" in autarky_str:
            try:
                autarky_hour = int(autarky_str.split(":")[0])
            except ValueError:
                autarky_hour = 8

        default_usage = float(config.get("default_usage", 0.85))
        night_demand = self.profile_manager.get_night_demand(now, autarky_hour, default_usage)
        self.data["night_demand"] = night_demand

        readings = current or self.last_readings or {}
        soc = readings.get("bat_soc", 0)

        return evaluate_strategy(
            soc=soc,
            capacity=float(config.get("battery_capacity", 15.0)),
            min_soc=float(config.get("min_soc_reserve", 10.0)),
            solar_remaining=self.data.get("solar_remaining", 0.0),
            pv_tomorrow_total=self.data.get("pv_tomorrow_total", 0.0),
            night_demand=night_demand,
            expected_daily_total=self.data.get("expected_daily_total", 0.0),
            ai_price_summary=self.data.get("ai_price_summary", {}) or {},
            current_price=self.data.get("current_price", 0.0),
            price_delta_threshold=float(config.get("price_delta_threshold", 5.0)),
        )

    def _load_savings(self):
        if os.path.exists(self.savings_path):
            try:
                with open(self.savings_path, 'r') as f:
                    saved = json.load(f)
                    self.data["savings"].update(saved)
            except Exception: 
                pass

    def _save_savings_to_disk(self):
        try:
            with open(self.savings_path, 'w') as f:
                json.dump(self.data["savings"], f)
        except Exception: 
            pass