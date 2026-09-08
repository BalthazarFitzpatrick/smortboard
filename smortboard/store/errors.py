"""exceptions raised by the store — callers never see sqlite3 errors directly"""


class StoreError(Exception):
    """base for every error the store raises"""


class BlockedReasonInvalidError(StoreError):
    """status='blocked' without a reason code, or a reason code on any other status"""


class NotFoundError(StoreError):
    """lookup or update targeted a row that does not exist"""


class UnknownFieldError(StoreError):
    """update_card was asked to write a field that is not writable"""
