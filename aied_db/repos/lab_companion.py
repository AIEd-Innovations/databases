"""`LabCompanionRepo` — RAG documents, chunks, student files and query log.

The retrieval method satisfies ui-student's `KnowledgeBase` protocol
(`agentic_system/kb/base.py`), so a Mongo-backed KB is a thin adapter:

    class MongoKB:
        def __init__(self, repo): self._repo = repo
        def search(self, query, top=None):
            return [RetrievedDoc(**hit) for hit in
                    self._repo.search(embedding_of(query), top_k=top or 3)]

`search()` returns dicts with exactly `content`, `source` and `uid` so that
adapter needs no field mapping. Embedding the query text is the caller's job:
this package holds no model client and makes no network calls of its own.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from .._util import new_id, now, strip_id, to_iso
from ..schema.lab_companion import EMBEDDING_DIMS, VECTOR_INDEX_NAME


class LabCompanionRepo:
    """Course knowledge base and student uploads for one course."""

    def __init__(self, db, course_id: str) -> None:
        if not course_id:
            raise ValueError("course_id is required")
        self._db = db
        self._course_id = course_id

    @property
    def course_id(self) -> str:
        return self._course_id

    def _scope(self, **extra: Any) -> dict[str, Any]:
        return {"course_id": self._course_id, **extra}

    @staticmethod
    def _row(document: Optional[dict], *date_fields: str) -> Optional[dict]:
        if document is None:
            return None
        row = strip_id(document) or {}
        for field in date_fields:
            if field in row:
                row[field] = to_iso(row[field])
        return row

    # ----------------------------------------------------------- documents
    def add_document(self, document: dict) -> dict:
        """Register an instructor upload. Human data — agents may not edit it."""
        document_id = document.get("document_id") or new_id()
        filename = document.get("filename")
        extracted_text = document.get("extracted_text", "")
        record = {
            "document_id": document_id,
            "course_id": self._course_id,
            "lab_id": document.get("lab_id"),
            "title": document.get("title") or filename or document_id,
            "filename": filename,
            "content_type": document.get("content_type"),
            "size_bytes": document.get("size_bytes"),
            "storage_key": document.get("storage_key")
            or f"rag/{self._course_id}/{document_id}/{filename or document_id}",
            "extracted_text": extracted_text,
            "char_count": int(document.get("char_count", len(extracted_text))),
            "chunk_count": 0,
            "uploaded_by": document.get("uploaded_by"),
            "uploaded_at": now(),
            "indexed_at": None,
        }
        self._db["rag_documents"].insert_one(record)
        return self._row(record, "uploaded_at", "indexed_at")

    def get_document(self, document_id: str) -> Optional[dict]:
        return self._row(
            self._db["rag_documents"].find_one(self._scope(document_id=document_id)),
            "uploaded_at",
            "indexed_at",
        )

    def list_documents(self, *, lab_id: Optional[str] = None, unindexed_only: bool = False) -> list[dict]:
        query = self._scope()
        if lab_id is not None:
            query["lab_id"] = lab_id
        if unindexed_only:
            query["indexed_at"] = None
        cursor = self._db["rag_documents"].find(query).sort("uploaded_at", -1)
        return [self._row(d, "uploaded_at", "indexed_at") for d in cursor]

    # ------------------------------------------------------------- vectors
    def add_chunks(self, document_id: str, chunks: Sequence[dict], *, embedding_model: str) -> int:
        """Append embedded chunks for a document, then stamp it as indexed.

        Each chunk needs `chunk_index`, `content` and `embedding`. Returns how
        many were written. Re-indexing a document inserts new `chunk_index`
        values rather than editing old ones — `rag_vectors` is append-only to
        the agent, so stale chunks are cleared by an instructor or admin.
        """
        if not chunks:
            return 0

        document = self._db["rag_documents"].find_one(self._scope(document_id=document_id))
        if document is None:
            raise ValueError(f"no rag_document {document_id!r} in course {self._course_id!r}")

        moment = now()
        records = []
        for chunk in chunks:
            embedding = list(chunk["embedding"])
            if len(embedding) != EMBEDDING_DIMS:
                raise ValueError(
                    f"embedding must have {EMBEDDING_DIMS} dimensions "
                    f"(index {VECTOR_INDEX_NAME}), got {len(embedding)}"
                )
            chunk_index = int(chunk["chunk_index"])
            content = chunk["content"]
            records.append(
                {
                    "vector_id": chunk.get("vector_id") or f"{document_id}_{chunk_index}",
                    "document_id": document_id,
                    "course_id": self._course_id,
                    "lab_id": document.get("lab_id"),
                    "chunk_index": chunk_index,
                    "content": content,
                    "embedding": [float(value) for value in embedding],
                    "embedding_model": embedding_model,
                    "char_count": len(content),
                    "created_at": moment,
                }
            )

        self._db["rag_vectors"].insert_many(records)
        self._db["rag_documents"].update_one(
            self._scope(document_id=document_id),
            {"$set": {"chunk_count": len(records), "indexed_at": moment}},
        )
        return len(records)

    def search(
        self,
        embedding: Sequence[float],
        *,
        top_k: int = 3,
        lab_id: Optional[str] = None,
        num_candidates: Optional[int] = None,
    ) -> list[dict]:
        """Vector search, returning `content` / `source` / `uid` per hit.

        Those three keys are `RetrievedDoc`'s fields, so the result feeds
        ui-student's KnowledgeBase protocol directly. `uid` is the
        `vector_id`, which is `f"{document_id}_{chunk_index}"` — the same
        composition Azure AI Search used.

        Needs the Atlas vector index; `$vectorSearch` is not available on a
        local mongod or in mongomock, so unit tests exercise `_hit_row`
        instead and this path is covered by the Atlas smoke test.
        """
        if len(embedding) != EMBEDDING_DIMS:
            raise ValueError(
                f"query embedding must have {EMBEDDING_DIMS} dimensions, got {len(embedding)}"
            )

        filters: dict[str, Any] = {"course_id": self._course_id}
        if lab_id is not None:
            filters["lab_id"] = lab_id

        pipeline: list[dict[str, Any]] = [
            {
                "$vectorSearch": {
                    "index": VECTOR_INDEX_NAME,
                    "path": "embedding",
                    "queryVector": [float(value) for value in embedding],
                    "numCandidates": num_candidates or max(top_k * 10, 100),
                    "limit": top_k,
                    "filter": filters,
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "vector_id": 1,
                    "document_id": 1,
                    "content": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]
        hits = list(self._db["rag_vectors"].aggregate(pipeline))
        titles = self._titles_for({hit["document_id"] for hit in hits})
        return [self._hit_row(hit, titles) for hit in hits]

    def _titles_for(self, document_ids: set[str]) -> dict[str, str]:
        if not document_ids:
            return {}
        cursor = self._db["rag_documents"].find(
            {"course_id": self._course_id, "document_id": {"$in": list(document_ids)}}
        )
        return {d["document_id"]: d.get("title", "") for d in cursor}

    @staticmethod
    def _hit_row(hit: dict, titles: dict[str, str]) -> dict:
        """One search hit as a `RetrievedDoc` field mapping."""
        return {
            "content": hit["content"],
            "source": titles.get(hit["document_id"], ""),
            "uid": hit["vector_id"],
            "score": hit.get("score"),
            "document_id": hit["document_id"],
        }

    # -------------------------------------------------------- student files
    def add_student_file(self, file: dict) -> dict:
        """Register a student upload. The student's own data; agents only read."""
        file_id = file.get("file_id") or new_id()
        filename = file["filename"]
        extracted_text = file.get("extracted_text", "")
        record = {
            "file_id": file_id,
            "course_id": self._course_id,
            "student_id": file["student_id"],
            "lab_id": file.get("lab_id"),
            "session_id": file.get("session_id"),
            "filename": filename,
            "content_type": file.get("content_type"),
            "size_bytes": file.get("size_bytes"),
            "storage_key": file.get("storage_key")
            or f"students/{self._course_id}/{file['student_id']}/{file_id}/{filename}",
            "extracted_text": extracted_text,
            "char_count": int(file.get("char_count", len(extracted_text))),
            "uploaded_at": now(),
        }
        self._db["student_files"].insert_one(record)
        return self._row(record, "uploaded_at")

    def list_student_files(
        self, student_id: str, *, lab_id: Optional[str] = None
    ) -> list[dict]:
        query = self._scope(student_id=student_id)
        if lab_id is not None:
            query["lab_id"] = lab_id
        cursor = self._db["student_files"].find(query).sort("uploaded_at", -1)
        return [self._row(d, "uploaded_at") for d in cursor]

    # ------------------------------------------------------------- queries
    def log_query(
        self,
        *,
        student_id: str,
        question: str,
        retrieved: Sequence[dict] = (),
        session_id: Optional[str] = None,
        lab_id: Optional[str] = None,
        top_k: Optional[int] = None,
        latency_ms: Optional[int] = None,
        embedding_model: Optional[str] = None,
    ) -> dict:
        """Append one retrieval to the query log.

        `retrieved` takes `search()`'s output directly; only the three fields
        the log keeps are stored, not the chunk text again.

        OPEN QUESTION: this is the third place a student question is written,
        alongside `interactions` and `guardian_questions`. The team has not
        decided which is the record; until it does, a caller that logs here
        should keep logging where it already did.
        """
        document = {
            "query_id": new_id(),
            "course_id": self._course_id,
            "student_id": student_id,
            "session_id": session_id,
            "lab_id": lab_id,
            "question": question,
            "retrieved": [
                {
                    "vector_id": hit["uid"] if "uid" in hit else hit["vector_id"],
                    "document_id": hit["document_id"],
                    "source": hit.get("source", ""),
                    "score": float(hit.get("score") or 0.0),
                }
                for hit in retrieved
            ],
            "top_k": top_k,
            "latency_ms": latency_ms,
            "embedding_model": embedding_model,
            "asked_at": now(),
        }
        self._db["rag_queries"].insert_one(document)
        return self._row(document, "asked_at")

    def list_queries(
        self, *, student_id: Optional[str] = None, session_id: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        query = self._scope()
        if student_id:
            query["student_id"] = student_id
        if session_id:
            query["session_id"] = session_id
        cursor = self._db["rag_queries"].find(query).sort("asked_at", -1).limit(limit)
        return [self._row(d, "asked_at") for d in cursor]
