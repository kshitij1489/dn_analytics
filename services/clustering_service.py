import sys
import os
import re
import uuid
import logging
from typing import Dict, List, Tuple, Optional, Any, NamedTuple
from datetime import datetime
import difflib

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.db.connection import get_db_connection
from utils.id_generator import generate_deterministic_id
from utils.menu_item_variant_enforcement import ensure_menu_item_has_variant_mapping
from utils.variant_metadata import infer_variant_metadata
from src.core.mapping_anomalies import (
    ensure_mapping_anomaly_schema,
    record_core_and_flag,
)
from src.core.menu_assignment_schema import ensure_assignment_sync_schema
from src.core.itemcode_mapping import (
    ensure_itemcode_mapping_schema,
    get_active_itemcode_mapping,
    observe_itemcode_assignment,
)
from src.core.global_menu_identity import resolve_global_identity_for_ingest
from src.core.global_menu_schema import resolve_global_menu_capability

try:
    from utils.clean_order_item import clean_order_item_name
except ImportError:
    logging.warning("Could not import clean_order_item_name")
    def clean_order_item_name(name):
        return {'name': name, 'variant': 'UNKNOWN', 'type': 'UNKNOWN'}

class ClusterMatch(NamedTuple):
    """Result of OrderItemCluster.add().

    match_method is the provenance of the resolution, one of:
      existing-id-hit  — petpooja id already mapped (reused as-is)
      itemcode-hit     — unseen Petpooja itemid assigned to an active,
                         route-eligible itemcode parent (server-backed
                         parent-level POS product code ownership)
      name-hit         — cleaned name+type matched an existing menu_item
      fuzzy-suggested  — new item, difflib suggested a verified item (score-based)
      new              — brand-new item, no suggestion
      unmatched        — no resolvable menu_item (empty/blank name)
      restaurant-pos / group-itemcode / global-alias — a server-approved
                         global-menu rule selected the canonical projection
    match_confidence is 0..100 (fuzzy = difflib ratio * 100; exact hits = 100).
    An itemcode hit uses 100.0: this is provenance confidence, not is_verified.
    """
    menu_item_id: Optional[str]
    order_item_id: Optional[str]
    variant_id: Optional[str]
    item_type: Optional[str]
    match_method: Optional[str]
    match_confidence: float


class OrderItemCluster:
    def __init__(self, db_conn=None):
        """
        Initialize Clustering Service.
        Args:
            db_conn: Existing sqlite3 connection. Every runtime caller passes one
                from an already-opened restaurant profile. The fallback below only
                serves standalone dev/CLI use and cannot pick a profile for you.
        """
        if db_conn:
            self.conn = db_conn
            self.own_connection = False
        else:
            try:
                # Standalone fallback: only usable when no explicit profile
                # routing is configured (i.e. not under the packaged app).
                self.conn, message = get_db_connection()
                self.own_connection = self.conn is not None
                if self.conn is None:
                    logging.error(
                        "OrderItemCluster needs an explicit restaurant profile "
                        "connection: %s",
                        message,
                    )
            except Exception as e:
                logging.error(f"Failed to create DB connection: {e}")
                self.conn = None
                self.own_connection = False

        if self.conn:
            try:
                ensure_mapping_anomaly_schema(self.conn)
            except Exception:
                logging.getLogger(__name__).debug(
                    "mapping anomaly schema ensure skipped", exc_info=True
                )
            try:
                ensure_assignment_sync_schema(self.conn)
            except Exception:
                logging.getLogger(__name__).debug(
                    "assignment sync schema ensure skipped", exc_info=True
                )
            try:
                # The standalone order-loader schema guard checks only a subset
                # of table names and can skip the canonical schema script on an
                # existing database, so guarantee the projection table here
                # before the first lookup. Idempotent on every startup path.
                ensure_itemcode_mapping_schema(self.conn)
            except Exception:
                logging.getLogger(__name__).debug(
                    "itemcode mapping schema ensure skipped", exc_info=True
                )

    def __del__(self):
        if hasattr(self, 'own_connection') and self.own_connection and self.conn:
            self.conn.close()

    def predict_menu_item_name(self, clean_name: str, item_type: str = None) -> Optional[Tuple[str, str, float]]:
        """
        Suggest an existing verified menu_item_id + name for a given raw name.
        Returns: (menu_item_id, name, score) or None
        """
        cursor = self.conn.cursor()
        try:
            # Get all verified items of the same type (or all if type is None)
            if item_type:
                cursor.execute("SELECT menu_item_id, name FROM menu_items WHERE type = ? AND is_verified = 1", (item_type,))
            else:
                cursor.execute("SELECT menu_item_id, name FROM menu_items WHERE is_verified = 1")
            
            candidates = cursor.fetchall()
            if not candidates:
                return None
            
            # Use difflib to find the best match
            names = [c[1] for c in candidates]
            best_matches = difflib.get_close_matches(clean_name, names, n=1, cutoff=0.7)
            
            if best_matches:
                match_name = best_matches[0]
                # Find the ID for this name
                for mid, name in candidates:
                    if name == match_name:
                        score = difflib.SequenceMatcher(None, clean_name, match_name).ratio()
                        return str(mid), name, score
            
            return None
        finally:
            cursor.close()

    def _ensure_variant(self, cursor, variant_name: str) -> str:
        """Upsert the variant row for variant_name; returns its variant_id.

        Populates unit/value via infer_variant_metadata so volume analytics can
        count new 200ML / UNKNOWN_* variants instead of leaving them NULL.
        """
        variant_id = generate_deterministic_id(variant_name)
        variant_meta = infer_variant_metadata(variant_name)
        cursor.execute("""
            INSERT INTO variants (variant_id, variant_name, unit, value, is_verified)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT (variant_id) DO UPDATE SET
                unit = COALESCE(variants.unit, excluded.unit),
                value = COALESCE(variants.value, excluded.value)
        """, (variant_id, variant_name, variant_meta["unit"], variant_meta["value"]))
        return variant_id

    def _mapping_inherits_verification(self, cursor, menu_item_id: str, variant_id: Optional[str]) -> bool:
        """C1 heuristic: a new mapping inherits verified status only when the
        same (menu_item_id, variant_id) pair is already verified by a
        server-acknowledged row."""
        cursor.execute(
            """
            SELECT 1 FROM menu_item_variants
            WHERE menu_item_id = ? AND is_verified = 1
              AND (verification_seq IS NOT NULL OR assignment_seq IS NOT NULL)
              AND ((variant_id = ?) OR (variant_id IS NULL AND ? IS NULL))
            LIMIT 1
            """,
            (menu_item_id, variant_id, variant_id),
        )
        return cursor.fetchone() is not None

    def _insert_order_item_mapping(self, cursor, order_item_id: str, menu_item_id: str,
                                   variant_id: Optional[str], is_verified: int) -> None:
        cursor.execute("""
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (order_item_id) DO NOTHING
        """, (str(order_item_id), menu_item_id, variant_id, is_verified))

    def add(self, name: str, order_item_id: str, is_addon: bool = False, *,
            itemcode=None, restaurant_id=None) -> "ClusterMatch":
        """
        Add an order item to the cluster.

        Regular-item resolution priority: existing Petpooja itemid assignment,
        then active itemcode parent mapping (parse the name only for its
        variant), then the existing clean-name/new-parent/fuzzy-suggestion
        path. itemcode/restaurant_id are keyword-only so addon and legacy call
        sites are unaffected; addon payloads carry no itemcode.

        Returns: ClusterMatch(menu_item_id, order_item_id, variant_id, item_type,
                              match_method, match_confidence)
        """
        if not self.conn:
            logging.error("No database connection available.")
            return ClusterMatch(None, None, None, None, None, 0.0)

        if not name or not name.strip():
            return ClusterMatch(None, None, None, None, "unmatched", 0.0)

        if not order_item_id:
            # Normalize before hashing so punctuation/spacing variants of the same
            # addon (e.g. "Choco Chips", "choco-chips", "Choco  Chips") collapse to
            # one synthetic id instead of splitting into separate mapping rows.
            # generate_deterministic_id already lowercases, but not this.
            normalized_name = re.sub(r'[^a-z0-9]+', ' ', name.lower()).strip()
            order_item_id = generate_deterministic_id(f"generated_{normalized_name}")

        # Itemcode routing applies only to regular items whose payload carries
        # both parent-key inputs; addons never produce itemcode mappings.
        use_itemcode = (
            not is_addon
            and restaurant_id is not None
            and itemcode is not None
        )

        cursor = self.conn.cursor()
        try:
            global_capability = resolve_global_menu_capability(self.conn)
            if global_capability.resolution_ready:
                global_match = resolve_global_identity_for_ingest(
                    self.conn,
                    order_item_id=order_item_id,
                    raw_name=name,
                    itemcode=itemcode,
                    is_addon=is_addon,
                )
                if global_match.resolved and global_match.local_menu_item_id:
                    variant_id = global_match.local_variant_id
                    if variant_id is None:
                        clean_result = clean_order_item_name(name)
                        variant_name = clean_result.get('variant', 'UNKNOWN') or 'UNKNOWN'
                        variant_id = self._ensure_variant(cursor, variant_name)
                    self._insert_order_item_mapping(
                        cursor,
                        str(order_item_id),
                        global_match.local_menu_item_id,
                        variant_id,
                        1,
                    )
                    ensure_menu_item_has_variant_mapping(
                        self.conn, global_match.local_menu_item_id, cursor=cursor
                    )
                    record_core_and_flag(
                        self.conn,
                        order_item_id=str(order_item_id),
                        name_raw=name,
                        is_addon=is_addon,
                        mapped_menu_item_id=global_match.local_menu_item_id,
                        mapped_name=global_match.canonical_name or name,
                        is_verified=True,
                        cursor=cursor,
                    )
                    return ClusterMatch(
                        global_match.local_menu_item_id,
                        str(order_item_id),
                        str(variant_id),
                        global_match.canonical_type or "UNKNOWN",
                        global_match.provenance,
                        100.0,
                    )

            # 1. Check if mapping already exists
            cursor.execute("""
                SELECT m.menu_item_id, m.variant_id, mi.type, mi.name, m.is_verified
                FROM menu_item_variants m
                JOIN menu_items mi ON m.menu_item_id = mi.menu_item_id
                WHERE m.order_item_id = ?
            """, (str(order_item_id),))


            existing = cursor.fetchone()

            if existing:
                menu_item_id, variant_id, type_text, mapped_name, mapped_verified = existing
                # Silent-reuse guard: this id already has a mapping and we are about
                # to inherit it. If the incoming name resolves to a different product
                # core than the id has carried before (and the mapping is verified so
                # it never re-enters the resolutions queue), surface it for a human.
                # Bookkeeping only — never rewrites the mapping, never commits here.
                record_core_and_flag(
                    self.conn,
                    order_item_id=str(order_item_id),
                    name_raw=name,
                    is_addon=is_addon,
                    mapped_menu_item_id=menu_item_id,
                    mapped_name=mapped_name,
                    is_verified=bool(mapped_verified),
                    cursor=cursor,
                )
                # Teach the itemcode projection even on an existing-itemid hit:
                # this is how a bad/manual split is detected (the code goes to
                # 'conflict') without letting itemcode override the assignment.
                if use_itemcode and not global_capability.resolution_ready:
                    observe_itemcode_assignment(
                        self.conn, restaurant_id, itemcode, str(menu_item_id),
                        cursor=cursor,
                    )
                return ClusterMatch(
                    str(menu_item_id), str(order_item_id), str(variant_id),
                    type_text, "existing-id-hit", 100.0,
                )

            # 2. NEW ITEM
            clean_result = clean_order_item_name(name)
            clean_name = clean_result['name']
            variant_name = clean_result.get('variant', 'UNKNOWN') or 'UNKNOWN'
            item_type = clean_result.get('type', 'UNKNOWN') or 'UNKNOWN'

            # 3. Active itemcode parent mapping: an unseen Petpooja itemid whose
            # product code already owns a route-eligible (server-backed) parent
            # joins that parent directly; the name parse above only determines
            # its variant. Conflicted, unknown, or not-yet-route-eligible codes
            # (get_active_itemcode_mapping returns None) fall through to the
            # normal parser path — see the Phase 9.2 gate in itemcode_mapping.py.
            if use_itemcode and not global_capability.resolution_ready:
                mapped_parent = get_active_itemcode_mapping(
                    self.conn, restaurant_id, itemcode, cursor=cursor
                )
                if mapped_parent:
                    cursor.execute(
                        "SELECT name, type FROM menu_items WHERE menu_item_id = ?",
                        (mapped_parent,),
                    )
                    parent_row = cursor.fetchone()
                    if parent_row:
                        parent_name, parent_type = parent_row[0], parent_row[1]
                        variant_id = self._ensure_variant(cursor, variant_name)
                        mapping_verified = 1 if self._mapping_inherits_verification(
                            cursor, mapped_parent, variant_id
                        ) else 0
                        self._insert_order_item_mapping(
                            cursor, str(order_item_id), mapped_parent,
                            variant_id, mapping_verified,
                        )
                        if mapping_verified == 1:
                            logging.info(
                                "verified-inherit: id=%s -> menu_item=%s variant=%s clean_name=%r raw=%r "
                                "(mapped verified without review via existing verified pair)",
                                str(order_item_id), mapped_parent, variant_id, clean_name, name,
                            )
                        ensure_menu_item_has_variant_mapping(
                            self.conn, mapped_parent, cursor=cursor
                        )
                        record_core_and_flag(
                            self.conn,
                            order_item_id=str(order_item_id),
                            name_raw=name,
                            is_addon=is_addon,
                            mapped_menu_item_id=mapped_parent,
                            mapped_name=parent_name,
                            is_verified=bool(mapping_verified),
                            cursor=cursor,
                        )
                        observe_itemcode_assignment(
                            self.conn, restaurant_id, itemcode, mapped_parent,
                            cursor=cursor,
                        )
                        return ClusterMatch(
                            str(mapped_parent), str(order_item_id), str(variant_id),
                            parent_type, "itemcode-hit", 100.0,
                        )
                    # Mapped parent row is missing (FK cascade should prevent
                    # this); fall through to the normal parser path.

            # Check if this cleaned name + type already exists in the DB
            cursor.execute("""
                SELECT menu_item_id FROM menu_items
                WHERE name = ? AND type = ?
            """, (clean_name, item_type))

            existing_item = cursor.fetchone()

            if existing_item:
                menu_item_id = str(existing_item[0])
            else:
                menu_item_id = generate_deterministic_id(clean_name, item_type)

            # Predict potential match (if we didn't find an exact name match) and
            # record how this item resolved so the provenance/score is real telemetry.
            suggested_id = None
            if existing_item:
                match_method = "name-hit"
                match_confidence = 100.0
            else:
                prediction = self.predict_menu_item_name(clean_name, item_type)
                if prediction:
                    suggested_id = prediction[0]
                    match_method = "fuzzy-suggested"
                    match_confidence = round(prediction[2] * 100.0, 1)
                else:
                    match_method = "new"
                    match_confidence = 0.0

            # CREATE MENU ITEM (with suggestion if found)
            cursor.execute("""
                INSERT INTO menu_items (menu_item_id, name, type, is_verified, suggestion_id)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT (menu_item_id) DO UPDATE SET
                    suggestion_id = COALESCE(menu_items.suggestion_id, excluded.suggestion_id)
            """, (menu_item_id, clean_name, item_type, suggested_id))

            # CREATE VARIANT
            variant_id = self._ensure_variant(cursor, variant_name)

            # CREATE MAPPING — inherit verified status if this menu_item_id + variant_id was verified elsewhere (C1 heuristic).
            mapping_verified = 1 if self._mapping_inherits_verification(
                cursor, menu_item_id, variant_id
            ) else 0

            self._insert_order_item_mapping(
                cursor, str(order_item_id), menu_item_id, variant_id, mapping_verified
            )

            # C1 audit: a brand-new id born verified=1 skips the resolutions
            # queue entirely, so a cleaner misparse that lands on an existing
            # verified (menu_item, variant) pair never surfaces for review. We
            # don't flag it as an anomaly — the mapped item name IS clean_name by
            # construction, so a raw-vs-item similarity check would fire on every
            # normal variant/qualifier strip (pure noise). Log instead, giving an
            # audit trail to grep when a silent mis-book is suspected.
            if mapping_verified == 1:
                logging.info(
                    "verified-inherit: id=%s -> menu_item=%s variant=%s clean_name=%r raw=%r "
                    "(mapped verified without review via existing verified pair)",
                    str(order_item_id), menu_item_id, variant_id, clean_name, name,
                )

            ensure_menu_item_has_variant_mapping(self.conn, menu_item_id, cursor=cursor)

            # Seed this id's baseline product core. cores is empty for a brand-new
            # id, so this records the founding identity without flagging anything;
            # a later order whose core differs is what surfaces as an anomaly.
            record_core_and_flag(
                self.conn,
                order_item_id=str(order_item_id),
                name_raw=name,
                is_addon=is_addon,
                mapped_menu_item_id=menu_item_id,
                mapped_name=clean_name,
                is_verified=bool(mapping_verified),
                cursor=cursor,
            )

            # Teach the itemcode projection from the parent the fallback chose.
            # First unambiguous observation activates the code; a contradictory
            # one puts it into conflict (never auto-resolved during ingest).
            if use_itemcode and not global_capability.resolution_ready:
                observe_itemcode_assignment(
                    self.conn, restaurant_id, itemcode, str(menu_item_id),
                    cursor=cursor,
                )

            # Transaction owned by the caller (process_order commits once per
            # order). Do NOT commit here: committing mid-order breaks per-order
            # atomicity — a later item/addon failure cannot roll back rows an
            # earlier add() already committed, so the order stays partially
            # loaded while the incremental watermark (MAX(stream_id)) still
            # advances past it, silently undercounting. Deferred-verification
            # flush is likewise deferred to the caller, after the order commits.

            return ClusterMatch(
                str(menu_item_id), str(order_item_id), str(variant_id),
                item_type, match_method, match_confidence,
            )

        except Exception as e:
            self.conn.rollback()
            logging.error(f"Error adding item {name} ({order_item_id}): {e}")
            raise e
        finally:
            cursor.close()
