import bpy
from bpy.props import PointerProperty
from bpy.types import Context, Menu, Scene

from .bone_ops import UF_OT_ResetBoneOrientation
from . import import_queue
from .import_helpers import UFImportUEAnim, UFImportUEModel, UFImportUEPose
from .panels import UEFORMAT_PT_Panel
from .settings import UFSettings, UF_OT_SaveDefaults, UF_OT_RestoreDefaults

operators = [
    UEFORMAT_PT_Panel, 
    UFImportUEModel, 
    UFImportUEAnim, 
    UFImportUEPose, 
    UFSettings, 
    UF_OT_SaveDefaults, 
    UF_OT_RestoreDefaults,
    UF_OT_ResetBoneOrientation,
]

def draw_import_menu(self: Menu, context: Context) -> None:  # noqa: ARG001
    self.layout.operator(UFImportUEModel.bl_idname, text="Unreal Model (.uemodel)")
    self.layout.operator(UFImportUEAnim.bl_idname, text="Unreal Animation (.ueanim)")
    self.layout.operator(UFImportUEPose.bl_idname, text="Unreal Pose Asset (.uepose)")

def register() -> None:
    import_queue.register()
    for operator in operators:
        bpy.utils.register_class(operator)

    Scene.uf_settings = PointerProperty(type=UFSettings)  # type: ignore[reportAttributeAccessIssue]
    bpy.types.TOPBAR_MT_file_import.append(draw_import_menu)

def unregister() -> None:
    for operator in operators:
        bpy.utils.unregister_class(operator)
    import_queue.unregister()

    del Scene.uf_settings  # type: ignore[reportAttributeAccessIssue]
    bpy.types.TOPBAR_MT_file_import.remove(draw_import_menu)