"""Bootstrap-Skript — legt freigegebene Seed-Posts in SQLite und in der KB an.

Einmalig im engine-api-Container nach dem ersten Build ausführen:
    docker exec engine-api python ingest_seed_posts.py
"""

import os
import sys

# Sicherstellen, dass der Modulpfad stimmt, wenn das Skript im Container läuft
sys.path.insert(0, os.path.dirname(__file__))

import db
import kb

# ---------------------------------------------------------------------------
# Seed-Posts — bereinigt (LinkedIn-URL-Artefakte entfernt)
# ---------------------------------------------------------------------------

SEED_POSTS = [
    {
        "topic": "Aviação e Infraestrutura",
        "title": "Wenn konventionelle Methoden an ihre Grenzen stossen … fliegen wir weiter.",
        "source_url": "",
        "post_text": (
            "When conventional methods reach their limits… we take to the skies. 🚁⚡\n\n"
            "Critical infrastructure demands uncompromising precision. Where traditional equipment fails "
            "in challenging terrain, we ensure your project's success.\n\n"
            "We specialize in the installation and removal of overhead power lines in remote areas—"
            "precise, highly efficient, and minimally invasive.\n\n"
            "🛠️ Efficient: Rapid logistics without costly access roads.\n"
            "🛡️ Safe: Highest standards for people and nature.\n"
            "🌱 Low Impact: Maximum protection for the environment.\n\n"
            "#Infrastructure #HelicopterOperations #GridExpansion #PowerLines "
            "#EnergySector #CriticalInfrastructure #AviationLogistics\n\n"
            "---\n\n"
            "Wenn konventionelle Methoden an ihre Grenzen stossen … fliegen wir weiter. 🚁⚡\n\n"
            "Kritische Infrastruktur verlangt kompromisslose Präzision. Wo klassische Baumaschinen im "
            "unwegsamen Gelände aufgeben, sichern wir den Erfolg Ihres Projekts.\n\n"
            "Wir unterstützen die Montage und Demontage von Freileitungen in abgelegenen Regionen – "
            "präzise, hocheffizient und minimalinvasiv.\n\n"
            "🛠️ Effizient: Schnelle Logistik ohne teuren Wegebau.\n"
            "🛡️ Sicher: Höchste Standards für Mensch und Natur.\n"
            "🌱 Minimalinvasiv: Maximaler Schutz der Umwelt.\n\n"
            "#Infrastruktur #Helikopter #Freileitungsbau #Netzausbau #Energie"
        ),
    },
    {
        "topic": "Regulação e Soberania de Dados",
        "title": "KI-Modell über Nacht abgeschaltet — Datenhoheit ist kein Buzzword",
        "source_url": "",
        "post_text": (
            "Letzten Freitag wurde eines der leistungsfähigsten KI-Modelle der USA über Nacht weltweit abgeschaltet.\n\n"
            "Nicht wegen eines technischen Defekts – auf behördliche Anordnung. Der Zugang sollte für alle "
            "Nicht-US-Bürger gesperrt werden. Also musste der Anbieter das Modell für alle Kunden vom Netz nehmen.\n\n"
            "Für die meisten ist das eine Tech-Schlagzeile.\n\n"
            "Für eine Spedition ist es etwas anderes: der Beweis, dass ein Werkzeug, auf das man sich verlässt, "
            "von einem Tag auf den anderen verschwinden kann – durch eine Entscheidung, die mit dem eigenen Geschäft "
            "nichts zu tun hat.\n\n"
            "In Ihrem Postfach steckt das Sensibelste, was Sie haben: Sendungsdaten, Preise, Empfängeradressen, "
            "Schadensfälle. Wo diese Daten verarbeitet werden und wer das System im Zweifel abschalten kann, ist "
            "keine IT-Fußnote. Es ist eine Frage von Betriebssicherheit und Vertrauen.\n\n"
            "Genau deshalb bauen wir Frachtwerk auf Datenhoheit: Verarbeitung in Europa, eine Architektur ohne "
            "Abhängigkeit von einem einzigen Anbieter – und der Grundsatz, dass keine Antwort ohne Freigabe "
            "Ihres Disponenten rausgeht.\n\n"
            "Souveränität ist kein Fahnenschwenken. Es ist Risikomanagement.\n\n"
            "Warum europäische KI für Speditionen mehr ist als ein Buzzword – im Blog: https://lnkd.in/erGUDmCJ\n\n"
            "#Logistik #Spedition #KI #Datenhoheit #DSGVO"
        ),
    },
    {
        "topic": "Crise da Cadeia de Suprimentos",
        "title": "75 % des deutschen Straßengüterverkehrs stehen vor dem Kollaps",
        "source_url": "",
        "post_text": (
            "75 % des deutschen Straßengüterverkehrs stehen vor dem Kollaps. Was das für Ihre Lieferkette bedeutet.\n\n"
            "Der deutsche Straßengüterverkehr steckt in der größten Liquiditätskrise seit Jahrzehnten. "
            "Die DVZ Deutsche Verkehrs-Zeitung hat es nüchtern analysiert – und was viele Verlader nicht "
            "sehen wollen: Das ist kein Carrier-Problem. Das ist eine tickende Bombe unter mittelständischen Lieferketten.\n\n"
            "Drei Viertel des deutschen Straßengüterverkehrs werden von kleinen Unternehmen erbracht. "
            "Mauterhöhung, Lohnsteigerungen, Energiepreise, überlange Zahlungsziele großer Auftraggeber – "
            "denen geht im Wortsinn das Geld zum Tanken aus. Fahrzeuge werden stillgelegt. Kapazität verschwindet. "
            "Und sie kommt nicht zurück.\n\n"
            "Was viele übersehen: Die Krise erreicht die Verlader-Seite mit 6–12 Monaten Verzögerung. "
            "Wer heute noch sagt \u201ebei uns l\u00e4uft alles, wir haben gute Konditionen\" \u2013 Vorsicht. "
            "Günstige Preise sind oft das Resultat eines Dienstleisters, der seine Substanz verbrennt. "
            "Wenn der kippt, kippt Ihre Lieferkette. Ohne Vorwarnung. Genau dann, wenn Sie ihn am dringendsten brauchen.\n\n"
            "Drei Frühwarnsignale, auf die Sie jetzt achten sollten:\n"
            "→ Ihr Carrier bittet plötzlich um kürzere Zahlungsziele oder Vorauszahlungen\n"
            "→ Disponenten und Fahrer wechseln auffällig häufig\n"
            "→ Servicequalität bricht ein – Verspätungen, Rückfragen, schlechte Erreichbarkeit\n\n"
            "Ein Signal ist kein Drama. Mehrere gleichzeitig sind ein Alarm.\n\n"
            "Was jetzt zählt: Partnerschaft statt Preiskampf. Das ist kein Soft Topic, sondern "
            "betriebswirtschaftliche Vernunft. Es gibt schlicht nicht mehr genug Wettbewerber, "
            "die man gegeneinander ausspielen könnte.\n\n"
            "Einer der größten Hebel, der in der Praxis unterschätzt wird: die Kommunikation zwischen "
            "Verlader und Dienstleister. Status, Avisierung, Eskalation – das frisst auf beiden Seiten "
            "Stunden, die niemand mehr hat. Wer hier automatisiert, senkt Kosten auf beiden Seiten und "
            "macht aus einer transaktionalen Beziehung eine belastbare Partnerschaft.\n\n"
            "Im Blogpost gehen wir tiefer rein: Frühwarnindikatoren, vier konkrete Schritte, "
            "wie Sie Ihr Portfolio jetzt absichern. 👉 https://lnkd.in/emEFz8GH\n\n"
            "#logistik #speditionen #kommunikation #frachtwerk"
        ),
    },
    {
        "topic": "Dados de Mercado e KI",
        "title": "17,8 % der Weltbevölkerung nutzt KI — in deutschen Dispoabteilungen?",
        "source_url": "",
        "post_text": (
            "17,8 % der arbeitsfähigen Weltbevölkerung nutzt KI. In deutschen Dispoabteilungen? "
            "Vermutlich deutlich weniger.\n\n"
            "Microsoft hat am 7. Mai den Global AI Diffusion Report 2026 Q1 veröffentlicht. "
            "Die Zahlen, die uns aufhorchen lassen:\n\n"
            "🌍 +1,5 Prozentpunkte globale KI-Nutzung in einem einzigen Quartal\n"
            "🇦🇪 VAE führen mit 70,1 % – Deutschland nicht in den Top-Rängen\n"
            "📈 78 % mehr Code-Pushes weltweit durch KI-Coding-Tools\n"
            "🔀 Schere zwischen Global North (27,5 %) und Global South (15,4 %) wächst weiter\n\n"
            "Besonders interessant für uns in der Logistik: Asien zieht massiv an, sobald die "
            "Sprachmodelle in der Landessprache funktionieren. Für deutschsprachige Speditionen heißt "
            "das – die technische Barriere ist gefallen. Was fehlt, ist die operative Integration.\n\n"
            "Genau hier setzen wir mit FRACHTWERK an. Routine-Dispo wie Statusabfragen, ETA-Updates, "
            "Rückfragen aus dem Postfach, Stammdatenpflege – Vorgänge, die jeden Tag Stunden verschlingen "
            "– werden automatisch entschieden und ausgeführt. Über TMS, Telematik und Postfach hinweg. "
            "DSGVO-konform, deutsche Cloud.\n\n"
            "Die Daten aus dem Microsoft-Report bestätigen: Wer jetzt nicht in produktive KI-Anwendungen "
            "geht, fällt nicht morgen zurück – sondern hat es bereits.\n\n"
            "👉 Originalreport: https://lnkd.in/gw98JyFZ\n\n"
            "#Logistik #Spedition #KI #Disposition #Mittelstand #Automatisierung"
        ),
    },
    {
        "topic": "Insolvência e Crise Estrutural",
        "title": "2,907 logistics companies went insolvent in Germany in 2025",
        "source_url": "",
        "post_text": (
            "2,907 logistics companies went insolvent in Germany in 2025.\n\n"
            "Not because they built bad businesses. Because the system around them stopped working.\n\n"
            "Wandt. 85 years old. 134 employees. Gone. Hermesmann. Nearly 70 years in operation. Gone.\n\n"
            "These companies survived wars, oil crises, and financial crashes. What they couldn't survive "
            "was the combination of razor-thin margins, labor shortages — and an operational overhead "
            "that never stopped growing.\n\n"
            "Here's what the insolvency statistics don't show: the average freight forwarder processes "
            "hundreds of emails per day. Manually. Shipment requests, quotes, status updates — copy-pasted "
            "by dispatchers who are already the most stretched people in the building.\n\n"
            "That's the problem FRACHTWERK is built to fix.\n\n"
            "We automate email processing for freight forwarders and transport companies — extracting data, "
            "routing requests, generating responses — directly from the inbox, directly into existing "
            "workflows. No migration. No retraining. Just the hours back that operators are currently "
            "bleeding every single day.\n\n"
            "The structural crisis in German logistics isn't only about succession. It's about whether "
            "these businesses stay fundable long enough for any transition to happen at all.\n\n"
            "Operational efficiency has become survival infrastructure.\n\n"
            "186,000 businesses in Germany need a successor by 2027. In logistics, the rate is even "
            "higher. The ones that make it to that conversation will be the ones that found ways to do "
            "more with less — before it was too late.\n\n"
            "That's who we're building for."
        ),
    },
]

# ---------------------------------------------------------------------------


def main():
    db.init_db()
    print(f"Ingesting {len(SEED_POSTS)} seed posts into SQLite + KB...\n")

    for i, p in enumerate(SEED_POSTS, 1):
        # Als freigegebenen Post in SQLite anlegen (überspringen, wenn source_url schon existiert)
        post_id = db.insert_post(
            topic=p["topic"],
            source_url=p.get("source_url", ""),
            title=p["title"],
            post_text=p["post_text"],
            status="approved",
        )
        print(f"[{i}/{len(SEED_POSTS)}] SQLite → post_id={post_id}: {p['title'][:60]}")

        # In die ChromaDB-KB einlesen
        ok = kb.ingest_post(
            post_id=post_id,
            topic=p["topic"],
            title=p["title"],
            post_text=p["post_text"],
        )
        status = "✓ KB ingested" if ok else "✗ KB ingest failed"
        print(f"           {status}")

    stats = kb.get_stats()
    print(f"\nKB stats after bootstrap: {stats}")
    print(
        f"\nRAG is {'ACTIVE' if stats.get('rag_active') else 'INACTIVE — add more approved posts'}."
    )


if __name__ == "__main__":
    main()
