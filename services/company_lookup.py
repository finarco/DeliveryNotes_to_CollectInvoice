"""Company lookup service — queries Slovak RPO and Czech ARES business registers."""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 5  # seconds


def _normalize_rpo_entity(entity: dict) -> dict:
    """Normalize an RPO entity response to our unified format."""
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
    result["name"] = entity.get("fullName") or entity.get("name") or ""

    identifiers = entity.get("identifiers") or []
    for ident in identifiers:
        if ident.get("type") == "ico" or ident.get("type") == "ICO":
            result["ico"] = ident.get("value", "")
    if not result["ico"]:
        result["ico"] = str(entity.get("id", ""))

    addresses = entity.get("addresses") or []
    if addresses:
        addr = addresses[0]
        result["street"] = addr.get("street") or addr.get("streetName") or ""
        result["street_number"] = addr.get("buildingNumber") or addr.get("registrationNumber") or ""
        result["city"] = addr.get("municipality") or addr.get("city") or ""
        result["postal_code"] = addr.get("postalCode") or ""

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


def lookup_by_ico(ico: str) -> Optional[dict]:
    """Look up a company by ICO, trying RPO (SK) then ARES (CZ)."""
    ico = ico.strip()
    if not ico:
        return None

    # Try RPO (Slovak Register)
    try:
        resp = requests.get(
            "https://data.statistics.sk/api/rpo/v1/search",
            params={"identifier": ico},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("items") or data.get("results") or []
            if items:
                entity = items[0] if isinstance(items, list) else items
                return _normalize_rpo_entity(entity)
    except Exception as e:
        logger.warning("RPO lookup failed for ICO %s: %s", ico, e)

    # Try ARES (Czech Register)
    try:
        resp = requests.get(
            f"https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico}",
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            return _normalize_ares_entity(data)
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
            "https://data.statistics.sk/api/rpo/v1/search",
            params={"fullName": name},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("items") or data.get("results") or []
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
