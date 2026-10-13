#pragma once

#include "DetailWidgetRow.h"
#include "IDetailPropertyRow.h"
#include "PropertyCustomizationHelpers.h"
#include "PhysicalMaterials/PhysicalMaterial.h"
#include "RenderUtils.h"
#include "MaterialShared.h"
#include "Misc/MessageDialog.h"
#include "UObject/StrongObjectPtr.h"

// Use property handles throughout: UE owns the widgets, transactions and the
// editor proxy's propagation to the instance. Never save packages from here.
struct FVFXMaterialGeneral
{
    UMaterialEditorInstanceConstant* Editor;

    void OverrideRow(IDetailPropertyRow& Row,TSharedRef<IPropertyHandle> Flag,bool RequiresPermutation=false)
    {
        auto* Proxy=Editor;
        auto Enabled=TAttribute<bool>::CreateLambda([Flag]{bool Value=false;Flag->GetValue(Value);return Value;});
        Row.EditCondition(Enabled,FOnBooleanValueChanged::CreateLambda([Proxy,Flag,RequiresPermutation](bool Value)
        {
            if(Value && RequiresPermutation && !Proxy->SourceInstance->IsStaticPermutationAllowedForCandidateParent(Proxy->Parent))return;
            Flag->SetValue(Value);
        }));
        Row.Visibility(TAttribute<EVisibility>::CreateLambda([Proxy,Enabled]
        {return !Proxy->bShowOnlyOverrides || Enabled.Get()?EVisibility::Visible:EVisibility::Collapsed;}));
        if(RequiresPermutation && !Proxy->SourceInstance->IsStaticPermutationAllowedForCandidateParent(Proxy->Parent))
            Row.ToolTip(FText::FromString(TEXT("此材质实例禁止创建新的着色器排列，无法启用此覆盖。")));
    }

    void ResetRow(IDetailPropertyRow& Row,TFunction<bool()> Different,TFunction<void()> Reset)
    {
        Row.OverrideResetToDefault(FResetToDefaultOverride::Create(
            FIsResetToDefaultVisible::CreateLambda([Different](TSharedPtr<IPropertyHandle>){return Different();}),
            FResetToDefaultHandler::CreateLambda([Reset](TSharedPtr<IPropertyHandle> Handle)
            {
                FScopedTransaction Transaction(FText::FromString(TEXT("恢复父材质属性")));
                Handle->NotifyPreChange();Reset();
                Handle->NotifyPostChange(EPropertyChangeType::ValueSet);Handle->NotifyFinishedChangingProperties();
            })));
    }

    void Build(IDetailLayoutBuilder& Layout)
    {
        auto* Proxy=Editor;
        // Ask the target engine to resolve parent inheritance. Custom builds can
        // extend UMaterialInterface's vtable, so never call its virtual getters
        // through the SDK headers used to build this small adapter.
        auto* ParentInstance=NewObject<UMaterialInstanceConstant>(GetTransientPackage(),NAME_None,RF_Transient);
        ParentInstance->SetParentEditorOnly(Proxy->Parent);
        auto Inherited=MakeShared<TStrongObjectPtr<UMaterialEditorInstanceConstant>>(
            NewObject<UMaterialEditorInstanceConstant>(GetTransientPackage(),MakeUniqueObjectName(GetTransientPackage(),UMaterialEditorInstanceConstant::StaticClass(),TEXT("VFXInheritedValues")),RF_Transient));
        (*Inherited)->SetSourceInstance(ParentInstance);
        auto& General=Layout.EditCategory(TEXT("VFXGeneral"),FText::FromString(TEXT("通用")));
        General.SetSortOrder(100);
        auto Parent=Layout.GetProperty(TEXT("Parent"));
        auto& ParentRow=General.AddProperty(Parent);
        ParentRow.CustomWidget().NameContent()[Parent->CreatePropertyNameWidget()]
            .ValueContent().MinDesiredWidth(160).MaxDesiredWidth(350)
            [SNew(SObjectPropertyEntryBox).PropertyHandle(Parent).AllowedClass(UMaterialInterface::StaticClass())
                .ThumbnailPool(Layout.GetThumbnailPool()).AllowClear(true)
                .OnShouldSetAsset_Lambda([Proxy](const FAssetData& Asset)
                {
                    auto* Instance=Cast<UMaterialInstance>(Asset.GetAsset());
                    if(Instance && (Instance==Proxy->SourceInstance || Instance->IsChildOf(Proxy->SourceInstance)))
                    {
                        FMessageDialog::Open(EAppMsgType::Ok,FText::FromString(TEXT("不能将当前实例或其子实例设为父材质。")));
                        return false;
                    }
                    return true;
                })];

        auto Phys=Layout.GetProperty(TEXT("PhysMaterial"));
        auto& PhysRow=General.AddProperty(Phys);
        OverrideRow(PhysRow,Layout.GetProperty(TEXT("bOverridePhysMaterial")));
        ResetRow(PhysRow,[Proxy,Inherited]{return Proxy->Parent && Proxy->PhysMaterial!=(*Inherited)->PhysMaterial;},
            [Proxy,Inherited]{if(Proxy->Parent)Proxy->PhysMaterial=(*Inherited)->PhysMaterial;});

        auto Lightmass=Layout.GetProperty(TEXT("LightmassSettings"));
        auto& LightGroup=General.AddGroup(TEXT("VFXLightmass"),Lightmass->GetPropertyDisplayName(),false,false);
#define VFX_LIGHT(Name,Getter) \
        {auto Setting=Lightmass->GetChildHandle(TEXT(#Name)); \
        auto& Row=LightGroup.AddPropertyRow(Setting->GetChildHandle(TEXT("ParameterValue")).ToSharedRef()); \
        Row.DisplayName(Setting->GetPropertyDisplayName()).ToolTip(Setting->GetToolTipText()); \
        OverrideRow(Row,Setting->GetChildHandle(TEXT("bOverride")).ToSharedRef()); \
        ResetRow(Row,[Proxy,Inherited]{return Proxy->Parent && Proxy->LightmassSettings.Name.ParameterValue!=(*Inherited)->LightmassSettings.Name.ParameterValue;}, \
            [Proxy,Inherited]{if(Proxy->Parent)Proxy->LightmassSettings.Name.ParameterValue=(*Inherited)->LightmassSettings.Name.ParameterValue;});}
        VFX_LIGHT(CastShadowAsMasked,GetCastShadowAsMasked)
        VFX_LIGHT(EmissiveBoost,GetEmissiveBoost)
        VFX_LIGHT(DiffuseBoost,GetDiffuseBoost)
        VFX_LIGHT(ExportResolutionScale,GetExportResolutionScale)
#undef VFX_LIGHT
        auto& Refraction=General.AddProperty(TEXT("RefractionDepthBias"));
        ResetRow(Refraction,[Proxy,Inherited]{return Proxy->Parent && (*Inherited)->RefractionDepthBias!=Proxy->RefractionDepthBias;},
            [Proxy,Inherited]{if(Proxy->Parent)Proxy->RefractionDepthBias=(*Inherited)->RefractionDepthBias;});

        auto& Overrides=General.AddGroup(TEXT("VFXBaseOverrides"),FText::FromString(TEXT("材质属性重载")),false,true);
        auto Base=Layout.GetProperty(TEXT("BasePropertyOverrides"));
#define VFX_BASE(Name,Flag,Getter,Permutation) \
        {auto Value=Base->GetChildHandle(TEXT(#Name)); \
        if(Value.IsValid() && Value->IsValidHandle()){auto& Row=Overrides.AddPropertyRow(Value.ToSharedRef()); \
        OverrideRow(Row,Base->GetChildHandle(TEXT(#Flag)).ToSharedRef(),Permutation); \
        ResetRow(Row,[Proxy,Inherited]{return Proxy->Parent && Proxy->BasePropertyOverrides.Name!=(*Inherited)->BasePropertyOverrides.Name;}, \
            [Proxy,Inherited]{if(Proxy->Parent)Proxy->BasePropertyOverrides.Name=(*Inherited)->BasePropertyOverrides.Name;});}}
        VFX_BASE(OpacityMaskClipValue,bOverride_OpacityMaskClipValue,GetOpacityMaskClipValue,true)
        VFX_BASE(BlendMode,bOverride_BlendMode,GetBlendMode,true)
        // Optional extension owned by the custom engine, including its enum restrictions.
        auto JX3=Layout.GetProperty(TEXT("JX3BlendMode"));
        if(JX3->IsValidHandle())
        {
            auto& Row=Overrides.AddPropertyRow(JX3);
            OverrideRow(Row,Layout.GetProperty(TEXT("bOverride_JX3BlendMode")),true);
            FProperty* Property=JX3->GetProperty();
            ResetRow(Row,[Proxy,Inherited,Property]{return Proxy->Parent && !Property->Identical_InContainer(Proxy,Inherited->Get());},
                [Proxy,Inherited,Property]{if(Proxy->Parent)Property->CopyCompleteValue_InContainer(Proxy,Inherited->Get());});
        }
        if(!Substrate::IsSubstrateEnabled() || (Proxy->Parent && Proxy->Parent->GetBaseMaterial()->SupportsShadingModelOverride()))
        {
            auto& Row=Overrides.AddPropertyRow(Base->GetChildHandle(TEXT("ShadingModel")).ToSharedRef());
            OverrideRow(Row,Base->GetChildHandle(TEXT("bOverride_ShadingModel")).ToSharedRef(),true);
            auto ParentShading=[Inherited]{return (*Inherited)->BasePropertyOverrides.ShadingModel;};
            ResetRow(Row,[Proxy,ParentShading]{return Proxy->Parent && Proxy->BasePropertyOverrides.ShadingModel!=ParentShading();},
                [Proxy,ParentShading]{if(Proxy->Parent)Proxy->BasePropertyOverrides.ShadingModel=ParentShading();});
        }
        VFX_BASE(TwoSided,bOverride_TwoSided,IsTwoSided,true)
        if(Substrate::IsSubstrateEnabled()){VFX_BASE(bIsThinSurface,bOverride_bIsThinSurface,IsThinSurface,true)}
        VFX_BASE(DitheredLODTransition,bOverride_DitheredLODTransition,IsDitheredLODTransition,true)
        VFX_BASE(bOutputTranslucentVelocity,bOverride_OutputTranslucentVelocity,IsTranslucencyWritingVelocity,true)
        VFX_BASE(bHasPixelAnimation,bOverride_bHasPixelAnimation,HasPixelAnimation,true)
        VFX_BASE(bEnableTessellation,bOverride_bEnableTessellation,IsTessellationEnabled,true)
        VFX_BASE(DisplacementScaling,bOverride_DisplacementScaling,GetDisplacementScaling,false)
        VFX_BASE(bEnableDisplacementFade,bOverride_bEnableDisplacementFade,IsDisplacementFadeEnabled,false)
        VFX_BASE(DisplacementFadeRange,bOverride_DisplacementFadeRange,GetDisplacementFadeRange,false)
        VFX_BASE(MaxWorldPositionOffsetDisplacement,bOverride_MaxWorldPositionOffsetDisplacement,GetMaxWorldPositionOffsetDisplacement,false)
        VFX_BASE(bCastDynamicShadowAsMasked,bOverride_CastDynamicShadowAsMasked,GetCastDynamicShadowAsMasked,true)
        VFX_BASE(bCompatibleWithLumenCardSharing,bOverride_CompatibleWithLumenCardSharing,IsCompatibleWithLumenCardSharing,false)
#undef VFX_BASE
        if(Proxy->SubsurfaceProfile || Proxy->BasePropertyOverrides.ShadingModel==MSM_SubsurfaceProfile)
            OverrideRow(Overrides.AddPropertyRow(Layout.GetProperty(TEXT("SubsurfaceProfile"))),Layout.GetProperty(TEXT("bOverrideSubsurfaceProfile")));
        if(Substrate::IsSubstrateEnabled())
        {
            if(Proxy->SpecularProfile)
                OverrideRow(Overrides.AddPropertyRow(Layout.GetProperty(TEXT("SpecularProfile"))),Layout.GetProperty(TEXT("bOverrideSpecularProfile")));
            if(Proxy->ToonProfile)
                OverrideRow(Overrides.AddPropertyRow(Layout.GetProperty(TEXT("ToonProfile"))),Layout.GetProperty(TEXT("bOverrideToonProfile")));
        }
        OverrideRow(Overrides.AddPropertyRow(Layout.GetProperty(TEXT("NaniteOverrideMaterial"))),Layout.GetProperty(TEXT("bNaniteOverride")));
    }
};
