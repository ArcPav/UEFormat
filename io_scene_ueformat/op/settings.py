import os
import json
from bpy.props import BoolProperty, FloatProperty, IntProperty, EnumProperty
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
    "import_curves": True
}

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_defaults = json.load(f)
            DEFAULT_VALUES.update(user_defaults)
    except Exception:
        pass

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

    def get_props(self) -> dict[str, Any]:
        props = {}
        for key in self.__annotations__.keys():
            props[key] = getattr(self, key)
        return props

class UF_OT_SaveDefaults(Operator):
    bl_idname = "uf.save_defaults"
    bl_label = "设为默认"
    bl_description = "将当前设置保存 以后都是这个状态"

    def execute(self, context):
        settings = context.scene.uf_settings
        data = settings.get_props()
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            self.report({'ERROR'}, f"保存失败 {str(e)}")
        return {'FINISHED'}

class UF_OT_RestoreDefaults(Operator):
    bl_idname = "uf.restore_defaults"
    bl_label = "恢复设置"
    bl_description = "清空保存的设置状态 恢复默认值"

    def execute(self, context):
        if os.path.exists(CONFIG_PATH):
            try:
                os.remove(CONFIG_PATH)
            except Exception as e:
                self.report({'ERROR'}, f"恢复失败: {str(e)}")
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
        return {'FINISHED'}