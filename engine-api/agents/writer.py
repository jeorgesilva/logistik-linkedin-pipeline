"""Agent 1: erzeugt den LinkedIn-Post (Argument → Rahmung → Überarbeitung → Korrektur)."""

import logging
import os

from agents import llm, rules
from agents.corrector import correct_german

logger = logging.getLogger(__name__)


POST_LANGUAGE = os.getenv("POST_LANGUAGE", "German")
MIN_POST_WORDS = int(os.getenv("MIN_POST_WORDS", "100"))
MAX_POST_WORDS = int(os.getenv("MAX_POST_WORDS", "180"))
# Bewusst kleiner als MIN_POST_WORDS: ein Ziel dieser Größe nur für den Argument-Block
# (Schritt 3a) lässt sich per Retry realistisch garantieren — anders als 100+ kohärente
# Wörter für den ganzen Post in einem Durchgang zu verlangen.
MIN_ARGUMENT_WORDS = int(os.getenv("MIN_ARGUMENT_WORDS", "70"))

# Schritt 3a — nur das Argument (den inhaltlichen Kern) entwerfen, bevor Hook/Pivot/Abschluss
# existieren. Von einem Modell zu verlangen, ein Längenziel über einen ganzen 6-teiligen
# strukturierten Post in einem Durchgang zu treffen, ist unzuverlässig; ein kleineres Ziel nur
# für diesen einen Absatz ist realistisch und prüfbar — der Rahmen (Schritt 3b) fügt weitere
# Wörter hinzu.
_ARGUMENT_SYSTEM = """\
Du bist ein erfahrener LinkedIn-Ghostwriter für Frachtwerk — KI-gestützte Dispositionsautomatisierung für Speditionen im deutschen Mittelstand.

""" + rules.WRITER_READER_PROFILE + """

AUFGABE: Schreibe NUR den Kern-Absatz eines LinkedIn-Posts — das Argument. Noch KEIN Hook, kein Pivot, kein Abschluss, keine Hashtags. Diese Reihenfolge ist Absicht: das Argument ist der eigentliche Inhalt; Hook und Abschluss werden erst danach um das fertige Argument herum gebaut.

DEIN JOB IST NICHT, DAS EREIGNIS ZUSAMMENZUFASSEN. Dein Job ist, eine Position dazu zu beziehen — ausgehend von der mitgelieferten Überzeugung (applied_belief), falls vorhanden. Ein Absatz, der auch von jedem anderen Logistik-SaaS stammen könnte, hat noch keine Position bezogen.

STRUKTUR:
1. Argument, 2–4 Sätze Fließtext, KEINE Bullet-Points: entwickle WARUM das für eine Spedition wichtig ist. Nutze contested_question und applied_belief als Gerüst.
2. Ein konkreter Beleg oder operatives Detail aus dem Artikel, das die Aussage verdient statt nur behauptet.
3. Optional, nur wenn es sich aus dem Argument zwingend ergibt: Frachtwerk als praktische Konsequenz, höchstens 1 Satz, NIE als Prämisse. "Frachtwerk hilft dabei..." ohne vorheriges Argument ist verboten.

LÄNGE: mindestens """ + str(MIN_ARGUMENT_WORDS) + """ Wörter. Ein Argument, das nicht entwickelt wird, ist kein akzeptables Ergebnis — jedes Thema lässt sich auf diese Länge entwickeln, ohne die Kohärenz zu verlieren.

TON: direkt, sachkundig, dringlich, unprätentiös. Kurze Sätze für Gewicht. Konkret vor abstrakt.

""" + rules.WRITER_THIRD_PARTY_RULE + """

""" + rules.FORBIDDEN_PHRASES_BLOCK + """

Antworte NUR als JSON: {"argument_text": "..."}."""

# Schritt 3b — Hook/Pivot/Abschluss um das bereits geschriebene Argument bauen. Das Argument
# wird als fester Text übergeben, und das Modell wird angewiesen, es nicht anzufassen; der
# finale post_text wird in Python zusammengesetzt (_assemble_post), nicht hier — so erreicht
# das validierte Argument den finalen Post garantiert wortgleich.
_FRAME_SYSTEM = """\
Du bist ein erfahrener LinkedIn-Ghostwriter für Frachtwerk — KI-gestützte Dispositionsautomatisierung für Speditionen im deutschen Mittelstand.

""" + rules.WRITER_READER_PROFILE + """

AUFGABE: Ein Kern-Argument für den Post ist BEREITS geschrieben (unten, unter "BEREITS GESCHRIEBENES ARGUMENT"). Deine Aufgabe ist NICHT, es umzuschreiben, zu kürzen oder zusammenzufassen — sondern einen Hook davor und einen Abschluss danach zu liefern, die natürlich in das Argument hineinführen bzw. daraus folgen.

TEILE, die du lieferst:
1. Hook (1 Zeile): das reale Ereignis, konkret — Datum, Zahl oder umgedeutetes Ereignis. Max 12 Wörter. Noch keine Frachtwerk-Nennung.
2. Pivot (1 Satz): "Die meisten sehen darin X. Für eine Spedition ist es Y." — oder sinngemäß. Muss inhaltlich direkt zum Argument unten hinführen.
3. Abschluss: Call-to-Action ODER offene Frage ODER ein prägnanter Aphorismus. Muss inhaltlich aus dem Argument folgen, nicht generisch sein.
4. 5–7 Hashtags, thematisch zum Artikel und Argument passend (mit "#").

FORMAT: Ich-Perspektive (institutionelles "wir"); kein Clickbait.

""" + rules.WRITER_THIRD_PARTY_RULE + """

""" + rules.FORBIDDEN_PHRASES_BLOCK + """

Antworte NUR als JSON: {"hook": "...", "pivot": "...", "closing": "...", "hashtags": ["#...", "..."]}."""

# Schritt 3c — echter Überarbeitungsdurchgang, kein blinder Retry. Python kennt vor diesem
# Aufruf bereits die exakte Wortzahl und hat nach verbotenen Phrasen gesucht — dem Modell
# werden diese Fakten mitgeteilt, statt es selbst urteilen zu lassen; seine eigentliche
# Aufgabe ist das Einzige, was Python nicht prüfen kann: ob Hook+Pivot (separat in 3b
# geschrieben) wirklich zum Argument führen oder angeklebt wirken.
_REVISE_SYSTEM = """\
Du bist Redakteur/Lektor für Frachtwerk-LinkedIn-Posts. Du bekommst einen fertig zusammengesetzten Post und prüfst ihn kritisch, bevor er freigegeben wird — wie ein Autor, der seinen Text am Ende noch einmal ganz liest, bevor er ihn abgibt.

PRÜFE:
1. KOHÄRENZ: Führen Hook und Pivot (die ersten 1-2 Zeilen) inhaltlich und logisch zum Argument, das danach kommt? Wirkt der Übergang angeklebt/unpassend, oder wie aus einem Guss?
2. LÄNGE: wird dir unten als Zahl mitgeteilt — zähle nicht selbst nach.
3. VERBOTENE PHRASEN: werden dir unten mitgeteilt, falls welche gefunden wurden.

Wenn du Probleme findest, korrigiere NUR das Nötige — behalte den Rest des Posts unverändert. Wenn der Post zu kurz ist, entwickle das Argument mit einem zusätzlichen konkreten Satz weiter (erfinde keine neuen Fakten, nutze nur was im mitgelieferten Artikeltext steht). Wenn eine verbotene Phrase gefunden wurde, entferne oder ersetze sie. Wenn nichts zu beanstanden ist, gib den Post EXAKT unverändert zurück.

Antworte NUR als JSON: {"post_text": "...", "changed": true, "reason": "kurze Begründung auf Deutsch, was ggf. korrigiert wurde, sonst leerer String"}."""


def _build_kb_context(article: dict) -> str:
    """Holt passende Beispiele und Guidelines aus der KB und formatiert sie für den Prompt."""
    try:
        from kb import retrieve_for_article
        query = f"{article.get('title', '')} {article.get('full_text', '')[:300]}"
        context = retrieve_for_article(query)
        parts: list[str] = []
        if context.get("guidelines"):
            parts.append("=== RICHTLINIEN AUS DEM STYLE GUIDE ===")
            for g in context["guidelines"]:
                label = g.get("section") or g.get("source", "")
                parts.append(f"[{label}]\n{g['text']}")
        if context.get("rejected"):
            parts.append("=== ABGELEHNTE POSTS — DIESES MUSTER VERMEIDEN ===")
            for i, rex in enumerate(context["rejected"], 1):
                parts.append(
                    f"Abgelehntes Beispiel {i} — {rex['title']} "
                    f"(Ablehnungsgrund: {rex['reason']}):\n{rex['post_text']}"
                )
        if context.get("examples"):
            parts.append("=== BEISPIELE GENEHMIGTER POSTS ===")
            for i, ex in enumerate(context["examples"], 1):
                parts.append(f"Beispiel {i} — {ex['title']}:\n{ex['post_text']}")
        if parts:
            return "\n\n".join(parts) + "\n\n"
    except Exception as e:
        logger.debug("KB-Kontext übersprungen: %s", e)
    return ""


def _format_bridge_context(bridge: dict | None) -> str:
    if not bridge:
        return ""
    belief = bridge.get("applied_belief", "") or "(keine — rein informativ behandeln, kein Argument erzwingen)"
    return (
        "=== NARRATIVE BRÜCKE — als Gerüst des Arguments verwenden ===\n"
        f"Was passiert ist: {bridge.get('what_happened', '')}\n"
        f"Umstrittene Frage/Annahme im Markt: {bridge.get('contested_question', '')}\n"
        f"Angewandte Frachtwerk-Überzeugung: {belief}\n"
        f"Konkreter Impact für eine Spedition: {bridge.get('impact_for_forwarders', '')}\n"
        f"Risiko / Chance: {bridge.get('risk_or_opportunity', '')}\n"
        f"Frachtwerk-Verbindung: {bridge.get('frachtwerk_connection', '')}\n\n"
    )


def _draft_argument(article: dict, bridge: dict | None, kb_context: str) -> str:
    """Schritt 3a: entwirft nur den Argument-Absatz — den inhaltlichen Kern.

    Ein zu kurzer erster Versuch bekommt einen *gezielten* Retry (vorheriger Entwurf +
    seine tatsächliche Wortzahl werden an das Modell zurückgegeben), statt denselben
    Prompt blind erneut zu senden — ein blinder Retry hat keinen Grund, sich nach oben
    anzunähern, und kann sich sogar verschlechtern.
    """
    base_user_msg = (
        f"{kb_context}"
        f"{_format_bridge_context(bridge)}"
        f"Artikeltitel: {article.get('title', '')}\n\n"
        f"Artikeltext:\n{article.get('full_text', '')[:3000]}"
    )
    user_msg = base_user_msg
    # 3, nicht 2: der gezielte Retry (unten) nähert sich dem Ziel in sichtbaren Schritten,
    # sobald das Modell echtes Feedback bekommt, aber ein Retry reicht nicht immer — in
    # echten Läufen stieg die Wortzahl in einem Retry von 45->62 und 39->60, also genau
    # einen Versuch unter MIN_ARGUMENT_WORDS.
    for attempt in range(3):
        try:
            raw = llm.chat(llm.WRITER_MODEL, _ARGUMENT_SYSTEM, user_msg)
            value = llm.extract_json(raw, "argument_text")
            if value:
                word_count = len(value.split())
                if word_count < MIN_ARGUMENT_WORDS:
                    logger.warning(
                        "Argumentation zu kurz (%d Wörter, min. %d) — Versuch %d, neuer Versuch: %s",
                        word_count, MIN_ARGUMENT_WORDS, attempt + 1, value[:100],
                    )
                    user_msg = (
                        f"{base_user_msg}\n\n"
                        f"DEIN LETZTER VERSUCH HATTE NUR {word_count} WÖRTER (Ziel: mindestens "
                        f"{MIN_ARGUMENT_WORDS}):\n\"{value}\"\n\n"
                        f"Entwickle DENSELBEN Gedanken weiter — ein zusätzlicher konkreter Satz "
                        f"mit einem Detail, das WÖRTLICH im Artikeltext oben steht. Nicht neu "
                        f"anfangen, nicht zusammenfassen, sondern vertiefen. Erfinde KEINE neuen "
                        f"Fakten, Namen, Orte oder Zahlen, die nicht im Artikeltext stehen — wenn "
                        f"der Artikel kein weiteres konkretes Detail hergibt, vertiefe stattdessen "
                        f"die Argumentation selbst (z. B. eine zusätzliche Konsequenz oder ein "
                        f"Trade-off), ohne Fakten zu erfinden."
                    )
                    continue
                return value
            logger.warning("Argumentation: Parsing fehlgeschlagen (Versuch %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Argumentation: Fehler beim Entwurf (Versuch %d): %s", attempt + 1, e)
    return ""


def _frame_post(article: dict, bridge: dict | None, argument_text: str, kb_context: str) -> dict | None:
    """Schritt 3b: baut Hook/Pivot/Abschluss/Hashtags um das fertige Argument."""
    user_msg = (
        f"{kb_context}"
        f"{_format_bridge_context(bridge)}"
        f"Artikeltitel: {article.get('title', '')}\n\n"
        f"=== BEREITS GESCHRIEBENES ARGUMENT (nicht verändern) ===\n{argument_text}"
    )
    required = ["hook", "pivot", "closing", "hashtags"]
    for attempt in range(2):
        try:
            raw = llm.chat(llm.WRITER_MODEL, _FRAME_SYSTEM, user_msg)
            data = llm.parse_json_obj(raw, required)
            if data is not None:
                return data
            logger.warning("Rahmung: Parsing fehlgeschlagen (Versuch %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Rahmung: Fehler (Versuch %d): %s", attempt + 1, e)
    return None


def _assemble_post(frame: dict, argument_text: str) -> str:
    hashtags = frame.get("hashtags") or []
    hashtags_line = " ".join(h if str(h).startswith("#") else f"#{h}" for h in hashtags)
    parts = [
        str(frame.get("hook", "")).strip(),
        str(frame.get("pivot", "")).strip(),
        argument_text.strip(),
        str(frame.get("closing", "")).strip(),
    ]
    body = "\n\n".join(p for p in parts if p)
    return f"{body}\n\n{hashtags_line}".strip() if hashtags_line else body


def _revise_post(article: dict, post_text: str) -> dict | None:
    """Schritt 3c: echter Überarbeitungsdurchgang — Kohärenz, Länge, verbotene Phrasen.

    Python berechnet vorab die Wortzahl, sucht nach verbotenen Phrasen und übergibt beides
    dem Modell als Fakten, statt es selbst urteilen zu lassen. Prüft außerdem die eigene
    Korrektur: Ist nach einer Überarbeitung noch eine verbotene Phrase vorhanden, wird das
    in einem zweiten Versuch zurückgemeldet, statt der Befolgung blind zu vertrauen — ein
    Modell, das eine Phrase entfernen soll, tut das nicht garantiert.
    """
    current_text = post_text
    last_result: dict | None = None
    required = ["post_text", "changed"]
    for attempt in range(2):
        word_count = len(current_text.split())
        found = rules.find_forbidden_phrases(current_text)
        if attempt > 0 and not found and word_count >= MIN_POST_WORDS:
            break
        user_msg = (
            f"AKTUELLE LÄNGE: {word_count} Wörter (Ziel: {MIN_POST_WORDS}-{MAX_POST_WORDS} Wörter)\n"
            f"GEFUNDENE VERBOTENE PHRASEN: {', '.join(found) if found else '(keine)'}\n\n"
            f"Artikeltext (nur zur Faktenprüfung, falls Entwicklung nötig):\n{article.get('full_text', '')[:2000]}\n\n"
            f"=== POST ===\n{current_text}"
        )
        try:
            raw = llm.chat(llm.WRITER_MODEL, _REVISE_SYSTEM, user_msg, temperature=0.3)
            data = llm.parse_json_obj(raw, required)
            if data is not None and data.get("post_text"):
                last_result = data
                current_text = data["post_text"]
                still_found = rules.find_forbidden_phrases(current_text)
                if not still_found:
                    return data
                logger.warning(
                    "Überarbeitung hat verbotene Formulierung(en) stehen lassen (Versuch %d): %s",
                    attempt + 1, still_found,
                )
                continue
            logger.warning("Überarbeitung: Parsing fehlgeschlagen (Versuch %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Überarbeitung: Fehler (Versuch %d): %s", attempt + 1, e)
    return last_result


def generate_post(article: dict, bridge: dict | None = None) -> str:
    """Agent 1: erzeugt einen deutschen LinkedIn-Post zum Artikel.

    Dreistufiger Ablauf, wie ein Mensch einen argumentativen Text entwirft: zuerst das
    Argument (Schritt 3a, der inhaltliche Kern), dann Hook/Pivot/Abschluss um das fertige
    Argument (Schritt 3b), schließlich prüft ein echter Überarbeitungsdurchgang Kohärenz,
    Länge und verbotene Phrasen vor der Rückgabe (Schritt 3c) — statt den ganzen
    strukturierten Post in einem Durchgang mit blindem Längen-Retry zu erzeugen. Optional
    korrigiert danach ein deutsches Korrekturmodell Grammatik und Zeichensetzung (Schritt 3d).
    """
    title = article.get("title", "")[:60]
    kb_context = _build_kb_context(article)

    argument_text = _draft_argument(article, bridge, kb_context)
    if not argument_text:
        logger.warning("Agent 1: Entwurf der Argumentation fehlgeschlagen für '%s'", title)
        return ""

    frame = _frame_post(article, bridge, argument_text, kb_context)
    if not frame:
        logger.warning("Agent 1: Rahmung fehlgeschlagen für '%s'", title)
        return ""

    post_text = _assemble_post(frame, argument_text)

    revision = _revise_post(article, post_text)
    if revision is not None:
        post_text = revision.get("post_text") or post_text
        if revision.get("changed"):
            logger.info("Agent 1: Überarbeitung hat den Post angepasst — %s", revision.get("reason", "")[:120])
    else:
        logger.warning("Agent 1: Überarbeitung fehlgeschlagen für '%s' — unüberarbeiteter Entwurf wird verwendet", title)

    post_text = correct_german(post_text)

    word_count = len(post_text.split())
    if word_count < MIN_POST_WORDS:
        logger.warning(
            "Agent 1: Post nach der Überarbeitung immer noch unter %d Wörtern (%d) — '%s' wird verworfen",
            MIN_POST_WORDS, word_count, title,
        )
        return ""
    return post_text
