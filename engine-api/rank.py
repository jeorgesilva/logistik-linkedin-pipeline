import logging
import os

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

DEDUP_THRESHOLD = float(os.getenv("DEDUP_THRESHOLD", "0.85"))


def _article_text(article: dict) -> str:
    return f"{article.get('title', '')} {article.get('full_text', '')[:500]}"


def dedup_and_rank(articles: list[dict], top_n: int = 10) -> list[dict]:
    if not articles:
        return []

    texts = [_article_text(a) for a in articles]

    try:
        vectorizer = TfidfVectorizer(max_features=5000, sublinear_tf=True)
        tfidf = vectorizer.fit_transform(texts)
        sim = cosine_similarity(tfidf)
    except Exception as e:
        logger.warning("TF-IDF fehlgeschlagen (%s); die ersten %d Artikel werden zurückgegeben.", e, top_n)
        return articles[:top_n]

    n = len(articles)
    kept: list[int] = []
    dropped: set[int] = set()

    for i in range(n):
        if i in dropped:
            continue
        kept.append(i)
        if len(kept) >= top_n:
            break
        for j in range(i + 1, n):
            if j not in dropped and sim[i, j] > DEDUP_THRESHOLD:
                dropped.add(j)

    selected = [articles[i] for i in kept]
    logger.info(
        "dedup_and_rank: %d → %d Artikel (Schwellenwert=%.2f)",
        n,
        len(selected),
        DEDUP_THRESHOLD,
    )
    return selected
