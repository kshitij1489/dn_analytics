# Mutation and Revision Sync Protocol

This document outlines the optimistic concurrency control protocol used between the Desktop App (client) and the Central Server for synchronizing changes to the Menu/Catalog and Customer data.

## 1. Overview

The Central Server acts as the absolute ground truth. Since multiple desktop applications might attempt to update the catalog or customer database simultaneously, the server uses a strict **Revision-Based Optimistic Concurrency** model to prevent race conditions and prevent data loss.

Every scope has a monotonically increasing revision number:
- Menu mutations use `menu_revision` (tracked in `MenuScopeState`)
- Customer mutations use `customer_revision` (tracked in `CustomerScopeState`)

## 2. The Client-Server Workflow

### Step 1: Initial State / Bootstrap
When the desktop app first synchronizes or pulls data from the server, it receives the latest state along with the current revision number (e.g., `menu_revision: 42`). The desktop app stores this revision locally as its baseline.

### Step 2: Sending an Update (Mutation)
When a user makes a change in the desktop app, the app prepares a "mutation commit" request. This request **must** include the `expected_menu_revision` (the baseline revision the client currently holds).

**Example Payload:**
```json
POST /desktop-analytics-sync/menu-mutations/commit/
{
  "schema_version": 1,
  "mutation_id": "123e4567-e89b-12d3-a456-426614174000",
  "mutation_type": "menu_merge.applied",
  "expected_menu_revision": 42,
  "event": {
    "remote_event_id": "event-123",
    "event_type": "menu_merge.applied",
    "schema_version": 2,
    "occurred_at": "2026-07-09T12:00:00Z",
    "merge_payload": {
      "assignments": [...]
    }
  },
  "catalog_delta": {
    "items": [...],
    "variants": [...]
  },
  "uploaded_by": { "employee_id": "...", "name": "..." },
  "uploaded_from": { "device_id": "...", "install_id": "...", "device_label": "..." }
}
```

**Required Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | Must be `1` |
| `mutation_id` | string (UUID v4) | Client-generated unique ID for idempotency |
| `mutation_type` | string | One of: `menu_merge.applied`, `menu_merge.undone`, `resolution_variant`, `order_item_remap`, `verify`, `catalog_update`, `derived_assignment.sync` |
| `expected_menu_revision` | int | The revision the client currently holds. For `derived_assignment.sync`, servers accept the field but do not use it for OCC. |
| `event` | object | The merge event (required for assignment-bearing mutations and `derived_assignment.sync`; omitted or null for `verify` and `catalog_update`) |
| `verification_events` | list | List of verification event objects (required for `verify` mutations with mapping rows; empty for pure `catalog_update`) |
| `catalog_delta` | object | Contains `items` and `variants` lists for catalog updates; required and non-empty for `catalog_update` |
| `uploaded_by` | object | Optional. Attribution: who made the change |
| `uploaded_from` | object | Optional. Attribution: which device |

### Step 3: Server Processing

The server receives the mutation and compares the client's `expected_menu_revision` against its own master database revision. The comparison happens inside a database transaction with `SELECT ... FOR UPDATE` to prevent concurrent mutations from racing.

`derived_assignment.sync` is the exception: it is a machine-derived, create-only flush for POS-backed local assignments. The server inserts each assignment only if no `OrderItemAssignment` already exists for that `order_item_id`; existing rows are reported in `skipped_existing`, never overwritten, and the mutation does not bump `menu_revision`.

#### Scenario A: Success (200 OK)
- **Condition:** The client's `expected_menu_revision` matches the server's master revision (e.g., both are 42).
- **Action:** The server accepts the mutation, commits it to the database, and increments its revision to 43.
- **Response:** Returns `200 OK` along with the **new** revision number and additional state.
- **Client Action:** The desktop app updates its local `menu_revision` to 43 and applies the returned state.

**Full 200 OK Response Shape:**
```json
{
  "status": "accepted",
  "mutation_id": "123e4567-e89b-12d3-a456-426614174000",
  "menu_revision": 43,
  "accepted_events": [
    {
      "remote_event_id": "event-123",
      "server_seq": 501,
      "server_ingested_at": "2026-07-09T12:00:01Z"
    }
  ],
  "assignment_rows": [...],
  "catalog_delta": { "items": [...], "variants": [...] },
  "merge_cursor": "...",
  "verification_cursor": "..."
}
```

| Response Field | Description |
|----------------|-------------|
| `status` | Always `"accepted"` on success |
| `mutation_id` | Echo of the client's mutation ID |
| `menu_revision` | The **new** server revision — client must store this for next request |
| ~~`strict_mode_enabled`~~ | **Removed in contract revision 1.2.** It was a hardcoded `true` kept only so already-deployed clients kept committing; every install now runs the always-strict release. |
| `accepted_events` | List of persisted events with their `server_seq` and `server_ingested_at` |
| `assignment_rows` | Updated assignment locator rows for the order items touched by this mutation |
| `catalog_delta` | Echo of the catalog changes applied |
| `merge_cursor` | Opaque cursor for the merge event stream (for delta polling) |
| `verification_cursor` | Opaque cursor for the verification event stream (for delta polling) |

#### Scenario B: Conflict (409 Conflict)
- **Condition:** The client's `expected_menu_revision` does not match the server's master revision (e.g., client sends 42, but the server is already at 45 because another app made changes).
- **Action:** The server rejects the mutation to prevent overwriting the other app's data. It queries all events that were committed between revision 42 and 45.
- **Response:** Returns `409 Conflict` containing the `conflicting_events` that the client missed.
- **Catalog-only rule:** `catalog_update` mutations have no `order_item_ids`, so the desktop fails closed on any 409 and surfaces a conflict instead of silently retrying. The user should refresh and retry the catalog edit.

**Full 409 Conflict Response Shape:**
```json
{
  "status": "conflict",
  "error": "Menu state changed on the server. Pull latest menu state and retry.",
  "expected_menu_revision": 42,
  "current_menu_revision": 45,
  "recommended_action": "pull_latest_menu_state",
  "conflicting_events": [
    {
      "remote_event_id": "event-from-other-app",
      "server_seq": 105,
      "server_ingested_at": "2026-07-09T12:05:00Z",
      "attribution": {
        "employee": { "employee_id": "emp-456", "name": "Other User" },
        "device": { "device_id": "dev-789", "install_id": "inst-012", "device_label": "POS-2" }
      },
      "order_item_ids": ["oi-1", "oi-2"]
    }
  ]
}
```

| Response Field | Description |
|----------------|-------------|
| `status` | Always `"conflict"` |
| `error` | Human-readable error message |
| `expected_menu_revision` | What the client sent |
| `current_menu_revision` | The server's actual current revision |
| `recommended_action` | Always `"pull_latest_menu_state"` — tells the client what to do |
| `conflicting_events` | List of events committed by other apps during the revision gap |
| `conflicting_events[].attribution` | Who and which device made the conflicting change |
| `conflicting_events[].order_item_ids` | Which order items were affected by the conflicting change |

### Step 4: Client Reconciliation (Handling a 409)
When the desktop app receives a `409 Conflict`, it must:
1. **Ingest Conflicting Events:** Parse the `conflicting_events` array from the 409 response and apply those changes to its local database. This step brings the client's local database up to the server's current state (revision 45).
2. **Update Revision:** Update its local `expected_menu_revision` to the `current_menu_revision` from the 409 response (45).
3. **Retry:** Re-evaluate its original mutation against the new local state. If the user's intent is still valid, retry the original POST request with the new `expected_menu_revision` (45). Use a **new `mutation_id`** for the retry since the payload may have changed after reconciliation.

## 3. Idempotency

Mutations are strictly idempotent via `mutation_id`. The desktop client generates a UUIDv4 for each mutation attempt. If the client loses connection right after a successful commit and retries the **exact same** `mutation_id`, the server will safely return the original `200 OK` response from the `MenuMutationLog` without double-applying the data or incorrectly failing with a 409 Conflict.

This also means: if you replay the same `mutation_id`, you get the same stored response regardless of the current server revision. The server treats it as "already committed."

## 4. Mutation Status Endpoint

If the client needs to check whether a previously submitted mutation was accepted (e.g., after a network timeout), it can poll the status endpoint:

```
GET /desktop-analytics-sync/menu-mutations/<mutation_id>/
```

- **If found:** Returns `200 OK` with the original accepted response body.
- **If not found:** Returns `404 Not Found`.

## 5. Strict Mode (now unconditional)

Sync is **strict-only** as of 2026-07-09 — the central server is the sole source of truth for every scope. There is no longer a per-scope toggle:
- There is no batched ingest endpoint. `menu-merges/ingest/`, `customer-merges/ingest/`, and `menu-mapping-verifications/ingest/` are unrouted and return `HTTP 404`; the commit endpoints are the only online/client mutation writers. The operator-only `import_menu_assignment_baseline` command remains a deliberate cutover/recovery write path.
- All interactive desktop changes **must** go through the mutation commit endpoints described in this document.
- The `strict_mode_enabled` flag is **no longer returned** (contract revision 1.2). It was a hardcoded `true` kept only so already-deployed clients kept committing; its removal condition — every install on the always-strict release — was verified before that build shipped (production 2026-08-08).

## 6. Customer Mutations

The customer mutation workflow is identical to menu mutations, with the following substitutions:

| Menu | Customer |
|------|----------|
| `POST /desktop-analytics-sync/menu-mutations/commit/` | `POST /desktop-analytics-sync/customer-mutations/commit/` |
| `GET /desktop-analytics-sync/menu-mutations/<id>/` | `GET /desktop-analytics-sync/customer-mutations/<id>/` |
| `expected_menu_revision` | `expected_customer_revision` |
| `menu_revision` (response) | `customer_revision` (response) |
| `MenuScopeState` | `CustomerScopeState` |
| `MenuMutationLog` | `CustomerMutationLog` |
