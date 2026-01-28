from typing import TYPE_CHECKING

import requests

from config_models import SuperfakturaConfig

if TYPE_CHECKING:
    from app import Invoice


class SuperFakturaClient:
    def __init__(self, config: SuperfakturaConfig):
        self.config = config

    def send_invoice(self, invoice: "Invoice") -> bool:
        url = f"{self.config.base_url}/invoices/create"
        payload = {
            "Invoice": {
                "company_id": self.config.company_id,
                "client_id": invoice.partner_id,
                "name": f"Zúčtovacia faktúra {invoice.id}",
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
