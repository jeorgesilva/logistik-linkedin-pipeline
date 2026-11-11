"""Gemeinsame Prompt-Bausteine: Überzeugungsbank, Leserprofil, verbotene Phrasen, Drittfirmen-Regeln."""


# Verdichtet aus data/style_guide/06_point_of_view.md — direkt in die Bridge-/Writer-
# Prompts eingebettet statt dem RAG-Retrieval überlassen. RAG liefert pro Aufruf nur 2 von
# 39 Guideline-Abschnitten, daher tauchte ein grundlegendes, immer benötigtes Element wie die
# Überzeugungsbank nur unzuverlässig auf; Posts bestanden dann aus Fakt + generischem
# „Frachtwerk hilft dabei ...“-Anhängsel statt einer echt argumentierten Position. Mit
# 06_point_of_view.md synchron halten, wenn sich die Überzeugungsbank ändert.
POV_BELIEFS = """\
1. Kommunikation ist die teuerste, unsichtbarste Kostenstelle in der Logistik — nicht Visibility-Dashboards oder Routenoptimierung.
2. Eine KI, die nur Vorschläge macht, ist keine Automatisierung — sie ist ein weiterer Klick, keine Entlastung.
3. TMS und Telematik dokumentieren die Kette. Entschieden wird im Postfach, nicht im Dashboard.
4. Ein niedriger Preis eines Dienstleisters ist meist Symptom für Substanzverbrennung, nicht ein Verhandlungserfolg.
5. Datenhoheit ist operatives Risikomanagement, keine politische Position — dieselbe Frage wie bei jedem kritischen Lieferanten.
6. Generische KI versteht Sprache, nicht Spedition — Branchenkontext ist kein Plugin, sondern das Produkt selbst.
7. Die Mittelstandskrise in der Logistik ist strukturell, nicht individuell — und trifft Einkäufer mit 6–12 Monaten Verzögerung.
8. Unter 2% der Logistikbetriebe laufen heute KI-gestützt — die technische Hürde ist gefallen, es fehlt an operativer Integration, nicht an Hype."""

WRITER_READER_PROFILE = """\
LESER: Disponent oder Inhaber einer mittelständischen Spedition. Denkt in: Kunden die wegen Lieferverzug anrufen, Frachtraten, Kundendaten im Postfach, Tagesgeschäft, Regulierung die seinen Betrieb trifft — nicht in "Logistik allgemein"."""

FORBIDDEN_MARKETING_PHRASES = [
    "innovative Lösung", "ganzheitlich", "Synergien",
    "Wir freuen uns zu verkünden", "revolutionär", "state of the art",
]
FORBIDDEN_INSTITUTIONAL_PHRASES = [
    "unsere Mission ist es", "wir sind stolz darauf", "unser Ansatz",
    "wir helfen Ihnen dabei", "hilft dabei", "wir freuen uns, dass",
]
FORBIDDEN_PHRASES_BLOCK = (
    "VERBOTENE PHRASEN (Marketing-Floskeln): "
    + ", ".join(f'"{p}"' for p in FORBIDDEN_MARKETING_PHRASES)
    + ".\nVERBOTENE PHRASEN (institutionelles Blabla — klingt nach Unternehmensmeldung statt nach Position): "
    + ", ".join(f'"{p}"' for p in FORBIDDEN_INSTITUTIONAL_PHRASES)
    + "."
)

WRITER_THIRD_PARTY_RULE = """\
DRITTFIRMEN: nie eine im Artikel genannte private Firma (außer Frachtwerk) für ihre Leistungen, Lösungen oder Erfolge loben oder hervorheben — immer analytisch und kritisch bleiben (Risiken, Trade-offs, Bedeutung für Wettbewerber), nie im Ton einer Erfolgsgeschichte oder Werbung."""

THIRD_PARTY_RULE = """\
REGEL ÜBER DRITTFIRMEN:
Wenn der Artikel eine bestimmte private Firma nennt (außer Frachtwerk), behandle sie nur als \
illustratives Beispiel eines größeren Branchenmusters — nie als Protagonistin oder \
"Erfolgsgeschichte". Übernimm nie Lob, Superlative oder werbliche Sprache des Quellartikels \
über diese Firma. Generalisiere wo möglich: statt "Firma X hat Y gemacht" schreibe "manche \
Speditionen machen Y" oder "die Branche bewegt sich in Richtung Y" — außer der Firmenname ist \
für die Glaubwürdigkeit des Fakts zwingend nötig."""


def find_forbidden_phrases(text: str) -> list[str]:
    lowered = text.lower()
    return [
        p for p in FORBIDDEN_MARKETING_PHRASES + FORBIDDEN_INSTITUTIONAL_PHRASES
        if p.lower() in lowered
    ]
