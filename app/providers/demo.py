"""A provider that reads a folder of neutral-shape files: the offline demo, and the contract's second implementation.

Point OPENSTRIDE_DEMO_PROVIDER at another folder to use your own export in the same shape.
Calendar events, wellness notes and hand-logged sessions are kept in the state folder, as a real provider keeps them on its side.
"""
import json
import os
import uuid
from datetime import date
from pathlib import Path

from app import workspace
from app.providers import base
from app.providers.base import Capabilities, ProviderError

FIXTURES = Path(__file__).parent.parent.parent / "examples" / "demo" / "provider"

GUIDE = """### The demo provider

The record is a fixed sample: three and a half weeks of a half-marathon plan. `load` is a heart-rate based
training load, `fitness`, `fatigue` and `form` are its 42-day and 7-day averages and their difference.
`intervals` are the watch's laps. Nothing is sent to a watch."""


class Demo:
    name = "demo"
    capabilities = Capabilities(calendar=True, upload=False, wellness_write=True, manual=True, gear=True)
    guide = GUIDE

    def __init__(self, folder: Path | None = None, store: Path | None = None):
        self.folder = Path(folder or os.environ.get("OPENSTRIDE_DEMO_PROVIDER") or FIXTURES)
        self._store = store

    @property
    def store(self) -> Path:
        return self._store or workspace.state() / "demo_provider.json"

    def read(self, name: str, default):
        path = self.folder / name
        return json.loads(path.read_text()) if path.exists() else default

    def saved(self) -> dict:
        return json.loads(self.store.read_text()) if self.store.exists() else {"events": {}, "wellness": {}, "manual": []}

    def save(self, data: dict) -> None:
        workspace.write_file(self.store, json.dumps(data, indent=1))

    def check(self) -> dict:
        return {"athlete": self.athlete()["name"], "id": "demo"}

    def activities(self, oldest: date, newest: date) -> list[dict]:
        rows = [*self.read("activities.json", []), *self.saved()["manual"]]
        return sorted((r for r in rows if oldest.isoformat() <= r["date"] <= newest.isoformat()), key=lambda r: r["start_local"])

    def intervals(self, activity_id: str) -> list[dict]:
        return next((r["intervals"] for r in self.read("activities.json", []) if r["id"] == activity_id), [])

    def streams(self, activity_id: str) -> dict:
        return self.read(f"streams/{activity_id}.json", {"activity_id": activity_id, "provider": self.name, "columns": {}})

    def wellness(self, oldest: date, newest: date) -> list[dict]:
        notes = self.saved()["wellness"]
        rows = [r | notes.get(r["date"], {}) for r in self.read("wellness.json", [])]
        return [r for r in rows if oldest.isoformat() <= r["date"] <= newest.isoformat()]

    def athlete(self) -> dict:
        return self.read("athlete.json", base.profile(provider=self.name, name="Demo"))

    def events(self, oldest: date, newest: date) -> list[dict]:
        events = self.saved()["events"].values()
        return [e for e in events if oldest.isoformat() <= e["date"] <= newest.isoformat()]

    def put_event(self, session: dict, context: dict, event_id: str | None = None) -> dict:
        data = self.saved()
        event_id = event_id if event_id in data["events"] else uuid.uuid4().hex[:8]
        data["events"][event_id] = {"id": event_id, "date": str(session["date"]), "name": session["title"], "external_id": f"openstride-{session['id']}"}
        self.save(data)
        return {"id": event_id, "structured": session["modality"] == "run", "notes": []}

    def delete_event(self, event_id: str) -> None:
        data = self.saved()
        data["events"].pop(event_id, None)
        self.save(data)

    def upload(self, filename: str, content: bytes) -> list[str]:
        raise base.Unsupported("The demo provider can't take files.")

    def put_wellness(self, day: date, fields: dict) -> None:
        unknown = sorted(set(fields) - set(base.WELLNESS_FIELDS))
        if unknown:
            raise ProviderError(f"Wellness has no field for {', '.join(unknown)}.")
        data = self.saved()
        data["wellness"].setdefault(day.isoformat(), {}).update(fields)
        self.save(data)

    def log_manual(self, entry: dict) -> str:
        data = self.saved()
        ident = f"dm{uuid.uuid4().hex[:8]}"
        data["manual"].append(base.activity(
            id=ident, provider=self.name, date=str(entry["date"]), start_local=f"{entry['date']}T{entry.get('time') or '12:00'}:00",
            sport=entry["sport"], name=entry.get("title") or entry["sport"].capitalize(), distance_km=entry.get("distance_km") or 0.0,
            moving_time_s=round(entry["minutes"] * 60), elapsed_time_s=round(entry["minutes"] * 60), rpe=entry.get("rpe"),
            load=round(entry["minutes"] * 0.5), source={"manual": True, "note": entry.get("note") or ""},
        ))
        self.save(data)
        return ident

    def gear(self) -> list[dict]:
        return self.read("gear.json", [])
