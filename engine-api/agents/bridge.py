"""Schritte 1–2: Relevanzprüfung und narrative Brücke zwischen Nachricht und Speditionsalltag."""

import json
import logging

from agents import llm, rules

logger = logging.getLogger(__name__)


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
Antworte NUR als JSON: {"relevant": true, "reason": "Begründung auf Deutsch"} \
oder {"relevant": false, "reason": "Begründung auf Deutsch"}."""

_BRIDGE_ANGLE_FIELDS = """\
    {
      "what_happened": "1-2 Sätze: Was genau passiert ist (konkret, faktenbasiert)",
      "contested_question": "Die umstrittene Annahme/Praxis im Markt, die dieses Ereignis berührt — 1 Satz",
      "applied_belief": "Die gewählte Überzeugung aus der Bank oben, wörtlich oder leicht ans Ereignis angepasst — leerer String wenn keine wirklich passt",
      "impact_for_forwarders": "Konkrete Auswirkung für eine Spedition",
      "risk_or_opportunity": "Konkretes Risiko oder Chance im Betrieb",
      "frachtwerk_connection": "Optional: wie die gewählte Überzeugung, wenn sie eine praktische Konsequenz verlangt, zu Frachtwerk führt — sonst leerer String",
      "mentioned_company": "Optional: Name der im Artikel zitierten Drittfirma, sonst leerer String"
    }"""

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
""" + rules.POV_BELIEFS + """

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

""" + rules.THIRD_PARTY_RULE + """

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
3. Schärfe impact_for_forwarders und risk_or_opportunity des gewählten Ansatzes: wenn der \
Entwurf noch vage ist ("könnte Auswirkungen haben", "hängt von der Umsetzung ab"), schreibe ihn \
so um, dass er ein konkretes Detail aus dem Artikel referenziert. Erfinde keine neuen Fakten — \
nutze nur, was im Artikel steht.

""" + rules.THIRD_PARTY_RULE + """

Antworte mit dem gewählten und geschärften Ansatz als JSON mit exakt den sieben Schlüsseln:
""" + _BRIDGE_ANGLE_FIELDS + """

Nur wenn ALLE 3 Ansätze nach diesem Vergleich wirklich keine belegbare, konkrete Auswirkung auf \
eine Spedition haben — nicht weil es unbequem ist, gründlich zu prüfen, sondern weil das Ereignis \
tatsächlich keinen Hebel für eine Spedition hat — antworte stattdessen:
{"invalid": true, "reason": "kurze Begründung auf Deutsch, warum keiner der 3 Ansätze trägt"}."""


def evaluate_relevance(article: dict) -> dict | None:
    """Schritt 1: Prüft, ob der Artikel konkrete Relevanz für eine Spedition hat.

    Gibt {"relevant": bool, "reason": str} zurück, bei Fehler None.
    """
    user_msg = (
        f"Schlagzeile: {article.get('title', '')}\n\n"
        f"Zusammenfassung:\n{article.get('full_text', '')[:1000]}"
    )
    required = ["relevant", "reason"]
    for attempt in range(2):
        try:
            raw = llm.chat(llm.WRITER_MODEL, _RELEVANCE_SYSTEM, user_msg)
            data = llm.parse_json_obj(raw, required)
            if data is not None:
                return data
            logger.warning("Relevanzprüfung: Parsing fehlgeschlagen (Versuch %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Relevanzprüfung: Fehler (Versuch %d): %s", attempt + 1, e)
    return None


_BRIDGE_ANGLE_KEYS = [
    "what_happened",
    "contested_question",
    "applied_belief",
    "impact_for_forwarders",
    "risk_or_opportunity",
    "frachtwerk_connection",
]


def _brainstorm_angles(article: dict, relevance_reason: str = "") -> list[dict]:
    """Schritt 2a: erzeugt bis zu 3 divergente Kandidaten-Bridges (Brainstorming-Schritt).

    Gibt eine Liste von Blickwinkel-Dicts zurück (jeweils im Format der alten
    build_bridge-Ausgabe), bei Fehler []. Hier bewusst nicht nach Qualität gefiltert —
    das übernimmt Schritt 2b.
    """
    context = f"Relevanz-Kontext: {relevance_reason}\n\n" if relevance_reason else ""
    user_msg = (
        f"{context}"
        f"Schlagzeile: {article.get('title', '')}\n\n"
        f"Artikeltext:\n{article.get('full_text', '')[:2000]}"
    )
    for attempt in range(2):
        try:
            raw = llm.chat(llm.WRITER_MODEL, _BRAINSTORM_SYSTEM, user_msg, temperature=0.9)
            data = llm.parse_json_obj(raw, ["angulos"])
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
            raw = llm.chat(llm.WRITER_MODEL, _BRIDGE_SELECT_SYSTEM, user_msg, temperature=0.3)
            data = llm.parse_json_obj(raw, _BRIDGE_ANGLE_KEYS) or llm.parse_json_obj(raw, ["invalid"])
            if data is not None:
                return data
            logger.warning("Brückenauswahl: Parsing fehlgeschlagen (Versuch %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Brückenauswahl: Fehler (Versuch %d): %s", attempt + 1, e)
    return None


def build_bridge(article: dict, relevance_reason: str = "") -> dict | None:
    """Schritt 2: baut die erzählerische Brücke zwischen Nachricht und Speditionsalltag.

    Zweistufiger Ablauf — 3 Kandidaten-Blickwinkel brainstormen (divergent), dann den
    stärksten kritisch auswählen und schärfen (konvergent) — statt sich in einem Durchgang
    auf einen einzigen Blickwinkel festzulegen. Ein vergleichendes Urteil zwischen
    Alternativen ist eine zuverlässigere Qualitätsschranke, als wenn ein Modell seine
    einzige eigene Ausgabe isoliert bewertet.

    Gibt {"what_happened", "impact_for_forwarders", "risk_or_opportunity",
    "frachtwerk_connection", ...} zurück, bei Fehler None.
    """
    title = article.get("title", "")[:70]

    angles = _brainstorm_angles(article, relevance_reason)
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
            len(angles), title, data.get("reason", "")[:120],
        )
        return None

    impact = data.get("impact_for_forwarders", "").strip()
    risk = data.get("risk_or_opportunity", "").strip()
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
