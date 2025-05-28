import os
import sys
import time
import json
import logging
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable
from sqlalchemy import create_engine, text
import cv2


# initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)
logger = logging.getLogger(__name__)


# configure flask application
app = Flask(__name__)


# configure from environment variables
MAX_FRAMES_TO_PROCESS = int(os.getenv('MAX_FRAMES_TO_PROCESS', '1000'))
SERVICE_NAME          = os.getenv('SERVICE_NAME',    'Data Generator')
KAFKA_BROKER          = os.getenv('KAFKA_BROKER',    'localhost:9092')
DATASET_ROOT_DIR      = os.getenv('DATASET_ROOT_DIR')
VID_SUBDIR            = os.getenv('VID_SUBDIR',      'vids')
VIDEO_FILE            = os.getenv('VIDEO_FILE')
DATA_TOPIC            = os.getenv('DATA_TOPIC',      'frames')

# environment variables for multiple producer instances
PRODUCER_INSTANCE_ID = int(os.getenv('PRODUCER_INSTANCE_ID', '0'))
TOTAL_PRODUCERS      = int(os.getenv('TOTAL_PRODUCERS', '1'))

POSTGRES_DB   = os.getenv('POSTGRES_DB')
POSTGRES_USER = os.getenv('POSTGRES_USER')
POSTGRES_PASS = os.getenv('POSTGRES_PASSWORD')


# validate essential environment variables
if not DATASET_ROOT_DIR or not VIDEO_FILE:
    logger.error("We must set DATASET_ROOT_DIR and VIDEO_FILE environment variables")
    sys.exit(1)


# set up postgres connection
db_url = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASS}@postgres:5432/{POSTGRES_DB}"
engine = create_engine(db_url)


# set up kafka producer
def make_producer(servers, retries=10, delay=3):
    """
    creates a kafka producer with retry logic
    """
    for i in range(retries):
        try:
            return KafkaProducer(
                bootstrap_servers=servers,
                value_serializer=lambda v: json.dumps(v).encode()
            )
        except NoBrokersAvailable:
            logger.warning(f"kafka broker not ready, retry {i+1}/{retries}")
            time.sleep(delay)
    raise RuntimeError("kafka broker unavailable")

producer = make_producer(KAFKA_BROKER)


# health check endpoint
@app.route('/')
def health():
    return f"{SERVICE_NAME} (instance {PRODUCER_INSTANCE_ID}) is up"


# status endpoint
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


# data status endpoint
@app.route('/data_status')
def data_status():
    root = DATASET_ROOT_DIR
    path = os.path.join(root, VID_SUBDIR, VIDEO_FILE)
    exists = os.path.isfile(path)
    return jsonify({
        'dataset_root': root,
        'video_path': path,
        'exists': exists
    }), (200 if exists else 400)


# frame streaming endpoint
@app.route('/start', methods=['POST'])
def start_stream():
    video_path = os.path.join(DATASET_ROOT_DIR, VID_SUBDIR, VIDEO_FILE)
    video_id   = os.path.splitext(VIDEO_FILE)[0]

    if not os.path.isfile(video_path): return jsonify({'error': f'video not found: {video_path}'}), 404

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened(): return jsonify({'error': 'cannot open video file'}), 500

    frame_count = 0
    frames_sent = 0
    logger.info(f"{SERVICE_NAME} streaming {VIDEO_FILE}")

    if MAX_FRAMES_TO_PROCESS > 0:
        logger.info(f"{SERVICE_NAME} will process a maximum of {MAX_FRAMES_TO_PROCESS} frames")

    while True:
        if MAX_FRAMES_TO_PROCESS > 0 and frame_count >= MAX_FRAMES_TO_PROCESS:
            logger.info(f"{SERVICE_NAME} reached maximum frame limit of {MAX_FRAMES_TO_PROCESS}, stopping stream")

            break

        ret, frame = cap.read()
        if not ret:
            logger.info(f"[{SERVICE_NAME} id {PRODUCER_INSTANCE_ID}] the end of the video was reached at source frame index {frame_count}")
            break

        if frame_count % TOTAL_PRODUCERS == PRODUCER_INSTANCE_ID:
            # jpeg–encode for transport
            success, buf = cv2.imencode('.jpg', frame)
            if not success:
                logger.error(f"failed to encode frame {frame_count}")
                frame_count += 1
                continue

            frame_bytes = buf.tobytes()
            timestamp   = datetime.now(timezone.utc).isoformat()
            frame_id    = f'{video_id}_frame_{frame_count:06d}'

            # save to postgres and disk
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

            # publish to kafka
            message = {
                'frame_id': frame_id,
                'video_id': video_id,
                'timestamp': timestamp,
                'frame_size_bytes': len(frame_bytes),
                'producer_instance_id': PRODUCER_INSTANCE_ID # for tracking or debugging
            }
            producer.send(DATA_TOPIC, message)
            logger.debug(f"sent {frame_id}")
            frames_sent += 1

        # increment for every frame read from source
        frame_count += 1

    cap.release()
    if MAX_FRAMES_TO_PROCESS > 0 and frame_count >= MAX_FRAMES_TO_PROCESS:
        return jsonify({'status': f'streamed {frame_count} frames from {VIDEO_FILE} (limit reached)'}), 200
    else:
        return jsonify({'status': f'streamed {frame_count} frames from {VIDEO_FILE} (end of video)'}), 200
    

# entrypoint
if __name__ == '__main__':
    logger.info(f"[{SERVICE_NAME} id {PRODUCER_INSTANCE_ID}] configuration: dataset_root_dir={DATASET_ROOT_DIR!r}, vid_subdir={VID_SUBDIR!r}, video_file={VIDEO_FILE!r}")
    if not DATASET_ROOT_DIR or not VIDEO_FILE:
        logger.error("missing required environment variables: dataset_root_dir and/or video_file")
    else:
        logger.info("all required environment variables are present, starting frame stream endpoint")
    app.run(host='0.0.0.0', port=5000, debug=True)