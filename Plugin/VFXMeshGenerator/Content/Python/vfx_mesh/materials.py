"""Packaged unlit VFX material with independent main/mask UV controls."""
import unreal

MASTER = '/VFXMeshGenerator/Materials/M_VFXMesh_Master'
UV_FIELDS = [('FlipU', '翻转 U', False), ('FlipV', '翻转 V', False),
             ('Angle', '旋转角度', 0.), ('FlowU', 'U 流速', 0.), ('FlowV', 'V 流速', 0.)]


def defaults():
    result = {'mode': 'generated', 'main_texture': '', 'mask_texture': '', 'material': '', 'generated_material': '', 'Opacity': 1., 'Brightness': 1.}
    for prefix in ('Main', 'Mask'):
        result.update({prefix + key: value for key, _, value in UV_FIELDS})
    return result


def create_master():
    """Build-time helper; the distributed plugin already contains this material."""
    existing = unreal.load_asset(MASTER)
    if existing:
        return existing
    lib = unreal.MaterialEditingLibrary
    mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        'M_VFXMesh_Master', '/VFXMeshGenerator/Materials', unreal.Material, unreal.MaterialFactoryNew())
    mat.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT)
    mat.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
    mat.set_editor_property('two_sided', True)
    lib.set_material_usage(mat, unreal.MaterialUsage.MATUSAGE_NIAGARA_MESH_PARTICLES)
    def node(cls, x, y, **props):
        n = lib.create_material_expression(mat, cls, x, y)
        for k, v in props.items(): n.set_editor_property(k, v)
        return n
    def wire(a, output, b, pin):
        if not lib.connect_material_expressions(a, output, b, pin):
            raise RuntimeError('材质节点连接失败：' + pin)
    def mul(a, ao, b, bo, x, y):
        n = node(unreal.MaterialExpressionMultiply, x, y)
        wire(a, ao, n, 'A'); wire(b, bo, n, 'B'); return n
    white = unreal.load_asset('/Engine/EngineResources/WhiteSquareTexture')
    if not white: raise RuntimeError('找不到默认白色贴图')
    uv = node(unreal.MaterialExpressionTextureCoordinate, -1600, 0)
    time = node(unreal.MaterialExpressionTime, -1600, 180)
    samples = []
    for index, prefix in enumerate(('Main', 'Mask')):
        y = index * 850
        names = ['UV', 'Time', 'FlipU', 'FlipV', 'Angle', 'FlowU', 'FlowV']
        inputs = []
        for name in names:
            item = unreal.CustomInput()
            item.set_editor_property('input_name', name)
            inputs.append(item)
        custom = node(unreal.MaterialExpressionCustom, -700, y,
            output_type=unreal.CustomMaterialOutputType.CMOT_FLOAT2,
            description=prefix + ' UV',
            inputs=inputs,
            code='float2 p=(UV-0.5)*float2(1-2*FlipU,1-2*FlipV);\n'
                 'float a=radians(Angle); float s=sin(a), c=cos(a);\n'
                 'return float2(c*p.x-s*p.y,s*p.x+c*p.y)+0.5+Time*float2(FlowU,FlowV);')
        wire(uv, '', custom, 'UV'); wire(time, '', custom, 'Time')
        for i, (key, _, default) in enumerate(UV_FIELDS):
            p = node(unreal.MaterialExpressionScalarParameter, -1250, y + 220 + i*110,
                     parameter_name=prefix+key, default_value=float(default), group=prefix+' UV')
            wire(p, '', custom, key)
        sample = node(unreal.MaterialExpressionTextureSampleParameter2D, -350, y,
                      parameter_name=prefix+'Texture', texture=white, group=prefix+' UV')
        wire(custom, '', sample, 'UVs'); samples.append(sample)
    brightness = node(unreal.MaterialExpressionScalarParameter, 0, -200,
                      parameter_name='Brightness', default_value=1., group='Surface')
    opacity = node(unreal.MaterialExpressionScalarParameter, 0, 800,
                   parameter_name='Opacity', default_value=1., group='Surface')
    particle = node(unreal.MaterialExpressionParticleColor, 0, 350)
    color = mul(samples[0], 'RGB', particle, 'RGB', 300, 0)
    color = mul(color, '', brightness, '', 550, 0)
    alpha = mul(samples[0], 'A', samples[1], 'R', 0, 600)
    alpha = mul(alpha, '', opacity, '', 300, 600)
    alpha = mul(alpha, '', particle, 'A', 550, 600)
    lib.connect_material_property(color, '', unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    lib.connect_material_property(alpha, '', unreal.MaterialProperty.MP_OPACITY)
    lib.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def validate(settings):
    p = defaults(); p.update(settings)
    if p['mode'] == 'custom':
        mat = unreal.load_asset(p['material']) if p['material'] else None
        if not isinstance(mat, unreal.MaterialInterface):
            raise ValueError('请指定一个有效的自定义材质。')
    elif p['mode'] != 'generated':
        raise ValueError('材质模式无效。')
    else:
        for key in ('main_texture','mask_texture'):
            if p[key] and not isinstance(unreal.load_asset(p[key]),unreal.Texture2D):
                raise ValueError('贴图不存在或不是 Texture2D。')
    return p


def apply(mesh, settings):
    p = validate(settings)
    if p['mode'] == 'custom':
        mat = unreal.load_asset(p['material'])
        mesh.set_material(0, mat)
        return mat
    master = unreal.load_asset(MASTER)
    if not master: raise RuntimeError('插件基础材质缺失，请重新安装插件。')
    mat = mesh.get_material(0)
    owner = mesh.get_path_name()
    if p['generated_material']:
        previous = unreal.load_asset(p['generated_material'])
        if previous and unreal.EditorAssetLibrary.get_metadata_tag(previous,'VFXMeshGenerator.Material') == owner:
            mat = previous
    if not isinstance(mat, unreal.MaterialInstanceConstant) or unreal.EditorAssetLibrary.get_metadata_tag(mat, 'VFXMeshGenerator.Material') != owner:
        package = mesh.get_path_name().split('.')[0]
        folder, meshname = package.rsplit('/', 1)
        path, name = unreal.AssetToolsHelpers.get_asset_tools().create_unique_asset_name(folder + '/MI_' + meshname, '')
        mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, path.rsplit('/',1)[0], unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
        unreal.MaterialEditingLibrary.set_material_instance_parent(mat, master)
        unreal.EditorAssetLibrary.set_metadata_tag(mat, 'VFXMeshGenerator.Material', owner)
    white = unreal.load_asset('/Engine/EngineResources/WhiteSquareTexture')
    lib = unreal.MaterialEditingLibrary
    for prefix, key in [('Main', 'main_texture'), ('Mask', 'mask_texture')]:
        texture = unreal.load_asset(p[key]) if p[key] else white
        if not isinstance(texture, unreal.Texture2D): raise ValueError('请使用 Texture2D 贴图。')
        lib.set_material_instance_texture_parameter_value(mat, prefix+'Texture', texture)
        for name, _, _ in UV_FIELDS:
            lib.set_material_instance_scalar_parameter_value(mat, prefix+name, float(p[prefix+name]))
    for key in ('Opacity', 'Brightness'):
        lib.set_material_instance_scalar_parameter_value(mat, key, float(p[key]))
    lib.update_material_instance(mat)
    mesh.set_material(0, mat)
    settings['generated_material'] = mat.get_path_name()
    return mat
