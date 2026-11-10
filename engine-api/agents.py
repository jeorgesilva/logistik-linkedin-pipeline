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
# Bewusst kleiner als MIN_POST_WORDS: ein Ziel dieser Größe nur für den Argument-Block
# (Schritt 3a) lässt sich per Retry realistisch garantieren — anders als 100+ kohärente
# Wörter für den ganzen Post in einem Durchgang zu verlangen.
MIN_ARGUMENT_WORDS = int(os.getenv("MIN_ARGUMENT_WORDS", "70"))

# Verdichtet aus data/style_guide/06_point_of_view.md — direkt in die Bridge-/Writer-
# Prompts eingebettet statt dem RAG-Retrieval überlassen. RAG liefert pro Aufruf nur 2 von
# 39 Guideline-Abschnitten, daher tauchte ein grundlegendes, immer benötigtes Element wie die
# Überzeugungsbank nur unzuverlässig auf; Posts bestanden dann aus Fakt + generischem
# „Frachtwerk hilft dabei ...“-Anhängsel statt einer echt argumentierten Position. Mit
# 06_point_of_view.md synchron halten, wenn sich die Überzeugungsbank ändert.
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
DRITTFIRMEN: nie eine im Artikel genannte private Firma (außer Frachtwerk) für ihre Leistungen, Lösungen oder Erfolge loben oder hervorheben — immer analytisch und kritisch bleiben (Risiken, Trade-offs, Bedeutung für Wettbewerber), nie im Ton einer Erfolgsgeschichte oder Werbung."""

# Schritt 3a — nur das Argument (den inhaltlichen Kern) entwerfen, bevor Hook/Pivot/Abschluss
# existieren. Von einem Modell zu verlangen, ein Längenziel über einen ganzen 6-teiligen
# strukturierten Post in einem Durchgang zu treffen, ist unzuverlässig; ein kleineres Ziel nur
# für diesen einen Absatz ist realistisch und prüfbar — der Rahmen (Schritt 3b) fügt weitere
# Wörter hinzu.
_ARGUMENT_SYSTEM = """\
Du bist ein erfahrener LinkedIn-Ghostwriter für Frachtwerk — KI-gestützte Dispositionsautomatisierung für Speditionen im deutschen Mittelstand.

""" + _WRITER_READER_PROFILE + """

AUFGABE: Schreibe NUR den Kern-Absatz eines LinkedIn-Posts — das Argument. Noch KEIN Hook, kein Pivot, kein Abschluss, keine Hashtags. Diese Reihenfolge ist Absicht: das Argument ist der eigentliche Inhalt; Hook und Abschluss werden erst danach um das fertige Argument herum gebaut.

DEIN JOB IST NICHT, DAS EREIGNIS ZUSAMMENZUFASSEN. Dein Job ist, eine Position dazu zu beziehen — ausgehend von der mitgelieferten Überzeugung (crenca_aplicada), falls vorhanden. Ein Absatz, der auch von jedem anderen Logistik-SaaS stammen könnte, hat noch keine Position bezogen.

STRUKTUR:
1. Argument, 2–4 Sätze Fließtext, KEINE Bullet-Points: entwickle WARUM das für eine Spedition wichtig ist. Nutze questao_contestada und crenca_aplicada als Gerüst.
2. Ein konkreter Beleg oder operatives Detail aus dem Artikel, das die Aussage verdient statt nur behauptet.
3. Optional, nur wenn es sich aus dem Argument zwingend ergibt: Frachtwerk als praktische Konsequenz, höchstens 1 Satz, NIE als Prämisse. "Frachtwerk hilft dabei..." ohne vorheriges Argument ist verboten.

LÄNGE: mindestens """ + str(MIN_ARGUMENT_WORDS) + """ Wörter. Ein Argument, das nicht entwickelt wird, ist kein akzeptables Ergebnis — jedes Thema lässt sich auf diese Länge entwickeln, ohne die Kohärenz zu verlieren.

TON: direkt, sachkundig, dringlich, unprätentiös. Kurze Sätze für Gewicht. Konkret vor abstrakt.

""" + _WRITER_THIRD_PARTY_RULE + """

""" + _FORBIDDEN_PHRASES_BLOCK + """

Antworte NUR als JSON: {"argument_text": "..."}."""

# Schritt 3b — Hook/Pivot/Abschluss um das bereits geschriebene Argument bauen. Das Argument
# wird als fester Text übergeben, und das Modell wird angewiesen, es nicht anzufassen; der
# finale post_text wird in Python zusammengesetzt (_assemble_post), nicht hier — so erreicht
# das validierte Argument den finalen Post garantiert wortgleich.
_FRAME_SYSTEM = """\
Du bist ein erfahrener LinkedIn-Ghostwriter für Frachtwerk — KI-gestützte Dispositionsautomatisierung für Speditionen im deutschen Mittelstand.

""" + _WRITER_READER_PROFILE + """

AUFGABE: Ein Kern-Argument für den Post ist BEREITS geschrieben (unten, unter "BEREITS GESCHRIEBENES ARGUMENT"). Deine Aufgabe ist NICHT, es umzuschreiben, zu kürzen oder zusammenzufassen — sondern einen Hook davor und einen Abschluss danach zu liefern, die natürlich in das Argument hineinführen bzw. daraus folgen.

TEILE, die du lieferst:
1. Hook (1 Zeile): das reale Ereignis, konkret — Datum, Zahl oder umgedeutetes Ereignis. Max 12 Wörter. Noch keine Frachtwerk-Nennung.
2. Pivot (1 Satz): "Die meisten sehen darin X. Für eine Spedition ist es Y." — oder sinngemäß. Muss inhaltlich direkt zum Argument unten hinführen.
3. Abschluss: Call-to-Action ODER offene Frage ODER ein prägnanter Aphorismus. Muss inhaltlich aus dem Argument folgen, nicht generisch sein.
4. 5–7 Hashtags, thematisch zum Artikel und Argument passend (mit "#").

FORMAT: Ich-Perspektive (institutionelles "wir"); kein Clickbait.

""" + _WRITER_THIRD_PARTY_RULE + """

""" + _FORBIDDEN_PHRASES_BLOCK + """

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
      "conexao_frachtwerk": "Optional: wie die gewählte Überzeugung, wenn sie eine praktische Konsequenz verlangt, zu Frachtwerk führt — sonst leerer String",
      "empresa_mencionada": "Optional: Name der im Artikel zitierten Drittfirma, sonst leerer String"
    }"""

_THIRD_PARTY_RULE = """\
REGEL ÜBER DRITTFIRMEN:
Wenn der Artikel eine bestimmte private Firma nennt (außer Frachtwerk), behandle sie nur als \
illustratives Beispiel eines größeren Branchenmusters — nie als Protagonistin oder \
"Erfolgsgeschichte". Übernimm nie Lob, Superlative oder werbliche Sprache des Quellartikels \
über diese Firma. Generalisiere wo möglich: statt "Firma X hat Y gemacht" schreibe "manche \
Speditionen machen Y" oder "die Branche bewegt sich in Richtung Y" — außer der Firmenname ist \
für die Glaubwürdigkeit des Fakts zwingend nötig."""

# Schritt 2a — divergenter Schritt. Ein einziger Durchgang zwang das Modell direkt vom Rohtext
# zu einer einzigen, strukturierten Wirkungsaussage; im Zweifel nahm es den bequemen Ausweg und
# schrieb wörtlich die Ausweichphrase, auf die der Verwerfungsfilter reagierte („nicht spezifisch
# genug“ wurde hier früher vorgegeben). Zuerst 3 Blickwinkel unverbindlich zu erzeugen, bevor
# irgendeine Qualitätsbewertung stattfindet, entspricht Brainstorming/Freewriting: mehrere grobe
# Prämissen auf Papier, bevor man entscheidet, welche es wert ist, argumentiert zu werden.
_BRAINSTORM_SYSTEM = """\
Du bist Analytiker für deutsche Speditionen und denkst im Sinne der Frachtwerk-Positionierung.

AUFGABE: Das ist Ideenfindung, kein Zusammenfassen und keine Entscheidung. Entwickle 3 \
verschiedene mögliche argumentative Brücken zwischen dem Nachrichtenereignis und der \
Frachtwerk-Positionierung, BEVOR du dich auf eine festlegst. Ein schwächerer oder spekulativer \
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

# Schritt 2b — konvergenter Schritt. Ein vergleichendes Urteil zwischen 3 konkreten Alternativen
# ist eine zuverlässigere Qualitätsschranke, als wenn ein Modell seine einzige eigene Ausgabe
# bewertet; das ersetzt den alten Verwerfungsfilter per Substring-Abgleich als primäre Schranke
# (dieser Filter bleibt nachgelagert nur als günstiges zweites Sicherheitsnetz erhalten).
_BRIDGE_SELECT_SYSTEM = """\
Du bist Redakteur für Frachtwerk und bewertest 3 vorgeschlagene argumentative Brücken zwischen \
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
WICHTIG: Frachtwerk ist der Auftraggeber dieses Beitrags — eine kurze, sachliche Erwähnung von \
Frachtwerk am Ende des Beitrags ist der reguläre, erwünschte Zweck des Textes und zählt NIEMALS \
als soa_como_ad=true, selbst wenn sie positiv formuliert ist. Bewerte NUR den Ton gegenüber \
Drittfirmen (jedes im Beitrag genannte Unternehmen außer Frachtwerk). \
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


def _parse_json_obj(text: str, required_keys: list) -> dict | None:
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


def evaluate_relevance(article: dict) -> dict | None:
    """Schritt 1: Prüft, ob der Artikel konkrete Relevanz für eine Spedition hat.

    Gibt {"relevante": bool, "motivo": str} zurück, bei Fehler None.
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
            logger.warning("Relevanzprüfung: Parsing fehlgeschlagen (Versuch %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Relevanzprüfung: Fehler (Versuch %d): %s", attempt + 1, e)
    return None


def evaluate_promotional_tone(post_text: str) -> dict | None:
    """Schritt 4 (nach der Erzeugung): erkennt Posts, die wie Werbung für ein Drittunternehmen klingen."""
    required = ["soa_como_ad", "motivo"]
    for attempt in range(2):
        try:
            raw = _chat(WRITER_MODEL, _AD_CHECK_SYSTEM, post_text)
            data = _parse_json_obj(raw, required)
            if data is not None:
                return data
            logger.warning("Werbeprüfung: Parsing fehlgeschlagen (Versuch %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Werbeprüfung: Fehler (Versuch %d): %s", attempt + 1, e)
    return None


_BRIDGE_ANGLE_KEYS = [
    "o_que_aconteceu",
    "questao_contestada",
    "crenca_aplicada",
    "impacto_para_spedition",
    "risco_ou_oportunidade",
    "conexao_frachtwerk",
]


def _brainstorm_angles(article: dict, relevance_motivo: str = "") -> list[dict]:
    """Schritt 2a: erzeugt bis zu 3 divergente Kandidaten-Bridges (Brainstorming-Schritt).

    Gibt eine Liste von Blickwinkel-Dicts zurück (jeweils im Format der alten
    build_bridge-Ausgabe), bei Fehler []. Hier bewusst nicht nach Qualität gefiltert —
    das übernimmt Schritt 2b.
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
            logger.warning("Brainstorming: Parsing fehlgeschlagen (Versuch %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Brainstorming: Fehler (Versuch %d): %s", attempt + 1, e)
    return []


def _select_best_angle(article: dict, angles: list[dict]) -> dict | None:
    """Schritt 2b: vergleicht die Kandidaten-Blickwinkel kritisch und wählt/schärft den stärksten.

    Gibt das Dict des Gewinners zurück, {"invalid": True, ...}, wenn keiner vertretbar ist,
    oder None bei einem Parse-Fehler.
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
            logger.warning("Brückenauswahl: Parsing fehlgeschlagen (Versuch %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Brückenauswahl: Fehler (Versuch %d): %s", attempt + 1, e)
    return None


def build_bridge(article: dict, relevance_motivo: str = "") -> dict | None:
    """Schritt 2: baut die erzählerische Brücke zwischen Nachricht und Speditionsalltag.

    Zweistufiger Ablauf — 3 Kandidaten-Blickwinkel brainstormen (divergent), dann den
    stärksten kritisch auswählen und schärfen (konvergent) — statt sich in einem Durchgang
    auf einen einzigen Blickwinkel festzulegen. Ein vergleichendes Urteil zwischen
    Alternativen ist eine zuverlässigere Qualitätsschranke, als wenn ein Modell seine
    einzige eigene Ausgabe isoliert bewertet.

    Gibt {"o_que_aconteceu", "impacto_para_spedition", "risco_ou_oportunidade",
    "conexao_frachtwerk", ...} zurück, bei Fehler None.
    """
    title = article.get("title", "")[:70]

    angles = _brainstorm_angles(article, relevance_motivo)
    if not angles:
        logger.info("Brücke verworfen: Brainstorming lieferte keine gültigen Blickwinkel — '%s'", title)
        return None

    data = _select_best_angle(article, angles)
    if data is None:
        logger.info("Brücke verworfen: Auswahl fehlgeschlagen (Parsing) — '%s'", title)
        return None
    if data.get("invalid"):
        logger.info(
            "Brücke verworfen: keiner der %d Blickwinkel ist haltbar — '%s' | %s",
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
            "Brücke verworfen: Auswirkung/Risiko nach der Auswahl leer oder generisch — '%s' | Auswirkung='%s'",
            title, impact[:120] or "(leer)",
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
        f"Angewandte Frachtwerk-Überzeugung: {crenca}\n"
        f"Konkreter Impact für eine Spedition: {bridge.get('impacto_para_spedition', '')}\n"
        f"Risiko / Chance: {bridge.get('risco_ou_oportunidade', '')}\n"
        f"Frachtwerk-Verbindung: {bridge.get('conexao_frachtwerk', '')}\n\n"
    )


def _find_forbidden_phrases(text: str) -> list[str]:
    lowered = text.lower()
    return [
        p for p in _FORBIDDEN_MARKETING_PHRASES + _FORBIDDEN_INSTITUTIONAL_PHRASES
        if p.lower() in lowered
    ]


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
            raw = _chat(WRITER_MODEL, _ARGUMENT_SYSTEM, user_msg)
            value = _extract_json(raw, "argument_text")
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
            raw = _chat(WRITER_MODEL, _FRAME_SYSTEM, user_msg)
            data = _parse_json_obj(raw, required)
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
    strukturierten Post in einem Durchgang mit blindem Längen-Retry zu erzeugen.
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
            logger.info("Agent 1: Überarbeitung hat den Post angepasst — %s", revision.get("motivo", "")[:120])
    else:
        logger.warning("Agent 1: Überarbeitung fehlgeschlagen für '%s' — unüberarbeiteter Entwurf wird verwendet", title)

    word_count = len(post_text.split())
    if word_count < MIN_POST_WORDS:
        logger.warning(
            "Agent 1: Post nach der Überarbeitung immer noch unter %d Wörtern (%d) — '%s' wird verworfen",
            MIN_POST_WORDS, word_count, title,
        )
        return ""
    return post_text


def generate_image_prompt(article: dict, post_text: str) -> str:
    """Agent 2: erzeugt einen englischen Text-to-Image-Prompt."""
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
            logger.warning("Agent 2: Parsing fehlgeschlagen (Versuch %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Agent 2: Fehler (Versuch %d): %s", attempt + 1, e)
    return "Professional logistics scene, German supply chain, editorial style, 16:9, no text"
