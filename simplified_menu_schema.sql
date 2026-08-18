-- PostgreSQL target schema for the global menu domain.
--
-- There is exactly one catalog. Every restaurant shares it, so there is no
-- group key: catalog identity is global by construction and menu_catalog is a
-- single row that carries the revision counter.
--
-- Rollout boundary:
--   * these tables are created beside the current Django-owned menu tables;
--   * they are populated and evaluated as a shadow schema before any API reads
--     or writes them;
--   * the current restaurant registry remains deployment authority during the
--     shadow phase, and restaurants is its queryable mirror;
--   * legacy restaurant merge events are copied into legacy_menu_history before
--     the old tables are eligible for deletion;
--   * cutover requires an idempotent backfill plus continuous synchronization or
--     a final write-fenced catch-up, followed by an explicit rollback window.
--
-- Design boundaries:
--   * canonical catalog identity is global and unqualified;
--   * variants may belong to more than one menu item;
--   * restaurant-specific menu state is limited to restaurant-qualified POS
--     locators; order-item facts remain the source for sales and unresolved IDs;
--   * menu_mapping_rules is the sole locator-to-canonical-identity authority;
--   * accepted mutations are audit/idempotency records, while menu_events are
--     immutable client-replay deltas;
--   * restaurant participation is temporal on the restaurant row itself, so
--     retiring a restaurant never rewrites historical mutations or facts;
--   * merge/mutation history keeps the originating restaurant_id for logging;
--   * active item/variant uniqueness is a fixed-width SHA-256 identity_key of
--     collapse-then-trim normalized text, matching
--     desktop_analytics_app_sync.services.global_menu_identity.
--
-- Wire note: the API maps catalog_revision to the contract's
-- menu_group_revision and emits a constant menu_group_id, so the transport
-- envelope is unaffected by the absence of a stored group key.
-- The constant is a protocol/catalog identifier and must not change after
-- clients have cached it.

-- Single-row holder for the catalog revision. The revision must be advanced in
-- the same transaction as the mutation that consumed it, so it lives in a table
-- rather than a sequence (sequences do not roll back).
CREATE TABLE menu_catalog (
    id SMALLINT PRIMARY KEY DEFAULT 1,
    name TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0,
    latest_event_seq BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT menu_catalog_singleton_ck
        CHECK (id = 1),
    CONSTRAINT menu_catalog_name_nonblank_ck
        CHECK (BTRIM(name) <> ''),
    CONSTRAINT menu_catalog_revision_nonnegative_ck
        CHECK (revision >= 0),
    CONSTRAINT menu_catalog_head_state_ck
        CHECK (
            (revision = 0 AND latest_event_seq IS NULL)
            OR
            (revision > 0 AND latest_event_seq IS NOT NULL)
        )
);

-- A CHECK can restrict the key but cannot require a row to exist. Seed the lock
-- row as part of schema creation; production migrations should use the same
-- idempotent insert.
INSERT INTO menu_catalog (id, name)
VALUES (1, 'common-menu')
ON CONFLICT (id) DO NOTHING;

-- During shadow evaluation this is populated from the settings registry and is
-- not an independent authorization or membership source. At API cutover the
-- ownership decision must be explicit: either this becomes the sole restaurant
-- directory or synchronization from settings remains mandatory. restaurant_id is
-- the exact Petpooja restID; there is intentionally no second alias for it.
-- Participation is temporal: every active restaurant is on the one catalog.
CREATE TABLE restaurants (
    restaurant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT,
    contact_information TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    onboarded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT restaurants_restaurant_id_nonblank_ck
        CHECK (BTRIM(restaurant_id) <> ''),
    CONSTRAINT restaurants_name_nonblank_ck
        CHECK (BTRIM(name) <> ''),
    CONSTRAINT restaurants_retired_state_ck
        CHECK (
            (is_active AND retired_at IS NULL)
            OR
            (NOT is_active AND retired_at IS NOT NULL)
        ),
    CONSTRAINT restaurants_retired_after_onboard_ck
        CHECK (retired_at IS NULL OR retired_at >= onboarded_at)
);

CREATE INDEX restaurants_active_idx
    ON restaurants (is_active, restaurant_id);

-- Mutation keys are TEXT, not UUID. Human commits are UUID v4, but existing
-- system events use identifiers such as migration-0033-… and migration-0034-….
-- Length matches desktop_analytics_app_sync.models.MAX_MUTATION_ID_LENGTH.
CREATE TABLE menu_mutations (
    mutation_id TEXT PRIMARY KEY,
    mutation_type TEXT NOT NULL,
    origin_type TEXT NOT NULL DEFAULT 'restaurant',
    origin_restaurant_id TEXT,
    expected_catalog_revision BIGINT NOT NULL,
    accepted_catalog_revision BIGINT NOT NULL,
    request_json JSONB NOT NULL,
    response_json JSONB NOT NULL,
    inverse_json JSONB,
    actor_json JSONB NOT NULL DEFAULT '{}'::JSONB,
    undo_of_mutation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT menu_mutations_origin_restaurant_fk
        FOREIGN KEY (origin_restaurant_id)
        REFERENCES restaurants(restaurant_id)
        ON DELETE RESTRICT,
    CONSTRAINT menu_mutations_undo_of_fk
        FOREIGN KEY (undo_of_mutation_id)
        REFERENCES menu_mutations(mutation_id)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT menu_mutations_revision_uniq
        UNIQUE (accepted_catalog_revision),
    -- Supports a composite event FK that proves an event carries the accepted
    -- revision of the mutation it names.
    CONSTRAINT menu_mutations_id_revision_uniq
        UNIQUE (mutation_id, accepted_catalog_revision),
    CONSTRAINT menu_mutations_id_nonblank_ck
        CHECK (BTRIM(mutation_id) <> ''),
    CONSTRAINT menu_mutations_id_length_ck
        CHECK (CHAR_LENGTH(mutation_id) <= 128),
    CONSTRAINT menu_mutations_undo_of_id_ck
        CHECK (
            undo_of_mutation_id IS NULL
            OR (
                BTRIM(undo_of_mutation_id) <> ''
                AND CHAR_LENGTH(undo_of_mutation_id) <= 128
            )
        ),
    CONSTRAINT menu_mutations_type_nonblank_ck
        CHECK (BTRIM(mutation_type) <> ''),
    CONSTRAINT menu_mutations_origin_type_ck
        CHECK (origin_type IN ('restaurant', 'system')),
    CONSTRAINT menu_mutations_origin_state_ck
        CHECK (
            (origin_type = 'restaurant'
                AND origin_restaurant_id IS NOT NULL
                AND BTRIM(origin_restaurant_id) <> '')
            OR
            (origin_type = 'system' AND origin_restaurant_id IS NULL)
        ),
    CONSTRAINT menu_mutations_request_object_ck
        CHECK (JSONB_TYPEOF(request_json) = 'object'),
    CONSTRAINT menu_mutations_response_object_ck
        CHECK (JSONB_TYPEOF(response_json) = 'object'),
    CONSTRAINT menu_mutations_inverse_object_ck
        CHECK (inverse_json IS NULL OR JSONB_TYPEOF(inverse_json) = 'object'),
    CONSTRAINT menu_mutations_actor_object_ck
        CHECK (JSONB_TYPEOF(actor_json) = 'object'),
    CONSTRAINT menu_mutations_expected_revision_nonnegative_ck
        CHECK (expected_catalog_revision >= 0),
    CONSTRAINT menu_mutations_accepted_revision_positive_ck
        CHECK (accepted_catalog_revision > 0),
    CONSTRAINT menu_mutations_revision_step_ck
        CHECK (accepted_catalog_revision = expected_catalog_revision + 1),
    CONSTRAINT menu_mutations_not_self_undo_ck
        CHECK (
            undo_of_mutation_id IS NULL
            OR undo_of_mutation_id <> mutation_id
        )
);

CREATE UNIQUE INDEX menu_mutations_undo_target_uniq
    ON menu_mutations (undo_of_mutation_id)
    WHERE undo_of_mutation_id IS NOT NULL;

CREATE INDEX menu_mutations_created_idx
    ON menu_mutations (created_at DESC);

CREATE INDEX menu_mutations_origin_restaurant_idx
    ON menu_mutations (origin_restaurant_id, created_at DESC)
    WHERE origin_restaurant_id IS NOT NULL;

CREATE TABLE menu_events (
    event_seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    catalog_revision BIGINT NOT NULL,
    mutation_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_json JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT menu_events_mutation_revision_fk
        FOREIGN KEY (mutation_id, catalog_revision)
        REFERENCES menu_mutations(mutation_id, accepted_catalog_revision)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT menu_events_revision_uniq
        UNIQUE (catalog_revision),
    CONSTRAINT menu_events_mutation_uniq
        UNIQUE (mutation_id),
    CONSTRAINT menu_events_seq_revision_uniq
        UNIQUE (event_seq, catalog_revision),
    CONSTRAINT menu_events_revision_positive_ck
        CHECK (catalog_revision > 0),
    CONSTRAINT menu_events_mutation_id_nonblank_ck
        CHECK (BTRIM(mutation_id) <> ''),
    CONSTRAINT menu_events_mutation_id_length_ck
        CHECK (CHAR_LENGTH(mutation_id) <= 128),
    CONSTRAINT menu_events_type_nonblank_ck
        CHECK (BTRIM(event_type) <> ''),
    CONSTRAINT menu_events_payload_object_ck
        CHECK (JSONB_TYPEOF(event_json) = 'object')
);

CREATE INDEX menu_events_occurred_idx
    ON menu_events (occurred_at DESC);

-- Keep the snapshot/event watermark on the same singleton row that commits lock.
-- The FK is added after menu_events to resolve the deliberate circular reference:
-- an event names its mutation, and the catalog state names the committed head.
ALTER TABLE menu_catalog
    ADD CONSTRAINT menu_catalog_latest_event_fk
    FOREIGN KEY (latest_event_seq, revision)
    REFERENCES menu_events(event_seq, catalog_revision)
    DEFERRABLE INITIALLY DEFERRED;

-- Immutable, non-undoable history copied from the current per-restaurant
-- menu_merge_events table. It remains separate from menu_mutations because old
-- restaurant events were never accepted singleton-catalog mutations. history_json
-- is the materialized history read model needed after the old facts/tables are
-- removed; source_payload_json preserves the original audit evidence.
CREATE TABLE legacy_menu_history (
    history_id TEXT PRIMARY KEY,
    restaurant_id TEXT NOT NULL,
    source_scope_key TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    source_payload_json JSONB NOT NULL,
    history_json JSONB NOT NULL,
    actor_json JSONB NOT NULL DEFAULT '{}'::JSONB,
    source_ingested_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT legacy_menu_history_restaurant_fk
        FOREIGN KEY (restaurant_id)
        REFERENCES restaurants(restaurant_id)
        ON DELETE RESTRICT,
    CONSTRAINT legacy_menu_history_source_uniq
        UNIQUE (source_scope_key, source_event_id),
    CONSTRAINT legacy_menu_history_id_nonblank_ck
        CHECK (BTRIM(history_id) <> ''),
    CONSTRAINT legacy_menu_history_scope_nonblank_ck
        CHECK (BTRIM(source_scope_key) <> ''),
    CONSTRAINT legacy_menu_history_event_id_nonblank_ck
        CHECK (BTRIM(source_event_id) <> ''),
    CONSTRAINT legacy_menu_history_event_type_nonblank_ck
        CHECK (BTRIM(event_type) <> ''),
    CONSTRAINT legacy_menu_history_payload_object_ck
        CHECK (JSONB_TYPEOF(source_payload_json) = 'object'),
    CONSTRAINT legacy_menu_history_projection_object_ck
        CHECK (JSONB_TYPEOF(history_json) = 'object'),
    CONSTRAINT legacy_menu_history_actor_object_ck
        CHECK (JSONB_TYPEOF(actor_json) = 'object')
);

CREATE INDEX legacy_menu_history_timeline_idx
    ON legacy_menu_history (occurred_at DESC, history_id DESC);

CREATE INDEX legacy_menu_history_restaurant_timeline_idx
    ON legacy_menu_history (restaurant_id, occurred_at DESC, history_id DESC);

-- Collapse whitespace runs to a single space, then trim, then lower — the same
-- order as normalize_identity_text. Trim-then-collapse would turn a tab-only
-- name into a space and keep leading/trailing tabs as spaces.
-- digest() comes from pgcrypto; identity_key is 64 hex characters so the
-- unique index cannot exceed the btree row-size limit.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE FUNCTION normalize_menu_identity_text(value TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
STRICT
AS $$
    SELECT LOWER(BTRIM(REGEXP_REPLACE(value, '[[:space:]]+', ' ', 'g')));
$$;

CREATE FUNCTION menu_identity_digest(parts TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
STRICT
AS $$
    SELECT encode(digest(convert_to(parts, 'UTF8'), 'sha256'), 'hex');
$$;

CREATE TABLE menu_items (
    menu_item_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT GENERATED ALWAYS AS (
        normalize_menu_identity_text(name)
    ) STORED,
    item_type TEXT NOT NULL DEFAULT '',
    normalized_item_type TEXT GENERATED ALWAYS AS (
        normalize_menu_identity_text(item_type)
    ) STORED,
    identity_key TEXT GENERATED ALWAYS AS (
        menu_identity_digest(
            normalize_menu_identity_text(name)
            || E'\x1f'
            || normalize_menu_identity_text(item_type)
        )
    ) STORED,
    description TEXT,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    lifecycle_state TEXT NOT NULL DEFAULT 'active',
    revision BIGINT NOT NULL DEFAULT 0,
    last_mutation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT menu_items_last_mutation_fk
        FOREIGN KEY (last_mutation_id, revision)
        REFERENCES menu_mutations(mutation_id, accepted_catalog_revision)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT menu_items_name_nonblank_ck
        CHECK (normalize_menu_identity_text(name) <> ''),
    CONSTRAINT menu_items_lifecycle_ck
        CHECK (lifecycle_state IN ('active', 'redirected', 'tombstoned')),
    CONSTRAINT menu_items_revision_nonnegative_ck
        CHECK (revision >= 0),
    -- MATCH SIMPLE skips a composite FK when last_mutation_id is NULL, so a
    -- row could otherwise claim revision > 0 with no mutation.
    CONSTRAINT menu_items_revision_mutation_ck
        CHECK (
            (revision = 0 AND last_mutation_id IS NULL)
            OR
            (revision > 0 AND last_mutation_id IS NOT NULL)
        )
);

-- Catalog-wide identity: one active item per SHA-256 of (normalized name, type).
CREATE UNIQUE INDEX menu_items_active_identity_uniq
    ON menu_items (identity_key)
    WHERE lifecycle_state = 'active';

CREATE INDEX menu_items_lifecycle_idx
    ON menu_items (lifecycle_state);

CREATE TABLE variants (
    variant_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT GENERATED ALWAYS AS (
        normalize_menu_identity_text(name)
    ) STORED,
    description TEXT,
    unit TEXT NOT NULL DEFAULT '',
    normalized_unit TEXT GENERATED ALWAYS AS (
        normalize_menu_identity_text(unit)
    ) STORED,
    -- DOUBLE PRECISION matches GlobalVariant.value (Django FloatField).
    -- NUMERIC(10, 2) would round 1.234 to 1.23 and can collapse two live variants
    -- into one identity_key.
    value DOUBLE PRECISION,
    identity_key TEXT GENERATED ALWAYS AS (
        menu_identity_digest(
            normalize_menu_identity_text(name)
            || E'\x1f'
            || CASE
                WHEN value IS NULL THEN normalize_menu_identity_text(unit)
                ELSE normalize_menu_identity_text(unit)
                    || E'\x1f'
                    || value::TEXT
            END
        )
    ) STORED,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    lifecycle_state TEXT NOT NULL DEFAULT 'active',
    revision BIGINT NOT NULL DEFAULT 0,
    last_mutation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT variants_last_mutation_fk
        FOREIGN KEY (last_mutation_id, revision)
        REFERENCES menu_mutations(mutation_id, accepted_catalog_revision)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT variants_name_nonblank_ck
        CHECK (normalize_menu_identity_text(name) <> ''),
    CONSTRAINT variants_value_nonnegative_ck
        CHECK (value IS NULL OR value >= 0),
    CONSTRAINT variants_lifecycle_ck
        CHECK (lifecycle_state IN ('active', 'redirected', 'tombstoned')),
    CONSTRAINT variants_revision_nonnegative_ck
        CHECK (revision >= 0),
    CONSTRAINT variants_revision_mutation_ck
        CHECK (
            (revision = 0 AND last_mutation_id IS NULL)
            OR
            (revision > 0 AND last_mutation_id IS NOT NULL)
        )
);

CREATE UNIQUE INDEX variants_active_identity_uniq
    ON variants (identity_key)
    WHERE lifecycle_state = 'active';

CREATE INDEX variants_lifecycle_idx
    ON variants (lifecycle_state);

-- This relation is canonical state, not a relationship inferred at read time.
-- Shadow backfill creates the distinct item/variant pairs found in legacy
-- assignments and authoritative rules. After cutover, only a revisioned mutation
-- may create or tombstone a membership, and its semantic delta must be included
-- in the same menu_event as the mutation. The future mutation contract must either
-- attach a new variant during variant creation or expose an explicit membership
-- mutation.
CREATE TABLE menu_item_variant_memberships (
    menu_item_id UUID NOT NULL,
    variant_id UUID NOT NULL,
    provenance TEXT NOT NULL,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    lifecycle_state TEXT NOT NULL DEFAULT 'active',
    revision BIGINT NOT NULL DEFAULT 0,
    last_mutation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (menu_item_id, variant_id),
    CONSTRAINT menu_item_variant_memberships_item_fk
        FOREIGN KEY (menu_item_id)
        REFERENCES menu_items(menu_item_id)
        ON DELETE RESTRICT,
    CONSTRAINT menu_item_variant_memberships_variant_fk
        FOREIGN KEY (variant_id)
        REFERENCES variants(variant_id)
        ON DELETE RESTRICT,
    CONSTRAINT menu_item_variant_memberships_last_mutation_fk
        FOREIGN KEY (last_mutation_id, revision)
        REFERENCES menu_mutations(mutation_id, accepted_catalog_revision)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT menu_item_variant_memberships_provenance_nonblank_ck
        CHECK (BTRIM(provenance) <> ''),
    CONSTRAINT menu_item_variant_memberships_lifecycle_ck
        CHECK (lifecycle_state IN ('active', 'tombstoned')),
    CONSTRAINT menu_item_variant_memberships_revision_nonnegative_ck
        CHECK (revision >= 0),
    CONSTRAINT menu_item_variant_memberships_revision_mutation_ck
        CHECK (
            (revision = 0 AND last_mutation_id IS NULL)
            OR
            (revision > 0 AND last_mutation_id IS NOT NULL)
        )
);

CREATE INDEX menu_item_variant_memberships_variant_idx
    ON menu_item_variant_memberships (variant_id, lifecycle_state);

CREATE TABLE menu_item_redirects (
    redirect_id UUID PRIMARY KEY,
    source_menu_item_id UUID NOT NULL,
    target_menu_item_id UUID NOT NULL,
    lifecycle_state TEXT NOT NULL DEFAULT 'active',
    created_revision BIGINT NOT NULL,
    retired_revision BIGINT,
    created_by_mutation_id TEXT NOT NULL,
    retired_by_mutation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retired_at TIMESTAMPTZ,
    CONSTRAINT menu_item_redirects_source_fk
        FOREIGN KEY (source_menu_item_id)
        REFERENCES menu_items(menu_item_id)
        ON DELETE RESTRICT,
    CONSTRAINT menu_item_redirects_target_fk
        FOREIGN KEY (target_menu_item_id)
        REFERENCES menu_items(menu_item_id)
        ON DELETE RESTRICT,
    CONSTRAINT menu_item_redirects_created_mutation_fk
        FOREIGN KEY (created_by_mutation_id, created_revision)
        REFERENCES menu_mutations(mutation_id, accepted_catalog_revision)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT menu_item_redirects_retired_mutation_fk
        FOREIGN KEY (retired_by_mutation_id, retired_revision)
        REFERENCES menu_mutations(mutation_id, accepted_catalog_revision)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT menu_item_redirects_not_self_ck
        CHECK (source_menu_item_id <> target_menu_item_id),
    CONSTRAINT menu_item_redirects_created_revision_positive_ck
        CHECK (created_revision > 0),
    CONSTRAINT menu_item_redirects_retired_revision_ck
        CHECK (
            retired_revision IS NULL
            OR retired_revision > created_revision
        ),
    CONSTRAINT menu_item_redirects_lifecycle_ck
        CHECK (
            (lifecycle_state = 'active'
                AND retired_revision IS NULL
                AND retired_by_mutation_id IS NULL
                AND retired_at IS NULL)
            OR
            (lifecycle_state = 'tombstoned'
                AND retired_revision IS NOT NULL
                AND retired_by_mutation_id IS NOT NULL
                AND retired_at IS NOT NULL)
        )
);

CREATE UNIQUE INDEX menu_item_redirects_active_source_uniq
    ON menu_item_redirects (source_menu_item_id)
    WHERE lifecycle_state = 'active';

CREATE INDEX menu_item_redirects_target_idx
    ON menu_item_redirects (target_menu_item_id)
    WHERE lifecycle_state = 'active';

CREATE TABLE variant_redirects (
    redirect_id UUID PRIMARY KEY,
    source_variant_id UUID NOT NULL,
    target_variant_id UUID NOT NULL,
    lifecycle_state TEXT NOT NULL DEFAULT 'active',
    created_revision BIGINT NOT NULL,
    retired_revision BIGINT,
    created_by_mutation_id TEXT NOT NULL,
    retired_by_mutation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retired_at TIMESTAMPTZ,
    CONSTRAINT variant_redirects_source_fk
        FOREIGN KEY (source_variant_id)
        REFERENCES variants(variant_id)
        ON DELETE RESTRICT,
    CONSTRAINT variant_redirects_target_fk
        FOREIGN KEY (target_variant_id)
        REFERENCES variants(variant_id)
        ON DELETE RESTRICT,
    CONSTRAINT variant_redirects_created_mutation_fk
        FOREIGN KEY (created_by_mutation_id, created_revision)
        REFERENCES menu_mutations(mutation_id, accepted_catalog_revision)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT variant_redirects_retired_mutation_fk
        FOREIGN KEY (retired_by_mutation_id, retired_revision)
        REFERENCES menu_mutations(mutation_id, accepted_catalog_revision)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT variant_redirects_not_self_ck
        CHECK (source_variant_id <> target_variant_id),
    CONSTRAINT variant_redirects_created_revision_positive_ck
        CHECK (created_revision > 0),
    CONSTRAINT variant_redirects_retired_revision_ck
        CHECK (
            retired_revision IS NULL
            OR retired_revision > created_revision
        ),
    CONSTRAINT variant_redirects_lifecycle_ck
        CHECK (
            (lifecycle_state = 'active'
                AND retired_revision IS NULL
                AND retired_by_mutation_id IS NULL
                AND retired_at IS NULL)
            OR
            (lifecycle_state = 'tombstoned'
                AND retired_revision IS NOT NULL
                AND retired_by_mutation_id IS NOT NULL
                AND retired_at IS NOT NULL)
        )
);

CREATE UNIQUE INDEX variant_redirects_active_source_uniq
    ON variant_redirects (source_variant_id)
    WHERE lifecycle_state = 'active';

CREATE INDEX variant_redirects_target_idx
    ON variant_redirects (target_variant_id)
    WHERE lifecycle_state = 'active';

-- The sole locator-to-canonical-identity authority. POS identifiers are
-- restaurant-qualified because Petpooja IDs may differ or collide between
-- restaurants. A reviewed itemcode is catalog-global and may identify only a
-- parent menu item. Unresolved IDs remain discoverable in order-item facts.
CREATE TABLE menu_mapping_rules (
    rule_id UUID PRIMARY KEY,
    restaurant_id TEXT,
    locator_kind TEXT NOT NULL,
    locator_value TEXT NOT NULL,
    normalized_locator TEXT GENERATED ALWAYS AS (BTRIM(locator_value)) STORED,
    menu_item_id UUID NOT NULL,
    variant_id UUID,
    provenance TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL DEFAULT 'active',
    revision BIGINT NOT NULL DEFAULT 0,
    last_mutation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT menu_mapping_rules_restaurant_fk
        FOREIGN KEY (restaurant_id)
        REFERENCES restaurants(restaurant_id)
        ON DELETE RESTRICT,
    CONSTRAINT menu_mapping_rules_item_fk
        FOREIGN KEY (menu_item_id)
        REFERENCES menu_items(menu_item_id)
        ON DELETE RESTRICT,
    CONSTRAINT menu_mapping_rules_variant_membership_fk
        FOREIGN KEY (menu_item_id, variant_id)
        REFERENCES menu_item_variant_memberships(menu_item_id, variant_id)
        ON DELETE RESTRICT,
    CONSTRAINT menu_mapping_rules_last_mutation_fk
        FOREIGN KEY (last_mutation_id, revision)
        REFERENCES menu_mutations(mutation_id, accepted_catalog_revision)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT menu_mapping_rules_locator_kind_ck
        CHECK (locator_kind IN ('pos_item', 'pos_addon', 'itemcode')),
    CONSTRAINT menu_mapping_rules_locator_restaurant_ck
        CHECK (
            (locator_kind IN ('pos_item', 'pos_addon')
                AND restaurant_id IS NOT NULL
                AND BTRIM(restaurant_id) <> '')
            OR
            (locator_kind = 'itemcode' AND restaurant_id IS NULL)
        ),
    CONSTRAINT menu_mapping_rules_itemcode_parent_ck
        CHECK (locator_kind <> 'itemcode' OR variant_id IS NULL),
    CONSTRAINT menu_mapping_rules_locator_value_nonblank_ck
        CHECK (BTRIM(locator_value) <> ''),
    CONSTRAINT menu_mapping_rules_provenance_nonblank_ck
        CHECK (BTRIM(provenance) <> ''),
    CONSTRAINT menu_mapping_rules_lifecycle_ck
        CHECK (lifecycle_state IN ('active', 'tombstoned')),
    CONSTRAINT menu_mapping_rules_revision_nonnegative_ck
        CHECK (revision >= 0),
    CONSTRAINT menu_mapping_rules_revision_mutation_ck
        CHECK (
            (revision = 0 AND last_mutation_id IS NULL)
            OR
            (revision > 0 AND last_mutation_id IS NOT NULL)
        )
);

CREATE UNIQUE INDEX menu_mapping_rules_active_restaurant_locator_uniq
    ON menu_mapping_rules (restaurant_id, locator_kind, normalized_locator)
    WHERE locator_kind IN ('pos_item', 'pos_addon')
        AND lifecycle_state = 'active';

CREATE UNIQUE INDEX menu_mapping_rules_active_itemcode_uniq
    ON menu_mapping_rules (normalized_locator)
    WHERE locator_kind = 'itemcode' AND lifecycle_state = 'active';

CREATE INDEX menu_mapping_rules_target_idx
    ON menu_mapping_rules (menu_item_id, variant_id)
    WHERE lifecycle_state = 'active';

-- Keep updated_at trustworthy for direct SQL writes as well as application writes.
CREATE FUNCTION set_menu_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

CREATE TRIGGER menu_catalog_set_updated_at
BEFORE UPDATE ON menu_catalog
FOR EACH ROW EXECUTE FUNCTION set_menu_updated_at();

CREATE TRIGGER restaurants_set_updated_at
BEFORE UPDATE ON restaurants
FOR EACH ROW EXECUTE FUNCTION set_menu_updated_at();

CREATE TRIGGER menu_items_set_updated_at
BEFORE UPDATE ON menu_items
FOR EACH ROW EXECUTE FUNCTION set_menu_updated_at();

CREATE TRIGGER variants_set_updated_at
BEFORE UPDATE ON variants
FOR EACH ROW EXECUTE FUNCTION set_menu_updated_at();

CREATE TRIGGER menu_item_variant_memberships_set_updated_at
BEFORE UPDATE ON menu_item_variant_memberships
FOR EACH ROW EXECUTE FUNCTION set_menu_updated_at();

CREATE TRIGGER menu_mapping_rules_set_updated_at
BEFORE UPDATE ON menu_mapping_rules
FOR EACH ROW EXECUTE FUNCTION set_menu_updated_at();
