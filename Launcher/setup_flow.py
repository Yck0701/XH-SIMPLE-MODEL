"""Selection-scoped discovery and guided setup; background results cannot cross projects."""
from pathlib import Path
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, messagebox
import os
import webbrowser

import core
import engine_discovery as discovery
import native_builder


def inspect_selection(project,engine,base,state,item,origin,remembered='',known=()):
    info=core.project_info(project) if project.strip() else None
    found=None
    if not engine.strip():
        found=discovery.discover(info,remembered,known)
        engine=found['selected'] or ''
        if not engine:return {'info':info,'discovery':found,'engine':''}
    engine=str(core.validate_engine(engine))
    archive=base/'offline'/item['file'] if origin=='offline' else core.download(item,state/'Downloads')
    if core.digest(archive)!=item['sha256'] or archive.stat().st_size!=item['size']:
        raise ValueError('安装包校验失败，请重新完整解压启动器或重新下载。')
    cache_warning=''
    try:report=native_builder.diagnostic(engine,archive,item,state/'NativeBuilds')
    except ValueError as error:
        if '缓存' not in str(error) and 'receipt.json' not in str(error):raise
        cache_warning=str(error)
        report=core.diagnostic_report(engine,archive)
    preflight_error=''
    if not report['engine']['compatible']:
        try:native_builder.preflight(engine,base/'compiler')
        except Exception as error:preflight_error=str(error)
    return {'info':info,'engine':engine,'discovery':found,'archive':archive,'item':dict(item),'origin':origin,
            'report':report,'preflight_error':preflight_error,'cache_warning':cache_warning}


class SetupFlow:
    def init_setup(self,config):
        self.project_engines=config.get('project_engines',{})
        if not isinstance(self.project_engines,dict):self.project_engines={}
        self.known_engines=config.get('known_engines',[])
        if not isinstance(self.known_engines,list):self.known_engines=[]
        self.engine_source=tk.StringVar(value='按工程关联、附近目录和已安装引擎自动查找。')
        self.setup_title=tk.StringVar(value='等待选择工程与引擎')
        self.setup_detail=tk.StringVar(value='选好后自动检测；需要适配时会在这里给出下一步。')
        self.setup_steps=tk.StringVar(value='① 找到引擎   →   ② 检查适配   →   ③ 编译与验证（需要时）   →   ④ 确认安装')
        self.setup_elapsed=tk.StringVar(value='')
        self.setup_result=None;self.setup_candidates=[];self._selection_revision=0
        self._setup_after=None;self._setup_tick=None;self._scan_running=False;self._scan_again=False
        self._suppress_selection=False;self._project_key=discovery.path_key(self.project.get())
        self._cancel_setup=None;self._task_started=None

    def bind_setup(self):
        self.project.trace_add('write',lambda *_:self.selection_changed(project_changed=True))
        self.engine.trace_add('write',lambda *_:self.selection_changed())

    def build_setup_card(self,page):
        card=self.card(page)
        self.label(card,textvariable=self.setup_title,style='CardHeading.TLabel')
        self.setup_detail_label=self.label(card,textvariable=self.setup_detail,style='CardMuted.TLabel',wraplength=690,justify='left',padding=(0,8))
        self.label(card,textvariable=self.setup_steps,style='CardMuted.TLabel',wraplength=690,justify='left')
        self.label(card,textvariable=self.setup_elapsed,style='CardMuted.TLabel',padding=(0,5))
        row=ttk.Frame(card,style='Card.TFrame');row.pack(fill='x',pady=(8,0))
        self.setup_button=self.button(row,'自动检测并准备安装',self.guided_install,accent=True,lock=True);self.setup_button.pack(side='left')
        self.button(row,'重新查找引擎',self.rescan_engines,lock=True).pack(side='left',padx=8)
        self.cancel_button=self.button(row,'取消本次任务',self.cancel_setup);self.cancel_button.pack(side='left');self.cancel_button.configure(state='disabled')
        self.candidates_button=self.button(card,'从已发现的引擎中选择…',self.choose_engine,lock=True);self.candidates_button.pack(anchor='w',pady=(8,0))
        card.bind('<Configure>',lambda e:self.setup_detail_label.configure(wraplength=max(280,e.width-40)))

    def selection_changed(self,project_changed=False):
        if self._suppress_selection:return
        self._selection_revision+=1;self.setup_result=None
        if project_changed:
            key=discovery.path_key(self.project.get())
            if key!=self._project_key:
                self._project_key=key;self.set_engine_value('');self.setup_candidates=[]
                self.engine_source.set('工程已切换，正在查找对应引擎。')
        self.setup_title.set('选择已变化，准备重新检测…')
        self.setup_button.configure(text='自动检测并准备安装')
        self.schedule_setup()

    def set_engine_value(self,value):
        self._suppress_selection=True
        try:self.engine.set(value)
        finally:self._suppress_selection=False

    def remember_engine(self,engine):
        if self.project.get():self.project_engines[discovery.path_key(self.project.get())]=engine
        self.known_engines=[engine]+[p for p in self.known_engines if isinstance(p,str) and discovery.path_key(p)!=discovery.path_key(engine)]
        self.known_engines=self.known_engines[:24]
        self.project_engines=dict(list(self.project_engines.items())[-100:])

    def schedule_setup(self,delay=600):
        if self._setup_after:
            try:self.after_cancel(self._setup_after)
            except tk.TclError:pass
        self._setup_after=self.after(delay,self.auto_setup)

    def selection_stamp(self):return (self._selection_revision,self.project.get(),self.engine.get())

    def rescan_engines(self):
        if self.busy:return
        self.set_engine_value('');self._selection_revision+=1;self.setup_result=None
        self.schedule_setup(1)

    def auto_setup(self):
        self._setup_after=None
        if self.closing:return
        if self.busy:self.schedule_setup(500);return
        if self._scan_running:self._scan_again=True;return
        if not self.project.get().strip() and not self.engine.get().strip():return
        self.start_inspection(automatic=True)

    def start_inspection(self,automatic=False,offline_only=False):
        base,state=self.setup_paths();stamp=self.selection_stamp()
        try:origin,item=self.package_choice(offline_only)
        except Exception as error:
            self.setup_failed(str(error));return
        item=dict(item);remembered=self.project_engines.get(discovery.path_key(stamp[1]),'');known=list(self.known_engines)
        if automatic:self._scan_running=True
        self.setup_title.set('正在查找引擎并检查适配…')
        self.setup_detail.set('检测在后台进行；可继续更换工程或引擎。' if automatic else '正在检查安装包、引擎与编译条件…')
        def job():
            try:return inspect_selection(stamp[1],stamp[2],base,state,item,origin,remembered,known)
            except Exception as error:return {'error':str(error),'trace':traceback.format_exc()}
        def done(result):
            if automatic:self._scan_running=False
            if self.closing:return
            if stamp!=self.selection_stamp():
                self._scan_again=False;self.schedule_setup(1);return
            if automatic:
                try:
                    latest_origin,latest=self.package_choice(offline_only)
                    if (latest_origin,latest.get('sha256'))!=(origin,item.get('sha256')):
                        self.schedule_setup(1);return
                except Exception as error:self.setup_failed(str(error));return
            if 'error' in result:
                self.setup_log(result['trace']);self.setup_failed(result['error'])
            else:
                self.accept_inspection(result)
                if not automatic and result.get('engine'):
                    self.continue_setup(result)
            if automatic and self._scan_again:self._scan_again=False;self.schedule_setup(1)
        self.worker(job,done,mutation=not automatic)

    def accept_inspection(self,result):
        found=result.get('discovery')
        if found:self.setup_candidates=found['candidates']
        if not result.get('engine'):
            self.setup_title.set('请选择对应引擎' if self.setup_candidates else '还没有找到引擎')
            self.setup_detail.set('找到多个或无法确认关联的候选。点击下方按钮，按版本、来源和路径选择一次，之后会记住。' if self.setup_candidates else '点击“选择引擎”找到 UnrealEditor.exe，也可粘贴引擎安装目录。无需改工程文件。')
            self.setup_button.configure(text='选择引擎后继续');self.setup_result=None;return
        engine=result['engine'];self.set_engine_value(engine);self.remember_engine(engine);self.save_settings()
        if result.get('info'):self.installed.set(result['info']['version'] or '尚未安装')
        source=next((' · '.join(c['sources']) for c in self.setup_candidates if discovery.path_key(c['path'])==discovery.path_key(engine)),'当前所选引擎（已记住此工程）')
        self.engine_source.set(source)
        result['selection']=(self.project.get(),self.engine.get());self.setup_result=result
        if result['report']['engine']['compatible']:
            self.setup_title.set('检测通过，可以安装')
            self.setup_detail.set(core.engine_version_label(result['report']['engine'].get('engine_version'))+'\n插件 '+result['item']['version']+' 已有匹配组件，无需编译。点击下方按钮确认安装。')
            self.setup_button.configure(text='确认安装到此工程…')
        elif result['preflight_error']:
            self.setup_title.set('需要补齐编译条件')
            self.setup_detail.set(result['preflight_error']+'\n点击下方查看解决步骤；处理后可直接重试，无需重新选择工程。')
            self.setup_button.configure(text='查看解决步骤…')
        else:
            self.setup_title.set('需要适配，已具备编译条件')
            self.setup_detail.set('插件 '+result['item']['version']+' 尚无此引擎的匹配组件。点击下方后自动编译 → 独立验证 → 确认安装，通常需要数分钟。')
            self.setup_button.configure(text='一键编译并继续安装…')
        self.status.set(self.setup_title.get())

    def choose_engine(self):
        if self.busy:return
        if not self.setup_candidates:self.rescan_engines();return
        window=tk.Toplevel(self);window.title('选择此工程的引擎');window.geometry('940x390');window.transient(self)
        body=ttk.Frame(window,padding=16);body.pack(fill='both',expand=True)
        self.label(body,'请核对版本与路径。选择一次后，下次会自动找到。',wraplength=850)
        tree=ttk.Treeview(body,columns=('version','source','path'),show='headings',selectmode='browse',height=9)
        for name,title,width in (('version','版本',85),('source','发现来源',230),('path','引擎路径',550)):
            tree.heading(name,text=title);tree.column(name,width=width,minwidth=70)
        tree.pack(fill='both',expand=True,pady=10)
        scroll=ttk.Scrollbar(body,orient='horizontal',command=tree.xview);scroll.pack(fill='x');tree.configure(xscrollcommand=scroll.set)
        for i,c in enumerate(self.setup_candidates):tree.insert('', 'end',iid=str(i),values=(c['version'],' / '.join(c['sources']),c['path']))
        def select(*_):
            selected=tree.selection()
            if not selected:return
            self.engine.set(self.setup_candidates[int(selected[0])]['path']);window.destroy()
        tree.bind('<Double-1>',select);self.button(body,'使用所选引擎',select,accent=True).pack(side='right',pady=10)

    def guided_install(self,offline_only=False):
        if self.busy:return
        if not self.project.get().strip():self.browse_project();return
        if not self.engine.get().strip() and self.setup_candidates:self.choose_engine();return
        self.start_inspection(offline_only=offline_only)

    def continue_setup(self,context):
        if not self.context_current(context):return
        if not context.get('info'):
            self.setup_failed('请先选择要安装的 .uproject 工程。');return
        if context['report']['engine']['compatible']:self.confirm_setup_install(context);return
        if context['preflight_error']:
            self.show_setup_solution(context['preflight_error']);return
        if not messagebox.askyesno('开始自动编译与验证',
                '引擎：'+context['engine']+'\n插件：'+context['item']['version']+
                '\n\n将自动完成：编译专用组件 → 在独立空工程验证 → 显示安装确认。\n过程通常需要数分钟，可取消；不修改引擎文件。\n\n是否开始？',parent=self):return
        base,state=self.setup_paths();self._cancel_setup=threading.Event();cancel=self._cancel_setup
        self.begin_setup_task('正在编译适配组件…')
        def job():
            try:
                result=native_builder.build(context['engine'],context['archive'],context['item'],base/'compiler',state/'NativeBuilds',
                    lambda message:self.events.put(('setup_phase',message)),cancel_event=cancel)
                return {'result':result}
            except Exception as error:return {'error':str(error),'trace':traceback.format_exc()}
        def done(outcome):
            self.end_setup_task()
            if 'error' in outcome:
                self.setup_log(outcome['trace']);self.setup_failed(outcome['error']);return
            if cancel.is_set():self.setup_failed('已取消。验证结果已保留，可稍后继续安装。');return
            context['cache_warning']=''
            self.setup_title.set('编译与验证通过，等待安装确认')
            self.confirm_setup_install(context)
        self.worker(job,done)

    def context_current(self,context):
        if context.get('selection')!=(self.project.get(),self.engine.get()):
            self.setup_failed('工程或引擎已变化，请重新检测当前选择。');return False
        return True

    def confirm_setup_install(self,context):
        if not self.context_current(context):return
        info=context['info'];item=context['item'];base,state=self.setup_paths()
        if not messagebox.askyesno('确认安装到此工程',
                '插件版本：'+item['version']+'\n目标工程：'+str(info['file'])+'\n安装目录：'+str(info['plugin'])+
                '\n\n只安装此插件，旧插件会备份。首次安装或原生组件变化时，请先保存并关闭这个工程。\n是否安装？',parent=self):
            self.setup_title.set('适配已就绪，尚未安装');self.setup_detail.set('已验证的适配会保留。再次点击下方按钮即可继续安装。');self.setup_button.configure(text='继续安装…');return
        self.begin_setup_task('正在安装已验证的插件…',cancellable=False)
        def job():
            try:
                local=None if context.get('cache_warning') else native_builder.cached(context['engine'],item,state/'NativeBuilds')
                result=core.install(info['file'],*(local or (context['archive'],item)),engine_path=context['engine'])
                return {'result':result}
            except Exception as error:return {'error':str(error),'trace':traceback.format_exc()}
        def done(outcome):
            self.end_setup_task()
            if 'error' in outcome:
                self.setup_log(outcome['trace']);self.setup_failed(outcome['error']+'\n适配结果已保留。处理后点击“重试”，无需重新编译。');return
            self.installed.set(item['version']);self.setup_title.set('安装完成，可以打开工作台')
            mode=outcome['result']['mode']
            self.setup_detail.set('热更新已完成，当前参数保留。' if mode=='hot_reloaded' else '点击“启动 UE / 独立工作台”。如果该工程已经打开，请保存工作并重启一次。')
            self.status.set(self.setup_title.get());self.progress['value']=100
        self.worker(job,done)

    def begin_setup_task(self,title,cancellable=True):
        self.setup_title.set(title);self._task_started=time.monotonic()
        self.cancel_button.configure(state='normal' if cancellable else 'disabled')
        self.progress.configure(mode='indeterminate');self.progress.start(15)
        self.tick_setup()

    def tick_setup(self):
        if self._task_started is None:return
        elapsed=int(time.monotonic()-self._task_started)
        self.setup_elapsed.set('已用时 '+str(elapsed//60)+' 分 '+str(elapsed%60)+' 秒 · 日志持续写入，可点击“查看编译日志”')
        self._setup_tick=self.after(1000,self.tick_setup)

    def end_setup_task(self):
        self._task_started=None;self._cancel_setup=None
        if self._setup_tick:self.after_cancel(self._setup_tick);self._setup_tick=None
        self.cancel_button.configure(state='disabled');self.progress.stop();self.progress.configure(mode='determinate');self.progress['value']=0

    def cancel_setup(self):
        if self._cancel_setup:
            self._cancel_setup.set();self.cancel_button.configure(state='disabled');self.setup_title.set('正在取消本次编译，请稍等…')

    def setup_failed(self,message):
        self.setup_title.set('尚未完成，请按提示处理')
        self.setup_detail.set(message);self.status.set('未安装新的插件。可查看原因后重试。');self.setup_button.configure(text='重试检测与安装…')

    def show_setup_solution(self,message):
        window=tk.Toplevel(self);window.title('完成适配前还需要这些准备');window.geometry('780x460');window.transient(self)
        frame=ttk.Frame(window,padding=20);frame.pack(fill='both',expand=True)
        self.label(frame,'补齐后点击重试，不用重新选择工程','Heading.TLabel')
        self.label(frame,message,wraplength=720,justify='left',padding=(0,14))
        self.label(frame,'• 缺少 MSVC / Windows SDK：在 Visual Studio Installer 中安装 C++ 工作负载。\n• 缺少引擎头文件或 .lib：补齐同一构建的 UE 开发包。\n• 安装失败提示工程正在运行：保存并关闭该工程，然后重试。',wraplength=720,justify='left')
        actions=ttk.Frame(frame);actions.pack(side='bottom',fill='x',pady=12)
        self.button(actions,'打开 C++ 安装器',self.open_cpp_installer).pack(side='left')
        self.button(actions,'查看官方开发环境说明 ↗',lambda:webbrowser.open(native_builder.DOCS)).pack(side='left',padx=8)
        def retry():window.destroy();self.guided_install()
        self.button(actions,'已处理，重试',retry,accent=True).pack(side='right')

    def open_cpp_installer(self):
        path=Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Microsoft Visual Studio/Installer/vs_installer.exe'
        if path.is_file():os.startfile(str(path))
        else:webbrowser.open('https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022')
