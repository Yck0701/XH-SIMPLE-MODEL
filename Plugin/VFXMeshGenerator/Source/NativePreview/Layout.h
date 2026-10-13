#pragma once
#include "Widgets/Layout/SSplitter.h"

// The three UMG roots are hosted directly in Slate, so retain their UObject
// trees explicitly rather than depending on Python wrapper lifetimes.
class SVFXLayout : public SSplitter,public FGCObject
{
public:
    UWidget* Roots[3]{};
    void Construct(const FArguments& Args,UWidget* Left,UWidget* Center,UWidget* Right)
    {Roots[0]=Left;Roots[1]=Center;Roots[2]=Right;SSplitter::Construct(Args);}
    virtual void AddReferencedObjects(FReferenceCollector& Collector) override
    {for(auto*& Root:Roots)Collector.AddReferencedObject(Root);}
    virtual FString GetReferencerName() const override{return TEXT("VFXWorkspaceLayout");}
};

static TMap<FString,TWeakPtr<SSplitter>> Layouts;
extern "C" __declspec(dllexport) int VFX_Layout(const wchar_t* HostPath,const wchar_t* LeftPath,const wchar_t* CenterPath,const wchar_t* RightPath,const double* Sizes)
{
    if(!IsInGameThread() || !Sizes)return 0;
    auto* Host=FindObject<UNativeWidgetHost>(nullptr,HostPath);
    auto* Left=FindObject<UWidget>(nullptr,LeftPath);auto* Center=FindObject<UWidget>(nullptr,CenterPath);auto* Right=FindObject<UWidget>(nullptr,RightPath);
    if(!Host || !Left || !Center || !Right)return 0;
    for(auto It=Layouts.CreateIterator();It;++It)if(!It.Value().IsValid())It.RemoveCurrent();
    auto Splitter=SNew(SVFXLayout,Left,Center,Right).Orientation(Orient_Horizontal).PhysicalSplitterHandleSize(6).HitDetectionSplitterHandleSize(10)
        +SSplitter::Slot().Value(float(Sizes[0])).MinSize(300)[Left->TakeWidget()]
        +SSplitter::Slot().Value(float(Sizes[1])).MinSize(360)[Center->TakeWidget()]
        +SSplitter::Slot().Value(float(Sizes[2])).MinSize(320)[Right->TakeWidget()];
    Host->SetContent(Splitter);Layouts.Add(HostPath,Splitter);return 1;
}
extern "C" __declspec(dllexport) int VFX_LayoutSizes(const wchar_t* HostPath,double* Sizes,int Set)
{
    if(!IsInGameThread() || !Sizes)return 0;
    auto* Weak=Layouts.Find(HostPath);auto Splitter=Weak?Weak->Pin():nullptr;if(!Splitter)return 0;
    for(int i=0;i<3;i++)
    {
        if(Set && (!FMath::IsFinite(Sizes[i]) || Sizes[i]<=0))return 0;
    }
    for(int i=0;i<3;i++)
    {
        if(Set)Splitter->SlotAt(i).SetSizeValue(float(Sizes[i]));
        else Sizes[i]=Splitter->SlotAt(i).GetSizeValue();
    }
    return 1;
}
