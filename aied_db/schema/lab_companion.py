"""Lab Companion collections: rag_documents, rag_vectors, student_files, rag_queries.

These replace ui-student's Azure AI Search knowledge base
(`agentic_system/kb/azure_search.py` @6f71458) with Atlas Vector Search.

The retrieval contract is `agentic_system/kb/base.py`:

    RetrievedDoc(content, source, uid)
    KnowledgeBase.search(query, top) -> list[RetrievedDoc]

Azure returned `parent_id`, `chunk_id`, `chunk` and `title` per hit, and
`AzureSearchKB` built `uid = f"{parent_id}_{chunk_id}"`. The split here keeps
that two-level shape: `rag_documents` is the parent file an instructor
uploaded, `rag_vectors` is one embedded chunk of it. So `content` comes from
`rag_vectors.content`, `source` from `rag_documents.title`, and `uid` from
`rag_vectors.vector_id` — a Mongo-backed KB can satisfy the protocol without
ui-student's agents changing at all.

Ownership, per the Oct 26 rules: `rag_documents` and `student_files` are
human uploads and are READ-ONLY to agents. `rag_vectors` is derived output
the indexer appends. `rag_queries` is an append-only log.
"""

from __future__ import annotations

from typing import Any

from ._common import IndexSpec, collection, nullable

# text-embedding-3-small / ada-002 are both 1536-dimensional.
EMBEDDING_DIMS = 1536
VECTOR_SIMILARITY = "cosine"
VECTOR_INDEX_NAME = "rag_vectors_embedding_idx"

RAG_DOCUMENT_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "document_id",
        "course_id",
        "title",
        "storage_key",
        "extracted_text",
        "char_count",
        "chunk_count",
        "uploaded_at",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "document_id": {
            "bsonType": "string",
            "description": "UUID4; Azure's parent_id. Human upload — read-only to agents",
        },
        "course_id": {"bsonType": "string", "description": "courses.id"},
        "lab_id": {
            "bsonType": nullable("string"),
            "description": "Set when the document belongs to one lab rather than the course",
        },
        "title": {"bsonType": "string", "description": "Azure's title; RetrievedDoc.source"},
        "filename": {"bsonType": nullable("string")},
        "content_type": {"bsonType": nullable("string")},
        "size_bytes": {"bsonType": ["int", "null"], "minimum": 0},
        "storage_key": {
            "bsonType": "string",
            "description": "rag/{course_id}/{document_id}/{filename} in shared file storage",
        },
        "extracted_text": {"bsonType": "string"},
        "char_count": {"bsonType": "int", "minimum": 0},
        "chunk_count": {
            "bsonType": "int",
            "minimum": 0,
            "description": "Derived: how many rag_vectors rows this document produced",
        },
        "uploaded_by": {"bsonType": nullable("string"), "description": "users.id"},
        "uploaded_at": {"bsonType": "date"},
        "indexed_at": {
            "bsonType": nullable("date"),
            "description": "Null until the embedder has written its chunks",
        },
    },
}

RAG_VECTOR_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "vector_id",
        "document_id",
        "course_id",
        "chunk_index",
        "content",
        "embedding",
        "embedding_model",
        "created_at",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "vector_id": {
            "bsonType": "string",
            "description": "Azure's uid: f'{document_id}_{chunk_index}'. RetrievedDoc.uid",
        },
        "document_id": {"bsonType": "string", "description": "rag_documents.document_id"},
        "course_id": {
            "bsonType": "string",
            "description": "Denormalised so vector search can pre-filter by course",
        },
        "lab_id": {"bsonType": nullable("string")},
        "chunk_index": {"bsonType": "int", "minimum": 0, "description": "0-based, within the document"},
        "content": {"bsonType": "string", "description": "Azure's chunk; RetrievedDoc.content"},
        "embedding": {
            "bsonType": "array",
            "minItems": EMBEDDING_DIMS,
            "maxItems": EMBEDDING_DIMS,
            "items": {"bsonType": "double"},
            "description": f"{EMBEDDING_DIMS} doubles; indexed by {VECTOR_INDEX_NAME}",
        },
        "embedding_model": {"bsonType": "string"},
        "char_count": {"bsonType": ["int", "null"], "minimum": 0},
        "created_at": {"bsonType": "date"},
    },
}

STUDENT_FILE_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "file_id",
        "course_id",
        "student_id",
        "filename",
        "storage_key",
        "extracted_text",
        "char_count",
        "uploaded_at",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "file_id": {
            "bsonType": "string",
            "description": "UUID4. The student's own work — read-only to agents",
        },
        "course_id": {"bsonType": "string"},
        "student_id": {"bsonType": "string", "description": "users.id"},
        "lab_id": {"bsonType": nullable("string")},
        "session_id": {"bsonType": nullable("string")},
        "filename": {"bsonType": "string"},
        "content_type": {"bsonType": nullable("string")},
        "size_bytes": {"bsonType": ["int", "null"], "minimum": 0},
        "storage_key": {
            "bsonType": "string",
            "description": "students/{course_id}/{student_id}/{file_id}/{filename}",
        },
        "extracted_text": {"bsonType": "string"},
        "char_count": {"bsonType": "int", "minimum": 0},
        "uploaded_at": {"bsonType": "date"},
    },
}

RETRIEVED_CHUNK_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["vector_id", "document_id", "score"],
    "additionalProperties": False,
    "properties": {
        "vector_id": {"bsonType": "string"},
        "document_id": {"bsonType": "string"},
        "source": {"bsonType": "string", "description": "rag_documents.title at query time"},
        "score": {"bsonType": ["double", "int"], "description": "Vector-search similarity"},
    },
}

RAG_QUERY_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["query_id", "course_id", "student_id", "question", "retrieved", "asked_at"],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "query_id": {"bsonType": "string", "description": "UUID4"},
        "course_id": {"bsonType": "string"},
        "student_id": {"bsonType": "string"},
        "session_id": {"bsonType": nullable("string"), "description": "guardian_sessions.session_id"},
        "lab_id": {"bsonType": nullable("string")},
        "question": {"bsonType": "string", "description": "The query text sent to the embedder"},
        "retrieved": {
            "bsonType": "array",
            "items": RETRIEVED_CHUNK_SCHEMA,
            "description": "What search() returned, in rank order; may be empty",
        },
        "top_k": {"bsonType": ["int", "null"], "minimum": 1},
        "latency_ms": {"bsonType": ["int", "null"], "minimum": 0},
        "embedding_model": {"bsonType": nullable("string")},
        "asked_at": {"bsonType": "date"},
    },
}

RAG_DOCUMENT_INDEXES: IndexSpec = [
    ([("document_id", 1)], True, "Primary key"),
    ([("course_id", 1), ("uploaded_at", -1)], False, "A course's documents, newest first"),
    ([("course_id", 1), ("lab_id", 1)], False, "One lab's documents"),
    ([("course_id", 1), ("indexed_at", 1)], False, "Documents still awaiting embedding"),
]

RAG_VECTOR_INDEXES: IndexSpec = [
    ([("vector_id", 1)], True, "Primary key; Azure's uid"),
    ([("document_id", 1), ("chunk_index", 1)], True, "Re-index a document idempotently"),
    ([("course_id", 1)], False, "Pre-filter vector search to one course"),
]

STUDENT_FILE_INDEXES: IndexSpec = [
    ([("file_id", 1)], True, "Primary key"),
    ([("course_id", 1), ("student_id", 1), ("uploaded_at", -1)], False, "A student's files"),
    ([("course_id", 1), ("lab_id", 1), ("student_id", 1)], False, "One lab's submitted files"),
]

RAG_QUERY_INDEXES: IndexSpec = [
    ([("query_id", 1)], True, "Primary key"),
    ([("course_id", 1), ("student_id", 1), ("asked_at", -1)], False, "A student's query history"),
    ([("session_id", 1), ("asked_at", 1)], False, "One session's queries, in order"),
]

# ── Atlas Vector Search ──────────────────────────────────────────────────────
# Not an ordinary index: created via `createSearchIndexes`, which Atlas
# supports but a local mongod and mongomock do not. `scripts/init_db.py`
# skips it with a warning when the command is unavailable.

VECTOR_SEARCH_INDEX: dict[str, Any] = {
    "name": VECTOR_INDEX_NAME,
    "type": "vectorSearch",
    "definition": {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": EMBEDDING_DIMS,
                "similarity": VECTOR_SIMILARITY,
            },
            # Pre-filter fields, so one index serves every course.
            {"type": "filter", "path": "course_id"},
            {"type": "filter", "path": "lab_id"},
        ]
    },
}

SEARCH_INDEXES: dict[str, list[dict[str, Any]]] = {
    "rag_vectors": [VECTOR_SEARCH_INDEX],
}

COLLECTIONS: dict[str, dict[str, Any]] = {
    "rag_documents": collection(RAG_DOCUMENT_SCHEMA, RAG_DOCUMENT_INDEXES),
    "rag_vectors": collection(RAG_VECTOR_SCHEMA, RAG_VECTOR_INDEXES),
    "student_files": collection(STUDENT_FILE_SCHEMA, STUDENT_FILE_INDEXES),
    "rag_queries": collection(RAG_QUERY_SCHEMA, RAG_QUERY_INDEXES),
}
