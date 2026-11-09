import json
import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
WRITER_MODEL = os.getenv("OLLAMA_WRITER_MODEL", "qwen2.5:14b-instruct")
IMAGE_PROMPT_MODEL = os.getenv("OLLAMA_IMAGE_PROMPT_MODEL", "llama3.2:3b")
TIMEOUT = int(os.getenv("OLLAMA_REQUEST_TIMEOUT", "600"))
POST_LANGUAGE = os.getenv("POST_LANGUAGE", "German")
MIN_POST_WORDS = int(os.getenv("MIN_POST_WORDS", "100"))
MAX_POST_WORDS = int(os.getenv("MAX_POST_WORDS", "180"))
# Deliberately smaller than MIN_POST_WORDS: a target this size for just the argument
# chunk (Etapa 3a) is realistic to guarantee via retry, unlike asking for 100+ coherent
# words across the whole post in one shot.
MIN_ARGUMENT_WORDS = int(os.getenv("MIN_ARGUMENT_WORDS", "70"))

# Condensed from data/style_guide/06_point_of_view.md — embedded directly in the
# bridge/writer prompts instead of left to RAG retrieval. RAG only surfaces 2 of 39
# guideline chunks per call, so a foundational, always-needed asset like the belief
# bank was showing up unreliably; posts came out as fact + generic "LogAid hilft
# dabei..." tack-on instead of an actual argued position. Keep this in sync with
# 06_point_of_view.md if the belief bank changes.
_POV_BELIEFS = """\
1. Kommunikation ist die teuerste, unsichtbarste Kostenstelle in der Logistik — nicht Visibility-Dashboards oder Routenoptimierung.
2. Eine KI, die nur Vorschläge macht, ist keine Automatisierung — sie ist ein weiterer Klick, keine Entlastung.
3. TMS und Telematik dokumentieren die Kette. Entschieden wird im Postfach, nicht im Dashboard.
4. Ein niedriger Preis eines Dienstleisters ist meist Symptom für Substanzverbrennung, nicht ein Verhandlungserfolg.
5. Datenhoheit ist operatives Risikomanagement, keine politische Position — dieselbe Frage wie bei jedem kritischen Lieferanten.
6. Generische KI versteht Sprache, nicht Spedition — Branchenkontext ist kein Plugin, sondern das Produkt selbst.
7. Die Mittelstandskrise in der Logistik ist strukturell, nicht individuell — und trifft Einkäufer mit 6–12 Monaten Verzögerung.
8. Unter 2% der Logistikbetriebe laufen heute KI-gestützt — die technische Hürde ist gefallen, es fehlt an operativer Integration, nicht an Hype."""

_WRITER_READER_PROFILE = """\
LESER: Disponent oder Inhaber einer mittelständischen Spedition. Denkt in: Kunden die wegen Lieferverzug anrufen, Frachtraten, Kundendaten im Postfach, Tagesgeschäft, Regulierung die seinen Betrieb trifft — nicht in "Logistik allgemein"."""

_FORBIDDEN_MARKETING_PHRASES = [
    "innovative Lösung", "ganzheitlich", "Synergien",
    "Wir freuen uns zu verkünden", "revolutionär", "state of the art",
]
_FORBIDDEN_INSTITUTIONAL_PHRASES = [
    "unsere Mission ist es", "wir sind stolz darauf", "unser Ansatz",
    "wir helfen Ihnen dabei", "hilft dabei", "wir freuen uns, dass",
]
_FORBIDDEN_PHRASES_BLOCK = (
    "VERBOTENE PHRASEN (Marketing-Floskeln): "
    + ", ".join(f'"{p}"' for p in _FORBIDDEN_MARKETING_PHRASES)
    + ".\nVERBOTENE PHRASEN (institutionelles Blabla — klingt nach Unternehmensmeldung statt nach Position): "
    + ", ".join(f'"{p}"' for p in _FORBIDDEN_INSTITUTIONAL_PHRASES)
    + "."
)

_WRITER_THIRD_PARTY_RULE = """\
DRITTFIRMEN: nie eine im Artikel genannte private Firma (außer LogAid) für ihre Leistungen, Lösungen oder Erfolge loben oder hervorheben — immer analytisch und kritisch bleiben (Risiken, Trade-offs, Bedeutung für Wettbewerber), nie im Ton einer Erfolgsgeschichte oder Werbung."""

# Etapa 3a — draft only the argument (the substantive core), before hook/pivot/closing
# exist. Asking a model to hit a length target across a whole 6-part structured post
# in one shot is unreliable; asking it to hit a smaller target for just this one
# paragraph is realistic and checkable — the frame (Etapa 3b) adds words on top.
_ARGUMENT_SYSTEM = """\
Du bist ein erfahrener LinkedIn-Ghostwriter für LogAid — KI-gestützte Dispositionsautomatisierung für Speditionen im deutschen Mittelstand.

""" + _WRITER_READER_PROFILE + """

AUFGABE: Schreibe NUR den Kern-Absatz eines LinkedIn-Posts — das Argument. Noch KEIN Hook, kein Pivot, kein Abschluss, keine Hashtags. Diese Reihenfolge ist Absicht: das Argument ist der eigentliche Inhalt; Hook und Abschluss werden erst danach um das fertige Argument herum gebaut.

DEIN JOB IST NICHT, DAS EREIGNIS ZUSAMMENZUFASSEN. Dein Job ist, eine Position dazu zu beziehen — ausgehend von der mitgelieferten Überzeugung (crenca_aplicada), falls vorhanden. Ein Absatz, der auch von jedem anderen Logistik-SaaS stammen könnte, hat noch keine Position bezogen.

STRUKTUR:
1. Argument, 2–4 Sätze Fließtext, KEINE Bullet-Points: entwickle WARUM das für eine Spedition wichtig ist. Nutze questao_contestada und crenca_aplicada als Gerüst.
2. Ein konkreter Beleg oder operatives Detail aus dem Artikel, das die Aussage verdient statt nur behauptet.
3. Optional, nur wenn es sich aus dem Argument zwingend ergibt: LogAid als praktische Konsequenz, höchstens 1 Satz, NIE als Prämisse. "LogAid hilft dabei..." ohne vorheriges Argument ist verboten.

LÄNGE: mindestens """ + str(MIN_ARGUMENT_WORDS) + """ Wörter. Ein Argument, das nicht entwickelt wird, ist kein akzeptables Ergebnis — jedes Thema lässt sich auf diese Länge entwickeln, ohne die Kohärenz zu verlieren.

TON: direkt, sachkundig, dringlich, unprätentiös. Kurze Sätze für Gewicht. Konkret vor abstrakt.

""" + _WRITER_THIRD_PARTY_RULE + """

""" + _FORBIDDEN_PHRASES_BLOCK + """

Antworte NUR als JSON: {"argument_text": "..."}."""

# Etapa 3b — build hook/pivot/closing around the already-written argument. The
# argument is handed over as fixed text and the model is told not to touch it;
# assembly of the final post_text happens in Python (_assemble_post), not here —
# that guarantees the validated argument reaches the final post verbatim.
_FRAME_SYSTEM = """\
Du bist ein erfahrener LinkedIn-Ghostwriter für LogAid — KI-gestützte Dispositionsautomatisierung für Speditionen im deutschen Mittelstand.

""" + _WRITER_READER_PROFILE + """

AUFGABE: Ein Kern-Argument für den Post ist BEREITS geschrieben (unten, unter "BEREITS GESCHRIEBENES ARGUMENT"). Deine Aufgabe ist NICHT, es umzuschreiben, zu kürzen oder zusammenzufassen — sondern einen Hook davor und einen Abschluss danach zu liefern, die natürlich in das Argument hineinführen bzw. daraus folgen.

TEILE, die du lieferst:
1. Hook (1 Zeile): das reale Ereignis, konkret — Datum, Zahl oder umgedeutetes Ereignis. Max 12 Wörter. Noch keine LogAid-Nennung.
2. Pivot (1 Satz): "Die meisten sehen darin X. Für eine Spedition ist es Y." — oder sinngemäß. Muss inhaltlich direkt zum Argument unten hinführen.
3. Abschluss: Call-to-Action ODER offene Frage ODER ein prägnanter Aphorismus. Muss inhaltlich aus dem Argument folgen, nicht generisch sein.
4. 5–7 Hashtags, thematisch zum Artikel und Argument passend (mit "#").

FORMAT: Ich-Perspektive (institutionelles "wir"); kein Clickbait.

""" + _WRITER_THIRD_PARTY_RULE + """

""" + _FORBIDDEN_PHRASES_BLOCK + """

Antworte NUR als JSON: {"hook": "...", "pivot": "...", "closing": "...", "hashtags": ["#...", "..."]}."""

# Etapa 3c — real revision pass, not a blind retry. Python already knows the exact
# word count and has already scanned for forbidden phrases before this call — the
# model is told those facts rather than asked to judge them itself, and its actual
# job here is the one thing Python can't check: whether hook+pivot (written
# separately in 3b) really connects to the argument, or feels bolted on.
_REVISE_SYSTEM = """\
Du bist Redakteur/Lektor für LogAid-LinkedIn-Posts. Du bekommst einen fertig zusammengesetzten Post und prüfst ihn kritisch, bevor er freigegeben wird — wie ein Autor, der seinen Text am Ende noch einmal ganz liest, bevor er ihn abgibt.

PRÜFE:
1. KOHÄRENZ: Führen Hook und Pivot (die ersten 1-2 Zeilen) inhaltlich und logisch zum Argument, das danach kommt? Wirkt der Übergang angeklebt/unpassend, oder wie aus einem Guss?
2. LÄNGE: wird dir unten als Zahl mitgeteilt — zähle nicht selbst nach.
3. VERBOTENE PHRASEN: werden dir unten mitgeteilt, falls welche gefunden wurden.

Wenn du Probleme findest, korrigiere NUR das Nötige — behalte den Rest des Posts unverändert. Wenn der Post zu kurz ist, entwickle das Argument mit einem zusätzlichen konkreten Satz weiter (erfinde keine neuen Fakten, nutze nur was im mitgelieferten Artikeltext steht). Wenn eine verbotene Phrase gefunden wurde, entferne oder ersetze sie. Wenn nichts zu beanstanden ist, gib den Post EXAKT unverändert zurück.

Antworte NUR als JSON: {"post_text": "...", "changed": true, "motivo": "kurze Begründung auf Deutsch, was ggf. korrigiert wurde, sonst leerer String"}."""

_RELEVANCE_SYSTEM = """\
Du bewertest, ob eine Nachricht eine konkrete, nicht-offensichtliche Auswirkung \
auf den operativen Alltag einer deutschen Spedition hat. \
Der Leser ist Disponent oder Inhaber einer mittelständischen Spedition — kein Supply-Chain-Manager \
eines Großkonzerns. Er denkt in: Kunden die wegen Lieferverzug anrufen, Frachtraten, \
Kundendaten im Postfach, Tagesgeschäft, Regulierung die seinen Betrieb trifft. \
Kriterien für Relevanz (mind. eines muss zutreffen): \
direkte Auswirkung auf Frachtpreise/Lieferzeiten/Kapazität auf europäischen oder globalen Routen; \
Auswirkung auf Datensicherheit oder Compliance einer Spedition in Deutschland/EU; \
Regulierung der EU, Deutschlands oder eines Landes mit direktem Einfluss auf europäische Speditionen; \
Technologie die im Alltag einer deutschen Spedition eingesetzt wird, ändert sich fundamental; \
globale Ereignisse (Hafenblockaden, Angriffe auf Schifffahrtsrouten, Streiks) die europäische Lieferketten konkret treffen. \
Kriterien für NICHT-Relevanz — eines dieser Merkmale reicht für false: \
rein US-amerikanische Regulierung (FMCSA, DOT, FRA, STB) ohne Auswirkung auf Europa; \
rein US-interne Marktentwicklungen ohne Bezug zu Import/Export nach Europa; \
"Logistik ist wichtig" oder allgemeine Wirtschaftsnachrichten ohne Speditions-Impact; \
Energie- und Klimanachrichten ohne direkten operativen Effekt für Speditionen heute; \
Tech-Nachrichten oder Unternehmenskäufe ohne operativen Einfluss auf Speditionen. \
Antworte NUR als JSON: {"relevante": true, "motivo": "Begründung auf Deutsch"} \
oder {"relevante": false, "motivo": "Begründung auf Deutsch"}."""

_BRIDGE_ANGLE_FIELDS = """\
    {
      "o_que_aconteceu": "1-2 Sätze: Was genau passiert ist (konkret, faktenbasiert)",
      "questao_contestada": "Die umstrittene Annahme/Praxis im Markt, die dieses Ereignis berührt — 1 Satz",
      "crenca_aplicada": "Die gewählte Überzeugung aus der Bank oben, wörtlich oder leicht ans Ereignis angepasst — leerer String wenn keine wirklich passt",
      "impacto_para_spedition": "Konkrete Auswirkung für eine Spedition",
      "risco_ou_oportunidade": "Konkretes Risiko oder Chance im Betrieb",
      "conexao_logaid": "Optional: wie die gewählte Überzeugung, wenn sie eine praktische Konsequenz verlangt, zu LogAid führt — sonst leerer String",
      "empresa_mencionada": "Optional: Name der im Artikel zitierten Drittfirma, sonst leerer String"
    }"""

_THIRD_PARTY_RULE = """\
REGEL ÜBER DRITTFIRMEN:
Wenn der Artikel eine bestimmte private Firma nennt (außer LogAid), behandle sie nur als \
illustratives Beispiel eines größeren Branchenmusters — nie als Protagonistin oder \
"Erfolgsgeschichte". Übernimm nie Lob, Superlative oder werbliche Sprache des Quellartikels \
über diese Firma. Generalisiere wo möglich: statt "Firma X hat Y gemacht" schreibe "manche \
Speditionen machen Y" oder "die Branche bewegt sich in Richtung Y" — außer der Firmenname ist \
für die Glaubwürdigkeit des Fakts zwingend nötig."""

# Etapa 2a — divergent step. One shot forced the model straight from raw text to a
# single, structured impact claim; when unsure it took the easy way out by writing
# the literal escape phrase the discard filter matched on ("nicht spezifisch genug"
# used to be instructed here). Generating 3 angles first, uncommitted, before any
# quality judgment happens, mirrors brainstorming/freewriting: several rough premises
# on paper before deciding which one is worth arguing.
_BRAINSTORM_SYSTEM = """\
Du bist Analytiker für deutsche Speditionen und denkst im Sinne der LogAid-Positionierung.

AUFGABE: Das ist Ideenfindung, kein Zusammenfassen und keine Entscheidung. Entwickle 3 \
verschiedene mögliche argumentative Brücken zwischen dem Nachrichtenereignis und der \
LogAid-Positionierung, BEVOR du dich auf eine festlegst. Ein schwächerer oder spekulativer \
Ansatz ist hier in Ordnung — die kritische Auswahl passiert in einem späteren Schritt, nicht hier.

ÜBERZEUGUNGSBANK (jede ist eine kontestierbare These — keine passende These heißt oft: kein Thema):
""" + _POV_BELIEFS + """

Für JEDEN der 3 Ansätze:
1. Was ist die umstrittene Annahme/Praxis im Markt, die dieses Ereignis berührt? (Was denkt oder tut ein Teil des Markts, das falsch oder unvollständig ist?)
2. Welche der 8 Überzeugungen löst diese Divergenz auf? Verschiedene Ansätze dürfen verschiedene Überzeugungen nutzen — oder auch keine (leerer String), wenn keine wirklich passt.
3. Wie beweist DIESES konkrete Ereignis die gewählte Überzeugung? (nicht umgekehrt: die Überzeugung dem Ereignis überstülpen)

Nutze die 3 Ansätze, um unterschiedliche Impact-Arten auszuprobieren — nicht 3 Varianten \
derselben Idee:
1. Direkter operativer Effekt (NUR wenn belegbar)
2. Strukturelles Risiko (Abhängigkeit, Kontrollverlust, Systemausfall)
3. Strategische Konsequenz (z. B. Vendor Lock-in, Regulierungsmacht)

VERBOTEN in jedem Ansatz:
- Auswirkungen auf "Frachtpreise", "Lieferzeiten", "Kapazität", wenn nicht DIREKT logisch ableitbar
- generische Aussagen über Logistik
- erfundene Kausalität

""" + _THIRD_PARTY_RULE + """

Fülle dieses JSON aus, mit genau 3 Objekten in "angulos", jedes mit dieser Struktur:
""" + _BRIDGE_ANGLE_FIELDS + """

Antworte NUR als JSON: {"angulos": [ANSATZ_1, ANSATZ_2, ANSATZ_3]} — Liste mit genau 3 Objekten."""

# Etapa 2b — convergent step. A contrastive judgment between 3 concrete alternatives
# is a more reliable quality gate than a model rating its own single output; this
# replaces the old substring-matching discard filter as the primary gate (that filter
# is kept downstream only as a cheap secondary safety net).
_BRIDGE_SELECT_SYSTEM = """\
Du bist Redakteur für LogAid und bewertest 3 vorgeschlagene argumentative Brücken zwischen \
einem Nachrichtenereignis und der Spedition-Realität. Du bekommst den Artikel und die 3 Ansätze \
im Vergleich — deine Aufgabe ist eine kritische, kontrastive Entscheidung, kein erneutes Brainstorming.

VORGEHEN:
1. Vergleiche die 3 Ansätze direkt gegeneinander: welcher hat den konkretesten, am wenigsten \
generischen Impact — belegt durch eine Zahl, einen Akteur oder einen Mechanismus AUS DEM ARTIKEL?
2. Wähle den stärksten Ansatz. Verwirf die anderen beiden vollständig.
3. Schärfe impacto_para_spedition und risco_ou_oportunidade des gewählten Ansatzes: wenn der \
Entwurf noch vage ist ("könnte Auswirkungen haben", "hängt von der Umsetzung ab"), schreibe ihn \
so um, dass er ein konkretes Detail aus dem Artikel referenziert. Erfinde keine neuen Fakten — \
nutze nur, was im Artikel steht.

""" + _THIRD_PARTY_RULE + """

Antworte mit dem gewählten und geschärften Ansatz als JSON mit exakt den sieben Schlüsseln:
""" + _BRIDGE_ANGLE_FIELDS + """

Nur wenn ALLE 3 Ansätze nach diesem Vergleich wirklich keine belegbare, konkrete Auswirkung auf \
eine Spedition haben — nicht weil es unbequem ist, gründlich zu prüfen, sondern weil das Ereignis \
tatsächlich keinen Hebel für eine Spedition hat — antworte stattdessen:
{"invalid": true, "motivo": "kurze Begründung auf Deutsch, warum keiner der 3 Ansätze trägt"}."""

_AD_CHECK_SYSTEM = """\
Du bewertest, ob ein LinkedIn-Beitrag wie eine unabhängige, kritische Analyse eines \
Branchenthemas klingt — oder wie Werbung/PR für ein bestimmtes DRITTUNTERNEHMEN oder \
dessen Produkt. \
WICHTIG: LogAid ist der Auftraggeber dieses Beitrags — eine kurze, sachliche Erwähnung von \
LogAid am Ende des Beitrags ist der reguläre, erwünschte Zweck des Textes und zählt NIEMALS \
als soa_como_ad=true, selbst wenn sie positiv formuliert ist. Bewerte NUR den Ton gegenüber \
Drittfirmen (jedes im Beitrag genannte Unternehmen außer LogAid). \
Kriterien für "soa_como_ad=true": der Beitrag lobt ein Drittunternehmen, hebt dessen \
Produkt/Lösung positiv hervor, oder liest sich wie eine Erfolgsgeschichte/Case Study eines \
Dritten. \
Antworte NUR als JSON: {"soa_como_ad": true, "motivo": "Begründung auf Deutsch"} \
oder {"soa_como_ad": false, "motivo": "Begründung auf Deutsch"}."""

_ART_DIRECTOR_SYSTEM = """\
You write text-to-image prompts for a clean, professional, editorial LinkedIn header \
image about the German logistics industry. Given the article topic and the post, produce \
ONE concise English prompt. Rules: vivid concrete scene (trucks, ports, warehouses, \
supply chains, etc. as fits the topic); editorial/photographic, modern, neutral palette; \
16:9; NO text, NO logos, NO real faces or brands. \
Answer ONLY as JSON: {"image_prompt": "..."}."""


def _extract_json(text: str, key: str) -> str | None:
    """Try to extract {key: value} from potentially messy LLM output."""
    try:
        data = json.loads(text)
        if key in data:
            return data[key]
    except json.JSONDecodeError:
        pass

    # Fallback: find JSON object in the text
    match = re.search(r'\{[^{}]*"' + re.escape(key) + r'"[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            return data.get(key)
        except json.JSONDecodeError:
            pass

    return None


def _parse_json_obj(text: str, required_keys: list) -> dict | None:
    """Parse a JSON object from LLM output, validating all required keys are present."""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and all(k in data for k in required_keys):
            return data
    except json.JSONDecodeError:
        pass
    # Fallback: find outermost JSON object (handles markdown code blocks, preambles)
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, dict) and all(k in data for k in required_keys):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _chat(model: str, system: str, user: str, temperature: float = 0.6) -> str:
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


def _build_kb_context(article: dict) -> str:
    """Retrieve relevant examples and guidelines from the KB and format them for the prompt."""
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
        logger.debug("KB context skipped: %s", e)
    return ""


def evaluate_relevance(article: dict) -> dict | None:
    """Step 1: Check if article has concrete relevance for a Spedition.

    Returns {"relevante": bool, "motivo": str} or None on failure.
    """
    user_msg = (
        f"Schlagzeile: {article.get('title', '')}\n\n"
        f"Zusammenfassung:\n{article.get('full_text', '')[:1000]}"
    )
    required = ["relevante", "motivo"]
    for attempt in range(2):
        try:
            raw = _chat(WRITER_MODEL, _RELEVANCE_SYSTEM, user_msg)
            data = _parse_json_obj(raw, required)
            if data is not None:
                return data
            logger.warning("Relevance parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Relevance check error (attempt %d): %s", attempt + 1, e)
    return None


def evaluate_promotional_tone(post_text: str) -> dict | None:
    """Step 4 (post-generation): flag posts that read as advertising for a third party."""
    required = ["soa_como_ad", "motivo"]
    for attempt in range(2):
        try:
            raw = _chat(WRITER_MODEL, _AD_CHECK_SYSTEM, post_text)
            data = _parse_json_obj(raw, required)
            if data is not None:
                return data
            logger.warning("Ad-check parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Ad-check error (attempt %d): %s", attempt + 1, e)
    return None


_BRIDGE_ANGLE_KEYS = [
    "o_que_aconteceu",
    "questao_contestada",
    "crenca_aplicada",
    "impacto_para_spedition",
    "risco_ou_oportunidade",
    "conexao_logaid",
]


def _brainstorm_angles(article: dict, relevance_motivo: str = "") -> list[dict]:
    """Etapa 2a: generate up to 3 divergent candidate bridges (brainstorm step).

    Returns a list of angle dicts (each shaped like the old build_bridge output),
    or [] on failure. Deliberately not filtered for quality here — that's Etapa 2b.
    """
    context = f"Relevanz-Kontext: {relevance_motivo}\n\n" if relevance_motivo else ""
    user_msg = (
        f"{context}"
        f"Schlagzeile: {article.get('title', '')}\n\n"
        f"Artikeltext:\n{article.get('full_text', '')[:2000]}"
    )
    for attempt in range(2):
        try:
            raw = _chat(WRITER_MODEL, _BRAINSTORM_SYSTEM, user_msg, temperature=0.9)
            data = _parse_json_obj(raw, ["angulos"])
            if data is not None and isinstance(data.get("angulos"), list):
                valid = [
                    a for a in data["angulos"]
                    if isinstance(a, dict) and all(k in a for k in _BRIDGE_ANGLE_KEYS)
                ]
                if valid:
                    return valid
            logger.warning("Brainstorm parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Brainstorm error (attempt %d): %s", attempt + 1, e)
    return []


def _select_best_angle(article: dict, angles: list[dict]) -> dict | None:
    """Etapa 2b: critically compare the candidate angles and pick/sharpen the strongest.

    Returns the winning angle dict, {"invalid": True, ...} if none are defensible,
    or None on parse failure.
    """
    angles_text = "\n\n".join(
        f"Ansatz {i}:\n{json.dumps(a, ensure_ascii=False)}"
        for i, a in enumerate(angles, 1)
    )
    user_msg = (
        f"Schlagzeile: {article.get('title', '')}\n\n"
        f"Artikeltext:\n{article.get('full_text', '')[:2000]}\n\n"
        f"=== VORGESCHLAGENE ANSÄTZE ===\n{angles_text}"
    )
    for attempt in range(2):
        try:
            raw = _chat(WRITER_MODEL, _BRIDGE_SELECT_SYSTEM, user_msg, temperature=0.3)
            data = _parse_json_obj(raw, _BRIDGE_ANGLE_KEYS) or _parse_json_obj(raw, ["invalid"])
            if data is not None:
                return data
            logger.warning("Bridge select parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Bridge select error (attempt %d): %s", attempt + 1, e)
    return None


def build_bridge(article: dict, relevance_motivo: str = "") -> dict | None:
    """Step 2: Build narrative bridge between the news event and Spedition reality.

    Two-stage process — brainstorm 3 candidate angles (divergent), then critically
    select and sharpen the strongest one (convergent) — instead of committing to a
    single angle in one shot. Contrastive judgment between alternatives is a more
    reliable quality gate than a model rating its own single output in isolation.

    Returns {"o_que_aconteceu", "impacto_para_spedition", "risco_ou_oportunidade",
    "conexao_logaid", ...} or None on failure.
    """
    title = article.get("title", "")[:70]

    angles = _brainstorm_angles(article, relevance_motivo)
    if not angles:
        logger.info("Bridge descartada: brainstorm não gerou ângulos válidos — '%s'", title)
        return None

    data = _select_best_angle(article, angles)
    if data is None:
        logger.info("Bridge descartada: seleção falhou (parse) — '%s'", title)
        return None
    if data.get("invalid"):
        logger.info(
            "Bridge descartada: nenhum dos %d ângulos defensável — '%s' | %s",
            len(angles), title, data.get("motivo", "")[:120],
        )
        return None

    impact = data.get("impacto_para_spedition", "").strip()
    risk = data.get("risco_ou_oportunidade", "").strip()
    generic_signals = [
        "nicht spezifisch",
        "nicht anwendbar",
        "allgemein",
        "hängt von technologie",
        "keine direkten",
    ]
    if not impact or not risk or any(s in impact.lower() for s in generic_signals):
        logger.info(
            "Bridge descartada: impacto/risco vazio ou genérico após seleção — '%s' | impacto='%s'",
            title, impact[:120] or "(vazio)",
        )
        return None

    return data


def _format_bridge_context(bridge: dict | None) -> str:
    if not bridge:
        return ""
    crenca = bridge.get("crenca_aplicada", "") or "(keine — rein informativ behandeln, kein Argument erzwingen)"
    return (
        "=== NARRATIVE BRÜCKE — als Gerüst des Arguments verwenden ===\n"
        f"Was passiert ist: {bridge.get('o_que_aconteceu', '')}\n"
        f"Umstrittene Frage/Annahme im Markt: {bridge.get('questao_contestada', '')}\n"
        f"Angewandte LogAid-Überzeugung: {crenca}\n"
        f"Konkreter Impact für eine Spedition: {bridge.get('impacto_para_spedition', '')}\n"
        f"Risiko / Chance: {bridge.get('risco_ou_oportunidade', '')}\n"
        f"LogAid-Verbindung: {bridge.get('conexao_logaid', '')}\n\n"
    )


def _find_forbidden_phrases(text: str) -> list[str]:
    lowered = text.lower()
    return [
        p for p in _FORBIDDEN_MARKETING_PHRASES + _FORBIDDEN_INSTITUTIONAL_PHRASES
        if p.lower() in lowered
    ]


def _draft_argument(article: dict, bridge: dict | None, kb_context: str) -> str:
    """Etapa 3a: draft only the argument paragraph — the substantive core.

    A short first attempt gets a *targeted* retry (previous draft + its actual
    word count fed back to the model) instead of blindly resending the same
    prompt — a blind retry has no reason to converge upward and can regress.
    """
    base_user_msg = (
        f"{kb_context}"
        f"{_format_bridge_context(bridge)}"
        f"Artikeltitel: {article.get('title', '')}\n\n"
        f"Artikeltext:\n{article.get('full_text', '')[:3000]}"
    )
    user_msg = base_user_msg
    # 3, not 2: the targeted retry (below) tends to climb toward the target in
    # visible increments once the model has real feedback to react to, but one
    # retry isn't always enough — observed live runs climbing 45->62 and 39->60
    # words in a single retry, one attempt short of MIN_ARGUMENT_WORDS.
    for attempt in range(3):
        try:
            raw = _chat(WRITER_MODEL, _ARGUMENT_SYSTEM, user_msg)
            value = _extract_json(raw, "argument_text")
            if value:
                word_count = len(value.split())
                if word_count < MIN_ARGUMENT_WORDS:
                    logger.warning(
                        "Argument too short (%d words, min %d) — attempt %d, retrying: %s",
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
            logger.warning("Argument parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Argument draft error (attempt %d): %s", attempt + 1, e)
    return ""


def _frame_post(article: dict, bridge: dict | None, argument_text: str, kb_context: str) -> dict | None:
    """Etapa 3b: build hook/pivot/closing/hashtags around the finished argument."""
    user_msg = (
        f"{kb_context}"
        f"{_format_bridge_context(bridge)}"
        f"Artikeltitel: {article.get('title', '')}\n\n"
        f"=== BEREITS GESCHRIEBENES ARGUMENT (nicht verändern) ===\n{argument_text}"
    )
    required = ["hook", "pivot", "closing", "hashtags"]
    for attempt in range(2):
        try:
            raw = _chat(WRITER_MODEL, _FRAME_SYSTEM, user_msg)
            data = _parse_json_obj(raw, required)
            if data is not None:
                return data
            logger.warning("Frame parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Frame error (attempt %d): %s", attempt + 1, e)
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
    """Etapa 3c: real revision pass — coherence, length, forbidden phrases.

    Python computes word count and scans for forbidden phrases beforehand and
    hands both to the model as facts, rather than asking it to judge them itself.
    Also verifies its own fix: if a forbidden phrase is still present after a
    revision, that's fed back on a second attempt instead of trusting compliance
    blindly — a model asked to remove a phrase isn't guaranteed to actually do it.
    """
    current_text = post_text
    last_result: dict | None = None
    required = ["post_text", "changed"]
    for attempt in range(2):
        word_count = len(current_text.split())
        found = _find_forbidden_phrases(current_text)
        if attempt > 0 and not found and word_count >= MIN_POST_WORDS:
            break
        user_msg = (
            f"AKTUELLE LÄNGE: {word_count} Wörter (Ziel: {MIN_POST_WORDS}-{MAX_POST_WORDS} Wörter)\n"
            f"GEFUNDENE VERBOTENE PHRASEN: {', '.join(found) if found else '(keine)'}\n\n"
            f"Artikeltext (nur zur Faktenprüfung, falls Entwicklung nötig):\n{article.get('full_text', '')[:2000]}\n\n"
            f"=== POST ===\n{current_text}"
        )
        try:
            raw = _chat(WRITER_MODEL, _REVISE_SYSTEM, user_msg, temperature=0.3)
            data = _parse_json_obj(raw, required)
            if data is not None and data.get("post_text"):
                last_result = data
                current_text = data["post_text"]
                still_found = _find_forbidden_phrases(current_text)
                if not still_found:
                    return data
                logger.warning(
                    "Revision left forbidden phrase(s) in place (attempt %d): %s",
                    attempt + 1, still_found,
                )
                continue
            logger.warning("Revision parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Revision error (attempt %d): %s", attempt + 1, e)
    return last_result


def generate_post(article: dict, bridge: dict | None = None) -> str:
    """Agent 1: produce a German LinkedIn post for the article.

    Three-stage process mirroring how a human drafts an argumentative text: draft
    the argument first (Etapa 3a, the substantive core), build hook/pivot/closing
    around the finished argument (Etapa 3b), then a real revision pass checks
    coherence, length and forbidden phrases before returning (Etapa 3c) — instead
    of generating the whole structured post in one shot with a blind length retry.
    """
    title = article.get("title", "")[:60]
    kb_context = _build_kb_context(article)

    argument_text = _draft_argument(article, bridge, kb_context)
    if not argument_text:
        logger.warning("Agent 1: argument drafting failed for '%s'", title)
        return ""

    frame = _frame_post(article, bridge, argument_text, kb_context)
    if not frame:
        logger.warning("Agent 1: framing failed for '%s'", title)
        return ""

    post_text = _assemble_post(frame, argument_text)

    revision = _revise_post(article, post_text)
    if revision is not None:
        post_text = revision.get("post_text") or post_text
        if revision.get("changed"):
            logger.info("Agent 1: revision adjusted post — %s", revision.get("motivo", "")[:120])
    else:
        logger.warning("Agent 1: revision step failed for '%s' — using unrevised draft", title)

    word_count = len(post_text.split())
    if word_count < MIN_POST_WORDS:
        logger.warning(
            "Agent 1: post still under %d words (%d) after revision — discarding '%s'",
            MIN_POST_WORDS, word_count, title,
        )
        return ""
    return post_text


def generate_image_prompt(article: dict, post_text: str) -> str:
    """Agent 2: produce an English text-to-image prompt."""
    user_msg = (
        f"Topic: {article.get('topic', '')}\n"
        f"Article title: {article.get('title', '')}\n\n"
        f"LinkedIn post:\n{post_text[:500]}"
    )
    for attempt in range(2):
        try:
            raw = _chat(IMAGE_PROMPT_MODEL, _ART_DIRECTOR_SYSTEM, user_msg)
            value = _extract_json(raw, "image_prompt")
            if value:
                return value
            logger.warning("Agent 2 parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Agent 2 error (attempt %d): %s", attempt + 1, e)
    return "Professional logistics scene, German supply chain, editorial style, 16:9, no text"
