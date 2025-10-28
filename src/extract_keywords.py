# extract_keywords.py 
# Script for extracting keywords

#################################
# import packages 

import json
import time
import os
import spacy
from spacy.lang.en.stop_words import STOP_WORDS
import sqlite3
from utils import load_config, init_logger


# Load English NLP model
nlp = spacy.load("en_core_web_sm")

# LOG 
log = init_logger("KEYWORD-EXTRACTION")

##################################
# 1. Extract Entities
def extract_entities(text):
    """Extract key entities with normalization."""
    doc = nlp(text)
    entities = set()
    for ent in doc.ents:
        if ent.label_ in ["GPE", "LOC", "ORG", "PERSON", "DATE", "NORP"]:
            entities.add((ent.label_, ent.text.strip()))
    return list(entities)

##################################
# 2. Extract Entities
def extract_keywords(text):
    doc = nlp(text)
    keywords = set()

    # Noun phrases (e.g. "health workers", "public hospital", "legislative elections")
    for chunk in doc.noun_chunks:
        phrase = chunk.text.lower().strip()
        if len(phrase.split()) > 1 and not any(w in STOP_WORDS for w in phrase.split()):
            keywords.add(phrase)

    # Add verbs for action context (e.g. "demonstrated", "marched", "organized")
    for token in doc:
        if token.pos_ == "VERB" and token.lemma_ not in STOP_WORDS:
            keywords.add(token.lemma_)

    return list(keywords)

##################################
# 3. Extract ALL Infor
def extract_information(text):
    entities = extract_entities(text)
    keywords = extract_keywords(text)

    return {
        "entities": entities,
        "keywords": keywords
    }

##################################
# 4. Add infos to the database
def load_keywords_database(country, db_path):
    """Extract keywords/entities from event notes and insert into database."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("""
        SELECT event_id_cnty, notes
        FROM events
        WHERE country = :country
        AND (keywords IS NULL OR entities IS NULL)
    """, {"country": country})

    rows = c.fetchall()

    log.info(f"Found {len(rows)} events in {country} missing keyword/entity info.")

    for event_id_cnty, notes in rows:
        if not notes:
            continue

        info = extract_information(notes)
        entities_json = json.dumps(info["entities"], ensure_ascii=False)
        keywords_json = json.dumps(info["keywords"], ensure_ascii=False)

        c.execute('''
            UPDATE events
            SET entities = :entities,
                keywords = :keywords
            WHERE event_id_cnty = :event_id_cnty
        ''', {
            "event_id_cnty": event_id_cnty,
            "entities": entities_json,
            "keywords": keywords_json
        })
        log.info(f"Found Entities and Keywords in the event #{event_id_cnty}")

        conn.commit()

    conn.close()
    log.info("Database updated with keywords and entities.")