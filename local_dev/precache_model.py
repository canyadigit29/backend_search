# (Legacy) precache_model.py
from sentence_transformers import CrossEncoder

print("Starting model pre-caching...")
try:
    model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
    print("Model 'cross-encoder/ms-marco-MiniLM-L-6-v2' downloaded and cached successfully.")
except Exception as e:
    print(f"Error during model pre-caching: {e}")
    exit(1)
