import json
import os
from pathlib import Path

import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from sregpt.config import MATCH_THRESHOLD, OLLAMA_HOST, OLLAMA_MODEL
from sregpt.vector_store import search_incidents

SEARCH_K = int(os.getenv("SEARCH_K", "12"))
MAX_CONTEXT_INCIDENTS = int(os.getenv("MAX_CONTEXT_INCIDENTS", "5"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "10m")
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "256"))
CHAT_HISTORY_TURNS = int(os.getenv("CHAT_HISTORY_TURNS", "8"))

app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
INDEX_HTML = BASE_DIR / "index.html"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def filter_results(results, scores, threshold=0.7):
    filtered = []

    for result, score in zip(results, scores):
        confidence = max(0.0, 1 - float(score))
        if confidence > threshold:
            filtered.append(result)

    return filtered


def build_context(results):
    context = []
    for result in results:
        context.append(
            f"Ticket: {result['ticket']} | "
            f"Issue: {result['issue']} | "
            f"Fix: {result['solution']}"
        )
    return "\n".join(context)


def build_fallback_context(query):
    lowered_query = query.lower()

    if "crashloop" in lowered_query:
        return (
            "Fallback Kubernetes guidance:\n"
            "- Check pod logs for the crash reason.\n"
            "- Inspect image, command, env vars, and mounted secrets/configmaps.\n"
            "- Run `kubectl describe pod <pod> -n <namespace>` to review events.\n"
            "- Fix the failing app config or dependency, then restart the deployment."
        )

    if "imagepull" in lowered_query or "image pull" in lowered_query:
        return (
            "Fallback Kubernetes guidance:\n"
            "- Verify the image name and tag.\n"
            "- Confirm registry access and image pull secrets.\n"
            "- Check pod events for `ErrImagePull` or `ImagePullBackOff`.\n"
            "- Redeploy after correcting registry credentials or image references."
        )

    if "pod is down" in lowered_query or "pod down" in lowered_query:
        return (
            "Fallback Kubernetes guidance:\n"
            "- Check whether the pod is `Pending`, `CrashLoopBackOff`, `ImagePullBackOff`, or `Evicted`.\n"
            "- Inspect `kubectl get pod -n <namespace> -o wide` and `kubectl describe pod <pod> -n <namespace>`.\n"
            "- Review recent deployment changes and resource limits.\n"
            "- Restart the workload only after identifying the root cause."
        )

    return (
        "Fallback troubleshooting guidance:\n"
        "- Ask for the workload name and namespace.\n"
        "- Inspect pod status, events, and logs.\n"
        "- Check recent deploys, config changes, and resource pressure.\n"
        "- Provide the most likely fix path, then ask one short follow-up only if needed."
    )


def build_history_block(history):
    lines = []

    for message in history[-CHAT_HISTORY_TURNS * 2 :]:
        role = "User" if message.get("type") == "user" else "Assistant"
        text = message.get("text", "").strip()
        if text:
            lines.append(f"{role}: {text}")

    return "\n".join(lines)


def build_retrieval_prompt(query, context, history):
    conversation = build_history_block(history)
    fallback_guidance = build_fallback_context(query)

    return f"""
You are SREGPT, a senior SRE assistant inside an ongoing troubleshooting session.

Conversation so far:
{conversation}

Current issue:
{query}

Relevant incidents:
{context}

Answer contract:
- Use exactly these headings in this order:
  1. Root cause
  2. Fix now
  3. Verify
  4. If still broken, do this next
- Keep each section short, technical, and actionable.
- Include related tickets only when they support the diagnosis.
- If the evidence is incomplete, state the assumption inside the relevant section and continue with the best next action.
- Do not ask for more details unless you truly cannot suggest a next step.
- Keep the session open by ending with one short follow-up question only when needed.

Fallback guidance for similar issues:
{fallback_guidance}
"""


def build_fallback_prompt(query, history):
    conversation = build_history_block(history)
    fallback_guidance = build_fallback_context(query)

    return f"""
You are SREGPT, a senior SRE assistant inside an ongoing troubleshooting session.

Conversation so far:
{conversation}

Current issue:
{query}

No strong incident matches were found in the vector database.

Use the following fallback guidance and general SRE knowledge:
{fallback_guidance}

Answer contract:
- Use exactly these headings in this order:
  1. Root cause
  2. Fix now
  3. Verify
  4. If still broken, do this next
- Make the first two sections concrete even if the incident matches are weak.
- Explain the expected symptom-to-cause mapping only where relevant.
- If details are missing, state the assumption and continue with the best next action.
- Do not stop at clarifying questions.
- Keep the session open by ending with one short follow-up question only if absolutely necessary.
"""


def orchestrate_prompt(query, results, scores, history):
    filtered_results = filter_results(results, scores, threshold=MATCH_THRESHOLD)
    filtered_results = filtered_results[:MAX_CONTEXT_INCIDENTS]

    if filtered_results:
        context = build_context(filtered_results)
        return {
            "mode": "retrieval",
            "prompt": build_retrieval_prompt(query, context, history),
            "matches_found": len(filtered_results),
            "total_matches": len(results),
        }

    return {
        "mode": "fallback",
        "prompt": build_fallback_prompt(query, history),
        "matches_found": 0,
        "total_matches": len(results),
    }


def stream_reasoning(prompt):
    
    response = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": True,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": {
                "num_predict": OLLAMA_NUM_PREDICT,
                "temperature": 0.2,
            },
        },
        stream=True,
    )

    for line in response.iter_lines():
        if line:
            try:
                data = json.loads(line.decode("utf-8"))
                if "response" in data:
                    yield data["response"]
            except Exception:
                continue


@app.get("/")
def home():
    return FileResponse(INDEX_HTML)


@app.get("/health")
def health():
    return {"message": "SREGPT Reasoning Mode with PostgreSQL pgvector"}


@app.post("/ask-stream")
def ask_stream(payload: dict):
    query = (payload.get("query") or "").strip()
    history = payload.get("history") or []
    results, scores = search_incidents(query, k=SEARCH_K)
    orchestration = orchestrate_prompt(query, results, scores, history)

    def final_stream():
        yield "⏳ Searching relevant incidents...\n\n"
        yield f"""
## 🔍 Issue Analysis Started

📌 Query: {query}

🤖 Mode: {orchestration['mode']}
🤖 Found {orchestration['matches_found']} relevant incidents (from {orchestration['total_matches']} total matches)
"""

        for chunk in stream_reasoning(orchestration["prompt"]):
            yield chunk

    return StreamingResponse(final_stream(), media_type="text/plain")


@app.get("/ask-stream")
def ask_stream_legacy(query: str):
    return ask_stream({"query": query, "history": []})
