#!/bin/bash

/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/venv/bin/python \
/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/src/pipline.py \
>> /home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/scripts/log/pipeline.log 2>&1
