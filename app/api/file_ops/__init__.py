# package marker

from . import ingest, upload


def load_upload():
    from . import upload


def load_ingest_unprocessed():
    from . import ingest_unprocessed
