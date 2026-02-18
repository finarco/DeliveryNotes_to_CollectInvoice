"""PDF generation using admin-editable HTML/CSS templates.

Admin can customise the HTML/CSS for each document type via the settings
interface.  When no custom template exists, built-in defaults are used.
HTML is rendered with Jinja2 then converted to PDF via *weasyprint* or
*xhtml2pdf*.  If neither is available the output falls back to a plain
HTML file (or the legacy reportlab approach).
"""

from __future__ import annotations

import os

from jinja2.sandbox import SandboxedEnvironment

from models import PdfTemplate
from services.tenant import get_current_tenant_id

# Try HTML-to-PDF converters (optional dependencies)
try:
    from xhtml2pdf import pisa  # type: ignore[import-untyped]

    _HAS_XHTML2PDF = True
except ImportError:
    _HAS_XHTML2PDF = False

try:
    import weasyprint  # type: ignore[import-untyped]

    _HAS_WEASYPRINT = True
except ImportError:
    _HAS_WEASYPRINT = False

_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output"
)

# ---------------------------------------------------------------------------
# Default templates
# ---------------------------------------------------------------------------

_DEFAULT_CSS = """
body { font-family: DejaVu Sans, Arial, sans-serif; font-size: 10pt; margin: 20mm; }
h1 { font-size: 14pt; margin-bottom: 10px; }
h2 { font-size: 12pt; margin-top: 15px; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; }
th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: left; }
th { background: #f0f0f0; }
.info-table td { border: none; padding: 2px 8px; }
.info-table { margin-bottom: 10px; }
.total { font-size: 12pt; font-weight: bold; margin-top: 15px; }
"""

_DEFAULT_DELIVERY_HTML = """\
<h1>Dodaci list {{ delivery.note_number or delivery.id }}</h1>
<table class="info-table">
  <tr><td><strong>Partner:</strong></td><td>{{ partner_name }}</td></tr>
  <tr><td><strong>Datum:</strong></td><td>{{ delivery.created_at.strftime('%d.%m.%Y') if delivery.created_at else '' }}</td></tr>
  <tr><td><strong>Planovany termin:</strong></td><td>{{ delivery.planned_delivery_datetime or '' }}</td></tr>
  <tr><td><strong>Skutocny termin:</strong></td><td>{{ delivery.actual_delivery_datetime or '' }}</td></tr>
</table>
<h2>Polozky</h2>
<table>
  <thead>
    <tr>
      <th>Nazov</th><th>Mnozstvo</th>
      {% if delivery.show_prices %}<th>Jedn. cena</th><th>Celkom</th>{% endif %}
    </tr>
  </thead>
  <tbody>
    {% for item in delivery.items %}
    <tr>
      <td>{{ item.product.name if item.product else (item.bundle.name if item.bundle else 'Polozka') }}</td>
      <td>{{ item.quantity }}x</td>
      {% if delivery.show_prices %}
      <td>{{ '%.2f'|format(item.unit_price) }} {{ currency }}</td>
      <td>{{ '%.2f'|format(item.line_total) }} {{ currency }}</td>
      {% endif %}
    </tr>
    {% for comp in item.components %}
    <tr>
      <td style="padding-left:20px">- {{ comp.product.name }}: {{ comp.quantity }}x</td>
      <td></td>{% if delivery.show_prices %}<td></td><td></td>{% endif %}
    </tr>
    {% endfor %}
    {% endfor %}
  </tbody>
</table>
"""

_DEFAULT_INVOICE_HTML = """\
<h1>Zuctovacia faktura {{ invoice.invoice_number or invoice.id }}</h1>
<table class="info-table">
  <tr><td><strong>Partner:</strong></td><td>{{ invoice.partner.name }}</td></tr>
  <tr><td><strong>Datum:</strong></td><td>{{ invoice.created_at.strftime('%d.%m.%Y') if invoice.created_at else '' }}</td></tr>
  {% if invoice.partner.ico %}<tr><td><strong>ICO:</strong></td><td>{{ invoice.partner.ico }}</td></tr>{% endif %}
  {% if invoice.partner.dic %}<tr><td><strong>DIC:</strong></td><td>{{ invoice.partner.dic }}</td></tr>{% endif %}
  {% if invoice.partner.ic_dph %}<tr><td><strong>IC DPH:</strong></td><td>{{ invoice.partner.ic_dph }}</td></tr>{% endif %}
</table>
<h2>Polozky</h2>
<table>
  <thead>
    <tr><th>Popis</th><th>Mnozstvo</th><th>Jedn. cena</th><th>DPH</th><th>Celkom s DPH</th></tr>
  </thead>
  <tbody>
    {% for item in invoice.items %}
    <tr>
      <td>{{ item.description }}</td>
      <td>{{ item.quantity }}x</td>
      <td>{{ '%.2f'|format(item.unit_price) }} {{ currency }}</td>
      <td>{{ '%.0f'|format(item.vat_rate) }}%</td>
      <td>{{ '%.2f'|format(item.total_with_vat) }} {{ currency }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
<p class="total">Spolu bez DPH: {{ '%.2f'|format(invoice.total) }} {{ currency }}</p>
<p class="total">Spolu s DPH: {{ '%.2f'|format(invoice.total_with_vat) }} {{ currency }}</p>
{% if qr_code_base64 %}
<div style="margin-top: 20px; text-align: center;">
  <p><strong>QR kod na platbu (PayBySquare)</strong></p>
  <img src="{{ qr_code_base64 }}" alt="QR platba" style="width: 150px; height: 150px;">
</div>
{% endif %}
"""

_DEFAULTS = {
    "delivery_note": _DEFAULT_DELIVERY_HTML,
    "invoice": _DEFAULT_INVOICE_HTML,
}


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------


def get_default_html(entity_type: str) -> str:
    """Return the built-in default HTML for *entity_type*."""
    return _DEFAULTS.get(entity_type, "")


def get_default_css() -> str:
    """Return the built-in default CSS."""
    return _DEFAULT_CSS


def _get_template(entity_type: str) -> tuple[str, str]:
    """Return ``(html, css)`` from DB or built-in defaults.

    Priority order:
    1. layout_config (visual editor JSON) — converted to HTML/CSS on-the-fly
    2. html_content / css_content (raw template editor)
    3. Built-in defaults
    """
    try:
        import json as _json
        tid = get_current_tenant_id()
        tmpl = PdfTemplate.query.filter_by(
            tenant_id=tid, entity_type=entity_type
        ).first()
        if tmpl and tmpl.layout_config:
            try:
                config = _json.loads(tmpl.layout_config)
                if config:
                    html_from_config = _html_from_config(entity_type, config)
                    css_from_config = _css_from_config(config)
                    return html_from_config, css_from_config
            except (ValueError, TypeError):
                pass
        if tmpl and tmpl.html_content:
            return tmpl.html_content, tmpl.css_content or _DEFAULT_CSS
    except Exception:
        pass
    return _DEFAULTS.get(entity_type, ""), _DEFAULT_CSS


def _css_from_config(config: dict) -> str:
    """Build a CSS string from a layout_config dict."""
    margins = config.get("margins", {})
    top = margins.get("top", 20)
    bottom = margins.get("bottom", 20)
    left = margins.get("left", 15)
    right = margins.get("right", 15)

    colors = config.get("colors", {})
    primary = colors.get("primary", "#1a1a2e")
    accent = colors.get("accent", "#e94560")

    fonts = config.get("fonts", {})
    heading_font = fonts.get("heading", "Space Grotesk")
    body_font = fonts.get("body", "Inter")

    return f"""
body {{
  font-family: '{body_font}', DejaVu Sans, Arial, sans-serif;
  font-size: 10pt;
  margin: {top}mm {right}mm {bottom}mm {left}mm;
  color: #333333;
}}
h1, h2, h3 {{
  font-family: '{heading_font}', Arial, sans-serif;
  color: {primary};
}}
h1 {{ font-size: 16pt; margin-bottom: 8px; }}
h2 {{ font-size: 12pt; margin-top: 14px; margin-bottom: 6px; }}
table {{
  width: 100%;
  border-collapse: collapse;
  margin-top: 10px;
}}
th {{
  background: {primary};
  color: #ffffff;
  padding: 5px 8px;
  text-align: left;
  font-family: '{heading_font}', Arial, sans-serif;
  font-size: 9pt;
}}
td {{
  border: 1px solid #dddddd;
  padding: 4px 8px;
  text-align: left;
  font-size: 9pt;
}}
.info-table td {{ border: none; padding: 2px 8px; }}
.info-table {{ margin-bottom: 12px; }}
.total {{ font-size: 11pt; font-weight: bold; margin-top: 12px; color: {primary}; }}
.footer-section {{ margin-top: 20px; border-top: 1px solid {primary}; padding-top: 10px; font-size: 8pt; color: #666666; }}
.accent {{ color: {accent}; }}
.header-section {{ margin-bottom: 16px; border-bottom: 2px solid {primary}; padding-bottom: 10px; }}
.company-info {{ font-size: 9pt; color: #555555; }}
"""


def _html_from_config(entity_type: str, config: dict) -> str:
    """Build a Jinja2 HTML template from a layout_config dict."""
    header_cfg = config.get("header", {})
    show_logo = header_cfg.get("show_logo", True)
    logo_position = header_cfg.get("logo_position", "left")
    show_company_info = header_cfg.get("show_company_info", True)

    columns = config.get("columns", ["item_name", "quantity", "unit_price", "total"])

    footer_cfg = config.get("footer", {})
    show_qr_code = footer_cfg.get("show_qr_code", True)
    show_bank_details = footer_cfg.get("show_bank_details", True)
    show_notes = footer_cfg.get("show_notes", True)

    # Logo alignment style
    logo_align = {"left": "left", "center": "center", "right": "right"}.get(logo_position, "left")

    # Build header block
    header_parts = []
    if show_logo or show_company_info:
        logo_html = ""
        company_html = ""
        if show_logo:
            logo_html = (
                "{% if tenant and tenant.logo_filename %}"
                "<img src=\"{{ url_for('uploaded_file', filename='logos/' + tenant.logo_filename) }}\""
                " alt=\"Logo\" style=\"max-height:60px; max-width:160px;\">"
                "{% endif %}"
            )
        if show_company_info:
            company_html = (
                "{% if tenant %}"
                "<div class=\"company-info\">"
                "<strong>{{ tenant.name }}</strong><br>"
                "{% if tenant.street %}{{ tenant.street }}, {% endif %}"
                "{% if tenant.city %}{{ tenant.city }}{% endif %}<br>"
                "{% if tenant.ico %}ICO: {{ tenant.ico }}{% endif %}"
                "{% if tenant.dic %} | DIC: {{ tenant.dic }}{% endif %}"
                "</div>"
                "{% endif %}"
            )
        header_parts.append(
            f'<div class="header-section" style="text-align:{logo_align};">'
            + logo_html
            + company_html
            + "</div>"
        )

    header_html = "\n".join(header_parts)

    # Build table columns
    col_labels = {
        "item_name": "Nazov",
        "quantity": "Mnozstvo",
        "unit_price": "Jedn. cena",
        "vat_rate": "DPH %",
        "total": "Celkom",
    }

    th_cells = "".join(f"<th>{col_labels.get(c, c)}</th>" for c in columns)

    if entity_type == "delivery_note":
        td_cells = ""
        for col in columns:
            if col == "item_name":
                td_cells += "<td>{{ item.product.name if item.product else (item.bundle.name if item.bundle else 'Polozka') }}</td>"
            elif col == "quantity":
                td_cells += "<td>{{ item.quantity }}x</td>"
            elif col == "unit_price":
                td_cells += "<td>{{ '%.2f'|format(item.unit_price) }} {{ currency }}</td>"
            elif col == "vat_rate":
                td_cells += "<td></td>"
            elif col == "total":
                td_cells += "<td>{{ '%.2f'|format(item.line_total) }} {{ currency }}</td>"

        table_html = (
            "<h2>Polozky</h2>\n"
            "<table>\n"
            "  <thead><tr>" + th_cells + "</tr></thead>\n"
            "  <tbody>\n"
            "    {% for item in delivery.items %}\n"
            "    <tr>" + td_cells + "</tr>\n"
            "    {% endfor %}\n"
            "  </tbody>\n"
            "</table>"
        )

        doc_title = "<h1>Dodaci list {{ delivery.note_number or delivery.id }}</h1>"
        info_table = (
            "<table class=\"info-table\">\n"
            "  <tr><td><strong>Partner:</strong></td><td>{{ partner_name }}</td></tr>\n"
            "  <tr><td><strong>Datum:</strong></td><td>"
            "{{ delivery.created_at.strftime('%d.%m.%Y') if delivery.created_at else '' }}"
            "</td></tr>\n"
            "</table>"
        )
        footer_html = _build_footer_html(footer_cfg, show_qr_code, show_bank_details, show_notes, entity_type="delivery_note")
        return header_html + doc_title + info_table + table_html + footer_html

    else:  # invoice
        td_cells = ""
        for col in columns:
            if col == "item_name":
                td_cells += "<td>{{ item.description }}</td>"
            elif col == "quantity":
                td_cells += "<td>{{ item.quantity }}x</td>"
            elif col == "unit_price":
                td_cells += "<td>{{ '%.2f'|format(item.unit_price) }} {{ currency }}</td>"
            elif col == "vat_rate":
                td_cells += "<td>{{ '%.0f'|format(item.vat_rate) }}%</td>"
            elif col == "total":
                td_cells += "<td>{{ '%.2f'|format(item.total_with_vat) }} {{ currency }}</td>"

        table_html = (
            "<h2>Polozky</h2>\n"
            "<table>\n"
            "  <thead><tr>" + th_cells + "</tr></thead>\n"
            "  <tbody>\n"
            "    {% for item in invoice.items %}\n"
            "    <tr>" + td_cells + "</tr>\n"
            "    {% endfor %}\n"
            "  </tbody>\n"
            "</table>\n"
            "<p class=\"total\">Spolu bez DPH: {{ '%.2f'|format(invoice.total) }} {{ currency }}</p>\n"
            "<p class=\"total\">Spolu s DPH: {{ '%.2f'|format(invoice.total_with_vat) }} {{ currency }}</p>"
        )

        doc_title = "<h1>Faktura {{ invoice.invoice_number or invoice.id }}</h1>"
        info_table = (
            "<table class=\"info-table\">\n"
            "  <tr><td><strong>Partner:</strong></td><td>{{ invoice.partner.name }}</td></tr>\n"
            "  <tr><td><strong>Datum:</strong></td><td>"
            "{{ invoice.created_at.strftime('%d.%m.%Y') if invoice.created_at else '' }}"
            "</td></tr>\n"
            "  {% if invoice.partner.ico %}<tr><td><strong>ICO:</strong></td><td>{{ invoice.partner.ico }}</td></tr>{% endif %}\n"
            "  {% if invoice.partner.dic %}<tr><td><strong>DIC:</strong></td><td>{{ invoice.partner.dic }}</td></tr>{% endif %}\n"
            "</table>"
        )
        footer_html = _build_footer_html(footer_cfg, show_qr_code, show_bank_details, show_notes, entity_type="invoice")
        return header_html + doc_title + info_table + table_html + footer_html


def _build_footer_html(
    footer_cfg: dict,
    show_qr_code: bool,
    show_bank_details: bool,
    show_notes: bool,
    entity_type: str = "invoice",
) -> str:
    """Build the footer HTML block based on footer config flags."""
    parts = []

    if show_bank_details:
        parts.append(
            "{% if bank_iban %}"
            "<div>Banka: {{ bank_name or '' }} | IBAN: {{ bank_iban }}</div>"
            "{% if bank_swift %}<div>SWIFT: {{ bank_swift }}</div>{% endif %}"
            "{% endif %}"
        )

    if show_notes and entity_type == "delivery_note":
        parts.append(
            "{% if delivery.notes %}<div><strong>Poznamky:</strong> {{ delivery.notes }}</div>{% endif %}"
        )
    elif show_notes and entity_type == "invoice":
        parts.append(
            "{% if invoice.notes %}<div><strong>Poznamky:</strong> {{ invoice.notes }}</div>{% endif %}"
        )

    if show_qr_code:
        parts.append(
            "{% if qr_code_base64 %}"
            "<div style=\"text-align:center; margin-top:10px;\">"
            "<p><strong>QR kod na platbu</strong></p>"
            "<img src=\"{{ qr_code_base64 }}\" alt=\"QR platba\" style=\"width:120px;height:120px;\">"
            "</div>"
            "{% endif %}"
        )

    if not parts:
        return ""

    return (
        "\n<div class=\"footer-section\">\n"
        + "\n".join(parts)
        + "\n</div>"
    )


def render_layout_preview(entity_type: str, config: dict) -> str:
    """Render a sample HTML preview document from a layout_config dict.

    Returns a fully self-contained HTML string suitable for display in an iframe.
    Uses synthetic sample data so no real DB records are needed.
    """
    css = _css_from_config(config)
    html_tmpl = _html_from_config(entity_type, config)

    # Build lightweight sample context objects using simple namespaces
    from types import SimpleNamespace
    import datetime

    sample_date = datetime.date(2026, 2, 18)

    if entity_type == "delivery_note":
        item1 = SimpleNamespace(
            product=SimpleNamespace(name="Montazna praca"),
            bundle=None,
            quantity=3,
            unit_price=50.00,
            line_total=150.00,
            components=[],
        )
        item2 = SimpleNamespace(
            product=SimpleNamespace(name="Material"),
            bundle=None,
            quantity=10,
            unit_price=12.50,
            line_total=125.00,
            components=[],
        )
        delivery = SimpleNamespace(
            id=42,
            note_number="DL-2026-042",
            created_at=sample_date,
            planned_delivery_datetime=None,
            actual_delivery_datetime=None,
            show_prices=True,
            items=[item1, item2],
            notes="Tovar dodany v poriadku.",
        )
        context = {
            "delivery": delivery,
            "partner_name": "ABC s.r.o.",
            "currency": "EUR",
            "tenant": SimpleNamespace(
                name="Moja Firma s.r.o.",
                street="Hlavna 1",
                city="Bratislava",
                ico="12345678",
                dic="SK12345678",
                logo_filename=None,
            ),
            "bank_iban": "SK89 0200 0000 0012 3456 7890",
            "bank_swift": "SUBASKBX",
            "bank_name": "Vseobecna uverova banka",
            "qr_code_base64": None,
        }
    else:  # invoice
        inv_item1 = SimpleNamespace(
            description="Montazne prace - januar 2026",
            quantity=1,
            unit_price=450.00,
            vat_rate=20.0,
            total_with_vat=540.00,
        )
        inv_item2 = SimpleNamespace(
            description="Material a spotrebny tovar",
            quantity=1,
            unit_price=250.00,
            vat_rate=20.0,
            total_with_vat=300.00,
        )
        partner = SimpleNamespace(
            name="XYZ a.s.",
            ico="87654321",
            dic="SK87654321",
            ic_dph=None,
        )
        invoice = SimpleNamespace(
            id=7,
            invoice_number="FA-2026-007",
            created_at=sample_date,
            partner=partner,
            items=[inv_item1, inv_item2],
            total=700.00,
            total_with_vat=840.00,
            notes=None,
        )
        context = {
            "invoice": invoice,
            "currency": "EUR",
            "tenant": SimpleNamespace(
                name="Moja Firma s.r.o.",
                street="Hlavna 1",
                city="Bratislava",
                ico="12345678",
                dic="SK12345678",
                logo_filename=None,
            ),
            "bank_iban": "SK89 0200 0000 0012 3456 7890",
            "bank_swift": "SUBASKBX",
            "bank_name": "Vseobecna uverova banka",
            "qr_code_base64": None,
        }

    return _render_html(html_tmpl, css, context)


def _render_html(html_template: str, css: str, context: dict) -> str:
    """Render the Jinja2 HTML template wrapped in a full HTML document."""
    env = SandboxedEnvironment()
    tmpl = env.from_string(html_template)
    body = tmpl.render(**context)
    return (
        "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
        f"<style>{css}</style></head><body>{body}</body></html>"
    )


def _html_to_pdf(full_html: str, output_path: str) -> str:
    """Convert rendered HTML to PDF.  Returns the output file path."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    if _HAS_WEASYPRINT:
        weasyprint.HTML(string=full_html).write_pdf(output_path)
        return output_path

    if _HAS_XHTML2PDF:
        with open(output_path, "wb") as fh:
            pisa.CreatePDF(full_html, dest=fh)
        return output_path

    # Fallback: save as HTML (user can print-to-PDF from browser)
    html_path = output_path.rsplit(".", 1)[0] + ".html"
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(full_html)
    return html_path


# ---------------------------------------------------------------------------
# Public API — same signatures as the legacy version
# ---------------------------------------------------------------------------


def generate_delivery_pdf(delivery, app_cfg) -> str:
    """Generate a PDF for a delivery note and return the file path."""
    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(_OUTPUT_DIR, f"delivery_{delivery.id}.pdf")

    html_tmpl, css = _get_template("delivery_note")
    partner_name = (
        delivery.primary_order.partner.name if delivery.primary_order else ""
    )
    context = {
        "delivery": delivery,
        "partner_name": partner_name,
        "currency": app_cfg.base_currency,
    }
    full_html = _render_html(html_tmpl, css, context)
    return _html_to_pdf(full_html, output_path)


def generate_invoice_pdf(invoice, app_cfg) -> str:
    """Generate a PDF for an invoice and return the file path."""
    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(_OUTPUT_DIR, f"invoice_{invoice.id}.pdf")

    html_tmpl, css = _get_template("invoice")

    # Generate QR code for payment (PayBySquare)
    qr_code_base64 = None
    try:
        from models import Tenant
        tenant = Tenant.query.get(invoice.tenant_id)
        if tenant:
            from services.qr_payment import generate_invoice_qr
            qr_code_base64 = generate_invoice_qr(invoice, tenant)
    except Exception:
        pass

    context = {
        "invoice": invoice,
        "currency": app_cfg.base_currency,
        "qr_code_base64": qr_code_base64,
    }
    full_html = _render_html(html_tmpl, css, context)
    return _html_to_pdf(full_html, output_path)
