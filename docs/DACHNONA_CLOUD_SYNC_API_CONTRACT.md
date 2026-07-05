# Dachnona Cloud Sync API Contract

**Audience:** Dachnona backend engineers / Codex agent implementing the cloud-side sync work  
**Status:** **Baseline contract (Section 5) is implemented** on the Dachnona central server and in use by the desktop client. The **single-source-of-truth** work first sketched in Section 16 has since been **implemented** — but via the assignment-applier + materialized-ground-truth design in [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md), which **supersedes** the Section 16 options. The authoritative, as-built wire contract — and the **freeze target for handoff** — is **Section 17**. Sections 5–15 remain accurate for the baseline; Section 16 is retained only as historical design context.  
**Scope:** merge + bootstrap + attribution sync (Sections 5–15), plus the as-built assignment/verification sync in Section 17. Section 16 is superseded — do not implement against it.

## 1. Purpose

This contract is the server-side counterpart to the changes described in [DACHNONA_CLOUD_SYNC_AND_COLLABORATION_PLAN.md](./DACHNONA_CLOUD_SYNC_AND_COLLABORATION_PLAN.md).

The analytics client already implements:

- customer merge push
- customer merge pull/apply
- device/install attribution on merge payloads
- menu bootstrap latest pull/apply
- menu merge push/pull

**Baseline (Section 5):** The endpoints and persistence described in **Section 5** and the detailed sections through **Section 15** are **already implemented** on the Dachnona central server (ingest + cursor pull for customer/menu merges, menu-bootstrap latest, attribution persistence). Treat that work as **complete** for collaboration sync.

**Single source of truth (now Section 17, implemented):** The verification-replay and richer-snapshot needs are **built and live** on the central server, using the assignment-applier design in [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md). See **Section 17** for the as-built contract (server-ordered `server_seq`, per-event accept/reject ingest, the mapping-verification stream, the materialized `order_item_assignments` ground truth, and the assignment snapshot endpoint). **Section 16 below is superseded** and kept only for history.

This document is intentionally **additive**, not a rewrite request. The backend Codex agent should:

- preserve existing auth, tenant scoping, middleware, error handling, and route grouping patterns already used by the Dachnona backend
- preserve already-working sync endpoints and tables
- add or extend only what is required for the missing merge/bootstrap contracts below
- prefer adapting current serializers/models/controllers over introducing a parallel sync subsystem

If an endpoint below already exists in the backend, keep the existing route and implementation style, and only make it compatible with the required request/response shape.

## 2. Source Of Truth

This contract is derived from the current analytics client implementation, especially:

- [src/core/customer_merge_shipper.py](../src/core/customer_merge_shipper.py)
- [src/core/customer_merge_sync.py](../src/core/customer_merge_sync.py)
- [src/core/customer_merge_sync_events.py](../src/core/customer_merge_sync_events.py)
- [src/core/menu_merge_shipper.py](../src/core/menu_merge_shipper.py)
- [src/core/menu_merge_sync.py](../src/core/menu_merge_sync.py)
- [src/core/menu_merge_sync_events.py](../src/core/menu_merge_sync_events.py)
- [src/core/menu_bootstrap_shipper.py](../src/core/menu_bootstrap_shipper.py)
- [src/core/menu_bootstrap_sync.py](../src/core/menu_bootstrap_sync.py)
- [tests/test_customer_merge_sync.py](../tests/test_customer_merge_sync.py)
- [tests/test_customer_merge_pull.py](../tests/test_customer_merge_pull.py)
- [tests/test_menu_merge_sync.py](../tests/test_menu_merge_sync.py)
- [tests/test_menu_bootstrap_pull.py](../tests/test_menu_bootstrap_pull.py)

If backend conventions differ, map this contract into those conventions without changing the client-required fields below.

## 3. Non-Goals

This change should **not**:

- rename or replace already-live Dachnona sync endpoints unrelated to merge/bootstrap sync
- force a new auth model
- require the desktop client to send new mandatory query params
- depend on local SQLite IDs from the desktop client
- normalize away the raw payloads such that the cloud can no longer replay the same event back to another device

## 4. Shared Rules

### 4.1 Auth

- The desktop client sends `Authorization: Bearer <cloud_sync_api_key>` when configured.
- Keep the backend's current auth implementation and API key verification flow.
- Do not make new headers mandatory unless the client is updated separately.

### 4.2 Tenant / Store Scoping

- All event storage and pull queries must be scoped to the correct tenant/store/restaurant using the backend's existing scoping mechanism.
- The current client does **not** send a required `store_id` query param on these pull calls.
- Therefore tenant/store resolution must come from the existing backend auth context, route context, host mapping, or equivalent backend mechanism.

### 4.3 Idempotency

- Ingest endpoints must be safe to retry.
- Deduplicate by `remote_event_id` within tenant/store scope.
- Repeated uploads of the same event must not create duplicate persisted events.
- Returning HTTP `200` or `202` for already-ingested events is acceptable.

### 4.4 Cursor Semantics

- Pull endpoints accept optional `cursor` and optional `limit`.
- Recommended ordering is ascending by effective event order:
  - `occurred_at`
  - then a stable server tie-breaker such as `ingested_at`
  - then primary key
- Return a stable opaque `next_cursor` for the last event included.
- If there are no newer rows, returning the same cursor or `null` is acceptable.

### 4.5 Raw Payload Preservation

- Persist the raw incoming JSON event payload.
- It is fine to also project searchable fields into columns.
- The pull endpoints should return payloads that remain semantically equivalent to what was ingested.
- Do not rebuild payloads from scratch if that risks dropping fields.

### 4.6 Forward Compatibility

- Ignore unknown fields instead of rejecting them.
- Treat `schema_version` as informational and persist it.
- `uploaded_by`, `uploaded_from`, `attribution.employee`, and `attribution.device` may be missing or partially populated on some rows.

## 5. Baseline backend deliverables (implemented)

The following **baseline** capabilities are **implemented on the Dachnona central server** and match what this document originally required. The desktop client relies on them today.

- `POST /desktop-analytics-sync/customer-merges/ingest` accepts and persists customer merge events idempotently  
- `GET /desktop-analytics-sync/customer-merges` returns customer merge deltas for cursor-based pull  
- customer merge rows persist and surface `device_id` / `install_id` attribution  
- `GET /desktop-analytics-sync/menu-bootstrap/latest` returns the latest bootstrap snapshot in a client-compatible shape  
- `POST /desktop-analytics-sync/menu-merges/ingest` accepts and persists menu merge events idempotently  
- `GET /desktop-analytics-sync/menu-merges` returns menu merge deltas for cursor-based pull  

**Product gap (not a baseline gap):** That baseline does **not** encode every **in-place mapping verification** (`menu_item_variants.is_verified` toggles without a merge history row) or every **`verify_item`** path on the desktop. The **desktop client** for **Section 16.1** (mapping verification ingest/pull + emitters) now lives in this repo; **Dachnona** must still implement the matching routes and persistence. **Section 16.2+** (bootstrap/snapshot checkpoints and related) remains future work — see [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md).

## 6. Recommended Persistence Shape

Use the backend's current ORM/schema style. Do not treat the names below as mandatory. They are the minimum data that must survive storage.

### 6.1 Customer Merge Event Storage

Minimum persisted fields:

- tenant/store scope key
- `remote_event_id` unique within scope
- `event_type`
- `schema_version`
- `occurred_at`
- `reverts_remote_event_id` nullable
- `payload_json`
- `uploaded_by_json` nullable
- `uploaded_from_json` nullable
- `employee_id` nullable
- `employee_name` nullable
- `device_id` nullable
- `install_id` nullable
- `device_label` nullable
- `ingested_at`

### 6.2 Menu Merge Event Storage

Minimum persisted fields:

- tenant/store scope key
- `remote_event_id` unique within scope
- `event_type`
- `schema_version`
- `occurred_at`
- `reverts_remote_event_id` nullable
- `payload_json`
- `uploaded_by_json` nullable
- `uploaded_from_json` nullable
- `employee_id` nullable
- `employee_name` nullable
- `device_id` nullable
- `install_id` nullable
- `device_label` nullable
- `ingested_at`

### 6.3 Menu Bootstrap Snapshot Storage

If the backend already persists menu bootstrap uploads, it is enough to ensure the latest snapshot can be served back in the response shape described below.

Minimum persisted fields:

- tenant/store scope key
- `id_maps` JSON
- `cluster_state` JSON
- `uploaded_by_json` nullable
- `uploaded_from_json` nullable
- `device_id` nullable
- `install_id` nullable
- `created_at` / `updated_at`

## 7. Customer Merge Ingest

### 7.1 Endpoint

`POST /desktop-analytics-sync/customer-merges/ingest`

### 7.2 Request Rules

- `Authorization: Bearer <token>` when cloud sync auth is configured
- `Content-Type: application/json`
- top-level payload contains `schema_version` and `events`
- top-level `uploaded_by` and `uploaded_from` are optional and should be persisted if present
- each event must be stored idempotently by `remote_event_id`
- the backend should preserve the full event payload, including `local_refs`, even though cloud logic should not depend on local SQLite IDs

### 7.3 Canonical Request Body

```json
{
  "schema_version": 1,
  "uploaded_by": {
    "employee_id": "0001",
    "name": "Owner"
  },
  "uploaded_from": {
    "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
    "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
    "device_label": "MacBook-Pro",
    "platform": "Darwin",
    "platform_release": "24.5.0",
    "machine": "arm64"
  },
  "events": [
    {
      "remote_event_id": "4f3198a5f96f4750b8f2dcdab0e2d89f",
      "schema_version": 1,
      "event_type": "customer_merge.applied",
      "occurred_at": "2024-02-05 12:00:00",
      "attribution": {
        "employee": {
          "employee_id": "0001",
          "name": "Owner"
        },
        "device": {
          "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
          "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
          "device_label": "MacBook-Pro",
          "platform": "Darwin",
          "platform_release": "24.5.0",
          "machine": "arm64"
        }
      },
      "source_customer": {
        "snapshot": {
          "name": "Rahul Sharma",
          "phone": "9999999999",
          "address": "HSR Layout",
          "gstin": null,
          "total_orders": 1,
          "total_spent": 80.0,
          "last_order_date": "2024-02-03 10:00:00",
          "is_verified": false
        },
        "portable_locators": {
          "customer_identity_key": "phone:source",
          "phone_hash": "8c1f1046219ddd216a023f792356ddf127fce372a8d304f8115b01f9501ef7c3",
          "name_address_hash": "7cb95a5f8f495f7b4454d7b2207c1c12ab57f2e1d1d5656e2e6ae4d3e15cf287",
          "name_normalized": "rahul sharma",
          "address_normalized": "hsr layout",
          "address_book_hashes": [
            "93d0a31ea8c636f0f4096f3c490769f2f255f78e7baf8854b45ea0f59052785e"
          ]
        }
      },
      "target_customer": {
        "snapshot": {
          "name": "Rahul S.",
          "phone": null,
          "address": "HSR Layout",
          "gstin": null,
          "total_orders": 1,
          "total_spent": 120.0,
          "last_order_date": "2024-02-04 10:00:00",
          "is_verified": false
        },
        "portable_locators": {
          "customer_identity_key": "addr:target",
          "phone_hash": null,
          "name_address_hash": "c2e16ebf6f3f5f7dab2db4b74df4c8f3817afef27506f3ccb452fc6d33f34551",
          "name_normalized": "rahul s.",
          "address_normalized": "hsr layout",
          "address_book_hashes": [
            "93d0a31ea8c636f0f4096f3c490769f2f255f78e7baf8854b45ea0f59052785e"
          ]
        }
      },
      "merge_metadata": {
        "similarity_score": 0.98,
        "model_name": "duplicate_matcher_v1",
        "reasons": [
          "phone exact match"
        ],
        "copied_address_count": 0,
        "target_before_fields": {
          "phone": null,
          "address": "HSR Layout",
          "gstin": null,
          "is_verified": false
        },
        "target_is_verified_after_merge": true,
        "mark_target_verified": true
      },
      "moved_orders": {
        "count": 1,
        "portable_refs": [
          {
            "petpooja_order_id": "PP-101",
            "stream_id": 5001,
            "event_id": "evt-101",
            "aggregate_id": "agg-101",
            "created_on": "2024-02-03 10:00:00",
            "total": 80.0,
            "local_order_id": 101
          }
        ]
      },
      "local_refs": {
        "merge_id": 999,
        "source_customer_id": 11,
        "target_customer_id": 22,
        "moved_order_ids": [
          444
        ],
        "inserted_target_address_ids": [],
        "removed_target_address_ids": []
      }
    }
  ]
}
```

### 7.4 Undo Event Example

The current client currently sends undo events with the same `source_customer`, `target_customer`, and `merge_metadata` structures as the applied event. The example below is shortened to emphasize the undo-specific fields. Backend storage and replay should preserve the full payload when those fields are present.

```json
{
  "remote_event_id": "74fc71320f9b4b598c7dcb2f6de716ef",
  "schema_version": 1,
  "event_type": "customer_merge.undone",
  "occurred_at": "2024-02-05 13:00:00",
  "reverts_remote_event_id": "4f3198a5f96f4750b8f2dcdab0e2d89f",
  "attribution": {
    "employee": {
      "employee_id": "0001",
      "name": "Owner"
    },
    "device": {
      "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
      "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
      "device_label": "MacBook-Pro",
      "platform": "Darwin",
      "platform_release": "24.5.0",
      "machine": "arm64"
    }
  },
  "source_customer": {},
  "target_customer": {},
  "merge_metadata": {},
  "undo_metadata": {
    "restored_order_count": 1,
    "restored_target_fields": [
      "address",
      "gstin",
      "is_verified",
      "phone"
    ],
    "original_merged_at": "2024-02-05 12:00:00"
  },
  "moved_orders": {
    "count": 1,
    "portable_refs": []
  },
  "local_refs": {
    "merge_id": 999
  }
}
```

### 7.5 Recommended Success Response

The current desktop client only requires a non-4xx/5xx response. The response body is not parsed today.

Recommended response:

```json
{
  "status": "ok",
  "schema_version": 1,
  "ingested_count": 1,
  "duplicate_count": 0
}
```

## 8. Customer Merge Delta Pull

### 8.1 Endpoint

`GET /desktop-analytics-sync/customer-merges`

### 8.2 Query Params

- `cursor` optional string
- `limit` optional integer

Recommended defaults:

- default `limit = 100`
- maximum `limit = 500`

### 8.3 Response Requirements

- return events in deterministic ascending replay order
- include `events`
- include `next_cursor`
- each event payload should be materially the same as the ingested payload
- include `device_id` / `install_id` in the returned event payloads via the preserved `attribution.device` object

### 8.4 Canonical Response

```json
{
  "events": [
    {
      "remote_event_id": "4f3198a5f96f4750b8f2dcdab0e2d89f",
      "schema_version": 1,
      "event_type": "customer_merge.applied",
      "occurred_at": "2024-02-05 12:00:00",
      "attribution": {
        "employee": {
          "employee_id": "0001",
          "name": "Owner"
        },
        "device": {
          "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
          "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
          "device_label": "MacBook-Pro",
          "platform": "Darwin",
          "platform_release": "24.5.0",
          "machine": "arm64"
        }
      },
      "source_customer": {
        "snapshot": {
          "name": "Rahul Sharma",
          "phone": "9999999999",
          "address": "HSR Layout",
          "gstin": null,
          "total_orders": 1,
          "total_spent": 80.0,
          "last_order_date": "2024-02-03 10:00:00",
          "is_verified": false
        },
        "portable_locators": {
          "customer_identity_key": "phone:source",
          "phone_hash": "8c1f1046219ddd216a023f792356ddf127fce372a8d304f8115b01f9501ef7c3",
          "name_address_hash": "7cb95a5f8f495f7b4454d7b2207c1c12ab57f2e1d1d5656e2e6ae4d3e15cf287",
          "name_normalized": "rahul sharma",
          "address_normalized": "hsr layout",
          "address_book_hashes": [
            "93d0a31ea8c636f0f4096f3c490769f2f255f78e7baf8854b45ea0f59052785e"
          ]
        }
      },
      "target_customer": {
        "snapshot": {
          "name": "Rahul S.",
          "phone": null,
          "address": "HSR Layout",
          "gstin": null,
          "total_orders": 1,
          "total_spent": 120.0,
          "last_order_date": "2024-02-04 10:00:00",
          "is_verified": false
        },
        "portable_locators": {
          "customer_identity_key": "addr:target",
          "phone_hash": null,
          "name_address_hash": "c2e16ebf6f3f5f7dab2db4b74df4c8f3817afef27506f3ccb452fc6d33f34551",
          "name_normalized": "rahul s.",
          "address_normalized": "hsr layout",
          "address_book_hashes": [
            "93d0a31ea8c636f0f4096f3c490769f2f255f78e7baf8854b45ea0f59052785e"
          ]
        }
      },
      "merge_metadata": {
        "similarity_score": 0.98,
        "model_name": "duplicate_matcher_v1",
        "reasons": [
          "phone exact match"
        ],
        "copied_address_count": 0,
        "target_before_fields": {
          "phone": null,
          "address": "HSR Layout",
          "gstin": null,
          "is_verified": false
        },
        "target_is_verified_after_merge": true,
        "mark_target_verified": true
      },
      "moved_orders": {
        "count": 1,
        "portable_refs": [
          {
            "petpooja_order_id": "PP-101",
            "stream_id": 5001,
            "event_id": "evt-101",
            "aggregate_id": "agg-101",
            "created_on": "2024-02-03 10:00:00",
            "total": 80.0,
            "local_order_id": 101
          }
        ]
      },
      "local_refs": {
        "merge_id": 999,
        "source_customer_id": 11,
        "target_customer_id": 22,
        "moved_order_ids": [
          444
        ],
        "inserted_target_address_ids": [],
        "removed_target_address_ids": []
      }
    }
  ],
  "next_cursor": "customer-merge-cursor-000001"
}
```

### 8.5 Compatibility Note

The current client also tolerates:

- `items` instead of `events`
- `cursor_after` instead of `next_cursor`

For new backend work, prefer `events` + `next_cursor`.

## 9. Menu Bootstrap Latest Pull

### 9.1 Endpoint

`GET /desktop-analytics-sync/menu-bootstrap/latest`

### 9.2 Server Behavior

- return the latest bootstrap snapshot for the authenticated tenant/store
- this can be backed by the existing menu bootstrap ingest storage if that already exists
- no client change is required if the backend already has this route and it returns a compatible shape

### 9.3 Required Response Shape

The client accepts either:

1. top-level `id_maps` and `cluster_state`, or
2. `snapshot.id_maps` and `snapshot.cluster_state`

Optional metadata the client will preserve if present:

- `updated_at`
- `created_at`
- `snapshot_id`
- `cursor`
- `version`

### 9.4 Preferred Response Example

```json
{
  "snapshot_id": "menu-bootstrap-2026-04-14T10:00:00Z",
  "version": 1,
  "updated_at": "2026-04-14T10:00:00Z",
  "id_maps": {
    "menu_id_to_str": {
      "item_cold_coffee": "Cold Coffee"
    },
    "variant_id_to_str": {
      "variant_large": "Large"
    },
    "type_id_to_str": {
      "type_beverage": "Beverage"
    }
  },
  "cluster_state": {
    "item_cold_coffee:type_beverage": {
      "101": [
        [
          "101",
          "variant_large"
        ]
      ]
    }
  }
}
```

### 9.5 Compatibility Note

Do not force a new envelope if the backend already returns:

```json
{
  "snapshot": {
    "id_maps": {},
    "cluster_state": {}
  }
}
```

That shape is also accepted by the current client.

## 10. Menu Merge Ingest

### 10.1 Endpoint

`POST /desktop-analytics-sync/menu-merges/ingest`

### 10.2 Request Rules

- same top-level envelope pattern as customer merge ingest
- same auth behavior
- same idempotency by `remote_event_id`
- preserve raw payload
- persist attribution including `device_id` / `install_id`

### 10.3 Canonical Request Body

```json
{
  "schema_version": 1,
  "uploaded_by": {
    "employee_id": "0001",
    "name": "Owner"
  },
  "uploaded_from": {
    "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
    "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
    "device_label": "MacBook-Pro",
    "platform": "Darwin",
    "platform_release": "24.5.0",
    "machine": "arm64"
  },
  "events": [
    {
      "remote_event_id": "remote-menu-merge-1",
      "schema_version": 1,
      "event_type": "menu_merge.applied",
      "occurred_at": "2026-04-14T10:00:00Z",
      "attribution": {
        "employee": {
          "employee_id": "0001",
          "name": "Owner"
        },
        "device": {
          "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
          "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
          "device_label": "MacBook-Pro",
          "platform": "Darwin",
          "platform_release": "24.5.0",
          "machine": "arm64"
        }
      },
      "source_item": {
        "menu_item_id": "item_source",
        "name": "Iced Coffee",
        "type": "Beverage",
        "is_verified": true
      },
      "target_item": {
        "menu_item_id": "item_target",
        "name": "Cold Coffee",
        "type": "Beverage",
        "is_verified": true
      },
      "merge_payload": {
        "kind": "basic_merge_v1",
        "operation_signature": "f7182ce9505bd3f7fbe750179741c181be1f5146142c86cc3d6b7a884fda06fa"
      },
      "local_refs": {
        "merge_id": 123
      }
    }
  ]
}
```

### 10.4 Supported `merge_payload.kind` Values

The backend does not need to execute menu merge logic. It needs to persist and replay the payload faithfully.

Supported kinds emitted/consumed by the current client:

- `basic_merge_v1`
- `variant_merge_v1`
- `resolution_variant_v1`

#### `variant_merge_v1` example

```json
{
  "kind": "variant_merge_v1",
  "variant_mappings": [
    {
      "source_variant_id": "variant_small",
      "source_variant_name": "Small",
      "target_variant_id": "variant_large",
      "target_variant_name": "Large"
    }
  ],
  "history_payload": {
    "kind": "variant_merge_v1"
  },
  "operation_signature": "6f2f4f2bfde0dce3ab670301a5ebecfe95d59cb6d4a0f8d8182424efc3021cef"
}
```

#### `resolution_variant_v1` example

```json
{
  "kind": "resolution_variant_v1",
  "resolution": {
    "source_variant_id": "variant_small",
    "source_variant_name": "Small",
    "target_variant_id": "variant_large",
    "target_variant_name": "Large"
  },
  "history_payload": {
    "kind": "resolution_variant_v1"
  },
  "operation_signature": "f7a7d7cc59b118baf2da5d9d22f0af0d2d95f503b2bbcc43ed47bdadf129e506"
}
```

### 10.5 Undo Event Example

```json
{
  "remote_event_id": "remote-menu-merge-undo-1",
  "schema_version": 1,
  "event_type": "menu_merge.undone",
  "occurred_at": "2026-04-14T10:05:00Z",
  "reverts_remote_event_id": "remote-menu-merge-1",
  "source_item": {
    "menu_item_id": "item_source",
    "name": "Iced Coffee",
    "type": "Beverage",
    "is_verified": true
  },
  "target_item": {
    "menu_item_id": "item_target",
    "name": "Cold Coffee",
    "type": "Beverage",
    "is_verified": true
  },
  "merge_payload": {
    "kind": "basic_merge_v1",
    "operation_signature": "f7182ce9505bd3f7fbe750179741c181be1f5146142c86cc3d6b7a884fda06fa"
  },
  "undo_metadata": {
    "original_merged_at": "2026-04-14T10:00:00Z"
  }
}
```

### 10.6 Recommended Success Response

As with customer merge ingest, the current client only requires a non-4xx/5xx response.

```json
{
  "status": "ok",
  "schema_version": 1,
  "ingested_count": 1,
  "duplicate_count": 0
}
```

## 11. Menu Merge Delta Pull

### 11.1 Endpoint

`GET /desktop-analytics-sync/menu-merges`

### 11.2 Query Params

- `cursor` optional string
- `limit` optional integer

### 11.3 Response Requirements

- return events in deterministic ascending replay order
- include `events`
- include `next_cursor`
- return each event payload unchanged enough for faithful client replay
- preserve `reverts_remote_event_id` on undo events

### 11.4 Canonical Response

```json
{
  "events": [
    {
      "remote_event_id": "remote-menu-merge-1",
      "schema_version": 1,
      "event_type": "menu_merge.applied",
      "occurred_at": "2026-04-14T10:00:00Z",
      "attribution": {
        "employee": {
          "employee_id": "0001",
          "name": "Owner"
        },
        "device": {
          "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
          "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
          "device_label": "MacBook-Pro",
          "platform": "Darwin",
          "platform_release": "24.5.0",
          "machine": "arm64"
        }
      },
      "source_item": {
        "menu_item_id": "item_source",
        "name": "Iced Coffee",
        "type": "Beverage",
        "is_verified": true
      },
      "target_item": {
        "menu_item_id": "item_target",
        "name": "Cold Coffee",
        "type": "Beverage",
        "is_verified": true
      },
      "merge_payload": {
        "kind": "basic_merge_v1",
        "operation_signature": "f7182ce9505bd3f7fbe750179741c181be1f5146142c86cc3d6b7a884fda06fa"
      }
    },
    {
      "remote_event_id": "remote-menu-merge-undo-1",
      "schema_version": 1,
      "event_type": "menu_merge.undone",
      "occurred_at": "2026-04-14T10:05:00Z",
      "reverts_remote_event_id": "remote-menu-merge-1",
      "attribution": {
        "employee": {
          "employee_id": "0001",
          "name": "Owner"
        },
        "device": {
          "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
          "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
          "device_label": "MacBook-Pro",
          "platform": "Darwin",
          "platform_release": "24.5.0",
          "machine": "arm64"
        }
      },
      "source_item": {
        "menu_item_id": "item_source",
        "name": "Iced Coffee",
        "type": "Beverage",
        "is_verified": true
      },
      "target_item": {
        "menu_item_id": "item_target",
        "name": "Cold Coffee",
        "type": "Beverage",
        "is_verified": true
      },
      "merge_payload": {
        "kind": "basic_merge_v1",
        "operation_signature": "f7182ce9505bd3f7fbe750179741c181be1f5146142c86cc3d6b7a884fda06fa"
      },
      "undo_metadata": {
        "original_merged_at": "2026-04-14T10:00:00Z"
      }
    }
  ],
  "next_cursor": "menu-merge-cursor-000002"
}
```

### 11.5 Compatibility Note

As with customer merges, the client also tolerates:

- `items` instead of `events`
- `cursor_after` instead of `next_cursor`

For new backend work, prefer `events` + `next_cursor`.

## 12. Attribution Persistence Requirement

The plan item for attribution should only be marked complete when both of the following are true:

1. `device_id` and `install_id` are persisted server-side for customer merge and menu merge events.
2. those values come back to the client via pull responses inside `attribution.device`, or via the preserved raw payload that contains `attribution.device`.

Minimum fields to preserve:

- `device_id`
- `install_id`
- `device_label`
- `platform`
- `platform_release`
- `machine`
- `employee_id`
- `employee_name`

The backend may additionally expose these in admin views, audit screens, or reporting tables if useful.

## 13. Implementation Notes For The Backend Codex Agent

- Reuse existing Dachnona models/tables if they already cover part of this data.
- If an existing sync/event table already stores JSON payloads, extend it rather than creating a redundant second store.
- If `menu-bootstrap/latest` is already live, do not replace it. Only confirm it emits `id_maps` and `cluster_state` in one of the accepted shapes.
- Do not make the backend depend on `local_refs`. Keep them only for audit/debug payload parity.
- Do not rewrite customer/menu payloads to local server IDs. Keep portable locators and raw payload fidelity.
- Prefer additive migrations:
  - new tables if no equivalent exists
  - new nullable columns if equivalent tables already exist
  - new indexes for `(tenant_scope, remote_event_id)` and pull ordering
- Preserve older rows that may not have attribution fields.

## 14. Completion Checklist

### 14.1 Baseline collaboration sync (complete)

The following **baseline** items are **done** on the Dachnona central server (aligned with Section 5). The corresponding rows in [DACHNONA_CLOUD_SYNC_AND_COLLABORATION_PLAN.md](./DACHNONA_CLOUD_SYNC_AND_COLLABORATION_PLAN.md) can be treated as **complete**:

- Dachnona backend customer merge ingest endpoint  
- Dachnona backend customer merge delta endpoint  
- Dachnona backend attribution persistence  
- Dachnona backend menu bootstrap latest endpoint  
- Dachnona backend menu merge ingest endpoint  
- Dachnona backend menu merge delta endpoint  

### 14.2 Assignment / verification sync (implemented — Section 17)

Delivered on the central server via the [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) design (supersedes the Section 16 options). Full wire contract in **Section 17**:

- [x] Server-ordered replay: `(ingested_at, id)`, v2 cursors, `server_seq` / `server_ingested_at` injected into every delta event  
- [x] Per-event accept/reject ingest (`accepted` / `rejected`; one bad event never 400s the batch)  
- [x] Menu merge events carry an explicit `assignments` list (schema v2); v1 derivation pinned by `contracts/menu_merge_event_fixtures.json`  
- [x] Mapping verification ingest + pull; the verification stream is the **sole owner** of `is_verified` (merge only seeds it on insert); shape pinned by `contracts/menu_mapping_verification_event_fixtures.json`  
- [x] Materialized `order_item_assignments` ground truth + `rebuild_order_item_assignments` + `import_menu_assignment_baseline`  
- [x] `GET /menu-assignments/snapshot` for fresh-install seeding (both merge and verification watermarks + cursors)  
- [ ] Optional: server-side compaction / retention policy for high-volume verification events  

## 15. Final Compatibility Summary

For the current analytics client to work without further changes, the backend must satisfy these client expectations:

- customer merge ingest returns any HTTP `< 400`
- menu merge ingest returns any HTTP `< 400`
- customer merge pull accepts `cursor` and `limit`, and returns event JSON plus a cursor
- menu merge pull accepts `cursor` and `limit`, and returns event JSON plus a cursor
- menu bootstrap latest returns `id_maps` and `cluster_state` either at top-level or under `snapshot`
- merge pull responses preserve event payloads, including attribution and undo links

The current client also depends on the **Section 17** surfaces (server_seq ordering, the verification stream, and the assignment snapshot). Those are the freeze target; the bullets above are the older baseline floor.

Anything beyond that may follow existing Dachnona backend conventions.

---

## 16. Extensions for menu single source of truth (SUPERSEDED — historical)

> **Superseded by Section 17.** This section captured three candidate designs (verification stream / richer snapshot / hosted reference DB) before the approach was settled. The product shipped **Option A** (the mapping-verification event stream) **plus** a materialized `order_item_assignments` ground truth and an assignment snapshot endpoint — see [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) and **Section 17** for what is actually built and frozen. Do not implement against Section 16; it is retained for design history only.

**Goal:** Allow a **second install** (or a device after **reset orders + sync**) to **converge** on the same **verified catalog + mapping** state as a **reference** desktop, as described in [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md).

**Why baseline is not enough:** The desktop can show **“All items verified!”** while **only** updating SQLite (and local `data/*.json` via `export_to_backups`) for paths that **do not** enqueue `menu_merge` sync events (e.g. in-place `menu_item_variants.is_verified = 1`, `verify_item`). The **baseline** merge + bootstrap APIs therefore **cannot** replay those edges today.

**Principle:** Reuse **Section 4** (auth, tenant scoping, idempotency, cursors, raw payload preservation, forward compatibility) for every new endpoint.

### 16.1 Option A — Mapping verification event stream (preferred for auditability)

**New server capabilities**

1. **Persistence** — Store events in tenant/store scope with at least:
   - `remote_event_id` (unique per scope), `event_type`, `occurred_at`, `schema_version`, `payload_json`, `ingested_at`, optional `reverts_remote_event_id`, same attribution projection pattern as Sections 6.1–6.2 where applicable.

2. **Ingest** — `POST /desktop-analytics-sync/menu-mapping-verifications/ingest` (path matches desktop `client_learning_shipper` / env defaults)  
   - Body: same envelope as menu merge ingest — top-level `schema_version`, `events` array of per-event JSON objects, optional `uploaded_by` / `uploaded_from`.  
   - Idempotent dedupe by `remote_event_id` per **Section 4.3**.  
   - Each event uses `event_type` of `mapping.verified`, `mapping.bulk_verified`, or `mapping.reopened` (desktop emits the first two today).

3. **Pull** — e.g. `GET /desktop-analytics-sync/menu-mapping-verifications`  
   - Query params: optional `cursor`, optional `limit` per **Section 4.4**.  
   - Response: `events` (or `items` alias tolerated by client), `next_cursor` (or `cursor_after` alias).  
   - Ordering: stable ascending for replay.

**Minimum payload semantics** (illustrative; exact JSON can mirror menu merge style)

- Identify the mapping: prefer **`order_item_id`** (POS-stable) and/or **`menu_item_id` + `variant_id`**.  
- Declare target: `is_verified` (typically `1`).  
- Include `schema_version`, `occurred_at`, `remote_event_id`, and optional `uploaded_by` / `uploaded_from` / `attribution` blobs per **Section 4.6** and **Section 12**.

**Server-side policy decisions** (must be documented in Dachnona runbooks)

- Retention / compaction for high-volume stores.  
- Conflict precedence when a verification event arrives **after** a merge undo (recommend: **merge / undo wins** over stale verify unless product says otherwise).

### 16.2 Option B — Richer menu bootstrap or catalog snapshot (checkpoints)

**Extend or add** (choose one product direction; both need server work if payloads grow beyond current limits)

1. **Extend** `GET /desktop-analytics-sync/menu-bootstrap/latest` to return, in addition to current `id_maps` + `cluster_state`:
   - `snapshot_version` / `generated_at` opaque version string  
   - Optional **`mapping_verification_overlay`** or **backward-compatible** extension of `cluster_state` list entries to include **`is_verified`** per mapping (see [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md) Section 5.2).

2. **Or** add **`GET /desktop-analytics-sync/menu-catalog-snapshot/latest`** returning a versioned document the client can apply as a checkpoint (same auth; consider **gzip** or signed URL if size exceeds practical JSON inline).

**Server responsibilities**

- Store and serve **large** snapshots safely (size limits, CDN URL, or chunked download if needed).  
- Validate tenant scope; do not mix stores.  
- Preserve **forward compatibility** (**Section 4.6**): unknown fields tolerated on ingest if client uploads snapshots.

### 16.3 Option C — Hosted reference DB (optional)

If the product uses **packaged SQLite** or encrypted blobs for factory/support installs, Dachnona may host **static artifacts** (signed URL, checksum header). This is **optional** and orthogonal to Sections 16.1–16.2.

### 16.4 Desktop client dependency (for implementers)

The desktop **Option A** client work for **Section 16.1** is implemented in this repo (`menu_mapping_verification_*`, `run_best_effort_cloud_pulls` ordering, `verify_item` / `resolve_menu_item_variant` emitters, `client_learning_shipper`). Dachnona must still expose the ingest/pull routes and persistence described above for uploads and cross-device replay to succeed end-to-end.

### 16.5 Explicit non-goals for extensions

- Do **not** require the desktop to send new **mandatory** auth headers beyond existing Bearer usage (**Section 4.1**).  
- Do **not** strip raw payloads for verification events; replay fidelity matters.  
- Prefer **additive** tables/columns over breaking existing merge/bootstrap tables.

---

## 17. As-built assignment & verification sync (implemented — freeze target)

This section is the **authoritative, as-built wire contract** for the collaboration-sync redesign in [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md). It is what the central server runs today and what the desktop client depends on. **Freeze this before handoff.** It reuses **Section 4** (auth, tenant scope, idempotency, raw-payload preservation) unchanged.

### 17.1 Server ordering, cursors, and per-event ingest

Applies to all three replay streams (customer merges, menu merges, mapping verifications) — they share one delta/ingest engine.

- **Replay order is server ingestion order only: `(ingested_at, id)`.** Client `occurred_at` is display metadata and is **not** used for ordering (an offline install's backlog must still reach peers whose cursors have advanced).
- **`server_seq` = the event row's autoincrement `id`.** Every delta event is returned with two injected keys: `"server_seq"` and `"server_ingested_at"` (ISO-8601). These are additive; the client keys conflict resolution on `server_seq`.
- **Cursors are opaque v2 tokens**: base64url of `{"v":2,"ingested_at":<iso>,"id":<int>}`. A v1 (or otherwise unparseable) cursor **must** be rejected with HTTP 400 and a distinct message (the client resets its cursor and re-pulls). Each stream has its **own** cursor and its **own** id space — never compare a `server_seq` from one stream against a row guarded by another.
- **Ingest is per-event, not all-or-nothing.** `POST .../ingest` validates and persists each event independently and returns:

  ```json
  {"status":"ok","schema_version":2,"ingested_count":N,"duplicate_count":M,
   "accepted":["<remote_event_id>", ...],
   "rejected":[{"remote_event_id":"...","error":"..."}]}
  ```

  A malformed event lands in `rejected`; its batch siblings still ingest. Dedupe is by `(scope_key, remote_event_id)`.

### 17.2 Menu merge events carry explicit `assignments`

Menu merge events (schema v2) include an explicit assignment list so any peer applies them without needing the source cluster to still exist:

```json
"merge_payload": {
  "kind": "resolution_variant_v1",
  "assignments": [
    {"order_item_id": "…", "menu_item_id": "<target>", "variant_id": "<id-or-null>", "is_verified": 1}
  ]
}
```

- A normalized assignment always carries `order_item_id` + `menu_item_id`; `variant_id` / `is_verified` are **omitted** when the event does not specify them; `variant_id: null` is the SQL NULL variant (`__NULL_VARIANT__` normalizes to null).
- Legacy v1 events (no `assignments`) are derived from `history_payload` (`mapping_rows`, or `affected_order_item_ids` + target).
- **Extraction parity is pinned by `contracts/menu_merge_event_fixtures.json`, which must be byte-identical in both repos.** Client extractor: `src/core/menu_assignment_apply.extract_assignments`; server extractor: `services/assignment_state.extract_assignments`. Both are tested against the fixtures.

### 17.3 Materialized ground truth (`order_item_assignments`)

The server maintains a per-scope, per-`order_item_id` row `→ (menu_item_id, variant_id, is_verified)` as events ingest:

- **Merge apply** (`apply_menu_merge_event_to_assignments`) upserts guarded by `last_seq < server_seq` (a conditional UPDATE, else INSERT). It writes `menu_item_id` / `variant_id` on every apply, but writes **`is_verified` only on INSERT** (a create-only seed).
- The table is the queryable ground truth and the source for the snapshot endpoint. `rebuild_order_item_assignments` replays the whole log (all merges, then all verifications) to reconstruct it identically; `import_menu_assignment_baseline` publishes a golden install's full state as authoritative v2 events.

### 17.4 `is_verified` has a single owning stream

**Invariant (plan I5):** the mapping-verification stream is the **sole** authority for `is_verified` on an existing row. The merge stream may only *seed* it when it first creates a row; it must never rewrite the flag on UPDATE. This removes any cross-stream ordering hazard — the flag can no longer diverge on merge-vs-verification interleaving.

Consequences the server enforces / relies on:

- The verification apply (`apply_menu_mapping_verification_event_to_assignments`) is guarded by **`last_verification_seq`** (the verification table's own id space), **never** by `last_seq`.
- Every flag-changing desktop operation — resolve, remap, verify/reopen, bulk actions, and undo — emits a verification event. `import_menu_assignment_baseline` therefore emits **both** merge events (mapping) **and** verification events (the golden flag), so a cutover converges `is_verified` on already-materialized rows.
- Snapshot bootstrap / force-reseed adopt the flag from the full-state snapshot; that is the one non-verification path that sets the flag, and it reads the server's already-materialized value.

### 17.5 Mapping verification stream

- **Ingest**: `POST /desktop-analytics-sync/menu-mapping-verifications/ingest` — same envelope and per-event accept/reject as §17.1.
- **Pull**: `GET /desktop-analytics-sync/menu-mapping-verifications` — `cursor`, `limit`; returns `{"events":[...],"next_cursor":...}` with `server_seq` injected (§17.1).
- **Event shapes** (top-level fields, `schema_version: 1`):
  - `mapping.verified` — `order_item_id`, `menu_item_id`, `variant_id`, `is_verified` (default 1).
  - `mapping.reopened` — `order_item_id`, `is_verified` (default 0).
  - `mapping.bulk_verified` — `mappings: [{order_item_id, menu_item_id, variant_id, is_verified}, ...]` (each default 1).
- The normalization from an event to its `(order_item_id, is_verified)` rows is **pinned by `contracts/menu_mapping_verification_event_fixtures.json`** (byte-identical in both repos). Client helper: `src/core/menu_mapping_verification_sync_events.extract_verification_entries`; server helper: `services/assignment_state.extract_verification_entries`. Verification apply is flag-only — it never rewrites `menu_item_id` / `variant_id`.

### 17.6 Assignment snapshot endpoint (fresh-install seeding)

`GET /desktop-analytics-sync/menu-assignments/snapshot` — auth per §4.1.

- **Query params**: `limit` (bounded), `after` = last `order_item_id` of the previous page.
- **Response**:

  ```json
  {
    "assignments": [
      {"order_item_id":"…","menu_item_id":"…","variant_id":null,
       "is_verified":1,"last_seq":123,"last_verification_seq":45,"last_event_id":"…"}
    ],
    "watermark_seq": 123, "watermark_cursor": "<v2 merge cursor>",
    "verification_watermark_seq": 45, "verification_watermark_cursor": "<v2 verification cursor>",
    "next_page": "<order_item_id or null>"
  }
  ```

- **Two independent watermarks, one per stream.** The client seeds its **merge** cursor from `watermark_cursor` and its **verification** cursor from `verification_watermark_cursor`, then tails each with `seq > watermark`. Because the streams have separate id spaces, both are required — omitting the verification watermark forces fresh installs to replay the entire verification stream.
- **Both watermarks are captured *before* the page is read**, so a cursor can never sit ahead of a row's materialized state; anything newer is re-delivered by the tail and re-applied idempotently under the per-row seq guard.

### 17.7 Freeze checklist for the central server team

Frozen surfaces the client relies on (changing any is a breaking change):

1. `(ingested_at, id)` replay order; v2 cursors; v1 rejected with 400.
2. `server_seq` + `server_ingested_at` on every delta event.
3. Per-event `accepted` / `rejected` ingest result.
4. `order_item_assignments` upsert rules: merge guarded by `last_seq`, `is_verified` seeded on INSERT only; verification guarded by `last_verification_seq`, flag-only.
5. Snapshot response fields incl. **both** watermarks + cursors and per-row `last_seq` / `last_verification_seq`.
6. The two contract fixtures, kept byte-identical across repos.

Additive changes (new response keys, new event kinds behind new `kind` values, new nullable columns) remain safe under §4.6.
