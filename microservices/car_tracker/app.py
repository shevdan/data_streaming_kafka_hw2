import os
import json
import numpy as np
import pandas as pd
from types import SimpleNamespace
from threading import Thread
from flask import Flask
from kafka import KafkaConsumer, KafkaProducer
from ultralytics.trackers.byte_tracker import BYTETracker

# ── Configuration ───────────────────────────────────────────────────
SERVICE_NAME = os.getenv("SERVICE_NAME", "Car Tracker")
BROKER       = os.getenv("KAFKA_BROKER", "localhost:9092")
IN_TOPIC     = os.getenv("INPUT_TOPIC", "detections-cars")
OUT_TOPIC    = os.getenv("OUTPUT_TOPIC", "tracks-cars")
FRAME_RATE   = int(os.getenv("FRAME_RATE", "30"))
CSV_PATH     = os.getenv("CSV_PATH", "/app/debug_output/car_tracks.csv")

os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
if not os.path.exists(CSV_PATH):
    pd.DataFrame(columns=[
        "frame_id", "video_id", "timestamp",
        "track_id", "x1", "y1", "x2", "y2", "confidence", "class_id"
    ]).to_csv(CSV_PATH, index=False)

# ── PseudoResults wrapper ────────────────────────────────────────────
class PseudoResults:
    def __init__(self, det_array: np.ndarray):
        self.conf = det_array[:, 4]
        self.cls  = np.zeros(det_array.shape[0], dtype=int)
        self.boxes = SimpleNamespace(
            xyxy = det_array[:, :4],
            cls  = self.cls
        )
        xyxy = det_array[:, :4]
        xy   = xyxy[:, :2]
        wh   = xyxy[:, 2:] - xyxy[:, :2]
        self.xywh = np.hstack((xy, wh))

# ── Flask Healthcheck ───────────────────────────────────────────────
app = Flask(__name__)
@app.route("/")
def health():
    return f"{SERVICE_NAME} is running!"

# ── Kafka Setup ─────────────────────────────────────────────────────
consumer = KafkaConsumer(
    IN_TOPIC,
    bootstrap_servers=BROKER,
    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    auto_offset_reset="earliest",
    enable_auto_commit=True,
    group_id="car-tracker-group"
)
producer = KafkaProducer(
    bootstrap_servers=BROKER,
    value_serializer=lambda v: json.dumps(v).encode("utf-8")
)

# ── ByteTrack Initialization ────────────────────────────────────────
tracker_args = SimpleNamespace(
    tracker_type      = "bytetrack",
    track_high_thresh = 0.25,
    track_low_thresh  = 0.1,
    new_track_thresh  = 0.25,
    track_buffer      = 30,
    match_thresh      = 0.8,
    fuse_score        = True
)
tracker = BYTETracker(tracker_args, frame_rate=FRAME_RATE)

# ── Tracking Loop ───────────────────────────────────────────────────
def track_loop():
    print(f"[{SERVICE_NAME}] Starting ByteTrack loop…")
    for msg in consumer:
        data      = msg.value
        frame_id  = data.get("frame_id")
        video_id  = data.get("video_id")
        timestamp = data.get("timestamp")
        dets      = data.get("detections", [])

        if not dets:
            continue

        # Build N×5 array: [x1, y1, x2, y2, confidence]
        det_array = np.array(
            [[*d["bbox"], d["confidence"]] for d in dets],
            dtype=float
        )

        pseudo  = PseudoResults(det_array)
        tracks  = tracker.update(pseudo, None)

        csv_rows, out_tracks = [], []

        # Handle NumPy-array output
        if isinstance(tracks, np.ndarray):
            for row in tracks:
                vals = row.tolist()
                # x,y,w,h always in first 4
                x, y, w, h = map(int, vals[:4])
                # 5th element is confidence (if present)
                conf = float(vals[4]) if len(vals) > 4 else None
                # Last element is track_id
                tid  = int(vals[-1]) if len(vals) > 5 else None
                x2, y2 = x + w, y + h

                out_tracks.append({
                    "track_id":   tid,
                    "bbox":       [x, y, x2, y2],
                    "confidence": conf,
                    "class_id":   0
                })
                csv_rows.append({
                    "frame_id":   frame_id,
                    "video_id":   video_id,
                    "timestamp":  timestamp,
                    "track_id":   tid,
                    "x1":         x,
                    "y1":         y,
                    "x2":         x2,
                    "y2":         y2,
                    "confidence": conf,
                    "class_id":   0
                })

        else:
            # Handle list of STrack objects
            for t in tracks:
                x, y, w, h = map(int, t.tlwh)
                x2, y2     = x + w, y + h
                tid        = int(t.track_id)
                conf       = float(t.score)
                cls_id     = int(getattr(t, "cls", 0))

                out_tracks.append({
                    "track_id":   tid,
                    "bbox":       [x, y, x2, y2],
                    "confidence": conf,
                    "class_id":   cls_id
                })
                csv_rows.append({
                    "frame_id":   frame_id,
                    "video_id":   video_id,
                    "timestamp":  timestamp,
                    "track_id":   tid,
                    "x1":         x,
                    "y1":         y,
                    "x2":         x2,
                    "y2":         y2,
                    "confidence": conf,
                    "class_id":   cls_id
                })

        # Publish to Kafka
        producer.send(OUT_TOPIC, {
            "frame_id":  frame_id,
            "video_id":  video_id,
            "timestamp": timestamp,
            "tracks":    out_tracks
        })
        # Append to CSV
        pd.DataFrame(csv_rows).to_csv(CSV_PATH, mode="a", header=False, index=False)

        print(f"[{SERVICE_NAME}] {frame_id} → {len(out_tracks)} tracks")

# ── Entrypoint ──────────────────────────────────────────────────────
if __name__ == "__main__":
    Thread(target=track_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
