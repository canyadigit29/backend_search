# precache_model.py
from sentence_transformers import CrossEncoder

# This script is run during the Docker build process.
# Its purpose is to download the Cross-Encoder model and save it
# into the Docker image layer. This prevents the model from being
# downloaded every time the application starts, which is slow and
# can fail in environments with limited network access.

print("Starting model pre-caching...")
try:
    # Instantiate the model to trigger the download
    model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
    print("Model 'cross-encoder/ms-marco-MiniLM-L-6-v2' downloaded and cached successfully.")
except Exception as e:
    print(f"Error during model pre-caching: {e}")
    # Exit with a non-zero status code to fail the build if caching fails
    exit(1)

