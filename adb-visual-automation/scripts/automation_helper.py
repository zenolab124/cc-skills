#!/usr/bin/env python3

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ENGINE_VERSION = "1.0.0"
SUPPORTED_ACTIONS_SCHEMA_VERSION = 1
CACHE_SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1


class HelperError(Exception):
    def __init__(self, status: str, message: str, exit_code: int = 20):
        super().__init__(message)
        self.status = status
        self.message = message
        self.exit_code = exit_code


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def emit(payload: Dict[str, Any], exit_code: int = 0) -> None:
    print(canonical_json(payload))
    raise SystemExit(exit_code)


def import_cv() -> Tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise HelperError("dependency_missing", f"OpenCV dependency unavailable: {exc}", 20)
    return cv2, np


def load_json(path: Path, missing: Optional[Any] = None) -> Any:
    if not path.exists():
        if missing is not None:
            return missing
        raise HelperError("file_missing", f"File does not exist: {path}", 20)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HelperError("invalid_json", f"Cannot read JSON {path}: {exc}", 20)


def load_actions(path: Path) -> Dict[str, Any]:
    data = load_json(path)
    if data.get("schema_version") != SUPPORTED_ACTIONS_SCHEMA_VERSION or not isinstance(data.get("actions"), dict):
        raise HelperError("invalid_config", "actions.json has an unsupported schema", 20)
    if data.get("engine_version") != ENGINE_VERSION:
        raise HelperError(
            "incompatible_engine",
            f"actions.json requires engine_version={data.get('engine_version')!r}; installed={ENGINE_VERSION}",
            20,
        )
    for action_id, action in data["actions"].items():
        required = {
            "package",
            "pre_page",
            "roi",
            "threshold",
            "min_margin",
            "tap_offset",
            "templates",
        }
        missing = sorted(required - set(action))
        if missing:
            raise HelperError("invalid_config", f"{action_id} is missing: {', '.join(missing)}", 20)
        has_post_page = isinstance(action.get("post_page"), str) and bool(action["post_page"])
        has_post_pages = (
            isinstance(action.get("post_pages"), list)
            and bool(action["post_pages"])
            and all(isinstance(page, str) and page for page in action["post_pages"])
        )
        if has_post_page == has_post_pages:
            raise HelperError(
                "invalid_config",
                f"{action_id} must define exactly one of post_page or post_pages",
                20,
            )
        roi = action["roi"]
        tap_offset = action["tap_offset"]
        if (
            not isinstance(roi, list)
            or len(roi) != 4
            or not all(isinstance(v, (int, float)) for v in roi)
            or not (0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1)
        ):
            raise HelperError("invalid_config", f"{action_id} has an invalid ROI", 20)
        if (
            not isinstance(tap_offset, list)
            or len(tap_offset) != 2
            or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in tap_offset)
        ):
            raise HelperError("invalid_config", f"{action_id} has an invalid tap_offset", 20)
        if not 0 < float(action["threshold"]) <= 1 or not 0 <= float(action["min_margin"]) <= 1:
            raise HelperError("invalid_config", f"{action_id} has invalid match thresholds", 20)
        if not isinstance(action["templates"], list):
            raise HelperError("invalid_config", f"{action_id}.templates must be a list", 20)
        fallback = action.get("vision_fallback")
        if fallback is not None:
            fallback_roi = fallback.get("roi") if isinstance(fallback, dict) else None
            fallback_tap = fallback.get("tap_offset") if isinstance(fallback, dict) else None
            if (
                not isinstance(fallback, dict)
                or fallback.get("enabled") is not True
                or not isinstance(fallback.get("target"), str)
                or not fallback["target"]
                or not isinstance(fallback_roi, list)
                or len(fallback_roi) != 4
                or not all(isinstance(value, (int, float)) for value in fallback_roi)
                or not (0 <= fallback_roi[0] < fallback_roi[2] <= 1 and 0 <= fallback_roi[1] < fallback_roi[3] <= 1)
                or not isinstance(fallback_tap, list)
                or len(fallback_tap) != 2
                or not all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in fallback_tap)
                or not 0 < float(fallback.get("min_area_ratio", 0)) <= float(fallback.get("max_area_ratio", 0)) <= 1
                or int(fallback.get("min_evidence_count", 0)) < 2
            ):
                raise HelperError("invalid_config", f"{action_id}.vision_fallback is invalid", 20)
            if (
                fallback_roi[0] < roi[0]
                or fallback_roi[1] < roi[1]
                or fallback_roi[2] > roi[2]
                or fallback_roi[3] > roi[3]
            ):
                raise HelperError("invalid_config", f"{action_id}.vision_fallback ROI exceeds action ROI", 20)
    return data


def get_action(config: Dict[str, Any], action_id: str) -> Dict[str, Any]:
    action = config["actions"].get(action_id)
    if action is None:
        raise HelperError("unknown_action", f"Unknown action: {action_id}", 13)
    return action


def ensure_state_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass


@contextlib.contextmanager
def state_lock(state_dir: Path) -> Iterable[None]:
    ensure_state_dir(state_dir)
    lock_path = state_dir / "run.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            lock_path.chmod(0o600)
        except OSError:
            pass
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_json(path: Path, value: Any) -> None:
    ensure_state_dir(path.parent)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def cache_path(state_dir: Path) -> Path:
    return state_dir / "cache-v1.json"


def empty_cache() -> Dict[str, Any]:
    return {"schema_version": CACHE_SCHEMA_VERSION, "updated_at": utc_now(), "entries": {}}


def load_cache(state_dir: Path) -> Dict[str, Any]:
    path = cache_path(state_dir)
    if not path.exists():
        return empty_cache()
    data = load_json(path)
    if data.get("schema_version") != CACHE_SCHEMA_VERSION or not isinstance(data.get("entries"), dict):
        raise HelperError("invalid_cache", "Coordinate cache has an unsupported schema", 20)
    return data


def append_event(state_dir: Path, event: Dict[str, Any]) -> None:
    ensure_state_dir(state_dir)
    path = state_dir / "events.jsonl"
    payload = {"schema_version": EVENT_SCHEMA_VERSION, "ts": utc_now(), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(payload) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def context_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    orientation = args.orientation
    expected_orientation = "portrait" if args.input_height >= args.input_width else "landscape"
    if orientation != expected_orientation:
        raise HelperError(
            "unsupported_context",
            f"orientation={orientation} conflicts with input dimensions {args.input_width}x{args.input_height}",
            13,
        )
    if args.rotation != 0 or orientation != "portrait":
        raise HelperError("unsupported_context", "Only portrait rotation=0 is supported", 13)
    if min(args.screen_width, args.screen_height, args.input_width, args.input_height) <= 0:
        raise HelperError("invalid_context", "Screen and input dimensions must be positive", 20)
    screen_ratio = args.screen_width / args.screen_height
    input_ratio = args.input_width / args.input_height
    if abs(screen_ratio - input_ratio) / input_ratio > 0.01:
        raise HelperError("unsupported_context", "Screenshot and Android input aspect ratios differ", 13)
    return {
        "action_id": args.action,
        "package": args.package,
        "version_name": args.version_name,
        "version_code": args.version_code,
        "screen_width": args.screen_width,
        "screen_height": args.screen_height,
        "input_width": args.input_width,
        "input_height": args.input_height,
        "orientation": orientation,
        "rotation": args.rotation,
        "pre_page": args.pre_page,
    }


def validate_action_context(action: Dict[str, Any], context: Dict[str, Any]) -> None:
    if action["package"] != context["package"]:
        raise HelperError("unsupported_context", "Package does not match action configuration", 13)
    if action["pre_page"] != context["pre_page"]:
        raise HelperError("unsupported_context", "Precondition page does not match action configuration", 13)


def bind_action_context(action: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    validate_action_context(action, context)
    return {
        **context,
        "engine_version": ENGINE_VERSION,
        "action_config_sha256": hashlib.sha256(
            canonical_json(action).encode("utf-8")
        ).hexdigest(),
    }


def context_key(context: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(context).encode("utf-8")).hexdigest()


def template_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_template(template: Dict[str, Any], templates_dir: Path) -> Path:
    relative = template.get("file")
    if not isinstance(relative, str) or not relative:
        raise HelperError("invalid_config", "Template file is missing", 20)
    templates_root = templates_dir.resolve()
    path = (templates_root / relative).resolve()
    if templates_root != path and templates_root not in path.parents:
        raise HelperError("invalid_config", f"Template escapes templates directory: {relative}", 20)
    return path


def template_compatible(template: Dict[str, Any], context: Dict[str, Any]) -> bool:
    version_codes = template.get("compatible_version_codes")
    if not isinstance(version_codes, list) or not version_codes:
        return False
    if str(context["version_code"]) not in {str(value) for value in version_codes}:
        return False
    if int(template.get("rotation", -1)) != context["rotation"]:
        return False
    return True


def command_doctor(args: argparse.Namespace) -> None:
    config = load_actions(args.actions)
    cv2, np = import_cv()
    missing_templates: List[str] = []
    invalid_templates: List[str] = []
    for action_id, action in config["actions"].items():
        if not action["templates"]:
            if action.get("required", True):
                missing_templates.append(action_id)
            continue
        for template in action["templates"]:
            try:
                path = resolve_template(template, args.templates_dir)
                if not path.is_file():
                    raise HelperError("file_missing", str(path), 20)
                expected_hash = template.get("sha256")
                if not isinstance(expected_hash, str) or template_sha256(path) != expected_hash:
                    invalid_templates.append(f"{action_id}:{template.get('id', path.name)}:sha256")
                if min(int(template.get("base_width", 0)), int(template.get("base_height", 0))) <= 0:
                    invalid_templates.append(f"{action_id}:{template.get('id', path.name)}:dimensions")
                if not template.get("compatible_version_codes"):
                    invalid_templates.append(f"{action_id}:{template.get('id', path.name)}:versions")
            except HelperError as exc:
                invalid_templates.append(f"{action_id}:{exc.message}")
    ensure_state_dir(args.state_dir)
    cache_status = "empty"
    with state_lock(args.state_dir):
        existing = load_cache(args.state_dir)
        if existing["entries"]:
            cache_status = "ready"
    status = "ready" if not missing_templates and not invalid_templates else "not_ready"
    append_event(
        args.state_dir,
        {
            "event": "doctor",
            "status": status,
            "missing_template_actions": missing_templates,
            "invalid_templates": invalid_templates,
        },
    )
    emit(
        {
            "status": status,
            "engine_version": ENGINE_VERSION,
            "actions_schema_version": SUPPORTED_ACTIONS_SCHEMA_VERSION,
            "python": sys.version.split()[0],
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "cache": cache_status,
            "missing_template_actions": missing_templates,
            "invalid_templates": invalid_templates,
        },
        0 if status == "ready" else 13,
    )


def command_lookup(args: argparse.Namespace) -> None:
    config = load_actions(args.actions)
    action = get_action(config, args.action)
    context = bind_action_context(action, context_from_args(args))
    key = context_key(context)
    with state_lock(args.state_dir):
        cache = load_cache(args.state_dir)
        entry = cache["entries"].get(key)
    if not entry or not entry.get("active"):
        append_event(args.state_dir, {"event": "cache_lookup", "action": args.action, "cache_key": key, "status": "miss"})
        emit({"status": "miss", "cache_key": key}, 10)
    coordinate = entry.get("coordinate")
    if (
        not isinstance(coordinate, dict)
        or not 0 <= int(coordinate.get("x", -1)) < context["input_width"]
        or not 0 <= int(coordinate.get("y", -1)) < context["input_height"]
    ):
        raise HelperError("invalid_cache", "Cached coordinate is outside the Android input bounds", 20)
    append_event(args.state_dir, {"event": "cache_lookup", "action": args.action, "cache_key": key, "status": "found"})
    emit({"status": "found", "source": "cache", "cache_key": key, "coordinate": coordinate})


def intersection_over_union(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    x1 = max(left["x"], right["x"])
    y1 = max(left["y"], right["y"])
    x2 = min(left["x"] + left["width"], right["x"] + right["width"])
    y2 = min(left["y"] + left["height"], right["y"] + right["height"])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = left["width"] * left["height"] + right["width"] * right["height"] - intersection
    return intersection / union if union else 0.0


def extract_candidates(
    score_map: Any,
    template_width: int,
    template_height: int,
    template_id: str,
    template_hash: str,
    scale: float,
    roi_x: int,
    roi_y: int,
    cv2: Any,
    np: Any,
) -> List[Dict[str, Any]]:
    working = score_map.copy()
    candidates: List[Dict[str, Any]] = []
    for _ in range(3):
        _, maximum, _, location = cv2.minMaxLoc(working)
        if not math.isfinite(float(maximum)):
            break
        x, y = int(location[0]), int(location[1])
        candidates.append(
            {
                "score": float(maximum),
                "bounds": {
                    "x": x + roi_x,
                    "y": y + roi_y,
                    "width": template_width,
                    "height": template_height,
                },
                "template_id": template_id,
                "template_sha256": template_hash,
                "scale": scale,
            }
        )
        suppress_left = max(0, x - template_width // 2)
        suppress_top = max(0, y - template_height // 2)
        suppress_right = min(working.shape[1], x + template_width)
        suppress_bottom = min(working.shape[0], y + template_height)
        working[suppress_top:suppress_bottom, suppress_left:suppress_right] = np.float32(-1.0)
    return candidates


def non_maximum_suppression(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
        if all(intersection_over_union(candidate["bounds"], prior["bounds"]) < 0.5 for prior in selected):
            selected.append(candidate)
    return selected


def scale_values(expected: float, tolerance: float, step: float) -> List[float]:
    if expected <= 0 or tolerance < 0 or step <= 0:
        raise HelperError("invalid_config", "Invalid template scaling parameters", 20)
    values: List[float] = []
    current = expected * (1 - tolerance)
    maximum = expected * (1 + tolerance)
    increment = expected * step
    while current <= maximum + increment / 2:
        values.append(current)
        current += increment
    return values


def command_match(args: argparse.Namespace) -> None:
    config = load_actions(args.actions)
    action = get_action(config, args.action)
    context = context_from_args(args)
    validate_action_context(action, context)
    cv2, np = import_cv()
    screen = cv2.imread(str(args.screen), cv2.IMREAD_GRAYSCALE)
    if screen is None:
        raise HelperError("invalid_screen", f"Cannot decode screen PNG: {args.screen}", 20)
    actual_height, actual_width = screen.shape[:2]
    if (actual_width, actual_height) != (context["screen_width"], context["screen_height"]):
        raise HelperError(
            "invalid_screen",
            f"PNG is {actual_width}x{actual_height}, expected {context['screen_width']}x{context['screen_height']}",
            20,
        )
    roi = action["roi"]
    roi_x1 = int(round(actual_width * roi[0]))
    roi_y1 = int(round(actual_height * roi[1]))
    roi_x2 = int(round(actual_width * roi[2]))
    roi_y2 = int(round(actual_height * roi[3]))
    region = screen[roi_y1:roi_y2, roi_x1:roi_x2]
    region_edges = cv2.Canny(region, 60, 160)
    compatible = [template for template in action["templates"] if template_compatible(template, context)]
    if not compatible:
        emit({"status": "unsupported_context", "reason": "no_compatible_template"}, 13)
    candidates: List[Dict[str, Any]] = []
    for template_config in compatible:
        path = resolve_template(template_config, args.templates_dir)
        if not path.is_file():
            raise HelperError("invalid_config", f"Template is missing: {path}", 20)
        expected_hash = template_config.get("sha256")
        actual_hash = template_sha256(path)
        if actual_hash != expected_hash:
            raise HelperError("invalid_config", f"Template hash mismatch: {path.name}", 20)
        template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise HelperError("invalid_config", f"Cannot decode template: {path}", 20)
        base_width = int(template_config["base_width"])
        base_height = int(template_config["base_height"])
        scale_x = actual_width / base_width
        scale_y = actual_height / base_height
        if abs(scale_x - scale_y) / scale_x > 0.01:
            continue
        for scale in scale_values(
            (scale_x + scale_y) / 2,
            float(action.get("scale_tolerance", 0.08)),
            float(action.get("scale_step", 0.02)),
        ):
            width = max(1, int(round(template.shape[1] * scale)))
            height = max(1, int(round(template.shape[0] * scale)))
            if width > region.shape[1] or height > region.shape[0] or min(width, height) < 3:
                continue
            resized = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
            normalized = resized
            gray_scores = cv2.matchTemplate(region, normalized, cv2.TM_CCOEFF_NORMED)
            edge_template = cv2.Canny(normalized, 60, 160)
            if int(np.count_nonzero(edge_template)) >= 8:
                edge_scores = cv2.matchTemplate(region_edges, edge_template, cv2.TM_CCOEFF_NORMED)
                score_map = gray_scores * 0.75 + edge_scores * 0.25
            else:
                score_map = gray_scores
            score_map = np.nan_to_num(score_map, nan=-1.0, posinf=-1.0, neginf=-1.0)
            candidates.extend(
                extract_candidates(
                    score_map,
                    width,
                    height,
                    str(template_config.get("id", path.stem)),
                    actual_hash,
                    scale,
                    roi_x1,
                    roi_y1,
                    cv2,
                    np,
                )
            )
    ranked = non_maximum_suppression(candidates)
    if not ranked:
        emit({"status": "not_found", "threshold": action["threshold"]}, 11)
    best = ranked[0]
    second_score = float(ranked[1]["score"]) if len(ranked) > 1 else -1.0
    margin = float(best["score"]) - second_score
    base_result = {
        "score": round(float(best["score"]), 6),
        "second_score": round(second_score, 6),
        "margin": round(margin, 6),
        "threshold": action["threshold"],
        "min_margin": action["min_margin"],
        "template_id": best["template_id"],
        "template_sha256": best["template_sha256"],
        "screenshot_bounds": best["bounds"],
        "roi_pixels": {"x": roi_x1, "y": roi_y1, "width": roi_x2 - roi_x1, "height": roi_y2 - roi_y1},
    }
    if float(best["score"]) < float(action["threshold"]):
        append_event(args.state_dir, {"event": "match", "action": args.action, "status": "not_found", **base_result})
        emit({"status": "not_found", **base_result}, 11)
    if second_score >= 0 and margin < float(action["min_margin"]):
        append_event(args.state_dir, {"event": "match", "action": args.action, "status": "ambiguous", **base_result})
        emit({"status": "ambiguous", **base_result}, 12)
    bounds = best["bounds"]
    tap_offset = action["tap_offset"]
    screen_x = bounds["x"] + bounds["width"] * float(tap_offset[0])
    screen_y = bounds["y"] + bounds["height"] * float(tap_offset[1])
    input_x = int(round(screen_x * context["input_width"] / context["screen_width"]))
    input_y = int(round(screen_y * context["input_height"] / context["screen_height"]))
    if not 0 <= input_x < context["input_width"] or not 0 <= input_y < context["input_height"]:
        raise HelperError("invalid_match", "Matched coordinate is outside Android input bounds", 20)
    result = {"status": "found", "source": "opencv", "coordinate": {"x": input_x, "y": input_y}, **base_result}
    append_event(args.state_dir, {"event": "match", "action": args.action, **result})
    emit(result)


def validate_vision_proposal(
    action: Dict[str, Any],
    context: Dict[str, Any],
    target: Optional[str],
    bounds: Optional[List[float]],
    evidence_count: int,
    expected_coordinate: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    fallback = action.get("vision_fallback")
    if not isinstance(fallback, dict) or not fallback.get("enabled"):
        raise HelperError("vision_not_allowed", "AI vision fallback is not enabled for this action", 13)
    if target != fallback.get("target"):
        raise HelperError("invalid_vision_target", "AI vision target does not match the action allowlist", 20)
    if evidence_count < int(fallback.get("min_evidence_count", 2)):
        raise HelperError("invalid_vision_target", "AI vision fallback requires more visual evidence", 20)
    if not isinstance(bounds, list) or len(bounds) != 4 or not all(math.isfinite(value) for value in bounds):
        raise HelperError("invalid_vision_target", "AI vision bounds must contain four finite normalized values", 20)
    x1, y1, x2, y2 = bounds
    if not 0 <= x1 < x2 <= 1 or not 0 <= y1 < y2 <= 1:
        raise HelperError("invalid_vision_target", "AI vision bounds are outside normalized image space", 20)
    roi = fallback.get("roi", action.get("roi"))
    if not isinstance(roi, list) or len(roi) != 4:
        raise HelperError("invalid_config", "AI vision fallback ROI is invalid", 20)
    if x1 < roi[0] or y1 < roi[1] or x2 > roi[2] or y2 > roi[3]:
        raise HelperError("vision_outside_safe_roi", "AI vision bounds leave the action safety ROI", 13)
    width = x2 - x1
    height = y2 - y1
    area = width * height
    if not float(fallback.get("min_area_ratio", 0.0001)) <= area <= float(fallback.get("max_area_ratio", 0.25)):
        raise HelperError("invalid_vision_target", "AI vision target area is outside configured limits", 20)
    tap_offset = fallback.get("tap_offset", [0.5, 0.5])
    screen_x = (x1 + width * float(tap_offset[0])) * context["screen_width"]
    screen_y = (y1 + height * float(tap_offset[1])) * context["screen_height"]
    coordinate = {
        "x": int(round(screen_x * context["input_width"] / context["screen_width"])),
        "y": int(round(screen_y * context["input_height"] / context["screen_height"])),
    }
    if not 0 <= coordinate["x"] < context["input_width"] or not 0 <= coordinate["y"] < context["input_height"]:
        raise HelperError("invalid_vision_target", "AI vision coordinate is outside Android input bounds", 20)
    if expected_coordinate is not None and coordinate != expected_coordinate:
        raise HelperError("invalid_feedback", "AI vision feedback coordinate differs from the validated target", 20)
    return {
        "target": target,
        "normalized_bounds": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        "coordinate": coordinate,
        "evidence_count": evidence_count,
    }


def command_vision_target(args: argparse.Namespace) -> None:
    config = load_actions(args.actions)
    action = get_action(config, args.action)
    context = context_from_args(args)
    validate_action_context(action, context)
    cv2, _ = import_cv()
    screen = cv2.imread(str(args.screen), cv2.IMREAD_GRAYSCALE)
    if screen is None:
        raise HelperError("invalid_screen", f"Cannot decode screen PNG: {args.screen}", 20)
    actual_height, actual_width = screen.shape[:2]
    if (actual_width, actual_height) != (context["screen_width"], context["screen_height"]):
        raise HelperError(
            "invalid_screen",
            f"PNG is {actual_width}x{actual_height}, expected {context['screen_width']}x{context['screen_height']}",
            20,
        )
    result = validate_vision_proposal(
        action,
        context,
        args.target,
        [args.x1, args.y1, args.x2, args.y2],
        args.evidence_count,
    )
    payload = {
        "status": "found",
        "source": "ai_vision",
        **result,
        "screen_sha256": template_sha256(args.screen),
    }
    append_event(
        args.state_dir,
        {
            "event": "vision_target",
            "action": args.action,
            "status": "found",
            "target": result["target"],
            "normalized_bounds": result["normalized_bounds"],
            "coordinate": result["coordinate"],
            "screen_sha256": payload["screen_sha256"],
        },
    )
    emit(payload)


def command_feedback(args: argparse.Namespace) -> None:
    config = load_actions(args.actions)
    action = get_action(config, args.action)
    context = bind_action_context(action, context_from_args(args))
    key = context_key(context)
    allowed_post_pages = action.get("post_pages", [action.get("post_page")])
    expected_success = args.result == "success" and args.observed_page in allowed_post_pages
    if args.result == "success" and not expected_success:
        raise HelperError("posterior_mismatch", "Success feedback does not match the configured postcondition", 20)
    with state_lock(args.state_dir):
        cache = load_cache(args.state_dir)
        prior = cache["entries"].get(key)
        if not expected_success:
            if prior:
                prior["active"] = False
                prior["failure_count"] = int(prior.get("failure_count", 0)) + 1
                prior["last_failure_at"] = utc_now()
                prior["last_failure_page"] = args.observed_page
                cache["entries"][key] = prior
                cache["updated_at"] = utc_now()
                atomic_write_json(cache_path(args.state_dir), cache)
            append_event(
                args.state_dir,
                {
                    "event": "feedback",
                    "action": args.action,
                    "cache_key": key,
                    "result": "failure",
                    "source": args.source,
                    "observed_page": args.observed_page,
                },
            )
            emit({"status": "invalidated" if prior else "rejected", "cache_key": key})
        if args.x is None or args.y is None:
            raise HelperError("invalid_feedback", "Successful feedback requires x and y", 20)
        if not 0 <= args.x < context["input_width"] or not 0 <= args.y < context["input_height"]:
            raise HelperError("invalid_feedback", "Successful coordinate is outside Android input bounds", 20)
        if args.source == "cache":
            if not prior or not prior.get("active"):
                raise HelperError("invalid_feedback", "Cache success requires an active prior entry", 20)
            if prior.get("coordinate") != {"x": args.x, "y": args.y}:
                raise HelperError("invalid_feedback", "Cache success coordinate differs from the stored entry", 20)
        else:
            if args.source == "opencv":
                template_config = next(
                    (
                        template
                        for template in action["templates"]
                        if template.get("id") == args.template_id and template_compatible(template, context)
                    ),
                    None,
                )
                if template_config is None:
                    raise HelperError("invalid_feedback", "OpenCV success references an unsupported template", 20)
                template_path = resolve_template(template_config, args.templates_dir)
                if not template_path.is_file():
                    raise HelperError("invalid_feedback", "OpenCV success template is missing", 20)
                expected_hash = template_sha256(template_path)
                if args.template_sha256 != expected_hash or template_config.get("sha256") != expected_hash:
                    raise HelperError("invalid_feedback", "OpenCV success template hash does not match", 20)
                if args.score is None or args.second_score is None:
                    raise HelperError("invalid_feedback", "OpenCV success requires match scores", 20)
                if args.score < float(action["threshold"]):
                    raise HelperError("invalid_feedback", "OpenCV success score is below threshold", 20)
                if args.second_score >= 0 and args.score - args.second_score < float(action["min_margin"]):
                    raise HelperError("invalid_feedback", "OpenCV success candidate is ambiguous", 20)
            else:
                if args.screen is None or not args.screen.is_file():
                    raise HelperError("invalid_feedback", "AI vision success requires the original screen PNG", 20)
                if args.screen_sha256 != template_sha256(args.screen):
                    raise HelperError("invalid_feedback", "AI vision screen hash does not match", 20)
                validate_vision_proposal(
                    action,
                    context,
                    args.vision_target,
                    [args.x1, args.y1, args.x2, args.y2]
                    if None not in (args.x1, args.y1, args.x2, args.y2)
                    else None,
                    args.evidence_count,
                    {"x": args.x, "y": args.y},
                )
        now = utc_now()
        first_learned = prior.get("first_learned_at", now) if prior else now
        effective_source = args.source
        effective_template_id = args.template_id
        effective_template_sha256 = args.template_sha256
        effective_score = args.score
        effective_second_score = args.second_score
        effective_vision_target = args.vision_target
        effective_vision_bounds = (
            {"x1": args.x1, "y1": args.y1, "x2": args.x2, "y2": args.y2}
            if None not in (args.x1, args.y1, args.x2, args.y2)
            else None
        )
        effective_screen_sha256 = args.screen_sha256
        if args.source == "cache" and prior:
            effective_source = prior.get("source", "opencv")
            effective_template_id = prior.get("template_id")
            effective_template_sha256 = prior.get("template_sha256")
            effective_score = prior.get("score")
            effective_second_score = prior.get("second_score")
            effective_vision_target = prior.get("vision_target")
            effective_vision_bounds = prior.get("vision_bounds")
            effective_screen_sha256 = prior.get("screen_sha256")
        entry = {
            "active": True,
            "binding": context,
            "coordinate": {"x": args.x, "y": args.y},
            "normalized_coordinate": {
                "x": args.x / context["input_width"],
                "y": args.y / context["input_height"],
            },
            "source": effective_source,
            "template_id": effective_template_id,
            "template_sha256": effective_template_sha256,
            "score": effective_score,
            "second_score": effective_second_score,
            "vision_target": effective_vision_target,
            "vision_bounds": effective_vision_bounds,
            "screen_sha256": effective_screen_sha256,
            "verified_post_page": args.observed_page,
            "first_learned_at": first_learned,
            "last_verified_at": now,
            "success_count": int(prior.get("success_count", 0)) + 1 if prior else 1,
            "failure_count": int(prior.get("failure_count", 0)) if prior else 0,
        }
        cache["entries"][key] = entry
        cache["updated_at"] = now
        atomic_write_json(cache_path(args.state_dir), cache)
    append_event(
        args.state_dir,
        {
            "event": "feedback",
            "action": args.action,
            "cache_key": key,
            "result": "success",
            "source": args.source,
            "observed_page": args.observed_page,
        },
    )
    emit({"status": "stored", "cache_key": key, "success_count": entry["success_count"]})


def polygon_area(points: Any) -> float:
    area = 0.0
    for index in range(len(points)):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % len(points)]
        area += float(x1) * float(y2) - float(x2) * float(y1)
    return abs(area) / 2


def command_verify_qrcode(args: argparse.Namespace) -> None:
    cv2, np = import_cv()
    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        raise HelperError("invalid_screen", f"Cannot decode image: {args.image}", 20)
    height, width = image.shape[:2]
    detector = cv2.QRCodeDetector()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    adaptive = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        51,
        7,
    )
    detected_candidates: List[Dict[str, Any]] = []
    for variant_name, candidate in (
        ("original", image),
        ("inverted", cv2.bitwise_not(image)),
        ("adaptive", adaptive),
        ("adaptive_inverted", cv2.bitwise_not(adaptive)),
    ):
        detected, points = detector.detect(candidate)
        if not detected or points is None:
            continue
        quadrilateral = np.asarray(points, dtype=float).reshape(-1, 2)
        if quadrilateral.shape != (4, 2):
            continue
        area = polygon_area(quadrilateral)
        area_ratio = area / (width * height)
        edges = [float(np.linalg.norm(quadrilateral[index] - quadrilateral[(index + 1) % 4])) for index in range(4)]
        edge_ratio = min(edges) / max(edges) if max(edges) else 0
        detected_candidates.append(
            {
                "detection_variant": variant_name,
                "quadrilateral": quadrilateral,
                "area_ratio": area_ratio,
                "edge_ratio": edge_ratio,
            }
        )
    if not detected_candidates:
        emit({"status": "not_found"}, 11)
    valid_candidates = [
        candidate
        for candidate in detected_candidates
        if 0.005 <= candidate["area_ratio"] <= 0.8 and candidate["edge_ratio"] >= 0.6
    ]
    if not valid_candidates:
        best_invalid = max(detected_candidates, key=lambda candidate: candidate["edge_ratio"])
        emit(
            {
                "status": "invalid_qrcode",
                "detection_variant": best_invalid["detection_variant"],
                "area_ratio": round(best_invalid["area_ratio"], 6),
                "edge_ratio": round(best_invalid["edge_ratio"], 6),
                "detected_variant_count": len(detected_candidates),
            },
            11,
        )
    best = max(valid_candidates, key=lambda candidate: (candidate["edge_ratio"], candidate["area_ratio"]))
    quadrilateral = best["quadrilateral"]
    area_ratio = best["area_ratio"]
    edge_ratio = best["edge_ratio"]
    detection_variant = best["detection_variant"]
    emit(
        {
            "status": "found",
            "detection_variant": detection_variant,
            "area_ratio": round(area_ratio, 6),
            "edge_ratio": round(edge_ratio, 6),
            "points": [[round(float(x), 2), round(float(y), 2)] for x, y in quadrilateral],
        }
    )


def add_context_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--action", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--version-code", required=True)
    parser.add_argument("--screen-width", required=True, type=int)
    parser.add_argument("--screen-height", required=True, type=int)
    parser.add_argument("--input-width", required=True, type=int)
    parser.add_argument("--input-height", required=True, type=int)
    parser.add_argument("--orientation", choices=("portrait", "landscape"), required=True)
    parser.add_argument("--rotation", required=True, type=int)
    parser.add_argument("--pre-page", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic ADB visual automation helper")
    parser.add_argument("--actions", type=Path, required=True)
    parser.add_argument("--templates-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor")
    doctor.set_defaults(handler=command_doctor)

    lookup = subparsers.add_parser("lookup")
    add_context_arguments(lookup)
    lookup.set_defaults(handler=command_lookup)

    match = subparsers.add_parser("match")
    add_context_arguments(match)
    match.add_argument("--screen", type=Path, required=True)
    match.set_defaults(handler=command_match)

    vision_target = subparsers.add_parser("vision-target")
    add_context_arguments(vision_target)
    vision_target.add_argument("--screen", type=Path, required=True)
    vision_target.add_argument("--target", required=True)
    vision_target.add_argument("--x1", type=float, required=True)
    vision_target.add_argument("--y1", type=float, required=True)
    vision_target.add_argument("--x2", type=float, required=True)
    vision_target.add_argument("--y2", type=float, required=True)
    vision_target.add_argument("--evidence-count", type=int, default=2)
    vision_target.set_defaults(handler=command_vision_target)

    feedback = subparsers.add_parser("feedback")
    add_context_arguments(feedback)
    feedback.add_argument("--source", choices=("cache", "opencv", "ai_vision"), required=True)
    feedback.add_argument("--result", choices=("success", "failure"), required=True)
    feedback.add_argument("--observed-page", required=True)
    feedback.add_argument("--x", type=int)
    feedback.add_argument("--y", type=int)
    feedback.add_argument("--template-id")
    feedback.add_argument("--template-sha256")
    feedback.add_argument("--score", type=float)
    feedback.add_argument("--second-score", type=float)
    feedback.add_argument("--screen", type=Path)
    feedback.add_argument("--screen-sha256")
    feedback.add_argument("--vision-target")
    feedback.add_argument("--x1", type=float)
    feedback.add_argument("--y1", type=float)
    feedback.add_argument("--x2", type=float)
    feedback.add_argument("--y2", type=float)
    feedback.add_argument("--evidence-count", type=int, default=2)
    feedback.set_defaults(handler=command_feedback)

    verify_qrcode = subparsers.add_parser("verify-qrcode")
    verify_qrcode.add_argument("--image", type=Path, required=True)
    verify_qrcode.set_defaults(handler=command_verify_qrcode)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
    except HelperError as exc:
        emit({"status": exc.status, "message": exc.message}, exc.exit_code)
    except Exception as exc:
        emit({"status": "internal_error", "message": f"{type(exc).__name__}: {exc}"}, 20)


if __name__ == "__main__":
    main()
