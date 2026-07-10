""" Evaluate-SWE-Gym-Revert.py
Evaluate scripts for "Revert" part of the CodeMem Datasets

In-Develop

"""
from constants.addr import CODE_MEM_ADDR,load_json
from constants.config import init_env_and_logger

def main():
    # initialize logger
    logger = init_env_and_logger()
    logger.info("Logger setup success!")

    ## Step1: Load the datasets
    ds = load_json(CODE_MEM_ADDR)

if __name__ == "__main__":
    main()