FROM python:3.9-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
      gcc \
      libglib2.0-0 \
      libsm6 \
      libxrender1 \
      libxext6 \
      libgl1-mesa-glx \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
      flask \
      kafka-python \
      opencv-python-headless \
      "numpy<2.0" \
      pandas \
      tqdm \
      PyYAML \
      scipy

COPY app.py .

RUN mkdir -p debug_output

EXPOSE 5000

CMD ["python", "app.py"]
