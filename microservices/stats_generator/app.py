import os
import json
import time
import threading
from datetime import datetime, timezone
import pandas as pd
from flask import Flask, jsonify
from kafka import KafkaConsumer


# configure from environment variables
SERVICE_NAME      = os.getenv("SERVICE_NAME", "Stats Generator")
KAFKA_BROKER      = os.getenv("KAFKA_BROKER", "localhost:9092")
PROCESS_LOG_TOPIC = os.getenv("PROCESS_LOG_TOPIC", "processing-logs")
CSV_PATH          = os.getenv("CSV_PATH", "/app/debug_output/stats.csv")
MAX_FRAMES_TO_PROCESS = int(os.getenv('MAX_FRAMES_TO_PROCESS', '1000'))
FINAL_REPORT_PATH = os.getenv('FINAL_REPORT_PATH', '/app/debug_output/final_experiment_report.json')


# ensure csv file exists with header
def _initialize_csv(path):
    """
    ensures the csv file exists and has the correct header
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pd.DataFrame(columns=[
        "frame_id",
        "producer_timestamp",
        "consumer_timestamp",
        "frame_size_bytes",
        "latency_s"
    ]).to_csv(path, index=False)

_initialize_csv(CSV_PATH)


# initialize metrics storage
metrics = {
    "start": time.time(),
    "count": 0,
    "total_bytes": 0,
    "latencies": []
}
report_saved = False

app = Flask(__name__)


# main consumption loop
def consume_loop():
    """
    consumes processing logs from kafka, computes metrics, and appends to csv
    """
    consumer = KafkaConsumer(
        PROCESS_LOG_TOPIC,
        bootstrap_servers=KAFKA_BROKER,
        value_deserializer=lambda m: json.loads(m.decode('utf-8'))
    )
    for record in consumer:
        data = record.value
        # parse producer timestamp
        prod_ts = datetime.fromisoformat(data["producer_timestamp"])
        cons_ts = datetime.now(timezone.utc)

        # compute latency
        latency_s = (cons_ts - prod_ts).total_seconds()

        # get actual frame size from payload
        frame_size = int(data.get("frame_size_bytes", 0))

        # update metrics
        metrics["count"] += 1
        metrics["total_bytes"] += frame_size
        metrics["latencies"].append(latency_s)

        # append to csv
        pd.DataFrame([{
            "frame_id":             data.get("frame_id"),
            "producer_timestamp":   prod_ts.isoformat(),
            "consumer_timestamp":   cons_ts.isoformat(),
            "frame_size_bytes":     frame_size,
            "latency_s":            latency_s
        }]).to_csv(CSV_PATH, mode="a", header=False, index=False)
    
        if MAX_FRAMES_TO_PROCESS > 0 and metrics["count"] >= MAX_FRAMES_TO_PROCESS and not report_saved:
            print(f"{SERVICE_NAME} processed {metrics['count']} frames, reaching target of {MAX_FRAMES_TO_PROCESS}")
            save_final_report()


# helper function to generate current report data
def get_current_report_data():
    """
    generates the report data dictionary based on current metrics
    """
    elapsed = time.time() - metrics["start"]
    max_latency = max(metrics["latencies"]) if metrics["latencies"] else 0.0
    throughput_mbps = 0.0
    if elapsed > 0:
        throughput_mbps = metrics["total_bytes"] * 8 / elapsed / 1e6
    
    is_complete = MAX_FRAMES_TO_PROCESS > 0 and metrics["count"] >= MAX_FRAMES_TO_PROCESS

    return {
        "max_latency_s":       max_latency,
        "messages_processed":  metrics["count"],
        "throughput_mbps":     throughput_mbps,
        "is_complete":         is_complete,
        "target_frames":       MAX_FRAMES_TO_PROCESS,
        "report_generation_timestamp": datetime.now(timezone.utc).isoformat()
    }


# saves the final report
def save_final_report():
    """
    saves the current metrics report to a json file
    """
    global report_saved
    if report_saved: return

    report_data = get_current_report_data()
    # ensure the directory for the report exists
    os.makedirs(os.path.dirname(FINAL_REPORT_PATH), exist_ok=True)
    with open(FINAL_REPORT_PATH, 'w') as f:
        json.dump(report_data, f, indent=4)
    print(f"{SERVICE_NAME} final report saved to {FINAL_REPORT_PATH} as {metrics['count']}/{MAX_FRAMES_TO_PROCESS} frames processed")
    report_saved = True


# report endpoint
@app.route("/report")
def report():
    report_data = get_current_report_data()
    return jsonify(report_data)


# entrypoint
if __name__ == "__main__":
    # start the background consumer
    threading.Thread(target=consume_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
    