from agents.writer import build_bridge, evaluate_relevance, generate_post

ARTICLES = [
    {
        "title": "US regulation forces AI shutdown worldwide",
        "full_text": (
            "Last Friday, one of the most powerful AI models was shut down globally "
            "due to regulatory action by the US government. The model had been used in "
            "dozens of countries for various consumer and enterprise applications. "
            "Experts say this sets a precedent for AI governance internationally."
        ),
    },
    {
        "title": "EU-Regulierung zur Lkw-Maut: Neue Kilometerpauschalen ab Januar 2026",
        "full_text": (
            "Die Europäische Union hat neue Regelungen zur Lkw-Maut verabschiedet, die ab "
            "Januar 2026 in Kraft treten. Schwere Nutzfahrzeuge über 7,5 Tonnen müssen auf "
            "allen Bundesautobahnen und wichtigen Bundesstraßen eine erhöhte Kilometerpauschale "
            "entrichten. Die neuen Sätze betragen je nach Emissionsklasse zwischen 0,24 und "
            "0,38 Euro pro Kilometer. Speditionen müssen ihre Frachtpreise entsprechend "
            "kalkulieren. Der Bundesverband Güterkraftverkehr Logistik und Entsorgung (BGL) "
            "schätzt, dass die durchschnittlichen Transportkosten für eine Spedition um 8 bis "
            "12 Prozent steigen werden. Kleinere Speditionen ohne Preissetzungsmacht "
            "befürchten, Kundenaufträge zu verlieren, wenn sie die Mehrkosten weitergeben."
        ),
    },
    {
        "title": "Hafenstreik Hamburg: Containerumschlag bricht um 40% ein",
        "full_text": (
            "Im Hamburger Hafen hat ein mehrtägiger Streik der Hafenarbeiter zu einem "
            "Einbruch des Containerumschlags um 40 Prozent geführt. Die Gewerkschaft ver.di "
            "fordert eine Lohnerhöhung von 3 Euro pro Stunde. Reedereien wie Maersk und MSC "
            "leiten Schiffe bereits nach Bremen, Rotterdam und Antwerpen um. "
            "Erste Speditionen berichten von Lieferverzögerungen von 5 bis 10 Tagen für "
            "Importe aus Asien. Besonders betroffen sind Branchen mit just-in-time-Lieferungen "
            "wie Automobilzulieferer und Elektronikhersteller. Disponenten müssen kurzfristig "
            "Alternativen für Sendungen finden, die ursprünglich über Hamburg geplant waren. "
            "Laut Prognosen könnte der Streik noch bis zu zwei Wochen andauern. "
            "Einige Speditionen erwägen bereits den Luftfrachtweg für zeitkritische Ware."
        ),
    },
    {
        "title": "Weltbank: Globale Lieferketten erholen sich nach COVID-Schocks",
        "full_text": (
            "Ein neuer Weltbank-Bericht zeigt, dass sich globale Lieferketten vier Jahre "
            "nach den COVID-19-Unterbrechungen weitgehend normalisiert haben. "
            "Die durchschnittlichen Containerraten sind auf Vorkrisenniveau zurückgekehrt. "
            "Laut dem Bericht haben Unternehmen weltweit ihre Resilienzstrategien verbessert "
            "und mehr Lagerbestände aufgebaut. Experten sehen dies als positives Signal "
            "für den globalen Handel insgesamt."
        ),
    },
]

print(f"Fila: {len(ARTICLES)} artigo(s) para testar\n")
print("=" * 60)

for i, article in enumerate(ARTICLES, 1):
    print(f"\n[{i}/{len(ARTICLES)}] '{article['title'][:70]}'")
    print("-" * 60)

    rel = evaluate_relevance(article)
    rel_flag = rel.get("relevant") if rel else False
    print(f"RELEVANCE → {rel_flag} | {(rel.get('reason', '') if rel else 'parse error')[:120]}")

    if not rel or not rel_flag:
        print("→ DESCARTADO (relevance). Próximo artigo.\n")
        continue

    bridge = build_bridge(article, rel.get("reason", ""))
    if not bridge:
        print("→ DESCARTADO (bridge inválida ou impacto genérico). Próximo artigo.\n")
        continue

    print(f"BRIDGE impact → {bridge.get('impact_for_forwarders', '')[:120]}")

    post = generate_post(article, bridge)
    if not post:
        print("→ DESCARTADO (writer vazio). Próximo artigo.\n")
        continue

    print("\n" + "=" * 60)
    print("POST GERADO:")
    print("=" * 60)
    print(post)
    print("=" * 60)
    print(f"\n✓ Pipeline completo com artigo [{i}]: '{article['title'][:70]}'")
    break
else:
    print("\n✗ Nenhum artigo passou por todos os filtros.")
