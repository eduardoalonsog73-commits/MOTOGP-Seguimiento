#!/usr/bin/env python3
"""Fetch official MotoGP results and publish a compact JSON snapshot for GitHub Pages."""
from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "motogp-live.json"
BASE = "https://api.motogp.pulselive.com/motogp/v1"
NOW = datetime.now(timezone.utc)

session = requests.Session()
retry = Retry(total=3, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
session.mount("https://", HTTPAdapter(max_retries=retry))
session.headers.update({
    "accept": "application/json",
    "x-origin": "motogp.com",
    "origin": "https://www.motogp.com",
    "referer": "https://www.motogp.com/",
    "user-agent": "Mozilla/5.0 MotoGP-PWA-DataUpdater/1.0",
})


def get_json(path: str) -> Any:
    response = session.get(BASE + path, timeout=30)
    response.raise_for_status()
    return response.json()


def as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def points_for(row: dict, session_type: str) -> float:
    value = row.get("points")
    try:
        if value is not None:
            return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        pos = int(row.get("position") or 0)
    except (TypeError, ValueError):
        pos = 0
    if session_type == "SPR":
        return float(([12, 9, 7, 6, 5, 4, 3, 2, 1][pos - 1]) if 1 <= pos <= 9 else 0)
    return float(([25, 20, 16, 13, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1][pos - 1]) if 1 <= pos <= 15 else 0)


def load_previous() -> dict:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> None:
    previous = load_previous()
    old_events = {str(x.get("id")): x for x in as_list(previous.get("events")) if x.get("id")}

    seasons = as_list(get_json("/results/seasons"))
    year = int(os.environ.get("MOTOGP_SEASON", NOW.year))
    season = next((x for x in seasons if int(x.get("year") or 0) == year), None)
    if season is None:
        season = next((x for x in seasons if x.get("current")), None)
    if not season:
        raise RuntimeError("No se ha encontrado la temporada actual de MotoGP")
    year = int(season.get("year") or year)

    categories = as_list(get_json(f"/results/categories?seasonUuid={season['id']}"))
    category = next((x for x in categories if "motogp" in str(x.get("name", "")).lower()), None)
    if not category:
        raise RuntimeError("No se ha encontrado la categoría MotoGP")

    standings_payload = get_json(f"/results/standings?seasonUuid={season['id']}&categoryUuid={category['id']}")
    standings_rows = as_list(standings_payload.get("classification") if isinstance(standings_payload, dict) else None)
    if not standings_rows:
        raise RuntimeError("La API no ha devuelto la clasificación de pilotos")
    standings = []
    for row in standings_rows:
        rider = row.get("rider") or {}
        team = row.get("team") or {}
        standings.append({
            "position": int(row.get("position") or len(standings) + 1),
            "id": str(rider.get("id") or ""),
            "name": rider.get("full_name") or rider.get("name") or "Piloto",
            "team": team.get("name") or "",
            "points": float(row.get("points") or 0),
        })
    standings.sort(key=lambda x: x["position"])

    events = as_list(get_json(f"/results/events?seasonUuid={season['id']}"))
    output_events = []
    recent_cutoff = NOW - timedelta(days=7)
    for event in sorted(events, key=lambda x: str(x.get("date_start") or "")):
        event_id = str(event.get("id") or event.get("uuid") or "")
        if not event_id:
            continue
        end_date = parse_date(event.get("date_end")) or parse_date(event.get("date_start"))
        previous_event = old_events.get(event_id, {})
        previous_sessions = previous_event.get("sessions", {}) if isinstance(previous_event.get("sessions"), dict) else {}
        # Historical weekends are fetched once. The current/recent weekend is revisited
        # so that Sprint and Grand Prix classifications can arrive at different times.
        should_check = event_id not in old_events or end_date is None or end_date >= recent_cutoff
        sessions_saved = dict(previous_sessions)
        if should_check:
            event_sessions = as_list(get_json(f"/results/sessions?eventUuid={event_id}&categoryUuid={category['id']}"))
            for race_session in event_sessions:
                kind = str(race_session.get("type") or "")
                if kind not in ("SPR", "RAC"):
                    continue
                session_id = str(race_session.get("id") or "")
                if not session_id or session_id in sessions_saved:
                    continue
                session_date = parse_date(race_session.get("date"))
                if session_date and session_date > NOW + timedelta(hours=2):
                    continue
                try:
                    payload = get_json(f"/results/session/{session_id}/classification?seasonYear={year}&test=false")
                except requests.RequestException:
                    continue
                classification = as_list(payload.get("classification") if isinstance(payload, dict) else None)
                if not classification:
                    continue
                points_map: dict[str, float] = {}
                positions_map: dict[str, int] = {}
                for result in classification:
                    rider = result.get("rider") or {}
                    rider_id = str(rider.get("id") or "")
                    if not rider_id:
                        continue
                    pts = points_for(result, kind)
                    if pts:
                        points_map[rider_id] = pts
                    try:
                        position = int(result.get("position") or 0)
                    except (TypeError, ValueError):
                        position = 0
                    if kind == "RAC" and 1 <= position <= 3:
                        positions_map[rider_id] = position
                sessions_saved[session_id] = {
                    "type": kind,
                    "points": points_map,
                    "podiums": positions_map,
                    "date": race_session.get("date"),
                }
                time.sleep(0.15)

        total_points: dict[str, float] = {}
        podiums: dict[str, int] = {}
        for saved in sessions_saved.values():
            for rider_id, points in (saved.get("points") or {}).items():
                total_points[rider_id] = total_points.get(rider_id, 0.0) + float(points or 0)
            for rider_id, position in (saved.get("podiums") or {}).items():
                podiums[rider_id] = int(position)
        if not sessions_saved:
            continue
        event_name = event.get("sponsored_name") or event.get("name") or (event.get("circuit") or {}).get("name") or "Gran Premio"
        short = event.get("short_name") or event_name
        output_events.append({
            "id": event_id,
            "name": event_name,
            "short": str(short).replace("Grand Prix", "").replace("Gran Premio", "").strip()[:9].upper(),
            "date_start": event.get("date_start"),
            "points": total_points,
            "podiums": podiums,
            "sessions": sessions_saved,
        })

    output = {
        "season": year,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "MotoGP Pulselive Results API",
        "standings": standings,
        "events": output_events,
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Actualizados {len(standings)} pilotos y {len(output_events)} Grandes Premios para {year}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR al actualizar datos MotoGP: {exc}", file=sys.stderr)
        raise
