#!/bin/bash
# This script activates your Python environment and runs your pipeline

# (1) Exit immediately if a command exits with a non-zero status
set -e

# (2) Activate your Python virtual environment (adjust this path)
source ~/Uni/Master/Semester_3/Dashboard_Project/venv/Dashboard_Project-_hGijfcO

# (3) Navigate to your project directory
cd /Users/yannickkayser/Uni/Master/Semester_3/Dashboard_Project/src/

# (4) Run the pipeline
python3 pipeline.py

# (5) Optionally log a message
echo "Pipeline executed successfully on $(date)"w