# DeliveryNotes_to_CollectInvoice

Jednoduchá webová aplikácia (Flask + SQLite) na evidenciu partnerov, tvorbu objednávok, dodacích listov a zúčtovacích faktúr s PDF výstupmi a exportom do Superfaktúry.

## Funkcionality
- Evidencia partnerov vrátane kontaktov, adries (sídlo/objednávacia/dodacia/fakturačná), zliav a identifikačných údajov.
- Evidencia produktov/služieb a cenníkových cien s históriou a možnosťou vylúčenia zo zliav.
- Kombinácie produktov/služieb s vlastnou cenou, históriou a označením pre vylúčenie zo zliav.
- Tvorba objednávok vrátane termínov zvozu a vyzdvihnutia a nastavenia zobrazenia cien.
- Objednávky obsahujú adresy zvozu/doručenia (z partnerových adries), spôsoby zvozu/dodania, platobné podmienky a stav potvrdenia.
- Vystavenie dodacích listov z objednávok + doplnenie neobjednaných položiek.
- Dodacie listy umožňujú výber viacerých objednávok v rovnakej skupine, plánovaný/skutočný termín dodania a potvrdenie prevzatia.
- Zúčtovanie dodacích listov do faktúry s rozpisom položiek.
- Faktúry podporujú manuálne položky a preberajú položky z dodacích listov spoločných partnerov (skupín).
- PDF výstupy dodacích listov a faktúr.
- Integrácia na export faktúr do Superfaktúry cez API.
- Plán zvozov a dodávok s evidenciou vozidiel a operačných dní/hodín.
- Audit log pre kľúčové udalosti (potvrdenia, exporty, plánovanie) a základné filtre/paginácia v prehľadoch.
- Konfiguračný súbor pre variabilné nastavenia (`config.yaml`).

## Prístupové práva (návrh)
- **admin**: plné práva (správa všetkých evidencií a exportov).
- **operator**: správa partnerov, objednávok, dodacích listov a faktúr.
- **collector**: správa dodacích listov (zvoz/vyzdvihnutie).
- **customer**: iba čítanie vlastných dokladov (v tomto prototype len návrh v role). 

Práva sú mapované v `ROLE_PERMISSIONS` v aplikácii, aby sa dali rozširovať o detailné schopnosti (napr. iba zobraziť ceny alebo len zobrazenie bez úprav).

## Spustenie
1. Nainštalujte závislosti: `pip install -r requirements.txt`
2. Skontrolujte `config.yaml` a nastavte hodnoty.
3. Spustite aplikáciu: `python app.py`
4. Prihláste sa: **admin / admin** (po prvej inicializácii databázy).

## VPS (Ubuntu) rýchle nasadenie
1. Nainštalujte Python a nginx:
   - `sudo apt update && sudo apt install -y python3 python3-venv python3-pip nginx`
2. Stiahnite repo:
   - `git clone <VAS_REPO_URL> /opt/delivery-notes`
   - Ak je repo súkromné, použite SSH URL (napr. `git@github.com:<org>/<repo>.git`).
3. Vytvorte virtualenv a nainštalujte závislosti:
   - `python3 -m venv /opt/delivery-notes/.venv`
   - `source /opt/delivery-notes/.venv/bin/activate && pip install -r /opt/delivery-notes/requirements.txt`
4. Spustite aplikáciu:
   - `source /opt/delivery-notes/.venv/bin/activate && python /opt/delivery-notes/app.py`

### Riešenie SyntaxError
Ak hlásenie uvádza `SyntaxError` pri spustení, odporúčané kroky:
1. Overte, že súbory sú aktuálne: `cd /opt/delivery-notes && git pull`.
2. Skontrolujte syntaktickú chybu: `python -m py_compile /opt/delivery-notes/app.py`.
3. Ak sa chyba objaví, prekopírujte súbor z repozitára alebo zmažte lokálny súbor a zopakujte `git pull`.

## Poznámky
- Konfiguračné hodnoty (SMTP, Superfaktúra, defaultné nastavenia) sa nachádzajú v `config.yaml`.
- PDF výstupy sa ukladajú do priečinka `output/`.
