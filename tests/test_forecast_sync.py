import json
import sqlite3
import unittest
from typing import Callable, Optional
from unittest import mock

from src.core.central_forecast_cache import (
    ensure_central_forecast_tables,
    get_forecast_sync_cursor,
    set_forecast_sync_cursor,
)
from src.core.central_forecast_cache import FAMILY_ITEMS, FAMILY_VOLUME, ITEM_FORWARD_DAYS, VOLUME_FORWARD_DAYS
from src.core.central_forecast_projection import (
    _merge_backtest_and_forecast,
    _normalize_volume_unit,
    build_awaiting_action_response,
    build_item_forecast_response,
    build_revenue_forecast_response,
    build_volume_forecast_response,
    get_served_revenue_run,
)
from src.core.forecast_sync import apply_forecast_page, pull_and_apply_forecast_deltas


class ForecastSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        ensure_central_forecast_tables(self.conn)

    def test_apply_page_idempotent_on_server_seq(self) -> None:
        page = {
            "scope_key": "default",
            "runs": [
                {
                    "server_seq": 100,
                    "run_id": "run-1",
                    "generated_on": "2026-07-08",
                    "status": "success",
                    "completed_at": "2026-07-09T00:00:00Z",
                    "families": ["revenue"],
                }
            ],
            "rows": [
                {
                    "server_seq": 101,
                    "run_id": "run-1",
                    "family": "revenue",
                    "kind": "forward",
                    "forecast_date": "2026-07-08",
                    "model_name": "gp",
                    "payload": {"revenue": 1000.0, "orders": 10, "gp_lower": 800, "gp_upper": 1200},
                }
            ],
            "weather_rows": [
                {
                    "server_seq": 102,
                    "weather_date": "2026-07-08",
                    "payload": {"temp_max": 35.0, "rain_sum": 0.0},
                }
            ],
        }
        stats1 = apply_forecast_page(self.conn, page)
        stats2 = apply_forecast_page(self.conn, page)
        self.assertEqual(stats1["rows_inserted"], 1)
        self.assertEqual(stats2["rows_inserted"], 0)
        count = self.conn.execute("SELECT COUNT(*) FROM central_forecast_rows").fetchone()[0]
        self.assertEqual(count, 1)

    def test_cursor_round_trip(self) -> None:
        self.assertIsNone(get_forecast_sync_cursor(self.conn))
        set_forecast_sync_cursor(self.conn, "500")
        self.assertEqual(get_forecast_sync_cursor(self.conn), "500")


def _page(*, next_cursor: str, has_more: bool, seq: int) -> dict:
    return {
        "scope_key": "default",
        "runs": [
            {
                "server_seq": seq,
                "run_id": f"run-{seq}",
                "generated_on": "2026-07-08",
                "status": "success",
                "completed_at": "2026-07-09T00:00:00Z",
                "families": ["revenue"],
            }
        ],
        "rows": [],
        "weather_rows": [],
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


def _row_page(*, next_cursor: str, run_id: str, seq: int, include_run: bool) -> dict:
    page = {
        "scope_key": "default",
        "runs": [],
        "rows": [
            {
                "server_seq": seq,
                "run_id": run_id,
                "family": "revenue",
                "kind": "forward",
                "forecast_date": "2026-07-08",
                "model_name": "gp",
                "payload": {"revenue": 1000.0, "orders": 10},
            }
        ],
        "weather_rows": [],
        "has_more": False,
        "next_cursor": next_cursor,
    }
    if include_run:
        page["runs"] = [
            {
                "server_seq": seq - 1,
                "run_id": run_id,
                "generated_on": "2026-07-08",
                "status": "success",
                "completed_at": "2026-07-09T00:00:00Z",
                "families": ["revenue"],
            }
        ]
    return page


class PullAndApplyForecastDeltasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        ensure_central_forecast_tables(self.conn)

    def test_bootstrap_used_when_cursor_is_null(self) -> None:
        self.assertIsNone(get_forecast_sync_cursor(self.conn))
        with mock.patch(
            "src.core.forecast_sync.get_forecast_bootstrap_endpoint",
            return_value="https://cloud.example/bootstrap",
        ), mock.patch(
            "src.core.forecast_sync.get_forecast_delta_endpoint",
            return_value="https://cloud.example/delta",
        ), mock.patch(
            "src.core.forecast_sync._fetch_page",
            return_value=_page(next_cursor="10", has_more=False, seq=1),
        ) as fetch_page:
            summary = pull_and_apply_forecast_deltas(self.conn)
        self.assertEqual(summary["mode"], "bootstrap")
        self.assertEqual(fetch_page.call_args.args[0], "https://cloud.example/bootstrap")

    def test_pagination_loops_until_has_more_false(self) -> None:
        set_forecast_sync_cursor(self.conn, "5")
        self.conn.commit()
        pages = [
            _page(next_cursor="6", has_more=True, seq=1),
            _page(next_cursor="7", has_more=False, seq=2),
        ]
        with mock.patch(
            "src.core.forecast_sync.get_forecast_bootstrap_endpoint",
            return_value="https://cloud.example/bootstrap",
        ), mock.patch(
            "src.core.forecast_sync.get_forecast_delta_endpoint",
            return_value="https://cloud.example/delta",
        ), mock.patch(
            "src.core.forecast_sync._fetch_page", side_effect=pages
        ) as fetch_page:
            summary = pull_and_apply_forecast_deltas(self.conn)
        self.assertEqual(fetch_page.call_count, 2)
        self.assertEqual(summary["pages"], 2)
        self.assertEqual(summary["next_cursor"], "7")
        self.assertEqual(get_forecast_sync_cursor(self.conn), "7")

    def test_cursor_not_advanced_on_transaction_rollback(self) -> None:
        self.assertIsNone(get_forecast_sync_cursor(self.conn))
        with mock.patch(
            "src.core.forecast_sync.get_forecast_bootstrap_endpoint",
            return_value="https://cloud.example/bootstrap",
        ), mock.patch(
            "src.core.forecast_sync.get_forecast_delta_endpoint",
            return_value="https://cloud.example/delta",
        ), mock.patch(
            "src.core.forecast_sync._fetch_page",
            return_value=_page(next_cursor="99", has_more=False, seq=1),
        ), mock.patch(
            "src.core.forecast_sync.apply_forecast_page",
            side_effect=RuntimeError("boom"),
        ):
            summary = pull_and_apply_forecast_deltas(self.conn)
        self.assertEqual(summary["status"], "error")
        self.assertIsNone(get_forecast_sync_cursor(self.conn))
        count = self.conn.execute("SELECT COUNT(*) FROM central_forecast_runs").fetchone()[0]
        self.assertEqual(count, 0)

    def test_missing_parent_run_recovers_once_from_bootstrap(self) -> None:
        set_forecast_sync_cursor(self.conn, "5")
        self.conn.commit()
        pages = [
            _row_page(next_cursor="7", run_id="run-new", seq=7, include_run=False),
            _row_page(next_cursor="7", run_id="run-new", seq=7, include_run=True),
        ]
        with mock.patch(
            "src.core.forecast_sync.get_forecast_bootstrap_endpoint",
            return_value="https://cloud.example/bootstrap",
        ), mock.patch(
            "src.core.forecast_sync.get_forecast_delta_endpoint",
            return_value="https://cloud.example/delta",
        ), mock.patch(
            "src.core.forecast_sync._fetch_page", side_effect=pages
        ) as fetch_page:
            summary = pull_and_apply_forecast_deltas(self.conn)

        self.assertEqual(fetch_page.call_count, 2)
        self.assertEqual(fetch_page.call_args_list[0].args[0], "https://cloud.example/delta")
        self.assertEqual(fetch_page.call_args_list[1].args[0], "https://cloud.example/bootstrap")
        self.assertEqual(summary["status"], "ok")
        self.assertTrue(summary["recovery"]["succeeded"])
        self.assertEqual(get_forecast_sync_cursor(self.conn), "7")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM central_forecast_rows").fetchone()[0],
            1,
        )

    def test_missing_parent_bootstrap_recovery_is_bounded(self) -> None:
        set_forecast_sync_cursor(self.conn, "5")
        self.conn.commit()
        orphan = _row_page(
            next_cursor="7", run_id="still-missing", seq=7, include_run=False
        )
        with mock.patch(
            "src.core.forecast_sync.get_forecast_bootstrap_endpoint",
            return_value="https://cloud.example/bootstrap",
        ), mock.patch(
            "src.core.forecast_sync.get_forecast_delta_endpoint",
            return_value="https://cloud.example/delta",
        ), mock.patch(
            "src.core.forecast_sync._fetch_page", side_effect=[orphan, orphan]
        ) as fetch_page:
            summary = pull_and_apply_forecast_deltas(self.conn)

        self.assertEqual(fetch_page.call_count, 2)
        self.assertEqual(summary["status"], "error")
        self.assertFalse(summary["recovery"]["succeeded"])
        self.assertEqual(get_forecast_sync_cursor(self.conn), "5")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM central_forecast_rows").fetchone()[0],
            0,
        )


class CentralForecastProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        ensure_central_forecast_tables(self.conn)
        self.run_id = "a45fc12b-ec65-4698-93c5-15682d17273e"
        self.conn.execute(
            """
            INSERT INTO central_forecast_runs (
                run_id, server_seq, scope_key, generated_on, status, completed_at, families
            ) VALUES (?, 1, 'default', '2026-07-08', 'success', '2026-07-09T00:00:00Z', ?)
            """,
            (self.run_id, json.dumps(["revenue"])),
        )
        models = ("weekday_avg", "holt_winters", "prophet", "gp")
        seq = 10
        for day_offset in range(7):
            forecast_date = f"2026-07-{8 + day_offset:02d}"
            for model in models:
                payload = {"revenue": 1000.0 + day_offset, "orders": 5}
                if model == "gp":
                    payload["gp_lower"] = 800.0
                    payload["gp_upper"] = 1200.0
                self.conn.execute(
                    """
                    INSERT INTO central_forecast_rows (
                        server_seq, run_id, scope_key, family, kind, forecast_date,
                        model_name, payload
                    ) VALUES (?, ?, 'default', 'revenue', 'forward', ?, ?, ?)
                    """,
                    (seq, self.run_id, forecast_date, model, json.dumps(payload)),
                )
                seq += 1
        self.conn.commit()

    def test_revenue_run_complete_and_served(self) -> None:
        run, using_fallback = get_served_revenue_run(
            self.conn, today_str="2026-07-08"
        )
        self.assertIsNotNone(run)
        self.assertEqual(run["run_id"], self.run_id)
        self.assertFalse(using_fallback)

    def test_build_revenue_response_shape(self) -> None:
        response = build_revenue_forecast_response(
            self.conn,
            history_rows=[],
            today_str="2026-07-08",
        )
        self.assertIsNotNone(response)
        assert response is not None
        self.assertIn("summary", response)
        self.assertIn("forecasts", response)
        self.assertEqual(len(response["forecasts"]["gp"]), 7)
        self.assertTrue(response["debug_info"]["served_from_central"])
        self.assertGreater(response["summary"]["projected_7d_revenue"], 0)

    def test_falls_back_to_previous_generated_on_when_today_incomplete(self) -> None:
        # Newer run (2026-07-09) only has 3/7 forward days per model -> incomplete.
        incomplete_run_id = "incomplete-run"
        self.conn.execute(
            """
            INSERT INTO central_forecast_runs (
                run_id, server_seq, scope_key, generated_on, status, completed_at, families
            ) VALUES (?, 2, 'default', '2026-07-09', 'success', '2026-07-09T12:00:00Z', ?)
            """,
            (incomplete_run_id, json.dumps(["revenue"])),
        )
        seq = 1000
        for day_offset in range(3):
            forecast_date = f"2026-07-{9 + day_offset:02d}"
            for model in ("weekday_avg", "holt_winters", "prophet", "gp"):
                self.conn.execute(
                    """
                    INSERT INTO central_forecast_rows (
                        server_seq, run_id, scope_key, family, kind, forecast_date,
                        model_name, payload
                    ) VALUES (?, ?, 'default', 'revenue', 'forward', ?, ?, ?)
                    """,
                    (seq, incomplete_run_id, forecast_date, model, json.dumps({"revenue": 1.0, "orders": 1})),
                )
                seq += 1
        self.conn.commit()

        run, using_fallback = get_served_revenue_run(self.conn, today_str="2026-07-09")
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run["run_id"], self.run_id)
        self.assertEqual(run["generated_on"], "2026-07-08")
        self.assertTrue(using_fallback)


class MergeBacktestAndForecastTests(unittest.TestCase):
    def test_past_from_backtest_future_from_forecast_sorted(self) -> None:
        backtest = {
            "gp": [
                {"date": "2026-07-06", "revenue": 100.0, "orders": 1},
                {"date": "2026-07-07", "revenue": 200.0, "orders": 2},
                {"date": "2026-07-08", "revenue": 999.0, "orders": 9},  # today: dropped in favor of forecast
            ]
        }
        forecast = {
            "gp": [
                {"date": "2026-07-08", "revenue": 300.0, "orders": 3},
                {"date": "2026-07-09", "revenue": 400.0, "orders": 4},
            ]
        }
        merged = _merge_backtest_and_forecast(backtest, forecast, today_str="2026-07-08")
        dates = [r["date"] for r in merged["gp"]]
        self.assertEqual(dates, ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09"])
        # Today's point comes from forecast (300.0), not backtest (999.0).
        today_point = next(r for r in merged["gp"] if r["date"] == "2026-07-08")
        self.assertEqual(today_point["revenue"], 300.0)
        # Backtest rows get default weather fields when missing.
        past_point = next(r for r in merged["gp"] if r["date"] == "2026-07-06")
        self.assertEqual(past_point["temp_max"], 0)
        self.assertEqual(past_point["rain_category"], "none")


class VolumeUnitNormalizationTests(unittest.TestCase):
    def test_normalize_volume_unit_never_returns_mg(self) -> None:
        self.assertEqual(_normalize_volume_unit("mg"), "g")
        self.assertEqual(_normalize_volume_unit("g"), "g")
        self.assertEqual(_normalize_volume_unit("units"), "units")
        self.assertEqual(_normalize_volume_unit("count"), "units")
        self.assertEqual(_normalize_volume_unit(None), "g")
        self.assertEqual(_normalize_volume_unit(""), "g")


class AwaitingActionResponseTests(unittest.TestCase):
    def test_awaiting_action_when_no_runs_cached(self) -> None:
        conn = sqlite3.connect(":memory:")
        ensure_central_forecast_tables(conn)
        response = build_awaiting_action_response(
            conn, family="revenue", default_message="No forecasts yet."
        )
        self.assertTrue(response["debug_info"]["awaiting_action"])
        self.assertEqual(response["forecasts"]["gp"], [])
        self.assertEqual(response["summary"]["projected_7d_revenue"], 0)


def _seed_entity_forward_rows(
    conn,
    *,
    run_id: str,
    family: str,
    entity_id: str,
    entity_name: str,
    start_date: str,
    days: int,
    seq_start: int,
    payload_builder: Callable[[int], dict],
    unit: Optional[str] = None,
) -> int:
    seq = seq_start
    year, month, day = (int(part) for part in start_date.split("-"))
    for offset in range(days):
        forecast_date = f"{year:04d}-{month:02d}-{day + offset:02d}"
        payload = payload_builder(offset)
        conn.execute(
            """
            INSERT INTO central_forecast_rows (
                server_seq, run_id, scope_key, family, kind, forecast_date,
                entity_id, entity_name, unit, payload
            ) VALUES (?, ?, 'default', ?, 'forward', ?, ?, ?, ?, ?)
            """,
            (
                seq,
                run_id,
                family,
                forecast_date,
                entity_id,
                entity_name,
                unit,
                json.dumps(payload),
            ),
        )
        seq += 1
    return seq


class ItemVolumeProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        ensure_central_forecast_tables(self.conn)
        self.run_id = "items-volume-run-1"
        self.conn.execute(
            """
            INSERT INTO central_forecast_runs (
                run_id, server_seq, scope_key, generated_on, status, completed_at, families
            ) VALUES (?, 1, 'default', '2026-07-08', 'success', '2026-07-09T00:00:00Z', ?)
            """,
            (self.run_id, json.dumps([FAMILY_ITEMS, FAMILY_VOLUME])),
        )
        seq = 10
        seq = _seed_entity_forward_rows(
            self.conn,
            run_id=self.run_id,
            family=FAMILY_ITEMS,
            entity_id="item-1",
            entity_name="Paneer Tikka",
            start_date="2026-07-08",
            days=ITEM_FORWARD_DAYS,
            seq_start=seq,
            payload_builder=lambda i: {
                "p50": 10.0 + i,
                "p90": 15.0 + i,
                "probability": 0.8,
                "recommended_prep": 12 + i,
            },
        )
        _seed_entity_forward_rows(
            self.conn,
            run_id=self.run_id,
            family=FAMILY_VOLUME,
            entity_id="item-1",
            entity_name="Paneer Tikka",
            start_date="2026-07-08",
            days=VOLUME_FORWARD_DAYS,
            seq_start=seq,
            unit="g",
            payload_builder=lambda i: {
                "p50": 100.0 + i,
                "p90": 150.0 + i,
                "probability": 0.75,
                "volume_value": 100.0 + i,
                "recommended_volume": 110.0 + i,
            },
        )
        self.conn.commit()

    def test_build_item_response_shape(self) -> None:
        response = build_item_forecast_response(
            self.conn,
            items_list=[{"item_id": "item-1", "item_name": "Paneer Tikka"}],
            history_rows=[],
            today_str="2026-07-08",
        )
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(len(response["forecast"]), ITEM_FORWARD_DAYS)
        self.assertEqual(response["forecast"][0]["item_id"], "item-1")
        self.assertTrue(response["debug_info"]["served_from_central"])

    def test_build_volume_response_shape(self) -> None:
        response = build_volume_forecast_response(
            self.conn,
            items_list=[{"item_id": "item-1", "item_name": "Paneer Tikka"}],
            history_rows=[],
            today_str="2026-07-08",
        )
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(len(response["forecast"]), VOLUME_FORWARD_DAYS)
        self.assertEqual(response["forecast"][0]["unit"], "g")
        self.assertTrue(response["debug_info"]["served_from_central"])

    def test_volume_response_never_serves_mg_unit(self) -> None:
        # Rows shipped with a raw "mg" unit (e.g. legacy central data); response
        # must normalize to "g", never leak "mg" to the chart layer.
        _seed_entity_forward_rows(
            self.conn,
            run_id=self.run_id,
            family=FAMILY_VOLUME,
            entity_id="item-2",
            entity_name="Spice Mix",
            start_date="2026-07-08",
            days=VOLUME_FORWARD_DAYS,
            seq_start=9000,
            unit="mg",
            payload_builder=lambda i: {
                "p50": 5.0 + i,
                "p90": 8.0 + i,
                "probability": 0.6,
                "volume_value": 5.0 + i,
                "recommended_volume": 6.0 + i,
            },
        )
        self.conn.commit()
        response = build_volume_forecast_response(
            self.conn,
            items_list=[{"item_id": "item-1", "item_name": "Paneer Tikka"}],
            history_rows=[],
            item_id="item-2",
            today_str="2026-07-08",
        )
        self.assertIsNotNone(response)
        assert response is not None
        units = {r["unit"] for r in response["forecast"]}
        self.assertNotIn("mg", units)
        self.assertEqual(units, {"g"})


if __name__ == "__main__":
    unittest.main()
