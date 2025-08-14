import logging


# default logging since this will primarily be a cmdline tool
logger = logging.getLogger(__name__.rsplit(".", 1)[0])
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
formatter = logging.Formatter(
    "\t".join(["%(name)s", "%(asctime)s", "%(levelname)s", "%(message)s"])
)
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.propagate = False
