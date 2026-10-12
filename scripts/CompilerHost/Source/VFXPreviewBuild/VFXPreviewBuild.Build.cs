using UnrealBuildTool;
public class VFXPreviewBuild : ModuleRules {
 public VFXPreviewBuild(ReadOnlyTargetRules Target) : base(Target) {
  PCHUsage=PCHUsageMode.NoPCHs;
  PrivateDependencyModuleNames.AddRange(new[]{"Core","CoreUObject","Engine","UnrealEd","Slate","SlateCore","UMG","AdvancedPreviewScene","GeometryFramework","DynamicMesh","GeometryCore","RenderCore","RHI","InputCore","ApplicationCore","Projects","ToolMenus"});
 }
}
