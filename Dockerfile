# Use a slim Python 3.13 base image
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install system dependencies (e.g., ffmpeg) and clean up in one layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster dependency management
RUN pip install --no-cache-dir uv

# Copy project files
COPY . /app/

# Install project dependencies using uv
RUN uv pip install -e . --system --no-cache

# Set default command to run the application
CMD ["tgmusic"]
