from functools import lru_cache

import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql
from sentence_transformers import SentenceTransformer

from sregpt.config import BATCH_SIZE, CSV_PATH, DB_CONFIG, MODEL_NAME, VECTOR_TABLE


@lru_cache(maxsize=1)
def get_model():
    return SentenceTransformer(MODEL_NAME)


def get_db_connection():
    conn = psycopg.connect(**DB_CONFIG)
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    register_vector(conn)
    return conn


def normalize_record(record):
    return {
        "issue": record.get("issue") or record.get("Issue Subject") or "",
        "solution": record.get("solution") or record.get("Issue Solution") or "",
        "ticket": record.get("ticket") or record.get("Ticket ID") or "N/A",
    }


def read_issue_records(csv_path=CSV_PATH):
    import pandas as pd

    df = pd.read_csv(csv_path)
    df = df.rename(
        columns={
            "Issue Subject": "issue",
            "Issue Solution": "solution",
            "Ticket ID": "ticket",
        }
    )

    df["issue"] = df["issue"].fillna("")
    df["solution"] = df["solution"].fillna("")
    df["ticket"] = df["ticket"].fillna("N/A")

    return df[["issue", "solution", "ticket"]].to_dict(orient="records")


def ensure_schema(conn, dimension):
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {table} (
                    id BIGSERIAL PRIMARY KEY,
                    issue TEXT NOT NULL,
                    solution TEXT NOT NULL,
                    ticket TEXT NOT NULL,
                    embedding vector({dimension}) NOT NULL
                )
                """
            ).format(
                table=sql.Identifier(VECTOR_TABLE),
                dimension=sql.SQL(str(dimension)),
            )
        )
        cur.execute(
            sql.SQL("TRUNCATE TABLE {table}").format(
                table=sql.Identifier(VECTOR_TABLE)
            )
        )
    conn.commit()


def create_vector_index(conn):
    index_name = f"{VECTOR_TABLE}_embedding_idx"

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("DROP INDEX IF EXISTS {index_name}").format(
                index_name=sql.Identifier(index_name)
            )
        )
        cur.execute(
            sql.SQL(
                """
                CREATE INDEX {index_name}
                ON {table}
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
                """
            ).format(
                index_name=sql.Identifier(index_name),
                table=sql.Identifier(VECTOR_TABLE),
            )
        )
        cur.execute(
            sql.SQL("ANALYZE {table}").format(
                table=sql.Identifier(VECTOR_TABLE)
            )
        )
    conn.commit()


def load_records_into_postgres(records, batch_size=BATCH_SIZE):
    model = get_model()
    texts = [f"{record['issue']} {record['solution']}" for record in records]
    embeddings = model.encode(texts, normalize_embeddings=True)
    dimension = len(embeddings[0])

    insert_sql = sql.SQL(
        """
        INSERT INTO {table} (issue, solution, ticket, embedding)
        VALUES (%s, %s, %s, %s)
        """
    ).format(table=sql.Identifier(VECTOR_TABLE))

    with get_db_connection() as conn:
        ensure_schema(conn, dimension)

        with conn.cursor() as cur:
            for start in range(0, len(records), batch_size):
                chunk = records[start:start + batch_size]
                chunk_embeddings = embeddings[start:start + batch_size]
                rows = [
                    (
                        record["issue"],
                        record["solution"],
                        record["ticket"],
                        embedding.tolist(),
                    )
                    for record, embedding in zip(chunk, chunk_embeddings)
                ]
                cur.executemany(insert_sql, rows)
        conn.commit()
        create_vector_index(conn)


def search_incidents(query, k=50):
    model = get_model()
    q_emb = model.encode(query, normalize_embeddings=True).tolist()

    query_sql = sql.SQL(
        """
        SELECT issue, solution, ticket, embedding <=> %s::vector AS distance
        FROM {table}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """
    ).format(table=sql.Identifier(VECTOR_TABLE))

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query_sql, (q_emb, q_emb, k))
            rows = cur.fetchall()

    results = [
        normalize_record(
            {"issue": issue, "solution": solution, "ticket": ticket}
        )
        for issue, solution, ticket, _distance in rows
    ]
    scores = [distance for _issue, _solution, _ticket, distance in rows]

    return results, scores
