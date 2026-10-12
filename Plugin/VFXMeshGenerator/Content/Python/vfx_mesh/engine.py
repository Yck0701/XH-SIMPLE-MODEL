"""Editor asset operations. Never replaces an existing asset object or edits unowned meshes."""
import json
import re
import unreal
from . import geometry

TAG = 'VFXMeshGenerator.Settings'
SCHEMA = 1


def read(mesh):
    if not isinstance(mesh, unreal.StaticMesh):
        raise ValueError('请在内容浏览器中选中本工具生成的静态网格。')
    raw = unreal.EditorAssetLibrary.get_metadata_tag(mesh, TAG)
    if not raw:
        raise ValueError('该模型没有生成参数。请选择本工具生成的模型，或新建模型。')
    data = json.loads(raw)
    if data.get('version') != SCHEMA:
        raise ValueError('参数版本不兼容，请使用对应版本插件。')
    data['geometry'] = geometry.validate(data['geometry'])
    return data


def dynamic_mesh(settings):
    data = geometry.build(settings)
    mesh = unreal.DynamicMesh()
    buffers = unreal.GeometryScriptSimpleMeshBuffers(
        vertices=[unreal.Vector(*v) for v in data['vertices']],
        normals=[unreal.Vector(*n) for n in data['normals']],
        uv0=[unreal.Vector2D(*uv) for uv in data['uv']],
        triangles=[unreal.IntVector(*f) for f in data['faces']])
    unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(mesh, buffers)
    if mesh.get_triangle_count() != len(data['faces']):
        raise RuntimeError('生成网格的三角形数量异常，未写入资产。')
    return mesh, {'vertices': len(data['vertices']), 'triangles': len(data['faces'])}


def write_settings(mesh, settings, surface=None):
    unreal.EditorAssetLibrary.set_metadata_tag(mesh, TAG, json.dumps({
        'version': SCHEMA, 'geometry': geometry.validate(settings),
        'surface': surface or {}}, ensure_ascii=False))


def create(settings, folder='/Game/VFXMeshes', name=None, surface=None):
    settings = geometry.validate(settings)
    folder = folder.strip().rstrip('/')
    if not re.fullmatch(r'/Game(?:/[A-Za-z0-9_\u4e00-\u9fff]+)+', folder):
        raise ValueError('保存目录格式：/Game/VFXMeshes（可使用中文、字母、数字、下划线）。')
    name = (name or ('SM_VFX_' + settings['shape'])).strip()
    if not re.fullmatch(r'[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*', name):
        raise ValueError('模型名称请使用中文、字母、数字、下划线，且不能以数字开头。')
    path, _ = unreal.AssetToolsHelpers.get_asset_tools().create_unique_asset_name(folder + '/' + name, '')
    dynamic, stats = dynamic_mesh(settings)
    options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions(
        enable_nanite=False, enable_collision=False,
        enable_recompute_normals=False, enable_recompute_tangents=True)
    mesh, outcome = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(dynamic, path, options)
    if not mesh or outcome != unreal.GeometryScriptOutcomePins.SUCCESS:
        raise RuntimeError('创建静态网格失败：' + str(outcome))
    write_settings(mesh, settings, surface)
    return mesh, stats


def update(mesh, settings, surface=None):
    read(mesh)  # Refuse destructive changes to arbitrary user assets.
    dynamic, stats = dynamic_mesh(settings)
    nanite = unreal.MeshNaniteSettings(enabled=False)
    options = unreal.GeometryScriptCopyMeshToAssetOptions(
        emit_transaction=True, enable_recompute_normals=False,
        enable_recompute_tangents=True, enable_remove_degenerates=True,
        apply_nanite_settings=True, new_nanite_settings=nanite,
        replace_materials=False)
    _, outcome = unreal.GeometryScript_AssetUtils.copy_mesh_to_static_mesh(
        dynamic, mesh, options, unreal.GeometryScriptMeshWriteLOD(lod_index=0))
    if outcome != unreal.GeometryScriptOutcomePins.SUCCESS:
        raise RuntimeError('更新静态网格失败：' + str(outcome))
    write_settings(mesh, settings, surface)
    return stats


def save(mesh):
    read(mesh)
    material = mesh.get_material(0)
    if material and unreal.EditorAssetLibrary.get_metadata_tag(material, 'VFXMeshGenerator.Material'):
        if not unreal.EditorAssetLibrary.save_loaded_asset(material):
            raise RuntimeError('材质保存失败，请检查目录权限。')
    if not unreal.EditorAssetLibrary.save_loaded_asset(mesh):
        raise RuntimeError('网格保存失败，请检查目录权限。')


def selected():
    assets = unreal.EditorUtilityLibrary.get_selected_assets()
    meshes = [asset for asset in assets if isinstance(asset, unreal.StaticMesh)]
    if len(meshes) != 1:
        raise ValueError('请在内容浏览器中只选中一个本工具生成的静态网格。')
    read(meshes[0])
    return meshes[0]
