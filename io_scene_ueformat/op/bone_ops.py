import bpy
from bpy.types import Context, Operator

from ..importer.reorient_utils import has_reorient_data, is_skeleton_reoriented, reset_bone_orientation
from ..importer.utils import get_active_armature


class UF_OT_ResetBoneOrientation(Operator):  # noqa: N801
    bl_idname = "uf.reset_bone_orientation"
    bl_label = "Reset Bone Orientation"
    bl_description = "Undo Reorient Bones on the active armature, restoring the original imported transforms"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        armature = get_active_armature()
        return armature is not None and armature.type == "ARMATURE" and has_reorient_data(armature.data)

    def execute(self, context: Context) -> set[str]:
        armature_object = get_active_armature()
        if armature_object is None or armature_object.type != "ARMATURE":
            self.report({"ERROR"}, "No armature selected/active")
            return {"CANCELLED"}

        armature_data = armature_object.data
        if not has_reorient_data(armature_data):
            self.report({"ERROR"}, "This armature has no UE Format import data to reset")
            return {"CANCELLED"}

        if not is_skeleton_reoriented(armature_data):
            self.report({"INFO"}, "Armature is already in its original (non-reoriented) orientation")
            return {"FINISHED"}

        settings = getattr(context.scene, "uf_settings", None)
        bone_length = settings.bone_length * settings.scale_factor if settings is not None else 1.0

        original_active = context.view_layer.objects.active
        original_mode = armature_object.mode

        context.view_layer.objects.active = armature_object
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            reset_bone_orientation(armature_data, bone_length=bone_length)
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
            if original_active is not None:
                context.view_layer.objects.active = original_active
            if original_mode != "OBJECT" and original_active is armature_object:
                bpy.ops.object.mode_set(mode=original_mode)

        self.report({"INFO"}, f"Reset bone orientation on '{armature_object.name}'")
        return {"FINISHED"}
