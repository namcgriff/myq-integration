class MyQError(Exception):
    """Base exception for myQ client errors."""


class MyQAuthError(MyQError):
    """Authentication failed."""


class MyQApiError(MyQError):
    """The myQ API returned an error."""


class MyQUnsupportedError(MyQError):
    """A requested transport or operation is not supported."""
