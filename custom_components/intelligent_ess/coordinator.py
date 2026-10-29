import logging
import json
import os
import asyncio
from datetime import timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .scheduler import calculate_strategy, evaluate_manual_windows, evaluate_automatic
from .planner import plan_dispatch
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

            # 6. STRATEGIE & STEUERUNG (Drei-Ebenen-Modell)
            #   Ebene 0: Empfehlung – wird IMMER berechnet (nur Anzeige, kein Eingriff)
            #   Ebene 1: Manuelle Zeitfenster – gelten IMMER (unabhängig vom Hauptschalter)
            #   Ebene 2: Automatik – nur wenn Hauptschalter "Ladeautomatik aktiv" AN ist
            auto_on = bool(config.get("auto_charge_enabled", False))

            # --- Ebene 0: Empfehlung immer berechnen ---
            recommendation = self._compute_recommendation(config, gates, now)
            self.data["recommendation"] = recommendation
            # Das (bereits gegatete) Lade-Raster dient Anzeige UND Automatik.
            self.data["ai_charge_profile"] = recommendation["charge_profile"]

            # --- Ebene 1: manuelle Zeitfenster (immer gültig) ---
            manual = evaluate_manual_windows(config)

            if manual is not None:
                # Vom Nutzer gesetztes festes Fenster -> hat Vorrang, läuft auch ohne Automatik.
                strat, msg, lock_needed = manual
                self._hold_latch_until = None
            elif auto_on:
                # --- Ebene 2: Automatik aktiv – Entscheidung aus dem Tages-Einsatzplaner ---
                ca = recommendation.get("current_action", "IDLE")
                reason = recommendation.get("current_reason", "Normalbetrieb (PV/Batterie)")
                if ca == "CHARGE":
                    strat, msg, lock_needed = "LADEN", reason, False
                elif ca == "HOLD":
                    strat, msg, lock_needed = "HOLD", reason, True
                else:  # DISCHARGE oder IDLE -> Akku normal nutzen, keine Sperre
                    strat, msg, lock_needed = "NORMAL", reason, False
            else:
                # Automatik AUS und kein manuelles Fenster -> KEINE Eigenaktion.
                strat, msg, lock_needed = "NORMAL", "Automatik aus – nur Empfehlungen", False
                self._hold_latch_until = None

            self.data.update({
                "strat": strat,
                "strat_msg": msg,
                "discharge_lock_active": lock_needed,
                "auto_on": auto_on,
                "manual_active": manual is not None,
            })

            # 7. HARDWARE-STEUERUNG
            # Nur eingreifen, wenn ein manuelles Fenster aktiv ist ODER die Automatik an ist.
            # Sonst: Hardware in den Normalzustand freigeben (echtes "AUS heißt AUS").
            if manual is not None or auto_on:
                await self._handle_hardware_control(config, lock_needed, strat)
            else:
                await self._release_all(config)

            # 7b. FAHRPLAN zusammenstellen (detailliert, fuers Dashboard)
            self._compose_fahrplan(config, gates, auto_on, now)

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

    # --- FAHRPLAN ---
    def _hold_window_from_options(self, config):
        """Liest ein (manuell/KI-gesetztes) aktives Sperrfenster aus den Optionen."""
        if not config.get("man_hold_s1_enabled", False):
            return None
        s = config.get("man_hold_s1_start")
        e = config.get("man_hold_s1_end")
        if not s or not e:
            return None
        return f"{str(s)[:5]}–{str(e)[:5]}"

    def _build_horizon(self, config, now, hours=24):
        """Baut den Prognosehorizont (Preis/PV/Verbrauch je Stunde) für den Planer."""
        prices = self.data.get("prices", []) or []
        if not prices:
            return []
        solar = self.data.get("solar_hourly", {}) or {}
        default_usage = float(config.get("default_usage", 0.85))

        # Preise nach lokaler Stunde indizieren (€/kWh).
        price_map = {}
        for p in prices:
            dtp = dt_util.parse_datetime(p.get("start_time")) if p.get("start_time") else None
            if not dtp:
                continue
            key = dt_util.as_local(dtp).replace(minute=0, second=0, microsecond=0)
            try:
                price_map[key] = float(p.get("total"))
            except (TypeError, ValueError):
                continue

        start = now.replace(minute=0, second=0, microsecond=0)
        horizon = []
        for k in range(hours):
            hdt = start + timedelta(hours=k)
            price = price_map.get(hdt)
            if price is None:
                continue  # ohne Preis keine Planung für diese Stunde
            load = float(self.profile_manager.get_profile_value(hdt, default_usage))
            pv = float(solar.get(hdt.strftime("%Y-%m-%d %H:00:00"), 0) or 0)
            horizon.append({
                "label": hdt.strftime("%H:%M"),
                "price": price,
                "net_load": max(0.0, load - pv),
                "pv_surplus": max(0.0, pv - load),
            })
        return horizon

    @staticmethod
    def _first_window(horizon, idxs):
        """Erstes zusammenhängendes Fenster als (start, ende) im Format HH:MM:00."""
        if not idxs:
            return (None, None)
        idxs = sorted(idxs)
        start = prev = idxs[0]
        for i in idxs[1:]:
            if i == prev + 1:
                prev = i
            else:
                break
        s = horizon[start]["label"] + ":00"
        if prev + 1 < len(horizon):
            e = horizon[prev + 1]["label"] + ":00"
        else:
            e = f"{(int(horizon[prev]['label'][:2]) + 1) % 24:02d}:00:00"
        return (s, e)

    def _compute_recommendation(self, config, gates, now):
        """Berechnet die Empfehlung über den Tages-Einsatzplaner – ohne Eingriff.

        Dient der Anzeige im Fahrplan und dem Button 'Empfehlung übernehmen'.
        """
        horizon = self._build_horizon(config, now)
        usable_now = float(gates.get("usable_battery", 0) or 0)
        cap = float(config.get("battery_capacity", 15.0))
        min_soc = float(config.get("min_soc_reserve", 10.0))
        usable_cap = max(0.1, (100.0 - min_soc) / 100.0 * cap)
        adv = float(config.get("charge_delta_threshold", 5.0)) / 100.0  # ct -> €

        plan = plan_dispatch(
            horizon, usable_now, usable_cap,
            charge_advantage_eur=adv, charge_per_hour=usable_cap,
        )

        # 24h-Raster der Lade-Stunden (Rückwärtskompatibilität / Anzeige).
        charge_profile = [0] * 24
        for i in plan["charge"]:
            try:
                charge_profile[int(horizon[i]["label"][:2])] = 1
            except (ValueError, IndexError):
                pass

        charge_start, charge_end = self._first_window(horizon, plan["charge"])
        hold_start, hold_end = self._first_window(horizon, plan["hold"])

        return {
            "current_action": plan["current_action"],
            "current_reason": plan["current_reason"],
            "charge_profile": charge_profile,
            "charge_windows": plan["windows"]["charge"],
            "hold_windows": plan["windows"]["hold"],
            "discharge_windows": plan["windows"]["discharge"],
            "charge_start": charge_start,
            "charge_end": charge_end,
            "hold_recommended": bool(plan["hold"]),
            "hold_window": plan["windows"]["hold"] or None,
            "hold_start": hold_start,
            "hold_end": hold_end,
            "plan_actions": plan["actions"],
        }

    def apply_recommendation(self):
        """Überträgt die aktuelle Empfehlung in die manuellen Zeitfenster.

        Einmaliger Vorgang (kein Automatik-Dauerbetrieb). Rückgabe: Klartext,
        was übernommen wurde.
        """
        rec = self.data.get("recommendation", {}) or {}
        new_opts = dict(self.entry.options)
        applied = []

        if rec.get("charge_start") and rec.get("charge_end"):
            new_opts["man_charge_s1_start"] = rec["charge_start"]
            new_opts["man_charge_s1_end"] = rec["charge_end"]
            new_opts["man_charge_s1_enabled"] = True
            applied.append(f"Laden {rec['charge_start'][:5]}–{rec['charge_end'][:5]}")
        else:
            new_opts["man_charge_s1_enabled"] = False

        if rec.get("hold_recommended") and rec.get("hold_start") and rec.get("hold_end"):
            new_opts["man_hold_s1_start"] = rec["hold_start"]
            new_opts["man_hold_s1_end"] = rec["hold_end"]
            new_opts["man_hold_s1_enabled"] = True
            applied.append(f"Sperre {rec['hold_start'][:5]}–{rec['hold_end'][:5]}")
        else:
            new_opts["man_hold_s1_enabled"] = False

        self.hass.config_entries.async_update_entry(self.entry, options=new_opts)
        return "Übernommen: " + (", ".join(applied) if applied else "nichts (keine Empfehlung aktiv)")

    def _compose_fahrplan(self, config, gates, auto_on, now):
        """Baut einen detaillierten, lesbaren Fahrplan + strukturierte Attribute.

        Setzt self.data['fahrplan'] (Kurzstatus als State), self.data['plan']
        (strukturiert fuer Automationen) und self.data['fahrplan_text']
        (mehrzeilig fuer die Dashboard-Anzeige).
        """
        strat = self.data.get("strat", "NORMAL")
        reason = self.data.get("strat_msg", "")
        summary = self.data.get("ai_price_summary", {}) or {}
        manual_active = self.data.get("manual_active", False)
        rec = self.data.get("recommendation", {}) or {}

        # Tages-Plan aus dem Einsatzplaner (gilt als Empfehlung, auch bei AUS).
        w_use = rec.get("discharge_windows") or ""
        w_hold = rec.get("hold_windows") or ""
        w_charge = rec.get("charge_windows") or ""

        emp_parts = []
        if w_charge:
            emp_parts.append(f"Laden {w_charge}")
        if w_hold:
            emp_parts.append(f"Akku sparen {w_hold}")
        if w_use:
            emp_parts.append(f"Akku nutzen {w_use}")
        empfehlung = " · ".join(emp_parts) if emp_parts else "kein Eingriff nötig"

        # Aktuelle/nächste Aktion in Worten.
        if strat == "LADEN":
            src = "manuelles Fenster" if manual_active else "Automatik"
            next_action = f"Lädt aus dem Netz ({src})"
        elif strat == "HOLD":
            src = "manuelles Fenster" if manual_active else "Automatik"
            next_action = f"Entladesperre aktiv – Akku wird gespart ({src})"
        elif not auto_on:
            next_action = f"Automatik AUS – Empfehlung: {empfehlung}"
        else:
            next_action = rec.get("current_reason", "Normalbetrieb (PV/Batterie)")

        figures = {
            "akku_nutzbar_kwh": gates.get("usable_battery"),
            "pv_rest_heute_kwh": self.data.get("solar_remaining"),
            "pv_morgen_kwh": self.data.get("pv_tomorrow_total"),
            "nacht_bedarf_kwh": self.data.get("night_demand"),
            "preis_tief_ct": summary.get("min_price"),
            "preis_tief_zeit": summary.get("min_time"),
            "preis_hoch_ct": summary.get("max_price"),
            "preis_hoch_zeit": summary.get("max_time"),
        }

        plan = {
            "modus": "AN" if auto_on else "AUS",
            "aktion": strat,
            "grund": reason,
            "naechste_aktion": next_action,
            "empfehlung": empfehlung,
            "fenster_akku_nutzen": w_use or "—",
            "fenster_akku_sparen": w_hold or "—",
            "fenster_laden": w_charge or "—",
            "kennzahlen": figures,
        }

        # Kurzstatus als Sensor-State (begrenzte Laenge).
        short = f"{'AUS' if not auto_on else strat}: {next_action}"
        self.data["fahrplan"] = short[:255]
        self.data["plan"] = plan

        # Mehrzeiliger Klartext (Tagesplan).
        lines = [
            f"Modus: {'AN' if auto_on else 'AUS'}  |  Aktion: {strat}",
            f"➡️ {next_action}",
        ]
        lines.append(f"⚡ Laden: {w_charge or '—'}")
        lines.append(f"🔒 Akku sparen: {w_hold or '—'}")
        lines.append(f"🔋 Akku nutzen: {w_use or '—'}")
        lines.append(
            f"🔋 Akku nutzbar {figures['akku_nutzbar_kwh']} kWh  |  "
            f"☀️ PV-Rest {figures['pv_rest_heute_kwh']} kWh  |  "
            f"Nachtbedarf {figures['nacht_bedarf_kwh']} kWh"
        )
        lines.append(
            f"📈 Tief {figures['preis_tief_ct']}ct ({figures['preis_tief_zeit']})  |  "
            f"Hoch {figures['preis_hoch_ct']}ct ({figures['preis_hoch_zeit']})"
        )
        self.data["fahrplan_text"] = "\n".join(lines)

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

        # 3. Smart-Switches: Ueberschuss-Verbraucher nur bei aktiver Automatik.
        #    (Gehört zur Automatik-Ebene; bei reinem Manuell-Fenster NICHT schalten.)
        if config.get("auto_charge_enabled", False):
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
        rec = data.get("recommendation", {}) or {}
        auto_on = bool(config.get("auto_charge_enabled", False))

        head = "AN" if auto_on else "AUS (Vorschau – so würde die Automatik bei Aktivierung handeln)"
        lines = [
            f"Automatik: {head}",
            "",
            f"🔋 Akku nutzbar: {gates.get('usable_battery', '?')} kWh  |  ☀️ PV-Rest heute: {data.get('solar_remaining', '?')} kWh",
            f"🏠 Nachtbedarf bis Autarkiestart: {data.get('night_demand', '?')} kWh",
            f"📈 Preis: Tief {summary.get('min_price', '?')}ct ({summary.get('min_time', '?')}) | "
            f"Hoch {summary.get('max_price', '?')}ct ({summary.get('max_time', '?')})",
            "",
            "Tagesplan (Einsatzplaner):",
            f"⚡ Laden: {rec.get('charge_windows') or '—'}",
            f"🔒 Akku sparen: {rec.get('hold_windows') or '—'}",
            f"🔋 Akku nutzen: {rec.get('discharge_windows') or '—'}",
            "",
            f"➡️ Jetzt: {rec.get('current_reason', data.get('strat_msg', ''))}",
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