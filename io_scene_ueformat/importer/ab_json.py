from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from ..logging import Log

_NORMALIZE_SUFFIXES = ("_skin", "_ab", "_lod0", "_skeleton", "_phy")

def _normalize_stem(stem: str) -> str:
    result = stem.lower()
    changed = True
    while changed:
        changed = False
        for suffix in _NORMALIZE_SUFFIXES:
            if result.endswith(suffix):
                result = result[: -len(suffix)]
                changed = True
    return result


def find_matching_ab_json(model_path: str | Path) -> Path | None:
    model_path = Path(model_path)
    directory = model_path.parent
    if not directory.is_dir():
        return None

    candidates = [p for p in directory.glob("*.json") if "_ab" in p.stem.lower()]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    model_key = _normalize_stem(model_path.stem)
    best_path: Path | None = None
    best_score = -1.0
    for candidate in candidates:
        candidate_key = _normalize_stem(candidate.stem)
        score = difflib.SequenceMatcher(None, model_key, candidate_key).ratio()
        if score > best_score:
            best_score = score
            best_path = candidate

    return best_path


def extract_ab_target_bones(json_path: str | Path) -> tuple[str | None, list[str]]:
    json_path = Path(json_path)
    target_skeleton_name: str | None = None
    target_bones: list[str] = []
    seen: set[str] = set()

    try:
        with json_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        Log.warn(f"Failed to parse AB json {json_path}: {e}")
        return None, []

    def walk(obj) -> None:
        nonlocal target_skeleton_name

        if isinstance(obj, dict):
            target_skeleton = obj.get("TargetSkeleton")
            if isinstance(target_skeleton, dict) and target_skeleton_name is None:
                object_name = target_skeleton.get("ObjectName", "")
                match = re.search(r"'([^']+)'", object_name)
                if match:
                    target_skeleton_name = match.group(1)

            mirror_bones = obj.get("MirrorBones")
            if isinstance(mirror_bones, list):
                for entry in mirror_bones:
                    if not isinstance(entry, dict):
                        continue
                    target_bone = entry.get("TargetBone")
                    if isinstance(target_bone, str) and target_bone and target_bone != "None" and target_bone not in seen:
                        seen.add(target_bone)
                        target_bones.append(target_bone)

            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(data)
    return target_skeleton_name, target_bones
