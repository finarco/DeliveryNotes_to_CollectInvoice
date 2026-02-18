"""Company lookup service — queries Slovak RPO and Czech ARES business registers."""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 8  # seconds


def _get_current_value(entries: list) -> str:
    """Return the most recent (no validTo) value from an RPO history list."""
    if not entries:
        return ""
    # Prefer entry with no validTo (= currently active)
    for entry in entries:
        if not entry.get("validTo"):
            return entry.get("value", "")
    # Fallback to last entry
    return entries[-1].get("value", "")


def _normalize_rpo_entity(entity: dict) -> dict:
    """Normalize an RPO entity response to our unified format.

    The RPO API at api.statistics.sk returns entities with history arrays
    (fullNames, addresses, identifiers) where each entry has validFrom/validTo.
    """
    result = {
        "name": "",
        "ico": "",
        "dic": "",
        "ic_dph": "",
        "street": "",
        "street_number": "",
        "city": "",
        "postal_code": "",
    }

    result["name"] = _get_current_value(entity.get("fullNames") or [])

    # ICO from identifiers array
    for ident in entity.get("identifiers") or []:
        if not ident.get("validTo"):
            result["ico"] = ident.get("value", "")
            break
    if not result["ico"]:
        identifiers = entity.get("identifiers") or []
        if identifiers:
            result["ico"] = identifiers[-1].get("value", "")

    # Address — pick the current one (no validTo)
    addresses = entity.get("addresses") or []
    for addr in addresses:
        if addr.get("validTo"):
            continue
        result["street"] = addr.get("street", "")
        result["street_number"] = addr.get("buildingNumber", "")
        municipality = addr.get("municipality")
        if isinstance(municipality, dict):
            result["city"] = municipality.get("value", "")
        elif isinstance(municipality, str):
            result["city"] = municipality
        postal_codes = addr.get("postalCodes") or []
        if postal_codes:
            result["postal_code"] = postal_codes[0]
        break

    return result


def _normalize_registeruz_entity(data: dict) -> dict:
    """Normalize a registeruz.sk entity to our unified format (fallback)."""
    result = {
        "name": data.get("nazovUJ", ""),
        "ico": data.get("ico", ""),
        "dic": data.get("dic", ""),
        "ic_dph": "",
        "street": "",
        "street_number": "",
        "city": data.get("mesto", ""),
        "postal_code": data.get("psc", ""),
    }
    ulica = data.get("ulica", "")
    if ulica:
        # ulica often contains "Street Number" combined, e.g. "Mesačná 130/15"
        parts = ulica.rsplit(" ", 1)
        if len(parts) == 2 and any(c.isdigit() for c in parts[1]):
            result["street"] = parts[0]
            result["street_number"] = parts[1]
        else:
            result["street"] = ulica
    # Format postal code with space if needed
    psc = result["postal_code"]
    if psc and len(psc) == 5 and " " not in psc:
        result["postal_code"] = psc[:3] + " " + psc[3:]
    return result


def _normalize_ares_entity(data: dict) -> dict:
    """Normalize an ARES entity response to our unified format."""
    result = {
        "name": data.get("obchodniJmeno", ""),
        "ico": str(data.get("ico", "")),
        "dic": data.get("dic", ""),
        "ic_dph": "",
        "street": "",
        "street_number": "",
        "city": "",
        "postal_code": "",
    }
    sidlo = data.get("sidlo") or {}
    result["street"] = sidlo.get("nazevUlice", "")
    cd = sidlo.get("cisloDomovni")
    co = sidlo.get("cisloOrientacni")
    if cd:
        result["street_number"] = str(cd)
        if co:
            result["street_number"] += f"/{co}"
    result["city"] = sidlo.get("nazevObce", "")
    result["postal_code"] = str(sidlo.get("psc", ""))
    if result["postal_code"] and len(result["postal_code"]) == 5:
        result["postal_code"] = result["postal_code"][:3] + " " + result["postal_code"][3:]
    return result


def _lookup_rpo(ico: str) -> Optional[dict]:
    """Look up a company via the Slovak RPO register (api.statistics.sk)."""
    resp = requests.get(
        "https://api.statistics.sk/rpo/v1/search",
        params={"identifier": ico},
        timeout=_TIMEOUT,
    )
    if resp.status_code == 200:
        data = resp.json()
        items = data.get("results") or data.get("items") or []
        if isinstance(data, list):
            items = data
        if items:
            return _normalize_rpo_entity(items[0])
    return None


def _lookup_registeruz(ico: str) -> Optional[dict]:
    """Look up a company via registeruz.sk (Slovak financial register, fallback)."""
    # Step 1: find internal ID by ICO
    resp = requests.get(
        "https://www.registeruz.sk/cruz-public/api/uctovne-jednotky",
        params={"zmenene-od": "2000-01-01", "pokracovat-za-id": "1",
                "max-zaznamov": "1", "ico": ico},
        timeout=_TIMEOUT,
        headers={"User-Agent": "DeliveryNotes/1.0"},
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    ids = data.get("id") or []
    if not ids:
        return None
    # Step 2: fetch details
    resp2 = requests.get(
        "https://www.registeruz.sk/cruz-public/api/uctovna-jednotka",
        params={"id": ids[0]},
        timeout=_TIMEOUT,
        headers={"User-Agent": "DeliveryNotes/1.0"},
    )
    if resp2.status_code == 200:
        return _normalize_registeruz_entity(resp2.json())
    return None


def _lookup_ares(ico: str) -> Optional[dict]:
    """Look up a company via the Czech ARES register."""
    resp = requests.get(
        f"https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico}",
        timeout=_TIMEOUT,
    )
    if resp.status_code == 200:
        return _normalize_ares_entity(resp.json())
    return None


def lookup_by_ico(ico: str) -> Optional[dict]:
    """Look up a company by ICO, trying RPO (SK) then registeruz (SK) then ARES (CZ)."""
    ico = ico.strip()
    if not ico:
        return None

    # Try RPO (Slovak Register — primary)
    try:
        result = _lookup_rpo(ico)
        if result:
            # RPO doesn't return DIC; supplement from registeruz if missing
            if not result.get("dic"):
                try:
                    ruz = _lookup_registeruz(ico)
                    if ruz and ruz.get("dic"):
                        result["dic"] = ruz["dic"]
                except Exception:
                    pass
            return result
    except Exception as e:
        logger.warning("RPO lookup failed for ICO %s: %s", ico, e)

    # Try registeruz.sk (Slovak financial register — fallback)
    try:
        result = _lookup_registeruz(ico)
        if result:
            return result
    except Exception as e:
        logger.warning("RegisterUZ lookup failed for ICO %s: %s", ico, e)

    # Try ARES (Czech Register)
    try:
        result = _lookup_ares(ico)
        if result:
            return result
    except Exception as e:
        logger.warning("ARES lookup failed for ICO %s: %s", ico, e)

    return None


def search_by_name(name: str) -> list[dict]:
    """Search for companies by name, trying RPO (SK) then ARES (CZ)."""
    name = name.strip()
    if not name or len(name) < 3:
        return []

    results = []

    # Try RPO
    try:
        resp = requests.get(
            "https://api.statistics.sk/rpo/v1/search",
            params={"fullName": name},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("results") or data.get("items") or []
            if isinstance(data, list):
                items = data
            if isinstance(items, list):
                for entity in items[:10]:
                    results.append(_normalize_rpo_entity(entity))
    except Exception as e:
        logger.warning("RPO name search failed for '%s': %s", name, e)

    # Try ARES
    try:
        resp = requests.get(
            "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/vyhledat",
            params={"obchodniJmeno": name, "start": 0, "pocet": 10},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("ekonomickeSubjekty") or []
            for entity in items[:10]:
                results.append(_normalize_ares_entity(entity))
    except Exception as e:
        logger.warning("ARES name search failed for '%s': %s", name, e)

    return results
