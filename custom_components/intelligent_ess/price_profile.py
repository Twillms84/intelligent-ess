"""Gelernter typischer Strompreis je Tagesstunde (für noch unbekannte Preise)."""
import logging
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
ALPHA = 0.15  # Gewicht des jeweils neuesten Tages im gleitenden Mittel


class PriceProfile:
    def __init__(self, hass, entry_id):
        self._store = Store(hass, 1, f"intelligent_ess_price_profile_{entry_id}")
        self._hours = {}   # "0".."23" -> €/kWh (gleitendes Mittel)
        self._seen = []    # zuletzt verrechnete Stunden-Schlüssel (Doppelzählung vermeiden)
        self._loaded = False

    async def _load(self):
        if self._loaded:
            return
        data = await self._store.async_load() or {}
        self._hours = data.get("hours", {})
        self._seen = data.get("seen", [])
        self._loaded = True

    async def async_update(self, raw_prices):
        """raw_prices: Liste {start_time,total} – auch vergangene Stunden."""
        await self._load()
        sums = {}
        for p in raw_prices or []:
            dtp = dt_util.parse_datetime(p.get("start_time")) if p.get("start_time") else None
            if not dtp:
                continue
            key = dt_util.as_local(dtp).replace(minute=0, second=0, microsecond=0)
            try:
                acc = sums.setdefault(key, [0.0, 0])
                acc[0] += float(p.get("total")); acc[1] += 1
            except (TypeError, ValueError):
                continue
        changed = False
        for key in sorted(sums):
            tag = key.strftime("%Y-%m-%dT%H")
            if tag in self._seen:
                continue
            val = sums[key][0] / sums[key][1]
            h = str(key.hour)
            old = self._hours.get(h)
            self._hours[h] = val if old is None else (1 - ALPHA) * old + ALPHA * val
            self._seen.append(tag)
            changed = True
        if changed:
            self._seen = self._seen[-96:]
            self._store.async_delay_save(lambda: {"hours": self._hours, "seen": self._seen}, 10)

    def typical(self, hour):
        v = self._hours.get(str(hour))
        return float(v) if v is not None else None
