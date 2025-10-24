FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Convert line endings to Unix format to prevent script errors
RUN sed -i 's/\r$//' start.sh

# Make start script executable
RUN chmod +x start.sh

# Expose port for local use (optional)
ENV PORT=8000

# Run start script
CMD ["./start.sh"]
