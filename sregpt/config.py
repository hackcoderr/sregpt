import os


MODEL_NAME = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
VECTOR_TABLE = os.getenv("PGVECTOR_TABLE", "incident_vectors")
CSV_PATH = os.getenv("ISSUES_CSV_PATH", "data/issues.csv")
BATCH_SIZE = int(os.getenv("PGVECTOR_BATCH_SIZE", "200"))
MATCH_THRESHOLD = float(os.getenv("MATCH_THRESHOLD", "0.55"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname": os.getenv("POSTGRES_DB", "sregpt"),
    "user": os.getenv("POSTGRES_USER", "sregpt"),
    "password": os.getenv("POSTGRES_PASSWORD", "sregpt123"),
}
