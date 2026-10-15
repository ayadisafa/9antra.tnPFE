from sentence_transformers import SentenceTransformer

# Global variable to cache the loaded model
_model = None

def get_similarity_model() -> SentenceTransformer:
    """
    Loads and caches the SentenceTransformer model in memory.
    """
    global _model
    if _model is None:
        print("[INFO] Loading SentenceTransformer model ('all-MiniLM-L6-v2')...")
        _model = SentenceTransformer('all-MiniLM-L6-v2')
    return _model 