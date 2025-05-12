from flask import Flask, jsonify, request
import os
import sys
import time
import base64
import json
import logging
from datetime import datetime, timezone
from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable
from sqlalchemy import create_engine, text
import cv2

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)

logger = logging.getLogger(__name__)

app = Flask(__name__)

SERVICE_NAME = os.environ.get('SERVICE_NAME', 'Unknown Service')
KAFKA_BROKER = os.environ.get('KAFKA_BROKER', 'localhost:9092')
DATASET_ROOT_DIR = os.environ.get('DATASET_ROOT_DIR')
DATA_TOPIC = os.environ.get('DATA_TOPIC')
TWO_PRODUCERS = os.environ.get('TWO_PRODUCERS', 'False').lower() == 'true'
VID_SUBDIR = os.environ.get('VID_SUBDIR')

POSTGRES_DB = os.environ.get('POSTGRES_DB')
POSTGRES_USER = os.environ.get('POSTGRES_USER')
POSTGRES_PASSWORD = os.environ.get('POSTGRES_PASSWORD')

engine = create_engine(f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@postgres:5432/{POSTGRES_DB}")

def create_kafka_producer(bootstrap_servers, retries=10, delay=3):
    for attempt in range(retries):
        try:
            return KafkaProducer(
                bootstrap_servers=bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode('utf-8')
            )
        except NoBrokersAvailable:
            print(f"[Kafka] Broker not ready yet, retrying ({attempt+1}/{retries})...")
            time.sleep(delay)
    raise RuntimeError("Kafka broker not available after retries.")

producer = create_kafka_producer(KAFKA_BROKER)
second_producer = create_kafka_producer(KAFKA_BROKER) if TWO_PRODUCERS else None

def send_to_kafka(producer, frame_id, video_id, frames_topic, timestamp):
    message = {
        'frame_id': frame_id,
        'timestamp': timestamp,
        'video_id': video_id
    }
    producer.send(frames_topic, message)
    logger.info(f"[{SERVICE_NAME}] Sent frame {frame_id}")

def save_frame_and_record(frame_bytes, frame_id, video_id, timestamp):
    path = f"/app/frame_store/{video_id}_{frame_id}.jpg"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(frame_bytes)

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO frames (frame_id, video_id, timestamp, path)
            VALUES (:frame_id, :video_id, :timestamp, :path)
            ON CONFLICT (frame_id) DO NOTHING
        """), {
            "frame_id": frame_id,
            "video_id": video_id,
            "timestamp": timestamp,
            "path": path
        })

    return frame_id


@app.route('/')
def hello_world():
    return f'{SERVICE_NAME} is running!'

@app.route('/status')
def status():
    status_info = {
        'service_name': SERVICE_NAME,
        'python_version': sys.version,
        'kafka_broker_env': KAFKA_BROKER,
    }
    status_info['dataset_path_configured'] = DATASET_ROOT_DIR is not None
    if DATASET_ROOT_DIR:
        status_info['dataset_path_exists'] = os.path.exists(DATASET_ROOT_DIR)
    else:
        status_info['dataset_path_exists'] = False
    return jsonify(status_info)

@app.route('/data_status')
def data_status():
    status_info = {}

    if not DATASET_ROOT_DIR:
        status_info['error'] = "DATASET_ROOT_DIR environment variable is not set."
        status_info['dataset_path'] = None
        status_info['path_exists'] = False
        status_info['contents'] = None
        status_info['expected_subdirs_found'] = False
        return jsonify(status_info), 400

    status_info['dataset_path'] = DATASET_ROOT_DIR

    path_exists = os.path.exists(DATASET_ROOT_DIR)
    status_info['path_exists'] = path_exists

    if path_exists:
        is_dir = os.path.isdir(DATASET_ROOT_DIR)
        status_info['is_directory'] = is_dir

        if is_dir:
            try:
                contents = os.listdir(DATASET_ROOT_DIR)
                status_info['contents'] = contents
                expected_subdirs = ['annotations', 'sequences']
                status_info['expected_subdirs_checked'] = expected_subdirs
                status_info['expected_subdirs_found'] = [d for d in expected_subdirs if d in contents and os.path.isdir(os.path.join(DATASET_ROOT_DIR, d))]
                status_info['all_expected_subdirs_found'] = all(d in contents and os.path.isdir(os.path.join(DATASET_ROOT_DIR, d)) for d in expected_subdirs)

                sequences_path = os.path.join(DATASET_ROOT_DIR, 'sequences')
                if os.path.isdir(sequences_path):
                    status_info['sequences_subdir_exists'] = True
                    try:
                        sequence_contents = os.listdir(sequences_path)
                        status_info['sequences_subdir_contents_count'] = len(sequence_contents)
                        status_info['sequences_subdir_contents_sample'] = sequence_contents[:5] # Show first 5
                    except Exception as e:
                        status_info['sequences_subdir_list_error'] = str(e)
                else:
                    status_info['sequences_subdir_exists'] = False

            except Exception as e:
                status_info['contents_list_error'] = str(e)
                status_info['contents'] = None
                status_info['expected_subdirs_found'] = False
                status_info['all_expected_subdirs_found'] = False
        else:
             status_info['is_directory'] = False
             status_info['contents'] = None
             status_info['expected_subdirs_found'] = False
             status_info['all_expected_subdirs_found'] = False

    else:
        status_info['contents'] = None
        status_info['is_directory'] = False
        status_info['expected_subdirs_found'] = False
        status_info['all_expected_subdirs_found'] = False


    return jsonify(status_info)

def handle_two_producers(first_producer, second_producer, idx):
    if second_producer is not None and idx % 2 == 1:
        return second_producer
    return first_producer

@app.route('/start/<input_id>', methods=['POST'])
def start_sequence_stream(input_id):
    input_path_video = os.path.join(DATASET_ROOT_DIR, VID_SUBDIR, input_id)

    frame_count = 0

    if os.path.isdir(input_path_video):
        frames = sorted(f for f in os.listdir(input_path_video) if f.lower().endswith(('.jpg', '.png')))
        for idx, frame_file in enumerate(frames):
            frame_path = os.path.join(input_path_video, frame_file)
            frame_id = f'frame_{frame_count:05d}'
            with open(frame_path, 'rb') as f:
                frame_bytes = f.read()

            timestamp = datetime.now(timezone.utc).isoformat()
            current_producer = handle_two_producers(producer, second_producer, idx)
            save_frame_and_record(frame_bytes, frame_id, input_id, timestamp)
            send_to_kafka(current_producer, frame_id, input_id, DATA_TOPIC, timestamp)
            logger.info(f"[{SERVICE_NAME}] Using {'second' if current_producer == second_producer else 'first'} producer for frame {frame_id}")
            frame_count += 1
    elif os.path.isfile(input_path_video) and input_id.endswith(('.mp4', '.avi', '.mov')):
        cap = cv2.VideoCapture(input_path_video)
        idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            _, buffer = cv2.imencode('.jpg', frame)
            frame_id = f'frame_{frame_count:05d}'
            timestamp = datetime.now(timezone.utc).isoformat()
            current_producer = handle_two_producers(producer, second_producer, idx)
            save_frame_and_record(buffer.tobytes(), frame_id, input_id, timestamp)
            send_to_kafka(current_producer, frame_id, input_id, DATA_TOPIC, timestamp)
            logger.info(f"[{SERVICE_NAME}] Using {'second' if current_producer == second_producer else 'first'} producer for frame {frame_id}")

            frame_count += 1
            idx += 1
        cap.release()
    else:
        return jsonify({'error': f'{input_id} is not a valid folder or video file.'}), 400

    return jsonify({'status': f'Sent {frame_count} frames from {input_id}.'})


if __name__ == '__main__':
    logger.info("Starting Flask app...")
    app.run(debug=True, host='0.0.0.0', port=5000)
