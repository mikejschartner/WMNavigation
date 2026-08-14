"""Quest / loot route planning. Recalculate only when callers ask — never per frame."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import permutations

from .loot_filter import item_best_price
from .models import ItemInfo, LootSpot, MapPoint
from .nav_graph import NavGraph
from .raid_time import LOOT_STOP_SECONDS, usable_loot_seconds, travel_seconds

# Ignore placeholder pins that are not real objective coordinates.
ANYWHERE_HINT = "anywhere on this map"
CLUSTER_M = 10.0
MOVE_RECALC_M = 28.0
ROUTE_IMPROVE_RATIO = 0.12


@dataclass
class RouteStop:
    x: float
    y: float
    z: float
    label: str
    kind: str  # start | quest | loot | extract


@dataclass
class RouteResult:
    kind: str  # quest | loot
    stops: list[RouteStop] = field(default_factory=list)
    waypoints: list[tuple[float, float, float]] = field(default_factory=list)
    extract_label: str = ""
    total_m: float = 0.0
    message: str = ""
    skipped: list[str] = field(default_factory=list)
    ok: bool = True
    gen: int = 0


def xz_dist(ax: float, az: float, bx: float, bz: float) -> float:
    return math.hypot(ax - bx, az - bz)


def _cost(graph: NavGraph, a: RouteStop | tuple, b: RouteStop | tuple) -> float:
    ax, ay, az = _xyz(a)
    bx, by, bz = _xyz(b)
    return graph.travel_cost(ax, ay, az, bx, by, bz)


def _xyz(p: RouteStop | tuple) -> tuple[float, float, float]:
    if isinstance(p, RouteStop):
        return p.x, p.y, p.z
    return float(p[0]), float(p[1]), float(p[2])


def _stitch(graph: NavGraph, stops: list[RouteStop]) -> tuple[list[tuple[float, float, float]], float]:
    pts: list[tuple[float, float, float]] = []
    total = 0.0
    for a, b in zip(stops, stops[1:]):
        chunk = graph.path((a.x, a.y, a.z), (b.x, b.y, b.z))
        if pts and chunk:
            chunk = chunk[1:]
        pts.extend(chunk)
        total += graph.path_length(chunk) if len(chunk) > 1 else _cost(graph, a, b)
    return pts, total


def _best_extract(graph: NavGraph, last: RouteStop, extracts: list[MapPoint]) -> MapPoint | None:
    if not extracts:
        return None
    return min(extracts, key=lambda e: _cost(graph, last, (e.x, e.y, e.z)))


def _two_opt(graph: NavGraph, start: RouteStop, mids: list[RouteStop], end: RouteStop) -> list[RouteStop]:
    if len(mids) < 2:
        return [start, *mids, end]

    def length(order: list[RouteStop]) -> float:
        seq = [start, *order, end]
        return sum(_cost(graph, a, b) for a, b in zip(seq, seq[1:]))

    best = list(mids)
    best_len = length(best)
    improved = True
    while improved:
        improved = False
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                cand = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                cand_len = length(cand)
                if cand_len + 0.05 < best_len:
                    best, best_len = cand, cand_len
                    improved = True
                    break
            if improved:
                break
    return [start, *best, end]


def _held_karp(graph: NavGraph, start: RouteStop, mids: list[RouteStop], end: RouteStop) -> list[RouteStop]:
    n = len(mids)
    if n <= 1:
        return [start, *mids, end]
    if n > 8:
        return _two_opt(graph, start, mids, end)
    best_seq: list[RouteStop] | None = None
    best_len = 1e18
    for perm in permutations(range(n)):
        seq = [start] + [mids[i] for i in perm] + [end]
        d = sum(_cost(graph, a, b) for a, b in zip(seq, seq[1:]))
        if d < best_len:
            best_len = d
            best_seq = seq
    return best_seq or [start, *mids, end]


def _optimize(graph: NavGraph, start: RouteStop, mids: list[RouteStop], end: RouteStop) -> list[RouteStop]:
    greedy = _nearest_insertion(graph, start, mids, end)
    mids_g = greedy[1:-1]
    return _held_karp(graph, start, mids_g, end)


def _nearest_insertion(
    graph: NavGraph, start: RouteStop, mids: list[RouteStop], end: RouteStop
) -> list[RouteStop]:
    remaining = list(mids)
    ordered: list[RouteStop] = []
    cur = start
    while remaining:
        nxt = min(remaining, key=lambda s: _cost(graph, cur, s))
        remaining.remove(nxt)
        ordered.append(nxt)
        cur = nxt
    return [start, *ordered, end]


def _usable_quest_spots(spots: list[MapPoint]) -> tuple[list[MapPoint], list[str]]:
    usable: list[MapPoint] = []
    skipped: list[str] = []
    seen: set[tuple[int, int, int]] = set()
    for spot in spots:
        meta = spot.meta or {}
        desc = str(meta.get("description") or "")
        if ANYWHERE_HINT in desc.lower():
            skipped.append(f"{spot.label or 'Quest'}: no map coordinates")
            continue
        if meta.get("optional"):
            continue
        key = (round(spot.x), round(spot.y), round(spot.z))
        if key in seen:
            continue
        seen.add(key)
        usable.append(spot)
    return usable, skipped


def _cluster_objectives(spots: list[MapPoint]) -> list[RouteStop]:
    """One stop per objective; pick a representative location for multi-spot objs."""
    groups: dict[str, list[MapPoint]] = {}
    for spot in spots:
        meta = spot.meta or {}
        oid = str(meta.get("objective_id") or spot.id)
        groups.setdefault(oid, []).append(spot)
    stops: list[RouteStop] = []
    for oid, group in groups.items():
        # Median-ish: first, we'll snap to nearest-to-start later.
        rep = group[0]
        label = (rep.meta or {}).get("description") or rep.label or "Objective"
        quest = (rep.meta or {}).get("quest_name") or ""
        if quest and quest not in str(label):
            label = f"{quest}: {label}"
        stops.append(
            RouteStop(x=rep.x, y=rep.y, z=rep.z, label=str(label)[:80], kind="quest")
        )
        # Keep other candidates on the stop via nearby replacement in _snap_candidates
        setattr(stops[-1], "_candidates", group)
    return stops


def _snap_candidates(graph: NavGraph, start: RouteStop, stops: list[RouteStop]) -> list[RouteStop]:
    cur = start
    snapped: list[RouteStop] = []
    for stop in stops:
        cands: list[MapPoint] = getattr(stop, "_candidates", None) or []
        if not cands:
            snapped.append(stop)
            cur = stop
            continue
        best = min(cands, key=lambda p: graph.travel_cost(cur.x, cur.y, cur.z, p.x, p.y, p.z))
        label = stop.label
        snapped.append(RouteStop(x=best.x, y=best.y, z=best.z, label=label, kind="quest"))
        cur = snapped[-1]
    return snapped


def plan_quest_route(
    *,
    player: tuple[float, float, float],
    quest_spots: list[MapPoint],
    extracts: list[MapPoint],
    graph: NavGraph | None = None,
) -> RouteResult:
    graph = graph or NavGraph()
    if not extracts:
        return RouteResult(kind="quest", ok=False, message="Select at least one available extract.")
    usable, skipped = _usable_quest_spots(quest_spots)
    if not usable:
        msg = "No selected quest objectives have map coordinates."
        if skipped:
            msg += " " + "; ".join(skipped[:4])
        return RouteResult(kind="quest", ok=False, message=msg, skipped=skipped)

    start = RouteStop(x=player[0], y=player[1], z=player[2], label="You", kind="start")
    mids = _cluster_objectives(usable)
    mids = _snap_candidates(graph, start, mids)

    best: RouteResult | None = None
    for extract in extracts:
        end = RouteStop(x=extract.x, y=extract.y, z=extract.z, label=extract.label or "Extract", kind="extract")
        ordered = _optimize(graph, start, list(mids), end)
        pts, total = _stitch(graph, ordered)
        if best is None or total < best.total_m:
            best = RouteResult(
                kind="quest",
                stops=ordered,
                waypoints=pts,
                extract_label=end.label,
                total_m=total,
                skipped=skipped,
                ok=True,
                message=f"Quest route · {len(ordered) - 2} objective(s) · {end.label} · {total:.0f} m",
            )
    return best or RouteResult(kind="quest", ok=False, message="Could not build a quest route.")


def _spot_value(spot: LootSpot, items: dict[str, ItemInfo], allowed: set[str] | None) -> int:
    best = 0
    for iid in spot.item_ids:
        if allowed is not None and iid not in allowed:
            continue
        item = items.get(iid)
        if not item:
            continue
        best = max(best, item_best_price(item))
    return best


def _cluster_loot(spots: list[LootSpot], values: dict[str, int]) -> list[RouteStop]:
    remaining = sorted(spots, key=lambda s: values.get(s.id, 0), reverse=True)
    used: set[str] = set()
    out: list[RouteStop] = []
    r2 = CLUSTER_M * CLUSTER_M
    for spot in remaining:
        if spot.id in used:
            continue
        members = [spot]
        used.add(spot.id)
        for other in remaining:
            if other.id in used:
                continue
            if (spot.x - other.x) ** 2 + (spot.z - other.z) ** 2 <= r2 and abs(spot.y - other.y) < 3.5:
                members.append(other)
                used.add(other.id)
        value = max(values.get(m.id, 0) for m in members)
        cx = sum(m.x for m in members) / len(members)
        cy = sum(m.y for m in members) / len(members)
        cz = sum(m.z for m in members) / len(members)
        stop = RouteStop(
            x=cx,
            y=cy,
            z=cz,
            label=f"Loot ₽{value:,}",
            kind="loot",
        )
        setattr(stop, "_value", value)
        out.append(stop)
    return out


def plan_loot_route(
    *,
    player: tuple[float, float, float],
    spots: list[LootSpot],
    items: dict[str, ItemInfo],
    extracts: list[MapPoint],
    allowed_ids: set[str] | None,
    locked_ids: set[str],
    remaining_s: float | None,
    graph: NavGraph | None = None,
    min_value: int = 50_000,
) -> RouteResult:
    graph = graph or NavGraph()
    if not extracts:
        return RouteResult(kind="loot", ok=False, message="Select at least one available extract.")

    accessible = [s for s in spots if s.id not in locked_ids]
    values = {s.id: _spot_value(s, items, allowed_ids) for s in accessible}
    candidates = [s for s in accessible if values.get(s.id, 0) >= min_value]
    if allowed_ids is not None:
        # Hunt/filter selection already encodes what the user cares about.
        candidates = [s for s in accessible if values.get(s.id, 0) > 0]
    if not candidates:
        return RouteResult(
            kind="loot",
            ok=False,
            message="No accessible loot matches your filters (locked rooms are skipped).",
        )

    start = RouteStop(x=player[0], y=player[1], z=player[2], label="You", kind="start")
    clusters = _cluster_loot(candidates, values)
    # Cap work: keep the best-scoring nearby clusters, not the whole map.
    clusters.sort(key=lambda s: getattr(s, "_value", 0) / (1.0 + xz_dist(start.x, start.z, s.x, s.z)), reverse=True)
    clusters = clusters[:40]

    nearest_ex = _best_extract(graph, start, extracts)
    if nearest_ex is None:
        return RouteResult(kind="loot", ok=False, message="Select at least one available extract.")

    extract_from_start = travel_seconds(_cost(graph, start, (nearest_ex.x, nearest_ex.y, nearest_ex.z)))
    budget = usable_loot_seconds(remaining_s, extract_from_start)

    chosen: list[RouteStop] = []
    cur = start
    used_s = 0.0
    remaining_clusters = list(clusters)
    while remaining_clusters:
        best_i = -1
        best_score = 0.0
        for i, stop in enumerate(remaining_clusters):
            travel = _cost(graph, cur, stop)
            value = float(getattr(stop, "_value", 0))
            # Value per meter traveled — not "highest pile on the map".
            score = value / (travel + 8.0)
            ex = _best_extract(graph, stop, extracts)
            if ex is None:
                continue
            need = travel_seconds(travel) + LOOT_STOP_SECONDS + travel_seconds(
                _cost(graph, stop, (ex.x, ex.y, ex.z))
            )
            if budget is not None and used_s + need > budget:
                continue
            if score > best_score:
                best_score = score
                best_i = i
        if best_i < 0:
            break
        # Diminishing returns: skip junk once we already have a strong route.
        if chosen and best_score < 400:
            break
        stop = remaining_clusters.pop(best_i)
        chosen.append(stop)
        used_s += travel_seconds(_cost(graph, cur, stop)) + LOOT_STOP_SECONDS
        cur = stop

    if not chosen:
        # Still extract — maybe time is too tight for loot.
        end = RouteStop(
            x=nearest_ex.x,
            y=nearest_ex.y,
            z=nearest_ex.z,
            label=nearest_ex.label or "Extract",
            kind="extract",
        )
        ordered = [start, end]
        pts, total = _stitch(graph, ordered)
        note = "Not enough raid time for loot — extract route only." if budget is not None else "No efficient loot stops found."
        return RouteResult(
            kind="loot",
            stops=ordered,
            waypoints=pts,
            extract_label=end.label,
            total_m=total,
            ok=True,
            message=note,
        )

    best: RouteResult | None = None
    for extract in extracts:
        end = RouteStop(x=extract.x, y=extract.y, z=extract.z, label=extract.label or "Extract", kind="extract")
        ordered = _two_opt(graph, start, list(chosen), end)
        pts, total = _stitch(graph, ordered)
        if best is None or total < best.total_m:
            n = len(ordered) - 2
            best = RouteResult(
                kind="loot",
                stops=ordered,
                waypoints=pts,
                extract_label=end.label,
                total_m=total,
                ok=True,
                message=f"Loot route · {n} stop(s) · {end.label} · {total:.0f} m",
            )
    return best or RouteResult(kind="loot", ok=False, message="Could not build a loot route.")


def should_refresh_route(
    previous: RouteResult | None,
    player: tuple[float, float, float],
    new_result: RouteResult,
) -> bool:
    if previous is None or not previous.ok or not previous.waypoints:
        return True
    if not new_result.ok:
        return False
    if new_result.total_m + 1 < previous.total_m * (1.0 - ROUTE_IMPROVE_RATIO):
        return True
    # Consume reached waypoints: if we are already at the next stop, accept.
    if len(previous.stops) >= 2:
        nxt = previous.stops[1]
        if xz_dist(player[0], player[2], nxt.x, nxt.z) < 12.0:
            return True
    return False


def player_moved_enough(origin: tuple[float, float, float] | None, now: tuple[float, float, float]) -> bool:
    if origin is None:
        return True
    return xz_dist(origin[0], origin[2], now[0], now[2]) >= MOVE_RECALC_M
