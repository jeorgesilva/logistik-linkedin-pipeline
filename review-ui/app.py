import os

import requests
import streamlit as st
from PIL import Image

ENGINE_API_URL = os.getenv("ENGINE_API_URL", "http://engine-api:8000")

# Anzeigenamen der Status-Werte — die Werte selbst sind API-Werte und bleiben unverändert.
STATUS_LABELS = {
    "all": "Alle",
    "draft": "Entwurf",
    "approved": "Genehmigt",
    "rejected": "Abgelehnt",
    "posted": "Veröffentlicht",
}

st.set_page_config(
    page_title="Logistik LinkedIn — Freigabe-Warteschlange",
    page_icon="📦",
    layout="wide",
)


def api_get(path: str, params: dict = None):
    try:
        r = requests.get(f"{ENGINE_API_URL}{path}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API-Fehler: {e}")
        return None


def api_patch(path: str, body: dict):
    try:
        r = requests.patch(f"{ENGINE_API_URL}{path}", json=body, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API-Fehler: {e}")
        return None


def api_post(path: str, body: dict = None):
    try:
        r = requests.post(f"{ENGINE_API_URL}{path}", json=body or {}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API-Fehler: {e}")
        return None


# --- Seitenleiste ---
st.sidebar.title("📦 Logistik Pipeline")
st.sidebar.markdown("**Freigabe-Warteschlange**")

status_options = ["all", "draft", "approved", "rejected", "posted"]
selected_status = st.sidebar.selectbox(
    "Nach Status filtern",
    status_options,
    format_func=lambda s: STATUS_LABELS.get(s, s),
)

if st.sidebar.button("🔄 Aktualisieren"):
    st.rerun()

if st.sidebar.button("✍️ Fehlende Texte generieren"):
    result = api_post("/posts/regenerate-missing-text")
    if result:
        count = result.get("count", 0)
        if count:
            st.sidebar.info(
                f"Text für {count} Post(s) wird im Hintergrund generiert. "
                "Klicke auf „Aktualisieren“, wenn er fertig ist."
            )
        else:
            st.sidebar.success("Keine Posts mit fehlendem Text.")

if st.sidebar.button("🚀 Pipeline jetzt starten"):
    result = api_post("/run")
    if result and result.get("status") == "started":
        st.sidebar.info("Pipeline gestartet. Klicke auf „Aktualisieren“, wenn sie fertig ist.")

st.sidebar.markdown("---")
st.sidebar.markdown("**Knowledge Base**")
kb_stats = api_get("/kb/stats") or {}
n_examples = kb_stats.get("style_examples", 0)
n_guidelines = kb_stats.get("style_guidelines", 0)
min_ex = kb_stats.get("min_examples_for_rag", 3)
rag_active = kb_stats.get("rag_active", False)

if rag_active:
    st.sidebar.success(f"RAG aktiv — {n_examples} Beispiele / {n_guidelines} Abschnitte")
else:
    st.sidebar.warning(f"RAG inaktiv — {n_examples}/{min_ex} genehmigte Beispiele")

if st.sidebar.button("🔗 Genehmigte synchronisieren"):
    result = api_post("/kb/sync")
    if result:
        count = result.get("count", 0)
        if count:
            st.sidebar.info(f"Synchronisiere {count} Post(s) im Hintergrund.")
        else:
            st.sidebar.info("Keine genehmigten Posts mit Text zum Synchronisieren.")

if st.sidebar.button("📚 Style Guide neu laden"):
    result = api_post("/kb/reload-guidelines")
    if result:
        st.sidebar.info("Style Guide wird im Hintergrund neu geladen.")

# --- Posts laden ---
params = {} if selected_status == "all" else {"status": selected_status}
all_posts = api_get("/posts", params=params) or []

# --- Anzahl je Status ---
all_for_counts = api_get("/posts") or []
counts = {}
for p in all_for_counts:
    s = p.get("status", "unknown")
    counts[s] = counts.get(s, 0) + 1

col1, col2, col3, col4 = st.columns(4)
col1.metric("📝 Entwurf", counts.get("draft", 0))
col2.metric("✅ Genehmigt", counts.get("approved", 0))
col3.metric("❌ Abgelehnt", counts.get("rejected", 0))
col4.metric("📤 Veröffentlicht", counts.get("posted", 0))

st.markdown("---")

if not all_posts:
    st.info("Keine Posts gefunden. Starte die Pipeline, um Entwürfe zu erzeugen.")
    st.stop()

# --- Post-Karten ---
for post in all_posts:
    pid = post["id"]
    status = post.get("status", "")
    status_label = STATUS_LABELS.get(status, status).upper()

    with st.expander(
        f"[{status_label}] #{pid} — {post.get('title','(ohne Titel)')[:80]}",
        expanded=(status == "draft"),
    ):
        left, right = st.columns([1, 2])

        with left:
            image_path = post.get("image_path", "")
            if image_path and os.path.exists(image_path):
                try:
                    img = Image.open(image_path)
                    st.image(img, use_column_width=True)
                except Exception:
                    st.caption("⚠️ Bild konnte nicht geladen werden")
            elif image_path and image_path.startswith("http"):
                st.image(image_path, use_column_width=True)
            else:
                st.caption("Kein Bild")

            if st.button("🖼 Bild neu generieren", key=f"regen_{pid}"):
                with st.spinner("Neues Bild wird generiert…"):
                    result = api_post(f"/posts/{pid}/regenerate-image")
                if result:
                    st.success("Bild neu generiert!")
                    st.rerun()

            st.markdown(f"**Thema:** {post.get('topic','')}")
            if post.get("source_url"):
                st.markdown(f"[Quellartikel ansehen ↗]({post['source_url']})")
            st.caption(f"Erstellt am: {post.get('created_at','')}")

        with right:
            new_text = st.text_area(
                "Post-Text",
                value=post.get("post_text", ""),
                height=220,
                key=f"text_{pid}",
            )

            if post.get("image_prompt"):
                if st.checkbox("Bildprompt anzeigen", key=f"prompt_{pid}"):
                    st.code(post["image_prompt"], language=None)

            btn_col1, btn_col2, btn_col3, btn_col4, btn_col5 = st.columns(5)

            with btn_col1:
                if st.button("💾 Speichern", key=f"save_{pid}"):
                    api_patch(f"/posts/{pid}", {"post_text": new_text})
                    st.success("Gespeichert!")
                    st.rerun()

            with btn_col2:
                if st.button("✅ Genehmigen", key=f"approve_{pid}", type="primary"):
                    api_patch(f"/posts/{pid}", {"post_text": new_text, "status": "approved"})
                    st.success("Genehmigt!")
                    st.rerun()

            with btn_col3:
                if st.button("❌ Ablehnen", key=f"reject_{pid}"):
                    st.session_state[f"rejecting_{pid}"] = True
                    st.rerun()

            with btn_col4:
                if st.button("🔄 Text neu generieren", key=f"regen_text_{pid}"):
                    result = api_post(f"/posts/{pid}/regenerate-text")
                    if result and result.get("status") == "started":
                        st.info("Text wird neu generiert. Klicke auf „Aktualisieren“, wenn er fertig ist.")

            with btn_col5:
                if status == "rejected":
                    if st.button("↩️ Wiederherstellen", key=f"restore_{pid}"):
                        api_patch(f"/posts/{pid}", {"status": "draft"})
                        st.rerun()

            if st.session_state.get(f"rejecting_{pid}"):
                st.markdown("**Warum wird dieser Post abgelehnt?** (Pflichtfeld — bitte konkret, nicht generisch)")
                reason = st.text_area(
                    "Ablehnungsgrund",
                    key=f"reject_reason_{pid}",
                    height=80,
                    label_visibility="collapsed",
                )
                reason_col1, reason_col2 = st.columns(2)
                with reason_col1:
                    if st.button("Ablehnung bestätigen", key=f"confirm_reject_{pid}", type="primary"):
                        if reason.strip():
                            result = api_post(f"/posts/{pid}/reject", {"reason": reason.strip()})
                            if result:
                                del st.session_state[f"rejecting_{pid}"]
                                st.warning("Abgelehnt.")
                                st.rerun()
                        else:
                            st.error("Bitte einen Grund angeben — Pflichtfeld.")
                with reason_col2:
                    if st.button("Abbrechen", key=f"cancel_reject_{pid}"):
                        del st.session_state[f"rejecting_{pid}"]
                        st.rerun()
