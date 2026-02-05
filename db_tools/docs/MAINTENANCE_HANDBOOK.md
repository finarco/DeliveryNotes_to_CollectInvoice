# Database Maintenance Handbook

## Príručka pre údržbu databázy

Tento dokument poskytuje podrobné pokyny pre údržbu databázy aplikácie DeliveryNotes_to_CollectInvoice.

---

## Obsah

1. [Núdzové postupy](#1-núdzové-postupy)
2. [Bežné úlohy údržby](#2-bežné-úlohy-údržby)
3. [Sprievodca Foreign Key závislosťami](#3-sprievodca-foreign-key-závislosťami)
4. [Bezpečné mazanie záznamov](#4-bezpečné-mazanie-záznamov)
5. [Oprava dát](#5-oprava-dát)
6. [Import dát](#6-import-dát)
7. [CLI príkazy](#7-cli-príkazy)

---

## 1. Núdzové postupy

### 1.1 Zálohovanie databázy

**Pred každou údržbovou operáciou vždy vytvorte zálohu:**

```bash
# Pomocou CLI
python db_tools_cli.py backup --output backup_$(date +%Y%m%d_%H%M%S).sql

# Pomocou Flask CLI
flask db-tools backup
```

**Pomocou webového rozhrania:**
1. Prihláste sa ako admin
2. Prejdite na: DB nástroje → Správa záloh
3. Kliknite "Vytvoriť zálohu"

### 1.2 Obnova zo zálohy

```bash
# SQLite
python db_tools_cli.py restore backup_20260205.db

# PostgreSQL
python db_tools_cli.py restore backup_20260205.sql
```

### 1.3 Kontrola integrity

```bash
python db_tools_cli.py check-integrity
```

Alebo cez webové rozhranie: DB nástroje → Údržba databázy

---

## 2. Bežné úlohy údržby

### 2.1 Odomknutie dokumentu

Keď je dokument nesprávne zamknutý:

```bash
# CLI
python db_tools_cli.py unlock order 123
python db_tools_cli.py unlock delivery_note 456
python db_tools_cli.py unlock invoice 789

# Flask CLI
flask db-tools unlock order 123
```

**Webové rozhranie:** DB nástroje → Údržba → Odomknúť dokument

### 2.2 Reset sekvencií číslovania

Po vymazaní dát alebo importe resetujte sekvencie:

```bash
python db_tools_cli.py reset-sequences
```

### 2.3 Regenerovanie chýbajúcich čísel dokladov

```bash
python db_tools_cli.py regenerate-numbers order
python db_tools_cli.py regenerate-numbers delivery_note
python db_tools_cli.py regenerate-numbers invoice
```

### 2.4 Oprava osirelých záznamov

```bash
python db_tools_cli.py repair-orphans
```

---

## 3. Sprievodca Foreign Key závislosťami

### Partner - referencie (nemožno zmazať ak existujú referencie)

| Tabuľka | Stĺpec | Správanie |
|---------|--------|-----------|
| User | partner_id | Nastaviť na NULL alebo soft-delete |
| Order | partner_id | **BLOKUJE** mazanie (použiť soft delete) |
| Invoice | partner_id | **BLOKUJE** mazanie (použiť soft delete) |
| PartnerAddress | partner_id | CASCADE delete |
| Contact | partner_id | CASCADE delete |

### Product - referencie

| Tabuľka | Stĺpec | Správanie |
|---------|--------|-----------|
| OrderItem | product_id | **BLOKUJE** (deaktivovať namiesto mazania) |
| DeliveryItem | product_id | **BLOKUJE** (deaktivovať namiesto mazania) |
| BundleItem | product_id | **BLOKUJE** (deaktivovať namiesto mazania) |
| DeliveryItemComponent | product_id | **BLOKUJE** |
| ProductRestriction | product_id | Odstrániť najprv reštrikcie |
| ProductPriceHistory | product_id | CASCADE delete |

### Bundle - referencie

| Tabuľka | Stĺpec | Správanie |
|---------|--------|-----------|
| DeliveryItem | bundle_id | **BLOKUJE** (deaktivovať) |
| BundleItem | bundle_id | CASCADE delete |
| BundlePriceHistory | bundle_id | CASCADE delete |

### Order - referencie

| Tabuľka | Stĺpec | Správanie |
|---------|--------|-----------|
| OrderItem | order_id | CASCADE delete |
| DeliveryNote | primary_order_id | Nastaviť na NULL |
| DeliveryNoteOrder | order_id | CASCADE delete |
| LogisticsPlan | order_id | CASCADE delete |

### DeliveryNote - referencie

| Tabuľka | Stĺpec | Správanie |
|---------|--------|-----------|
| DeliveryItem | delivery_note_id | CASCADE delete |
| DeliveryNoteOrder | delivery_note_id | CASCADE delete |
| InvoiceItem | source_delivery_id | Nastaviť na NULL |
| LogisticsPlan | delivery_note_id | CASCADE delete |

### Invoice - referencie

| Tabuľka | Stĺpec | Správanie |
|---------|--------|-----------|
| InvoiceItem | invoice_id | CASCADE delete |

### Vehicle - referencie

| Tabuľka | Stĺpec | Správanie |
|---------|--------|-----------|
| VehicleSchedule | vehicle_id | CASCADE delete |
| LogisticsPlan | vehicle_id | Nastaviť na NULL |

---

## 4. Bezpečné mazanie záznamov

### 4.1 Poradie mazania (FK-safe)

Pri kompletnom vymazaní databázy dodržujte toto poradie:

```
Úroveň 1 (mazať ako prvé):
  - audit_log
  - product_price_history
  - bundle_price_history
  - vehicle_schedule

Úroveň 2:
  - delivery_item_component
  - invoice_item
  - delivery_item
  - logistics_plan
  - delivery_note_order
  - contact
  - product_restriction

Úroveň 3:
  - order_item
  - bundle_item
  - delivery_note
  - invoice

Úroveň 4:
  - order
  - bundle
  - product
  - vehicle
  - partner_address

Úroveň 5 (mazať ako posledné):
  - partner
  - user

Konfiguračné tabuľky (voliteľné):
  - number_sequence
  - numbering_config
  - app_setting
  - pdf_template
```

### 4.2 Soft delete vs Hard delete

**Preferujte soft delete pre:**
- Partner (`is_deleted = True`)
- Product (`is_active = False`)
- Bundle (`is_active = False`)
- User (`is_active = False`)

**Hard delete je vhodné pre:**
- Testovacie dáta
- Duplicitné záznamy
- Osirelé záznamy bez rodičov

---

## 5. Oprava dát

### 5.1 Nájdenie osirelých záznamov

```sql
-- Osirelé OrderItem
SELECT * FROM order_item
WHERE order_id NOT IN (SELECT id FROM "order");

-- Osirelé DeliveryItem
SELECT * FROM delivery_item
WHERE delivery_note_id NOT IN (SELECT id FROM delivery_note);

-- Osirelé InvoiceItem
SELECT * FROM invoice_item
WHERE invoice_id NOT IN (SELECT id FROM invoice);
```

### 5.2 Oprava nekonzistentných dát

```sql
-- Nastavenie chýbajúcich hodnôt
UPDATE product SET vat_rate = 20.0 WHERE vat_rate IS NULL;

-- Oprava nesprávnych stavov
UPDATE delivery_note SET invoiced = 0
WHERE id NOT IN (SELECT DISTINCT source_delivery_id FROM invoice_item WHERE source_delivery_id IS NOT NULL);
```

### 5.3 Kontrola duplicitných čísel faktúr

```sql
SELECT invoice_number, COUNT(*) as cnt
FROM invoice
WHERE invoice_number IS NOT NULL
GROUP BY invoice_number
HAVING COUNT(*) > 1;
```

---

## 6. Import dát

### 6.1 Podporované formáty

- **CSV** (UTF-8, oddeľovač: čiarka alebo bodkočiarka)
- **XLSX** (Excel 2007+)
- **XLS** (Excel 97-2003)

### 6.2 Poradie importu

Pre zachovanie FK závislostí importujte v tomto poradí:

1. `user`
2. `partner`
3. `partner_address`
4. `contact`
5. `product`
6. `bundle`
7. `bundle_item`
8. `vehicle`

### 6.3 FK rozlišovanie názvov

Import podporuje odkazovanie pomocou názvov namiesto ID:

```csv
# Namiesto partner_id môžete použiť partner_name
partner_name,name,email
"ABC Company s.r.o.","Ján Novák",jan@abc.sk
```

**Normalizácia názvov:**
- Case-insensitive: `FINARCO` = `finarco`
- Normalizácia medzier: `s. r. o.` = `s.r.o.`
- Normalizácia čiarok: `finarco, s.r.o.` = `finarco s.r.o.`

**Pozor:** Rôzne názvy zostávajú rôzne:
- `finarco s.r.o.` ≠ `finarco B s.r.o.`

### 6.4 Validačné pravidlá

| Entita | Povinné polia | Validácie |
|--------|---------------|-----------|
| Partner | name | IČO: 8 číslic, DIČ: 10 číslic, IČ DPH: SK + 10 číslic |
| Contact | partner_id/partner_name, name | email validácia |
| Product | name, price | price >= 0, vat_rate 0-100 |
| Bundle | name, bundle_price | bundle_price >= 0 |
| Vehicle | name | registration_number unique |

---

## 7. CLI príkazy

### Základné príkazy

```bash
# Zobraziť pomoc
python db_tools_cli.py --help

# Záloha
python db_tools_cli.py backup [--output FILE]

# Obnova
python db_tools_cli.py restore BACKUP_FILE

# Vymazanie databázy
python db_tools_cli.py wipe [--dry-run] [--include-config] [--confirm]

# Import
python db_tools_cli.py import FILE --entity-type TYPE [--preview] [--conflict-mode skip|update|error]

# Šablóna importu
python db_tools_cli.py template ENTITY_TYPE

# Kontrola integrity
python db_tools_cli.py check-integrity

# Reset sekvencií
python db_tools_cli.py reset-sequences

# Oprava osirelých záznamov
python db_tools_cli.py repair-orphans

# Odomknutie dokumentu
python db_tools_cli.py unlock ENTITY_TYPE ENTITY_ID

# Export do CSV
python db_tools_cli.py export ENTITY_TYPE [--output FILE]

# SQL dotaz (len SELECT)
python db_tools_cli.py query "SELECT * FROM partner LIMIT 10"
```

### Flask CLI príkazy

```bash
flask db-tools backup
flask db-tools restore BACKUP_FILE
flask db-tools wipe --dry-run
flask db-tools import FILE --type partner
flask db-tools check-integrity
```

---

## Kontakt a podpora

Pri problémoch kontaktujte administrátora systému alebo vytvorte issue na GitHub repozitári.

---

*Posledná aktualizácia: 2026-02-05*
