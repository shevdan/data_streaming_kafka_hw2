import os
import json
import time
import threading
from datetime import datetime, timezone
import pandas as pd
from flask import Flask, jsonify
from kafka import KafkaConsumer

# ── Configuration ───────────────────────────────────────────────────
SERVICE_NAME      = os.getenv("SERVICE_NAME", "Stats Generator")
KAFKA_BROKER      = os.getenv("KAFKA_BROKER", "localhost:9092")
PROCESS_LOG_TOPIC = os.getenv("PROCESS_LOG_TOPIC", "processing-logs")
CSV_PATH          = os.getenv("CSV_PATH", "/app/debug_output/stats.csv")
MAX_FRAMES_TO_PROCESS = int(os.getenv('MAX_FRAMES_TO_PROCESS', '1000'))
FINAL_REPORT_PATH = os.getenv('FINAL_REPORT_PATH', '/app/debug_output/final_experiment_report.json')


# Ensure CSV exists with header
os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
pd.DataFrame(columns=[
    "frame_id",
    "producer_timestamp",
    "consumer_timestamp",
    "frame_size_bytes",
    "latency_s"
]).to_csv(CSV_PATH, index=False)

# ── Metrics storage ─────────────────────────────────────────────────
metrics = {
    "start": time.time(),
    "count": 0,
    "total_bytes": 0,
    "latencies": []
}
report_saved = False

app = Flask(__name__)

def consume_loop():
    consumer = KafkaConsumer(
        PROCESS_LOG_TOPIC,
        bootstrap_servers=KAFKA_BROKER,
        value_deserializer=lambda m: json.loads(m.decode('utf-8'))
    )
    for record in consumer:
        data = record.value
        # 1) Parse producer timestamp
        prod_ts = datetime.fromisoformat(data["producer_timestamp"])
        cons_ts = datetime.now(timezone.utc)

        # 2) Compute latency
        latency_s = (cons_ts - prod_ts).total_seconds()

        # 3) Get actual frame size from payload
        frame_size = int(data.get("frame_size_bytes", 0))

        # 4) Update metrics
        metrics["count"] += 1
        metrics["total_bytes"] += frame_size
        metrics["latencies"].append(latency_s)

        # 5) Append to CSV
        pd.DataFrame([{
            "frame_id":             data.get("frame_id"),
            "producer_timestamp":   prod_ts.isoformat(),
            "consumer_timestamp":   cons_ts.isoformat(),
            "frame_size_bytes":     frame_size,
            "latency_s":            latency_s
        }]).to_csv(CSV_PATH, mode="a", header=False, index=False)
    
        if MAX_FRAMES_TO_PROCESS > 0 and metrics["count"] >= MAX_FRAMES_TO_PROCESS and not report_saved:
            print(f"[{SERVICE_NAME}] Processed {metrics['count']} frames, reaching target of {MAX_FRAMES_TO_PROCESS}.")
            save_final_report()

def get_current_report_data():
    """Helper function to generate the report data dictionary."""
    elapsed = time.time() - metrics["start"]
    max_latency = max(metrics["latencies"]) if metrics["latencies"] else 0.0
    throughput_mbps = 0.0
    if elapsed > 0:
        throughput_mbps = metrics["total_bytes"] * 8 / elapsed / 1e6
    
    is_complete = False
    if MAX_FRAMES_TO_PROCESS > 0 and metrics["count"] >= MAX_FRAMES_TO_PROCESS:
        is_complete = True

    return {
        "max_latency_s":       max_latency,
        "messages_processed":  metrics["count"],
        "throughput_mbps":     throughput_mbps,
        "is_complete":         is_complete,
        "target_frames":       MAX_FRAMES_TO_PROCESS,
        "report_generation_timestamp": datetime.now(timezone.utc).isoformat()
    }

def save_final_report():
    """Saves the current metrics report to a JSON file."""
    global report_saved
    if report_saved: # Prevent saving multiple times
        return

    report_data = get_current_report_data()
    # Ensure the directory for the report exists
    os.makedirs(os.path.dirname(FINAL_REPORT_PATH), exist_ok=True)
    with open(FINAL_REPORT_PATH, 'w') as f:
        json.dump(report_data, f, indent=4)
    print(f"[{SERVICE_NAME}] Final report saved to {FINAL_REPORT_PATH} as {metrics['count']}/{MAX_FRAMES_TO_PROCESS} frames processed.")
    report_saved = True

@app.route("/report")
def report():
    report_data = get_current_report_data()
    return jsonify(report_data)

if __name__ == "__main__":
    # fire up the background consumer
    threading.Thread(target=consume_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
