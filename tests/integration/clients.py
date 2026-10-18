from uuid import UUID

from ledger.application.clients import Client
from ledger.domain.ids import ClientId

# Registered before every integration test, since accounts and entries must belong to a real
# client.
ACME = ClientId(UUID("0b6f2c8e-3a41-4d6e-9c55-7e1d2a9f4b10"))
GLOBEX = ClientId(UUID("5e8a1d47-c2b9-4f30-8d16-a4c7e9b2f051"))
REGISTERED = (Client(ACME, "Acme Payments"), Client(GLOBEX, "Globex Payroll"))
