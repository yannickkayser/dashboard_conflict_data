# Pipeline Automation Guide

## Overview

The conflict data pipeline runs automatically using **supercronic** (a cron-compatible job scheduler) to ensure continuous data collection and processing. This document explains the automation architecture, scheduling strategy, and maintenance procedures.

***

## Automation Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    AUTOMATION WORKFLOW                            │
└──────────────────────────────────────────────────────────────────┘

Supercronic (Scheduler)
    │
    ├─→ Weekly (Sunday 2 AM)  → run-pipelines.sh acled
    │                            └─→ pipelineACLED.py
    │
    ├─→ Daily (6 AM)           → run-pipelines.sh gnews
    │                            └─→ pipelineGNEWS.py
    │
    ├─→ Weekly (Sunday 4 AM)   → run-pipelines.sh matching
    │                            └─→ pipelinematchingCountry.py
    │
    └─→ Daily (5 AM)           → run-pipelines.sh sentiment
                                 └─→ pipelineSentimentAnalysis.py

All outputs logged to: logs/[pipeline]_[timestamp].log
Master log: logs/pipeline_master.log
```

***

## Scheduling Strategy

### Why This Schedule?

| Pipeline | Frequency | Time | Rationale |
|----------|-----------|------|-----------|
| **ACLED** | Weekly | Sunday 2 AM | ACLED data updates weekly; minimizes API load |
| **GNews** | Daily | 6 AM | News articles published continuously; daily capture ensures coverage |
| **Matching** | Weekly | Sunday 4 AM | Runs after ACLED completes; weekly batch processing is sufficient |
| **Sentiment** | Daily | 5 AM | Processes new articles from GNews; runs before morning traffic |

### Execution Order (Sundays)

```
2:00 AM  ──→  ACLED starts
2:15 AM  ──→  ACLED completes
4:00 AM  ──→  Matching starts (uses fresh ACLED data)
4:10 AM  ──→  Matching completes
5:00 AM  ──→  Sentiment starts (processes week's articles)
5:45 AM  ──→  Sentiment completes
6:00 AM  ──→  GNews starts (collects new day's articles)
6:20 AM  ──→  GNews completes
```

***

## Cron Schedule Configuration

### Crontab Format
```
# ┌───────────── minute (0 - 59)
# │ ┌───────────── hour (0 - 23)
# │ │ ┌───────────── day of month (1 - 31)
# │ │ │ ┌───────────── month (1 - 12)
# │ │ │ │ ┌───────────── day of week (0 - 6) (Sunday=0)
# │ │ │ │ │
# │ │ │ │ │
# * * * * * command
```

### Current Schedule

**File Location:** `/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/crontab`

```cron
# Run ACLED once a week on Sundays at 2 AM
0 2 * * 0 /home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/run-pipelines.sh acled

# Run GNews every day at 6 AM
0 6 * * * /home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/run-pipelines.sh gnews

# Run matching weekly on Sundays at 4 AM
0 4 * * 0 /home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/run-pipelines.sh matching

# Run sentiment daily at 5 AM
0 5 * * * /home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/run-pipelines.sh sentiment
```

***

## Wrapper Script: `run-pipelines.sh`

### Purpose
The bash wrapper script provides:
- **Error handling:** Continues execution even if one stage fails
- **Logging:** Separate logs per pipeline + master log
- **Flexibility:** Run individual pipelines or combinations
- **Status tracking:** Success/failure indicators with timestamps

### Usage

```bash
# Run individual pipelines
./run-pipelines.sh acled       # ACLED only
./run-pipelines.sh gnews       # GNews only
./run-pipelines.sh matching    # Matching only
./run-pipelines.sh sentiment   # Sentiment only

# Run everything sequentially
./run-pipelines.sh all         # All pipelines in order
```

### Script Structure

```bash
#!/bin/bash
set -e  # Exit on error

PROJECT_DIR="/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data"
LOG_DIR="$PROJECT_DIR/logs"
DATE=$(date +%Y%m%d_%H%M%S)

# Functions for each pipeline
run_acled() {
    python src/pipelineACLED.py >> "$LOG_DIR/acled_$DATE.log" 2>&1
}

# Main execution logic
case "$PIPELINE" in
    acled) run_acled ;;
    gnews) run_gnews ;;
    # ... etc
esac
```

### Log Output Example

```
===== Pipeline Run Started: 2026-01-20 02:00:00 =====
Mode: acled
Running ACLED pipeline...
✓ ACLED completed at Mon Jan 20 02:15:23 UTC 2026
===== Pipeline Run Completed: 2026-01-20 02:15:23 =====
```

***

## Supercronic Setup

### What is Supercronic?

Supercronic is a cron-compatible job scheduler designed for containerized environments and cloud deployments. Unlike traditional cron, it:
- Runs in the foreground (doesn't require system daemon)
- Provides better logging
- Handles timezone issues more reliably
- Works well in Docker containers

### Installation

```bash
# Download supercronic binary
SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.29/supercronic-linux-amd64
SUPERCRONIC=supercronic-linux-amd64

wget "$SUPERCRONIC_URL" -O "$SUPERCRONIC"
chmod +x "$SUPERCRONIC"
sudo mv "$SUPERCRONIC" /usr/local/bin/supercronic
```

### Running Supercronic

```bash
# Start supercronic with crontab file
./supercronic ./cronjob-flexibel


***

## Monitoring & Maintenance

### Check Pipeline Status

```bash
# View master log (all pipeline runs)
tail -f logs/pipeline_master.log

# View specific pipeline log
tail -f logs/acled_20260120_020000.log
tail -f logs/gnews_20260120_060000.log

# Check if pipelines are running
ps aux | grep pipelineACLED.py
ps aux | grep pipelineGNEWS.py

# Count recent successful runs
grep "âœ"" logs/pipeline_master.log | tail -20
```

### Database Health Checks

```bash
# Check database sizes
du -sh data/*.db

# Verify record counts
sqlite3 data/gnews_articles_from2023.db "SELECT COUNT(*) FROM articles;"
sqlite3 data/conflict_data.db "SELECT COUNT(*) FROM events;"
sqlite3 data/matched_conflict.db "SELECT COUNT(*) FROM match_country_wide;"

# Check last update time
sqlite3 data/gnews_articles_from2023.db \
  "SELECT MAX(publishedAt) FROM articles;"
```

***

## Performance Optimization

### Staggered Scheduling

If resource contention occurs, stagger pipeline execution:

```cron
# Option 1: Spread throughout the day
0 2 * * 0 run-pipelines.sh acled      # 2 AM Sunday
0 6 * * * run-pipelines.sh gnews      # 6 AM daily
0 14 * * 0 run-pipelines.sh matching  # 2 PM Sunday
0 18 * * * run-pipelines.sh sentiment # 6 PM daily

# Option 2: Run processing at night, collection in morning
0 1 * * 0 run-pipelines.sh acled      # 1 AM Sunday
0 8 * * * run-pipelines.sh gnews      # 8 AM daily
0 2 * * 0 run-pipelines.sh matching   # 2 AM Sunday
0 22 * * * run-pipelines.sh sentiment # 10 PM daily
```

### Resource Limits

Add resource constraints to prevent overload:

```bash
# In run-pipelines.sh, add before python calls:
ulimit -v 16000000  # Limit to 16GB RAM
nice -n 10 python src/pipelineGNEWS.py  # Lower CPU priority
```

***

## Quick Reference

### Common Commands

```bash
# Start automation
./supercronic ./cronjob-flexible

# Stop automation
pkill supercronic

# Run pipeline manually
./run-pipelines.sh [acled|gnews|matching|sentiment|all]

# Check status
tail -f logs/pipeline_master.log

# View schedule
cat crontab

# Test crontab syntax
supercronic -test cronjob-flexible
```

### Important Paths

| Path | Description |
|------|-------------|
| `/home/mlci_2025s1_group2/conflictmediamirror/dashboard_conflict_data/` | Project root |
| `crontab` | Supercronic schedule file |
| `run-pipelines.sh` | Pipeline wrapper script |
| `logs/pipeline_master.log` | Master execution log |
| `logs/[pipeline]_[timestamp].log` | Individual pipeline logs |
| `data/*.db` | SQLite databases |

***

