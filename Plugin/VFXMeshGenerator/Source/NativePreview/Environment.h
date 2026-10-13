#pragma once

#include "Components/DirectionalLightComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/PostProcessComponent.h"
#include "UObject/StrongObjectPtr.h"

// FAdvancedPreviewScene normally edits the shared asset-viewer profiles. Give
// this workspace its own transient settings, including for K/L mouse gestures.
class FVFXPreviewScene : public FAdvancedPreviewScene
{
public:
    TStrongObjectPtr<UAssetViewerSettings> LocalSettings;
    FPreviewSceneProfile InitialProfile;
    float MeshBottom=0.f,FloorHeight=0.f;
    FVFXPreviewScene():FAdvancedPreviewScene(FPreviewScene::ConstructionValues())
    {
        InitialProfile=*GetCurrentProfile();
        DefaultSettings->OnAssetViewerSettingsChanged().Remove(RefreshDelegate);
        RefreshDelegate.Reset();
        LocalSettings.Reset(NewObject<UAssetViewerSettings>(GetTransientPackage(),NAME_None,RF_Transient));
        LocalSettings->Profiles={InitialProfile};
        DefaultSettings=LocalSettings.Get();CurrentProfileIndex=0;
        GetCurrentProfile()->bRotateLightingRig=false;
        bRotateLighting=false;
    }
    void PlaceFloor(){SetFloorOffset(-MeshBottom-FloorHeight);}
    void Read(double* V) const
    {
        const auto& P=*GetCurrentProfile();const auto R=DirectionalLight->GetComponentRotation();
        V[0]=P.SkyLightIntensity;V[1]=P.DirectionalLightIntensity;V[2]=P.LightingRigRotation;
        V[3]=R.Pitch;V[4]=R.Yaw;V[5]=P.DirectionalLightColor.R;V[6]=P.DirectionalLightColor.G;V[7]=P.DirectionalLightColor.B;
        V[8]=P.PostProcessingSettings.AutoExposureBias;V[9]=P.PostProcessingSettings.BloomIntensity;
        V[10]=P.PostProcessingSettings.AutoExposureMethod!=AEM_Manual;
        V[11]=P.EnvironmentColor.R;V[12]=P.EnvironmentColor.G;V[13]=P.EnvironmentColor.B;
        V[14]=P.EnvironmentIntensity;V[15]=P.bShowEnvironment;V[16]=P.bShowFloor;
        V[17]=P.bPostProcessingEnabled;V[18]=FloorHeight;V[19]=P.bUseSkyLighting;
    }
    bool Apply(const double* V,const wchar_t* CubePath)
    {
        if(!V || !CubePath)return false;
        for(int I=0;I<20;I++)if(!FMath::IsFinite(V[I]))return false;
        UTextureCube* Cube=nullptr;
        if(*CubePath)
        {
            Cube=LoadObject<UTextureCube>(nullptr,CubePath);
            if(!Cube)return false;
        }
        auto& P=*GetCurrentProfile();
        const float Sky=FMath::Clamp(float(V[0]),0.f,20.f);
        const float Rotation=FMath::Clamp(float(V[2]),0.f,360.f);
        const bool ChangeSky=Sky!=P.SkyLightIntensity;
        const bool ChangeEnvironment=Cube!=P.EnvironmentCubeMap.Get() || Rotation!=P.LightingRigRotation;
        P.SkyLightIntensity=Sky;P.DirectionalLightIntensity=FMath::Clamp(float(V[1]),0.f,20.f);
        P.LightingRigRotation=Rotation;P.EnvironmentCubeMap=Cube;
        P.EnvironmentCubeMapPath=Cube?Cube->GetPathName():FString();
        P.DirectionalLightRotation=FRotator(FMath::Clamp(V[3],-90.,90.),FMath::Clamp(V[4],-180.,180.),0);
        P.DirectionalLightColor=FLinearColor(FMath::Clamp(V[5],0.,100.),FMath::Clamp(V[6],0.,100.),FMath::Clamp(V[7],0.,100.));
        SetLightDirection(P.DirectionalLightRotation);
        P.PostProcessingSettings.bOverride_AutoExposureBias=true;
        P.PostProcessingSettings.AutoExposureBias=FMath::Clamp(float(V[8]),-10.f,10.f);
        P.PostProcessingSettings.bOverride_BloomIntensity=true;
        P.PostProcessingSettings.BloomIntensity=FMath::Clamp(float(V[9]),0.f,10.f);
        P.PostProcessingSettings.bOverride_AutoExposureMethod=true;
        P.PostProcessingSettings.AutoExposureMethod=V[10]!=0?AEM_Histogram:AEM_Manual;
        P.PostProcessingSettings.bOverride_AutoExposureApplyPhysicalCameraExposure=true;
        P.PostProcessingSettings.AutoExposureApplyPhysicalCameraExposure=false;
        P.EnvironmentColor=FLinearColor(FMath::Clamp(V[11],0.,1.),FMath::Clamp(V[12],0.,1.),FMath::Clamp(V[13],0.,1.));
        P.EnvironmentIntensity=FMath::Clamp(float(V[14]),0.f,20.f);
        P.bShowEnvironment=V[15]!=0;P.bShowFloor=V[16]!=0;P.bPostProcessingEnabled=V[17]!=0;
        FloorHeight=FMath::Clamp(float(V[18]),-10000.f,10000.f);P.bUseSkyLighting=V[19]!=0;
        UILightingRigRotationDelta=0;
        UpdateScene(P,ChangeSky,ChangeEnvironment,true,true);PlaceFloor();
        return true;
    }
};
