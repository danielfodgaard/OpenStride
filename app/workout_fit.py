"""A planned run as a FIT workout file, and as the JSON Garmin Connect takes.

Both come from one list of steps, so the watch sees the same workout either way. Stdlib only:
this file needs three message types, and a FIT file is headers, fixed records and a CRC.

Values are from the FIT SDK Profile (read from Garmin's garmin-fit-sdk 21.x `Profile`):
  file: workout=5. manufacturer: development=255. sport: running=1.
  wkt_step_duration: time=0, distance=1, open=5, repeat_until_steps_cmplt=6.
  wkt_step_target: speed=0, open=2. intensity: active=0, warmup=2, cooldown=3, recovery=4.
  mesg_num: file_id=0, workout=26, workout_step=27.
  duration_time scale 1000 s (so ms), duration_distance scale 100 m (so cm),
  custom_target_speed_low/high scale 1000 m/s (so mm/s).
Layout is from https://developer.garmin.com/fit/articles/file-types/workout.html : file_id,
then workout, then steps. A repeat step follows its block, with duration_value the
message_index to go back to, target_type open and target_value the repeat count.
A custom range needs target_value 0. num_valid_steps counts repeat steps too.
"""
import hashlib
import json
import random
import re
import struct
import time
from dataclasses import dataclass, field
from datetime import date

from app.models import DatedSession, Session, Step

NAME_LIMIT = 15  # what a watch shows of a workout name
SINGLE_PACE_BAND_S = 5  # static/render/format.js targetBand
FIT_EPOCH = 631065600  # 1989-12-31T00:00:00Z
PROFILE_VERSION = 2100
PROTOCOL_VERSION = 0x10  # 1.0: nothing here needs 2.0, and every device reads it

MANUFACTURER_DEVELOPMENT = 255
FILE_WORKOUT = 5
SPORT_RUNNING = 1
DURATION_TIME, DURATION_DISTANCE, DURATION_OPEN, DURATION_REPEAT = 0, 1, 5, 6
TARGET_SPEED, TARGET_OPEN = 0, 2
INTENSITY = {"active": 0, "warmup": 2, "cooldown": 3, "recovery": 4}

UINT8_ENUM, STRING, UINT16, UINT32, UINT32Z = 0x00, 0x07, 0x84, 0x86, 0x8C
# (field number, struct code, FIT base type), per message.
FILE_ID = [(0, "B", UINT8_ENUM), (1, "H", UINT16), (2, "H", UINT16), (3, "I", UINT32Z), (4, "I", UINT32)]
WORKOUT = [(4, "B", UINT8_ENUM), (6, "H", UINT16)]  # the name is added at its own length
WORKOUT_NAME = 8
WORKOUT_STEP = [
    (254, "H", UINT16),  # message_index
    (1, "B", UINT8_ENUM),  # duration_type
    (2, "I", UINT32),  # duration_value
    (3, "B", UINT8_ENUM),  # target_type
    (4, "I", UINT32),  # target_value
    (5, "I", UINT32),  # custom_target_value_low
    (6, "I", UINT32),  # custom_target_value_high
    (7, "B", UINT8_ENUM),  # intensity
]


@dataclass
class WatchStep:
    intensity: str
    metres: float = 0
    seconds: float = 0
    speed: tuple[float, float] | None = None  # m/s, slow to fast


@dataclass
class RepeatBlock:
    count: int
    steps: list[WatchStep]


@dataclass
class Workout:
    data: bytes
    notes: list[str] = field(default_factory=list)


def speed_band(pace: str) -> tuple[float, float] | None:
    """Mirrors targetBand in format.js: a range as written, one pace ±5 s/km. None if unreadable."""
    times = [int(minutes) * 60 + int(seconds) for minutes, seconds in re.findall(r"(\d+):(\d\d)", pace or "")]
    if not times:
        return None
    fastest, slowest = (min(times), max(times)) if len(times) > 1 else (times[0] - SINGLE_PACE_BAND_S, times[0] + SINGLE_PACE_BAND_S)
    return (1000 / slowest, 1000 / fastest) if fastest > 0 else None


def intensity_of(description: str) -> str:
    text = description.lower()
    if "warm" in text:
        return "warmup"
    if "cool" in text:
        return "cooldown"
    return "recovery" if re.search(r"recover|jog|\brest\b|float", text) else "active"


def watch_step(intensity: str, distance_km: float, duration_min: float, pace: str, notes: list[str], label: str) -> WatchStep:
    speed = speed_band(pace)
    if speed is None and pace.strip():
        notes.append(f"{label}: no pace target on the watch, could not read “{pace}”.")
    return WatchStep(intensity, distance_km * 1000, 0 if distance_km else duration_min * 60, speed)


def steps_of(session: Session) -> tuple[list[WatchStep | RepeatBlock], list[str]]:
    """Planned steps as a watch runs them. Pairing agrees with expandSteps in format.js."""
    notes: list[str] = []
    if not session.steps:
        return [watch_step("active", session.distance_km, session.duration_min, session.target_pace, notes, session.title)], notes

    def convert(step: Step) -> WatchStep:
        return watch_step(intensity_of(step.description), step.distance_km, step.duration_min, step.target_pace, notes, step.description or session.title)

    items: list[WatchStep | RepeatBlock] = []
    planned = session.steps
    i = 0
    while i < len(planned):
        step = planned[i]
        following = planned[i + 1] if i + 1 < len(planned) else None
        if step.repeat > 1 and following and following.repeat > 1 and abs(step.repeat - following.repeat) <= 1:
            # 4 reps and 3 jogs are 3 rounds of both and one more rep, not a fourth jog.
            rounds = min(step.repeat, following.repeat)
            items.append(RepeatBlock(rounds, [convert(step), convert(following)]))
            if step.repeat != following.repeat:
                items.append(convert(step if step.repeat > rounds else following))
            i += 2
            continue
        if step.repeat > 1:
            items.append(RepeatBlock(step.repeat, [convert(step)]))
        elif step.repeat == 1:
            items.append(convert(step))
        i += 1
    return items, notes


def workout_name(session: Session) -> str:
    name = re.sub(r"\s*×\s*", "x", session.title).strip()  # watch fonts don't all have ×
    return name[:NAME_LIMIT].strip() or "Run"


def crc16(data: bytes) -> int:
    """CRC-16/ARC, which is what the FIT protocol's nibble-table CRC computes."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def message(local: int, global_number: int, layout: list, rows: list[tuple]) -> bytes:
    """One definition, then a data record per row."""
    fields = [(number, struct.calcsize(f"<{code}"), base) for number, code, base in layout]
    head = struct.pack("<BBBHB", 0x40 | local, 0, 0, global_number, len(fields))
    head += b"".join(struct.pack("BBB", *item) for item in fields)
    row_format = "<B" + "".join(code for _, code, _ in layout)
    return head + b"".join(struct.pack(row_format, local, *row) for row in rows)


def step_row(index: int, step: WatchStep) -> tuple:
    if step.metres:
        duration = (DURATION_DISTANCE, round(step.metres * 100))
    elif step.seconds:
        duration = (DURATION_TIME, round(step.seconds * 1000))
    else:
        duration = (DURATION_OPEN, 0)
    target = (TARGET_SPEED, 0, *(round(speed * 1000) for speed in step.speed)) if step.speed else (TARGET_OPEN, 0, 0, 0)
    return (index, *duration, *target, INTENSITY[step.intensity])


def step_rows(items: list[WatchStep | RepeatBlock]) -> list[tuple]:
    rows: list[tuple] = []
    for item in items:
        if isinstance(item, WatchStep):
            rows.append(step_row(len(rows), item))
            continue
        first = len(rows)
        rows += [step_row(len(rows) + n, step) for n, step in enumerate(item.steps)]
        rows.append((len(rows), DURATION_REPEAT, first, TARGET_OPEN, item.count, 0, 0, INTENSITY["active"]))
    return rows


def encode(name: str, items: list[WatchStep | RepeatBlock], created: float) -> bytes:
    rows = step_rows(items)
    encoded_name = name.encode() + b"\0"
    serial = random.randint(1, 2**32 - 2)  # uint32z: 0 means invalid
    body = message(0, 0, FILE_ID, [(FILE_WORKOUT, MANUFACTURER_DEVELOPMENT, 0, serial, round(created) - FIT_EPOCH)])
    body += message(1, 26, [*WORKOUT, (WORKOUT_NAME, f"{len(encoded_name)}s", STRING)], [(SPORT_RUNNING, len(rows), encoded_name)])
    body += message(2, 27, WORKOUT_STEP, rows)
    header = struct.pack("<BBHI4s", 14, PROTOCOL_VERSION, PROFILE_VERSION, len(body), b".FIT")
    header += struct.pack("<H", crc16(header))
    return header + body + struct.pack("<H", crc16(header + body))


def build(session: Session) -> Workout:
    items, notes = steps_of(session)
    return Workout(encode(workout_name(session), items, time.time()), notes)


# Garmin Connect's JSON: ids as in garminconnect/workout.py, whose models test this shape.
RUNNING = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
STEP_TYPES = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "active": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
}
REPEAT_TYPE = {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6}
END_OPEN = {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
END_DISTANCE = {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
END_ITERATIONS = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}
NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
PACE_TARGET = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 1}


def estimated_seconds(step: WatchStep) -> float:
    if step.seconds or not step.metres or not step.speed:
        return step.seconds
    return step.metres / (sum(step.speed) / 2)


def garmin_json(session: Session) -> dict:
    items, _ = steps_of(session)
    order = 0

    def step_json(step: WatchStep) -> dict:
        nonlocal order
        order += 1
        end, value = (END_DISTANCE, step.metres) if step.metres else (END_TIME, step.seconds) if step.seconds else (END_OPEN, None)
        target = {"targetValueOne": round(step.speed[0], 3), "targetValueTwo": round(step.speed[1], 3)} if step.speed else {}
        return {
            "type": "ExecutableStepDTO",
            "stepOrder": order,
            "stepType": STEP_TYPES[step.intensity],
            "endCondition": end,
            "endConditionValue": value,
            "targetType": PACE_TARGET if step.speed else NO_TARGET,
        } | target

    def block_json(block: RepeatBlock) -> dict:
        nonlocal order
        order += 1
        group = {
            "type": "RepeatGroupDTO",
            "stepOrder": order,
            "stepType": REPEAT_TYPE,
            "numberOfIterations": block.count,
            "endCondition": END_ITERATIONS,
            "endConditionValue": float(block.count),
            "smartRepeat": False,
        }
        return group | {"workoutSteps": [step_json(step) for step in block.steps]}

    total = sum(
        estimated_seconds(item) if isinstance(item, WatchStep) else item.count * sum(map(estimated_seconds, item.steps))
        for item in items
    )
    return {
        "workoutName": workout_name(session),
        "sportType": RUNNING,
        "estimatedDurationInSecs": round(total),
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": RUNNING,
            "workoutSteps": [step_json(item) if isinstance(item, WatchStep) else block_json(item) for item in items],
        }],
    }


def fingerprint(session: DatedSession) -> str:
    """Changes exactly when what the watch would get changes, so a reworded note needs no re-send."""
    sent = [session.date.isoformat(), garmin_json(session)]
    return hashlib.sha256(json.dumps(sent, sort_keys=True).encode()).hexdigest()


def file_name(session: DatedSession) -> str:
    return f"{session.date.isoformat()}-{re.sub(r'[^a-z0-9]+', '-', session.title.lower()).strip('-') or 'run'}.fit"
