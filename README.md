# VFX 特效网格生成器

在 Unreal 编辑器里直接调整特效模型，Niagara 引用同一个 Static Mesh 后，拖动参数就能看到变化。

**[下载 Windows 启动器](https://github.com/Yck0701/XH-SIMPLE-MODEL/releases/latest)** · **[使用教学](docs/使用教学.md)**

## 三步开始

1. 下载发行版中的 `VFXMeshLauncher-版本-Windows.zip`，**完整解压**，双击 `VFX特效网格启动器.exe`。无需安装 Python。
2. 选择你的 `.uproject`，确认对应的 UE 5.8 引擎，点击 **安装 / 更新插件**。
3. 首次安装后重新启动工程，点击 **启动 UE / 打开面板**，创建模型；在 Niagara 的 Mesh Renderer 里选中它。

启动器默认每次启动都检查本仓库的正式发行版。下载后校验 SHA-256，并在工程的 Saved 目录备份旧插件。断网时可以安装内置版本。

## 功能

- 九种参数化网格、分段、变换、轴向、枢轴与法线翻转。
- 主贴图与遮罩贴图，独立 UV 翻转、旋转与流速。
- 编辑器内实时修改原 Static Mesh；支持 Niagara 粒子颜色和透明度。
- 参数随模型保存，载入所选模型继续修改，支持撤回。
- 兼容的 Python 脚本更新可热重载，保留当前面板中的未保存参数。
- 面板、材质资源、插件依赖或热更新组件变化时，需要保存并关闭工程后更新。
- 启动器自身也可在“更新中心”升级，采用独立版本目录，无需覆盖正在运行的 EXE。

## 适用环境

Windows 10/11 x64、Unreal Engine **5.8**。已在 UE 5.8.3 验证。仅供编辑器制作使用；生成结果是普通 Static Mesh 与材质资产。依赖 Python Script Plugin、Editor Scripting Utilities、Geometry Scripting 和 Niagara，插件描述文件会声明这些依赖。若工程显式禁用了依赖，启动器会提示先启用。

半透明特效网格默认关闭 Nanite。第一次从旧版升级后，需要重启一次 UE 才能建立热更新连接。

## 源码与维护

- `Plugin/VFXMeshGenerator`：Unreal 插件。
- `Launcher`：Python/Tk Windows 启动器。
- `tests`：更新、安装、备份、失败回退及界面启动检查测试。
- `scripts/build_release.py`：构建离线包、EXE 与发行版清单。
- [发布新版本](docs/发布新版本.md)

本仓库尚未指定开源许可证。源码公开不代表授予额外使用或分发许可。
