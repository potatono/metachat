import logging
import time
import queue
from datetime import datetime
from config import *


class CursesLogHandler(logging.Handler):
    """Thread-safe log handler that queues messages for curses display"""
    
    _message_queue = queue.Queue()
    _last_log_time = 0
    
    def __init__(self):
        super().__init__()
        
    def emit(self, record):
        try:
            msg = self.format(record)
            CursesLogHandler._message_queue.put(msg)
            CursesLogHandler._last_log_time = time.time()
        except:
            pass  # Don't let logging errors break the app
    
    @classmethod
    def get_message_queue(cls):
        return cls._message_queue
    
    @classmethod
    def get_last_log_time(cls):
        return cls._last_log_time


def Logger(name):
    """Create logger for curses display"""
    logger = logging.getLogger(name)
    logger.setLevel(CONFIG.get("DEFAULT", "log_level", fallback="INFO"))

    formatter = logging.Formatter("%(relativeCreated)-8d %(levelname)-5s [%(name)-20.20s] %(message)s")

    # Always use curses-safe handler that queues messages
    handler = CursesLogHandler()
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
        file_handler = logging.FileHandler(log_path, "a", "utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger

    
    

