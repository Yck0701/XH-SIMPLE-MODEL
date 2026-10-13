"""Stable reflected types. Schema changes require restart, not Python reinstancing."""
import unreal


@unreal.uclass()
class VFXUserMaterialSettings(unreal.Object):
    material=unreal.uproperty(unreal.MaterialInterface,meta={'Category':'当前材质','DisplayName':'材质 / 材质实例'})


@unreal.uclass()
class VFXImportedModelSettings(unreal.Object):
    model=unreal.uproperty(unreal.Object,meta={'Category':'模型','DisplayName':'自定义模型','AllowedClasses':'/Script/Engine.StaticMesh,/Script/Engine.SkeletalMesh'})
    use_animation=unreal.uproperty(bool,meta={'Category':'动画预览','DisplayName':'使用模型动画'})
    animation=unreal.uproperty(unreal.AnimSequence,meta={'Category':'动画预览','DisplayName':'动画序列','EditCondition':'use_animation'})
    playing=unreal.uproperty(bool,meta={'Category':'动画预览','DisplayName':'播放','EditCondition':'use_animation'})
    looping=unreal.uproperty(bool,meta={'Category':'动画预览','DisplayName':'循环','EditCondition':'use_animation'})
    play_rate=unreal.uproperty(float,meta={'Category':'动画预览','DisplayName':'播放速度','ClampMin':'0.1','ClampMax':'4','UIMin':'0.1','UIMax':'4','EditCondition':'use_animation'})


def number(label,category,lo,hi):
    return unreal.uproperty(float,meta={'DisplayName':label,'Category':category,'ClampMin':str(lo),'ClampMax':str(hi),'UIMin':str(lo),'UIMax':str(hi)})


@unreal.uclass()
class VFXPreviewEnvironmentSettings(unreal.Object):
    environment_cube=unreal.uproperty(unreal.TextureCube,meta={'DisplayName':'环境贴图 / HDRI','Category':'01 环境','ForceShowEngineContent':'true'})
    show_environment=unreal.uproperty(bool,meta={'DisplayName':'显示 HDRI 背景','Category':'01 环境'})
    sky_lighting=unreal.uproperty(bool,meta={'DisplayName':'启用天光与反射','Category':'01 环境'})
    sky_intensity=number('天光强度','01 环境',0,20)
    sky_rotation=number('环境旋转 °','01 环境',0,360)
    light_intensity=number('主光强度','02 主光',0,20)
    light_color=unreal.uproperty(unreal.LinearColor,meta={'DisplayName':'主光颜色','Category':'02 主光'})
    light_pitch=number('主光俯仰 °','02 主光',-90,90)
    light_yaw=number('主光水平角 °','02 主光',-180,180)
    post_processing=unreal.uproperty(bool,meta={'DisplayName':'启用后处理','Category':'03 曝光与后处理'})
    auto_exposure=unreal.uproperty(bool,meta={'DisplayName':'自动曝光','Category':'03 曝光与后处理'})
    exposure_bias=number('曝光补偿 EV','03 曝光与后处理',-10,10)
    bloom=number('泛光强度','03 曝光与后处理',0,10)
    background_color=unreal.uproperty(unreal.LinearColor,meta={'DisplayName':'纯色背景','Category':'04 背景与地板','ToolTip':'关闭 HDRI 背景后生效。'})
    background_intensity=number('纯色背景亮度','04 背景与地板',0,20)
    show_floor=unreal.uproperty(bool,meta={'DisplayName':'显示地板','Category':'04 背景与地板'})
    floor_height=number('地板偏移 cm','04 背景与地板',-10000,10000)
