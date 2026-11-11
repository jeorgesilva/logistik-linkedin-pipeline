"""LLM-Agenten der Pipeline.

- llm.py        — gemeinsame Ollama-Aufrufe und JSON-Parsing
- rules.py      — gemeinsame Prompt-Bausteine (Überzeugungsbank, verbotene Phrasen, Drittfirmen-Regeln)
- bridge.py     — Relevanzprüfung und narrative Brücke (Schritte 1–2)
- writer.py     — Post-Erzeugung (Agent 1, Schritte 3a–3c)
- corrector.py  — optionale Korrektur mit dem deutschen Korrekturmodell (Schritt 3d)
- image.py      — Bildprompt (Agent 2)
- moderation.py — Werbeprüfung (Ton gegenüber Drittunternehmen)
"""
