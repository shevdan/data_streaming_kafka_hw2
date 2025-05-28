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
MAX_FRAMES_TO_PROCESS = int(os.getenv('MAX_FRAMES_TO_PROCESS', '1000'))
SERVICE_NAME     = os.getenv('SERVICE_NAME',    'Data Generator')
KAFKA_BROKER     = os.getenv('KAFKA_BROKER',    'localhost:9092')
DATASET_ROOT_DIR = os.getenv('DATASET_ROOT_DIR')
VID_SUBDIR       = os.getenv('VID_SUBDIR',      'vids')
VIDEO_FILE       = os.getenv('VIDEO_FILE')
DATA_TOPIC       = os.getenv('DATA_TOPIC',      'frames')

# New environment variables for multiple producer instances
PRODUCER_INSTANCE_ID = int(os.getenv('PRODUCER_INSTANCE_ID', '0'))
TOTAL_PRODUCERS    = int(os.getenv('TOTAL_PRODUCERS', '1'))

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

# ── Kafka producer ───────────────────────────────────────────────────
def make_producer(servers, retries=10, delay=3):
    for i in range(retries):
        try:
            return KafkaProducer(
                bootstrap_servers=servers,
                value_serializer=lambda v: json.dumps(v).encode()
            )
        except NoBrokersAvailable:
            logger.warning(f"[{SERVICE_NAME} ID {PRODUCER_INSTANCE_ID}] Kafka broker not ready, retry {i+1}/{retries}")
            time.sleep(delay)
    raise RuntimeError("Kafka broker unavailable")

producer = make_producer(KAFKA_BROKER) # Each instance has one producer

# ── Health & Data Status ──────────────────────────────────────────────
@app.route('/')
def health():
    return f"{SERVICE_NAME} (Instance {PRODUCER_INSTANCE_ID}) up"

@app.route('/status')
def status():
    return jsonify({
        'service_name': SERVICE_NAME,
        'producer_instance_id': PRODUCER_INSTANCE_ID,
        'total_producers': TOTAL_PRODUCERS,
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

    # frame_index_for_distribution tracks overall frames from video source
    frame_index_for_distribution = 0
    # frames_sent_by_this_producer tracks frames sent by this specific instance
    frames_sent_by_this_producer = 0

    logger.info(f"[{SERVICE_NAME} ID {PRODUCER_INSTANCE_ID}/{TOTAL_PRODUCERS-1}] Streaming {VIDEO_FILE}…")

    if MAX_FRAMES_TO_PROCESS > 0:
        logger.info(f"[{SERVICE_NAME} ID {PRODUCER_INSTANCE_ID}] Will process frames from source up to index {MAX_FRAMES_TO_PROCESS -1}.")

    while True:
        # Check against total frames to be processed from the video source
        if MAX_FRAMES_TO_PROCESS > 0 and frame_index_for_distribution >= MAX_FRAMES_TO_PROCESS:
            logger.info(f"[{SERVICE_NAME} ID {PRODUCER_INSTANCE_ID}] Reached overall source frame limit of {MAX_FRAMES_TO_PROCESS} (current index {frame_index_for_distribution}). Stopping stream.")
            break

        ret, frame = cap.read()
        if not ret:
            logger.info(f"[{SERVICE_NAME} ID {PRODUCER_INSTANCE_ID}] End of video reached at source frame index {frame_index_for_distribution}.")
            break

        # Splitting logic: process this frame if (frame_index % total_producers) == this_producer_id
        if frame_index_for_distribution % TOTAL_PRODUCERS == PRODUCER_INSTANCE_ID:
            success, buf = cv2.imencode('.jpg', frame)
            if not success:
                logger.error(f"[{SERVICE_NAME} ID {PRODUCER_INSTANCE_ID}] Failed to encode frame {frame_index_for_distribution}")
                frame_index_for_distribution += 1 # Crucial: still advance frame index
                continue

            frame_bytes = buf.tobytes()
            timestamp   = datetime.now(timezone.utc).isoformat()
            # Use frame_index_for_distribution for a globally unique frame_id
            frame_id    = f'{video_id}_frame_{frame_index_for_distribution:06d}'

            # Save to Postgres + disk (ON CONFLICT DO NOTHING handles concurrent writes from different producers for the same frame_id)
            path = f"/app/frame_store/{video_id}_{frame_index_for_distribution:06d}.jpg"
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
            message = {
                'frame_id': frame_id,
                'video_id': video_id,
                'timestamp': timestamp,
                'frame_size_bytes': len(frame_bytes),
                'producer_instance_id': PRODUCER_INSTANCE_ID # For tracking/debugging
            }
            producer.send(DATA_TOPIC, message)
            logger.debug(f"[{SERVICE_NAME} ID {PRODUCER_INSTANCE_ID}] Sent {frame_id} (source index {frame_index_for_distribution})")
            frames_sent_by_this_producer += 1
        # else:
            # This instance is not responsible for this frame, skip sending
            # logger.debug(f"[{SERVICE_NAME} ID {PRODUCER_INSTANCE_ID}] Skipping source frame {frame_index_for_distribution}")

        frame_index_for_distribution += 1 # Increment for every frame read from source

    cap.release()
    
    status_msg_part = f"Streamed {frames_sent_by_this_producer} frames from {VIDEO_FILE} by producer {PRODUCER_INSTANCE_ID}."
    if MAX_FRAMES_TO_PROCESS > 0 and frame_index_for_distribution >= MAX_FRAMES_TO_PROCESS:
        status_msg = f'{status_msg_part} Reached overall source frame limit ({frame_index_for_distribution}/{MAX_FRAMES_TO_PROCESS}).'
    else:
        status_msg = f'{status_msg_part} Reached end of video (processed {frame_index_for_distribution} source frames).'
    
    logger.info(f"[{SERVICE_NAME} ID {PRODUCER_INSTANCE_ID}] {status_msg}")
    return jsonify({'status': status_msg}), 200

# ── Entrypoint ────────────────────────────────────────────────────────
if __name__ == '__main__':
    logger.info(f"[{SERVICE_NAME} ID {PRODUCER_INSTANCE_ID}] CONFIG → DATASET_ROOT_DIR={DATASET_ROOT_DIR!r}, VIDEO_FILE={VIDEO_FILE!r}, MAX_FRAMES_TO_PROCESS={MAX_FRAMES_TO_PROCESS}")
    logger.info(f"[{SERVICE_NAME} ID {PRODUCER_INSTANCE_ID}] Instance ID: {PRODUCER_INSTANCE_ID}, Total Producers: {TOTAL_PRODUCERS}")

    if not DATASET_ROOT_DIR or not VIDEO_FILE:
        logger.error("Missing required env vars: DATASET_ROOT_DIR and/or VIDEO_FILE")
    else:
        logger.info(f"[{SERVICE_NAME} ID {PRODUCER_INSTANCE_ID}] All required env vars present, starting frame stream endpoint")
    
    app.run(host='0.0.0.0', port=5000, debug=True) # debug=True is usually for dev