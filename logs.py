import logging

from datetime import datetime
from config import *

def Logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(CONFIG.get("DEFAULT", "log_level", fallback="INFO"))

    formatter = logging.Formatter("%(relativeCreated)-8d %(levelname)-5s [%(name)-20.20s] %(message)s")

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    now = datetime.now()
    data = {
        "month": now.strftime("%m"),
        "day": now.strftime("%d"),
        "year": now.strftime("%Y")
    }
    log_path = CONFIG.get("DEFAULT", "log_path", vars=data, fallback=None)

    if log_path:
        handler = logging.FileHandler(log_path, "a", "utf-8")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger

    
    

