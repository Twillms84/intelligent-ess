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
        IntelligentESSPreviewButton(coordinator, entry),
        IntelligentESSApplyRecommendationButton(coordinator, entry),
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

class IntelligentESSPreviewButton(IntelligentESSBaseButton):
    """Zeigt als Vorschau (ohne Nebenwirkungen), was die Automatik tun würde."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "Automatik-Vorschau", "mdi:eye-check-outline", "preview_btn", None)

    async def async_press(self) -> None:
        _LOGGER.info("Automatik-Vorschau angefordert.")
        await self.coordinator.async_request_refresh()
        await self.coordinator.async_post_preview()


class IntelligentESSApplyRecommendationButton(IntelligentESSBaseButton):
    """Überträgt die aktuelle (deterministische) Empfehlung in die manuellen Zeitfenster."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "Empfehlung übernehmen", "mdi:check-decagram", "apply_rec_btn", None)

    async def async_press(self) -> None:
        _LOGGER.info("Empfehlung übernehmen angefordert.")
        # Frische Daten, dann Empfehlung in manuelle Slots schreiben.
        await self.coordinator.async_request_refresh()
        try:
            result = self.coordinator.apply_recommendation()
        except Exception as ex:  # noqa: BLE001
            _LOGGER.error("Empfehlung konnte nicht übernommen werden: %s", ex)
            result = f"Fehler: {ex}"
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {
                "title": "Intelligent ESS – Empfehlung übernommen",
                "message": f"{result}\n\nHinweis: Läuft als manuelles Zeitfenster – unabhängig von der Automatik.",
                "notification_id": "ess_apply_rec",
            },
        )


class IntelligentESSKIButton(IntelligentESSBaseButton):
    """KI Strategie-Check: erklärt den (deterministischen) Tagesplan verständlich
    und prüft ihn auf Plausibilität. Die KI erfindet KEINE eigenen Zeiten mehr –
    die Entscheidungen kommen aus dem Einsatzplaner, die KI liefert die Erklärung.
    """
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "KI Strategie-Check", "mdi:robot-vacuum-variant", "ki_button", None)

    async def async_press(self) -> None:
        _LOGGER.info("--- KI-STRATEGIE-CHECK START ---")
        ki_text = "Analyse läuft..."

        try:
            # Frische Daten, damit der Plan aktuell ist.
            await self.coordinator.async_request_refresh()

            config = {**self.entry.data, **self.entry.options}
            data = self.coordinator.data if self.coordinator.data else {}
            readings = getattr(self.coordinator, "last_readings", {}) or {}
            gates = data.get("gates", {}) or {}
            rec = data.get("recommendation", {}) or {}
            summary = data.get("ai_price_summary", {}) or {}

            soc_now = readings.get("bat_soc", "?")
            usable = gates.get("usable_battery", "?")
            solar_remaining = data.get("solar_remaining", "?")
            night_demand = data.get("night_demand", "?")

            # Stundentabelle aus demselben Horizont wie der Planer.
            now = dt_util.now()
            try:
                horizon = self.coordinator._build_horizon(config, now)
            except Exception:  # noqa: BLE001
                horizon = []
            actions = rec.get("plan_actions", [])
            action_de = {"DISCHARGE": "Akku nutzen", "HOLD": "Akku sparen",
                         "CHARGE": "Laden", "IDLE": "-"}
            rows = []
            for i, h in enumerate(horizon[:24]):
                act = action_de.get(actions[i] if i < len(actions) else "IDLE", "-")
                rows.append(
                    f"{h['label']}: {round(h['price'] * 100, 1)}ct | "
                    f"Bedarf {round(h['net_load'], 2)} kWh | PV+ {round(h['pv_surplus'], 2)} kWh -> {act}"
                )
            table = "\n".join(rows) if rows else "keine Prognosedaten"

            prompt = (
                "Du bist 'Intelligent ESS', ein KI-Energiemanager für einen Heimspeicher. "
                "Ein deterministischer Einsatzplaner hat bereits einen optimierten Tagesplan berechnet: "
                "Er verteilt die begrenzte Akku-Energie auf die TEUERSTEN Stunden, die sie noch deckt, "
                "berücksichtigt die PV-Nachladung und sperrt die Entladung nur in günstigen Stunden vor "
                "späteren teuren Stunden.\n\n"
                "Deine Aufgabe ist NICHT, eigene Uhrzeiten zu erfinden, sondern:\n"
                "1. Erkläre dem Nutzer verständlich in 4-6 Sätzen mit konkreten Zahlen, was der Plan heute "
                "vorsieht und warum (wann Akku genutzt, wann gespart, wann geladen wird).\n"
                "2. Prüfe den Plan auf Plausibilität. Wenn du eine sinnvolle Verbesserung siehst, nenne sie "
                "kurz als Hinweis (sie wird NICHT automatisch übernommen). Wenn der Plan schlüssig ist, sage das.\n\n"
                "=== BATTERIE & PROGNOSE ===\n"
                f"Ladestand: {soc_now}% (nutzbar {usable} kWh)\n"
                f"PV-Rest heute: {solar_remaining} kWh | Nachtbedarf bis Autarkiestart: {night_demand} kWh\n"
                f"Preis: Tief {summary.get('min_price', '?')}ct ({summary.get('min_time', '?')}) | "
                f"Hoch {summary.get('max_price', '?')}ct ({summary.get('max_time', '?')}) | "
                f"Schnitt {summary.get('avg_price', '?')}ct\n\n"
                "=== TAGESPLAN (verbindlich, vom Planer) ===\n"
                f"Laden: {rec.get('charge_windows') or '-'}\n"
                f"Akku sparen (Sperre): {rec.get('hold_windows') or '-'}\n"
                f"Akku nutzen: {rec.get('discharge_windows') or '-'}\n\n"
                "=== STUNDENTABELLE ===\n"
                f"{table}\n\n"
                "Antworte nur mit dem Erklärungstext (kein JSON, keine Codeblöcke)."
            )

            max_retries = 3
            retry_delay = 5
            full_text = ""
            agent_id = config.get("conversation_agent") or "conversation.home_assistant"
            for attempt in range(max_retries):
                try:
                    result = await self.hass.services.async_call(
                        "conversation", "process",
                        {"text": prompt, "agent_id": agent_id},
                        blocking=True, return_response=True,
                    )
                    current_text = result["response"]["speech"]["plain"]["speech"]
                    if "high demand" in current_text or "Sorry, I had a problem" in current_text:
                        if attempt < max_retries - 1:
                            _LOGGER.warning("KI überlastet (Versuch %s/%s).", attempt + 1, max_retries)
                            await asyncio.sleep(retry_delay)
                            continue
                    full_text = current_text
                    break
                except Exception as inner_e:  # noqa: BLE001
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                    else:
                        raise inner_e

            ki_text = full_text.strip() if full_text else "Keine Antwort von der KI erhalten."

        except Exception as e:  # noqa: BLE001
            _LOGGER.error("Fehler im KI-Button: %s", e)
            ki_text = f"Fehler bei der Analyse: {e}"

        await self.hass.services.async_call(
            "persistent_notification", "create",
            {"title": "Intelligent ESS KI", "message": f"🤖 {ki_text}", "notification_id": "ess_ki"},
        )
