# Cloud Data Sync API — Integration Specification

**Audience:** Cloud server implementers (Django + PostgreSQL).  
**Purpose:** Define the HTTP API and database schema so the analytics client can push errors, learning data, menu bootstrap, and conversations to your server. Use this document to implement the ingest endpoints and Postgres models.

**Client behaviour:** The analytics app sends data to the URLs you provide. It uses **Bearer token** auth (optional), **JSON** bodies, and stable IDs for idempotency. All payloads may include **`uploaded_by`** (employee_id, name) for attribution.

---

## 1. Overview

| Endpoint (client → cloud) | Method | Purpose |
|----------------------------|--------|---------|
| `/desktop-analytics-sync/errors/ingest` | POST | Batch of error/crash log records |
| `/desktop-analytics-sync/learning/ingest` | POST | AI pipeline metadata (ai_logs, ai_feedback) + Tier 3 (cache_stats, aggregated_counters, schema_hash) |
| `/desktop-analytics-sync/menu-bootstrap/ingest` | POST | Menu knowledge: id_maps + cluster_state (for new-user bootstrap) |
| `/desktop-analytics-sync/conversations/sync` | POST | AI conversations + messages (existing sync) |
| `/desktop-analytics-sync/menu-bootstrap/latest` | GET | *(Optional)* Return latest merged menu bootstrap for new clients to pull |

Base URL and optional API key are configured on the client via environment variables.

**LLM cache DB:** The analytics app stores LLM response cache in a separate SQLite DB (`llm_cache.db`; schema in `database/schema_llm_cache.sql`). Cloud sync should include this DB (or export entries where `is_incorrect = 1`) so the cloud can use human feedback: users mark incorrect cache entries in AI Mode Telemetry, and the cloud can learn from them. See **§ LLM cache and human feedback** below.

---

## 2. Authentication

- **Header:** `Authorization: Bearer <API_KEY>` (client sends when `CLIENT_LEARNING_API_KEY` or `MASTER_SERVER_API_KEY` is set).
- **Your responsibility:** Validate the token (e.g. API key or JWT), return `401 Unauthorized` if invalid. If you do not require auth, you may ignore the header.

---

## 3. Endpoints and Request/Response

### 3.1 Errors Ingest

**POST** `/desktop-analytics-sync/errors/ingest`

**Request body:**

```json
{
  "records": [
    {
      "record_id": "string (32-char hex, client-generated, stable for idempotency)",
      "payload": {
        "ts": "ISO8601 datetime (UTC)",
        "level": "ERROR",
        "message": "string",
        "exception": "string or null",
        "traceback": "string or null",
        "context": { "action": "...", "user_query": "...", "generated_sql": "...", "error_kind": "..." }
      }
    }
  ],
  "uploaded_by": {
    "employee_id": "string",
    "name": "string"
  }
}
```

- `uploaded_by` is optional (omitted if client has no app user).
- `records` may be empty; you may return 200 and do nothing.

**Response:** `200 OK` with any JSON body (e.g. `{"received": 5}`). Client treats 2xx as success and will truncate/remove sent log lines.

**Idempotency:** Client sends a stable `record_id` per record. Store `record_id` and deduplicate (e.g. ignore or upsert by `record_id`) so the same batch re-sent does not create duplicates.

---

### 3.2 Learning Ingest

**POST** `/desktop-analytics-sync/learning/ingest`

**Request body:**

```json
{
  "ai_logs": [
    {
      "query_id": "UUID string",
      "user_query": "string",
      "intent": "string or null",
      "sql_generated": "string or null",
      "response_type": "text|table|chart|multi or null",
      "response_payload": "string (JSON summary, not full result data)",
      "error_message": "string or null",
      "execution_time_ms": "integer or null",
      "created_at": "datetime string",
      "raw_user_query": "string or null",
      "corrected_query": "string or null",
      "action_sequence": ["RUN_SQL", "GENERATE_CHART"] or null,
      "explanation": "string or null"
    }
  ],
  "ai_feedback": [
    {
      "feedback_id": "integer",
      "query_id": "UUID string",
      "is_positive": "boolean",
      "comment": "string or null",
      "created_at": "datetime string"
    }
  ],
  "cache_stats": {
    "total_entries": "integer",
    "by_call_id": { "correct_query": 10, "classify_intent": 20, ... }
  },
  "aggregated_counters": {
    "intents_per_day": [
      { "intent": "SQL_QUERY", "date": "YYYY-MM-DD", "count": 5 }
    ],
    "response_type_counts": { "table": 10, "chart": 3, "text": 2 },
    "total_ai_logs_7d": 15
  },
  "schema_hash": "SHA256 hex string or null",
  "uploaded_by": {
    "employee_id": "string",
    "name": "string"
  }
}
```

- Any of `ai_logs`, `ai_feedback`, `cache_stats`, `aggregated_counters`, `schema_hash`, `uploaded_by` may be present. Client always sends at least Tier 3 (cache_stats, aggregated_counters, schema_hash) and may send empty arrays.
- `action_sequence` is a JSON array of action identifiers.

**Response:** `200 OK`. Client marks sent `query_id`/`feedback_id` as uploaded locally.

**Idempotency:** Use `query_id` (and optionally `feedback_id`) as unique keys; upsert or ignore duplicates.

#### LLM cache and human feedback

The client stores LLM response cache in a separate SQLite DB (`llm_cache.db`; schema in `database/schema_llm_cache.sql`). Each row has `key_hash`, `call_id`, `value`, `created_at`, `last_used_at`, and **`is_incorrect`**.

**`is_incorrect` flag**

| Value | Meaning |
|-------|--------|
| `0` (default) | Entry not marked as incorrect; cached response is treated as valid. |
| `1` | User marked this cache entry as incorrect in AI Mode Telemetry (human feedback that the cached LLM response was wrong). |

- In AI Mode Telemetry, users can tick an "Incorrect" checkbox per row; the client updates `is_incorrect` in the DB immediately (and may sync to cloud).
- Users can unmark an entry (toggle back to 0); the cloud may receive both `is_incorrect = 1` and later `is_incorrect = 0` for the same `key_hash`.

**Expected behavior (cloud)**

- Use entries with **`is_incorrect = 1`** as negative feedback: e.g. avoid reusing or reinforcing those responses, improve prompts/models, or flag patterns that led to wrong answers.
- Entries with `is_incorrect = 0` are normal cache; only `is_incorrect = 1` carries explicit "this was wrong" signal. You may ignore or store `is_incorrect = 0` for analytics.
- Idempotency: if the client sends `llm_cache_feedback`, use `key_hash` (and optionally `call_id`) to upsert so repeated or updated feedback does not create duplicates.

**Cloud sync:** The client should sync `llm_cache.db` (or export rows where `is_incorrect = 1`) so the cloud can use this human feedback. You may extend `/desktop-analytics-sync/learning/ingest` to accept an optional payload such as `llm_cache_feedback: [{ "key_hash", "call_id", "is_incorrect", "created_at", "last_used_at" }]` or sync the whole cache DB file according to your pipeline.

---

### 3.3 Menu Bootstrap Ingest

**POST** `/desktop-analytics-sync/menu-bootstrap/ingest`

**Request body:**

```json
{
  "id_maps": {
    "menu_id_to_str": { "uuid": "Menu Item Name", ... },
    "variant_id_to_str": { "uuid": "Variant Name", ... },
    "type_id_to_str": { "type_id": "Dessert", ... }
  },
  "cluster_state": {
    "menu_item_id:type_id": {
      "order_item_id": [ ["order_item_id", "variant_id"], ... ],
      ...
    },
    ...
  },
  "uploaded_by": {
    "employee_id": "string",
    "name": "string"
  }
}
```

- `cluster_state` keys are strings of the form `"<menu_item_id>:<type_id>"`. Values are objects mapping `order_item_id` (string) to a list of `[order_item_id, variant_id]` pairs.
- You may merge multiple clients’ submissions (e.g. union or consensus) and expose a single “latest” bootstrap via GET (see 3.5).

**Response:** `200 OK`.

---

### 3.4 Conversations Sync (existing)

**POST** `/desktop-analytics-sync/conversations/sync`

**Request body:**

```json
{
  "conversations": [
    {
      "conversation_id": "UUID string",
      "title": "string or null",
      "started_at": "datetime",
      "updated_at": "datetime",
      "synced_at": "datetime or null",
      "messages": [
        {
          "message_id": "UUID string",
          "role": "user|ai",
          "content": "string or JSON (text, table, chart config, etc.)",
          "type": "text|table|chart|multi or null",
          "sql_query": "string or null",
          "explanation": "string or null",
          "query_id": "UUID or null",
          "query_status": "complete|incomplete|ignored or null",
          "created_at": "datetime"
        }
      ]
    }
  ]
}
```

**Response:** `200 OK`. Client then sets `synced_at` locally for the sent conversations.

**Idempotency:** Use `conversation_id` and `message_id`; upsert by these IDs.

---

### 3.5 Menu Bootstrap — Get Latest (optional)

**GET** `/desktop-analytics-sync/menu-bootstrap/latest`

**Purpose:** New clients can call this to download the merged menu bootstrap (id_maps + cluster_state) and run their local `perform_seeding()`.

**Response:** `200 OK` with JSON:

```json
{
  "id_maps": { "menu_id_to_str": {...}, "variant_id_to_str": {...}, "type_id_to_str": {...} },
  "cluster_state": { "menu_item_id:type_id": { "order_item_id": [...], ... }, ... }
}
```

- If no bootstrap exists yet, return `404` or `200` with empty objects.

---

## 4. PostgreSQL Schema (Django-Oriented)

Below is a schema you can implement in Postgres (via Django models or raw SQL). All tables use your own `id` (e.g. BigAuto) for internal use; client IDs are stored for deduplication and reference.

### 4.1 Upload Attribution (shared)

Store who uploaded each batch; useful for filtering and support.

```sql
-- Optional: single row per client/device if you want to normalize uploaded_by
CREATE TABLE sync_uploaders (
    id BIGSERIAL PRIMARY KEY,
    employee_id VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 4.2 Error Records

```sql
CREATE TABLE ingest_error_records (
    id BIGSERIAL PRIMARY KEY,
    record_id VARCHAR(64) NOT NULL UNIQUE,  -- client-generated, for idempotency
    employee_id VARCHAR(64),
    name VARCHAR(255),
    ts TIMESTAMPTZ,
    level VARCHAR(32),
    message TEXT,
    exception TEXT,
    traceback TEXT,
    context JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_ingest_error_records_record_id ON ingest_error_records (record_id);
CREATE INDEX idx_ingest_error_records_ts ON ingest_error_records (ts);
CREATE INDEX idx_ingest_error_records_employee ON ingest_error_records (employee_id);
```

### 4.3 Learning — AI Logs

```sql
CREATE TABLE ingest_ai_logs (
    id BIGSERIAL PRIMARY KEY,
    query_id UUID NOT NULL UNIQUE,
    employee_id VARCHAR(64),
    name VARCHAR(255),
    user_query TEXT,
    intent VARCHAR(128),
    sql_generated TEXT,
    response_type VARCHAR(32),
    response_payload TEXT,
    error_message TEXT,
    execution_time_ms INTEGER,
    created_at TIMESTAMPTZ,
    raw_user_query TEXT,
    corrected_query TEXT,
    action_sequence JSONB,
    explanation TEXT,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_ingest_ai_logs_query_id ON ingest_ai_logs (query_id);
CREATE INDEX idx_ingest_ai_logs_created ON ingest_ai_logs (created_at);
CREATE INDEX idx_ingest_ai_logs_employee ON ingest_ai_logs (employee_id);
CREATE INDEX idx_ingest_ai_logs_intent ON ingest_ai_logs (intent);
```

### 4.4 Learning — AI Feedback

```sql
CREATE TABLE ingest_ai_feedback (
    id BIGSERIAL PRIMARY KEY,
    feedback_id BIGINT NOT NULL,
    query_id UUID NOT NULL,
    employee_id VARCHAR(64),
    name VARCHAR(255),
    is_positive BOOLEAN NOT NULL,
    comment TEXT,
    created_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (feedback_id, query_id)
);

CREATE INDEX idx_ingest_ai_feedback_query_id ON ingest_ai_feedback (query_id);
CREATE INDEX idx_ingest_ai_feedback_employee ON ingest_ai_feedback (employee_id);
```

### 4.5 Learning — Tier 3 (aggregates / cache / schema)

You can store Tier 3 in one table per batch or in separate tables. Example: one row per ingest batch.

```sql
CREATE TABLE ingest_learning_tier3 (
    id BIGSERIAL PRIMARY KEY,
    employee_id VARCHAR(64),
    name VARCHAR(255),
    cache_stats JSONB NOT NULL DEFAULT '{}',
    aggregated_counters JSONB NOT NULL DEFAULT '{}',
    schema_hash VARCHAR(64),
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ingest_learning_tier3_ingested ON ingest_learning_tier3 (ingested_at);
CREATE INDEX idx_ingest_learning_tier3_employee ON ingest_learning_tier3 (employee_id);
```

### 4.6 Menu Bootstrap

Store the latest merged bootstrap (or one row per submission and merge in application logic).

```sql
CREATE TABLE ingest_menu_bootstrap (
    id BIGSERIAL PRIMARY KEY,
    employee_id VARCHAR(64),
    name VARCHAR(255),
    id_maps JSONB NOT NULL,
    cluster_state JSONB NOT NULL,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

-- Optional: table to hold the single “latest” merged bootstrap for GET /desktop-analytics-sync/menu-bootstrap/latest
CREATE TABLE menu_bootstrap_latest (
    id SERIAL PRIMARY KEY,
    id_maps JSONB NOT NULL,
    cluster_state JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- Keep one row; update in place when you merge new ingest_menu_bootstrap data.
```

### 4.7 Conversations Sync

```sql
CREATE TABLE ingest_conversations (
    id BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL UNIQUE,
    title VARCHAR(512),
    started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE ingest_conversation_messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES ingest_conversations(conversation_id) ON DELETE CASCADE,
    message_id UUID NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    type VARCHAR(32),
    sql_query TEXT,
    explanation TEXT,
    query_id UUID,
    query_status VARCHAR(32),
    created_at TIMESTAMPTZ,
    UNIQUE (conversation_id, message_id)
);

CREATE INDEX idx_ingest_messages_conversation ON ingest_conversation_messages (conversation_id);
```

---

## 5. Django Model Examples (Sketch)

You can map the above to Django models and use a single `uploaded_by` FK if you normalize uploaders.

```python
# models.py (sketch)

from django.db import models
import uuid

class SyncUploader(models.Model):
    employee_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

class IngestErrorRecord(models.Model):
    record_id = models.CharField(max_length=64, unique=True)
    uploader = models.ForeignKey(SyncUploader, null=True, on_delete=models.SET_NULL)
    ts = models.DateTimeField(null=True)
    level = models.CharField(max_length=32, null=True)
    message = models.TextField(blank=True)
    exception = models.TextField(null=True)
    traceback = models.TextField(null=True)
    context = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

class IngestAILog(models.Model):
    query_id = models.UUIDField(unique=True, db_index=True)
    uploader = models.ForeignKey(SyncUploader, null=True, on_delete=models.SET_NULL)
    user_query = models.TextField(blank=True)
    intent = models.CharField(max_length=128, null=True)
    sql_generated = models.TextField(null=True)
    response_type = models.CharField(max_length=32, null=True)
    response_payload = models.TextField(null=True)
    error_message = models.TextField(null=True)
    execution_time_ms = models.IntegerField(null=True)
    created_at = models.DateTimeField(null=True)
    raw_user_query = models.TextField(null=True)
    corrected_query = models.TextField(null=True)
    action_sequence = models.JSONField(null=True)
    explanation = models.TextField(null=True)
    ingested_at = models.DateTimeField(auto_now_add=True)

class IngestAIFeedback(models.Model):
    feedback_id = models.BigIntegerField()
    query_id = models.UUIDField(db_index=True)
    uploader = models.ForeignKey(SyncUploader, null=True, on_delete=models.SET_NULL)
    is_positive = models.BooleanField()
    comment = models.TextField(null=True)
    created_at = models.DateTimeField(null=True)
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("feedback_id", "query_id")]

class IngestLearningTier3(models.Model):
    uploader = models.ForeignKey(SyncUploader, null=True, on_delete=models.SET_NULL)
    cache_stats = models.JSONField(default=dict)
    aggregated_counters = models.JSONField(default=dict)
    schema_hash = models.CharField(max_length=64, null=True)
    ingested_at = models.DateTimeField(auto_now_add=True)

class IngestMenuBootstrap(models.Model):
    uploader = models.ForeignKey(SyncUploader, null=True, on_delete=models.SET_NULL)
    id_maps = models.JSONField()
    cluster_state = models.JSONField()
    ingested_at = models.DateTimeField(auto_now_add=True)

class MenuBootstrapLatest(models.Model):
    """Single row: current merged bootstrap for GET /desktop-analytics-sync/menu-bootstrap/latest"""
    id_maps = models.JSONField()
    cluster_state = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

class IngestConversation(models.Model):
    conversation_id = models.UUIDField(unique=True)
    title = models.CharField(max_length=512, null=True)
    started_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(null=True)
    synced_at = models.DateTimeField(null=True)
    ingested_at = models.DateTimeField(auto_now_add=True)

class IngestConversationMessage(models.Model):
    conversation = models.ForeignKey(IngestConversation, on_delete=models.CASCADE)
    message_id = models.UUIDField()
    role = models.CharField(max_length=16)
    content = models.TextField()
    type = models.CharField(max_length=32, null=True)
    sql_query = models.TextField(null=True)
    explanation = models.TextField(null=True)
    query_id = models.UUIDField(null=True)
    query_status = models.CharField(max_length=32, null=True)
    created_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = [("conversation", "message_id")]
```

---

## 6. Idempotency and Deduplication

| Source | Client ID field | Recommendation |
|--------|------------------|----------------|
| Errors | `record_id` (per record) | Store `record_id` UNIQUE; ON CONFLICT DO NOTHING or skip insert if exists. |
| AI logs | `query_id` | UPSERT by `query_id`. |
| AI feedback | `feedback_id` + `query_id` | UPSERT by (feedback_id, query_id). |
| Conversations | `conversation_id`, `message_id` | UPSERT conversation by conversation_id; UPSERT messages by (conversation_id, message_id). |
| Menu bootstrap | No client-side id | Append or merge; update `menu_bootstrap_latest` when you want to expose latest. |

Use PostgreSQL `ON CONFLICT (unique_column) DO UPDATE SET ...` or Django `update_or_create` to avoid duplicates when the client retries.

---

## 7. PII and Retention

- **PII:** Request bodies may contain `raw_user_query`, `corrected_query`, `user_query`, `message`, and `context` (e.g. user_query, generated_sql). Treat as sensitive; apply access control and retention.
- **Retention:** Define retention (e.g. 90 days for error records, 1 year for ai_logs) and document in your privacy policy. Client does not delete data after upload; you own retention and purge.

---

## 8. Client Configuration (Reference)

The analytics client uses these environment variables. You provide the base URL and optional key.

| Variable | Purpose |
|----------|---------|
| `CLIENT_LEARNING_ERROR_INGEST_URL` | Full URL for POST errors (e.g. `https://your-server.com/desktop-analytics-sync/errors/ingest`) |
| `CLIENT_LEARNING_INGEST_URL` | Full URL for POST learning |
| `CLIENT_LEARNING_MENU_BOOTSTRAP_INGEST_URL` | Full URL for POST menu bootstrap |
| `CLIENT_LEARNING_API_KEY` | Bearer token for the three above |
| `MASTER_SERVER_URL` | Base URL for conversation sync (e.g. `https://your-server.com`) |
| `MASTER_SERVER_API_KEY` | Bearer token for conversation sync |

Conversation sync uses `MASTER_SERVER_URL` + `/desktop-analytics-sync/conversations/sync`. The other three use full URLs. All use JSON and `Content-Type: application/json`.

---

## 9. Summary Checklist

- [ ] Implement POST `/desktop-analytics-sync/errors/ingest` (body: records, optional uploaded_by); store by record_id.
- [ ] Implement POST `/desktop-analytics-sync/learning/ingest` (body: ai_logs, ai_feedback, cache_stats, aggregated_counters, schema_hash, optional uploaded_by); store with query_id / feedback_id deduplication.
- [ ] Implement POST `/desktop-analytics-sync/menu-bootstrap/ingest` (body: id_maps, cluster_state, optional uploaded_by); merge into menu_bootstrap_latest if desired.
- [ ] Implement POST `/desktop-analytics-sync/conversations/sync` (body: conversations with messages); upsert by conversation_id and message_id.
- [ ] Optional: GET `/desktop-analytics-sync/menu-bootstrap/latest` returning id_maps + cluster_state.
- [ ] Auth: validate Bearer token, return 401 if required and missing/invalid.
- [ ] Postgres: create tables (or Django models) per §4–5; add indexes for query_id, record_id, employee_id, and time ranges.
- [ ] Idempotency: use client IDs to deduplicate; document retention and PII handling.

This specification is the single reference for implementing the cloud side of the analytics client data sync.
