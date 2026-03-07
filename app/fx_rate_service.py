"""
Service FX centralise - Taux de change via Frankfurter (ECB) + cache Redis.

Usage:
    from .fx_rate_service import get_fx_rates_cached
    rates = await get_fx_rates_cached("CHF", {"EUR", "USD"}, "2026-01-01", "2026-03-07")
    # => {"2026-01-15": {"EUR": 0.94, "USD": 1.08}, ...}
"""

import json
import logging
from typing import Dict, Optional, Set

import httpx

from .redis_client import get_redis

logger = logging.getLogger(__name__)

FRANKFURTER_CURRENCIES = {
    "AUD", "BGN", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP",
    "HKD", "HUF", "IDR", "ILS", "INR", "ISK", "JPY", "KRW", "MXN", "MYR",
    "NOK", "NZD", "PHP", "PLN", "RON", "SEK", "SGD", "THB", "TRY", "USD", "ZAR",
}

CURRENCY_ALIASES = {
    "DH": "MAD", "€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY",
    "Fr.": "CHF", "fr.": "CHF", "SFr.": "CHF", "Fr": "CHF",
}

FX_CACHE_TTL = 86400  # 24h


def normalize_currency(code: str) -> str:
    return CURRENCY_ALIASES.get(code, code).upper().strip()


async def get_fx_rates_cached(
    base_currency: str,
    target_currencies: Set[str],
    date_from: Optional[str],
    date_to: Optional[str],
) -> Dict[str, Dict[str, float]]:
    """
    Retourne les taux de change pour un range de dates.
    Cache Redis: fx_rates:{base}:{YYYY-MM} TTL 24h
    """
    base = normalize_currency(base_currency)
    targets = {normalize_currency(c) for c in target_currencies}
    targets.discard(base)

    if not targets or not date_from or not date_to:
        return {}

    frankfurter_targets = targets & FRANKFURTER_CURRENCIES
    if not frankfurter_targets:
        return {}

    # Check Redis cache (key per month of date_from)
    cache_key = f"fx_rates:{base}:{date_from[:7]}"
    try:
        r = get_redis()
        cached = r.get(cache_key)
        if cached:
            rates = json.loads(cached)
            if _covers_targets(rates, frankfurter_targets):
                return rates
    except Exception as e:
        logger.warning(f"[FX] Redis cache read error: {e}")

    # Frankfurter API
    try:
        url = f"https://api.frankfurter.dev/{date_from}..{date_to}"
        params = {"from": base, "to": ",".join(sorted(frankfurter_targets))}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            rates = data.get("rates", {})

        # Cache in Redis
        try:
            r = get_redis()
            r.set(cache_key, json.dumps(rates), ex=FX_CACHE_TTL)
        except Exception as e:
            logger.warning(f"[FX] Redis cache write error: {e}")

        return rates

    except Exception as e:
        logger.warning(f"[FX] Frankfurter API error: {e}")
        return {}


def get_fx_rate_for_date(
    rates: Dict[str, Dict[str, float]],
    target_currency: str,
    date_str: str,
) -> Optional[float]:
    """
    Lookup un taux pour une date precise. Si la date exacte n'existe pas
    (weekend/ferie), retourne le taux du jour ouvrable le plus proche precedent.
    """
    target = normalize_currency(target_currency)

    # Exact date
    day_rates = rates.get(date_str)
    if day_rates and target in day_rates:
        return day_rates[target]

    # Nearest previous business day
    sorted_dates = sorted(d for d in rates.keys() if d <= date_str)
    if sorted_dates:
        day_rates = rates.get(sorted_dates[-1], {})
        if target in day_rates:
            return day_rates[target]

    # Nearest next business day (fallback)
    sorted_dates = sorted(d for d in rates.keys() if d > date_str)
    if sorted_dates:
        day_rates = rates.get(sorted_dates[0], {})
        if target in day_rates:
            return day_rates[target]

    return None


def _covers_targets(rates: dict, targets: set) -> bool:
    """Verifie que le cache couvre toutes les devises demandees."""
    if not rates:
        return False
    first_day = next(iter(rates.values()), {})
    return targets.issubset(set(first_day.keys()))
