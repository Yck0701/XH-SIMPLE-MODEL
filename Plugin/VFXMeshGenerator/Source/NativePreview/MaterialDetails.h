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
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Text/STextBlock.h"
#include "Widgets/Layout/SBorder.h"
#include "Misc/PackageName.h"
#include "MaterialGeneral.h"

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
        FVFXMaterialGeneral{Editor}.Build(Layout);
        auto Groups=Layout.GetProperty(TEXT("ParameterGroups"));Layout.HideProperty(Groups);
        auto& Category=Layout.EditCategory(TEXT("VFXParameters"),FText::FromString(TEXT("参数组")));
        Category.SetSortOrder(0);
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
        // Parent changes regenerate parameter objects; refresh rows after the
        // edit has finished. Base changes also affect inherited values/visibility.
        if(Property && !(Event.ChangeType&EPropertyChangeType::Interactive) &&
            (Property->GetOwnerStruct()->GetName().Contains(TEXT("Static")) ||
             Property->GetOwnerStruct()==UMaterialEditorInstanceConstant::StaticClass() ||
             (Event.MemberProperty && Event.MemberProperty->GetOwnerStruct()==UMaterialEditorInstanceConstant::StaticClass())))
            RefreshPending=true;
    }
    void ObjectChanged(UObject* Object,FPropertyChangedEvent&)
    {
        if(!Editor || Applying)return;
        for(UMaterialInterface* Material=Editor->SourceInstance;Material;)
        {
            if(Material==Object){Revision++;RefreshPending=true;return;}
            auto* Instance=Cast<UMaterialInstance>(Material);Material=Instance?Instance->Parent.Get():nullptr;
        }
    }
    virtual void PostUndo(bool Success) override {if(Success){Revision++;RefreshPending=true;}}
    virtual void PostRedo(bool Success) override {if(Success){Revision++;RefreshPending=true;}}
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
struct FVFXMaterialSaveState
{
    FString Folder,Name,Path,Error;
    int Result=0;
    void Validate(const FString& Input)
    {
        Name=Input.TrimStartAndEnd();Path=Folder+TEXT("/")+Name;Error.Reset();
        if(Name.IsEmpty())Error=TEXT("请输入新实例名称。");
        else if(Name.Len()>128)Error=TEXT("名称不能超过 128 个字符。");
        else if(Name[0]>=TEXT('0') && Name[0]<=TEXT('9'))Error=TEXT("名称不能以数字开头。");
        else for(TCHAR C:Name)
            if(!(C==TEXT('_') || (C>=TEXT('A') && C<=TEXT('Z')) || (C>=TEXT('a') && C<=TEXT('z')) ||
                (C>=TEXT('0') && C<=TEXT('9')) || (C>=0x4e00 && C<=0x9fff)))
                {Error=TEXT("名称请使用中文、字母、数字或下划线。");break;}
        if(Error.IsEmpty() && (FindPackage(nullptr,*Path) || FPackageName::DoesPackageExist(Path)))
            Error=TEXT("该名称已存在，请修改名称；不会覆盖已有资产。");
    }
};
extern "C" __declspec(dllexport) int VFX_MaterialSaveDialog(const wchar_t* HostPath,const wchar_t* SourcePath,const wchar_t* NewPath,int CanReplace,int Live,wchar_t* OutPath,int Capacity)
{
    if(!IsInGameThread() || !OutPath || Capacity<2)return -1;OutPath[0]=0;
    auto* Weak=MaterialViews.Find(HostPath);auto View=Weak?Weak->Pin():nullptr;if(!View)return -1;
    auto State=MakeShared<FVFXMaterialSaveState>();
    FString DefaultName;FString(NewPath).Split(TEXT("/"),&State->Folder,&DefaultName,ESearchCase::CaseSensitive,ESearchDir::FromEnd);
    State->Validate(DefaultName);
    auto Dialog=SNew(SWindow).Title(FText::FromString(TEXT("确认保存材质")))
        .SizingRule(ESizingRule::Autosized).SupportsMinimize(false).SupportsMaximize(false);
    TWeakPtr<SWindow> WeakDialog=Dialog;
    auto Choose=[State,WeakDialog](int Choice)
    {
        if(Choice==2){State->Validate(State->Name);if(!State->Error.IsEmpty())return FReply::Handled();}
        State->Result=Choice;if(auto Window=WeakDialog.Pin())Window->RequestDestroyWindow();return FReply::Handled();
    };
    Dialog->SetContent(SNew(SBorder).Padding(20)
        [SNew(SVerticalBox)
        +SVerticalBox::Slot().AutoHeight().Padding(0,0,0,14)
            [SNew(STextBlock).Text(FText::FromString(TEXT("选择本次材质调整的保存方式")))]
        +SVerticalBox::Slot().AutoHeight().Padding(0,0,0,8)
            [SNew(STextBlock).Text(FText::FromString(FString(TEXT("原材质：\n"))+SourcePath)).AutoWrapText(true).WrapTextAt(560)]
        +SVerticalBox::Slot().AutoHeight().Padding(0,0,0,18)
            [SNew(SButton).IsEnabled(CanReplace!=0).Text(FText::FromString(TEXT("替换原材质并保存"))).OnClicked_Lambda([Choose](){return Choose(1);})]
        +SVerticalBox::Slot().AutoHeight().Padding(0,0,0,8)
            [SNew(STextBlock).Text(FText::FromString(TEXT("新实例名称")))]
        +SVerticalBox::Slot().AutoHeight().Padding(0,0,0,8)
            [SNew(SEditableTextBox).Text(FText::FromString(DefaultName)).MinDesiredWidth(560)
                .OnTextChanged_Lambda([State](const FText& Text){State->Validate(Text.ToString());})]
        +SVerticalBox::Slot().AutoHeight().Padding(0,0,0,8)
            [SNew(STextBlock).Text_Lambda([State](){return FText::FromString(State->Path);}).AutoWrapText(true).WrapTextAt(560)]
        +SVerticalBox::Slot().AutoHeight().Padding(0,0,0,8)
            [SNew(STextBlock).Text_Lambda([State](){return FText::FromString(State->Error);})
                .ColorAndOpacity(FLinearColor(1,.35f,.2f)).AutoWrapText(true).WrapTextAt(560)]
        +SVerticalBox::Slot().AutoHeight().Padding(0,0,0,18)
            [SNew(SButton).IsEnabled_Lambda([State](){return State->Error.IsEmpty();})
                .Text(FText::FromString(TEXT("新增实例并保存"))).OnClicked_Lambda([Choose](){return Choose(2);})]
        +SVerticalBox::Slot().AutoHeight().Padding(0,0,0,14)
            [SNew(STextBlock).Text(FText::FromString(Live?
                TEXT("仅保存所选材质实例。实时迭代已应用的原材质修改不会因另存而撤销。模型、母材质和关卡不会一同保存。"):
                TEXT("仅保存所选材质实例。替换保留原引用；新增会切换工作台材质。模型、母材质和关卡不会一同保存。"))).AutoWrapText(true).WrapTextAt(560)]
        +SVerticalBox::Slot().AutoHeight()
            [SNew(SButton).Text(FText::FromString(TEXT("取消"))).OnClicked_Lambda([Choose](){return Choose(0);})]
        ]);
    FSlateApplication::Get().AddModalWindow(Dialog,FSlateApplication::Get().FindWidgetWindow(View.ToSharedRef()));
    if(State->Result==2)
    {
        if(State->Path.Len()>=Capacity)return -1;
        FCString::Strncpy(OutPath,*State->Path,Capacity);
    }
    return State->Result;
}
// Duplicate through the running engine so custom material fields and static
// permutations stay intact. A draft never belongs to a saved asset package.
extern "C" __declspec(dllexport) const wchar_t* VFX_MaterialDraft(const wchar_t* SourcePath)
{
    static FString Result;Result.Reset();
    if(!IsInGameThread())return *Result;
    auto* Source=FindObject<UMaterialInterface>(nullptr,SourcePath);if(!Source)return *Result;
    UMaterialInstanceConstant* Draft=nullptr;
    if(auto* Instance=Cast<UMaterialInstanceConstant>(Source))
        Draft=DuplicateObject<UMaterialInstanceConstant>(Instance,GetTransientPackage(),MakeUniqueObjectName(GetTransientPackage(),Instance->GetClass(),TEXT("VFXMaterialDraft")));
    else
    {
        Draft=NewObject<UMaterialInstanceConstant>(GetTransientPackage(),NAME_None,RF_Transient|RF_Transactional);
        Draft->SetParentEditorOnly(Source);
    }
    Draft->ClearFlags(RF_Public|RF_Standalone);Draft->SetFlags(RF_Transient|RF_Transactional);
    Result=Draft->GetPathName();return *Result;
}
extern "C" __declspec(dllexport) int VFX_MaterialCopy(const wchar_t* SourcePath,const wchar_t* TargetPath)
{
    if(!IsInGameThread())return 0;
    auto* Source=FindObject<UMaterialInstanceConstant>(nullptr,SourcePath);
    auto* Target=FindObject<UMaterialInstanceConstant>(nullptr,TargetPath);
    if(!Source || !Target || Source==Target || !Target->GetPathName().StartsWith(TEXT("/Game/")))return 0;
    if(Source->Parent==Target)return 0;
    if(auto* Parent=Cast<UMaterialInstance>(Source->Parent.Get());Parent && Parent->IsChildOf(Target))return 0;
    // The target keeps its object identity, package and references. Do not
    // broadcast object replacement or remap any unrelated editor objects.
    FScopedTransaction Transaction(FText::FromString(TEXT("应用材质预览参数")));
    Target->Modify();Target->PreEditChange(nullptr);
    UEngine::FCopyPropertiesForUnrelatedObjectsParams Params;
    Params.bNotifyObjectReplacement=false;Params.bClearReferences=false;Params.bDoDelta=false;
    UEngine::CopyPropertiesForUnrelatedObjects(Source,Target,Params);
    Target->PostEditChange();Target->MarkPackageDirty();return 1;
}
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
    return View?View->Revision:-1;
}
