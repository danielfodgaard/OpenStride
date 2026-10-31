// Shared formatting. Dates are handled as UTC so daylight saving can't shift them.
export const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const LONG_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export function paceText(secondsPerKm) {
  const total = Math.round(secondsPerKm);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function timeText(seconds) {
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

const asDate = (iso) => new Date(`${iso.slice(0, 10)}T00:00:00Z`);
export const dayMs = (iso) => asDate(iso).getTime();

export function addDays(iso, days) {
  const date = asDate(iso);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function dateText(iso) {
  const date = asDate(iso);
  return `${LONG_DAYS[date.getUTCDay()]} ${date.getUTCDate()} ${MONTHS[date.getUTCMonth()].slice(0, 3)} ${date.getUTCFullYear()}`;
}

export const shortDate = (iso) => dateText(iso).slice(0, -5);

export const roundKm = (km) => Number(km.toFixed(1));

export function monthHeading(iso) {
  const date = asDate(iso);
  return `${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

// "2026-11-03T07:12:30" as stored: it's already local time, so read it, don't convert it.
export const clockText = (localIso) => localIso?.slice(11, 16) ?? "";

export function utcText(iso) {
  return iso ? `${dateText(iso)} ${iso.slice(11, 16)} UTC` : "";
}

// A target as [fastest, slowest] seconds per km. "5:11-6:13/km" is a range; "4:05/km" gets ±5 s.
export function targetBand(text) {
  const times = [...(text ?? "").matchAll(/(\d+):(\d\d)/g)].map((m) => Number(m[1]) * 60 + Number(m[2]));
  if (!times.length) return null;
  return times.length > 1 ? [Math.min(...times), Math.max(...times)] : [times[0] - 5, times[0] + 5];
}

export function amountText(item) {
  if (item.distance_km) return `${item.distance_km} km`;
  return item.duration_min ? `${item.duration_min} min` : "";
}

export function stepText(step) {
  if (step.reps || step.load) {  // a strength exercise; absent in v1 and v2 files
    return `${step.repeat} × ${step.reps} ${step.description}${step.load ? ` — ${step.load}` : ""}`;
  }
  const size = step.distance_km ? `${step.distance_km.toFixed(1)} km` : amountText(step);
  const sized = `${step.repeat > 1 ? `${step.repeat} × ` : ""}${size}`.trim();
  return [[sized, step.target_pace].filter(Boolean).join(" @ "), step.description].filter(Boolean).join(" — ");
}

export function actualText(workout) {
  if (workout.manual) {
    return ["Done", workout.duration_min ? `${workout.duration_min} min` : "",
      workout.distance_km ? `${workout.distance_km} km` : "", workout.rpe ? `RPE ${workout.rpe}` : ""].filter(Boolean).join(" · ");
  }
  const parts = [`${workout.distance_km.toFixed(2)} km`, timeText(workout.moving_time_s), workout.avg_pace];
  if (workout.avg_hr) parts.push(`${workout.avg_hr} bpm`);
  return parts.join(" · ");
}

// Repeats in order. Paired repeats, like 6 reps with 5 or 6 jogs, alternate as a watch laps them.
export function expandSteps(steps) {
  const expanded = [];
  for (let i = 0; i < steps.length; i += 1) {
    const step = steps[i];
    const next = steps[i + 1];
    if (step.repeat > 1 && next?.repeat > 1 && Math.abs(step.repeat - next.repeat) <= 1) {
      for (let n = 0; n < Math.max(step.repeat, next.repeat); n += 1) {
        if (n < step.repeat) expanded.push(step);
        if (n < next.repeat) expanded.push(next);
      }
      i += 1;
    } else {
      for (let n = 0; n < step.repeat; n += 1) expanded.push(step);
    }
  }
  return expanded;
}
