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
    """Return ``(html, css)`` from DB or built-in defaults."""
    try:
        tmpl = PdfTemplate.query.filter_by(entity_type=entity_type).first()
        if tmpl and tmpl.html_content:
            return tmpl.html_content, tmpl.css_content or _DEFAULT_CSS
    except Exception:
        pass
    return _DEFAULTS.get(entity_type, ""), _DEFAULT_CSS


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
    context = {
        "invoice": invoice,
        "currency": app_cfg.base_currency,
    }
    full_html = _render_html(html_tmpl, css, context)
    return _html_to_pdf(full_html, output_path)
