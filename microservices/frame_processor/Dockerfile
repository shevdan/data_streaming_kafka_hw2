# Dockerfile for car_detector
FROM python:3.9-slim

# Install system dependencies for OpenCV and Kafka client
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
      gcc \
      libglib2.0-0 \
      libsm6 \
      libxrender1 \
      libxext6 \
      libgl1-mesa-glx \
  && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# 3. Install the rest of the requirements
RUN pip install --no-cache-dir \
      flask \
      kafka-python \
      opencv-python-headless \
      "numpy<2.0" \
      pandas \
      tqdm \
      PyYAML \
      scipy

# Copy application code
COPY app.py .

# Create directory for CSV debug output
RUN mkdir -p debug_output

# Expose Flask port
EXPOSE 5000

# Default command
CMD ["python", "app.py"]
