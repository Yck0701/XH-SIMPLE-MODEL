import unreal
try:
    from vfx_mesh.runtime import startup
    startup()
    from vfx_mesh import launcher_bridge
    launcher_bridge.startup()
except Exception:
    import traceback
    unreal.log_warning('[VFX Mesh] 插件初始化失败：' + traceback.format_exc())
