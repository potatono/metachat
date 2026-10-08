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
    def post(cls, line):
        """Queue a line for the console regardless of log level."""
        cls._message_queue.put(line)
        cls._last_log_time = time.time()

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

    # Always use curses-safe handler that queues messages.  The console can't
    # scroll back, so it only shows problems; the log file gets everything.
    handler = CursesLogHandler()
    handler.setLevel(CONFIG.get("DEFAULT", "console_log_level", fallback="WARNING"))
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


_conversation_log = None

def conversation(speaker, text):
    """A line of the pairing conversation: always shown on the console,
    whatever its log level, and written to the log file."""
    global _conversation_log
    if _conversation_log is None:
        _conversation_log = Logger("conversation")
        # The line is posted to the console below; the logger only writes
        # the file, or a low console_log_level would show it twice.
        for handler in list(_conversation_log.handlers):
            if isinstance(handler, CursesLogHandler):
                _conversation_log.removeHandler(handler)
    line = f"{speaker}: {text}"
    CursesLogHandler.post(line)
    _conversation_log.info(line)

    
    

