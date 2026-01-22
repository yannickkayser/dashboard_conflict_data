# CONFLICT–NEWS DASHBOARD

How accurately and comprehensively does German media reflect the global landscape of protests and conflicts, and what systematic biases or emotional framings emerge when news coverage is compared to real-world conflict data?

This project builds a **data pipeline and analytical dashboard** to connect **country-level conflict data** with **German-language news coverage**, enabling systematic study of visibility, bias, and emotional framing in reporting on protests, conflicts, and political violence.

***

## Project Overview

The workflow starts from **ACLED**, one of the most comprehensive open datasets on global conflict events, providing a “ground truth” of what happens: each protest, battle, or political violence incident is recorded with location, date, actors, and event type. On the media side, the project collects German-language news from **GNews** (and can be extended with other outlets), querying by country, keyword, and time window to retrieve headlines, article texts, timestamps, and outlet metadata.

A central matching layer links **country-level conflicts** to **media articles** using geographic and temporal proximity. Once conflicts and articles are connected, NLP components perform sentiment and emotion analysis, topic and keyword extraction, and article clustering to characterize how conflicts are framed and which conflicts receive coverage at all.

All processed data feeds into an interactive dashboard that allows users to:

- Compare **conflict intensity vs. media coverage** on a global map.
- Inspect **timelines of attention** to specific conflicts, regions, or actors.
- Filter by **conflict type**, region, outlet, or time period.
- Explore **keyword and emotion maps** that trace dominant narrative tones and emotional framings.

The dashboard is designed as a **living system**: the pipeline can be scheduled, so new ACLED events and news articles are continuously integrated, keeping the analytical view up to date.

***

## Repository Structure

Use this section as the main entry point to the technical documentation and operations.


| File | Description | When to use |
| :-- | :-- | :-- |
| `PIPELINE_GUIDE.md` | High-level architecture and end-to-end pipeline description (ACLED + GNews + matching + NLP). | To understand how all components fit together conceptually before changing code or adding data. |
| `DATABASE_SCHEMA.md` | Full schema reference for all SQLite databases produced by the pipeline (GNews, ACLED, matching outputs). | When writing custom SQL, building visualizations, or checking how a specific field is defined. |
| `QUICK_START.md` | Operational checklist for running the pipeline (commands, order of execution, basic validation queries). | For day-to-day use: running or re-running the full pipeline or onboarding collaborators. |
| `TROUBLESHOOTING.md` | Common errors, performance issues, and optimization strategies (APIs, GPU/CPU limits, DB locks, memory). | When a stage fails, is too slow, or when moving to different hardware. |
| `AUTOMATION.md` | Scheduled execution with supercronic, monitoring, and alerting setup. | For understanding/modifying the automated scheduling or troubleshooting cron jobs. |


***

## Data Sources

- **ACLED conflict data** (`conflict_data.db`):
Event-level records of protests, riots, battles, and political violence, aggregated into higher-level conflicts and country-level indicators.
- **German-language news** (`gnews_articles_from2023.db`, `deleted_dupgnews2023.db`):
Articles fetched via GNews (and Tagesschau API where available), deduplicated, translated to English, and enriched with country labels and NLP features.
- **Matching outputs** (`matched_conflict.db`):
Tables that link articles to countries and specific conflicts, with temporal lag information and conflict metadata for analysis of coverage, bias, and attention.

***

## Pipeline and Dashboard Logic

1. **Collect conflict events** from ACLED as empirical baseline (what happened, where, and when).
2. **Collect news articles** from German-language outlets over matching time windows and relevant keywords (what was reported).
3. **Clean and normalize** media data (deduplication, translation, country classification).
4. **Enrich articles** with NLP features: sentiment/emotion, event-type proxies, topics, and clusters.
5. **Match events to coverage** using country and temporal alignment, creating article–country tables.
6. **Feed outputs into the dashboard**, enabling map, timeline, and narrative/framing analyses that surface omissions, biases, and emotional geographies in German media.

***

## How to Start

For a practical entry point:

1. Read [**`PIPELINE_GUIDE.md`**](docs/PIPELINE_GUIDE.md) for the conceptual model of the system.
2. Use [**`QUICK_START.md`**](docs/QUICK_START.md) to run the full pipeline once and generate all databases.
3. Refer to [**`DATABASE_SCHEMA.md`**](docs/DATABASE_SCHEMA.md) when constructing your first analytical queries (e.g., coverage vs. fatalities by country).
4. Keep [**`TROUBLESHOOTING.md`**](docs/TROUBLESHOOTING.md) nearby when scaling up, moving to GPU, or encountering API/database issues.
5. Look at [**`AUTOMATION.md`**](docs/AUTOMATION.md) for the constant fetching and updating of the pipeline to ensure that the dashboard shows the newest possible data.




