# microservices/data_generator/app.py
import os
import sys
import time
import base64
import json
import logging
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable
from sqlalchemy import create_engine, text
import cv2

# ── Logging ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)

logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Configuration from ENV ────────────────────────────────────────────
SERVICE_NAME     = os.getenv('SERVICE_NAME',    'Data Generator')
KAFKA_BROKER     = os.getenv('KAFKA_BROKER',    'localhost:9092')
DATASET_ROOT_DIR = os.getenv('DATASET_ROOT_DIR')
VID_SUBDIR       = os.getenv('VID_SUBDIR',      'vids')
VIDEO_FILE       = os.getenv('VIDEO_FILE')      # e.g. "vid_01.mp4"
DATA_TOPIC       = os.getenv('DATA_TOPIC',      'frames')
TWO_PRODUCERS    = os.getenv('TWO_PRODUCERS','False').lower()=='true'

POSTGRES_DB      = os.getenv('POSTGRES_DB')
POSTGRES_USER    = os.getenv('POSTGRES_USER')
POSTGRES_PASS    = os.getenv('POSTGRES_PASSWORD')

# ── Validate essential vars ───────────────────────────────────────────
if not DATASET_ROOT_DIR or not VIDEO_FILE:
    logger.error("You must set DATASET_ROOT_DIR and VIDEO_FILE in the environment")
    sys.exit(1)

# ── Postgres connection ───────────────────────────────────────────────
db_url = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASS}@postgres:5432/{POSTGRES_DB}"
engine = create_engine(db_url)

# ── Kafka producers ───────────────────────────────────────────────────
def make_producer(servers, retries=10, delay=3):
    for i in range(retries):
        try:
            return KafkaProducer(
                bootstrap_servers=servers,
                value_serializer=lambda v: json.dumps(v).encode()
            )
        except NoBrokersAvailable:
            logger.warning(f"[Kafka] broker not ready, retry {i+1}/{retries}")
            time.sleep(delay)
    raise RuntimeError("Kafka broker unavailable")

producer      = make_producer(KAFKA_BROKER)
second_prod   = make_producer(KAFKA_BROKER) if TWO_PRODUCERS else None

def choose_producer(idx):
    return second_prod if (second_prod and idx%2==1) else producer

# ── Health & Data Status ──────────────────────────────────────────────
@app.route('/')
def health():
    return f"{SERVICE_NAME} up"

@app.route('/status')
def status():
    return jsonify({
        'service_name': SERVICE_NAME,
        'python_version': sys.version,
        'kafka_broker': KAFKA_BROKER,
        'video_file': VIDEO_FILE
    })

@app.route('/data_status')
def data_status():
    root = DATASET_ROOT_DIR
    path = os.path.join(root, VID_SUBDIR, VIDEO_FILE)
    ok = os.path.isfile(path)
    return jsonify({
        'dataset_root': root,
        'video_path': path,
        'exists': ok
    }), (200 if ok else 400)

# ── Frame streaming endpoint ──────────────────────────────────────────
@app.route('/start', methods=['POST'])
def start_stream():
    video_path = os.path.join(DATASET_ROOT_DIR, VID_SUBDIR, VIDEO_FILE)
    video_id   = os.path.splitext(VIDEO_FILE)[0]

    if not os.path.isfile(video_path):
        return jsonify({'error': f'Video not found: {video_path}'}), 404

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return jsonify({'error': 'Cannot open video file'}), 500

    frame_count = 0
    idx = 0
    logger.info(f"[{SERVICE_NAME}] Streaming {VIDEO_FILE}…")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # JPEG–encode for transport
        success, buf = cv2.imencode('.jpg', frame)
        if not success:
            logger.error(f"Failed to encode frame {frame_count}")
            continue

        frame_bytes = buf.tobytes()
        timestamp   = datetime.now(timezone.utc).isoformat()
        frame_id    = f'{video_id}_frame_{frame_count:06d}'
        prod        = choose_producer(idx)

        # Save to Postgres + disk
        path = f"/app/frame_store/{video_id}_{frame_count:06d}.jpg"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(frame_bytes)

        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO frames(frame_id, video_id, timestamp, path)
                VALUES(:fid, :vid, :ts, :pth)
                ON CONFLICT DO NOTHING
            """), {
                'fid':   frame_id,
                'vid':   video_id,
                'ts':    timestamp,
                'pth':   path
            })

        # Publish to Kafka
        prod.send(DATA_TOPIC, {
            'frame_id': frame_id,
            'video_id': video_id,
            'timestamp': timestamp,
            'frame_size_bytes': len(frame_bytes)
        })
        logger.debug(f"Sent {frame_id}")
        frame_count += 1
        idx += 1

    cap.release()
    return jsonify({'status': f'Streamed {frame_count} frames from {VIDEO_FILE}'}), 200

# ── Entrypoint ────────────────────────────────────────────────────────
if __name__ == '__main__':
    # debug print all the critical env vars
    logger.info(f"CONFIG → DATASET_ROOT_DIR={DATASET_ROOT_DIR!r}, VID_SUBDIR={VID_SUBDIR!r}, VIDEO_FILE={VIDEO_FILE!r}")
    if not DATASET_ROOT_DIR or not VIDEO_FILE:
        logger.error("Missing required env vars: DATASET_ROOT_DIR and/or VIDEO_FILE")
    else:
        logger.info("All required env vars present, starting frame stream endpoint")
    # run Flask
    app.run(host='0.0.0.0', port=5000, debug=True)

