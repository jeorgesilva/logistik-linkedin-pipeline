import os

import requests
import streamlit as st
from PIL import Image

ENGINE_API_URL = os.getenv("ENGINE_API_URL", "http://engine-api:8000")

st.set_page_config(
    page_title="Logistik LinkedIn — Review Queue",
    page_icon="📦",
    layout="wide",
)


def api_get(path: str, params: dict = None):
    try:
        r = requests.get(f"{ENGINE_API_URL}{path}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def api_patch(path: str, body: dict):
    try:
        r = requests.patch(f"{ENGINE_API_URL}{path}", json=body, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def api_post(path: str, body: dict = None):
    try:
        r = requests.post(f"{ENGINE_API_URL}{path}", json=body or {}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


# --- Seitenleiste ---
st.sidebar.title("📦 Logistik Pipeline")
st.sidebar.markdown("**Review Queue**")

status_options = ["all", "draft", "approved", "rejected", "posted"]
selected_status = st.sidebar.selectbox("Filter by status", status_options)

if st.sidebar.button("🔄 Refresh"):
    st.rerun()

if st.sidebar.button("✍️ Generate Missing Texts"):
    result = api_post("/posts/regenerate-missing-text")
    if result:
        count = result.get("count", 0)
        if count:
            st.sidebar.info(f"Generating text for {count} post(s) in background. Click Refresh when done.")
        else:
            st.sidebar.success("No posts with missing text.")

if st.sidebar.button("🚀 Run Pipeline Now"):
    result = api_post("/run")
    if result and result.get("status") == "started":
        st.sidebar.info("Pipeline gestartet. Klicken Sie auf Refresh, wenn er fertig ist.")

st.sidebar.markdown("---")
st.sidebar.markdown("**Knowledge Base**")
kb_stats = api_get("/kb/stats") or {}
n_examples = kb_stats.get("style_examples", 0)
n_guidelines = kb_stats.get("style_guidelines", 0)
min_ex = kb_stats.get("min_examples_for_rag", 3)
rag_active = kb_stats.get("rag_active", False)

if rag_active:
    st.sidebar.success(f"RAG aktiv — {n_examples} Beispiele / {n_guidelines} Chunks")
else:
    st.sidebar.warning(f"RAG inaktiv — {n_examples}/{min_ex} genehmigte Beispiele")

if st.sidebar.button("🔗 Sync Approved Posts"):
    result = api_post("/kb/sync")
    if result:
        count = result.get("count", 0)
        if count:
            st.sidebar.info(f"Synchronisiere {count} Post(s) im Hintergrund.")
        else:
            st.sidebar.info("Keine genehmigten Posts mit Text zum Synchronisieren.")

if st.sidebar.button("📚 Reload Style Guide"):
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
col1.metric("📝 Draft", counts.get("draft", 0))
col2.metric("✅ Approved", counts.get("approved", 0))
col3.metric("❌ Rejected", counts.get("rejected", 0))
col4.metric("📤 Posted", counts.get("posted", 0))

st.markdown("---")

if not all_posts:
    st.info("No posts found. Run the pipeline to generate drafts.")
    st.stop()

# --- Post-Karten ---
for post in all_posts:
    pid = post["id"]

    with st.expander(
        f"[{post.get('status','').upper()}] #{pid} — {post.get('title','(no title)')[:80]}",
        expanded=(post.get("status") == "draft"),
    ):
        left, right = st.columns([1, 2])

        with left:
            image_path = post.get("image_path", "")
            if image_path and os.path.exists(image_path):
                try:
                    img = Image.open(image_path)
                    st.image(img, use_column_width=True)
                except Exception:
                    st.caption("⚠️ Image could not be loaded")
            elif image_path and image_path.startswith("http"):
                st.image(image_path, use_column_width=True)
            else:
                st.caption("No image")

            if st.button("🖼 Regenerate Image", key=f"regen_{pid}"):
                with st.spinner("Generating new image…"):
                    result = api_post(f"/posts/{pid}/regenerate-image")
                if result:
                    st.success("Image regenerated!")
                    st.rerun()

            st.markdown(f"**Topic:** {post.get('topic','')}")
            if post.get("source_url"):
                st.markdown(f"[Source article]({post['source_url']})")
            st.caption(f"Created: {post.get('created_at','')}")

        with right:
            new_text = st.text_area(
                "Post text",
                value=post.get("post_text", ""),
                height=220,
                key=f"text_{pid}",
            )

            if post.get("image_prompt"):
                if st.checkbox("Show image prompt", key=f"prompt_{pid}"):
                    st.code(post["image_prompt"], language=None)

            btn_col1, btn_col2, btn_col3, btn_col4, btn_col5 = st.columns(5)

            with btn_col1:
                if st.button("💾 Save", key=f"save_{pid}"):
                    api_patch(f"/posts/{pid}", {"post_text": new_text})
                    st.success("Gespeichert!")
                    st.rerun()

            with btn_col2:
                if st.button("✅ Approve", key=f"approve_{pid}", type="primary"):
                    api_patch(f"/posts/{pid}", {"post_text": new_text, "status": "approved"})
                    st.success("Genehmigt!")
                    st.rerun()

            with btn_col3:
                if st.button("❌ Reject", key=f"reject_{pid}"):
                    st.session_state[f"rejecting_{pid}"] = True
                    st.rerun()

            with btn_col4:
                if st.button("🔄 Regenerate Text", key=f"regen_text_{pid}"):
                    result = api_post(f"/posts/{pid}/regenerate-text")
                    if result and result.get("status") == "started":
                        st.info("Text wird neu generiert. Klicken Sie auf Refresh wenn fertig.")

            with btn_col5:
                if post.get("status") == "rejected":
                    if st.button("↩️ Restore", key=f"restore_{pid}"):
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
