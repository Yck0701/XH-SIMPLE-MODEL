#pragma once

// Target UnrealEd headers can expose an engine-specific opaque enum. Its fixed
// storage is sufficient here; values remain owned by that engine's editor.
enum class EJX3MaterialBlendMode : uint8;

#include "PropertyEditorModule.h"
#include "IDetailsView.h"
#include "IDetailCustomization.h"
#include "DetailLayoutBuilder.h"
#include "DetailCategoryBuilder.h"
#include "IDetailGroup.h"
#include "PropertyHandle.h"
#include "MaterialPropertyHelpers.h"
#include "MaterialEditor/DEditorParameterValue.h"
#include "MaterialEditor/MaterialEditorInstanceConstant.h"
#include "Materials/MaterialInstanceConstant.h"
#include "UObject/GCObject.h"
#include "UObject/UObjectIterator.h"
#include "Misc/NotifyHook.h"
#include "Editor.h"
#include "EditorUndoClient.h"
#include "ScopedTransaction.h"
#include "Widgets/SCompoundWidget.h"

// Keep engine-owned parameter types and row widgets. Only the surrounding
// grouping is ours; values, enums, ranges, channels and reset use UE's helpers.
class FVFXMaterialLayout : public IDetailCustomization
{
public:
    UMaterialEditorInstanceConstant* Editor;
    TFunction<void(bool,UDEditorParameterValue*)> Override;
    bool* ShowHidden;
    FVFXMaterialLayout(UMaterialEditorInstanceConstant* InEditor,
        TFunction<void(bool,UDEditorParameterValue*)> InOverride,bool* InShowHidden)
        :Editor(InEditor),Override(MoveTemp(InOverride)),ShowHidden(InShowHidden){}
    virtual void CustomizeDetails(IDetailLayoutBuilder& Layout) override
    {
        TArray<FName> Categories;Layout.GetCategoryNames(Categories);
        for(FName Name:Categories)Layout.HideCategory(Name);
        auto Groups=Layout.GetProperty(TEXT("ParameterGroups"));Layout.HideProperty(Groups);
        auto& Category=Layout.EditCategory(TEXT("VFXParameters"),FText::FromString(TEXT("参数组")));
        TMap<FString,IDetailGroup*> Branches;
        for(int32 G=0;G<Editor->ParameterGroups.Num();G++)
        {
            const auto& Group=Editor->ParameterGroups[G];
            auto GroupHandle=Groups->GetChildHandle(G);
            auto Parameters=GroupHandle.IsValid()?GroupHandle->GetChildHandle(TEXT("Parameters")):nullptr;
            if(!Parameters.IsValid())continue;
            for(int32 P=0;P<Group.Parameters.Num();P++)
            {
                auto* Parameter=Group.Parameters[P].Get();
                if(!Parameter || !FMaterialPropertyHelpers::ShouldCreatePropertyRowForParameter(Parameter))continue;
                auto ParameterHandle=Parameters->GetChildHandle(P);
                auto Value=ParameterHandle.IsValid()?ParameterHandle->GetChildHandle(TEXT("ParameterValue")):nullptr;
                if(!Value.IsValid() || !Value->IsValidHandle())continue;
                FString GroupPath=Group.GroupName.ToString();
                if(Parameter->ParameterInfo.Association!=GlobalParameter)
                    GroupPath=FString::Printf(TEXT("%s %d|%s"),Parameter->ParameterInfo.Association==LayerParameter?TEXT("图层"):TEXT("混合"),Parameter->ParameterInfo.Index, *GroupPath);
                TArray<FString> Parts;GroupPath.ParseIntoArray(Parts,TEXT("|"),true);
                IDetailGroup* Branch=nullptr;FString Key;
                for(const FString& Part:Parts)
                {
                    Key+=TEXT("|")+Part;
                    auto** Existing=Branches.Find(Key);
                    if(Existing)Branch=*Existing;
                    else
                    {
                        auto& NewGroup=Branch?Branch->AddGroup(FName(*Key),FText::FromString(Part),true):Category.AddGroup(FName(*Key),FText::FromString(Part),false,true);
                        Branch=&NewGroup;Branches.Add(Key,Branch);
                    }
                }
                if(!Branch)continue;
                FMaterialParameterPropertyRowOverrides Options;
                Options.ShowHiddenDelegate=FGetShowHiddenParameters::CreateLambda([Flag=ShowHidden](bool& Value){Value=*Flag;});
                Options.OnEditConditionValueChanged=FOnBooleanValueChanged::CreateLambda([Callback=Override,Parameter](bool Enabled){Callback(Enabled,Parameter);});
                auto& Row=Branch->AddPropertyRow(Value.ToSharedRef());
                FMaterialPropertyHelpers::ConfigurePropertyRowForParameter(Row,Parameter,Value,Editor,Options);
                FMaterialPropertyHelpers::SetPropertyRowParameterWidget(Row,Parameter,Value,Editor);
            }
        }
    }
};

class SVFXMaterialDetails : public SCompoundWidget,public FGCObject,public FNotifyHook,public FEditorUndoClient
{
public:
    SLATE_BEGIN_ARGS(SVFXMaterialDetails){}SLATE_END_ARGS()
    UMaterialEditorInstanceConstant* Editor=nullptr;
    TSharedPtr<IDetailsView> Details;
    bool Applying=false,RefreshPending=false,ShowHidden=false,SyncPeers=false;
    int Revision=0;
    FDelegateHandle ChangedHandle;
    void Construct(const FArguments&)
    {
        auto& Module=FModuleManager::LoadModuleChecked<FPropertyEditorModule>(TEXT("PropertyEditor"));
        FDetailsViewArgs Args;
        Args.NameAreaSettings=FDetailsViewArgs::HideNameArea;Args.bHideSelectionTip=true;
        Args.bAllowSearch=true;Args.NotifyHook=this;Args.bShowModifiedPropertiesOption=false;
        Args.bShowCustomFilterOption=true;Args.ViewIdentifier=TEXT("VFXMesh.MaterialDetails");
        Details=Module.CreateDetailView(Args);
        Details->SetCustomFilterLabel(FText::FromString(TEXT("仅显示已覆盖参数")));
        Details->SetCustomFilterDelegate(FSimpleDelegate::CreateSP(this,&SVFXMaterialDetails::ToggleOverrides));
        ChildSlot[Details.ToSharedRef()];
        ChangedHandle=FCoreUObjectDelegates::OnObjectPropertyChanged.AddSP(this,&SVFXMaterialDetails::ObjectChanged);
        GEditor->RegisterForUndo(this);
    }
    virtual ~SVFXMaterialDetails()
    {
        FCoreUObjectDelegates::OnObjectPropertyChanged.Remove(ChangedHandle);
        if(GEditor)GEditor->UnregisterForUndo(this);
        if(Details)Details->SetObject(nullptr);
    }
    virtual void AddReferencedObjects(FReferenceCollector& Collector) override {Collector.AddReferencedObject(Editor);}
    virtual FString GetReferencerName() const override {return TEXT("VFXMaterialDetails");}
    void SetMaterial(UMaterialInstanceConstant* Material)
    {
        Details->SetObject(nullptr);Editor=nullptr;RefreshPending=false;
        if(!Material)return;
        TGuardValue<bool> Guard(Applying,true);
        Editor=NewObject<UMaterialEditorInstanceConstant>(GetTransientPackage(),NAME_None,RF_Transactional|RF_Transient);
        Editor->SetSourceInstance(Material);Editor->DetailsView=Details;
        TWeakPtr<SVFXMaterialDetails> Weak=SharedThis(this);
        Details->RegisterInstancedCustomPropertyLayout(UMaterialEditorInstanceConstant::StaticClass(),
            FOnGetDetailCustomizationInstance::CreateLambda([Weak]() -> TSharedRef<IDetailCustomization>
            {
                auto Self=Weak.Pin();
                return MakeShared<FVFXMaterialLayout>(Self->Editor,[Weak](bool Enabled,UDEditorParameterValue* Parameter)
                    {if(auto Owner=Weak.Pin())Owner->OverrideParameter(Enabled,Parameter);},&Self->ShowHidden);
            }));
        Details->SetObject(Editor,true);
    }
    void ToggleOverrides(){if(Editor){Editor->bShowOnlyOverrides=!Editor->bShowOnlyOverrides;Details->ForceRefresh();}}
    void SetShowHidden(bool Enabled){ShowHidden=Enabled;Details->ForceRefresh();}
    void Refresh()
    {
        if(!Editor)return;
        TGuardValue<bool> Guard(Applying,true);
        Editor->SetSourceInstance(Editor->SourceInstance);Details->ForceRefresh();RefreshPending=false;
    }
    void OverrideParameter(bool Enabled,UDEditorParameterValue* Parameter)
    {
        TGuardValue<bool> Guard(Applying,true);
        FScopedTransaction Transaction(FText::FromString(TEXT("材质参数覆盖")));
        Editor->SourceInstance->Modify();
        // The engine helper owns the transaction, checkbox and reset semantics.
        FMaterialPropertyHelpers::OnOverrideParameter(Enabled,Parameter,Editor);
        Revision++;SyncPeers=true;RefreshPending=true;
    }
    virtual void NotifyPreChange(FProperty*) override
    {
        Applying=true;
        if(Editor && Editor->SourceInstance)Editor->SourceInstance->Modify();
    }
    virtual void NotifyPostChange(const FPropertyChangedEvent& Event,FProperty* Property) override
    {
        Applying=false;Revision++;
        if(!(Event.ChangeType&EPropertyChangeType::Interactive))SyncPeers=true;
        if(Property && Property->GetOwnerStruct()->GetName().Contains(TEXT("Static")))RefreshPending=true;
    }
    void ObjectChanged(UObject* Object,FPropertyChangedEvent&)
    {
        if(!Editor || Applying)return;
        for(UMaterialInterface* Material=Editor->SourceInstance;Material;)
        {
            if(Material==Object){RefreshPending=true;return;}
            auto* Instance=Cast<UMaterialInstance>(Material);Material=Instance?Instance->Parent.Get():nullptr;
        }
    }
    virtual void PostUndo(bool Success) override {if(Success)RefreshPending=true;}
    virtual void PostRedo(bool Success) override {if(Success)RefreshPending=true;}
    virtual void Tick(const FGeometry& Geometry,double Time,float Delta) override
    {
        SCompoundWidget::Tick(Geometry,Time,Delta);
        if(SyncPeers && Editor)
        {
            SyncPeers=false;
            // Synchronize open native editors before they can reapply stale values.
            for(TObjectIterator<UMaterialEditorInstanceConstant> It;It;++It)
                if(*It!=Editor && It->SourceInstance==Editor->SourceInstance && It->DetailsView.IsValid())
                {It->SetSourceInstance(Editor->SourceInstance);It->DetailsView.Pin()->ForceRefresh();}
        }
        if(RefreshPending && !Applying)Refresh();
    }
};

static TMap<FString,TWeakPtr<SVFXMaterialDetails>> MaterialViews;
extern "C" __declspec(dllexport) int VFX_MaterialDetails(const wchar_t* HostPath,const wchar_t* MaterialPath)
{
    if(!IsInGameThread())return 0;
    auto* Host=FindObject<UNativeWidgetHost>(nullptr,HostPath);if(!Host)return 0;
    auto* Existing=MaterialViews.Find(HostPath);auto View=Existing?Existing->Pin():nullptr;
    if(!View){View=SNew(SVFXMaterialDetails);Host->SetContent(View.ToSharedRef());MaterialViews.Add(HostPath,View);}
    auto* Material=FindObject<UMaterialInstanceConstant>(nullptr,MaterialPath);
    View->SetMaterial(Material);return 1;
}
extern "C" __declspec(dllexport) int VFX_MaterialRefresh(const wchar_t* HostPath,int ShowHidden)
{
    if(!IsInGameThread())return 0;
    auto* Existing=MaterialViews.Find(HostPath);auto View=Existing?Existing->Pin():nullptr;if(!View)return 0;
    View->SetShowHidden(ShowHidden!=0);View->Refresh();return 1;
}
extern "C" __declspec(dllexport) int VFX_MaterialRevision(const wchar_t* HostPath)
{
    if(!IsInGameThread())return 0;
    auto* Existing=MaterialViews.Find(HostPath);auto View=Existing?Existing->Pin():nullptr;
    return View?View->Revision:0;
}
