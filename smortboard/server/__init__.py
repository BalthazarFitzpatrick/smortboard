"""the http server: json api plus asset serving, calling into smortboard.store"""

from smortboard.server.app import build_server

__all__ = ["build_server"]
