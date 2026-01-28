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

## Poznámky
- Konfiguračné hodnoty (SMTP, Superfaktúra, defaultné nastavenia) sa nachádzajú v `config.yaml`.
- PDF výstupy sa ukladajú do priečinka `output/`.
