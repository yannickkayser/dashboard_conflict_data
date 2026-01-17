#!/bin/bash
# run-pipelines.sh - Smart wrapper for all pipelines
# Usage: ./run-pipelines.sh [acled|gnews|matching|sentiment|all|collection|processing]

set -e

PROJECT_DIR="/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data"
LOG_DIR="$PROJECT_DIR/logs"
DATE=$(date +%Y%m%d_%H%M%S)

cd "$PROJECT_DIR"

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Function to run ACLED pipeline
run_acled() {
    echo "Running ACLED pipeline..." | tee -a "$LOG_DIR/pipeline_master.log"
    if python src/pipelineACLED.py >> "$LOG_DIR/acled_$DATE.log" 2>&1; then
        echo "✓ ACLED completed at $(date)" | tee -a "$LOG_DIR/pipeline_master.log"
        return 0
    else
        echo "✗ ACLED failed at $(date)" | tee -a "$LOG_DIR/pipeline_master.log"
        return 1
    fi
}

# Function to run GNews pipeline
run_gnews() {
    echo "Running GNews pipeline..." | tee -a "$LOG_DIR/pipeline_master.log"
    if python src/pipelineGNEWS.py >> "$LOG_DIR/gnews_$DATE.log" 2>&1; then
        echo "✓ GNews completed at $(date)" | tee -a "$LOG_DIR/pipeline_master.log"
        return 0
    else
        echo "✗ GNews failed at $(date)" | tee -a "$LOG_DIR/pipeline_master.log"
        return 1
    fi
}

# Function to run matching pipeline
run_matching() {
    echo "Running matching pipeline..." | tee -a "$LOG_DIR/pipeline_master.log"
    if python src/pipelinematchingCountry.py >> "$LOG_DIR/matching_$DATE.log" 2>&1; then
        echo "✓ Matching completed at $(date)" | tee -a "$LOG_DIR/pipeline_master.log"
        return 0
    else
        echo "✗ Matching failed at $(date)" | tee -a "$LOG_DIR/pipeline_master.log"
        return 1
    fi
}

# Function to run sentiment analysis
run_sentiment() {
    echo "Running sentiment analysis..." | tee -a "$LOG_DIR/pipeline_master.log"
    if python src/pipelineSentimentAnalysis.py >> "$LOG_DIR/sentiment_$DATE.log" 2>&1; then
        echo "✓ Sentiment completed at $(date)" | tee -a "$LOG_DIR/pipeline_master.log"
        return 0
    else
        echo "✗ Sentiment failed at $(date)" | tee -a "$LOG_DIR/pipeline_master.log"
        return 1
    fi
}

# Main logic
PIPELINE=${1:-all}  # Default to "all" if no argument provided

echo "===== Pipeline Run Started: $(date) =====" | tee -a "$LOG_DIR/pipeline_master.log"
echo "Mode: $PIPELINE" | tee -a "$LOG_DIR/pipeline_master.log"

case "$PIPELINE" in
    acled)
        run_acled
        ;;
    gnews)
        run_gnews
        ;;
    matching)
        run_matching
        ;;
    sentiment)
        run_sentiment
        ;;
    collection)
        # Run data collection pipelines
        run_acled && run_gnews
        ;;
    processing)
        # Run data processing pipelines
        run_matching && run_sentiment
        ;;
    all)
        # Run all pipelines in sequence
        run_acled && run_gnews && run_matching && run_sentiment
        ;;
    *)
        echo "Unknown pipeline: $PIPELINE"
        echo "Usage: $0 [acled|gnews|matching|sentiment|all|collection|processing]"
        exit 1
        ;;
esac

EXIT_CODE=$?
echo "===== Pipeline Run Completed: $(date) =====" | tee -a "$LOG_DIR/pipeline_master.log"
exit $EXIT_CODE