"""Screenshots der Review-UI und des Dashboards für README/Portfolio aufnehmen.

Manueller Schritt: Das Skript startet die Stack NICHT, lädt keine Modelle und führt die
Pipeline nicht aus. Es erwartet, dass engine-api und review-ui bereits laufen und schon ein
paar Posts in der Datenbank stehen (z. B. nach einem `POST /run`).

Einrichtung (einmalig, auf dem Host — nicht im Container):
    pip install playwright
    playwright install chromium

Aufruf:
    python scripts/capture_demo.py
    python scripts/capture_demo.py --out /tmp/shots --skip-dashboard

Aufgenommen werden:
    review_ui_queue.png      Freigabe-Warteschlange (Kennzahlen + Post-Liste)
    review_ui_post.png       ein aufgeklappter Post (Bild, Text, Aktionen)
    review_ui_sidebar.png    Seitenleiste mit Knowledge-Base-Status (RAG aktiv/inaktiv)
    dashboard_overview.png   statisches Dashboard (cloudflare-dashboard/public)
    dashboard_post.png       Dashboard mit geöffneter Post-Detailansicht

Das Dashboard wird ohne Webserver und ohne Änderung an der .env geladen: Playwright liefert
die Dateien aus cloudflare-dashboard/public direkt aus, blockiert die Abfrage beim
Cloudflare Worker (damit keine echte Tunnel-URL übernommen wird) und leitet API-Aufrufe an
--api-url weiter, inklusive CORS-Header. CORS_ALLOWED_ORIGINS muss dafür nicht gesetzt sein.
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
from pathlib import Path

try:
    from playwright.sync_api import Page, Route, sync_playwright
except ImportError:
    sys.exit("Playwright fehlt: pip install playwright && playwright install chromium")

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = ROOT / "cloudflare-dashboard" / "public"
DASHBOARD_ORIGIN = "http://dashboard.local"  # fiktiver Origin, wird komplett per Route bedient
STORAGE_KEY = "logistik_api_base_url"  # muss zu STORAGE_KEY in cloudflare-dashboard/public/app.js passen
VIEWPORT = {"width": 1440, "height": 900}


def capture_review_ui(page: Page, url: str, out: Path) -> None:
    page.goto(url, wait_until="networkidle")
    # Streamlit rendert clientseitig; erst weiter, wenn die Kennzahlen sichtbar sind.
    page.get_by_text("Genehmigt").first.wait_for(timeout=30_000)
    page.wait_for_timeout(1500)
    page.screenshot(path=out / "review_ui_queue.png")
    print("✓ review_ui_queue.png")

    page.locator('[data-testid="stSidebar"]').screenshot(path=out / "review_ui_sidebar.png")
    print("✓ review_ui_sidebar.png")

    expanders = page.locator('[data-testid="stExpander"]')
    if expanders.count() == 0:
        print("– keine Posts vorhanden, review_ui_post.png übersprungen")
        return
    first = expanders.first
    # Streamlit rendert auch zugeklappte Expander (nur versteckt) — daher Sichtbarkeit prüfen.
    # Entwürfe sind schon aufgeklappt, alle anderen Status nicht.
    if not first.locator("textarea").first.is_visible():
        first.locator("summary").click()
    first.locator("textarea").first.wait_for(timeout=15_000)
    page.wait_for_timeout(1500)  # Bild nachladen lassen
    first.screenshot(path=out / "review_ui_post.png")
    print("✓ review_ui_post.png")


def _serve_dashboard_file(route: Route) -> None:
    rel = route.request.url.removeprefix(DASHBOARD_ORIGIN).split("?")[0].lstrip("/") or "index.html"
    path = (DASHBOARD_DIR / rel).resolve()
    if DASHBOARD_DIR.resolve() not in path.parents or not path.is_file():
        route.fulfill(status=404, body="not found")
        return
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    route.fulfill(status=200, body=path.read_bytes(), content_type=content_type)


def _proxy_api(route: Route) -> None:
    headers = {"access-control-allow-origin": "*", "access-control-allow-headers": "*",
               "access-control-allow-methods": "GET, POST, PATCH, OPTIONS"}
    if route.request.method == "OPTIONS":
        route.fulfill(status=204, headers=headers)
        return
    response = route.fetch()  # läuft außerhalb des Browsers, daher kein CORS-Problem
    route.fulfill(response=response, headers={**response.headers, **headers})


def capture_dashboard(page: Page, api_url: str, out: Path) -> None:
    api_url = api_url.rstrip("/")
    page.add_init_script(f"localStorage.setItem({STORAGE_KEY!r}, {api_url!r});")
    page.route("**/api/tunnel-url", lambda route: route.fulfill(status=404, body=""))
    page.route(f"{DASHBOARD_ORIGIN}/**", _serve_dashboard_file)
    page.route(f"{api_url}/**", _proxy_api)

    page.goto(f"{DASHBOARD_ORIGIN}/index.html")
    page.locator("#main-screen").wait_for(state="visible", timeout=15_000)
    cards = page.locator(".post-card")
    try:
        cards.first.wait_for(timeout=15_000)
    except Exception:  # noqa: BLE001 — leere Datenbank ist kein Fehler, nur ein leerer Screenshot
        print("– keine Post-Karten im Dashboard gefunden")
    page.wait_for_timeout(2000)  # Bilder und KB-Status nachladen lassen
    page.screenshot(path=out / "dashboard_overview.png")
    print("✓ dashboard_overview.png")

    if cards.count() == 0:
        return
    cards.first.click()
    page.locator("#drawer-content").locator("*").first.wait_for(timeout=15_000)
    page.wait_for_timeout(1500)
    page.screenshot(path=out / "dashboard_post.png")
    print("✓ dashboard_post.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--review-ui-url", default="http://localhost:8503")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "screenshots")
    parser.add_argument("--skip-dashboard", action="store_true")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=2, locale="de-DE")
        capture_review_ui(context.new_page(), args.review_ui_url, args.out)
        if not args.skip_dashboard:
            capture_dashboard(context.new_page(), args.api_url, args.out)
        browser.close()
    print(f"Screenshots gespeichert in {args.out}")


if __name__ == "__main__":
    main()
