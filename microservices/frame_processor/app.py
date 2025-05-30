import os
import json
import time
import socket
import pandas as pd
from datetime import datetime, timezone
from threading import Thread
from flask import Flask
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import NoBrokersAvailable


# configure from environment variables
SERVICE_NAME       = os.getenv("SERVICE_NAME", "Frame Processor")
KAFKA_BROKER       = os.getenv("KAFKA_BROKER", "localhost:9092")
INPUT_TOPIC        = os.getenv("INPUT_TOPIC", "frames")
PROCESS_LOG_TOPIC  = os.getenv("PROCESS_LOG_TOPIC", "processing-logs")
CSV_PATH           = os.getenv("CSV_PATH", "/app/debug_output/processing_logs.csv")


# generate unique processor id based on container hostname
CONTAINER_HOSTNAME = socket.gethostname()
PROCESSOR_ID = f"processor-{CONTAINER_HOSTNAME}"

print(f"{SERVICE_NAME} container hostname: {CONTAINER_HOSTNAME}")
print(f"{SERVICE_NAME} processor id: {PROCESSOR_ID}")


# ensure csv file exists with header
def _initialize_csv(path):
    """
    ensures the csv file exists and has the correct header
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pd.DataFrame(columns=[
        "frame_id",
        "video_id",
        "producer_timestamp",
        "consumer_timestamp",
        "processor_id",
        "frame_size_bytes"
    ]).to_csv(path, index=False)

_initialize_csv(CSV_PATH)


# flask health check endpoint
app = Flask(__name__)
@app.route("/")
def health():
    return f"{SERVICE_NAME} ({PROCESSOR_ID}) is running"


# kafka setup
def make_producer():
    """
    creates a kafka producer with retry logic
    """
    for _ in range(10):
        try:
            return KafkaProducer(
                bootstrap_servers=KAFKA_BROKER,
                value_serializer=lambda v: json.dumps(v).encode("utf-8")
            )
        except NoBrokersAvailable:
            time.sleep(2)
    raise RuntimeError("kafka broker not available")

producer = make_producer()
consumer = KafkaConsumer(
    INPUT_TOPIC,
    bootstrap_servers=KAFKA_BROKER,
    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    auto_offset_reset="earliest",
    enable_auto_commit=True,
    group_id=f"{SERVICE_NAME}-group"
)


# main processing loop
def process_loop():
    """
    consumes messages from kafka, simulates processing, and logs results
    """
    print(f"{SERVICE_NAME} ({PROCESSOR_ID}) starting")
    for msg in consumer:
        data       = msg.value
        frame_id   = data["frame_id"]
        video_id   = data["video_id"]
        prod_ts    = data["timestamp"]

        # simulate 1s of "processing"
        time.sleep(1)

        # record when we finished
        cons_ts = datetime.now(timezone.utc).isoformat()
        log_row = {
            "frame_id":           frame_id,
            "video_id":           video_id,
            "producer_timestamp": prod_ts,
            "consumer_timestamp": cons_ts,
            "processor_id":       PROCESSOR_ID,
            "frame_size_bytes":     data.get("frame_size_bytes")
        }

        # append to csv
        pd.DataFrame([log_row]).to_csv(CSV_PATH, mode="a", header=False, index=False)
        # publish for stats
        producer.send(PROCESS_LOG_TOPIC, log_row)

        print(f"{SERVICE_NAME} ({PROCESSOR_ID}) processed {frame_id}")


# entrypoint
if __name__ == "__main__":
    Thread(target=process_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
    