from sregpt.vector_store import load_records_into_postgres, read_issue_records


def build_index():
    records = read_issue_records()
    load_records_into_postgres(records)
    print(f"Loaded {len(records)} records into PostgreSQL")


if __name__ == "__main__":
    build_index()
