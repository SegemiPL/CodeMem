import json

# File Dependency and Repo Dependency addr of SWE-Gym filtered data
FILE_DEPENDENCY_ADDR = "../data/file_dependency.json"
REPO_DEPENDENCY_ADDR = "../data/repo_dependency.json"
TARGET_SEQUENCE_ADDR = "../data/target_sequence.json"

# Instance info and qa addr of SWE-Gym
INSTANCE_INFO_ADDR = "../data/instance_info.json"
INSTANCE_QA_ADDR = "../data/instance_qa.json"

# Instance_query_addr of OctoBench data
INSTANCE_QUERY_ADDR = "../data/octo_instance_query.json"

# Code_Mem all dataset
CODE_MEM_ADDR = "../data/code_mem_dataset.json"

# Load_json
def load_json(addr: str)->dict:
    d = {}
    with open(addr, "r", encoding = "utf-8") as f:
        d = json.load(f)
    return d

def save_json(addr: str, d: dict):
    with open(addr, "w", encoding = "utf-8") as f:
        json.dump(d,f,indent = 4)