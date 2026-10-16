# -*- coding: utf-8 -*-
"""NexusGapFill public single-file edition 0.4.0, Nexus 2.16 / Python 3.7.

使用方法：
1. 将脚本和试验副本放在全英文 ASCII 路径；启用一个已标定 VSK 受试者，设置 ROI。
2. Python 页选择本文件，参数留空，点击 Run Script。
3. Current Pipeline 菜单 Refresh List，选择输出的 NGF_SINGLE_...，运行。

只需保存本 .py 文件。配置、流程和运行状态自动生成到本机缓存；
原生流程和本脚本通过外层 Pipeline 顺序运行，禁止嵌套 RunPipeline。
备用算法的每一帧均标 LOW；有值不等于真实准确。保留原已有值和原生结果。
写出试验名._NGF.c3d、_NGF.provenance.csv 和 _NGF.summary.json。
不自动识别亮灯、不自动计算关节角；至少出现一次的动态物理标记是目标。
需要本机 Nexus Python、NumPy、viconnexusapi 和 pyCGM2 的 BTK。

低维 Kalman/RTS 为独立实现，参考 Burke & Lasenby (2016),
doi:10.1016/j.jbiomech.2016.04.016；不声称与 MoGapFill 数值等价。
"""
# SPDX-License-Identifier: MIT
import argparse
import traceback

# 可修改以下参数，然后重新运行空参数准备步骤。
CONFIG = {'spline_seconds': 0.05,
 'rigid_seconds': 0.25,
 'pattern_seconds': 0.1,
 'kinematic_seconds': 0.25,
 'allow_low_reliability_completion': True,
 'use_kalman': True,
 'use_rigid_extension': True,
 'use_ridge': True,
 'kalman_min_complete_frames': 20,
 'kalman_max_dimensions': 8,
 'kalman_energy': 0.995,
 'observation_noise_mm': 1.5,
 'maximum_rigid_distance_cv': 0.1,
 'maximum_rigid_fit_rmse_mm': 20.0,
 'minimum_geometry_ratio': 0.001,
 'invalidate_old_model_outputs': True,
 'protected_roots': [],
 'cache_root': ''}

# ===== Fallback algorithms =====
"""Fallback estimates, kept separate from Nexus and from original observations.

The low-dimensional random-walk Kalman/RTS smoother follows the approach of
Burke & Lasenby (2016), doi:10.1016/j.jbiomech.2016.04.016. This is an independent
implementation, not a claim of numerical equivalence to MoGapFill or pyCGM2.
Every fallback estimate in this first release is labelled LOW, even when smooth.
"""
import itertools
import numpy as np


DEFAULTS = {
    "spline_seconds": 0.05,
    "rigid_seconds": 0.25,
    "pattern_seconds": 0.10,
    "kinematic_seconds": 0.25,
    "kalman_min_complete_frames": 20,
    "kalman_max_dimensions": 8,
    "kalman_energy": 0.995,
    "observation_noise_mm": 1.5,
    "maximum_rigid_distance_cv": 0.10,
    "maximum_rigid_offset_sd_mm": 15.0,
    "maximum_rigid_fit_rmse_mm": 20.0,
    "minimum_geometry_ratio": 0.001,
    "allow_low_reliability_completion": True,
    "use_kalman": True,
    "use_rigid_extension": True,
    "use_ridge": True,
}


def runs(mask):
    edges = np.diff(np.r_[False, np.asarray(mask, bool), False].astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def array_frame_origin(array_length, trial_range):
    """Nexus can return frame-1 padded arrays, or only the loaded trial range."""
    first, last = map(int, trial_range)
    if array_length == last:
        return 1
    if array_length == last - first + 1:
        return first
    raise ValueError("Cannot establish frame mapping: length=%s, range=%s" %
                     (array_length, trial_range))


def roi_mask(length, origin, roi):
    frames = np.arange(length, dtype=np.int64) + int(origin)
    return (frames >= int(roi[0])) & (frames <= int(roi[1]))


def _rotation_fit(reference, current, minimum_ratio):
    a0, b0 = reference.mean(axis=0), current.mean(axis=0)
    a, b = reference - a0, current - b0
    shape = np.linalg.svd(a, compute_uv=False)
    if len(shape) < 2 or shape[0] < 1e-8 or shape[1] / shape[0] < minimum_ratio:
        return None
    u, _, vt = np.linalg.svd(a.T.dot(b), full_matrices=False)
    correction = np.eye(3)
    correction[2, 2] = np.sign(np.linalg.det(u.dot(vt)))
    rotation = u.dot(correction).dot(vt)  # row-vector convention
    error = float(np.sqrt(np.mean(np.sum((a.dot(rotation) - b) ** 2, axis=1))))
    return rotation, a0, b0, error


def eligible_rigid_groups(xyz, valid, markers, segments, config=None):
    """VSK membership is a candidate, not proof that a marker group is rigid."""
    cfg = dict(DEFAULTS, **(config or {}))
    lookup = {m: i for i, m in enumerate(markers)}
    accepted, rejected = {}, {}
    for segment, names in segments.items():
        names = sorted(set(m for m in names if m in lookup))
        if len(names) < 4:
            rejected[segment] = "fewer_than_four_available_markers"
            continue
        ids = [lookup[m] for m in names]
        distances = []
        for i, j in itertools.combinations(ids, 2):
            ok = valid[:, i] & valid[:, j]
            if ok.sum() < 10:
                continue
            d = np.linalg.norm(xyz[ok, i] - xyz[ok, j], axis=1)
            med = float(np.median(d))
            if med > 1e-5:
                distances.append(float(np.std(d) / med))
        if not distances or max(distances) > cfg["maximum_rigid_distance_cv"]:
            rejected[segment] = "insufficient_overlap_or_nonrigid_distance_variation"
            continue
        accepted[segment] = names
    return accepted, rejected


def low_dimensional_kalman(xyz, valid, config=None):
    """Fit on complete *input* frames; assimilate input observations only.

    Native-filled samples are deliberately not supplied as observed training
    truth. Returned variances are model variances, not calibrated error bounds.
    """
    cfg = dict(DEFAULTS, **(config or {}))
    data = np.asarray(xyz, float).reshape(len(xyz), -1)
    observed = np.repeat(np.asarray(valid, bool), 3, axis=1) & np.isfinite(data)
    complete = np.flatnonzero(observed.all(axis=1))
    if len(complete) < cfg["kalman_min_complete_frames"]:
        raise ValueError("insufficient_common_complete_input_frames")
    training = data[complete]
    mean = training.mean(axis=0)
    _, singular, vt = np.linalg.svd(training - mean, full_matrices=False)
    energy = singular ** 2
    if energy.sum() < 1e-10:
        prediction = np.tile(mean, (len(data), 1))
        return prediction.reshape(xyz.shape), np.zeros_like(xyz)
    dimension = int(np.searchsorted(np.cumsum(energy) / energy.sum(),
                                    cfg["kalman_energy"]) + 1)
    dimension = max(1, min(dimension, cfg["kalman_max_dimensions"], len(complete)-1))
    basis = vt[:dimension].T
    latent = (training - mean).dot(basis)
    intervals = np.maximum(np.diff(complete), 1).astype(float)
    differences = np.diff(latent, axis=0) / np.sqrt(intervals[:, None])
    r = float(cfg["observation_noise_mm"]) ** 2
    q = np.diag(np.maximum(np.mean(differences ** 2, axis=0), r * 0.01))
    eye = np.eye(dimension)
    state = np.zeros(dimension)
    covariance = eye * 1e6
    means = np.empty((len(data), dimension))
    covariances = np.empty((len(data), dimension, dimension))
    for frame in range(len(data)):
        covariance = covariance + q
        use = observed[frame]
        if use.any():
            h = basis[use]
            covariance = np.linalg.inv(np.linalg.inv(covariance) + h.T.dot(h) / r)
            state = state + covariance.dot(h.T).dot(data[frame, use] - mean[use] - h.dot(state)) / r
        means[frame], covariances[frame] = state, covariance
    smooth = means.copy()
    smooth_covariance = covariances.copy()
    for frame in range(len(data)-2, -1, -1):
        gain = np.linalg.solve((covariances[frame] + q).T, covariances[frame].T).T
        smooth[frame] += gain.dot(smooth[frame+1] - means[frame])
        smooth_covariance[frame] += gain.dot(
            smooth_covariance[frame+1] - covariances[frame] - q).dot(gain.T)
    prediction = smooth.dot(basis.T) + mean
    variance = np.maximum(np.einsum("ij,tjk,ik->ti", basis, smooth_covariance, basis), 0)
    if not np.isfinite(prediction).all():
        raise ValueError("nonfinite_kalman_prediction")
    return prediction.reshape(xyz.shape), variance.reshape(xyz.shape)


def _rigid_extension(original, original_valid, current, current_valid, target,
                     donors, start, end, cfg):
    if len(donors) < 3:
        return None
    # Calibration requires simultaneous input data for the target and donors.
    reference_frames = np.flatnonzero(original_valid[:, target] &
                                     original_valid[:, donors].all(axis=1))
    if not len(reference_frames):
        return None
    nearest = np.argmin(np.minimum(abs(reference_frames-start), abs(reference_frames-end)))
    reference_frame = reference_frames[nearest]
    reference = original[reference_frame, donors]
    available = np.flatnonzero(current_valid[start:end, donors].all(axis=1)) + start
    output = np.full((end-start, 3), np.nan)
    residuals = []
    for frame in available:
        fitted = _rotation_fit(reference, current[frame, donors], cfg["minimum_geometry_ratio"])
        if fitted is None:
            continue
        rotation, a0, b0, error = fitted
        if error <= cfg["maximum_rigid_fit_rmse_mm"]:
            output[frame-start] = (original[reference_frame, target] - a0).dot(rotation) + b0
            residuals.append(error)
    if not np.isfinite(output).any():
        return None
    return output, {"reference_index": int(reference_frame),
                    "donor_indices": list(map(int, donors)),
                    "rigid_fit_rmse_mm_max": float(max(residuals))}


def _ridge_prediction(original, original_valid, current, current_valid, target,
                      start, end, preferred):
    candidates = [j for j in range(original.shape[1]) if j != target and
                  current_valid[start:end, j].all()]
    candidates.sort(key=lambda j: (j not in preferred, -int(original_valid[:, j].sum())))
    for count in (min(4, len(candidates)), min(2, len(candidates)), 1):
        if count < 1:
            continue
        donors = candidates[:count]
        good = original_valid[:, target] & original_valid[:, donors].all(axis=1)
        if good.sum() < max(12, 3*count+2):
            continue
        a = original[good][:, donors].reshape(int(good.sum()), -1)
        b = original[good, target]
        center = a.mean(axis=0)
        scale = np.maximum(a.std(axis=0), 1.0)
        z = np.c_[(a-center)/scale, np.ones(len(a))]
        penalty = np.eye(z.shape[1]) * 0.1
        penalty[-1, -1] = 0.0
        beta = np.linalg.solve(z.T.dot(z)+penalty, z.T.dot(b))
        test = current[start:end, donors].reshape(end-start, -1)
        prediction = np.c_[(test-center)/scale, np.ones(len(test))].dot(beta)
        if np.isfinite(prediction).all():
            return prediction, {"donor_indices": donors, "fit_input_frames": int(good.sum())}
    return None


def _relative_interpolation(original, original_valid, current, current_valid,
                            target, start, end, preferred):
    known = np.flatnonzero(original_valid[:, target])
    if not len(known):
        return None
    donors = [j for j in range(original.shape[1]) if j != target and
              current_valid[start:end, j].all()]
    donors.sort(key=lambda j: (j not in preferred, -int(original_valid[:, j].sum())))
    for donor in donors:
        common = np.flatnonzero(original_valid[:, target] & original_valid[:, donor])
        if not len(common):
            continue
        delta = original[common, target] - original[common, donor]
        frames = np.arange(start, end)
        offset = np.column_stack([np.interp(frames, common, delta[:, a]) for a in range(3)])
        return current[start:end, donor] + offset, {"donor_indices": [donor]}
    frames = np.arange(start, end)
    values = np.column_stack([np.interp(frames, known, original[known, target, a])
                              for a in range(3)])
    method = "temporal_linear_estimate" if start > known[0] and end <= known[-1] else "nearest_input_hold"
    return values, {"donor_indices": [], "method": method}


def _anchor_prediction(prediction, current, valid, target, start, end):
    """Correct model bias at available boundaries without editing those values."""
    values = prediction[start:end, target].copy()
    left = np.flatnonzero(valid[:start, target])
    right = np.flatnonzero(valid[end:, target]) + end
    if len(left) and len(right):
        a, b = int(left[-1]), int(right[0])
        da = current[a, target] - prediction[a, target]
        db = current[b, target] - prediction[b, target]
        u = (np.arange(start, end)-a).astype(float)/(b-a)
        values += (1-u[:, None])*da + u[:, None]*db
    elif len(right):
        b = int(right[0])
        values += current[b, target] - prediction[b, target]
    elif len(left):
        a = int(left[-1])
        values += current[a, target] - prediction[a, target]
    return values


def complete_remaining(original, original_valid, after_native, native_valid,
                       markers, segments, roi, config=None, native_methods=None):
    """Fill only still-missing ROI samples. No fallback can replace native data."""
    cfg = dict(DEFAULTS, **(config or {}))
    original, current = np.asarray(original, float), np.array(after_native, float, copy=True)
    original_valid = np.asarray(original_valid, bool) & np.isfinite(original).all(axis=2)
    native_valid = np.asarray(native_valid, bool) & np.isfinite(current).all(axis=2)
    if current.shape != original.shape or current.shape != original_valid.shape + (3,):
        raise ValueError("coordinate/mask shape mismatch")
    if len(markers) != current.shape[1] or len(set(markers)) != len(markers):
        raise ValueError("duplicate marker names or incorrect marker count")
    roi = np.asarray(roi, bool)
    if roi.shape != (len(current),):
        raise ValueError("incorrect ROI mask")
    # Original observations and every out-of-ROI sample are immutable.
    current[original_valid] = original[original_valid]
    native_valid[original_valid] = True
    current[~roi] = original[~roi]
    native_valid[~roi] = original_valid[~roi]
    frozen, frozen_valid = current.copy(), native_valid.copy()
    valid = native_valid.copy()
    methods = np.full(valid.shape, "missing", dtype="U64")
    methods[original_valid] = "input_existing"
    native_added = valid & ~original_valid
    methods[native_added] = "nexus_native"
    if native_methods is not None:
        native_methods = np.asarray(native_methods)
        methods[native_added] = native_methods[native_added]
    low = np.zeros_like(valid)
    model_sd = np.full(valid.shape, np.nan)
    details, warnings = [], []
    lookup = {m: i for i, m in enumerate(markers)}
    grouped = {name: [lookup[m] for m in names if m in lookup]
               for name, names in segments.items()}
    rigid_groups, _ = eligible_rigid_groups(original, original_valid, markers, segments, cfg)

    def apply(target, start, end, values, method, info):
        indices = np.arange(start, end)
        use = (~valid[indices, target]) & roi[indices] & np.isfinite(values).all(axis=1)
        indices = indices[use]
        if not len(indices):
            return
        current[indices, target] = values[use]
        valid[indices, target] = True
        methods[indices, target] = method
        low[indices, target] = True
        details.append(dict(marker=markers[target], start_index=int(indices[0]),
                            end_index=int(indices[-1]), indices=indices.tolist(),
                            method=method, reliability="LOW", information=info))

    if not cfg["allow_low_reliability_completion"]:
        return dict(xyz=current, valid=valid, methods=methods, low=low,
                    model_sd=model_sd, details=details, warnings=warnings)
    if cfg["use_rigid_extension"]:
        for names in rigid_groups.values():
            ids = [lookup[m] for m in names]
            for target in ids:
                donors = [j for j in ids if j != target]
                for start, end in runs(~valid[:, target] & roi):
                    result = _rigid_extension(original, original_valid, frozen, frozen_valid,
                                              target, donors, start, end, cfg)
                    if result is not None:
                        apply(target, start, end, result[0], "rigid_model_extension", result[1])
    prediction, variance = None, None
    if cfg["use_kalman"] and (~valid & roi[:, None]).any():
        try:
            prediction, variance = low_dimensional_kalman(original, original_valid, cfg)
        except (ValueError, np.linalg.LinAlgError) as error:
            warnings.append("global_kalman: " + str(error))
    for target in range(len(markers)):
        preferred = set(itertools.chain.from_iterable(ids for ids in grouped.values() if target in ids)) - {target}
        local_prediction, local_variance = prediction, variance
        local_target = target
        if prediction is None and cfg["use_kalman"] and (~valid[:, target] & roi).any():
            subset = [target] + sorted(preferred)
            if len(subset) >= 2:
                try:
                    local_prediction, local_variance = low_dimensional_kalman(
                        original[:, subset], original_valid[:, subset], cfg)
                    local_target = 0
                except (ValueError, np.linalg.LinAlgError) as error:
                    warnings.append("local_kalman/%s: %s" % (markers[target], error))
        for start, end in runs(~valid[:, target] & roi):
            if local_prediction is not None:
                # Local subsets reorder the target to column zero.
                corrected = _anchor_prediction(local_prediction, frozen[:, [target]] if local_target == 0 and prediction is None else frozen,
                    frozen_valid[:, [target]] if local_target == 0 and prediction is None else frozen_valid,
                    local_target, start, end)
                apply(target, start, end, corrected,
                      "low_dimensional_kalman", {"training": "input_existing_only", "boundary_bias_corrected": True})
                model_sd[start:end, target] = np.sqrt(np.maximum(
                    local_variance[start:end, local_target].mean(axis=1), 0))
            if (~valid[start:end, target]).any() and cfg["use_ridge"]:
                result = _ridge_prediction(original, original_valid, frozen, frozen_valid,
                                           target, start, end, preferred)
                if result is not None:
                    apply(target, start, end, result[0], "marker_ridge_regression", result[1])
            if (~valid[start:end, target]).any():
                result = _relative_interpolation(original, original_valid, frozen, frozen_valid,
                                                target, start, end, preferred)
                if result is not None:
                    info = result[1]
                    method = info.pop("method", "relative_offset_interpolation_or_hold")
                    apply(target, start, end, result[0], method, info)
    if not np.array_equal(current[frozen_valid], frozen[frozen_valid]):
        raise AssertionError("fallback changed an existing or native-filled sample")
    outside_a, outside_b = current[~roi], original[~roi]
    if not np.all((outside_a == outside_b) | (np.isnan(outside_a) & np.isnan(outside_b))):
        raise AssertionError("out-of-ROI samples changed")
    if (~valid & roi[:, None]).any():
        warnings.append("unidentifiable_targets_with_no_input_observations")
    return dict(xyz=current, valid=valid, methods=methods, low=low,
                model_sd=model_sd, details=details, warnings=warnings)

# ===== Nexus adapter =====
"""Nexus adapter and auditable, non-nested Pipeline operations."""
import csv
import hashlib
import json
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np


SCRIPT_FILE = Path(__file__).resolve()
_installation_id = hashlib.sha256(str(SCRIPT_FILE).encode("utf-8")).hexdigest()[:12]
ROOT = Path(CONFIG["cache_root"] or os.environ.get("LOCALAPPDATA", str(SCRIPT_FILE.parent))) / "NexusGapFillSingle" / _installation_id



def _ascii_text(value):
    return bool(value) and all(32 <= ord(character) < 127 for character in str(value))


def ascii_path_guard(paths):
    """Check the supplied spelling AND resolved physical absolute path, including parents.

    ASCII letters, digits, spaces and normal ASCII punctuation are allowed.
    Non-ASCII text and control characters are refused; do not rename or transliterate.
    """
    failures = []
    for label, value in paths.items():
        raw = str(value)
        absolute = os.path.abspath(raw)
        resolved = str(Path(absolute).resolve())
        for form, path in (("specified", raw), ("absolute", absolute), ("resolved", resolved)):
            if not _ascii_text(path):
                failures.append("%s (%s): %s" % (label, form, path.encode("unicode_escape").decode("ascii")))
    if failures:
        raise RuntimeError("ASCII_PATH_REQUIRED: use English/ASCII paths for every parent directory and filename. "
                           "Create an independent copy; do not rename source files automatically. " + " | ".join(failures))


def path_preflight(nexus, install=True, profile_path=None):
    folder, trial = nexus.GetTrialName()
    if not folder or not trial:
        raise RuntimeError("Open a trial copy in Nexus Offline mode first.")
    subject = active_subject(nexus)
    paths = {"script": SCRIPT_FILE, "Python interpreter": sys.executable,
             "cache": ROOT, "trial directory": folder,
             "trial C3D": Path(folder) / (trial + ".c3d"),
             "subject VSK": Path(folder) / (subject + ".vsk")}
    if install:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA is unavailable; use the Windows account running Nexus.")
        paths["Nexus private Pipeline directory"] = Path(appdata) / "Vicon/Nexus2.x/Configurations/Pipelines"
    if profile_path is not None:
        paths["profile"] = profile_path
    session = Path(folder)
    if session.is_dir():
        for item in session.iterdir():
            if item.is_file() and item.suffix.lower() in (".c3d", ".enf", ".vsk", ".vst", ".mp", ".xcp", ".x2d", ".x1d"):
                paths["session file " + item.name.encode("unicode_escape").decode("ascii")] = item
    ascii_path_guard(paths)
    return {"status": "ascii_paths_ok", "checked_paths": {k: str(v) for k, v in paths.items()},
            "scope": "current script, Python, trial/session files, cache, profile and Nexus private Pipeline directory; inspect extra paths in later pipelines separately"}


def settings():
    return dict(DEFAULTS, **CONFIG)



def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    ascii_path_guard({"JSON output": path})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(str(temporary), str(path))




def connect():
    from viconnexusapi import ViconNexus
    nexus = ViconNexus.ViconNexus()
    if not nexus.IsConnected():
        raise RuntimeError("Nexus is not connected. Open Nexus 2.16 in Offline mode first.")
    return nexus


def active_subject(nexus):
    names, _, active = nexus.GetSubjectInfo()
    names = [name for name, enabled in zip(names, active) if enabled]
    if len(names) != 1:
        raise RuntimeError("Version 0.1 requires exactly one active subject, found %d." % len(names))
    return names[0]


def layout_description(nexus, subject):
    segments = {name: sorted(set(nexus.GetSegmentDetails(subject, name)[2]))
                for name in sorted(nexus.GetSegmentNames(subject))}
    # Include template-associated optional markers even when no trajectory exists.
    # This keeps the layout identity stable when KinFit exposes such a marker.
    markers = sorted(set(nexus.GetMarkerNames(subject)).union(
        marker for names in segments.values() for marker in names))
    canonical = dict(markers=markers, segments=segments)
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return canonical, digest


def capture(nexus):
    subject = active_subject(nexus)
    folder, trial = nexus.GetTrialName()
    if not folder or not trial:
        raise RuntimeError("Open a reconstructed, labelled trial before using NGF.")
    description, digest = layout_description(nexus, subject)
    loaded, excluded = {}, {}
    for name in description["markers"]:
        if not nexus.HasTrajectory(subject, name):
            excluded[name] = "no_trajectory_in_this_trial"
            continue
        x, y, z, exists = nexus.GetTrajectory(subject, name)
        a = np.column_stack([x, y, z]).astype(float)
        e = np.asarray(exists, bool) & np.isfinite(a).all(axis=1)
        if not e.any():
            excluded[name] = "never_observed_in_loaded_trial_requires_explicit_target_definition"
            continue
        loaded[name] = a, e
    if not loaded:
        raise RuntimeError("No markers with input observations were found.")
    names = sorted(loaded)
    lengths = {len(loaded[name][0]) for name in names}
    if len(lengths) != 1:
        raise RuntimeError("Marker trajectory lengths differ.")
    origin = array_frame_origin(next(iter(lengths)), nexus.GetTrialRange())
    roi = tuple(map(int, nexus.GetTrialRegionOfInterest()))
    description = dict(description, target_markers=names)
    meta = dict(trial_folder=str(Path(folder).resolve()), trial=trial, subject=subject,
                layout=digest, description=description, excluded=excluded,
                array_frame_origin=origin, trial_range=list(nexus.GetTrialRange()),
                roi=list(roi), frame_rate=float(nexus.GetFrameRate()),
                input_provenance="existing_valid_unknown_origin_not_necessarily_camera_measured")
    xyz = np.stack([loaded[name][0] for name in names], axis=1)
    valid = np.column_stack([loaded[name][1] for name in names])
    if nexus.GetTrialName() != (folder, trial) or tuple(nexus.GetTrialRegionOfInterest()) != roi:
        raise RuntimeError("The active trial or ROI changed while reading; retry with editing paused.")
    return meta, xyz, valid


def _check_write_target(folder, cfg):
    folder = os.path.normcase(os.path.abspath(str(folder)))
    for protected in cfg.get("protected_roots", []):
        protected = os.path.normcase(os.path.abspath(protected))
        try:
            inside = os.path.commonpath([folder, protected]) == protected
        except ValueError:
            inside = False
        if inside:
            raise RuntimeError("This input folder is protected. Open an independent copy: " + folder)


def _native_entry(root, display, operation, params):
    entry = ET.SubElement(root, "Entry", DisplayName=display, Enabled="1",
                          OperationId="0", OperationName=operation)
    node = ET.SubElement(entry, "ParamList", name="")
    for name, value in params.items():
        if isinstance(value, tuple):
            ET.SubElement(node, "Param", name=name, macro=value[0])
        else:
            ET.SubElement(node, "Param", name=name, value=str(value))


def _python_entry(root, display, arguments):
    _native_entry(root, display, "Python", dict(Script=str(SCRIPT_FILE),
        ScriptArgs=arguments + " --from-nexus", UseNexusPython="true", LaunchPython="true"))


def generate_pipeline(profile, profile_path):
    """Original Vicon XML operation names, followed by Python checkpoints."""
    cfg, root = profile["settings"], ET.Element("Pipeline")
    quoted = ' --profile "%s"' % profile_path
    _python_entry(root, "NGF 00 - Record original masks", "begin" + quoted)
    start, end = ("SELECTED_START_FRAME",), ("SELECTED_END_FRAME",)
    _native_entry(root, "NGF 01 - Nexus short gaps", "FillGapsWoltring", dict(
        MaxGapToFill=cfg["spline_seconds"], MaxGapFillType=1, START_FRAME=start, LAST_FRAME=end))
    _python_entry(root, "NGF audit - Woltring", "checkpoint --stage nexus_woltring" + quoted)
    for index, (segment, names) in enumerate(sorted(profile["rigid_groups"].items())):
        _native_entry(root, "NGF 02 - Rigid " + segment, "FillGapsRigidBody", dict(
            MaxGapToFill=cfg["rigid_seconds"], MaxGapFillType=1, FirstFrame=start,
            LastFrame=end, Trajectories=",".join(names)))
        _python_entry(root, "NGF audit - Rigid " + segment,
                      "checkpoint --stage nexus_rigid_%d" % index + quoted)
    _native_entry(root, "NGF 03 - Nexus pattern", "FillGapsPattern", dict(
        MaxGapToFill=cfg["pattern_seconds"], MaxGapFillType=1, START_FRAME=start,
        LAST_FRAME=end, Trajectories=("ALL_MARKERS",), DonorTraj=("AUTO_DONOR_MARKERS",),
        AllowUnlabeled=0, AllowDifferentSegment=0))
    _python_entry(root, "NGF audit - Pattern", "checkpoint --stage nexus_pattern" + quoted)
    _native_entry(root, "NGF 04 - Kinematic fit", "ComputeSubjectMotion", dict(
        START_FRAME=start, LAST_FRAME=end, SUBJECTS=("ACTIVE_SUBJECTS",),
        PriorImportance=25, MeanPoseRatio=1))
    _native_entry(root, "NGF 05 - Nexus kinematic fill", "FillGapsKinematic", dict(
        MaxGapToFill=cfg["kinematic_seconds"], MaxGapFillType=1, FirstFrame=start,
        LastFrame=end, SegmentToUse=("ALL_SEGMENTS",), SUBJECTS=("ACTIVE_SUBJECTS",)))
    _python_entry(root, "NGF audit - Kinematic", "checkpoint --stage nexus_kinematic" + quoted)
    _python_entry(root, "NGF 06 - Complete remaining gaps and audit", "finish" + quoted)
    # Export to a new C3D; the input C3D and calibrated VSK remain on disk.
    _native_entry(root, "NGF 07 - Export separate _NGF C3D", "Exportc3d", dict(
        Filename=("CURRENT_TRIAL",), StartFrame=start, EndFrame=end, Postfix="_NGF",
        IntegerFormat=0, SubjectPrefix=1, XAxis=4, YAxis=2, ZAxis=0))
    _python_entry(root, "NGF 08 - Verify exported C3D", "verify-export" + quoted)
    return b'<?xml version="1.1" encoding="UTF-8" standalone="no" ?>\n' + ET.tostring(root, encoding="utf-8")


def prepare(nexus, install=False):
    path_report = path_preflight(nexus, install=install)
    meta, xyz, valid = capture(nexus)
    cfg = settings()
    groups, rejected = eligible_rigid_groups(xyz, valid, meta["description"]["target_markers"],
                                             meta["description"]["segments"], cfg)
    profile_data = dict(layout=meta["layout"], description=meta["description"],
                        rigid_groups=groups, rejected_rigid_groups=rejected, settings=cfg)
    # A moved installation must register a different name rather than collide
    # with an older pipeline whose Python entries point at another directory.
    identity = dict(profile_data, script_root=str(ROOT.resolve()))
    profile_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    name = "NGF_SINGLE_" + profile_hash
    profile_path = ROOT / "profiles" / (name + ".json")
    write_json(profile_path, profile_data)
    xml = generate_pipeline(profile_data, profile_path)
    pipeline_path = ROOT / "pipelines" / (name + ".Pipeline")
    pipeline_path.parent.mkdir(exist_ok=True)
    pipeline_path.write_bytes(xml)
    if install:
        private = Path(os.environ["APPDATA"]) / "Vicon/Nexus2.x/Configurations/Pipelines"
        private.mkdir(parents=True, exist_ok=True)
        destination = private / pipeline_path.name
        if destination.exists() and destination.read_bytes() != xml:
            raise RuntimeError("Existing private pipeline has different contents: " + str(destination))
        destination.write_bytes(xml)
    result = dict(path_preflight=path_report, pipeline=name, profile=str(profile_path), generated_pipeline=str(pipeline_path),
                  subject=meta["subject"], targets=len(meta["description"]["target_markers"]),
                  excluded=meta["excluded"], rigid_groups=groups, installed=install)
    write_json(ROOT / "last_prepared.json", result)
    return result


def _state_pointer(meta):
    # Separate pointer per subject, folder and trial; no state reuse across trials.
    key = "\0".join([meta["trial_folder"], meta["trial"], meta["subject"]])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return ROOT / "state" / (digest + ".json")


def _load_run(nexus, profile_path):
    path_preflight(nexus, profile_path=profile_path)
    profile = read_json(profile_path)
    meta, xyz, valid = capture(nexus)
    if meta["layout"] != profile["layout"]:
        raise RuntimeError("The active VSK layout differs from this pipeline. Prepare a pipeline for this layout.")
    pointer = _state_pointer(meta)
    if not pointer.exists():
        raise RuntimeError("NGF begin step has not run for this trial.")
    run_path = Path(read_json(pointer)["run_path"])
    state = read_json(run_path / "run.json")
    for key in ("subject", "trial_folder", "trial", "layout", "roi", "array_frame_origin"):
        if state["input"][key] != meta[key]:
            raise RuntimeError("Trial or ROI changed during pipeline: " + key)
    expected = state["input"]["description"]["target_markers"]
    actual = meta["description"]["target_markers"]
    if not set(expected).issubset(actual):
        raise RuntimeError("An original target marker disappeared during pipeline.")
    # KinFit may expose previously absent optional model markers. They remain
    # outside this trial's targets and will be invalidated at the checkpoint.
    take = [actual.index(name) for name in expected]
    xyz, valid = xyz[:, take], valid[:, take]
    meta["description"]["target_markers"] = expected
    meta["excluded"] = state["input"]["excluded"]
    with np.load(str(run_path / "original.npz"), allow_pickle=False) as data:
        original, original_valid = data["xyz"], data["valid"]
    with np.load(str(run_path / "latest.npz"), allow_pickle=False) as data:
        previous, previous_valid, methods = data["xyz"], data["valid"], data["methods"]
    return profile, meta, xyz, valid, run_path, state, original, original_valid, previous, previous_valid, methods


def begin(nexus, profile_path):
    path_preflight(nexus, profile_path=profile_path)
    profile = read_json(profile_path)
    meta, xyz, valid = capture(nexus)
    if meta["layout"] != profile["layout"]:
        raise RuntimeError("VSK layout mismatch. Run prepare for the current marker layout.")
    _check_write_target(meta["trial_folder"], profile["settings"])
    accepted, _ = eligible_rigid_groups(xyz, valid, meta["description"]["target_markers"],
                                        profile["rigid_groups"], profile["settings"])
    unavailable = set(profile["rigid_groups"]) - set(accepted)
    if unavailable:
        raise RuntimeError("Rigid groups failed this trial's precheck; prepare a new profile: " +
                           ", ".join(sorted(unavailable)))
    run_id = time.strftime("%Y%m%d_%H%M%S") + "_%d" % os.getpid()
    run_path = Path(meta["trial_folder"]) / "NGF_reports" / (meta["trial"] + "__" + run_id)
    run_path.mkdir(parents=True, exist_ok=False)
    for source in (Path(meta["trial_folder"]) / (meta["trial"] + ".c3d"),
                   Path(meta["trial_folder"]) / (meta["subject"] + ".vsk"),
                   Path(meta["trial_folder"]) / (meta["trial"] + "._NGF.c3d")):
        if source.exists():
            destination = run_path / "disk_backup" / source.name
            destination.parent.mkdir(exist_ok=True)
            shutil.copy2(str(source), str(destination))
    methods = np.full(valid.shape, "missing", dtype="U64")
    methods[valid] = "input_existing"
    np.savez_compressed(str(run_path / "original.npz"), xyz=xyz, valid=valid)
    np.savez_compressed(str(run_path / "latest.npz"), xyz=xyz, valid=valid, methods=methods)
    state = dict(version="0.4.0", run_id=run_id, status="native_processing", input=meta,
                 profile=str(profile_path), stages=[], warnings=[
                     "input_existing means present on entry; prior fills cannot automatically be distinguished from measurements",
                     "model uncertainty and smoothness are not calibrated accuracy guarantees"],
                 excluded_markers=meta["excluded"])
    write_json(run_path / "run.json", state)
    write_json(_state_pointer(meta), dict(run_path=str(run_path)))
    print("NGF begin:", run_path)


def _write(nexus, meta, xyz, valid, before_xyz=None, before_valid=None):
    """Write changed columns only; compare invalid/out-of-ROI samples too."""
    written = []
    for index, name in enumerate(meta["description"]["target_markers"]):
        values = xyz[:, index]
        if before_xyz is not None and before_valid is not None:
            same_xyz = (values == before_xyz[:, index]) | (
                np.isnan(values) & np.isnan(before_xyz[:, index]))
            if same_xyz.all() and np.array_equal(valid[:, index], before_valid[:, index]):
                continue
        nexus.SetTrajectory(meta["subject"], name, values[:, 0].tolist(), values[:, 1].tolist(),
                            values[:, 2].tolist(), valid[:, index].tolist())
        written.append(name)
    return written


def checkpoint(nexus, profile_path, stage):
    loaded = _load_run(nexus, profile_path)
    profile, meta, xyz, valid, run_path, state, original, original_valid, previous, previous_valid, methods = loaded
    if state["status"] != "native_processing":
        raise RuntimeError("Checkpoint called outside the native processing phase.")
    started = time.perf_counter()
    before_xyz, before_valid = xyz.copy(), valid.copy()
    roi = roi_mask(len(xyz), meta["array_frame_origin"], meta["roi"])
    added = valid & ~previous_valid & roi[:, None]
    xyz[previous_valid] = previous[previous_valid]
    valid[previous_valid] = True
    xyz[~roi], valid[~roi] = original[~roi], original_valid[~roi]
    methods[added] = stage
    written = _write(nexus, meta, xyz, valid, before_xyz, before_valid)
    # Native operations must not create optional/static-only marker trajectories.
    for name in meta["excluded"]:
        if nexus.HasTrajectory(meta["subject"], name):
            x, y, z, e = nexus.GetTrajectory(meta["subject"], name)
            if any(e):
                nexus.SetTrajectory(meta["subject"], name, x, y, z, [False]*len(e))
    np.savez_compressed(str(run_path / "latest.npz"), xyz=xyz, valid=valid, methods=methods)
    state["stages"].append(dict(stage=stage, added=int(added.sum()),
        trajectory_writes=len(written), checkpoint_seconds=time.perf_counter()-started))
    write_json(run_path / "run.json", state)
    print("NGF", stage, "filled", int(added.sum()))


def _csv_report(path, meta, result):
    first = meta["array_frame_origin"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Trial", "Subject", "Marker", "OriginalFrame", "TimeFromTrialStart_s",
                         "X_mm", "Y_mm", "Z_mm", "Source", "Method", "LowReliability",
                         "Reliability", "ModelSD_mm_not_validated_error"])
        for index in np.flatnonzero(roi_mask(len(result["xyz"]), first, meta["roi"])):
            for j, marker in enumerate(meta["description"]["target_markers"]):
                method = str(result["methods"][index, j])
                if method == "input_existing":
                    source, reliability = "INPUT_EXISTING", "UNKNOWN_INPUT_ORIGIN"
                elif result["low"][index, j]:
                    source, reliability = "FALLBACK_ESTIMATE", "LOW"
                elif result["valid"][index, j]:
                    source, reliability = "NEXUS_ESTIMATE", "NATIVE_UNVALIDATED"
                else:
                    source, reliability = "UNRESOLVED", "NO_IDENTIFIABLE_ESTIMATE"
                sd = result["model_sd"][index, j]
                writer.writerow([meta["trial"], meta["subject"], marker, int(first+index),
                    (first+index-meta["trial_range"][0])/meta["frame_rate"],
                    *result["xyz"][index, j].tolist(), source, method,
                    int(result["low"][index, j]), reliability,
                    "" if not np.isfinite(sd) else float(sd)])


def finish(nexus, profile_path):
    loaded = _load_run(nexus, profile_path)
    profile, meta, xyz, valid, run_path, state, original, original_valid, previous, previous_valid, methods = loaded
    if state["status"] != "native_processing":
        raise RuntimeError("Finish called out of order; rerun the complete NGF pipeline.")
    roi = roi_mask(len(xyz), meta["array_frame_origin"], meta["roi"])
    result = complete_remaining(original, original_valid, previous, previous_valid,
        meta["description"]["target_markers"], meta["description"]["segments"], roi,
        profile["settings"], native_methods=methods)
    written = _write(nexus, meta, result["xyz"], result["valid"], xyz, valid)
    # Exact SDK readback check before export. No model is fitted or recalibrated here.
    readback_error = 0.0
    for j, marker in enumerate(meta["description"]["target_markers"]):
        x, y, z, e = nexus.GetTrajectory(meta["subject"], marker)
        got = np.column_stack([x, y, z])
        if not np.array_equal(e, result["valid"][:, j]):
            raise RuntimeError("SDK validity readback failed for " + marker)
        use = result["valid"][:, j]
        readback_error = max(readback_error, float(np.max(np.abs(got[use]-result["xyz"][use, j]))))
    if readback_error > 0.02:
        raise RuntimeError("SDK coordinate readback exceeded 0.02 mm: %s" % readback_error)
    changed = bool((result["valid"] & ~original_valid & roi[:, None]).any())
    invalidated = []
    if changed and profile["settings"].get("invalidate_old_model_outputs", True):
        for name in nexus.GetModelOutputNames(meta["subject"]):
            components, exists = nexus.GetModelOutput(meta["subject"], name)
            if any(exists):
                nexus.SetModelOutput(meta["subject"], name, components, [False]*len(exists))
                invalidated.append(name)
    np.savez_compressed(str(run_path / "completed.npz"), **{
        k: result[k] for k in ("xyz", "valid", "methods", "low", "model_sd")})
    csv_path = run_path / "per_frame_provenance.csv"
    _csv_report(csv_path, meta, result)
    shutil.copyfile(str(csv_path), str(Path(meta["trial_folder"]) /
                                     (meta["trial"] + "_NGF.provenance.csv")))
    state["status"] = "completed_in_memory_export_pending"
    state["warnings"].extend(result["warnings"])
    state["fallback_segments"] = result["details"]
    state["summary"] = dict(
        input_missing=int((~original_valid & roi[:, None]).sum()),
        native_added=int((previous_valid & ~original_valid & roi[:, None]).sum()),
        fallback_added=int(result["low"].sum()),
        remaining=int((~result["valid"] & roi[:, None]).sum()),
        sdk_readback_max_mm=readback_error,
        fallback_trajectory_writes=len(written),
        invalidated_old_model_outputs=invalidated)
    write_json(run_path / "run.json", state)
    print("NGF finish:", json.dumps(state["summary"]))


def verify_export(nexus, profile_path):
    loaded = _load_run(nexus, profile_path)
    _, meta, _, _, run_path, state, _, _, _, _, _ = loaded
    if state["status"] != "completed_in_memory_export_pending":
        raise RuntimeError("Export verification called before finish.")
    # Nexus 2.16 inserts a period before the Exportc3d Postfix parameter.
    output = Path(meta["trial_folder"]) / (meta["trial"] + "._NGF.c3d")
    if not output.exists() or output.stat().st_mtime < (run_path / "completed.npz").stat().st_mtime - 2:
        raise RuntimeError("A newly exported _NGF.c3d was not found.")
    from pyCGM2 import btk
    reader = btk.btkAcquisitionFileReader()
    reader.SetFilename(str(output))
    reader.Update()
    acquisition = reader.GetOutput()
    first, last = meta["roi"]
    if acquisition.GetFirstFrame() != first or acquisition.GetLastFrame() != last:
        raise RuntimeError("Exported C3D frame range differs from ROI.")
    points = {point.GetLabel(): point for point in btk.Iterate(acquisition.GetPoints())}
    with np.load(str(run_path / "completed.npz"), allow_pickle=False) as data:
        roi = roi_mask(len(data["xyz"]), meta["array_frame_origin"], meta["roi"])
        expected, expected_valid = data["xyz"][roi], data["valid"][roi]
        maximum = 0.0
        for j, marker in enumerate(meta["description"]["target_markers"]):
            label = meta["subject"] + ":" + marker
            if label not in points:
                label = marker
            if label not in points:
                raise RuntimeError("Marker missing from exported C3D: " + marker)
            point = points[label]
            actual_valid = point.GetResiduals()[:, 0] >= 0
            if not np.array_equal(actual_valid, expected_valid[:, j]):
                raise RuntimeError("Exported validity mismatch: " + marker)
            if actual_valid.any():
                maximum = max(maximum, float(np.max(np.abs(
                    point.GetValues()[actual_valid] - expected[actual_valid, j]))))
    if maximum > 0.02:
        raise RuntimeError("C3D export coordinate mismatch exceeds 0.02 mm: %s" % maximum)
    state["status"] = "export_verified"
    state["export"] = dict(file=str(output), maximum_coordinate_difference_mm=maximum,
        sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    write_json(run_path / "run.json", state)
    write_json(Path(meta["trial_folder"]) / (meta["trial"] + "_NGF.summary.json"), state)
    print("NGF verified:", output)




def main(argv=None):
    parser = argparse.ArgumentParser(description="Single-file Nexus-first gap filling")
    parser.add_argument("command", nargs="?", default="prepare",
                        choices=["prepare", "preflight", "inspect", "begin", "checkpoint", "finish", "verify-export"])
    parser.add_argument("--profile")
    parser.add_argument("--stage")
    parser.add_argument("--from-nexus", action="store_true")
    args = parser.parse_args(argv)
    if args.command in ("begin", "checkpoint", "finish", "verify-export") and not args.profile:
        parser.error("This internal pipeline step requires --profile; leave arguments blank to prepare.")
    if args.command == "checkpoint" and not args.stage:
        parser.error("checkpoint requires --stage")
    ascii_path_guard({"script": SCRIPT_FILE, "Python interpreter": sys.executable, "cache": ROOT})
    nexus = connect()
    try:
        if args.command == "prepare":
            folder, trial = nexus.GetTrialName()
            if not folder or not trial:
                raise RuntimeError("Open a trial copy in Nexus Offline mode first.")
            _check_write_target(folder, settings())
            from pyCGM2 import btk  # fail before native processing if export verification is unavailable
            prepared = prepare(nexus, install=True)
            print("\nNGF SINGLE READY: " + prepared["pipeline"])
            print("Preparation only: no marker coordinates have been changed.")
            print("In Nexus: Current Pipeline > Refresh List > select the name above > Run.")
            print("No command line or additional program files are needed.")
            print("Excluded markers: " + json.dumps(prepared["excluded"], ensure_ascii=False))
            print("Automatic cache: " + str(ROOT))
        elif args.command == "preflight":
            print(json.dumps(path_preflight(nexus), ensure_ascii=True, indent=2))
        elif args.command == "inspect":
            meta, xyz, valid = capture(nexus)
            print(json.dumps(dict(metadata=meta, shape=list(xyz.shape),
                                  total_existing=int(valid.sum())), ensure_ascii=False, indent=2))
        elif args.command == "begin":
            begin(nexus, args.profile)
        elif args.command == "checkpoint":
            checkpoint(nexus, args.profile, args.stage)
        elif args.command == "finish":
            finish(nexus, args.profile)
        elif args.command == "verify-export":
            verify_export(nexus, args.profile)
    finally:
        nexus.Disconnect()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        error = traceback.format_exc()
        print(error, file=sys.stderr)
        try:
            ascii_path_guard({"error log": ROOT})
            ROOT.mkdir(parents=True, exist_ok=True)
            (ROOT / "last_error.log").write_text(error, encoding="utf-8")
            print("NGF error log: " + str(ROOT / "last_error.log"), file=sys.stderr)
        except (OSError, RuntimeError):
            pass
        sys.exit(1)
