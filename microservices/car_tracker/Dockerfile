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

# Copy and install Python dependencies
# 1. First install PyTorch CPU wheel (so ultralytics sees torch already)
RUN pip install --no-cache-dir \
      torch==2.0.1+cpu torchvision==0.15.2+cpu \
      --extra-index-url https://download.pytorch.org/whl/cpu

# 2. Install ultralytics *without* its dependencies
RUN pip install --no-cache-dir ultralytics --no-deps

# 3. Install the rest of your requirements (excluding ultralytics)
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
