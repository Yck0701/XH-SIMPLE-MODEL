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
#include "SceneView.h"
#include "SceneManagement.h"
#include "Widgets/SNullWidget.h"
#include "InputCoreTypes.h"
#include "Framework/Application/SlateApplication.h"
#include "Widgets/SWindow.h"

class FVFXViewportClient : public FEditorViewportClient
{
public:
    TSharedPtr<FAdvancedPreviewScene> Scene;
    FBox MeshBox=FBox(FVector(-100),FVector(100));
    bool bAxes=true;
    bool bTurntable=false;
    bool bWireframe=false;
    TArray<TPair<FVector,FVector>> Edges;
    FVFXViewportClient(TSharedPtr<FAdvancedPreviewScene> InScene,TSharedRef<SEditorViewport> Widget)
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
        return Scene->HandleInputKey(Args)||FEditorViewportClient::InputKey(Args);
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
        if(bWireframe)
        {
            for(const auto& Edge:Edges)
                PDI->DrawLine(Edge.Key,Edge.Value,FLinearColor(.05f,.8f,1.f),SDPG_World,.8f,1.f,true);
        }
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
    TSharedPtr<FAdvancedPreviewScene> Scene;
    TSharedPtr<FVFXViewportClient> VFXClient;
    UDynamicMeshComponent* Mesh=nullptr;
    void Construct(const FArguments&)
    {
        Scene=MakeShared<FAdvancedPreviewScene>(FPreviewScene::ConstructionValues());
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
        Mesh->SetDynamicMesh(Dynamic);Mesh->SetMaterial(0,Material?Material:UMaterial::GetDefaultMaterial(MD_Surface));
        Mesh->UpdateBounds();Mesh->MarkRenderStateDirty();VFXClient->MeshBox=Mesh->Bounds.GetBox();
        VFXClient->Edges.Reset();
        Dynamic->ProcessMesh([this](const UE::Geometry::FDynamicMesh3& Data)
        {
            VFXClient->Edges.Reserve(Data.EdgeCount());
            for(int ID:Data.EdgeIndicesItr())
            {
                const auto Pair=Data.GetEdgeV(ID);
                VFXClient->Edges.Emplace(Data.GetVertex(Pair.A),Data.GetVertex(Pair.B));
            }
        });
        Scene->SetFloorOffset(float(-VFXClient->MeshBox.Min.Z));
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
            if(Size.X<1020 || Size.Y<650)Window->Resize(FVector2D(1200,800));
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
    View->Scene->SetFloorVisibility(Floor!=0,true);View->Scene->SetEnvironmentVisibility(Environment!=0,true);
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
extern "C" __declspec(dllexport) int VFX_Camera(const wchar_t* HostPath,double* Values)
{
    if(!IsInGameThread())return 0;
    auto* Weak=Viewports.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;if(!View)return 0;
    const auto P=View->VFXClient->GetViewLocation();const auto R=View->VFXClient->GetViewRotation();
    Values[0]=P.X;Values[1]=P.Y;Values[2]=P.Z;Values[3]=R.Pitch;Values[4]=R.Yaw;Values[5]=R.Roll;return 1;
}
