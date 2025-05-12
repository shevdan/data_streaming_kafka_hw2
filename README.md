# data_streaming_kafka_hw2


## 1. Dataset Setup

This project processes a sample. The `data_generator` service will iterate through each frame and send them to topics.

Navigate to the root directory of this project in your terminal. Run the provided Python script:
    ```bash
    python download_dataset.py
    ```
This script will download the `vids.zip` archive from Google Drive and extract its contents into the `./data/vids` directory in the project root. The `docker-compose.yaml` file is then configured to mount the `./data/vids` directory on your host into the `/app/vids` directory *inside* the `data_generator` container.



To start you can simply run 

```docker compose up```

Make sure to specify number of partitions for the topic in docker-compose.yaml in topic-init service

However, for correct startup later, make sure to run following commands one by one:

```docker compose down```

```docker compose run --rm topic_cleanup```

This way topic will be cleared and recreated with possibly other configs next time.

Alternatively, you could run this script:

```bash ./topic_cleanup.sh```


Each microservice is configured to expose an HTTP port (mapped from the container's internal port 5000 to a specific port on the host machine).

*   **Data Generator:**  (Mapped port 5001)
    *   Access: `http://localhost:5001/`
    *   Status endpoint: `http://localhost:5001/status` - Provides general service info and confirms environment variables are set.
    *   Data check endpoint: `http://localhost:5001/data_status` - Verifies that the volume mount is working and the service can see the configured dataset directory and its contents. Check this endpoint to confirm your data is correctly mounted.
    *   Start data generation into frames: `http://localhost:5001/start/<input_id>`. It populates topics for car and person detection with frames. It looks for input id in specified data root in `sequences` folder. Supports pure video and video split by frames already.
