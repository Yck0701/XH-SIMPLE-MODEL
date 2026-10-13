#include "CoreMinimal.h"
#include "Components/NativeWidgetHost.h"
#include "Components/DynamicMeshComponent.h"
#include "UDynamicMesh.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "SEditorViewport.h"
#include "AdvancedPreviewScene.h"
#include "EditorViewportClient.h"
#include "Engine/World.h"
#include "Engine/Engine.h"
#include "Materials/Material.h"
#include "MaterialDomain.h"
#include "CanvasItem.h"
#include "CanvasTypes.h"
#include "BatchedElements.h"
#include "SceneView.h"
#include "SceneManagement.h"
#include "Widgets/SNullWidget.h"
#include "InputCoreTypes.h"
#include "Framework/Application/SlateApplication.h"
#include "Widgets/SWindow.h"
#include "MaterialDetails.h"
#include "Environment.h"
#include "Layout.h"

class FVFXViewportClient : public FEditorViewportClient
{
public:
    TSharedPtr<FVFXPreviewScene> Scene;
    FBox MeshBox=FBox(FVector(-100),FVector(100));
    bool bAxes=true;
    bool bTurntable=false;
    bool bWireframe=false;
    float WireOpacity=.35f;
    int WireDraws=0,WireSegments=0;
    TArray<TPair<FVector,FVector>> Edges;
    FVFXViewportClient(TSharedPtr<FVFXPreviewScene> InScene,TSharedRef<SEditorViewport> Widget)
        :FEditorViewportClient(nullptr,InScene.Get(),Widget),Scene(InScene)
    {
        SetViewMode(VMI_Lit);SetRealtime(true);bSetListenerPosition=false;
        EngineShowFlags.SetSeparateTranslucency(true);
        EngineShowFlags.SetCompositeEditorPrimitives(true);
        DrawHelper.bDrawGrid=true;DrawHelper.bDrawPivot=false;DrawHelper.bDrawWorldBox=false;
        bUsingOrbitCamera=true;OverrideNearClipPlane(1.f);
        SetViewLocation(FVector(300,-300,220));SetViewRotation(FRotator(-25,135,0));
        SetLookAtLocation(FVector::ZeroVector,true);
    }
    virtual bool ShouldOrbitCamera() const override {return true;}
    void SetAxes(bool Enabled){bAxes=Enabled;bDrawAxes=Enabled;}
    void SetGrid(bool Enabled){DrawHelper.bDrawGrid=Enabled;}
    void Focus(){FocusViewportOnBox(MeshBox+FVector::ZeroVector,true);Invalidate();}
    virtual bool InputKey(const FInputKeyEventArgs& Args) override
    {
        if(Args.Key==EKeys::F && Args.Event==IE_Pressed){Focus();return true;}
        // Shared profile navigation shortcuts write per-project user settings.
        // Environment editing here is confined to the workspace's local profile.
        return FEditorViewportClient::InputKey(Args);
    }
    virtual bool InputAxis(const FInputKeyEventArgs& Args) override
    {
        if(Scene->HandleViewportInput(Args.Viewport,Args.InputDevice,Args.Key,Args.AmountDepressed,Args.DeltaTime,Args.NumSamples,Args.IsGamepad()))return true;
        return FEditorViewportClient::InputAxis(Args);
    }
    virtual void Tick(float DeltaSeconds) override
    {
        FEditorViewportClient::Tick(DeltaSeconds);
        if(bTurntable)
        {
            const FVector Pivot=GetLookAtLocation();
            SetViewLocation(Pivot+(GetViewLocation()-Pivot).RotateAngleAxis(DeltaSeconds*20,FVector::UpVector));
            SetViewRotation((Pivot-GetViewLocation()).Rotation());
        }
        Scene->GetWorld()->Tick(LEVELTICK_All,DeltaSeconds);
    }
    virtual void Draw(const FSceneView* View,FPrimitiveDrawInterface* PDI) override
    {
        FEditorViewportClient::Draw(View,PDI);
        if(bAxes)
        {
            const float Length=FMath::Max(20.f,float(MeshBox.GetExtent().GetMax())*.7f);
            DrawCoordinateSystem(PDI,FVector::ZeroVector,FRotator::ZeroRotator,Length,SDPG_Foreground,2.f);
            PDI->DrawPoint(FVector::ZeroVector,FLinearColor::Yellow,9.f,SDPG_Foreground);
        }
    }
    virtual void DrawCanvas(FViewport& Viewport,FSceneView& View,FCanvas& Canvas) override
    {
        FEditorViewportClient::DrawCanvas(Viewport,View,Canvas);
        WireSegments=0;
        if(bWireframe && WireOpacity>0.f)
        {
            ++WireDraws;
            // Draw topology after the scene, using an unjittered projection.
            // It cannot fight scene depth or accumulate TAA/MSAA edge artifacts.
            const FMatrix Projection=View.ViewMatrices.GetViewMatrix()*View.ViewMatrices.ComputeProjectionNoAAMatrix();
            for(const auto& Edge:Edges)
            {
                FVector4 A=Projection.TransformFVector4(FVector4(Edge.Key,1));
                FVector4 B=Projection.TransformFVector4(FVector4(Edge.Value,1));
                double Start=0,End=1;
                auto Clip=[&](double DA,double DB)
                {
                    if(DA<0 && DB<0)return false;
                    if(DA<0)Start=FMath::Max(Start,DA/(DA-DB));
                    if(DB<0)End=FMath::Min(End,DA/(DA-DB));
                    return Start<=End;
                };
                if(!Clip(A.W+A.X,B.W+B.X)||!Clip(A.W-A.X,B.W-B.X)||
                   !Clip(A.W+A.Y,B.W+B.Y)||!Clip(A.W-A.Y,B.W-B.Y)||
                   !Clip(A.Z,B.Z)||!Clip(A.W-A.Z,B.W-B.Z))continue;
                const FVector4 Delta=B-A;B=A+Delta*End;A+=Delta*Start;
                FVector2D P,Q;
                if(!View.ScreenToPixel(A,P)||!View.ScreenToPixel(B,Q))continue;
                // FCanvasLineItem/AddLine force alpha to 1; use the engine's
                // translucent batch so opacity blends with the material below.
                Canvas.GetBatchedElements(FCanvas::ET_Line)->AddTranslucentLine(
                    FVector(P.X,P.Y,0),FVector(Q.X,Q.Y,0),FLinearColor(.05f,.8f,1.f,WireOpacity),
                    Canvas.GetHitProxyId(),1.2f,0.f,false);
                ++WireSegments;
            }
        }
        if(!bAxes)return;
        const float Length=FMath::Max(20.f,float(MeshBox.GetExtent().GetMax())*.7f);
        const FVector Points[]={FVector::ZeroVector,FVector(Length,0,0),FVector(0,Length,0),FVector(0,0,Length)};
        const TCHAR* Labels[]={TEXT("Origin 0,0,0"),TEXT("X"),TEXT("Y"),TEXT("Z")};
        const FLinearColor Colors[]={FLinearColor::Yellow,FLinearColor::Red,FLinearColor::Green,FLinearColor(.2,.5,1)};
        for(int32 i=0;i<4;i++)
        {
            FVector2D Pixel;
            if(View.WorldToPixel(Points[i],Pixel))
            {
                FCanvasTextItem Text(Pixel+FVector2D(6,6),FText::FromString(Labels[i]),GEngine->GetSmallFont(),Colors[i]);
                Text.EnableShadow(FLinearColor::Black);Canvas.DrawItem(Text);
            }
        }
    }
};

class SVFXNativeViewport : public SEditorViewport
{
public:
    SLATE_BEGIN_ARGS(SVFXNativeViewport){}SLATE_END_ARGS()
    TSharedPtr<FVFXPreviewScene> Scene;
    TSharedPtr<FVFXViewportClient> VFXClient;
    UDynamicMeshComponent* Mesh=nullptr;
    UMeshComponent* External=nullptr;
    bool CastShadow=true;
    void Construct(const FArguments&)
    {
        Scene=MakeShared<FVFXPreviewScene>();
        Mesh=NewObject<UDynamicMeshComponent>(GetTransientPackage(),NAME_None,RF_Transient);
        Scene->AddComponent(Mesh,FTransform::Identity);
        SEditorViewport::Construct(SEditorViewport::FArguments());
    }
    virtual TSharedRef<FEditorViewportClient> MakeEditorViewportClient() override
    {
        VFXClient=MakeShared<FVFXViewportClient>(Scene,SharedThis(this));return VFXClient.ToSharedRef();
    }
    void Update(UDynamicMesh* Dynamic,UMaterialInterface* Material,bool bFrame)
    {
        if(External){Scene->RemoveComponent(External);External=nullptr;}
        Mesh->SetVisibility(true);Mesh->SetCastShadow(CastShadow);
        Mesh->SetDynamicMesh(Dynamic);Mesh->SetMaterial(0,Material?Material:UMaterial::GetDefaultMaterial(MD_Surface));
        Mesh->UpdateBounds();Mesh->MarkRenderStateDirty();VFXClient->MeshBox=Mesh->Bounds.GetBox();
        SetEdges(Dynamic);
        Scene->MeshBottom=float(VFXClient->MeshBox.Min.Z);Scene->PlaceFloor();
        if(bFrame)VFXClient->Focus();Invalidate();
    }
    void SetEdges(UDynamicMesh* Dynamic)
    {
        VFXClient->Edges.Reset();
        if(!Dynamic)return;
        Dynamic->ProcessMesh([this](const UE::Geometry::FDynamicMesh3& Data)
        {
            VFXClient->Edges.Reserve(Data.EdgeCount());
            for(int ID:Data.EdgeIndicesItr())
            {
                const auto Pair=Data.GetEdgeV(ID);
                VFXClient->Edges.Emplace(Data.GetVertex(Pair.A),Data.GetVertex(Pair.B));
            }
        });
    }
    void SetExternal(UMeshComponent* Component,UDynamicMesh* Topology,bool bFrame)
    {
        Mesh->SetVisibility(false);
        if(External!=Component)
        {
            if(External)Scene->RemoveComponent(External);
            External=Component;Scene->AddComponent(Component,FTransform::Identity);
        }
        External->SetCastShadow(CastShadow);External->UpdateBounds();
        VFXClient->MeshBox=External->Bounds.GetBox();SetEdges(Topology);
        Scene->MeshBottom=float(VFXClient->MeshBox.Min.Z);Scene->PlaceFloor();
        if(bFrame)VFXClient->Focus();Invalidate();
    }
};

static TMap<FString,TWeakPtr<SVFXNativeViewport>> Viewports;
// Python editing APIs identify global parameters by name only. Reject ambiguous
// names and layer parameters so an edit cannot target a different parameter.
extern "C" __declspec(dllexport) int VFX_GlobalParameter(const wchar_t* MaterialPath,int Kind,const wchar_t* Name)
{
    if(!IsInGameThread())return 0;
    auto* Material=FindObject<UMaterialInterface>(nullptr,MaterialPath);if(!Material)return 0;
    int Matches=0;int FoundKind=-1;
    for(int Type=0;Type<4;Type++)
    {
        TArray<FMaterialParameterInfo> Info;TArray<FGuid> Guids;
        if(Type==0)Material->GetAllScalarParameterInfo(Info,Guids);
        if(Type==1)Material->GetAllVectorParameterInfo(Info,Guids);
        if(Type==2)Material->GetAllTextureParameterInfo(Info,Guids);
        if(Type==3)Material->GetAllStaticSwitchParameterInfo(Info,Guids);
        for(const auto& P:Info)if(P.Name==FName(Name) && P.Association==EMaterialParameterAssociation::GlobalParameter)
        {Matches++;FoundKind=Type;}
    }
    return Matches==1 && FoundKind==Kind;
}
extern "C" __declspec(dllexport) int VFX_Attach(const wchar_t* HostPath)
{
    if(!IsInGameThread())return 0;
    auto* Host=FindObject<UNativeWidgetHost>(nullptr,HostPath);if(!Host)return 0;
    auto Widget=SNew(SVFXNativeViewport);Host->SetContent(Widget);Viewports.Add(HostPath,Widget);
    if(auto Cached=Host->GetCachedWidget())
    {
        if(auto Window=FSlateApplication::Get().FindWidgetWindow(Cached.ToSharedRef()))
        {
            const FVector2D Size=Window->GetSizeInScreen();
            if(Size.X<1180 || Size.Y<720)Window->Resize(FVector2D(1600,900));
        }
    }
    return 1;
}
extern "C" __declspec(dllexport) int VFX_SetMesh(const wchar_t* HostPath,const wchar_t* MeshPath,const wchar_t* MaterialPath,int Frame)
{
    if(!IsInGameThread())return 0;
    auto* Weak=Viewports.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;
    auto* Dynamic=FindObject<UDynamicMesh>(nullptr,MeshPath);if(!View||!Dynamic)return 0;
    auto* Material=FindObject<UMaterialInterface>(nullptr,MaterialPath);View->Update(Dynamic,Material,Frame!=0);return 1;
}
extern "C" __declspec(dllexport) int VFX_Options(const wchar_t* HostPath,int Axes,int Grid,int Floor,int Environment,int Turntable)
{
    if(!IsInGameThread())return 0;
    auto* Weak=Viewports.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;if(!View)return 0;
    View->VFXClient->SetAxes(Axes!=0);View->VFXClient->SetGrid(Grid!=0);
    View->VFXClient->bTurntable=Turntable!=0;
    auto* Profile=View->Scene->GetCurrentProfile();Profile->bShowFloor=Floor!=0;Profile->bShowEnvironment=Environment!=0;
    View->Scene->SetFloorVisibility(Floor!=0,true);View->Scene->SetEnvironmentVisibility(Environment!=0,true);
    View->Invalidate();return 1;
}
extern "C" __declspec(dllexport) int VFX_SetComponent(const wchar_t* HostPath,const wchar_t* ComponentPath,const wchar_t* TopologyPath,int Frame)
{
    if(!IsInGameThread())return 0;
    auto* Weak=Viewports.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;
    auto* Component=FindObject<UMeshComponent>(nullptr,ComponentPath);if(!View||!Component)return 0;
    View->SetExternal(Component,FindObject<UDynamicMesh>(nullptr,TopologyPath),Frame!=0);return 1;
}
extern "C" __declspec(dllexport) int VFX_Shadow(const wchar_t* HostPath,int Enabled)
{
    if(!IsInGameThread())return 0;
    auto* Weak=Viewports.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;if(!View)return 0;
    View->CastShadow=Enabled!=0;View->Mesh->SetCastShadow(View->CastShadow);
    if(View->External)View->External->SetCastShadow(View->CastShadow);
    View->Invalidate();return 1;
}
extern "C" __declspec(dllexport) int VFX_Focus(const wchar_t* HostPath)
{
    if(!IsInGameThread())return 0;
    auto* Weak=Viewports.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;if(!View)return 0;View->VFXClient->Focus();return 1;
}
extern "C" __declspec(dllexport) int VFX_Wireframe(const wchar_t* HostPath,int Enabled)
{
    if(!IsInGameThread())return 0;
    auto* Weak=Viewports.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;if(!View)return 0;
    View->VFXClient->bWireframe=Enabled!=0;View->Invalidate();return 1;
}
extern "C" __declspec(dllexport) int VFX_WireOpacity(const wchar_t* HostPath,double Opacity)
{
    if(!IsInGameThread() || !FMath::IsFinite(Opacity))return 0;
    auto* Weak=Viewports.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;if(!View)return 0;
    View->VFXClient->WireOpacity=float(FMath::Clamp(Opacity,0.,1.));View->Invalidate();return 1;
}
extern "C" __declspec(dllexport) int VFX_Camera(const wchar_t* HostPath,double* Values)
{
    if(!IsInGameThread())return 0;
    auto* Weak=Viewports.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;if(!View)return 0;
    const auto P=View->VFXClient->GetViewLocation();const auto R=View->VFXClient->GetViewRotation();
    Values[0]=P.X;Values[1]=P.Y;Values[2]=P.Z;Values[3]=R.Pitch;Values[4]=R.Yaw;Values[5]=R.Roll;return 1;
}
extern "C" __declspec(dllexport) int VFX_WireInfo(const wchar_t* HostPath,double* Values)
{
    if(!IsInGameThread())return 0;
    auto* Weak=Viewports.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;if(!View)return 0;
    Values[0]=View->VFXClient->Edges.Num();Values[1]=View->VFXClient->bWireframe;Values[2]=View->VFXClient->WireDraws;
    Values[3]=View->VFXClient->WireSegments;
    return 1;
}
extern "C" __declspec(dllexport) int VFX_EnvironmentGet(const wchar_t* HostPath,double* Values,wchar_t* CubePath,int Capacity)
{
    if(!IsInGameThread() || !Values || !CubePath || Capacity<1)return 0;
    auto* Weak=Viewports.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;if(!View)return 0;
    View->Scene->Read(Values);
    FCString::Strncpy(CubePath,*View->Scene->GetCurrentProfile()->EnvironmentCubeMap.ToString(),Capacity);return 1;
}
extern "C" __declspec(dllexport) int VFX_EnvironmentSet(const wchar_t* HostPath,const double* Values,const wchar_t* CubePath)
{
    if(!IsInGameThread())return 0;
    auto* Weak=Viewports.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;if(!View)return 0;
    if(!View->Scene->Apply(Values,CubePath))return 0;
    View->Invalidate();return 1;
}
// Read-only diagnostic for isolation regression tests; no profile/config writes.
extern "C" __declspec(dllexport) unsigned int VFX_GlobalEnvironmentHash()
{
    if(!IsInGameThread())return 0;
    FString Text;
    for(const auto& Profile:UAssetViewerSettings::Get()->Profiles)
        FPreviewSceneProfile::StaticStruct()->ExportText(Text,&Profile,nullptr,nullptr,PPF_None,nullptr);
    return FCrc::StrCrc32(*Text);
}
