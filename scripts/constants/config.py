import logging

from dotenv import load_dotenv

def init_env_and_logger():
    # Load the .env
    load_dotenv(override = True)

    # Initialize the logger
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

    return logger