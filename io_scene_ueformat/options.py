from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bpy.types import Armature

    from io_scene_ueformat.op.settings import UFSettings


@dataclass(slots=True)
class UEFormatOptions:
    link: bool = True
    scale_factor: float = 0.01

    @classmethod
    def from_settings(cls, settings: UFSettings) -> UEFormatOptions:
        return cls(**{
            k: v for k, v in settings.get_props().items()
            if k in inspect.signature(cls).parameters
        })


@dataclass(slots=True)
class UEModelOptions(UEFormatOptions):
    bone_length: float = 4.0
    reorient_bones: bool = False
    bone_display_type: str = "STICK"
    clean_unused_bones: bool = False
    ignore_bone_groups: bool = False
    search_textures: bool = False
    ignore_blend_mode: bool = False
    ignore_normal_texture: bool = False
    import_collision: bool = False
    import_sockets: bool = True
    import_morph_targets: bool = True
    import_virtual_bones: bool = False
    target_lod: int = 0
    allowed_reorient_children: dict = None

    attach_main: bool = False
    attach_skeleton: str = "AUTO"
    attach_root_bone: str = "position"
    attach_target_bone: str = "auto"
    attach_copy_location: bool = True
    attach_copy_rotation: bool = False


@dataclass(slots=True)
class UEAnimOptions(UEFormatOptions):
    rotation_only: bool = False
    import_curves: bool = True
    override_skeleton: Armature | None = None

@dataclass(slots=True)
class UEPoseOptions(UEFormatOptions):
    root_bone: str = ""
    override_skeleton: Armature | None = None