# Data_Feature

This document offers a global insight for some feature of the origin dataset

## SWE-Gym

### About the base commit

- Totol Instance: 2438
- Same Base Commit Numbers: 223
    - These 223 same base commit has 2-3 instance

### About the patch file

- Almost all the files in patch are .py, some files are not:
{'in', 'pyx', 'txt', 'pyi', 'yaml', 'pxd', 'c', 'build', 'md', 'ipynb', 'yml', 'sh', 'h', 'rst'}

- maybe some parts of these should be filtered, such as the "md","in","txt"

### About the dependency relationship

- the dict under repo_dependency.json records the dependency between instances across different repos.
- These data can be used to filter the target and middle (E.g. When a target has at least 5 (or other) non-dependent middle instance, then we choose it as a target)
- The dict structure:
- repo
    - Target instance id                             # The Target instance id in each repo
        - Middle instance id : no_dependent          # if the middle instance touched files is all the same in target instance base_commit, the number is 1, else 0.
    - ...
- ...

- the dict under file_dependency is formed from repo_dependency, filtered by removing middle instance with same touched-files.
- The structure is all the same 

- The dict under instance_qa.json records QA for each target.
- Currently there are only 10 instances' attempts, with 7 actually available instances (3 generated failed).





