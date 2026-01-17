# chatbot_demo.py
from __future__ import annotations

from pathlib import Path
import re
import sqlite3
from typing import List, Tuple

import streamlit as st
from openai import OpenAI

# ---- Config ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # adjust if needed
DB_PATH = PROJECT_ROOT / "data" / "conflict_data.db"         # <-- CHANGE to your real path
MODEL_NAME = "gpt-4o-mini"

K_RETRIEVE = 60              # how many notes to retrieve
MAX_CONTEXT_CHARS = 12000    # hard cap on text sent to LLM
MAX_OUTPUT_TOKENS = 450

# ---- OpenAI client ----
# Put OPENAI_API_KEY into .streamlit/secrets.toml (recommended)
# [general]
# OPENAI_API_KEY="sk-..."
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])


@st.cache_resource
def get_connection() -> sqlite3.Connection:
    # check_same_thread=False is needed for Streamlit multi-threading
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_data
def get_countries() -> List[str]:
    con = get_connection()
    rows = con.execute("SELECT DISTINCT country FROM events ORDER BY country;").fetchall()
    return [r[0] for r in rows if r[0]]


def sanitize_fts_query(user_text: str) -> str:
    """
    Build a robust FTS5 MATCH query:
    - tokenizes to simple words
    - OR-joins tokens (broad match)
    - adds prefix '*' for longer tokens to increase recall (helps 'demonstrat*')
    """
    user_text = user_text.replace('"', " ").replace("'", " ")
    tokens = re.findall(r"[A-Za-z0-9_]+", user_text)
    tokens = [t.lower() for t in tokens if t.strip()]

    if not tokens:
        return ""

    # Cap tokens to keep queries sane
    tokens = tokens[:20]

    # Prefix-match longer tokens for better recall
    def maybe_prefix(t: str) -> str:
        return f"{t}*" if len(t) >= 6 else t

    tokens = [maybe_prefix(t) for t in tokens]
    return " OR ".join(tokens)


def retrieve_notes(country: str, question: str, k: int = K_RETRIEVE) -> List[Tuple[str, str, str]]:
    """
    Returns list of (event_id_cnty, event_date, notes) from DB using FTS5.
    Uses events_fts linked by rowid -> events.rowid.
    """
    match_q = sanitize_fts_query(question)
    if not match_q:
        return []

    con = get_connection()
    sql = """
        SELECT e.event_id_cnty, e.event_date, e.notes
        FROM events_fts
        JOIN events e ON e.rowid = events_fts.rowid
        WHERE events_fts.country = ?
          AND events_fts MATCH ?
        ORDER BY bm25(events_fts)
        LIMIT ?;
    """
    return con.execute(sql, (country, match_q, k)).fetchall()


def build_context(rows: List[Tuple[str, str, str]], max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """
    Construct a compact context block with citations.
    """
    parts = []
    total = 0
    for event_id, event_date, notes in rows:
        if not notes:
            continue
        piece = f"[event_id_cnty={event_id} | date={event_date}] {notes.strip()}\n"
        if total + len(piece) > max_chars:
            break
        parts.append(piece)
        total += len(piece)
    return "".join(parts)


def ask_llm(country: str, question: str, rows: List[Tuple[str, str, str]]) -> str:
    context = build_context(rows)

    system = (
        "You are a conflict dashboard assistant.\n"
        "Answer ONLY using the provided event notes as factual evidence.\n"
        "Do NOT use external knowledge, background facts, or assumptions.\n"
        "If the notes are insufficient, say so explicitly and suggest how to refine the question.\n"
        "When making factual claims, cite event_id_cnty values from the notes.\n"
        "Keep the answer clear and concise.\n"
    )

    user = (
        f"Country: {country}\n"
        f"Question: {question}\n\n"
        f"Event notes:\n{context}"
    )

    resp = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    return resp.choices[0].message.content


# ------------------ Streamlit UI ------------------
st.set_page_config(page_title="Chatbot Demo", layout="wide")
st.title("🧠 Country Conflict Chatbot (Demo)")

# Country selection (later you will replace this with your existing selection variable)
countries = get_countries()
if not countries:
    st.error("No countries found in events table. Check DB path and schema.")
    st.stop()

country = st.selectbox("Select a country", countries, index=0)

# Session state for chat
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []

# Render history
for m in st.session_state.chat_messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

prompt = st.chat_input("Ask what is going on in this country…")

if prompt:
    # show user message
    st.session_state.chat_messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # retrieve + answer
    rows = retrieve_notes(country, prompt, k=K_RETRIEVE)

    with st.chat_message("assistant"):
        if not rows:
            out = (
                "I couldn't find matching event notes for that question in the database.\n\n"
                "Try:\n"
                "- using different keywords (actors, locations, event types)\n"
                "- asking about a specific time period\n"
                "- rephrasing with simpler terms"
            )
            st.markdown(out)
        else:
            out = ask_llm(country, prompt, rows)
            st.markdown(out)

            with st.expander("Sources used (top matches)"):
                for event_id, event_date, _ in rows[:15]:
                    st.write(f"- event_id_cnty={event_id}, date={event_date}")

    st.session_state.chat_messages.append({"role": "assistant", "content": out})
