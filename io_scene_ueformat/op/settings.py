import os
import json
from bpy.props import BoolProperty, FloatProperty, IntProperty, EnumProperty, StringProperty
from bpy.types import PropertyGroup, Operator
from typing import Any

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULT_VALUES = {
    "scale_factor": 0.01,
    "bone_length": 2.0,
    "reorient_bones": False,
    "bone_display_type": "STICK",
    "clean_unused_bones": False,
    "ignore_bone_groups": False,
    "search_textures": False,
    "ignore_blend_mode": False,       
    "ignore_normal_texture": False,   
    "target_lod": 0,
    "import_collision": False,
    "import_morph_targets": True,
    "import_sockets": True,
    "import_virtual_bones": False,
    "rotation_only": False,
    "import_curves": True,
    "attach_main": False,
    "attach_skeleton": "AUTO",
    "attach_root_bone": "position",
    "attach_target_bone": "auto",
    "attach_copy_auto": True,
    "attach_copy_location": False,
    "attach_copy_rotation": False,
}

_attach_skeleton_items_cache: list[tuple[str, str, str]] = [("AUTO", "Auto", "Automatically select skeleton")]

def _get_attach_skeleton_items(self, context):
    items = [("AUTO", "Auto", "Automatically select skeleton")]
    if context is not None and context.scene is not None:
        for obj in context.scene.objects:
            if obj.type == "ARMATURE":
                items.append((obj.name, obj.name, ""))

    _attach_skeleton_items_cache[:] = items
    return _attach_skeleton_items_cache

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_defaults = json.load(f)
            DEFAULT_VALUES.update(user_defaults)
    except Exception:
        pass

def _update_attach_auto(self, context):
    if self.attach_copy_auto:
        self.attach_copy_location = False
        self.attach_copy_rotation = False

def _update_attach_manual(self, context):
    if self.attach_copy_location or self.attach_copy_rotation:
        self.attach_copy_auto = False

class UFSettings(PropertyGroup):
    scale_factor: FloatProperty(name="Scale", default=DEFAULT_VALUES.get("scale_factor", 0.01), min=0.01)
    
    bone_length: FloatProperty(name="Bone Length", default=DEFAULT_VALUES.get("bone_length", 4.0), min=0.1)
    reorient_bones: BoolProperty(name="Reorient Bones", default=DEFAULT_VALUES.get("reorient_bones", False))
    
    bone_display_type: EnumProperty(
        name="Bone Display",
        items=[
            ('OCTAHEDRAL', "八面锥 (OCTAHEDRAL)", ""),
            ('STICK', "棍形 (STICK)", ""),
            ('BBONE', "柔性骨骼 (B-BONE)", ""),
            ('ENVELOPE', "封套 (ENVELOPE)", ""),
            ('WIRE', "线框 (WIRE)", ""),
        ],
        default=DEFAULT_VALUES.get("bone_display_type", "STICK")
    )
    
    clean_unused_bones: BoolProperty(name="Clean Unused Bones", default=DEFAULT_VALUES.get("clean_unused_bones", False))

    ignore_bone_groups: BoolProperty(name="Ignore Bone Group", default=DEFAULT_VALUES.get("ignore_bone_groups", False))
    
    search_textures: BoolProperty(name="Search Textures", default=DEFAULT_VALUES.get("search_textures", True))
    ignore_blend_mode: BoolProperty(name="Ignore Blend Mode (A)", default=DEFAULT_VALUES.get("ignore_blend_mode", False))
    ignore_normal_texture: BoolProperty(name="Ignore Normal Texture", default=DEFAULT_VALUES.get("ignore_normal_texture", False))
    
    target_lod: IntProperty(name="Level of Detail", default=DEFAULT_VALUES.get("target_lod", 0), min=0)
    import_collision: BoolProperty(name="Import Collision", default=DEFAULT_VALUES.get("import_collision", False))
    import_morph_targets: BoolProperty(name="Import Morph Targets", default=DEFAULT_VALUES.get("import_morph_targets", True))
    import_sockets: BoolProperty(name="Import Sockets", default=DEFAULT_VALUES.get("import_sockets", True))
    import_virtual_bones: BoolProperty(name="Import Virtual Bones", default=DEFAULT_VALUES.get("import_virtual_bones", False))

    rotation_only: BoolProperty(name="Rotation Only", default=DEFAULT_VALUES.get("rotation_only", False))
    import_curves: BoolProperty(name="Import Curves", default=DEFAULT_VALUES.get("import_curves", True))

    attach_main: BoolProperty(name="Attach Main (NTE)", default=DEFAULT_VALUES.get("attach_main", False))
    attach_skeleton: EnumProperty(name="Skeleton", items=_get_attach_skeleton_items)
    attach_root_bone: StringProperty(name="Root Bone", default=DEFAULT_VALUES.get("attach_root_bone", "position"))
    attach_target_bone: StringProperty(name="Target Bone", default=DEFAULT_VALUES.get("attach_target_bone", "auto"))
    attach_copy_auto: BoolProperty(name="Auto", default=DEFAULT_VALUES.get("attach_copy_auto", True), update=_update_attach_auto)
    attach_copy_location: BoolProperty(name="Location", default=DEFAULT_VALUES.get("attach_copy_location", False), update=_update_attach_manual)
    attach_copy_rotation: BoolProperty(name="Rotate", default=DEFAULT_VALUES.get("attach_copy_rotation", False), update=_update_attach_manual)

    def get_props(self) -> dict[str, Any]:
        props = {}
        for key in getattr(type(self), '__annotations__', {}).keys():
            props[key] = getattr(self, key)
        return props

class UF_OT_SaveDefaults(Operator):
    bl_idname = "uf.save_defaults"
    bl_label = "Save settings"
    bl_description = "Save current state"

    def execute(self, context):
        settings = context.scene.uf_settings
        data = settings.get_props()
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            self.report({'ERROR'}, f"Save failed {str(e)}")
        return {'FINISHED'}

class UF_OT_RestoreDefaults(Operator):
    bl_idname = "uf.restore_defaults"
    bl_label = "Restore settings"
    bl_description = "Clear the saved state"

    def execute(self, context):
        if os.path.exists(CONFIG_PATH):
            try:
                os.remove(CONFIG_PATH)
            except Exception as e:
                self.report({'ERROR'}, f"Reset failed: {str(e)}")
                return {'CANCELLED'}

        settings = context.scene.uf_settings
        settings.scale_factor = 0.01
        settings.bone_length = 2.0
        settings.reorient_bones = False
        settings.bone_display_type = "STICK"
        settings.clean_unused_bones = False
        settings.ignore_bone_groups = False
        settings.search_textures = False
        settings.ignore_blend_mode = False
        settings.ignore_normal_texture = False
        settings.target_lod = 0
        settings.import_collision = False
        settings.import_morph_targets = True
        settings.import_sockets = True
        settings.import_virtual_bones = False
        settings.rotation_only = False
        settings.import_curves = True
        settings.attach_main = False
        settings.attach_skeleton = "AUTO"
        settings.attach_root_bone = "position"
        settings.attach_target_bone = "auto"
        settings.attach_copy_auto = True
        settings.attach_copy_location = False
        settings.attach_copy_rotation = False
        return {'FINISHED'}