#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[2]
DEFAULT_CONFIG = REPO_ROOT / "motion-tracking" / "config" / "opencv_tracking_videos.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "motion-tracking" / "user-data" / "opencv"
MAX_PLOT_POINTS = 12000
DEFAULT_RELAY_EVENT_FIRST_S = 50.0
DEFAULT_RELAY_EVENT_PERIOD_S = 50.0
DEFAULT_RELAY_EVENT_WINDOW_S = 5.0


@dataclass(frozen=True)
class VideoConfig:
    video_id: str
    video_path: Path
    manual_csv: Path | None
    manual_displacement_scale: float
    tip_seed_px: tuple[float, float]
    reference_seed_px: tuple[float, float]
    params: dict[str, Any]


@dataclass(frozen=True)
class VideoMetadata:
    width: int
    height: int
    fps: float
    reported_frame_count: int


def resolve_path(path_value: str | None, repo_root: Path) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if path.is_absolute():
        return path
    return repo_root / path


def load_config(config_path: Path, repo_root: Path) -> list[VideoConfig]:
    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    default_params = raw.get("default_params", {})
    videos = []
    for item in raw["videos"]:
        params = dict(default_params)
        params.update(item.get("params", {}))
        videos.append(
            VideoConfig(
                video_id=str(item["id"]),
                video_path=resolve_path(item["video_path"], repo_root),
                manual_csv=resolve_path(item.get("manual_csv"), repo_root),
                manual_displacement_scale=float(item.get("manual_displacement_scale", 1.0)),
                tip_seed_px=tuple(float(v) for v in item["tip_seed_px"]),
                reference_seed_px=tuple(float(v) for v in item["reference_seed_px"]),
                params=params,
            )
        )
    return videos


def scaled_odd_size(
    min_dimension: int,
    fraction: float,
    min_px: int,
    max_px: int,
) -> int:
    size = int(round(min_dimension * fraction))
    size = max(min_px, min(max_px, size))
    if size % 2 == 0:
        size += 1
    return size


def scaled_radius(
    min_dimension: int,
    fraction: float,
    min_px: int,
    max_px: int,
) -> int:
    return max(min_px, min(max_px, int(round(min_dimension * fraction))))


def read_metadata(cap: cv2.VideoCapture) -> VideoMetadata:
    width = int(round(cap.get(cv2.CAP_PROP_FRAME_WIDTH)))
    height = int(round(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    frame_count = int(round(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    if width <= 0 or height <= 0:
        raise RuntimeError("OpenCV did not report a valid video resolution")
    if not math.isfinite(fps) or fps <= 0:
        raise RuntimeError("OpenCV did not report a valid video FPS")
    return VideoMetadata(width=width, height=height, fps=fps, reported_frame_count=frame_count)


def to_gray(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (3, 3), 0)


def crop_center(image: np.ndarray, center_xy: tuple[float, float], radius: int) -> np.ndarray:
    x, y = center_xy
    x0 = int(round(x)) - radius
    y0 = int(round(y)) - radius
    x1 = x0 + radius * 2 + 1
    y1 = y0 + radius * 2 + 1

    left = max(0, -x0)
    top = max(0, -y0)
    right = max(0, x1 - image.shape[1])
    bottom = max(0, y1 - image.shape[0])

    if left or top or right or bottom:
        image = cv2.copyMakeBorder(image, top, bottom, left, right, cv2.BORDER_REFLECT_101)
        x0 += left
        x1 += left
        y0 += top
        y1 += top

    return image[y0:y1, x0:x1]


def search_roi(
    image: np.ndarray,
    center_xy: tuple[float, float],
    template_radius: int,
    search_radius: int,
) -> tuple[np.ndarray, int, int]:
    x, y = center_xy
    radius = template_radius + search_radius
    x0 = max(0, int(round(x)) - radius)
    y0 = max(0, int(round(y)) - radius)
    x1 = min(image.shape[1], int(round(x)) + radius + 1)
    y1 = min(image.shape[0], int(round(y)) + radius + 1)
    return image[y0:y1, x0:x1], x0, y0


def subpixel_peak(response: np.ndarray, peak_xy: tuple[int, int]) -> tuple[float, float]:
    x, y = peak_xy
    dx = 0.0
    dy = 0.0
    if 0 < x < response.shape[1] - 1:
        left = float(response[y, x - 1])
        middle = float(response[y, x])
        right = float(response[y, x + 1])
        denom = left - 2.0 * middle + right
        if abs(denom) > 1e-12:
            dx = max(-0.5, min(0.5, 0.5 * (left - right) / denom))
    if 0 < y < response.shape[0] - 1:
        top = float(response[y - 1, x])
        middle = float(response[y, x])
        bottom = float(response[y + 1, x])
        denom = top - 2.0 * middle + bottom
        if abs(denom) > 1e-12:
            dy = max(-0.5, min(0.5, 0.5 * (top - bottom) / denom))
    return dx, dy


def template_match_center(
    gray: np.ndarray,
    template: np.ndarray,
    prediction_xy: tuple[float, float],
    template_radius: int,
    search_radius: int,
) -> tuple[tuple[float, float] | None, float]:
    roi, x0, y0 = search_roi(gray, prediction_xy, template_radius, search_radius)
    if roi.shape[0] < template.shape[0] or roi.shape[1] < template.shape[1]:
        return None, float("nan")

    response = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(response)
    dx, dy = subpixel_peak(response, max_loc)
    center_x = x0 + max_loc[0] + dx + template_radius
    center_y = y0 + max_loc[1] + dy + template_radius
    return (center_x, center_y), float(max_val)


def find_dark_endpoint(
    gray: np.ndarray,
    previous_cv_xy: tuple[float, float],
    reference_cv_xy: tuple[float, float],
    direction_unit: np.ndarray,
    roi_radius: int,
    threshold_max: float,
    min_area_px: int,
    max_component_distance_fraction: float,
    score_mode: str,
) -> tuple[tuple[float, float] | None, float]:
    previous = np.array(previous_cv_xy, dtype=float)
    reference = np.array(reference_cv_xy, dtype=float)
    x0 = max(0, int(round(previous[0] - roi_radius)))
    y0 = max(0, int(round(previous[1] - roi_radius)))
    x1 = min(gray.shape[1], int(round(previous[0] + roi_radius + 1)))
    y1 = min(gray.shape[0], int(round(previous[1] + roi_radius + 1)))
    if x1 <= x0 or y1 <= y0:
        return None, float("nan")

    roi = gray[y0:y1, x0:x1]
    blur = cv2.GaussianBlur(roi, (3, 3), 0)
    otsu_threshold, _ = cv2.threshold(
        blur,
        0,
        255,
        cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU,
    )
    threshold = min(float(otsu_threshold), threshold_max)
    mask = (blur < threshold).astype("uint8")
    component_count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)

    best_point: np.ndarray | None = None
    best_score = -float("inf")
    max_component_distance = roi_radius * max_component_distance_fraction
    for label in range(1, component_count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area_px:
            continue

        centroid = np.array(centroids[label], dtype=float) + np.array([x0, y0], dtype=float)
        if np.linalg.norm(centroid - previous) > max_component_distance:
            continue

        ys, xs = np.where(labels == label)
        points = np.column_stack([xs + x0, ys + y0]).astype(float)
        if score_mode == "distance":
            endpoint_values = np.linalg.norm(points - reference, axis=1)
        else:
            endpoint_values = (points - reference) @ direction_unit
        point_index = int(np.argmax(endpoint_values))
        point = points[point_index]
        score = float(endpoint_values[point_index] - 0.05 * np.linalg.norm(point - previous))
        if score > best_score:
            best_score = score
            best_point = point

    if best_point is None:
        return None, float("nan")
    return (float(best_point[0]), float(best_point[1])), best_score


def valid_lk_result(
    status: np.ndarray | None,
    point: np.ndarray | None,
    metadata: VideoMetadata,
) -> bool:
    if status is None or point is None:
        return False
    if int(status.ravel()[0]) != 1:
        return False
    x, y = point.reshape(-1, 2)[0]
    return math.isfinite(float(x)) and math.isfinite(float(y)) and 0 <= x < metadata.width and 0 <= y < metadata.height


def write_tracking_row(
    writer: csv.DictWriter,
    video_id: str,
    frame_idx: int,
    fps: float,
    frame_height: int,
    point_cv_xy: tuple[float, float],
    reference_cv_xy: tuple[float, float],
    match_score: float,
    lk_error: float,
    status: str,
) -> None:
    tip_x, tip_cv_y = point_cv_xy
    ref_x, ref_cv_y = reference_cv_xy
    # Blender movie-clip marker coordinates use a bottom-left origin. OpenCV
    # tracks in top-left image coordinates, then exports in the Blender/manual
    # convention so existing CSV comparisons stay in the same coordinate system.
    tip_y = frame_height - tip_cv_y
    ref_y = frame_height - ref_cv_y
    writer.writerow(
        {
            "video_id": video_id,
            "frame": frame_idx,
            "time_s": frame_idx / fps,
            "tip_x_px": tip_x,
            "tip_y_px": tip_y,
            "clamp_x_px": ref_x,
            "clamp_y_px": ref_y,
            "displacement_x_px": tip_x - ref_x,
            "displacement_y_px": tip_y - ref_y,
            "match_score": match_score,
            "ncc_error": 1.0 - match_score if math.isfinite(match_score) else float("nan"),
            "lk_error": lk_error,
            "status": status,
        }
    )


def track_video(
    config: VideoConfig,
    output_dir: Path,
    *,
    limit_frames: int | None = None,
    progress_every: int = 5000,
) -> Path:
    cap = cv2.VideoCapture(str(config.video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {config.video_path}")

    metadata = read_metadata(cap)
    ok, first_frame = cap.read()
    if not ok:
        raise RuntimeError(f"Could not read first frame: {config.video_path}")

    min_dimension = min(metadata.width, metadata.height)
    lk_window = scaled_odd_size(
        min_dimension,
        float(config.params["lk_window_fraction"]),
        int(config.params["lk_window_min_px"]),
        int(config.params["lk_window_max_px"]),
    )
    template_radius = scaled_radius(
        min_dimension,
        float(config.params["template_radius_fraction"]),
        int(config.params["template_radius_min_px"]),
        int(config.params["template_radius_max_px"]),
    )
    search_radius = scaled_radius(
        min_dimension,
        float(config.params["template_search_fraction"]),
        int(config.params["template_search_min_px"]),
        int(config.params["template_search_max_px"]),
    )
    template_every_n = max(0, int(config.params["template_every_n"]))
    template_min_score = float(config.params["template_min_score"])
    max_template_correction = float(config.params["max_template_correction_px"])
    use_lk_prediction = bool(config.params.get("use_lk_prediction", True))
    method = str(config.params.get("method", "template"))
    endpoint_roi_radius = scaled_radius(
        min_dimension,
        float(config.params.get("endpoint_roi_radius_fraction", 0.16)),
        int(config.params.get("endpoint_roi_radius_min_px", 110)),
        int(config.params.get("endpoint_roi_radius_max_px", 190)),
    )
    endpoint_threshold_max = float(config.params.get("endpoint_threshold_max", 125.0))
    endpoint_min_area_px = int(config.params.get("endpoint_min_area_px", 5))
    endpoint_max_component_distance_fraction = float(
        config.params.get("endpoint_max_component_distance_fraction", 0.9)
    )
    endpoint_score_mode = str(config.params.get("endpoint_score_mode", "projection"))

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{config.video_id}_opencv.csv"

    first_gray = to_gray(first_frame)
    first_endpoint_gray = cv2.cvtColor(first_frame, cv2.COLOR_BGR2GRAY)
    tip_seed_cv_xy = (config.tip_seed_px[0], metadata.height - config.tip_seed_px[1])
    reference_cv_xy = (
        config.reference_seed_px[0],
        metadata.height - config.reference_seed_px[1],
    )
    template = crop_center(first_gray, tip_seed_cv_xy, template_radius)
    prev_gray = first_gray
    current_cv_xy = tip_seed_cv_xy
    endpoint_direction = np.array(tip_seed_cv_xy, dtype=float) - np.array(reference_cv_xy, dtype=float)
    endpoint_direction_norm = float(np.linalg.norm(endpoint_direction))
    if endpoint_direction_norm <= 0:
        raise RuntimeError(f"{config.video_id}: tip and reference seed coordinates are identical")
    endpoint_direction_unit = endpoint_direction / endpoint_direction_norm
    endpoint_offset: np.ndarray | None = None
    if method == "dark_endpoint":
        first_endpoint, _ = find_dark_endpoint(
            first_endpoint_gray,
            tip_seed_cv_xy,
            reference_cv_xy,
            endpoint_direction_unit,
            endpoint_roi_radius,
            endpoint_threshold_max,
            endpoint_min_area_px,
            endpoint_max_component_distance_fraction,
            endpoint_score_mode,
        )
        if first_endpoint is not None:
            endpoint_offset = np.array(tip_seed_cv_xy, dtype=float) - np.array(first_endpoint, dtype=float)

    lk_params = {
        "winSize": (lk_window, lk_window),
        "maxLevel": int(config.params["lk_max_level"]),
        "criteria": (
            cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
            30,
            0.01,
        ),
        "minEigThreshold": float(config.params["lk_min_eig_threshold"]),
    }

    start_time = time.monotonic()
    fields = [
        "video_id",
        "frame",
        "time_s",
        "tip_x_px",
        "tip_y_px",
        "clamp_x_px",
        "clamp_y_px",
        "displacement_x_px",
        "displacement_y_px",
        "match_score",
        "ncc_error",
        "lk_error",
        "status",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()

        write_tracking_row(
            writer,
            config.video_id,
            0,
            metadata.fps,
            metadata.height,
            current_cv_xy,
            reference_cv_xy,
            1.0,
            float("nan"),
            "seed",
        )

        frame_idx = 1
        prev_point = np.array([[current_cv_xy]], dtype=np.float32)
        while True:
            if limit_frames is not None and frame_idx >= limit_frames:
                break
            ok, frame = cap.read()
            if not ok:
                break

            if method == "dark_endpoint":
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                gray = to_gray(frame)
            lk_error = float("nan")
            if method == "dark_endpoint":
                endpoint_xy, match_score = find_dark_endpoint(
                    gray,
                    current_cv_xy,
                    reference_cv_xy,
                    endpoint_direction_unit,
                    endpoint_roi_radius,
                    endpoint_threshold_max,
                    endpoint_min_area_px,
                    endpoint_max_component_distance_fraction,
                    endpoint_score_mode,
                )
                if endpoint_xy is not None:
                    endpoint_point = np.array(endpoint_xy, dtype=float)
                    if endpoint_offset is None:
                        endpoint_offset = np.array(current_cv_xy, dtype=float) - endpoint_point
                    current_cv_xy = tuple(float(v) for v in endpoint_point + endpoint_offset)
                    status = "dark_endpoint"
                else:
                    match_score = float("nan")
                    status = "dark_endpoint_fallback"
            elif use_lk_prediction:
                next_point, lk_status, lk_error_arr = cv2.calcOpticalFlowPyrLK(
                    prev_gray,
                    gray,
                    prev_point,
                    None,
                    **lk_params,
                )

                if lk_error_arr is not None:
                    lk_error = float(lk_error_arr.ravel()[0])

                status = "lk"
                if valid_lk_result(lk_status, next_point, metadata):
                    predicted_xy = tuple(float(v) for v in next_point.reshape(-1, 2)[0])
                else:
                    predicted_xy = current_cv_xy
                    status = "template_fallback"
            else:
                predicted_xy = current_cv_xy
                status = "template_search"

            if method != "dark_endpoint":
                match_score = float("nan")
                should_template_match = (
                    status == "template_fallback"
                    or (template_every_n > 0 and frame_idx % template_every_n == 0)
                )
                if should_template_match:
                    matched_xy, match_score = template_match_center(
                        gray,
                        template,
                        predicted_xy,
                        template_radius,
                        search_radius,
                    )
                    if matched_xy is not None and math.isfinite(match_score):
                        correction = math.dist(predicted_xy, matched_xy)
                        if status in {"template_fallback", "template_search"} or (
                            match_score >= template_min_score and correction <= max_template_correction
                        ):
                            current_cv_xy = matched_xy
                            status = "template" if status in {"template_fallback", "template_search"} else "lk_template"
                        else:
                            current_cv_xy = predicted_xy
                    else:
                        current_cv_xy = predicted_xy
                else:
                    current_cv_xy = predicted_xy

            write_tracking_row(
                writer,
                config.video_id,
                frame_idx,
                metadata.fps,
                metadata.height,
                current_cv_xy,
                reference_cv_xy,
                match_score,
                lk_error,
                status,
            )

            prev_gray = gray
            prev_point = np.array([[current_cv_xy]], dtype=np.float32)
            frame_idx += 1
            if progress_every > 0 and frame_idx % progress_every == 0:
                elapsed = time.monotonic() - start_time
                print(
                    f"{config.video_id}: tracked {frame_idx} frames "
                    f"({elapsed:.1f}s, {frame_idx / max(elapsed, 1e-9):.1f} fps)"
                )

    cap.release()
    smoothing_window_s = float(config.params.get("smoothing_window_s", 0.0))
    if smoothing_window_s > 0:
        smooth_tracking_csv(output_path, metadata.fps, smoothing_window_s)
    elapsed = time.monotonic() - start_time
    print(
        f"{config.video_id}: wrote {output_path} "
        f"({frame_idx} frames, source metadata {metadata.width}x{metadata.height} "
        f"@ {metadata.fps:g} fps, reported {metadata.reported_frame_count} frames, {elapsed:.1f}s)"
    )
    return output_path


def smooth_tracking_csv(output_path: Path, fps: float, smoothing_window_s: float) -> None:
    df = pd.read_csv(output_path)
    if df.empty:
        return

    window = int(round(smoothing_window_s * fps))
    if window < 3:
        return
    if window % 2 == 0:
        window += 1

    df["raw_tip_x_px"] = df["tip_x_px"]
    df["raw_tip_y_px"] = df["tip_y_px"]
    df["tip_x_px"] = df["raw_tip_x_px"].rolling(window, center=True, min_periods=1).median()
    df["tip_y_px"] = df["raw_tip_y_px"].rolling(window, center=True, min_periods=1).median()

    # Preserve the exact manual seed on frame 0; the clamp/reference remains fixed throughout.
    df.loc[df.index[0], "tip_x_px"] = df.loc[df.index[0], "raw_tip_x_px"]
    df.loc[df.index[0], "tip_y_px"] = df.loc[df.index[0], "raw_tip_y_px"]
    df["displacement_x_px"] = df["tip_x_px"] - df["clamp_x_px"]
    df["displacement_y_px"] = df["tip_y_px"] - df["clamp_y_px"]
    df.to_csv(output_path, index=False)


def load_manual_csv(path: Path, fps: float, displacement_scale: float) -> pd.DataFrame:
    manual = pd.read_csv(
        path,
        header=None,
        names=["manual_time_s", "manual_displacement_x_px", "manual_displacement_y_px"],
        skipinitialspace=True,
    )
    manual["manual_displacement_x_px"] *= displacement_scale
    manual["manual_displacement_y_px"] *= displacement_scale
    manual["frame"] = np.rint(manual["manual_time_s"] * fps).astype(int)
    manual = manual.drop_duplicates("frame", keep="first")
    return manual


def downsample_for_plot(df: pd.DataFrame, max_points: int = MAX_PLOT_POINTS) -> pd.DataFrame:
    stride = max(1, len(df) // max_points)
    return df.iloc[::stride].copy()


def relay_event_times(max_time_s: float, first_s: float, period_s: float) -> list[float]:
    if period_s <= 0 or max_time_s < first_s:
        return []

    events = []
    event = first_s
    while event <= max_time_s + 1e-9:
        events.append(event)
        event += period_s
    return events


def relay_window_dataframe(
    merged: pd.DataFrame,
    first_s: float,
    period_s: float,
    window_s: float,
) -> pd.DataFrame:
    if window_s <= 0:
        return pd.DataFrame()

    pieces = []
    max_time_s = float(merged["time_s"].max())
    for event_s in relay_event_times(max_time_s, first_s, period_s):
        window = merged[
            (merged["time_s"] >= event_s - window_s)
            & (merged["time_s"] <= event_s + window_s)
        ].copy()
        if window.empty:
            continue
        window["relay_event_s"] = event_s
        window["relay_time_s"] = window["time_s"] - event_s
        pieces.append(window)

    if not pieces:
        return pd.DataFrame()
    return pd.concat(pieces, ignore_index=True)


def radial_summary(df: pd.DataFrame, prefix: str) -> dict[str, Any]:
    if df.empty:
        return {
            f"{prefix}_frames": 0,
            f"{prefix}_radial_rmse_px": float("nan"),
            f"{prefix}_radial_p95_px": float("nan"),
            f"{prefix}_radial_max_px": float("nan"),
        }
    return {
        f"{prefix}_frames": int(len(df)),
        f"{prefix}_radial_rmse_px": float(np.sqrt(np.mean(np.square(df["error_r_px"])))),
        f"{prefix}_radial_p95_px": float(df["error_r_px"].quantile(0.95)),
        f"{prefix}_radial_max_px": float(df["error_r_px"].max()),
    }


def compare_tracking(
    config: VideoConfig,
    auto_csv: Path,
    comparison_dir: Path,
) -> dict[str, Any] | None:
    if config.manual_csv is None or not config.manual_csv.exists():
        print(f"{config.video_id}: no manual CSV available, skipping comparison")
        return None

    auto = pd.read_csv(auto_csv)
    if auto.empty:
        raise RuntimeError(f"No automated rows in {auto_csv}")
    fps = 1.0 / float(auto["time_s"].iloc[1] - auto["time_s"].iloc[0]) if len(auto) > 1 else 0.0
    manual = load_manual_csv(config.manual_csv, fps, config.manual_displacement_scale)

    merged = auto.merge(manual, on="frame", how="inner")
    if merged.empty:
        raise RuntimeError(f"No overlapping frames for {config.video_id}")

    merged["error_x_px"] = merged["displacement_x_px"] - merged["manual_displacement_x_px"]
    merged["error_y_px"] = merged["displacement_y_px"] - merged["manual_displacement_y_px"]
    merged["error_r_px"] = np.hypot(merged["error_x_px"], merged["error_y_px"])
    relay_first_s = float(config.params.get("relay_event_first_s", DEFAULT_RELAY_EVENT_FIRST_S))
    relay_period_s = float(config.params.get("relay_event_period_s", DEFAULT_RELAY_EVENT_PERIOD_S))
    relay_window_s = float(config.params.get("relay_event_window_s", DEFAULT_RELAY_EVENT_WINDOW_S))
    event_times = relay_event_times(float(merged["time_s"].max()), relay_first_s, relay_period_s)
    relay_windows = relay_window_dataframe(merged, relay_first_s, relay_period_s, relay_window_s)

    comparison_dir.mkdir(parents=True, exist_ok=True)
    comparison_csv = comparison_dir / f"{config.video_id}_comparison.csv"
    merged.to_csv(comparison_csv, index=False)

    metrics = {
        "video_id": config.video_id,
        "frames_compared": int(len(merged)),
        "auto_frames": int(len(auto)),
        "manual_frames": int(len(manual)),
        "mean_abs_error_x_px": float(merged["error_x_px"].abs().mean()),
        "mean_abs_error_y_px": float(merged["error_y_px"].abs().mean()),
        "rmse_x_px": float(np.sqrt(np.mean(np.square(merged["error_x_px"])))),
        "rmse_y_px": float(np.sqrt(np.mean(np.square(merged["error_y_px"]))),
        ),
        "radial_rmse_px": float(np.sqrt(np.mean(np.square(merged["error_r_px"])))),
        "radial_p50_px": float(merged["error_r_px"].quantile(0.50)),
        "radial_p95_px": float(merged["error_r_px"].quantile(0.95)),
        "radial_max_px": float(merged["error_r_px"].max()),
        "bias_x_px": float(merged["error_x_px"].mean()),
        "bias_y_px": float(merged["error_y_px"].mean()),
        "mean_match_score": float(merged["match_score"].mean(skipna=True)),
        "mean_lk_error": float(merged["lk_error"].mean(skipna=True)),
        "relay_event_first_s": relay_first_s,
        "relay_event_period_s": relay_period_s,
        "relay_window_s": relay_window_s,
        "relay_events": int(relay_windows["relay_event_s"].nunique()) if not relay_windows.empty else 0,
        "comparison_csv": str(comparison_csv),
    }
    metrics.update(radial_summary(relay_windows, "relay_window"))

    plot_path = comparison_dir / f"{config.video_id}_comparison.svg"
    plot_comparison(merged, metrics, plot_path, event_times)
    metrics["comparison_svg"] = str(plot_path)
    if not relay_windows.empty:
        relay_plot_path = comparison_dir / f"{config.video_id}_relay_windows.svg"
        plot_relay_windows(relay_windows, metrics, relay_plot_path)
        metrics["relay_window_svg"] = str(relay_plot_path)
    print(
        f"{config.video_id}: radial RMSE {metrics['radial_rmse_px']:.3f}px, "
        f"p95 {metrics['radial_p95_px']:.3f}px over {metrics['frames_compared']} frames; "
        f"relay-window RMSE {metrics['relay_window_radial_rmse_px']:.3f}px"
    )
    return metrics


def plot_comparison(
    merged: pd.DataFrame,
    metrics: dict[str, Any],
    output_path: Path,
    event_times: list[float],
) -> None:
    plot_df = downsample_for_plot(merged)
    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    fig.suptitle(
        f"{metrics['video_id']} full run OpenCV vs Blender manual "
        f"(radial RMSE {metrics['radial_rmse_px']:.2f}px)"
    )

    axes[0].plot(
        plot_df["time_s"],
        plot_df["manual_displacement_x_px"],
        label="manual x",
        linewidth=1.2,
    )
    axes[0].plot(
        plot_df["time_s"],
        plot_df["displacement_x_px"],
        label="opencv x",
        linewidth=1.0,
        alpha=0.8,
    )
    axes[0].set_ylabel("x displacement px")
    axes[0].legend(loc="best")

    axes[1].plot(
        plot_df["time_s"],
        plot_df["manual_displacement_y_px"],
        label="manual y",
        linewidth=1.2,
    )
    axes[1].plot(
        plot_df["time_s"],
        plot_df["displacement_y_px"],
        label="opencv y",
        linewidth=1.0,
        alpha=0.8,
    )
    axes[1].set_ylabel("y displacement px")
    axes[1].legend(loc="best")

    axes[2].plot(plot_df["time_s"], plot_df["error_r_px"], linewidth=1.0)
    axes[2].set_ylabel("radial error px")
    axes[2].set_xlabel("time s")

    for axis in axes:
        for event_s in event_times:
            axis.axvline(event_s, color="0.75", linewidth=0.6, alpha=0.55, zorder=0)
        axis.grid(True, alpha=0.25)
        axis.set_xlim(float(merged["time_s"].min()), float(merged["time_s"].max()))

    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)


def plot_relay_windows(relay_windows: pd.DataFrame, metrics: dict[str, Any], output_path: Path) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    fig.suptitle(
        f"{metrics['video_id']} relay-edge windows "
        f"(+/-{metrics['relay_window_s']:.1f}s, RMSE "
        f"{metrics['relay_window_radial_rmse_px']:.2f}px)"
    )

    for index, (_, window) in enumerate(relay_windows.groupby("relay_event_s")):
        manual_label = "manual" if index == 0 else None
        opencv_label = "opencv" if index == 0 else None
        error_label = "radial error" if index == 0 else None
        axes[0].plot(
            window["relay_time_s"],
            window["manual_displacement_y_px"],
            color="0.35",
            linewidth=0.8,
            alpha=0.25,
            label=manual_label,
        )
        axes[0].plot(
            window["relay_time_s"],
            window["displacement_y_px"],
            color="#1f77b4",
            linewidth=0.8,
            alpha=0.35,
            label=opencv_label,
        )
        axes[1].plot(
            window["relay_time_s"],
            window["manual_displacement_x_px"],
            color="0.35",
            linewidth=0.8,
            alpha=0.25,
        )
        axes[1].plot(
            window["relay_time_s"],
            window["displacement_x_px"],
            color="#1f77b4",
            linewidth=0.8,
            alpha=0.35,
        )
        axes[2].plot(
            window["relay_time_s"],
            window["error_r_px"],
            color="#d62728",
            linewidth=0.8,
            alpha=0.3,
            label=error_label,
        )

    axes[0].set_ylabel("y displacement px")
    axes[1].set_ylabel("x displacement px")
    axes[2].set_ylabel("radial error px")
    axes[2].set_xlabel("time from relay edge s")
    axes[0].legend(loc="best")
    axes[2].legend(loc="best")

    for axis in axes:
        axis.axvline(0.0, color="0.2", linewidth=0.9, alpha=0.8)
        axis.set_xlim(-float(metrics["relay_window_s"]), float(metrics["relay_window_s"]))
        axis.grid(True, alpha=0.25)

    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)


def write_summary(metrics: list[dict[str, Any]], comparison_dir: Path) -> Path | None:
    if not metrics:
        return None
    summary_path = comparison_dir / "summary.csv"
    pd.DataFrame(metrics).to_csv(summary_path, index=False)
    print(f"wrote {summary_path}")
    return summary_path


def write_relay_window_summary(metrics: list[dict[str, Any]], comparison_dir: Path) -> Path | None:
    rows = []
    for item in metrics:
        if int(item.get("relay_window_frames", 0)) <= 0:
            continue
        rows.append(
            {
                "video_id": item["video_id"],
                "relay_events": item["relay_events"],
                "relay_window_s": item["relay_window_s"],
                "frames": item["relay_window_frames"],
                "radial_rmse_px": item["relay_window_radial_rmse_px"],
                "radial_p95_px": item["relay_window_radial_p95_px"],
                "radial_max_px": item["relay_window_radial_max_px"],
                "relay_window_svg": item.get("relay_window_svg", ""),
            }
        )
    if not rows:
        return None
    summary_path = comparison_dir / "event_window_summary.csv"
    pd.DataFrame(rows).to_csv(summary_path, index=False)
    print(f"wrote {summary_path}")
    return summary_path


def cv_point_from_blender_xy(x_px: float, y_px: float, frame_height: int, scale: float) -> tuple[int, int]:
    return int(round(x_px * scale)), int(round((frame_height - y_px) * scale))


def preview_output_path(
    preview_dir: Path,
    video_id: str,
    start_s: float | None,
    end_s: float | None,
) -> Path:
    if start_s is None and end_s is None:
        return preview_dir / f"{video_id}_tracking_preview.mp4"

    start_label = "start" if start_s is None else f"{start_s:g}s"
    end_label = "end" if end_s is None else f"{end_s:g}s"
    return preview_dir / f"{video_id}_tracking_preview_{start_label}-{end_label}.mp4"


def draw_cross(
    frame: np.ndarray,
    point: tuple[int, int],
    color: tuple[int, int, int],
    radius: int,
    thickness: int,
) -> None:
    x, y = point
    cv2.line(frame, (x - radius, y - radius), (x + radius, y + radius), color, thickness, cv2.LINE_AA)
    cv2.line(frame, (x - radius, y + radius), (x + radius, y - radius), color, thickness, cv2.LINE_AA)


def write_preview_video(
    config: VideoConfig,
    tracking_csv: Path,
    preview_dir: Path,
    *,
    start_s: float | None = None,
    end_s: float | None = None,
    frame_stride: int = 2,
    max_width: int = 1280,
    trail_s: float = 2.0,
) -> Path:
    if not tracking_csv.exists():
        raise RuntimeError(f"Tracking CSV does not exist: {tracking_csv}")
    if frame_stride < 1:
        raise RuntimeError("--preview-stride must be >= 1")
    if max_width < 1:
        raise RuntimeError("--preview-max-width must be >= 1")

    cap = cv2.VideoCapture(str(config.video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {config.video_path}")
    metadata = read_metadata(cap)

    tracking = pd.read_csv(tracking_csv).set_index("frame")
    if tracking.empty:
        raise RuntimeError(f"No tracking rows in {tracking_csv}")

    manual_by_frame: pd.DataFrame | None = None
    if config.manual_csv is not None and config.manual_csv.exists():
        manual_by_frame = load_manual_csv(
            config.manual_csv,
            metadata.fps,
            config.manual_displacement_scale,
        ).set_index("frame")

    start_frame = 0 if start_s is None else max(0, int(round(start_s * metadata.fps)))
    if end_s is None:
        end_frame = metadata.reported_frame_count - 1 if metadata.reported_frame_count > 0 else int(tracking.index.max())
    else:
        end_frame = int(round(end_s * metadata.fps))
        if metadata.reported_frame_count > 0:
            end_frame = min(end_frame, metadata.reported_frame_count - 1)
    end_frame = min(end_frame, int(tracking.index.max()))
    if end_frame < start_frame:
        raise RuntimeError(f"Preview end frame {end_frame} is before start frame {start_frame}")

    scale = min(1.0, max_width / metadata.width)
    output_width = max(2, int(round(metadata.width * scale)))
    output_height = max(2, int(round(metadata.height * scale)))
    output_width -= output_width % 2
    output_height -= output_height % 2
    output_fps = metadata.fps / frame_stride

    preview_dir.mkdir(parents=True, exist_ok=True)
    output_path = preview_output_path(preview_dir, config.video_id, start_s, end_s)
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        output_fps,
        (output_width, output_height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Could not open preview writer: {output_path}")

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    trail_points: list[tuple[int, int]] = []
    max_trail_points = max(2, int(round(trail_s * metadata.fps / frame_stride)))
    written = 0
    frame_idx = start_frame
    while frame_idx <= end_frame:
        ok, frame = cap.read()
        if not ok:
            break
        if (frame_idx - start_frame) % frame_stride == 0 and frame_idx in tracking.index:
            output_frame = cv2.resize(frame, (output_width, output_height), interpolation=cv2.INTER_AREA)
            row = tracking.loc[frame_idx]
            tip = cv_point_from_blender_xy(row["tip_x_px"], row["tip_y_px"], metadata.height, scale)
            clamp = cv_point_from_blender_xy(row["clamp_x_px"], row["clamp_y_px"], metadata.height, scale)
            trail_points.append(tip)
            if len(trail_points) > max_trail_points:
                trail_points = trail_points[-max_trail_points:]

            if len(trail_points) > 1:
                cv2.polylines(output_frame, [np.array(trail_points, dtype=np.int32)], False, (255, 170, 0), 2)
            cv2.line(output_frame, clamp, tip, (0, 220, 220), 1, cv2.LINE_AA)
            cv2.circle(output_frame, tip, 7, (255, 255, 0), 2, cv2.LINE_AA)
            draw_cross(output_frame, clamp, (0, 255, 0), 8, 2)

            if manual_by_frame is not None and frame_idx in manual_by_frame.index:
                manual_row = manual_by_frame.loc[frame_idx]
                manual_tip_x = row["clamp_x_px"] + manual_row["manual_displacement_x_px"]
                manual_tip_y = row["clamp_y_px"] + manual_row["manual_displacement_y_px"]
                manual_tip = cv_point_from_blender_xy(manual_tip_x, manual_tip_y, metadata.height, scale)
                draw_cross(output_frame, manual_tip, (0, 128, 255), 7, 2)

            label = (
                f"{config.video_id}  frame {frame_idx}  t={frame_idx / metadata.fps:.2f}s  "
                f"{row['status']}"
            )
            cv2.putText(output_frame, label, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(output_frame, label, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(output_frame, "cyan circle: OpenCV tip   green x: clamp   orange x: manual tip", (16, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(output_frame, "cyan circle: OpenCV tip   green x: clamp   orange x: manual tip", (16, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1, cv2.LINE_AA)

            writer.write(output_frame)
            written += 1
        frame_idx += 1

    cap.release()
    writer.release()
    if written == 0:
        raise RuntimeError(f"No preview frames were written for {config.video_id}")
    print(
        f"{config.video_id}: wrote {output_path} "
        f"({written} frames, {output_width}x{output_height} @ {output_fps:g} fps)"
    )
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track actuator tip motion with OpenCV and compare against Blender manual CSVs."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--comparison-dir", type=Path, default=DEFAULT_OUTPUT_DIR / "comparisons")
    parser.add_argument("--video-id", action="append", help="Video id to process. Repeat or omit for all.")
    parser.add_argument("--no-track", action="store_true", help="Only compare existing automated CSVs.")
    parser.add_argument("--no-compare", action="store_true", help="Only write automated tracking CSVs.")
    parser.add_argument("--write-preview", action="store_true", help="Write annotated MP4 tracking previews.")
    parser.add_argument("--preview-dir", type=Path, default=DEFAULT_OUTPUT_DIR / "previews")
    parser.add_argument("--preview-stride", type=int, default=2, help="Write one preview frame per N source frames.")
    parser.add_argument("--preview-max-width", type=int, default=1280)
    parser.add_argument("--preview-start-s", type=float)
    parser.add_argument("--preview-end-s", type=float)
    parser.add_argument("--preview-trail-s", type=float, default=2.0)
    parser.add_argument("--limit-frames", type=int, help="Debug option: stop after this many frames.")
    parser.add_argument("--progress-every", type=int, default=5000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    configs = load_config(args.config.resolve(), repo_root)
    if args.video_id:
        selected = set(args.video_id)
        configs = [config for config in configs if config.video_id in selected]
        missing = selected - {config.video_id for config in configs}
        if missing:
            raise SystemExit(f"Unknown video id(s): {', '.join(sorted(missing))}")

    metrics = []
    for config in configs:
        auto_csv = args.output_dir / f"{config.video_id}_opencv.csv"
        if not args.no_track:
            auto_csv = track_video(
                config,
                args.output_dir,
                limit_frames=args.limit_frames,
                progress_every=args.progress_every,
            )
        if not args.no_compare:
            result = compare_tracking(config, auto_csv, args.comparison_dir)
            if result:
                metrics.append(result)
        if args.write_preview:
            write_preview_video(
                config,
                auto_csv,
                args.preview_dir,
                start_s=args.preview_start_s,
                end_s=args.preview_end_s,
                frame_stride=args.preview_stride,
                max_width=args.preview_max_width,
                trail_s=args.preview_trail_s,
            )

    if not args.no_compare:
        write_summary(metrics, args.comparison_dir)
        write_relay_window_summary(metrics, args.comparison_dir)


if __name__ == "__main__":
    main()
