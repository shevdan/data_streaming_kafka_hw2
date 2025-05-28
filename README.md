# data_streaming_kafka_hw2


### 1. Dataset Setup
The project processes frames from a sample video file. The `data_generator` service iterates through these frames.
First, ensure the dataset is available. Navigate to the root directory of this project and run:
```bash
python download_dataset.py
```
This script downloads `vids.zip` from a Google Drive link and extracts it into the `./data/vids` directory. The `docker-compose.yaml` file is configured to mount this directory into the `data_generator` container.
### 2. Running an Experiment
For each experiment configuration, the `docker-compose.yaml` file requires specific adjustments. 
Note:
	The `MAX_FRAMES_TO_PROCESS` environment variable, set to 1000 in both the `data_generator` and `stats_generator` services, caps the total frames processed. This limit is chosen because the original video contains approximately 73k frames, which would lead to excessively long run times for low-throughput configurations. After we tested some numbers, processing 1000 frames is pretty sufficient to validate theoretical distributions across different setups.
#### Number of producers
For experiments involving a single producer, activate only the `data_generator` service in `docker-compose.yaml` (switch to branch '`main`'). For the two-producer experiment, activate `data_generator_1` and `data_generator_2` services (switch to branch '`two_producers`'), each configured with a unique `PRODUCER_INSTANCE_ID` namely, "0" and "1" respectively and their `TOTAL_PRODUCERS` set to "2". These services also require different host port mappings, like 5001 for `data_generator_1` and 5002 for `data_generator_2`.
#### Number of partitions
The number of partitions for the `frames` topic is set in the `topic_init` service using the `--partitions=` argument. 
#### Number of consumers
The number of consumer instances (`frame_processor` replicas) is controlled using the `--scale` option.
#### Start services
- To start services for a single producer configuration, for example, 1 producer with 10 partitions and 5 consumers:
```bash
docker compose up -d --scale frame_processor=5 data_generator frame_processor stats_generator
```
- To start services for the two-producer experiment, namely 2 producers with 10 partitions and 10 consumers:
```bash
# first, switch to the branch containing the two-producer docker-compose config
git checkout two-producers
# then, start the services
docker compose up -d --scale frame_processor=10 data_generator_1 data_generator_2 frame_processor stats_generator
```
#### Send request(s)
After starting the services, trigger the data generation process by sending a POST request to the `data_generator`'s `/start` endpoint. 
- For a *single producer* setup:
```bash
curl -X POST http://localhost:5001/start
```
- For the *two-producer* setup, trigger both instances concurrently:
```bash
curl -X POST http://localhost:5001/start &
curl -X POST http://localhost:5002/start &
wait
```
#### Additional notes
After initiation, allow some time for the processing to complete. The `stats_generator` service indicates completion by writing `final_experiment_report.json` to the `debug_output` directory once `MAX_FRAMES_TO_PROCESS` messages have been processed. 
The report's progress can also be monitored via the `/report` endpoint at `http://localhost:5007/report`.

Upon experiment completion, copy the contents of the `debug_output` directory (which includes `final_experiment_report.json`, `processing_logs.csv`, and `stats.csv`) to a separate, labeled location corresponding to the applied experiment configuration.

Finally, we shut down the services and cleared resources:
```bash
docker compose down -v
```
This process was repeated for each configuration in the experimental series.
