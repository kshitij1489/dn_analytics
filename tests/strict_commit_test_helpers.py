"""
Shared fakes for exercising the strict-mode commit path in tests without a real
server. Production code always takes this path now (see utils/menu_utils.py,
src/core/queries/customer_merge_queries.py) — these helpers stand in for the
server's "accept" response and drive the same apply_accepted() pull-appliers a
real commit uses, so self-apply parity holds in tests too.
"""

import itertools
from typing import Any, Callable, Dict, List, Optional


def make_fake_menu_commit(
    *,
    seq_fn: Optional[Callable[[Dict[str, Any]], int]] = None,
    revision_start: int = 1,
    captured_events: Optional[List[Dict[str, Any]]] = None,
):
    """
    Build a side_effect for patching src.core.menu_mutation_commit.commit_mutation.

    seq_fn(event) -> int assigns a server_seq to each event (plan.event and each of
    plan.verification_events); defaults to a private incrementing counter. Pass a
    FakeEventServer's .ingest method to keep seq assignment in sync with a
    multi-install pull simulation. If captured_events is given, the final
    plan.event (with server_seq stamped) is appended to it after each commit.
    """
    from src.core.menu_mutation_commit import CommitResult, apply_accepted

    own_counter = itertools.count(1)
    resolved_seq_fn = seq_fn or (lambda _event: next(own_counter))
    revision_counter = itertools.count(revision_start)

    def _commit(conn, plan):
        accepted_events = []
        stamped_event = None
        if plan.event:
            seq = resolved_seq_fn(plan.event)
            accepted_events.append(
                {
                    "remote_event_id": plan.event["remote_event_id"],
                    "server_seq": seq,
                    "server_ingested_at": f"2026-07-05T00:00:{seq % 60:02d}+00:00",
                }
            )
            stamped_event = dict(plan.event, server_seq=seq)
        for event in plan.verification_events:
            vseq = resolved_seq_fn(event)
            accepted_events.append(
                {
                    "remote_event_id": event["remote_event_id"],
                    "server_seq": vseq,
                    "server_ingested_at": f"2026-07-05T00:01:{vseq % 60:02d}+00:00",
                }
            )

        body = {
            "status": "accepted",
            "mutation_id": plan.mutation_id,
            "menu_revision": next(revision_counter),
            "accepted_events": accepted_events,
            "assignment_rows": [],
            "catalog_delta": plan.catalog_delta,
            "merge_cursor": "0",
            "verification_cursor": "0",
        }
        apply_result = apply_accepted(conn, body, plan)
        if captured_events is not None and stamped_event is not None:
            captured_events.append(stamped_event)
        return CommitResult(status="ok", message="accepted", merge_id=apply_result.get("local_merge_id"))

    return _commit


def make_capturing_menu_commit(captured: Dict[str, Any]):
    """
    Build a side_effect for patching src.core.menu_mutation_commit.commit_mutation
    that records the plan's built event payloads into `captured` and self-applies.

    After each commit, captured["merge_event"] holds the seq-stamped plan.event and
    captured["verification_events"] holds the plan's verification event payloads —
    the strict path builds these into the plan (not the legacy outbox tables), so a
    test that used to read menu_merge_sync_events / menu_mapping_verification_sync_events
    reads them from here instead. Server seqs increment across commits (merge from 41,
    verification from 100) so a follow-up mutation (e.g. an undo) applies over the
    prior one instead of being rejected as stale.
    """
    from src.core.menu_mutation_commit import CommitResult, apply_accepted

    merge_seq = itertools.count(41)
    verification_seq = itertools.count(100)
    revision_counter = itertools.count(2)

    def _commit(conn, plan):
        accepted_events = []
        stamped_seq = None
        if plan.event:
            stamped_seq = next(merge_seq)
            accepted_events.append(
                {
                    "remote_event_id": plan.event["remote_event_id"],
                    "server_seq": stamped_seq,
                    "server_ingested_at": f"2026-07-05T00:00:{stamped_seq % 60:02d}+00:00",
                }
            )
        for event in plan.verification_events:
            vseq = next(verification_seq)
            accepted_events.append(
                {
                    "remote_event_id": event["remote_event_id"],
                    "server_seq": vseq,
                    "server_ingested_at": f"2026-07-05T00:01:{vseq % 60:02d}+00:00",
                }
            )

        body = {
            "status": "accepted",
            "mutation_id": plan.mutation_id,
            "menu_revision": next(revision_counter),
            "accepted_events": accepted_events,
            "assignment_rows": [],
            "catalog_delta": plan.catalog_delta,
            "merge_cursor": "0",
            "verification_cursor": "0",
        }
        apply_result = apply_accepted(conn, body, plan)
        if plan.event and stamped_seq is not None:
            captured["merge_event"] = dict(plan.event, server_seq=stamped_seq)
        captured["verification_events"] = [dict(event) for event in plan.verification_events]
        return CommitResult(status="ok", message="accepted", merge_id=apply_result.get("local_merge_id"))

    return _commit
