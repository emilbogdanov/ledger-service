# Shared here rather than in each aggregate so journal entries can reference accounts
# without importing them.
from typing import NewType
from uuid import UUID

AccountId = NewType("AccountId", UUID)
EntryId = NewType("EntryId", UUID)
