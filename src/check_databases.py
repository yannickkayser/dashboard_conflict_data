#!/usr/bin/env python3
# check_databases.py
#
# Quick database inspection script to check GNews and ACLED data pipeline outputs

import os
import sqlite3
from pathlib import Path
from typing import List, Tuple, Dict
from datetime import datetime


def get_data_dir() -> Path:
    """Get the data directory path"""
    base_dir = Path(__file__).resolve().parent
    data_dir = base_dir / ".." / "data"
    if not data_dir.exists():
        data_dir = base_dir / "data"
    return data_dir.resolve()


def check_database_exists(db_path: Path) -> bool:
    """Check if database file exists"""
    return db_path.exists()


def get_table_list(db_path: Path) -> List[str]:
    """Get list of tables in database"""
    if not db_path.exists():
        return []
    
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [row[0] for row in cur.fetchall()]
    conn.close()
    return tables


def get_table_info(db_path: Path, table: str) -> Dict:
    """Get detailed info about a table"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    
    info = {
        "count": 0,
        "columns": [],
        "sample_row": None,
        "date_range": None
    }
    
    try:
        # Row count
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        info["count"] = cur.fetchone()[0]
        
        # Column names
        cur.execute(f"PRAGMA table_info({table});")
        info["columns"] = [row[1] for row in cur.fetchall()]
        
        # Sample row (first row)
        if info["count"] > 0:
            cur.execute(f"SELECT * FROM {table} LIMIT 1;")
            info["sample_row"] = cur.fetchone()
        
        # Date range (if date columns exist)
        date_cols = [c for c in info["columns"] if 'date' in c.lower() or 'published' in c.lower()]
        if date_cols and info["count"] > 0:
            date_col = date_cols[0]
            try:
                cur.execute(f"SELECT MIN({date_col}), MAX({date_col}) FROM {table};")
                info["date_range"] = cur.fetchone()
            except:
                pass
    
    except Exception as e:
        info["error"] = str(e)
    
    conn.close()
    return info


def format_size(size_bytes: int) -> str:
    """Format file size in human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def print_separator(char="=", length=70):
    """Print a separator line"""
    print(char * length)


def check_gnews_databases():
    """Check GNews pipeline databases"""
    print_separator()
    print("GNEWS DATABASES")
    print_separator()
    
    data_dir = get_data_dir()
    
    # Database 1: Raw articles
    db1 = data_dir / "gnews_articles_from2023.db"
    print(f"\n1. Raw Articles Database")
    print(f"   Path: {db1}")
    
    if check_database_exists(db1):
        size = db1.stat().st_size
        print(f"   Size: {format_size(size)}")
        
        tables = get_table_list(db1)
        print(f"   Tables: {', '.join(tables) if tables else 'None'}")
        
        for table in tables:
            info = get_table_info(db1, table)
            print(f"\n   Table: {table}")
            print(f"   - Rows: {info['count']:,}")
            print(f"   - Columns ({len(info['columns'])}): {', '.join(info['columns'][:5])}")
            if len(info['columns']) > 5:
                print(f"                  {', '.join(info['columns'][5:])}")
            if info['date_range']:
                print(f"   - Date range: {info['date_range'][0]} to {info['date_range'][1]}")
    else:
        print("   ❌ Database not found")
    
    # Database 2: Processed articles
    db2 = data_dir / "deleted_dupgnews2023.db"
    print(f"\n2. Processed Articles Database")
    print(f"   Path: {db2}")
    
    if check_database_exists(db2):
        size = db2.stat().st_size
        print(f"   Size: {format_size(size)}")
        
        tables = get_table_list(db2)
        print(f"   Tables: {', '.join(tables) if tables else 'None'}")
        
        for table in tables:
            info = get_table_info(db2, table)
            print(f"\n   Table: {table}")
            print(f"   - Rows: {info['count']:,}")
            print(f"   - Columns ({len(info['columns'])}): {', '.join(info['columns'][:5])}")
            if len(info['columns']) > 5:
                print(f"                  {', '.join(info['columns'][5:])}")
            if info['date_range']:
                print(f"   - Date range: {info['date_range'][0]} to {info['date_range'][1]}")
            
            # Special stats for articles_eng
            if table == "articles_eng":
                conn = sqlite3.connect(str(db2))
                cur = conn.cursor()
                
                # Country distribution
                cur.execute("""
                    SELECT article_country, COUNT(*) as cnt
                    FROM articles_eng
                    WHERE article_country != 'NA'
                    GROUP BY article_country
                    ORDER BY cnt DESC
                    LIMIT 5;
                """)
                countries = cur.fetchall()
                if countries:
                    print(f"   - Top 5 countries:")
                    for country, cnt in countries:
                        print(f"     • {country}: {cnt:,} articles")
                
                # NA count
                cur.execute("SELECT COUNT(*) FROM articles_eng WHERE article_country = 'NA';")
                na_count = cur.fetchone()[0]
                print(f"   - Unclassified (NA): {na_count:,} articles")
                
                conn.close()
    else:
        print("   ❌ Database not found")


def check_acled_database():
    """Check ACLED conflict database"""
    print_separator()
    print("ACLED DATABASE")
    print_separator()
    
    data_dir = get_data_dir()
    db = data_dir / "conflict_data.db"
    
    print(f"\nConflict Data Database")
    print(f"Path: {db}")
    
    if check_database_exists(db):
        size = db.stat().st_size
        print(f"Size: {format_size(size)}")
        
        tables = get_table_list(db)
        print(f"Tables ({len(tables)}): {', '.join(tables[:10])}")
        if len(tables) > 10:
            print(f"         (and {len(tables) - 10} more...)")
        
        # Key tables to check
        key_tables = ["events", "unique_conflict", "conflict_features", "conflict_country"]
        
        for table in key_tables:
            if table in tables:
                info = get_table_info(db, table)
                print(f"\nTable: {table}")
                print(f"- Rows: {info['count']:,}")
                print(f"- Columns ({len(info['columns'])}): {', '.join(info['columns'][:8])}")
                if len(info['columns']) > 8:
                    print(f"           {', '.join(info['columns'][8:16])}")
                if info['date_range']:
                    print(f"- Date range: {info['date_range'][0]} to {info['date_range'][1]}")
                
                # Special stats per table
                conn = sqlite3.connect(str(db))
                cur = conn.cursor()
                
                if table == "events":
                    # Top countries by event count
                    cur.execute("""
                        SELECT country, COUNT(*) as cnt
                        FROM events
                        GROUP BY country
                        ORDER BY cnt DESC
                        LIMIT 5;
                    """)
                    countries = cur.fetchall()
                    if countries:
                        print(f"- Top 5 countries by events:")
                        for country, cnt in countries:
                            print(f"  • {country}: {cnt:,} events")
                
                elif table == "conflict_country":
                    # Top countries by conflicts
                    cur.execute("""
                        SELECT country, n_events, total_fatalities
                        FROM conflict_country
                        ORDER BY n_events DESC
                        LIMIT 5;
                    """)
                    countries = cur.fetchall()
                    if countries:
                        print(f"- Top 5 countries by conflicts:")
                        for country, events, fatalities in countries:
                            print(f"  • {country}: {events:,} events, {fatalities:,} fatalities")
                
                conn.close()
    else:
        print("❌ Database not found")


def check_pipeline_status():
    """Check overall pipeline status"""
    print_separator()
    print("PIPELINE STATUS SUMMARY")
    print_separator()
    
    data_dir = get_data_dir()
    
    status = {
        "GNews Raw": check_database_exists(data_dir / "gnews_articles_from2023.db"),
        "GNews Processed": check_database_exists(data_dir / "deleted_dupgnews2023.db"),
        "ACLED Conflict": check_database_exists(data_dir / "conflict_data.db")
    }
    
    print("\nDatabase Status:")
    for name, exists in status.items():
        status_icon = "✓" if exists else "❌"
        print(f"  {status_icon} {name}")
    
    # Check if processed tables exist
    if status["GNews Processed"]:
        db = data_dir / "deleted_dupgnews2023.db"
        tables = get_table_list(db)
        
        print("\nGNews Pipeline Stages:")
        print(f"  {'✓' if 'article_without_duplicates' in tables else '❌'} Deduplication completed")
        print(f"  {'✓' if 'articles_eng' in tables else '❌'} Translation completed")
        
        if 'articles_eng' in tables:
            conn = sqlite3.connect(str(db))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM articles_eng;")
            count = cur.fetchone()[0]
            print(f"\n  📊 Total processed articles: {count:,}")
            conn.close()
    
    if status["ACLED Conflict"]:
        db = data_dir / "conflict_data.db"
        tables = get_table_list(db)
        
        print("\nACLED Pipeline Stages:")
        print(f"  {'✓' if 'events' in tables else '❌'} Raw events loaded")
        print(f"  {'✓' if 'event_conflict' in tables else '❌'} Conflict mapping completed")
        print(f"  {'✓' if 'unique_conflict' in tables else '❌'} Unique conflicts created")
        print(f"  {'✓' if 'conflict_features' in tables else '❌'} Features computed")
        print(f"  {'✓' if 'conflict_country' in tables else '❌'} Country aggregation completed")
        
        if 'events' in tables:
            conn = sqlite3.connect(str(db))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM events;")
            events = cur.fetchone()[0]
            
            if 'unique_conflict' in tables:
                cur.execute("SELECT COUNT(*) FROM unique_conflict;")
                conflicts = cur.fetchone()[0]
                print(f"\n  📊 Total events: {events:,}")
                print(f"  📊 Total conflicts: {conflicts:,}")
            else:
                print(f"\n  📊 Total events: {events:,}")
            
            conn.close()


def main():
    """Main function"""
    print("\n")
    print_separator("=", 70)
    print("DATABASE INSPECTION TOOL")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print_separator("=", 70)
    
    data_dir = get_data_dir()
    print(f"\nData directory: {data_dir}")
    
    # Check all databases
    check_gnews_databases()
    print()
    check_acled_database()
    print()
    check_pipeline_status()
    
    print("\n")
    print_separator("=", 70)
    print("INSPECTION COMPLETE")
    print_separator("=", 70)
    print()


if __name__ == "__main__":
    main()