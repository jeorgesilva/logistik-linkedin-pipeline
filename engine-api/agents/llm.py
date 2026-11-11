"""Gemeinsame Ollama-Aufrufe und JSON-Parsing für alle Agenten."""

import json
import os
import re

import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
WRITER_MODEL = os.getenv("OLLAMA_WRITER_MODEL", "qwen2.5:14b-instruct")
TIMEOUT = int(os.getenv("OLLAMA_REQUEST_TIMEOUT", "600"))


def extract_json(text: str, key: str) -> str | None:
    """Versucht, {key: value} aus möglicherweise unsauberer LLM-Ausgabe zu extrahieren."""
    try:
        data = json.loads(text)
        if key in data:
            return data[key]
    except json.JSONDecodeError:
        pass

    # Fallback: JSON-Objekt im Text suchen
    match = re.search(r'\{[^{}]*"' + re.escape(key) + r'"[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            return data.get(key)
        except json.JSONDecodeError:
            pass

    return None


def parse_json_obj(text: str, required_keys: list) -> dict | None:
    """Parst ein JSON-Objekt aus der LLM-Ausgabe und prüft, ob alle Pflichtschlüssel vorhanden sind."""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and all(k in data for k in required_keys):
            return data
    except json.JSONDecodeError:
        pass
    # Fallback: äußerstes JSON-Objekt suchen (fängt Markdown-Codeblöcke und Vorreden ab)
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, dict) and all(k in data for k in required_keys):
                return data
        except json.JSONDecodeError:
            pass
    return None


def chat(model: str, system: str, user: str, temperature: float = 0.6) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": temperature},
    }
    resp = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json=payload,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def chat_text(host: str, model: str, system: str, user: str, temperature: float = 0.2) -> str:
    """Wie chat, aber ohne JSON-Modus — für Modelle, die reinen Text zurückgeben."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": temperature},
    }
    resp = requests.post(f"{host}/api/chat", json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()["message"]["content"]
