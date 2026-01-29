from typing import TYPE_CHECKING

import requests
import logging
from typing import TYPE_CHECKING

import requests
from requests.exceptions import RequestException

from config_models import SuperfakturaConfig

if TYPE_CHECKING:
    from app import Invoice

logger = logging.getLogger(__name__)


class SuperFakturaError(Exception):
    """Exception raised for Superfaktura API errors."""

    pass


class SuperFakturaClient:
    def __init__(self, config: SuperfakturaConfig):
        self.config = config

    def send_invoice(self, invoice: "Invoice") -> bool:
        """Send invoice to Superfaktura API.

        Args:
            invoice: Invoice object to send.

        Returns:
            True if successful, False otherwise.

        Raises:
            SuperFakturaError: If API returns an error response.
        """
        url = f"{self.config.base_url}/invoices/create"
        payload = {
            "Invoice": {
                "company_id": self.config.company_id,
                "client_id": invoice.partner_id,
                "name": f"Zúčtovacia faktúra {invoice.id}",
                "client_name": invoice.partner.name,
                "client_ico": invoice.partner.ico,
                "client_dic": invoice.partner.dic,
                "client_ic_dph": invoice.partner.ic_dph,
                "client_address": f"{invoice.partner.street} {invoice.partner.street_number}, {invoice.partner.postal_code} {invoice.partner.city}\",
                "items": [
                    {
                        "name": item.description,
                        "quantity": item.quantity,
                        "unit_price": item.unit_price,
                    }
                    for item in invoice.items
                ],
            }
        }
        response = requests.post(
            url,
            auth=(self.config.api_email, self.config.api_key),
            json=payload,
            timeout=30,
        )
        return response.status_code in {200, 201}

        try:
            logger.info(f"Sending invoice {invoice.id} to Superfaktura")
            response = requests.post(
                url,
                auth=(self.config.api_email, self.config.api_key),
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            logger.info(f"Invoice {invoice.id} sent successfully")
            return True

        except requests.exceptions.Timeout:
            logger.error(f"Timeout while sending invoice {invoice.id} to Superfaktura")
            raise SuperFakturaError("Connection to Superfaktura timed out")

        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error for invoice {invoice.id}: {e}")
            raise SuperFakturaError(f"Could not connect to Superfaktura: {e}")

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error for invoice {invoice.id}: {e}")
            raise SuperFakturaError(f"Superfaktura API error: {e}")

        except RequestException as e:
            logger.error(f"Request error for invoice {invoice.id}: {e}")
            raise SuperFakturaError(f"Request to Superfaktura failed: {e}")
