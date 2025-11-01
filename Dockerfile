FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN apt-get update && apt-get install -y \
        gawk \
        poppler-utils \
        tesseract-ocr \
        ghostscript \
        qpdf \
        pngquant \
    && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Pre-cache the sentence-transformer model
# This runs the script to download the model during the build
RUN python precache_model.py

# Convert line endings to Unix format to prevent script errors
RUN sed -i 's/\r$//' start.sh

# Make start script executable
RUN chmod +x start.sh

# Expose port for local use (optional)
ENV PORT=8000

# Run start script
CMD ["./start.sh"]
