import logging
import os
from config import Config

os.makedirs('logs', exist_ok=True)

def setup_logger():
    logger = logging.getLogger('TradingBot')
    logger.setLevel(getattr(logging, Config.LOG_LEVEL))
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    file_handler = logging.FileHandler(Config.LOG_FILE)
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logger()
