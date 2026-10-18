# The API maps each DomainError subclass to an HTTP status in a single place,
# so the domain never needs to know about HTTP.
class DomainError(Exception):
    pass


class CurrencyMismatchError(DomainError):
    pass


class InvalidValueError(DomainError):
    pass
