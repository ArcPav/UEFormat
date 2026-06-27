from typing import cast

from bpy.types import Context, Operator, Panel

from ..typing import UFormatContext, UFSettings


class UEFORMAT_PT_Panel(Panel):  # noqa: N801
    bl_category = "UE Format"
    bl_label = "UE Format"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"

    def draw(self, context: Context | UFormatContext) -> None:
        assert hasattr(context.scene, "uf_settings")  # noqa: S101

        context = cast(UFormatContext, context)
        uf_settings = context.scene.uf_settings

        self.draw_general_options(self, uf_settings)
        self.draw_model_options(self, uf_settings)
        self.draw_anim_options(self, uf_settings)
        self.draw_pose_options(self, uf_settings)

    @staticmethod
    def draw_general_options(obj: Panel | Operator, settings: UFSettings) -> None:
        box = obj.layout.box()
        box.label(text="General", icon="SETTINGS")
        box.row().prop(settings, "scale_factor")

        row = box.row(align=True)
        row.operator("uf.save_defaults", text="Set as Default")
        row.operator("uf.restore_defaults", text="Restore Settings")

    @staticmethod
    def draw_model_options(
        obj: Panel | Operator,
        settings: UFSettings,
        *,
        import_menu: bool = False,
    ) -> None:
        box = obj.layout.box()
        box.label(text="Model", icon="OUTLINER_OB_MESH")
        box.row().prop(settings, "target_lod")
        box.row().prop(settings, "import_collision")
        box.row().prop(settings, "import_morph_targets")
        box.row().prop(settings, "import_sockets")
        box.row().prop(settings, "import_virtual_bones")
        
        box.row().prop(settings, "reorient_bones")

        box.row().prop(settings, "clean_unused_bones")
        box.row().prop(settings, "ignore_bone_groups")
        
        box.row().prop(settings, "search_textures")
        if settings.search_textures:
            col = box.column(align=True)
            r1 = col.row()
            r1.separator(factor=2.0)
            r1.prop(settings, "ignore_blend_mode")
            r2 = col.row()
            r2.separator(factor=2.0)
            r2.prop(settings, "ignore_normal_texture")

        split = box.split(factor=0.3)
        split.label(text="Bone Setup")
        row = split.row(align=True)
        row.prop(settings, "bone_length", text="Length")  
        row.prop(settings, "bone_display_type", text="")

        if not import_menu:
            box.row().operator("uf.import_uemodel", icon="MESH_DATA")

    @staticmethod
    def draw_anim_options(
        obj: Panel | Operator,
        settings: UFSettings,
        *,
        import_menu: bool = False,
    ) -> None:
        box = obj.layout.box()
        box.label(text="Animation", icon="ACTION")
        box.row().prop(settings, "rotation_only")
        box.row().prop(settings, "import_curves")

        if not import_menu:
            box.row().operator("uf.import_ueanim", icon="ANIM")

    @staticmethod
    def draw_pose_options(
            obj: Panel | Operator,
            settings: UFSettings,
            *,
            import_menu: bool = False,
    ) -> None:
        box = obj.layout.box()
        box.label(text="Pose", icon="OUTLINER_OB_ARMATURE")

        if not import_menu:
            box.row().operator("uf.import_uepose", icon="ARMATURE_DATA")