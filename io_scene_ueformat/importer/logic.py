from __future__ import annotations

import gzip, json
import os, re
import difflib
from pathlib import Path
from typing import cast

import bpy
import numpy as np
from bpy.types import Action, ArmatureModifier, ByteColorAttribute, EditBone, FCurve, Object, PoseBone
from bpy_extras import anim_utils
from mathutils import Matrix, Quaternion, Vector
from math import *

from .constants import MAGIC, MODEL_IDENTIFIER, POSE_IDENTIFIER, ANIM_IDENTIFIER
from .version import EUEFormatVersion
from .archive.reader import FArchiveReader
from .space import to_blender_space
from .dto.model import ModelDto
from .dto.anim import AnimDto, CurveDto as Curve, FloatKeyDto as FloatKey
from .dto.pose import PoseDto
from .reorient_utils import reorient_bones
from .ab_json import extract_ab_target_bones, find_matching_ab_json
from .utils import *
from ..logging import Log
from ..options import UEAnimOptions, UEFormatOptions, UEModelOptions, UEPoseOptions

def set_bone_group_color(arm_obj, pose_bone, palette_name, group_name):
    bg = arm_obj.pose.bone_groups.get(group_name)
    if not bg:
        bg = arm_obj.pose.bone_groups.new(name=group_name)
        bg.color_set = palette_name
    pose_bone.bone_group = bg

class UEFormatImport:
    def __init__(self, options: UEFormatOptions) -> None:
        self.options = options
        self._texture_map = {}
        self._material_file_map = {}
        self._caches_initialized = False

    def _init_caches(self, base_dir: Path):
        if self._caches_initialized:
            return

        self._texture_map = {}
        self._material_file_map = {}
        possible_exts = {'.tga', '.png', '.jpg'}
        target_suffixes = {'.json', '.uasset.json'}
        
        for root, _, files in os.walk(base_dir):
            for f in files:
                name, ext = os.path.splitext(f)
                if ext.lower() in possible_exts:
                    key = name.lower()
                    if key not in self._texture_map:
                        self._texture_map[key] = []
                    self._texture_map[key].append(os.path.join(root, f))
                
                f_lower = f.lower()
                for suffix in target_suffixes:
                    if f_lower.endswith(suffix):
                        if suffix == '.uasset.json':
                            m_name = f[:-12]
                        else:
                            m_name = name
                        key = m_name.lower()
                        if key not in self._material_file_map:
                            self._material_file_map[key] = []
                        self._material_file_map[key].append(os.path.join(root, f))
                        break
                        
        self._caches_initialized = True

    def _resolve_best_path(self, candidate_paths: list[str], reference_path: str | Path | None) -> str | None:
        if not candidate_paths: return None
        if not reference_path: return candidate_paths[0]
        
        ref_parts = Path(reference_path).parts
        best = candidate_paths[0]
        max_m = -1
        for c in candidate_paths:
            c_parts = Path(c).parts
            m = 0
            for p1, p2 in zip(ref_parts, c_parts):
                if p1.lower() == p2.lower(): m += 1
                else: break
            if m > max_m:
                max_m = m
                best = c
        return best

    def _find_material_files(self, material_name: str) -> list[str]:
        material_name_lower = material_name.lower()
        if material_name_lower in self._material_file_map:
            return self._material_file_map[material_name_lower].copy()
        
        candidates = list(self._material_file_map.keys())
        if not candidates:
            return []
            
        matches = difflib.get_close_matches(material_name_lower, candidates, n=1, cutoff=0.8)
        if matches:
            return self._material_file_map[matches[0]].copy()
            
        return []

    def _parse_material_info(self, file_path: str):
        file_path_lower = file_path.lower()
        textures = {'base_name': None, 'normal_name': None}
        blend_mode = 'OPAQUE'
        opacity_clip_value = 0.33

        try:
            if file_path_lower.endswith('.json'):
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                tex_dict = data.get('Textures', {})
                params_dict = data.get('Parameters', {})
                
                for key in ['BaseColor', 'PM_Diffuse', 'Diffuse', 'Albedo', 'BaseMap']:
                    if key in tex_dict:
                        textures['base_name'] = tex_dict[key].split('/')[-1].split('.')[0]
                        break

                for key in tex_dict:
                    if re.match(r"^No[rmalpsRMALPS_]+$", key) or 'normal' in key.lower():
                        textures['normal_name'] = tex_dict[key].split('/')[-1].split('.')[0]
                        break
                        
                b_mode = params_dict.get('BlendMode', 0)
                if b_mode == 1 or params_dict.get('Properties', {}).get('BasePropertyOverrides', {}).get('BlendMode') == 'EBlendMode::BLEND_Masked':
                    blend_mode = 'CLIP'
                elif params_dict.get('IsTranslucent', False) or b_mode in (2, 3):
                    blend_mode = 'BLEND'
                    
                overrides = params_dict.get('Properties', {}).get('BasePropertyOverrides', {})
                if 'OpacityMaskClipValue' in overrides:
                    opacity_clip_value = float(overrides['OpacityMaskClipValue'])
                elif 'OpacityMaskClipValue' in params_dict.get('Properties', {}):
                    opacity_clip_value = float(params_dict['Properties']['OpacityMaskClipValue'])

        except Exception as e:
            Log.warn(f"Failed to parse material file {file_path}: {e}")

        return {
            'texture_name': textures['base_name'],
            'normal_name': textures['normal_name'],
            'blend_mode': blend_mode,
            'opacity_clip_value': opacity_clip_value
        }

    def _apply_texture_to_material(self, material: bpy.types.Material, material_info: dict, reference_path: str | Path | None):
        texture_name = material_info.get('texture_name')
        normal_name = material_info.get('normal_name')
        blend_mode = material_info.get('blend_mode', 'OPAQUE')
        opacity_clip_value = material_info.get('opacity_clip_value', 0.33)
        
        ignore_blend = getattr(self.options, "ignore_blend_mode", False)
        ignore_normal = getattr(self.options, "ignore_normal_texture", False)

        if not texture_name and not normal_name:
            return

        def resolve_texture(name):
            if not name: return None
            cands = self._texture_map.get(name.lower())
            if not cands: return None
            return self._resolve_best_path(cands, reference_path)

        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        nodes.clear()

        bsdf = nodes.new('ShaderNodeBsdfPrincipled')
        bsdf.location = (0, 0)
        output = nodes.new('ShaderNodeOutputMaterial')
        output.location = (300, 0)
        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

        if texture_name:
            texture_path = resolve_texture(texture_name)
            if texture_path:
                tex_node = nodes.new('ShaderNodeTexImage')
                try:
                    img_name = os.path.basename(texture_path)
                    img = bpy.data.images.get(img_name)
                    if not img:
                        img = bpy.data.images.load(texture_path)
                    img.alpha_mode = 'STRAIGHT'
                    tex_node.image = img
                except:
                    pass
                tex_node.location = (-300, 0)
                
                if 'Base Color' in bsdf.inputs:
                    links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])

                if ignore_blend:
                    material.blend_method = 'OPAQUE'
                else:
                    if blend_mode == 'CLIP':
                        if 'Alpha' in bsdf.inputs:
                            links.new(tex_node.outputs['Alpha'], bsdf.inputs['Alpha'])
                        material.blend_method = 'CLIP'
                        material.alpha_threshold = opacity_clip_value
                        material.shadow_method = 'CLIP'
                    elif blend_mode == 'BLEND':
                        if 'Alpha' in bsdf.inputs:
                            links.new(tex_node.outputs['Alpha'], bsdf.inputs['Alpha'])
                        material.blend_method = 'BLEND'
                        material.shadow_method = 'HASHED'
                    else:
                        material.blend_method = 'OPAQUE'

        if normal_name and not ignore_normal:
            normal_path = resolve_texture(normal_name)
            if normal_path:
                norm_tex_node = nodes.new('ShaderNodeTexImage')
                try:
                    img_name = os.path.basename(normal_path)
                    img = bpy.data.images.get(img_name)
                    if not img:
                        img = bpy.data.images.load(normal_path)
                    img.colorspace_settings.name = 'Non-Color'
                    img.alpha_mode = 'CHANNEL_PACKED'
                    norm_tex_node.image = img
                except:
                    pass
                norm_tex_node.location = (-600, -300)
                
                norm_map_node = nodes.new('ShaderNodeNormalMap')
                norm_map_node.location = (-300, -300)
                
                links.new(norm_tex_node.outputs['Color'], norm_map_node.inputs['Color'])
                if 'Normal' in bsdf.inputs:
                    links.new(norm_map_node.outputs['Normal'], bsdf.inputs['Normal'])

        material.use_backface_culling = False
        material.show_transparent_back = False

    def _apply_attach_main(self, armature_object: bpy.types.Object, file_path: Path | None) -> None:
        if not file_path:
            Log.warn("Unable to locate *AB.json")
            return

        ab_path = find_matching_ab_json(file_path)
        if ab_path is None:
            Log.warn(f"No matching *AB.json found")
            return

        _, target_bones = extract_ab_target_bones(ab_path)

        skeleton_choice = getattr(self.options, "attach_skeleton", "AUTO") or "AUTO"
        target_armature = None
        if skeleton_choice != "AUTO":
            target_armature = bpy.data.objects.get(skeleton_choice)
        if target_armature is None:
            candidates = [o for o in bpy.data.objects if o.type == "ARMATURE" and o is not armature_object]
            target_armature = candidates[0] if candidates else None
        if target_armature is None:
            Log.warn("No Skeleton Found")
            return

        target_bone_name = (getattr(self.options, "attach_target_bone", "") or "").strip()
        if target_bone_name.lower() == "auto":
            target_bone_name = ""

        if target_bone_name and target_bone_name not in target_armature.data.bones:
            Log.warn(f"Attach Main: Manually specified {target_bone_name} do not exist, Back Auto")
            target_bone_name = ""

        if not target_bone_name:
            target_bone_name = next((b for b in target_bones if b in target_armature.data.bones), None)

        if not target_bone_name:
            Log.warn(f"Attach Main: {target_armature.name} Can't find {ab_path.name} Specify bone")
            return

        root_bone_name = getattr(self.options, "attach_root_bone", "") or "position"
        root_bone = get_case_insensitive(armature_object.pose.bones, root_bone_name)
        if root_bone is None:
            Log.warn(f"Attach Main: No {armature_object.name} bone, Revert to {root_bone_name}")
            root_bone = next(iter(armature_object.pose.bones), None)
        if root_bone is None:
            Log.warn(f"Attach Main: {armature_object.name} No bones to mount.")
            return

        copy_location = bool(getattr(self.options, "attach_copy_location", True))
        copy_rotation = bool(getattr(self.options, "attach_copy_rotation", False))

        if copy_location:
            constraint = cast(bpy.types.CopyLocationConstraint, root_bone.constraints.new("COPY_LOCATION"))
            constraint.target = target_armature
            constraint.subtarget = target_bone_name

        if copy_rotation:
            constraint = cast(bpy.types.CopyRotationConstraint, root_bone.constraints.new("COPY_ROTATION"))
            constraint.target = target_armature
            constraint.subtarget = target_bone_name

        Log.info(f"Attach Main: {armature_object.name}[{root_bone.name}] -> {target_armature.name}[{target_bone_name}]")

    def import_file(self, path: str | Path):
        path = path if isinstance(path, Path) else Path(path)
        
        if path.suffix.lower() == '.json':
            return self.import_mesh_asset(path)
            
        Log.time_start(f"Import {path}")

        with path.open("rb") as file:
            obj = self.import_data(file.read(), file_path=path)

        Log.time_end(f"Import {path}")
        return obj
    
    def import_mesh_asset(self, path: Path) -> tuple:
        Log.time_start(f"Import MeshAsset {path.name}")
        
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            
        if isinstance(data, list) and len(data) > 0:
            props = data[0].get("Properties", {})
        else:
            props = data.get("Properties", {})

        content_dir = path.parent
        for p in path.parents:
            if p.name.lower() == "content":
                content_dir = p
                break
                
        def resolve_uemodel(obj_path: str, obj_name: str) -> Path | None:
            if "'" in obj_name:
                obj_name = obj_name.split("'")[1]
            
            if obj_path:
                clean_path = obj_path.split('.')[0]
                if clean_path.startswith("/Game/"):
                    clean_path = clean_path[6:]
                target = content_dir / f"{clean_path}.uemodel"
                if target.exists():
                    return target
                    
            for file_path in content_dir.rglob(f"{obj_name}.uemodel"):
                return file_path
                
            return None

        main_mesh_data = props.get("FashionMeshData", {}).get("CharacterMesh", {})
        main_path = resolve_uemodel(main_mesh_data.get("ObjectPath", ""), main_mesh_data.get("ObjectName", ""))
        
        main_armature = None
        if main_path:
            orig_attach = getattr(self.options, "attach_main", False)
            self.options.attach_main = False 
            
            res = self.import_file(main_path)
            main_armature = res[0] if isinstance(res, tuple) else res
            
            self.options.attach_main = orig_attach
        else:
            Log.warn(f"MeshAsset: Cannot resolve main mesh {main_mesh_data.get('ObjectName')}")
            return None, None

        if main_armature and main_armature.type == 'ARMATURE':
            attached_data = props.get("ArrayFashionAttachedMeshData", [])
            for item in attached_data:
                char_mesh = item.get("CharacterMesh", {})
                att_path = resolve_uemodel(char_mesh.get("ObjectPath", ""), char_mesh.get("ObjectName", ""))
                socket_name = item.get("SocketName", "")
                
                if att_path:
                    orig_attach = getattr(self.options, "attach_main", False)
                    self.options.attach_main = False
                    
                    res = self.import_file(att_path)
                    att_armature = res[0] if isinstance(res, tuple) else res
                    
                    self.options.attach_main = orig_attach
                    
                    if att_armature and att_armature.type == 'ARMATURE':
                        root_bone_name = getattr(self.options, "attach_root_bone", "position")
                        att_pose_bones = att_armature.pose.bones
                        
                        root_bone = att_pose_bones.get(root_bone_name)
                        if not root_bone and len(att_pose_bones) > 0:
                            root_bone = att_pose_bones[0]
                            
                        if root_bone and socket_name in main_armature.pose.bones:
                            do_loc = getattr(self.options, "attach_copy_location", False)
                            do_rot = getattr(self.options, "attach_copy_rotation", False)
                            
                            if getattr(self.options, "attach_copy_auto", True):
                                rel_loc = item.get("RelativeLocation", {})
                                loc_is_zero = all(abs(rel_loc.get(k, 0.0)) < 0.001 for k in ("X", "Y", "Z"))
                                do_loc = loc_is_zero
                                
                                rel_rot = item.get("RelativeRotation", {})
                                rot_is_zero = all(abs(rel_rot.get(k, 0.0)) < 0.001 for k in ("Pitch", "Yaw", "Roll"))
                                do_rot = rot_is_zero
                            
                            if do_loc:
                                const_loc = root_bone.constraints.new('COPY_LOCATION')
                                const_loc.target = main_armature
                                const_loc.subtarget = socket_name
                            if do_rot:
                                const_rot = root_bone.constraints.new('COPY_ROTATION')
                                const_rot.target = main_armature
                                const_rot.subtarget = socket_name

        Log.time_end(f"Import MeshAsset {path.name}")
        return main_armature, None

    def import_data(self, data: bytes, file_path: Path = None) -> Object | Action:
        with FArchiveReader(data) as ar:
            return self.import_data_by_reader(ar, file_path=file_path)

    def _parse_header(self, ar: FArchiveReader) -> tuple[dict, FArchiveReader]:
        magic = ar.read_string(len(MAGIC))
        if magic != MAGIC:
            msg = "Invalid magic"
            raise ValueError(msg)

        identifier = ar.read_fstring()
        file_version = EUEFormatVersion(int.from_bytes(ar.read_byte(), byteorder="big"))
        if file_version > EUEFormatVersion.LatestVersion:
            msg = f"File Version {file_version} is not supported for this version of the importer."
            Log.error(msg)
            raise ValueError(msg)

        object_name = ar.read_fstring()
        object_path = ""
        if file_version >= EUEFormatVersion.AttributeFormatRestructure:
            object_path = ar.read_fstring()

        Log.info(f"Importing {object_name}")

        read_archive = ar
        is_compressed = ar.read_bool()
        if is_compressed:
            compression_type = ar.read_fstring()
            uncompressed_size = ar.read_int()
            _compressed_size = ar.read_int()

            if compression_type == "GZIP":
                read_archive = FArchiveReader(gzip.decompress(ar.read_to_end()))
            elif compression_type == "ZSTD":
                from .. import zstd_decompressor
                read_archive = FArchiveReader(
                    zstd_decompressor.decompress(ar.read_to_end(), uncompressed_size)
                )
            else:
                msg = f"Unknown Compression Type: {compression_type}"
                Log.error(msg)
                raise ValueError(msg)

        read_archive.file_version = file_version

        header = {
            "identifier": identifier,
            "file_version": file_version,
            "object_name": object_name,
            "object_path": object_path,
        }
        return header, read_archive

    def _parse_body(self, header: dict, ar: FArchiveReader):
        if header["file_version"] >= EUEFormatVersion.AttributeFormatRestructure:
            from . import deserialize as parser
        else:
            from . import legacy as parser

        identifier = header["identifier"]
        if identifier == MODEL_IDENTIFIER:
            return parser.read_model(ar)
        if identifier == ANIM_IDENTIFIER:
            return parser.read_anim(ar)
        if identifier == POSE_IDENTIFIER:
            return parser.read_pose(ar)

        msg = f"Unknown identifier: {identifier}"
        Log.error(msg)
        raise ValueError(msg)

    def import_data_by_reader(self, ar: FArchiveReader, file_path: Path = None) -> Object | Action:
        header, body = self._parse_header(ar)
        dto = self._parse_body(header, body)
        to_blender_space(dto, header["file_version"], self.options.scale_factor)

        if header["identifier"] == MODEL_IDENTIFIER:
            return self.import_uemodel_data(dto, header["object_name"], file_path)
        if header["identifier"] == ANIM_IDENTIFIER:
            return self.import_ueanim_data(dto, header["object_name"], file_path=file_path)
        if header["identifier"] == POSE_IDENTIFIER:
            return self.import_uepose_data(dto, header["object_name"])

        msg = f"Unknown identifier: {header['identifier']}"
        Log.error(msg)
        raise ValueError


    def import_uemodel_data(self, dto: ModelDto, name: str, file_path: Path = None) -> tuple[bpy.types.Object, ModelDto]:
        assert isinstance(self.options, UEModelOptions)

        data = dto

        if getattr(self.options, "search_textures", False) and file_path:
            search_dir = file_path.parent
            for _ in range(3):
                if search_dir.parent == search_dir:
                    break
                if search_dir.name.lower() in ['content', 'exports', 'game', 'characters']:
                    break
                search_dir = search_dir.parent
            self._init_caches(search_dir)

        # meshes
        return_object = None
        target_lod = min(self.options.target_lod, len(data.lods) - 1)
        created_lods: list[Object] = []
        for index, lod in enumerate(data.lods):
            if index != target_lod:
                continue
                
            lod_name = f"{name}_{lod.name}"
            mesh_data = bpy.data.meshes.new(lod_name)
            mesh_data.from_pydata(lod.vertices, [], lod.indices)

            mesh_object = bpy.data.objects.new(lod_name, mesh_data)
            return_object = mesh_object
            if self.options.link:
                bpy.context.collection.objects.link(mesh_object)

            # normals
            if len(lod.normals) > 0:
                mesh_data.polygons.foreach_set("use_smooth", [True] * len(mesh_data.polygons))
                mesh_data.normals_split_custom_set_from_vertices(lod.normals)
                mesh_data.use_auto_smooth = True

            # weights
            if lod.weights and data.skeleton and data.skeleton.bones:
                for weight in lod.weights:
                    bone_name = data.skeleton.bones[weight.bone_index].name
                    vertex_group = mesh_object.vertex_groups.get(bone_name)
                    if not vertex_group:
                        vertex_group = mesh_object.vertex_groups.new(name=bone_name)
                    vertex_group.add([weight.vertex_index], weight.weight, "ADD")

            # morph targets
            if self.options.import_morph_targets and lod.morphs:
                if not mesh_object.data.shape_keys:
                    mesh_object.shape_key_add(name="Basis", from_mix=False)

                for morph in lod.morphs:
                    key = mesh_object.shape_key_add(from_mix=False)
                    key.name = morph.name
                    key.interpolation = "KEY_LINEAR"

                    for delta in morph.deltas:
                        key.data[delta.vertex_index].co += Vector(delta.position)
                        
                    key.value = 0

            squish = lambda array: array.reshape(array.size)
            do_remapping = lambda array, indices: array[indices]

            vertices = [vertex for polygon in mesh_data.polygons for vertex in polygon.vertices]
            for color_info in lod.colors:
                remapped = do_remapping(color_info.data, vertices)
                vertex_color = cast(
                    ByteColorAttribute,
                    mesh_data.color_attributes.new(
                        domain="CORNER",
                        type="BYTE_COLOR",
                        name=color_info.name,
                    ),
                )
                vertex_color.data.foreach_set("color", squish(remapped))

            for i, uvs in enumerate(lod.uvs):
                remapped = do_remapping(uvs, vertices)
                uv_layer = mesh_data.uv_layers.new(name="UV" + str(i))
                uv_layer.data.foreach_set("uv", squish(remapped))

            # materials
            if lod.materials:
                for i, material in enumerate(lod.materials):
                    mat = bpy.data.materials.get(material.material_name)
                    if mat is None:
                        mat = bpy.data.materials.new(name=material.material_name)
                        
                        if getattr(self.options, "search_textures", False) and file_path:
                            mat_file_paths = self._find_material_files(material.material_name)
                            if mat_file_paths:
                                clean_mat_name = re.sub(r'\.\d+$', '', material.material_name)
                                
                                def score_path(p):
                                    p_lower = p.lower()
                                    s = 0
                                    
                                    keywords = [k for k in re.split(r'[_ -]', clean_mat_name.lower()) if len(k) > 2]
                                    dir_name = os.path.dirname(p_lower)
                                    
                                    for kw in keywords:
                                        if kw in dir_name:
                                            s += 1
                                            
                                    return (s, -len(p_lower))
                                    
                                mat_file_paths.sort(key=score_path, reverse=True)
                                
                                best_mat_file_path = self._resolve_best_path(mat_file_paths, file_path)
                                
                                if best_mat_file_path:
                                    mat_info = self._parse_material_info(best_mat_file_path)
                                    if mat_info:
                                        self._apply_texture_to_material(mat, mat_info, best_mat_file_path)
                                    
                    mesh_data.materials.append(mat)

                    start_face_index = material.first_index // 3
                    end_face_index = start_face_index + material.num_faces
                    for face_index in range(start_face_index, end_face_index):
                        mesh_data.polygons[face_index].material_index = i

            created_lods.append(mesh_object)

        # skeleton
        if data.skeleton and (data.skeleton.bones or (self.options.import_sockets and data.skeleton.sockets)):
            armature_data = bpy.data.armatures.new(name=name)
            armature_data.display_type = getattr(self.options, "bone_display_type", "STICK")

            orig_parent_indices = {b.parent_index for b in data.skeleton.bones}
            orig_leaf_names = {b.name for i, b in enumerate(data.skeleton.bones) if i not in orig_parent_indices}

            template_armature_object = bpy.data.objects.new(name + "_Template_Skeleton", armature_data)
            template_armature_object.show_in_front = True
            bpy.context.collection.objects.link(template_armature_object)
            bpy.context.view_layer.objects.active = template_armature_object
            template_armature_object.select_set(state=True)

            if data.skeleton.bones:
                # create bones
                bpy.ops.object.mode_set(mode="EDIT")
                edit_bones = armature_data.edit_bones
                for bone_ in data.skeleton.bones:
                    bone_pos = Vector(bone_.position)
                    bone_rot = Quaternion((bone_.rotation[3], bone_.rotation[0], bone_.rotation[1], bone_.rotation[2]))
                    bone_scale = Vector(bone_.scale) if getattr(bone_, "scale", None) is not None else Vector((1.0, 1.0, 1.0))

                    edit_bone = edit_bones.new(bone_.name)
                    edit_bone["orig_loc"] = bone_pos
                    edit_bone["orig_quat"] = bone_rot.conjugated()
                    edit_bone.length = self.options.bone_length * self.options.scale_factor

                    bone_matrix = Matrix.LocRotScale(bone_pos, bone_rot, bone_scale)

                    if bone_.parent_index >= 0:
                        parent_bone = cast(EditBone | None, edit_bones.get(data.skeleton.bones[bone_.parent_index].name))
                        edit_bone.parent = parent_bone
                        bone_matrix = cast(Matrix, parent_bone.matrix) @ bone_matrix

                    edit_bone.matrix = bone_matrix

                    if not self.options.reorient_bones:
                        edit_bone["post_quat"] = bone_rot
                bpy.ops.object.mode_set(mode="OBJECT")

            # sockets
            if self.options.import_sockets and data.skeleton.sockets:
                bpy.ops.object.mode_set(mode="EDIT")
                for socket in data.skeleton.sockets:
                    socket_bone = edit_bones.new(socket.name)
                    socket_bone.layers[1] = True
                    socket_bone.layers[0] = False
                    socket_bone["is_socket"] = True

                    parent_bone = cast(EditBone | None, get_case_insensitive(edit_bones, socket.parent_name))
                    if parent_bone is None:
                        continue
                    socket_bone.parent = parent_bone
                    socket_bone.length = self.options.bone_length * self.options.scale_factor
                    socket_bone.matrix = (
                        cast(Matrix, parent_bone.matrix)
                        @ Matrix.Translation(socket.position)
                        @ Quaternion((socket.rotation[3], socket.rotation[0], socket.rotation[1], socket.rotation[2])).to_matrix().to_4x4()
                    )
                bpy.ops.object.mode_set(mode="OBJECT")

            if data.skeleton.bones and self.options.reorient_bones:
                bpy.ops.object.mode_set(mode="EDIT")

                reorient_bones(
                    armature_data,
                    bone_length=self.options.bone_length * self.options.scale_factor,
                    allowed_reorient_children=self.options.allowed_reorient_children,
                )

                bpy.ops.object.mode_set(mode="OBJECT")

            if getattr(self.options, "clean_unused_bones", False) and data.skeleton.bones:
                used_bones = set()
                
                target_lod_idx = min(self.options.target_lod, len(data.lods) - 1)
                if target_lod_idx >= 0:
                    lod = data.lods[target_lod_idx]
                    if lod.weights:
                        for weight in lod.weights:
                            b_name = data.skeleton.bones[weight.bone_index].name
                            used_bones.add(b_name)
                                
                def mark_parent(idx):
                    p_idx = data.skeleton.bones[idx].parent_index
                    if p_idx >= 0:
                        p_name = data.skeleton.bones[p_idx].name
                        if p_name not in used_bones:
                            used_bones.add(p_name)
                            mark_parent(p_idx)
                            
                for i, b in enumerate(data.skeleton.bones):
                    if b.name in used_bones:
                        mark_parent(i)
                        
                bpy.ops.object.mode_set(mode="EDIT")
                edit_bones = armature_data.edit_bones
                bones_to_remove = []
                for b in edit_bones:
                    if b.get("is_socket"):
                        if b.parent and b.parent.name not in used_bones:
                            bones_to_remove.append(b)
                    elif b.name not in used_bones:
                        bones_to_remove.append(b)
                        
                for b in bones_to_remove:
                    if b.name in edit_bones: 
                        edit_bones.remove(b)
                bpy.ops.object.mode_set(mode="OBJECT")

            if created_lods:
                bpy.data.objects.remove(template_armature_object)
            else:
                template_armature_object.name = name
                return_object = template_armature_object

                if self.options.import_virtual_bones:
                    bpy.ops.object.mode_set(mode="EDIT")
                    edit_bones = armature_data.edit_bones
                    for virtual in data.skeleton.virtual_bones:
                        source_bone = edit_bones.get(virtual.source_name)
                        if source_bone is None:
                            continue

                        virtual_bone = edit_bones.new(virtual.virtual_name)
                        virtual_bone.layers[2] = True
                        virtual_bone.layers[0] = False

                        virtual_bone.head = source_bone.tail
                        virtual_bone.tail = source_bone.head

                    bpy.ops.object.mode_set(mode="POSE")

                    for virtual in data.skeleton.virtual_bones:
                        virtual_bone = template_armature_object.pose.bones.get(virtual.virtual_name)
                        if virtual_bone is None:
                            continue

                        constraint = virtual_bone.constraints.new("IK")
                        constraint.target = template_armature_object
                        constraint.subtarget = virtual.target_name
                        constraint.chain_count = 1
                        virtual_bone.ik_stretch = 1

                    bpy.ops.object.mode_set(mode="OBJECT")

                # template colors
                if not getattr(self.options, "ignore_bone_groups", False):
                    for bone in template_armature_object.pose.bones:
                        if bone.name in orig_leaf_names and bone.name.lower():
                            set_bone_group_color(template_armature_object, bone, "THEME03", "Leaf Bones")
                    for socket in data.skeleton.sockets:
                        socket_bone = template_armature_object.pose.bones.get(socket.name)
                        if socket_bone is not None:
                            set_bone_group_color(template_armature_object, socket_bone, "THEME05", "Sockets")
                    for virtual in data.skeleton.virtual_bones:
                        virtual_bone = template_armature_object.pose.bones.get(virtual.virtual_name)
                        if virtual_bone is not None:
                            set_bone_group_color(template_armature_object, virtual_bone, "THEME11", "Virtual Bones")

            for lod in created_lods:
                armature_object = bpy.data.objects.new(lod.name + "_Skeleton", armature_data)
                armature_object.show_in_front = True
                return_object = armature_object

                if self.options.link:
                    bpy.context.collection.objects.link(armature_object)
                bpy.context.view_layer.objects.active = armature_object
                armature_object.select_set(state=True)
                lod.parent = armature_object

                armature_modifier = cast(ArmatureModifier, lod.modifiers.new(armature_object.name, type="ARMATURE"))
                armature_modifier.show_expanded = False
                armature_modifier.use_vertex_groups = True
                armature_modifier.object = armature_object

                bpy.ops.object.mode_set(mode="POSE")

                # lod colors
                if not getattr(self.options, "ignore_bone_groups", False):
                    for bone in armature_object.pose.bones:
                        if not (vertex_group := lod.vertex_groups.get(bone.name)) or not has_vertex_weights(lod, vertex_group):
                            set_bone_group_color(armature_object, bone, "THEME14", "Unused Bones")
                            continue
                        if bone.name in orig_leaf_names and bone.name.lower():
                            set_bone_group_color(armature_object, bone, "THEME03", "Leaf Bones")
                    for socket in data.skeleton.sockets:
                        socket_bone = armature_object.pose.bones.get(socket.name)
                        if socket_bone is not None:
                            set_bone_group_color(armature_object, socket_bone, "THEME05", "Sockets")
                    for virtual in data.skeleton.virtual_bones:
                        virtual_bone = armature_object.pose.bones.get(virtual.virtual_name)
                        if virtual_bone is not None:
                            set_bone_group_color(armature_object, virtual_bone, "THEME11", "Virtual Bones")

                bpy.ops.object.mode_set(mode="OBJECT")

                if getattr(self.options, "attach_main", False):
                    self._apply_attach_main(armature_object, file_path)

        # collision
        if self.options.import_collision and data.collisions:
            for index, collision in enumerate(data.collisions):
                collision_name = index if collision.name == "None" else collision.name
                collision_object_name = f"UCX_{name}_{collision_name}"
                collision_mesh_data = bpy.data.meshes.new(collision_object_name)
                collision_mesh_data.from_pydata(collision.vertices, [], collision.indices)

                collision_mesh_object = bpy.data.objects.new(collision_object_name, collision_mesh_data)
                collision_mesh_object.display_type = "WIRE"
                if self.options.link:
                    bpy.context.collection.objects.link(collision_mesh_object)

        return return_object, data

    def import_ueanim_data(self, dto: AnimDto, name: str, file_path: Path = None) -> tuple[bpy.types.Action, AnimDto]:
        assert isinstance(self.options, UEAnimOptions)

        data = dto
        action = bpy.data.actions.new(name=name)

        armature = self.options.override_skeleton or get_active_armature()
        assert isinstance(armature, bpy.types.Object)
        
        if armature_anim_data := armature.animation_data:
            armature_anim_data.action = None
        
        if self.options.link:
            armature.animation_data_create()
            armature.animation_data.action = action

        if self.options.import_curves and file_path:
            json_path = file_path.with_suffix(".json")
            if json_path.exists():
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        jdata = json.load(f)
                    
                    scene = bpy.context.scene
                    current_fps = scene.render.fps / scene.render.fps_base
                    fps = current_fps

                    if isinstance(jdata, list) and len(jdata) > 0:
                        anim_seq = jdata[0]
                        
                        props = anim_seq.get("Properties", {})
                        frame_rate_data = props.get("PlatformTargetFrameRate", {})
                        fps_value = frame_rate_data.get("Value") or frame_rate_data.get("Default")
                        
                        if fps_value:
                            num = fps_value.get("Numerator", 30)
                            den = fps_value.get("Denominator", 1)
                            den = den if den > 0 else 1
                            target_fps = num / den
                            
                            if target_fps > 0 and abs(current_fps - target_fps) > 1e-4:
                                scene.render.fps = round(target_fps)
                                scene.render.fps_base = round(target_fps) / target_fps
                                
                            fps = target_fps
                        elif data.metadata and data.metadata.frames_per_second > 0:
                            fps = data.metadata.frames_per_second

                        if "CompressedCurveData" in anim_seq:
                            float_curves = anim_seq["CompressedCurveData"].get("FloatCurves", [])
                            for fc in float_curves:
                                c_name = fc.get("CurveName")
                                keys_data = fc.get("FloatCurve", {}).get("Keys", [])
                                
                                if not keys_data:
                                    continue

                                first_val = keys_data[0].get("Value", 0.0)
                                is_constant = all(abs(k.get("Value", 0.0) - first_val) < 1e-6 for k in keys_data)

                                new_keys = []
                                if is_constant:
                                    frame = int(round(keys_data[0].get("Time", 0.0) * fps))
                                    new_keys.append(FloatKey(frame=frame, value=first_val))
                                else:
                                    for i, k in enumerate(keys_data):
                                        val = k.get("Value", 0.0)
                                        frame = int(round(k.get("Time", 0.0) * fps))
                                        
                                        if i == 0 or i == len(keys_data) - 1:
                                            new_keys.append(FloatKey(frame=frame, value=val))
                                        else:
                                            prev_val = keys_data[i-1].get("Value", 0.0)
                                            next_val = keys_data[i+1].get("Value", 0.0)
                                            if abs(val - prev_val) > 1e-6 or abs(val - next_val) > 1e-6:
                                                new_keys.append(FloatKey(frame=frame, value=val))
                                
                                existing_curve = next((c for c in data.curves if c.name == c_name), None)
                                if existing_curve:
                                    existing_curve.keys = new_keys
                                else:
                                    data.curves.append(Curve(name=c_name, keys=new_keys))
                except Exception as e:
                    Log.warn(f"Failed to load JSON curves from {json_path.name}: {e}")

        # bone anim data
        pose_bones = armature.pose.bones
        for track in data.tracks:
            bone = get_case_insensitive(pose_bones, track.name)
            if bone is None:
                continue

            def create_fcurves(name: str, count: int, key_count: int | None, bone: PoseBone) -> list[FCurve]:
                path = bone.path_from_id(name)
                curves: list[FCurve] = []
                for i in range(count):
                    curve = action.fcurves.new(path, index=i)
                    curve.keyframe_points.add(key_count)
                    curves.append(curve)
                return curves

            def add_key(curves: list[FCurve], vector: Vector | list[float] | Quaternion, key_index: int, frame: int) -> None:
                for i in range(len(vector)):
                    curves[i].keyframe_points[key_index].co = frame, vector[i]
                    curves[i].keyframe_points[key_index].interpolation = "LINEAR"

            orig_loc = Vector(orig_loc) if (orig_loc := bone.bone.get("orig_loc")) else Vector()
            orig_quat = Quaternion(orig_quat) if (orig_quat := bone.bone.get("orig_quat")) else Quaternion()
            post_quat = Quaternion(post_quat) if (post_quat := bone.bone.get("post_quat")) else Quaternion()

            if not self.options.rotation_only:
                loc_curves = create_fcurves("location", 3, len(track.position_keys), bone)
                scale_curves = create_fcurves("scale", 3, len(track.scale_keys), bone)
                for index, key in enumerate(track.position_keys):
                    pos = Vector(key.value)
                    pos -= orig_loc
                    pos.rotate(post_quat.conjugated())
                    add_key(loc_curves, pos, index, key.frame)

                for index, key in enumerate(track.scale_keys):
                    add_key(scale_curves, key.value, index, key.frame)

            rot_curves = create_fcurves("rotation_quaternion", 4, len(track.rotation_keys), bone)
            for index, key in enumerate(track.rotation_keys):
                p_quat = Quaternion((key.value[3], key.value[0], key.value[1], key.value[2])).conjugated()
                q = post_quat.copy()
                q.rotate(orig_quat)
                quat = q
                q = post_quat.copy()
                q.rotate(p_quat)
                quat.rotate(q.conjugated())

                add_key(rot_curves, quat, index, key.frame)

            bone.matrix_basis.identity()

        # curve anim data
        if self.options.import_curves:
            meshes = []
            if armature.type == 'ARMATURE':
                meshes = [child for child in armature.children if child.type == 'MESH' and child.data.shape_keys]
            elif armature.type == 'MESH' and armature.data.shape_keys:
                meshes = [armature]
                
            for mesh in meshes:
                shape_keys = mesh.data.shape_keys
                shape_keys.name = "Pose Asset"
                if shape_key_anim_data := shape_keys.animation_data:
                    shape_key_anim_data.action = None
    
                shape_keys_action = bpy.data.actions.new(name=f"{name}_{mesh.name}_Curves")
                
                if self.options.link:
                    shape_keys.animation_data_create()
                    shape_keys.animation_data.action = shape_keys_action
    
                key_blocks = shape_keys.key_blocks
                for key_block in key_blocks:
                    key_block.value = 0
                    
                for curve in data.curves:
                    if not (shape_key := best(key_blocks, lambda block: block.name.lower(), curve.name.lower())):
                        continue
                        
                    for key in curve.keys:
                        shape_key.value = key.value
                        shape_key.keyframe_insert(data_path="value", frame=key.frame)

        return action, data

    def import_uepose_data(self, dto: PoseDto, name: str):
        assert isinstance(self.options, UEPoseOptions)

        data = dto
        selected_armature = self.options.override_skeleton or get_active_armature()
        assert isinstance(selected_armature, bpy.types.Object)
        
        selected_mesh = get_armature_mesh(selected_armature)
        original_shape_key_lock = selected_mesh.show_only_shape_key
        original_mode = bpy.context.active_object.mode
        bpy.ops.object.mode_set(mode="OBJECT")
        armature_modifier: bpy.types.ArmatureModifier = first(selected_mesh.modifiers, lambda mod: mod.type == "ARMATURE")

        selected_mesh.show_only_shape_key = False
        bone_swap_orig_parents(selected_armature)
        muted_constraints = disable_constraints(selected_armature)

        if not selected_mesh.data.shape_keys:
            selected_mesh.shape_key_add(name="Basis", from_mix=False)

        original_values = {}
        for shape_key in selected_mesh.data.shape_keys.key_blocks:
            if shape_key.value != 0:
                original_values[shape_key.name] = shape_key.value
                shape_key.value = 0
                
        root_bone = selected_armature.pose.bones.get(self.options.root_bone) or selected_armature.pose.bones[0]

        pose_names = []
        for pose in data.poses:
            pose_names.append(pose.name)

            bpy.context.view_layer.objects.active = selected_armature
            bpy.ops.object.mode_set(mode="POSE")
            bpy.ops.pose.select_all(action="SELECT")
            bpy.ops.pose.transforms_clear()
            bpy.ops.pose.select_all(action="DESELECT")

            contributed = False
            for pose_key in pose.keys:
                pose_bone: bpy.types.PoseBone = get_case_insensitive(selected_armature.pose.bones, pose_key.bone_name)
                
                if not pose_bone:
                    continue
                if root_bone and not bone_has_parent(pose_bone, root_bone):
                    continue
                if not bone_hierarchy_has_vertex_groups(pose_bone, selected_mesh.vertex_groups):
                    continue

                pose_bone.matrix_basis.identity()

                rotation = pose_key.rotation
                edit_bone = pose_bone.bone
                post_quat = Quaternion(post_quat) if (post_quat := edit_bone.get("post_quat")) else Quaternion()

                q = post_quat.copy()
                q.rotate(make_quat(rotation))
                quat = post_quat.copy()
                quat.rotate(q.conjugated())
                pose_bone.rotation_quaternion = quat.conjugated() @ pose_bone.rotation_quaternion

                loc = make_vector(pose_key.position)
                loc.rotate(post_quat.conjugated())

                pose_bone.location = pose_bone.location + loc
                pose_bone.scale = Vector((1, 1, 1)) + make_vector(pose_key.scale)

                pose_bone.rotation_quaternion.normalize()
                contributed = True

            if not contributed:
                continue

            bpy.ops.object.mode_set(mode="OBJECT")
            bpy.context.view_layer.objects.active = selected_mesh
            selected_mesh.select_set(True)
            bpy.ops.object.modifier_apply_as_shapekey(keep_modifier=True, modifier=armature_modifier.name)

            selected_mesh.data.shape_keys.key_blocks[-1].name = pose.name
            selected_mesh.data.shape_keys.key_blocks[-1].value = 0

        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = selected_mesh
        selected_mesh.select_set(True)

        key_blocks = selected_mesh.data.shape_keys.key_blocks
        for pose in data.poses:
            if len(pose.curves) == 0:
                continue

            pose_name = pose.name
            if pose_name in key_blocks:
                pose_name = f"curve_{pose_name}"
            
            contributed = False
            for curve in pose.curves:
                target_curve_name = data.curve_names[curve.curve_index]
                if not (curve_shape_key := key_blocks.get(target_curve_name)):
                    continue

                curve_value = curve.influence
                if curve_value < curve_shape_key.slider_min:
                    curve_shape_key.slider_min = curve_value - 1.0

                if curve_value > curve_shape_key.slider_max:
                    curve_shape_key.slider_max = curve_value + 1.0

                curve_shape_key.value = curve_value
                contributed = True

            if contributed:
                selected_mesh.shape_key_add(name=pose_name, from_mix=True)
            
            for key in key_blocks:
                key.value = 0
                
        if len(original_values) > 0:
            for key_block in key_blocks:
                if orig_value := original_values.get(key_block.name):
                    key_block.value = orig_value
            
        bpy.context.view_layer.objects.active = selected_armature
        bpy.ops.object.mode_set(mode="POSE")
        bpy.ops.pose.select_all(action="SELECT")
        bpy.ops.pose.transforms_clear()
        bpy.ops.pose.select_all(action="DESELECT")

        bone_swap_orig_parents(selected_armature)
        for constraint in muted_constraints:
            constraint.mute = False

        selected_mesh.show_only_shape_key = original_shape_key_lock
        bpy.ops.object.mode_set(mode=original_mode)
        bpy.context.view_layer.objects.active = selected_mesh