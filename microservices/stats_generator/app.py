import os
import json
import time
import threading
from datetime import datetime
import pandas as pd
from flask import Flask, jsonify
from kafka import KafkaConsumer

# ── Configuration ───────────────────────────────────────────────────
SERVICE_NAME      = os.getenv("SERVICE_NAME", "Stats Generator")
KAFKA_BROKER      = os.getenv("KAFKA_BROKER", "localhost:9092")
PROCESS_LOG_TOPIC = os.getenv("PROCESS_LOG_TOPIC", "processing-logs")
CSV_PATH          = os.getenv("CSV_PATH", "/app/debug_output/stats.csv")

# Ensure CSV exists with header
os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
if not os.path.exists(CSV_PATH):
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
        cons_ts = datetime.utcnow()

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

# fire up the background consumer
threading.Thread(target=consume_loop, daemon=True).start()

@app.route("/report")
def report():
    elapsed = time.time() - metrics["start"]
    max_latency = max(metrics["latencies"]) if metrics["latencies"] else 0.0
    throughput_mbps = metrics["total_bytes"] * 8 / elapsed / 1e6

    return jsonify({
        "max_latency_s":       max_latency,
        "messages_processed":  metrics["count"],
        "throughput_mbps":     throughput_mbps
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5007)
