# SREGPT

SREGPT is a retrieval-augmented troubleshooting assistant for SRE and DevOps incidents. It uses FastAPI for the API layer, PostgreSQL with `pgvector` for similarity search over past incident records, SentenceTransformers for embeddings, and Ollama with `llama3.2` for answer generation.

The repo is structured so the API runtime and ingestion runtime can ship as separate container images while reusing shared Python modules.

## Preview

![SREGPT UI](docs/latest-preview.png)

## Architecture

```text
           ┌──────────────┐
           │   User (UI)  │
           └──────┬───────┘
                  ↓
           ┌──────────────┐
           │   FastAPI    │  ← Entry point
           └──────┬───────┘
                  ↓
   ┌────────────────────────────┐
   │  Query Processing Layer    │
   └──────┬─────────────────────┘
          ↓
   ┌────────────────────────────┐
   │  Embedding Model           │
   │ (SentenceTransformer)      │
   └──────┬─────────────────────┘
          ↓
   ┌────────────────────────────┐
   │ PostgreSQL + pgvector      │
   └──────┬─────────────────────┘
          ↓
   ┌────────────────────────────┐
   │  Incident Data Layer       │
   │ (CSV → Pandas → records)   │
   └──────┬─────────────────────┘
          ↓
   ┌────────────────────────────┐
   │  Decision Engine           │
   │ (Top-K + Filtering)        │
   └──────┬─────────────┬───────┘
          ↓             ↓
 High Match ✅      Low Match ❌
     ↓                  ↓
┌──────────────┐  ┌────────────────┐
│ Return KB    │  │  Ollama LLM    │
│ Solution     │  │  (llama3.2)    │
└──────┬───────┘  └──────┬─────────┘
       ↓                ↓
       └──────────→ Final Response
```

Runtime flow:

1. The user asks a troubleshooting question from the browser UI.
2. FastAPI receives the request and passes it into the query-processing flow.
3. The query is embedded with `all-MiniLM-L6-v2` using SentenceTransformers.
4. PostgreSQL with `pgvector` searches the stored incident embeddings built from the CSV knowledge base.
5. PostgreSQL returns the top 50 nearest incident matches.
6. The app converts raw scores into a simple confidence value and filters out low-relevance matches.
7. The filtered result set is capped before being sent to Ollama as grounded incident context.
8. Ollama generates the final streamed troubleshooting response.

## Tech Stack

- Python 3.9+
- FastAPI
- Uvicorn
- PostgreSQL
- pgvector
- SentenceTransformers
- Pandas
- Ollama
- Llama 3.2 via Ollama
- HTML/CSS/JavaScript frontend

## Project Structure

```text
sregpt/
├── app.py                # FastAPI app and streaming endpoint
├── embeddings.py         # Ingestion entrypoint for loading vectors into PostgreSQL
├── sregpt/
│   ├── config.py         # Shared env/config loading
│   └── vector_store.py   # Shared pgvector + embedding utilities
├── Dockerfile.api        # API image build
├── Dockerfile.ingest     # Ingestion image build
├── requirements.api.txt
├── requirements.common.txt
├── requirements.ingest.txt
├── index.html            # Frontend chat UI
├── k8s/
│   ├── sregpt-api.yaml         # API deployment/service
│   ├── sregpt-config.yaml      # Shared runtime config
│   ├── sregpt-ingest-job.yaml  # Ingestion batch job
│   └── postgres-pgvector.yaml  # PostgreSQL pod/service/PVC manifest
├── requirements.txt      # Python dependencies
├── data/
│   └── issues.csv        # Source incident dataset
└── .venv/                # Local virtual environment (ignored by git)
```

## Prerequisites

Make sure these are available on your machine:

- Python 3.9 or later
- `pip`
- Kubernetes access to deploy PostgreSQL and Ollama pods

## Setup

Create and activate a virtual environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```sh
pip install -r requirements.txt
```   

## Container Strategy

Use separate images for serving and ingestion:

- `Dockerfile.api` builds the FastAPI serving image
- `Dockerfile.ingest` builds the ingestion image that runs `embeddings.py`
- `sregpt/config.py` and `sregpt/vector_store.py` hold shared logic so the images stay separate without duplicating code

Build them locally:

```sh
docker build -f Dockerfile.api -t sregpt-api:latest .
docker build -f Dockerfile.ingest -t sregpt-ingest:latest .
```

## Start PostgreSQL with pgvector

Launch the database pod and service:

```sh
kubectl apply -f k8s/postgres-pgvector.yaml
kubectl apply -f k8s/sregpt-config.yaml
```

For local testing outside Kubernetes, port-forward the service:

```sh
kubectl port-forward svc/sregpt-postgres 5432:5432
```

Export the database connection settings before loading data or running the API:

```sh
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export POSTGRES_DB=sregpt
export POSTGRES_USER=sregpt
export POSTGRES_PASSWORD=sregpt123
```

## Prepare the Knowledge Base

The retrieval layer uses [`data/issues.csv`](/Users/sackashyap/Documents/mytech/sregpt/data/issues.csv) as the source dataset.

To build or rebuild the vector table in PostgreSQL:

```sh
python embeddings.py
```

To run the same load inside Kubernetes with the ingestion image:

```sh
kubectl apply -f k8s/sregpt-ingest-job.yaml
kubectl logs -n sregpt job/sregpt-ingest -f
```

Expected CSV columns:

- `Issue Subject`
- `Issue Solution`
- `Ticket ID`

## Start Ollama with Llama 3.2

Launch the Ollama pod and service:

```sh
kubectl apply -f k8s/ollama-llama32.yaml
```

The pod starts `ollama serve`, pulls `llama3.2`, and exposes it inside the cluster at:

```text
http://sregpt-ollama:11434
```

Set these environment variables in the FastAPI deployment:

```sh
export OLLAMA_HOST=http://sregpt-ollama:11434
export OLLAMA_MODEL=llama3.2
```

## Run the API

Start the FastAPI server:

```sh
uvicorn app:app --reload
```

The API will run at:

```text
http://127.0.0.1:8000
```

Health check:

```sh
curl http://127.0.0.1:8000/health
```

Example query:

```sh
curl "http://127.0.0.1:8000/ask-stream?query=node%20not%20ready"
```

To run the API in Kubernetes after building and pushing the image:

```sh
kubectl apply -f k8s/sregpt-api.yaml
```

## Frontend

The browser UI is served by FastAPI at `/` from [`index.html`](/Users/sackashyap/Documents/mytech/sregpt/index.html). It calls the same-host API endpoint:

```text
GET /ask-stream?query=...
```

The UI supports:

- chat-style interaction
- markdown rendering
- syntax-highlighted code blocks
- local chat persistence in browser storage
- clearing chat history

## API Endpoints

### `GET /`

Serves the browser UI from `index.html`.

### `GET /health`

Simple health endpoint.

Example response:

```json
{
  "message": "SREGPT Reasoning Mode with PostgreSQL pgvector"
}
```

### `GET /ask-stream?query=...`

Streams a troubleshooting response built from:

- top 50 PostgreSQL `pgvector` matches from the internal ticket dataset
- score-based relevance filtering with a `0.55` threshold
- a final capped context of up to 5 incidents
- `llama3.2` generation through Ollama

## How Retrieval Works

[`embeddings.py`](/Users/sackashyap/Documents/mytech/sregpt/embeddings.py) normalizes the CSV columns into this internal schema:

- `issue`
- `solution`
- `ticket`

[`app.py`](/Users/sackashyap/Documents/mytech/sregpt/app.py) then:

1. embeds the user query
2. searches PostgreSQL with `pgvector` using `k=12`
3. converts cosine distance into a simple confidence estimate using `1 - distance`
4. filters out low-relevance matches using a `0.55` threshold
5. caps the filtered context to 5 incidents to avoid overloading the prompt
6. sends the remaining incident context to Ollama with a shorter prompt and warm keep-alive
7. streams the final grounded answer back to the client

Current LLM settings in [`app.py`](/Users/sackashyap/Downloads/mytech/sregpt/app.py):

- Ollama endpoint: `OLLAMA_HOST` defaulting to `http://localhost:11434`
- Ollama model: `OLLAMA_MODEL` defaulting to `llama3.2`

Current retrieval settings in [`app.py`](/Users/sackashyap/Documents/mytech/sregpt/app.py):

- PostgreSQL `pgvector` search scope: `k=12`
- Filtering function: `filter_results(results, scores, threshold=0.55)`
- Max incident context sent to the LLM: `5`

You can tune these if you want more recall:

- `SEARCH_K` controls the initial vector search fan-out
- `MAX_CONTEXT_INCIDENTS` caps how many incidents reach the prompt
- `OLLAMA_NUM_PREDICT` limits the answer length
- `OLLAMA_KEEP_ALIVE` keeps the model warm between requests

## Common Commands

Activate virtual environment:

```sh
source .venv/bin/activate
```

Check installed package:

```sh
pip show fastapi
```

Rebuild embeddings:

```sh
python embeddings.py
```

Run app:

```sh
uvicorn app:app --reload
```

## Troubleshooting

### `could not connect to ollama server`

Check that the Ollama pod is running and the service is reachable:

```sh
kubectl get pods -n sregpt
kubectl get svc -n sregpt
```

### `could not connect to server at "127.0.0.1", port 5432`

Start the PostgreSQL service or port-forward it from Kubernetes, then rerun `python embeddings.py`.

```sh
kubectl port-forward -n sregpt svc/sregpt-postgres 5432:5432
```

### `500 Internal Server Error` from `/ask-stream`

Check:

- `/health` returns `200`
- the `incident_vectors` table exists
- PostgreSQL has rows from `python embeddings.py`
- `OLLAMA_HOST` points at `http://sregpt-ollama:11434` in the cluster
- `OLLAMA_MODEL` is set to `llama3.2`

## Git Ignore

This repo ignores the local virtual environment through [`.gitignore`](/Users/sackashyap/Documents/mytech/sregpt/.gitignore):

```gitignore
.venv/
```

## Notes

- This project is designed for local enterprise-style troubleshooting workflows.
- It currently uses local Ollama inference instead of a cloud LLM API.
- The incident knowledge base is file-based and suitable for a small internal dataset. For production-scale usage, move ticket data and vector storage into managed services with explicit versioning and ingestion pipelines.
