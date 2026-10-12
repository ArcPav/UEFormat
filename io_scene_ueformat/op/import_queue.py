from __future__ import annotations

import os
import traceback
from dataclasses import replace
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator, PropertyGroup, UIList, WindowManager

from ..importer.logic import UEFormatImport
from ..options import UEModelOptions
from .settings import _get_attach_skeleton_items, _update_attach_auto, _update_attach_manual

MODEL_EXTENSIONS = {".uemodel", ".json"}


class UFQueueItem(PropertyGroup):
    path: StringProperty(name="Path", subtype="FILE_PATH")  # type: ignore[valid-type]


def _norm(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _is_model_file(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in MODEL_EXTENSIONS


ATTACH_FIELDS = (
    "attach_skeleton",
    "attach_root_bone",
    "attach_target_bone",
    "attach_copy_auto",
    "attach_copy_location",
    "attach_copy_rotation",
)


_suppress_custom = [False]


def _mark_custom(self, context) -> None:  # noqa: ANN001
    if not _suppress_custom[0]:
        self.custom = True


def _auto_changed(self, context) -> None:  # noqa: ANN001
    _update_attach_auto(self, context)
    _mark_custom(self, context)


def _manual_changed(self, context) -> None:  # noqa: ANN001
    _update_attach_manual(self, context)
    _mark_custom(self, context)


class UFAttachOverride(PropertyGroup):
    path: StringProperty(name="Path", subtype="FILE_PATH")  # type: ignore[valid-type]
    custom: BoolProperty(default=False)  # type: ignore[valid-type]
    attach_skeleton: EnumProperty(name="Skeleton", items=_get_attach_skeleton_items, update=_mark_custom)  # type: ignore[valid-type]
    attach_root_bone: StringProperty(name="Root Bone", default="position", update=_mark_custom)  # type: ignore[valid-type]
    attach_target_bone: StringProperty(name="Target Bone", default="auto", update=_mark_custom)  # type: ignore[valid-type]
    attach_copy_auto: BoolProperty(name="Auto", default=True, update=_auto_changed)  # type: ignore[valid-type]
    attach_copy_location: BoolProperty(name="Location", default=False, update=_manual_changed)  # type: ignore[valid-type]
    attach_copy_rotation: BoolProperty(name="Rotate", default=False, update=_manual_changed)  # type: ignore[valid-type]


def get_selected_model_paths(context) -> list[str]:
    space = getattr(context, "space_data", None)
    params = getattr(space, "params", None)
    if params is None:
        return []

    directory = params.directory
    if isinstance(directory, bytes):
        directory = os.fsdecode(directory)

    entries = list(getattr(context, "selected_files", None) or [])
    if not entries:
        active = getattr(context, "active_file", None)
        if active is not None:
            entries = [active]

    result: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        rel = getattr(entry, "relative_path", "") or getattr(entry, "name", "")
        if not rel:
            continue
        full = os.path.join(directory, rel)
        if not os.path.isfile(full) or not _is_model_file(full):
            continue
        key = _norm(full)
        if key not in seen:
            seen.add(key)
            result.append(full)
    return result


def _queue_paths(context) -> list[str]:
    return [item.path for item in context.window_manager.uf_import_queue]


def _remove_from_queue(context, done: set[str]) -> None:
    queue = context.window_manager.uf_import_queue
    for i in reversed(range(len(queue))):
        if _norm(queue[i].path) in done:
            queue.remove(i)
    remove_overrides(context, done)
    wm = context.window_manager
    wm.uf_import_queue_index = min(wm.uf_import_queue_index, max(len(queue) - 1, 0))
    reset_stale_mesh_selection(context)


_models_cache: dict[str, str] = {}


def collect_models(context) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for p in [*_queue_paths(context), *get_selected_model_paths(context)]:
        key = _norm(p)
        if key not in seen:
            seen.add(key)
            paths.append(p)
    return paths


def find_override(context, path: str):
    key = _norm(path)
    for ov in context.window_manager.uf_attach_overrides:
        if _norm(ov.path) == key:
            return ov
    return None


def remove_overrides(context, keys: set[str]) -> None:
    overrides = context.window_manager.uf_attach_overrides
    for i in reversed(range(len(overrides))):
        if _norm(overrides[i].path) in keys:
            overrides.remove(i)
    if current_mesh_key(context) in keys:
        context.window_manager.uf_attach_mesh = ""


def copy_attach_settings(src, dst) -> None:
    _suppress_custom[0] = True
    try:
        try:
            dst.attach_skeleton = src.attach_skeleton
        except TypeError:
            pass
        dst.attach_root_bone = src.attach_root_bone
        dst.attach_target_bone = src.attach_target_bone
        dst.attach_copy_location = src.attach_copy_location
        dst.attach_copy_rotation = src.attach_copy_rotation
        dst.attach_copy_auto = src.attach_copy_auto
    finally:
        _suppress_custom[0] = False


def ensure_override(context, path: str):
    ov = find_override(context, path)
    if ov is None:
        ov = context.window_manager.uf_attach_overrides.add()
        ov.path = path
        ov.custom = False
        copy_attach_settings(context.scene.uf_settings, ov)
    elif not ov.custom:
        copy_attach_settings(context.scene.uf_settings, ov)
    return ov


def options_for(context, options, path: str):
    ov = find_override(context, path)
    if ov is None or not ov.custom or options is None:
        return options
    fields = {f: getattr(ov, f) for f in ATTACH_FIELDS if hasattr(options, f)}
    return replace(options, **fields)


def current_mesh_key(context) -> str | None:
    return context.window_manager.uf_attach_mesh or None


def reset_stale_mesh_selection(context) -> None:
    key = current_mesh_key(context)
    if key is not None and key not in {_norm(p) for p in collect_models(context)}:
        context.window_manager.uf_attach_mesh = ""


def _short_name(path: str) -> str:
    p = Path(path)
    return f"{p.parent.name}/{p.name}" if p.parent.name else p.name


def import_model_paths(operator: Operator, context, options, extra_paths=()) -> set[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for p in [*_queue_paths(context), *map(os.fspath, extra_paths)]:
        key = _norm(p)
        if key not in seen:
            seen.add(key)
            paths.append(p)

    if not paths:
        operator.report({"WARNING"}, "Nothing to import")
        return {"CANCELLED"}

    wm = context.window_manager
    use_overrides = len(paths) > 1
    wm.progress_begin(0, len(paths))
    done: set[str] = set()
    failed: list[str] = []
    try:
        for i, p in enumerate(paths):
            wm.progress_update(i)
            try:
                if not os.path.isfile(p):
                    raise FileNotFoundError(p)
                UEFormatImport(options_for(context, options, p) if use_overrides else options).import_file(Path(p))
                done.add(_norm(p))
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                failed.append(f"{Path(p).name}: {e}")
    finally:
        wm.progress_end()

    _remove_from_queue(context, done)

    if failed:
        operator.report(
            {"WARNING"},
            f"Imported {len(done)}/{len(paths)}. Failed (kept in queue): " + "; ".join(failed),
        )
    else:
        operator.report({"INFO"}, f"Imported {len(done)} model(s)")
    return {"FINISHED"} if done else {"CANCELLED"}


class UF_OT_QueueAdd(Operator):  # noqa: N801
    bl_idname = "uf.queue_add"
    bl_label = "Add to Import Queue"
    bl_description = "Add the selected .uemodel files to the import queue"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context) -> bool:
        space = getattr(context, "space_data", None)
        return space is not None and space.type == "FILE_BROWSER"

    def execute(self, context) -> set[str]:
        paths = get_selected_model_paths(context)
        if not paths:
            self.report({"WARNING"}, "No .uemodel file selected")
            return {"CANCELLED"}

        queue = context.window_manager.uf_import_queue
        existing = {_norm(item.path) for item in queue}
        added = 0
        for p in paths:
            if _norm(p) in existing:
                continue
            queue.add().path = p
            existing.add(_norm(p))
            added += 1

        skipped = len(paths) - added
        msg = f"Added {added} model(s) to the import queue (total {len(queue)})"
        if skipped:
            msg += f", {skipped} already queued"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class UF_OT_QueueRemove(Operator):  # noqa: N801
    bl_idname = "uf.queue_remove"
    bl_label = "Remove from Queue"
    bl_description = "Remove this entry from the import queue"
    bl_options = {"INTERNAL"}

    index: IntProperty()  # type: ignore[valid-type]

    def execute(self, context) -> set[str]:
        wm = context.window_manager
        if 0 <= self.index < len(wm.uf_import_queue):
            remove_overrides(context, {_norm(wm.uf_import_queue[self.index].path)})
            wm.uf_import_queue.remove(self.index)
            reset_stale_mesh_selection(context)
            wm.uf_import_queue_index = min(wm.uf_import_queue_index, max(len(wm.uf_import_queue) - 1, 0))
        return {"FINISHED"}


class UF_OT_QueueRemoveSelected(Operator):  # noqa: N801
    bl_idname = "uf.queue_remove_selected"
    bl_label = "Remove from Import Queue"
    bl_description = "Remove the selected .uemodel files from the import queue"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context) -> bool:
        space = getattr(context, "space_data", None)
        return space is not None and space.type == "FILE_BROWSER"

    def execute(self, context) -> set[str]:
        targets = {_norm(p) for p in get_selected_model_paths(context)}
        queue = context.window_manager.uf_import_queue
        before = len(queue)
        _remove_from_queue(context, targets)
        removed = before - len(queue)
        if not removed:
            self.report({"WARNING"}, "Selected files are not in the import queue")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Removed {removed} model(s) from the import queue (left {len(queue)})")
        return {"FINISHED"}


class UF_OT_QueueClear(Operator):  # noqa: N801
    bl_idname = "uf.queue_clear"
    bl_label = "Clear Import Queue"
    bl_description = "Remove every entry from the import queue"
    bl_options = {"INTERNAL"}

    def execute(self, context) -> set[str]:
        context.window_manager.uf_import_queue.clear()
        context.window_manager.uf_import_queue_index = 0
        context.window_manager.uf_attach_overrides.clear()
        context.window_manager.uf_attach_mesh = ""
        return {"FINISHED"}


class UF_OT_ImportQueue(Operator):  # noqa: N801
    bl_idname = "uf.import_queue"
    bl_label = "Import Queue"
    bl_description = "Import every model in the import queue at once"

    @classmethod
    def poll(cls, context) -> bool:
        return len(context.window_manager.uf_import_queue) > 0

    def execute(self, context) -> set[str]:
        options = UEModelOptions.from_settings(context.scene.uf_settings)
        return import_model_paths(self, context, options)


class UF_OT_AttachMeshSelect(Operator):  # noqa: N801
    bl_idname = "uf.attach_mesh_select"
    bl_label = "Select Mesh"
    bl_description = "Edit the Attach Main settings of this model"
    bl_options = {"INTERNAL"}

    key: StringProperty()  # type: ignore[valid-type]

    def execute(self, context) -> set[str]:
        if self.key:
            ensure_override(context, _models_cache.get(self.key, self.key))
        context.window_manager.uf_attach_mesh = self.key
        return {"FINISHED"}


class UF_MT_AttachMesh(Menu):  # noqa: N801
    bl_label = "Mesh"

    def draw(self, context) -> None:
        layout = self.layout
        layout.operator(UF_OT_AttachMeshSelect.bl_idname, text="Global", icon="WORLD").key = ""
        for key, path in _models_cache.items():
            ov = find_override(context, path)
            custom = ov is not None and ov.custom
            layout.operator(
                UF_OT_AttachMeshSelect.bl_idname,
                text=_short_name(path) + (" (custom)" if custom else ""),
                icon="MESH_DATA",
            ).key = key


class UF_OT_AttachCustomReset(Operator):  # noqa: N801
    bl_idname = "uf.attach_custom_reset"
    bl_label = "Reset to Global"
    bl_description = "Drop this model's own Attach Main settings and use the global ones again"
    bl_options = {"INTERNAL"}

    def execute(self, context) -> set[str]:
        key = current_mesh_key(context)
        ov = find_override(context, key) if key else None
        if ov is not None:
            copy_attach_settings(context.scene.uf_settings, ov)
            ov.custom = False
        return {"FINISHED"}


class UF_UL_ImportQueue(UIList):  # noqa: N801
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):  # noqa: ANN001, PLR0913
        p = Path(item.path)
        row = layout.row(align=True)
        row.label(text=f"{p.parent.name}/{p.name}" if p.parent.name else p.name, icon="MESH_DATA")
        row.operator(UF_OT_QueueRemove.bl_idname, text="", icon="X", emboss=False).index = index


def draw_queue_box(layout, context, *, show_import_button: bool = False) -> None:
    wm = context.window_manager
    queue = wm.uf_import_queue

    box = layout.box()
    header = box.row()
    header.label(text=f"Import Queue ({len(queue)})", icon="OUTLINER_COLLECTION")
    if queue:
        clear = header.row()
        clear.alignment = "RIGHT"
        clear.operator(UF_OT_QueueClear.bl_idname, text="Clear", icon="TRASH")

    if not queue:
        col = box.column(align=True)
        col.label(text="Queue is empty.")
        col.label(text="File browser: right-click a model")
        col.label(text="> Add to Import Queue")
        return

    box.template_list(
        UF_UL_ImportQueue.__name__,
        "",
        wm,
        "uf_import_queue",
        wm,
        "uf_import_queue_index",
        rows=min(len(queue), 8),
        maxrows=8,
    )
    if show_import_button:
        box.operator(UF_OT_ImportQueue.bl_idname, icon="IMPORT")


def draw_attach_mesh_selector(layout, context, settings):
    wm = context.window_manager
    models = collect_models(context)
    _models_cache.clear()
    _models_cache.update({_norm(p): p for p in models})
    multiple = len(models) > 1

    key = wm.uf_attach_mesh if multiple and wm.uf_attach_mesh in _models_cache else None
    override = find_override(context, key) if key else None
    custom = override is not None and override.custom

    if override is None:
        label, icon = "Global", "WORLD"
    else:
        label = _short_name(_models_cache[key]) + (" (custom)" if custom else "")
        icon = "MESH_DATA"

    split = layout.split(factor=0.3)
    split.enabled = multiple
    split.label(text="Mesh:")
    split.menu(UF_MT_AttachMesh.__name__, text=label, icon=icon)

    if override is None:
        return settings
    if custom:
        layout.operator(UF_OT_AttachCustomReset.bl_idname, icon="LOOP_BACK")
    return override


def draw_file_context_menu(self: Menu, context) -> None:  # noqa: ANN001
    layout = self.layout
    selected = get_selected_model_paths(context)
    if not selected:
        return

    queued = {_norm(item.path) for item in context.window_manager.uf_import_queue}
    to_add = [p for p in selected if _norm(p) not in queued]
    to_remove = [p for p in selected if _norm(p) in queued]

    layout.separator()
    if to_add:
        layout.operator(
            UF_OT_QueueAdd.bl_idname,
            text=f"Add to Import Queue ({len(to_add)})",
            icon="ADD",
        )
    if to_remove:
        layout.operator(
            UF_OT_QueueRemoveSelected.bl_idname,
            text=f"Remove from Import Queue ({len(to_remove)})",
            icon="REMOVE",
        )


classes = [
    UFQueueItem,
    UFAttachOverride,
    UF_UL_ImportQueue,
    UF_OT_QueueAdd,
    UF_OT_QueueRemove,
    UF_OT_QueueRemoveSelected,
    UF_OT_QueueClear,
    UF_OT_ImportQueue,
    UF_OT_AttachMeshSelect,
    UF_MT_AttachMesh,
    UF_OT_AttachCustomReset,
]


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)

    WindowManager.uf_import_queue = CollectionProperty(type=UFQueueItem)  # type: ignore[reportAttributeAccessIssue]
    WindowManager.uf_import_queue_index = IntProperty()  # type: ignore[reportAttributeAccessIssue]
    WindowManager.uf_attach_overrides = CollectionProperty(type=UFAttachOverride)  # type: ignore[reportAttributeAccessIssue]
    WindowManager.uf_attach_mesh = StringProperty(name="Mesh")  # type: ignore[reportAttributeAccessIssue]

    menu = getattr(bpy.types, "FILEBROWSER_MT_context_menu", None)
    if menu is not None:
        menu.append(draw_file_context_menu)


def unregister() -> None:
    menu = getattr(bpy.types, "FILEBROWSER_MT_context_menu", None)
    if menu is not None:
        menu.remove(draw_file_context_menu)

    del WindowManager.uf_import_queue  # type: ignore[reportAttributeAccessIssue]
    del WindowManager.uf_import_queue_index  # type: ignore[reportAttributeAccessIssue]
    del WindowManager.uf_attach_overrides  # type: ignore[reportAttributeAccessIssue]
    del WindowManager.uf_attach_mesh  # type: ignore[reportAttributeAccessIssue]

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
