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
nlp = spacy.load("en_core_web_trf")

def extract_entities(text):
    """Extract key entities with normalization."""
    doc = nlp(text)
    entities = set()
    for ent in doc.ents:
        if ent.label_ in ["GPE", "LOC", "ORG", "PERSON", "DATE", "NORP"]:
            entities.add((ent.label_, ent.text.strip()))
    return list(entities)


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

def extract_information(text):
    entities = extract_entities(text)
    keywords = extract_keywords(text)

    return {
        "entities": entities,
        "keywords": keywords
    }