from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VECTOR_DIMS = 384
BM25_K1 = 1.5
BM25_B = 0.75
PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class HybridRepairRAG:
    def __init__(self, index_path: Path, *, vector_db: str = "sqlite", embedding_config: dict[str, Any] | None = None) -> None:
        self.index_path = index_path
        self.vector_db = vector_db
        self.embedding_provider = create_embedding_provider(embedding_config or {})
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def rebuild(self, *, playbooks: list[dict[str, Any]], repairs: list[dict[str, Any]]) -> None:
        documents = [document_from_playbook(playbook) for playbook in playbooks]
        documents.extend(document_from_repair(repair) for repair in repairs)
        with sqlite3.connect(self.index_path) as conn:
            conn.execute("delete from rag_documents")
            conn.executemany(
                """
                insert into rag_documents(id, source, text, payload_json, vector_json, updated_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        doc["id"],
                        doc["source"],
                        doc["text"],
                        json.dumps(doc["payload"], ensure_ascii=False),
                        json.dumps(self.embedding_provider.embed_texts([doc["text"]])[0]),
                        datetime.now(timezone.utc).isoformat(),
                    )
                    for doc in documents
                ],
            )
        if self.vector_db == "chroma":
            ChromaVectorStore.from_index_path(self.index_path).rebuild(documents, self.embedding_provider)
        elif self.vector_db != "sqlite":
            raise ValueError(f"Unsupported vector DB backend: {self.vector_db}")

    def retrieve(self, query_text: str, *, top_k: int = 5) -> dict[str, Any]:
        documents = self._load_documents()
        if not documents:
            return {"hits": [], "stats": {"documents": 0, "queryTokens": []}}

        query_tokens = tokenize(query_text)
        bm25_scores = score_bm25(query_tokens, documents)
        vector_scores = self._vector_scores(query_text, documents)
        max_bm25 = max(bm25_scores) if bm25_scores else 0

        hits = []
        for doc, bm25_score, vector_score in zip(documents, bm25_scores, vector_scores):
            normalized_bm25 = bm25_score / max_bm25 if max_bm25 > 0 else 0.0
            confidence = float(doc["payload"].get("confidence", 0.7) or 0.7)
            hybrid_score = normalized_bm25 * 0.55 + vector_score * 0.35 + min(confidence, 1.0) * 0.10
            if hybrid_score <= 0.05:
                continue
            hits.append(
                {
                    **to_repair_hit(doc["payload"]),
                    "source": doc["source"],
                    "score": round(hybrid_score, 3),
                    "hybridScore": round(hybrid_score, 3),
                    "bm25Score": round(normalized_bm25, 3),
                    "rawBm25Score": round(bm25_score, 3),
                    "vectorScore": round(vector_score, 3),
                    "matchedTerms": matched_terms(query_tokens, doc["tokens"]),
                    "retrieval": "hybrid-bm25-vector",
                }
            )
        hits = sorted(hits, key=lambda item: item["hybridScore"], reverse=True)[:top_k]
        return {
            "hits": hits,
            "stats": {
                "documents": len(documents),
                "queryTokens": query_tokens[:30],
                "indexPath": str(self.index_path),
                "vectorBackend": self.vector_db,
                "vectorDbPath": str(chroma_path_for(self.index_path)) if self.vector_db == "chroma" else str(self.index_path),
                "embeddingProvider": self.embedding_provider.name,
                "embeddingModel": self.embedding_provider.model,
                "vectorDims": self.embedding_provider.dimensions,
                "ranker": "0.55*BM25 + 0.35*vector + 0.10*confidence",
            },
        }

    def _vector_scores(self, query_text: str, documents: list[dict[str, Any]]) -> list[float]:
        if self.vector_db == "chroma":
            scores_by_id = ChromaVectorStore.from_index_path(self.index_path).query(query_text, len(documents), self.embedding_provider)
            return [scores_by_id.get(doc["id"], 0.0) for doc in documents]
        if self.vector_db != "sqlite":
            raise ValueError(f"Unsupported vector DB backend: {self.vector_db}")
        query_vector = self.embedding_provider.embed_texts([query_text])[0]
        return [cosine(query_vector, doc["vector"]) for doc in documents]

    def _init_db(self) -> None:
        with sqlite3.connect(self.index_path) as conn:
            conn.execute(
                """
                create table if not exists rag_documents (
                  id text primary key,
                  source text not null,
                  text text not null,
                  payload_json text not null,
                  vector_json text not null,
                  updated_at text not null
                )
                """
            )

    def _load_documents(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.index_path) as conn:
            rows = conn.execute("select id, source, text, payload_json, vector_json from rag_documents").fetchall()
        docs = []
        for doc_id, source, text, payload_json, vector_json in rows:
            tokens = tokenize(text)
            docs.append(
                {
                    "id": doc_id,
                    "source": source,
                    "text": text,
                    "tokens": tokens,
                    "termCounts": Counter(tokens),
                    "payload": json.loads(payload_json),
                    "vector": json.loads(vector_json),
                }
            )
        return docs


def rag_index_path_for(memory_path: Path | None) -> Path:
    if memory_path:
        return memory_path.with_suffix(".rag.sqlite")
    return Path("artifacts/memory/repair-rag.sqlite").resolve()


def chroma_path_for(index_path: Path) -> Path:
    return index_path.with_suffix(".chroma")


def build_repair_query(*, fingerprint: dict[str, Any], raw_log: str = "", reproduction: dict[str, Any] | None = None) -> str:
    reproduction = reproduction or {}
    return "\n".join(
        [
            f"normalizedSignature: {fingerprint.get('normalizedSignature', '')}",
            f"failureType: {fingerprint.get('failureType', '')}",
            f"errorCode: {fingerprint.get('errorCode', '')}",
            f"language: {fingerprint.get('language', '')}",
            f"packageManager: {fingerprint.get('packageManager', '')}",
            f"failedFiles: {' '.join(fingerprint.get('failedFiles', []))}",
            f"changedFiles: {' '.join(fingerprint.get('changedFiles', []))}",
            f"command: {fingerprint.get('command', '')}",
            f"ciLog: {raw_log[:4000]}",
            f"stdout: {(reproduction.get('stdout') or '')[:2000]}",
            f"stderr: {(reproduction.get('stderr') or '')[:2000]}",
        ]
    )


def document_from_playbook(playbook: dict[str, Any]) -> dict[str, Any]:
    payload = {
        **playbook,
        "source": "static-playbook",
        "fingerprint": {
            "normalizedSignature": playbook.get("failureSignature"),
            "failureType": playbook.get("failureType"),
            "errorCode": playbook.get("errorCode"),
            "language": playbook.get("language"),
        },
    }
    text = " ".join(
        [
            str(playbook.get("id", "")),
            str(playbook.get("failureSignature", "")),
            str(playbook.get("failureType", "")),
            str(playbook.get("errorCode", "")),
            str(playbook.get("language", "")),
            " ".join(playbook.get("changedFilePatterns", [])),
            str(playbook.get("strategy", "")),
            " ".join(playbook.get("verificationCommands", [])),
        ]
    )
    return {"id": f"playbook:{playbook['id']}", "source": "static-playbook", "text": text, "payload": payload}


def document_from_repair(repair: dict[str, Any]) -> dict[str, Any]:
    fingerprint = repair.get("fingerprint", {})
    patch_summary = repair.get("patchSummary", {})
    text = " ".join(
        [
            str(repair.get("id", "")),
            str(fingerprint.get("normalizedSignature", "")),
            str(fingerprint.get("failureType", "")),
            str(fingerprint.get("errorCode", "")),
            str(fingerprint.get("language", "")),
            str(fingerprint.get("packageManager", "")),
            str(repair.get("strategy", "")),
            " ".join(patch_summary.get("changedFiles", [])),
            " ".join(patch_summary.get("riskTags", [])),
            " ".join(repair.get("verificationCommands", [])),
            " ".join(repair.get("examplePatchIds", [])),
        ]
    )
    return {"id": f"repair:{repair['id']}", "source": "verified-repair", "text": text, "payload": {**repair, "source": "verified-repair"}}


def to_repair_hit(payload: dict[str, Any]) -> dict[str, Any]:
    fingerprint = payload.get("fingerprint", {})
    return {
        "id": payload.get("id", "unknown"),
        "failureSignature": payload.get("failureSignature") or fingerprint.get("normalizedSignature"),
        "failureType": payload.get("failureType") or fingerprint.get("failureType"),
        "errorCode": payload.get("errorCode") or fingerprint.get("errorCode"),
        "language": payload.get("language") or fingerprint.get("language"),
        "strategy": payload.get("strategy", "Reuse retrieved repair evidence."),
        "verificationCommands": payload.get("verificationCommands", []),
        "successCount": payload.get("successCount", 0),
        "failureCount": payload.get("failureCount", 0),
        "confidence": payload.get("confidence", 0.7),
        "reasons": [],
    }


def score_bm25(query_tokens: list[str], documents: list[dict[str, Any]]) -> list[float]:
    if not query_tokens:
        return [0.0 for _ in documents]
    total_docs = len(documents)
    avgdl = sum(len(doc["tokens"]) for doc in documents) / total_docs
    dfs = Counter()
    for doc in documents:
        for token in set(doc["tokens"]):
            dfs[token] += 1

    scores = []
    for doc in documents:
        score = 0.0
        doc_len = len(doc["tokens"]) or 1
        for token in query_tokens:
            tf = doc["termCounts"].get(token, 0)
            if not tf:
                continue
            idf = math.log(1 + (total_docs - dfs[token] + 0.5) / (dfs[token] + 0.5))
            denom = tf + BM25_K1 * (1 - BM25_B + BM25_B * doc_len / avgdl)
            score += idf * (tf * (BM25_K1 + 1)) / denom
        scores.append(score)
    return scores


class EmbeddingProvider:
    name = "base"
    model = "base"
    dimensions = VECTOR_DIMS

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class HashEmbeddingProvider(EmbeddingProvider):
    name = "hash"
    model = "hashing-vector"
    dimensions = VECTOR_DIMS

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [hash_vectorize(text, self.dimensions) for text in texts]


class HttpEmbeddingProvider(EmbeddingProvider):
    env_key = ""
    default_base_url = ""
    default_model = ""
    default_dimensions = 1024
    batch_size = 10

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, model: str | None = None, dimensions: int | None = None) -> None:
        self.api_key = api_key or os.getenv(self.env_key)
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.model = model or self.default_model
        self.dimensions = int(dimensions or self.default_dimensions)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise RuntimeError(f"{self.env_key} is not set; cannot use {self.name} embeddings.")
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            embeddings.extend(self._embed_batch(texts[start : start + self.batch_size]))
        return embeddings

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        body = self._request_body(texts)
        request = urllib.request.Request(
            self.endpoint(),
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            hint = ""
            if "Model.AccessDenied" in detail:
                hint = " Hint: enable this embedding model for the API key in the provider console, or switch model/region."
            raise RuntimeError(f"{self.name} embedding request failed: HTTP {error.code} {detail}{hint}") from error
        return [normalize_vector(item["embedding"]) for item in payload.get("data", [])]

    def endpoint(self) -> str:
        raise NotImplementedError

    def _request_body(self, texts: list[str]) -> dict[str, Any]:
        return {"model": self.model, "input": texts, "dimensions": self.dimensions, "encoding_format": "float"}


class DashScopeEmbeddingProvider(HttpEmbeddingProvider):
    name = "dashscope"
    env_key = "DASHSCOPE_API_KEY"
    default_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    default_model = "text-embedding-v4"
    default_dimensions = 1024
    batch_size = 10

    def endpoint(self) -> str:
        return f"{self.base_url}/embeddings"


class ZhipuEmbeddingProvider(HttpEmbeddingProvider):
    name = "zhipu"
    env_key = "ZHIPU_API_KEY"
    default_base_url = "https://open.bigmodel.cn/api/paas/v4"
    default_model = "embedding-3"
    default_dimensions = 1024
    batch_size = 64

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not self.api_key:
            self.api_key = os.getenv("ZAI_API_KEY")

    def endpoint(self) -> str:
        return f"{self.base_url}/embeddings"


def create_embedding_provider(config: dict[str, Any]) -> EmbeddingProvider:
    provider = str(config.get("provider") or "hash").lower()
    common = {
        "api_key": config.get("api_key"),
        "base_url": config.get("base_url"),
        "model": config.get("model"),
        "dimensions": config.get("dimensions"),
    }
    if provider == "hash":
        return HashEmbeddingProvider()
    if provider == "dashscope":
        return DashScopeEmbeddingProvider(**common)
    if provider == "zhipu":
        return ZhipuEmbeddingProvider(**common)
    raise ValueError(f"Unsupported embedding provider: {provider}")


def embedding_config_from_flags(flags: dict[str, Any]) -> dict[str, Any]:
    provider = str(flags.get("embedding-provider") or os.getenv("CIFIX_EMBEDDING_PROVIDER") or "hash")
    return {
        "provider": provider,
        "model": flags.get("embedding-model") or os.getenv("CIFIX_EMBEDDING_MODEL") or default_embedding_model(provider),
        "dimensions": int(flags.get("embedding-dimensions") or os.getenv("CIFIX_EMBEDDING_DIMENSIONS") or default_embedding_dimensions(provider)),
        "base_url": flags.get("embedding-base-url") or os.getenv("CIFIX_EMBEDDING_BASE_URL") or default_embedding_base_url(provider),
        "api_key": flags.get("embedding-api-key"),
    }


def default_embedding_model(provider: str) -> str:
    return {"dashscope": "text-embedding-v4", "zhipu": "embedding-3"}.get(provider, "hashing-vector")


def default_embedding_dimensions(provider: str) -> int:
    return {"dashscope": 1024, "zhipu": 1024}.get(provider, VECTOR_DIMS)


def default_embedding_base_url(provider: str) -> str | None:
    return {
        "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    }.get(provider)


def hash_vectorize(text: str, dimensions: int = VECTOR_DIMS) -> list[float]:
    tokens = tokenize(text)
    if len(tokens) > 1:
        tokens.extend(f"{left}_{right}" for left, right in zip(tokens, tokens[1:]))
    vector = [0.0] * dimensions
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        raw = int.from_bytes(digest, "big")
        index = raw % dimensions
        sign = 1.0 if (raw >> 9) % 2 == 0 else -1.0
        vector[index] += sign
    return normalize_vector(vector)


def normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [round(value / norm, 6) for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return max(0.0, sum(a * b for a, b in zip(left, right)))


def tokenize(text: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_./:-]*|ERR_[A-Z_]+|TS\d{4}|\d+", text)
    tokens = []
    for token in raw_tokens:
        lowered = token.lower().strip(".,;()[]{}'\"")
        if not lowered:
            continue
        tokens.append(lowered)
        if ":" in lowered:
            tokens.extend(part for part in lowered.split(":") if part)
        if "/" in lowered:
            tokens.extend(part for part in lowered.split("/") if part)
    return tokens


def matched_terms(query_tokens: list[str], doc_tokens: list[str]) -> list[str]:
    doc_set = set(doc_tokens)
    return sorted({token for token in query_tokens if token in doc_set})[:12]


class ChromaVectorStore:
    def __init__(self, persist_path: Path, collection_name: str) -> None:
        self.persist_path = persist_path
        self.collection_name = collection_name

    @classmethod
    def from_index_path(cls, index_path: Path) -> "ChromaVectorStore":
        digest = hashlib.blake2b(str(index_path.resolve()).encode("utf-8"), digest_size=6).hexdigest()
        return cls(chroma_path_for(index_path), f"cifix_repair_{digest}")

    def rebuild(self, documents: list[dict[str, Any]], embedding_provider: EmbeddingProvider) -> None:
        chromadb = import_chromadb()
        self.persist_path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(self.persist_path))
        try:
            client.delete_collection(self.collection_name)
        except Exception:
            pass
        collection = client.create_collection(name=self.collection_name, metadata={"hnsw:space": "cosine"})
        if not documents:
            return
        collection.add(
            ids=[doc["id"] for doc in documents],
            documents=[doc["text"] for doc in documents],
            embeddings=embedding_provider.embed_texts([doc["text"] for doc in documents]),
            metadatas=[{"source": doc["source"]} for doc in documents],
        )

    def query(self, query_text: str, top_k: int, embedding_provider: EmbeddingProvider | None = None) -> dict[str, float]:
        chromadb = import_chromadb()
        client = chromadb.PersistentClient(path=str(self.persist_path))
        collection = client.get_collection(name=self.collection_name)
        provider = embedding_provider or HashEmbeddingProvider()
        result = collection.query(query_embeddings=provider.embed_texts([query_text]), n_results=max(1, top_k))
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return {doc_id: max(0.0, 1.0 - float(distance)) for doc_id, distance in zip(ids, distances)}


def import_chromadb() -> Any:
    try:
        import chromadb
    except ImportError as error:
        raise RuntimeError(
            "ChromaDB is not installed. Install vector DB support with `python3 -m pip install 'chromadb>=0.5'` "
            "or use the default `--vector-db sqlite` fallback."
        ) from error
    return chromadb


def query_repair_rag(flags: dict[str, Any]) -> dict[str, Any]:
    query = str(flags.get("query") or "").strip()
    if not query:
        raise ValueError("rag needs --query <text>")
    memory_path = Path(flags.get("memory-path") or "artifacts/memory/verified-repairs.json").resolve()
    vector_db = str(flags.get("vector-db") or "sqlite")
    embedding_config = embedding_config_from_flags(flags)
    playbooks = json.loads((PACKAGE_ROOT / "data" / "playbooks.json").read_text())
    repairs = load_repair_memory(memory_path)
    rag = HybridRepairRAG(rag_index_path_for(memory_path), vector_db=vector_db, embedding_config=embedding_config)
    rag.rebuild(playbooks=playbooks, repairs=repairs)
    return rag.retrieve(query, top_k=int(flags.get("top-k") or 5))


def load_repair_memory(memory_path: Path) -> list[dict[str, Any]]:
    if not memory_path.exists():
        return []
    try:
        loaded = json.loads(memory_path.read_text())
    except json.JSONDecodeError:
        return []
    records = loaded.get("repairs", []) if isinstance(loaded, dict) else loaded
    return records if isinstance(records, list) else []
