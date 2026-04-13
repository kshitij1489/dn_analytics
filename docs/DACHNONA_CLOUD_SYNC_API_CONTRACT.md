# Dachnona Cloud Sync API Contract

**Audience:** Dachnona backend engineers / Codex agent implementing the cloud-side sync work  
**Status:** Ready for implementation on the Dachnona backend  
**Scope:** Add the missing cloud endpoints and persistence needed by the analytics desktop client work already implemented in this repo

## 1. Purpose

This contract is the server-side counterpart to the changes described in [DACHNONA_CLOUD_SYNC_AND_COLLABORATION_PLAN.md](/Users/kshitijsharma/Documents/projects/analytics/docs/DACHNONA_CLOUD_SYNC_AND_COLLABORATION_PLAN.md).

The analytics client already implements:

- customer merge push
- customer merge pull/apply
- device/install attribution on merge payloads
- menu bootstrap latest pull/apply
- menu merge push/pull

The remaining work is on the Dachnona cloud backend.

This document is intentionally **additive**, not a rewrite request. The backend Codex agent should:

- preserve existing auth, tenant scoping, middleware, error handling, and route grouping patterns already used by the Dachnona backend
- preserve already-working sync endpoints and tables
- add or extend only what is required for the missing merge/bootstrap contracts below
- prefer adapting current serializers/models/controllers over introducing a parallel sync subsystem

If an endpoint below already exists in the backend, keep the existing route and implementation style, and only make it compatible with the required request/response shape.

## 2. Source Of Truth

This contract is derived from the current analytics client implementation, especially:

- [src/core/customer_merge_shipper.py](/Users/kshitijsharma/Documents/projects/analytics/src/core/customer_merge_shipper.py)
- [src/core/customer_merge_sync.py](/Users/kshitijsharma/Documents/projects/analytics/src/core/customer_merge_sync.py)
- [src/core/customer_merge_sync_events.py](/Users/kshitijsharma/Documents/projects/analytics/src/core/customer_merge_sync_events.py)
- [src/core/menu_merge_shipper.py](/Users/kshitijsharma/Documents/projects/analytics/src/core/menu_merge_shipper.py)
- [src/core/menu_merge_sync.py](/Users/kshitijsharma/Documents/projects/analytics/src/core/menu_merge_sync.py)
- [src/core/menu_merge_sync_events.py](/Users/kshitijsharma/Documents/projects/analytics/src/core/menu_merge_sync_events.py)
- [src/core/menu_bootstrap_shipper.py](/Users/kshitijsharma/Documents/projects/analytics/src/core/menu_bootstrap_shipper.py)
- [src/core/menu_bootstrap_sync.py](/Users/kshitijsharma/Documents/projects/analytics/src/core/menu_bootstrap_sync.py)
- [tests/test_customer_merge_sync.py](/Users/kshitijsharma/Documents/projects/analytics/tests/test_customer_merge_sync.py)
- [tests/test_customer_merge_pull.py](/Users/kshitijsharma/Documents/projects/analytics/tests/test_customer_merge_pull.py)
- [tests/test_menu_merge_sync.py](/Users/kshitijsharma/Documents/projects/analytics/tests/test_menu_merge_sync.py)
- [tests/test_menu_bootstrap_pull.py](/Users/kshitijsharma/Documents/projects/analytics/tests/test_menu_bootstrap_pull.py)

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

## 5. Required Backend Deliverables

The backend work is complete for this change when all of the following are true:

- `POST /desktop-analytics-sync/customer-merges/ingest` accepts and persists customer merge events idempotently
- `GET /desktop-analytics-sync/customer-merges` returns customer merge deltas for cursor-based pull
- customer merge rows persist and surface `device_id` / `install_id` attribution
- `GET /desktop-analytics-sync/menu-bootstrap/latest` returns the latest bootstrap snapshot in a client-compatible shape
- `POST /desktop-analytics-sync/menu-merges/ingest` accepts and persists menu merge events idempotently
- `GET /desktop-analytics-sync/menu-merges` returns menu merge deltas for cursor-based pull

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

After implementing this contract on the Dachnona backend, the following rows in [DACHNONA_CLOUD_SYNC_AND_COLLABORATION_PLAN.md](/Users/kshitijsharma/Documents/projects/analytics/docs/DACHNONA_CLOUD_SYNC_AND_COLLABORATION_PLAN.md) can be marked complete:

- Dachnona backend ingest endpoint
- Dachnona backend delta endpoint
- Dachnona backend attribution persistence
- Dachnona backend menu bootstrap latest endpoint
- Dachnona backend menu merge ingest endpoint
- Dachnona backend menu merge delta endpoint

## 15. Final Compatibility Summary

For the current analytics client to work without further changes, the backend must satisfy these client expectations:

- customer merge ingest returns any HTTP `< 400`
- menu merge ingest returns any HTTP `< 400`
- customer merge pull accepts `cursor` and `limit`, and returns event JSON plus a cursor
- menu merge pull accepts `cursor` and `limit`, and returns event JSON plus a cursor
- menu bootstrap latest returns `id_maps` and `cluster_state` either at top-level or under `snapshot`
- merge pull responses preserve event payloads, including attribution and undo links

Anything beyond that may follow existing Dachnona backend conventions.
