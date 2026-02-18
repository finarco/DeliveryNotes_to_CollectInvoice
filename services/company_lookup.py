"""Company lookup service — queries Slovak RPO, Czech ARES, EU VIES and Slovak
Financial Administration business registers."""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 8  # seconds

# ---------------------------------------------------------------------------
# VAT (DPH) verification helpers
# ---------------------------------------------------------------------------

def _get_fs_api_key() -> Optional[str]:
    """Return the Financial Administration OpenData API key, if configured.

    Checks (in order):
    1. Environment variable ``FS_OPENDATA_API_KEY``
    2. Tenant-specific AppSetting ``fs_opendata_api_key``
    3. Global AppSetting ``fs_opendata_api_key`` (tenant_id=NULL)
    """
    key = os.environ.get("FS_OPENDATA_API_KEY")
    if key:
        return key
    try:
        from models import AppSetting
        from services.tenant import tenant_query
        # Tenant-specific key first
        row = tenant_query(AppSetting).filter_by(key="fs_opendata_api_key").first()
        if row and row.value:
            return row.value
        # Fall back to global key
        row = AppSetting.query.filter_by(tenant_id=None, key="fs_opendata_api_key").first()
        if row and row.value:
            return row.value
    except Exception:
        pass
    return None


def check_vat_vies(country_code: str, vat_number: str) -> Optional[dict]:
    """Check VAT registration via EU VIES REST API (free, no auth).

    Returns a dict with ``valid``, ``name``, ``address`` or None on error.
    For Slovak companies pass ``country_code="SK"`` and ``vat_number=DIC``
    (without the SK prefix).
    """
    try:
        resp = requests.post(
            "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number",
            json={"countryCode": country_code, "vatNumber": vat_number},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning("VIES check failed for %s%s: %s", country_code, vat_number, e)
    return None


def check_vat_fs(ic_dph: str) -> Optional[dict]:
    """Check VAT registration via Slovak Financial Administration OpenData API.

    Requires an API key (see ``_get_fs_api_key``).  Searches the ``ds_dphs``
    list by IČ DPH and returns registration details including type (§4/§7/§7a).

    Returns a dict with keys: ``ic_dph``, ``ico``, ``nazov``, ``druh_reg``,
    ``datum_reg``, ``datum_zmeny_druhu_reg``, or None.
    """
    api_key = _get_fs_api_key()
    if not api_key:
        return None

    # ic_dph must include SK prefix for the FS search
    search_val = ic_dph if ic_dph.startswith("SK") else f"SK{ic_dph}"

    try:
        resp = requests.get(
            "https://iz.opendata.financnasprava.sk/api/data/ds_dphs/search",
            params={"column": "ic_dph", "search": search_val, "page": 1},
            headers={"key": api_key},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data") or []
            if items:
                row = items[0]
                return {
                    "ic_dph": row.get("ic_dph", ""),
                    "ico": row.get("ico", ""),
                    "nazov": row.get("nazov_ds", ""),
                    "druh_reg": row.get("druh_reg_dph", ""),
                    "datum_reg": row.get("datum_reg", ""),
                    "datum_zmeny_druhu_reg": row.get("datum_zmeny_druhu_reg", ""),
                }
        elif resp.status_code == 401:
            logger.warning("FS OpenData API key is invalid or expired")
    except Exception as e:
        logger.warning("FS OpenData lookup failed for %s: %s", search_val, e)
    return None


def enrich_vat_info(result: dict) -> dict:
    """Add VAT (DPH) information to a company lookup result.

    Enriches the result dict with:
    - ``ic_dph``: the full IČ DPH (e.g. SK2120289182) if registered
    - ``is_vat_payer``: True/False
    - ``vat_reg_type``: registration paragraph (e.g. "§4") if available via FS API
    - ``vat_reg_date``: registration date if available via FS API
    """
    dic = result.get("dic", "")
    if not dic:
        result.setdefault("is_vat_payer", False)
        return result

    # Determine country code — Slovak by default unless we know otherwise
    ico = result.get("ico", "")
    # Czech ICOs are 8 digits, Slovak are 8 digits too, but DIC format differs:
    # SK DIC is 10 digits, CZ DIC = CZ + ICO
    if dic.startswith("CZ"):
        country_code = "CZ"
        vat_number = dic[2:]
    else:
        country_code = "SK"
        vat_number = dic

    # 1. Check VIES (free, always available)
    vies = check_vat_vies(country_code, vat_number)
    if vies and vies.get("valid"):
        result["ic_dph"] = f"{country_code}{vat_number}"
        result["is_vat_payer"] = True
    else:
        result["is_vat_payer"] = False

    # 2. If Slovak and VAT payer, try FS OpenData for registration type
    if result["is_vat_payer"] and country_code == "SK":
        fs = check_vat_fs(vat_number)
        if fs:
            result["vat_reg_type"] = fs.get("druh_reg", "")
            result["vat_reg_date"] = fs.get("datum_reg", "")

    return result


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
    """Look up a company by ICO, trying RPO (SK) then registeruz (SK) then ARES (CZ).

    The result is automatically enriched with VAT (DPH) information via VIES
    and optionally via the Slovak Financial Administration OpenData API.
    """
    ico = ico.strip()
    if not ico:
        return None

    result = None

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
    except Exception as e:
        logger.warning("RPO lookup failed for ICO %s: %s", ico, e)

    # Try registeruz.sk (Slovak financial register — fallback)
    if not result:
        try:
            result = _lookup_registeruz(ico)
        except Exception as e:
            logger.warning("RegisterUZ lookup failed for ICO %s: %s", ico, e)

    # Try ARES (Czech Register)
    if not result:
        try:
            result = _lookup_ares(ico)
        except Exception as e:
            logger.warning("ARES lookup failed for ICO %s: %s", ico, e)

    if result:
        enrich_vat_info(result)

    return result


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
