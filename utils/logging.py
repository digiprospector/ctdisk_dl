import os
import logging
from colorlog import ColoredFormatter
import datetime
from logging.handlers import RotatingFileHandler

def get_logger():
    """Return a logger with a default ColoredFormatter."""
    formatter = ColoredFormatter(
        "%(log_color)s[%(levelname).1s][%(asctime)s][%(filename)s:%(lineno)d]:%(message)s",
        datefmt='%Y%m%d %H:%M:%S',
        reset=True,
        log_colors={
            'DEBUG':    'cyan',
            'INFO':     'green',
            'WARNING':  'yellow',
            'ERROR':    'red',
            'CRITICAL': 'bold_red',
        }
    )

    logger = logging.getLogger('videowather')
    #add handler at first time call
    if not logger.hasHandlers():
        logger.setLevel(logging.DEBUG)

        s_handler = logging.StreamHandler()
        s_handler.setFormatter(formatter)
        s_handler.setLevel(logging.ERROR)
        logger.addHandler(s_handler)

        f_handler = RotatingFileHandler(os.path.join('log', 'log'), maxBytes=10000000, backupCount=5)
        f_handler.setFormatter(formatter)
        f_handler.setLevel(logging.DEBUG)
        logger.addHandler(f_handler)

    return logger

log = get_logger()
