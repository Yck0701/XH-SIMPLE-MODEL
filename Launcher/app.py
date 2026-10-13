"""Windows launcher; network and installation work never run on the UI thread."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser
import core
from version import VERSION, PRODUCT

BASE=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
STATE=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'VFXMeshLauncher'
BG='#0c111b';CARD='#141f2e';TEXT='#e7eef8';MUTED='#8da0b8';ACCENT='#65e1cd';LINE='#29384d'


def settings():
    try:return core.read_json(STATE/'settings.json')
    except (OSError,ValueError):return {}


def prefer_updated_launcher():
    if not getattr(sys,'frozen',False):return False
    try:
        current=core.read_json(STATE/'preferred-launcher.json');exe=Path(current['exe'])
        if core.version_tuple(current['version'])>core.version_tuple(VERSION) and exe.resolve().is_relative_to((STATE/'Versions').resolve()) and core.digest(exe)==current['sha256']:
            subprocess.Popen([str(exe)]);return True
    except (OSError,ValueError,KeyError):pass
    return False


class App(tk.Tk):
    def __init__(self, initial_project='', auto_check=None):
        super().__init__();self.title(PRODUCT+'  '+VERSION);self.geometry('1080x810');self.minsize(980,760)
        self.configure(bg=BG)
        self.events=queue.Queue();self.busy=False;self.checking=False;self.closing=False;self.feed=None;self.last_update_error=''
        config=settings()
        self.project=tk.StringVar(value=initial_project or config.get('project',''))
        self.engine=tk.StringVar(value=config.get('engine',''))
        self.auto_check=tk.BooleanVar(value=config.get('auto_check',True) if auto_check is None else auto_check)
        self.status=tk.StringVar(value='选好工程，先安装，再启动。')
        self.installed=tk.StringVar(value='未选择工程')
        self.remote=tk.StringVar(value='等待检查')
        self.update_status=tk.StringVar(value='打开启动器时会在后台自动检查 GitHub 更新。')
        self.check_time=tk.StringVar(value='尚未检查')
        self.notes=tk.StringVar(value='首次使用可直接安装内置版本，之后通过 GitHub 获取更新。')
        self.control_widgets=[];self.pages={};self.nav={}
        self.style_ui();self.build_ui();self.refresh_project();self.show('home')
        self.protocol('WM_DELETE_WINDOW',self.close)
        self._pump_after=self.after(80,self.pump)
        self._check_after=self.after(500,self.check) if self.auto_check.get() else None

    def style_ui(self):
        s=ttk.Style(self);s.theme_use('clam')
        s.configure('.',font=('Microsoft YaHei UI',10),background=BG,foreground=TEXT)
        s.configure('TFrame',background=BG);s.configure('Card.TFrame',background=CARD)
        s.configure('TLabel',background=BG,foreground=TEXT)
        s.configure('Card.TLabel',background=CARD,foreground=TEXT)
        s.configure('Muted.TLabel',background=BG,foreground=MUTED)
        s.configure('CardMuted.TLabel',background=CARD,foreground=MUTED)
        s.configure('Title.TLabel',font=('Microsoft YaHei UI',23,'bold'))
        s.configure('Heading.TLabel',font=('Microsoft YaHei UI',13,'bold'))
        s.configure('CardHeading.TLabel',background=CARD,font=('Microsoft YaHei UI',12,'bold'))
        s.configure('TButton',background='#233249',foreground=TEXT,padding=(16,10),borderwidth=0)
        s.map('TButton',background=[('active','#30445e'),('disabled','#1a2433')],foreground=[('disabled','#627188')])
        s.configure('Accent.TButton',background=ACCENT,foreground='#0c2726',font=('Microsoft YaHei UI',10,'bold'))
        s.map('Accent.TButton',background=[('active','#96f1df'),('disabled','#244c4b')],foreground=[('disabled','#8faba8')])
        s.configure('Nav.TButton',background=BG,foreground=MUTED,anchor='w',padding=(18,14))
        s.map('Nav.TButton',background=[('active',CARD)])
        s.configure('Selected.Nav.TButton',background='#173b40',foreground=ACCENT)
        s.configure('TEntry',fieldbackground='#0c1420',foreground=TEXT,insertcolor=ACCENT,padding=8,bordercolor=LINE)
        s.configure('TCheckbutton',background=BG,foreground=MUTED,indicatorbackground=CARD,indicatorforeground=ACCENT)
        s.map('TCheckbutton',background=[('active',BG)],foreground=[('active',TEXT)])
        s.configure('Horizontal.TProgressbar',background=ACCENT,troughcolor=CARD,borderwidth=0)

    def label(self,parent,text='',style='TLabel',**kwargs):
        w=ttk.Label(parent,text=text,style=style,**kwargs);w.pack(anchor='w');return w

    def button(self,parent,text,command,accent=False,lock=False):
        w=ttk.Button(parent,text=text,command=command,style='Accent.TButton' if accent else 'TButton')
        if lock:self.control_widgets.append(w)
        return w

    def card(self,parent):
        f=ttk.Frame(parent,style='Card.TFrame',padding=18);f.pack(fill='x',pady=(0,12));return f

    def build_ui(self):
        outer=ttk.Frame(self,padding=24);outer.pack(fill='both',expand=True)
        sidebar=ttk.Frame(outer,width=175);sidebar.pack(side='left',fill='y',padx=(0,25));sidebar.pack_propagate(False)
        logo=tk.Canvas(sidebar,width=70,height=65,bg=BG,highlightthickness=0);logo.pack(anchor='w',pady=(3,12))
        logo.create_oval(8,4,60,56,outline=ACCENT,width=2);logo.create_oval(23,4,45,56,outline='#408a8b')
        logo.create_oval(8,20,60,40,outline='#408a8b');logo.create_line(8,30,60,30,fill=ACCENT)
        self.label(sidebar,'VFX MESH','Heading.TLabel');self.label(sidebar,'特效网格 · 编辑器工具','Muted.TLabel')
        ttk.Frame(sidebar,height=30).pack()
        for key,title in [('home','01   开始使用'),('updates','02   更新中心'),('guide','03   使用教学'),('backup','04   备份与恢复')]:
            b=self.button(sidebar,title,lambda k=key:self.show(k));b.configure(style='Nav.TButton');b.pack(fill='x',pady=3);self.nav[key]=b
        footer=ttk.Frame(sidebar);footer.pack(side='bottom',fill='x')
        self.label(footer,'启动器 '+VERSION,'Muted.TLabel')
        self.label(footer,'适用 UE 5.8','Muted.TLabel')
        self.button(footer,'GitHub 仓库 ↗',lambda:webbrowser.open(core.WEB)).pack(fill='x',pady=(12,0))
        main=ttk.Frame(outer);main.pack(side='left',fill='both',expand=True)
        self.container=ttk.Frame(main);self.container.pack(fill='both',expand=True)
        for key in self.nav:self.pages[key]=ttk.Frame(self.container)
        self.build_home();self.build_updates();self.build_guide();self.build_backup()
        self.progress=ttk.Progressbar(main,mode='determinate');self.progress.pack(fill='x',pady=(12,8))
        self.status_label=ttk.Label(main,textvariable=self.status,wraplength=760,style='Muted.TLabel');self.status_label.pack(fill='x')

    def build_home(self):
        page=self.pages['home']
        self.label(page,'让特效网格，随手可调','Title.TLabel')
        self.label(page,'不再反复导入导出。安装一次，在 UE 中直接改形状。','Muted.TLabel',padding=(0,7,0,18))
        target=self.card(page);self.label(target,'1  选择你的 Unreal 工程','CardHeading.TLabel')
        self.label(target,'选择 .uproject 文件。下次打开会自动记住。','CardMuted.TLabel',padding=(0,4,0,10))
        row=ttk.Frame(target,style='Card.TFrame');row.pack(fill='x')
        entry=ttk.Entry(row,textvariable=self.project);entry.pack(side='left',fill='x',expand=True,padx=(0,8));self.control_widgets.append(entry)
        self.button(row,'选择工程',self.browse_project,lock=True).pack(side='left')
        entry.bind('<FocusOut>',lambda _:self.refresh_project())
        self.label(target,'对应引擎（通常自动找到）','CardMuted.TLabel',padding=(0,14,0,5))
        row=ttk.Frame(target,style='Card.TFrame');row.pack(fill='x')
        entry=ttk.Entry(row,textvariable=self.engine);entry.pack(side='left',fill='x',expand=True,padx=(0,8));self.control_widgets.append(entry)
        self.button(row,'选择引擎',self.browse_engine,lock=True).pack(side='left')
        versions=self.card(page)
        row=ttk.Frame(versions,style='Card.TFrame');row.pack(fill='x')
        for title,variable in [('工程已安装',self.installed),('GitHub 最新版',self.remote)]:
            col=ttk.Frame(row,style='Card.TFrame');col.pack(side='left',fill='x',expand=True)
            self.label(col,title,'CardMuted.TLabel');self.label(col,style='CardHeading.TLabel',textvariable=variable,padding=(0,5))
        self.label(versions,'默认自动检查更新；无法联网时可安装内置版本。','CardMuted.TLabel',padding=(0,10,0,0))
        actions=ttk.Frame(page);actions.pack(fill='x',pady=(4,12))
        self.button(actions,'2  安装 / 更新插件',self.install,accent=True,lock=True).pack(side='left',padx=(0,10))
        self.button(actions,'3  启动 UE / 独立工作台',self.launch,lock=True).pack(side='left')
        self.button(actions,'检测兼容性',self.check_compatibility,lock=True).pack(side='left',padx=(10,0))
        self.label(page,'首次安装需要重启一次 UE，之后兼容的脚本更新可直接热重载。','Muted.TLabel',padding=(0,6))
        self.label(page,'在 Niagara 中把生成的模型指定给 Mesh Renderer 一次，之后即可实时调参。','Muted.TLabel',wraplength=740)
        self.button(page,'第一次用？查看 5 步教学 →',lambda:self.show('guide')).pack(anchor='w',pady=(15,0))

    def build_updates(self):
        page=self.pages['updates'];self.label(page,'更新中心','Title.TLabel')
        self.label(page,'更新来源：Yck0701 / XH-SIMPLE-MODEL','Muted.TLabel',padding=(0,7,0,20))
        card=self.card(page);self.label(card,style='CardHeading.TLabel',textvariable=self.update_status,wraplength=730)
        self.label(card,style='CardMuted.TLabel',textvariable=self.check_time,padding=(0,10))
        ttk.Checkbutton(page,text='每次打开启动器，自动检查 GitHub 更新',variable=self.auto_check,command=self.save_settings).pack(anchor='w',pady=(4,14))
        row=ttk.Frame(page);row.pack(fill='x')
        self.button(row,'立即检查',self.check).pack(side='left',padx=(0,10))
        self.button(row,'安装 / 更新插件',self.install,accent=True,lock=True).pack(side='left',padx=(0,10))
        self.button(row,'更新启动器并重开',self.update_launcher,lock=True).pack(side='left')
        row=ttk.Frame(page);row.pack(fill='x',pady=(10,0))
        self.button(row,'安装内置版本',lambda:self.install(offline_only=True),lock=True).pack(side='left',padx=(0,10))
        self.button(row,'浏览器下载 ↗',lambda:webbrowser.open(core.WEB+'/releases/latest')).pack(side='left',padx=(0,10))
        self.button(row,'导出诊断报告',self.export_diagnostic,lock=True).pack(side='left')
        self.label(page,'更新说明','Heading.TLabel',padding=(0,24,0,12))
        self.label(page,textvariable=self.notes,wraplength=735,justify='left')
        self.label(page,'更新规则','Heading.TLabel',padding=(0,25,0,12))
        self.label(page,'• 脚本更新：下载校验后直接刷新 UE 面板，保留未保存的模型参数。\n• 面板 / 原生组件 / 资源更新：先保存并关闭该工程，再点击更新。\n• 无法联网：继续使用当前版本，稍后再检查。\n• 不会自动关闭 UE，也不会覆盖工程中的特效模型。',wraplength=730,style='Muted.TLabel',justify='left')

    def build_guide(self):
        page=self.pages['guide'];self.label(page,'5 步做出第一个特效网格','Title.TLabel')
        self.label(page,'跟着做一遍，之后只需拖参数。','Muted.TLabel',padding=(0,7,0,20))
        steps=[('01   选工程 → 安装','回到“开始使用”，选中 .uproject，点击“安装 / 更新插件”。第一次安装后重启 UE。'),
            ('02   打开独立工作台','从 UE 顶部“特效网格”打开工作台。左侧调模型，中间鼠标控制预览，右侧切换材质 / 环境；顶部可收起两侧面板。'),
            ('03   调整材质与环境','材质在侧栏直接调整，保存时选择替换或新增实例，成功后显示路径。开启“实时迭代原材质”可联动 Niagara。环境页可调 HDRI、灯光、曝光和地板。'),
            ('04   选 UE 目录 → 确认保存','在内容浏览器选文件夹，点“使用所选目录”，再点左侧“保存位置”下方的“保存生成模型…”。材质保存会打开独立确认弹窗，两者分别保存。'),
            ('05   Niagara 引用 → 实时调参','Mesh Renderer → Meshes → 第 0 项 → Mesh 选择保存的模型。修改参数更新同一资产；再次保存仍需确认。')]
        for title,body in steps:
            card=self.card(page);self.label(card,title,'CardHeading.TLabel');self.label(card,body,'CardMuted.TLabel',wraplength=710,justify='left',padding=(0,6,0,0))
        self.button(page,'打开完整图解教学 ↗',self.open_guide).pack(anchor='w',pady=(0,6))

    def build_backup(self):
        page=self.pages['backup'];self.label(page,'不怕更新出问题','Title.TLabel')
        self.label(page,'每次更新前，旧插件会完整备份到当前工程的 Saved 目录。','Muted.TLabel',padding=(0,7,0,24))
        card=self.card(page);self.label(card,'需要恢复旧版本时','CardHeading.TLabel')
        self.label(card,'1. 保存并关闭当前 Unreal 工程。\n2. 点击下方“恢复上次备份”。\n3. 重新启动 UE。','CardMuted.TLabel',padding=(0,10,0,0),justify='left')
        row=ttk.Frame(page);row.pack(fill='x',pady=8)
        self.button(row,'恢复上次备份',self.restore,lock=True).pack(side='left',padx=(0,10))
        self.button(row,'查看备份目录',lambda:self.open_folder('backups')).pack(side='left',padx=(0,10))
        self.button(row,'打开插件目录',lambda:self.open_folder('plugin')).pack(side='left')
        self.label(page,'常见问题','Heading.TLabel',padding=(0,28,0,12))
        self.label(page,'找不到菜单：确认选的是当前工程，首次安装后重启 UE。\n材质没变化：检查 Niagara 是否使用了覆盖材质。\n模型仍然切边：本工具默认关闭 Nanite，请不要为半透明特效网格开启它。\n提示需重启：本次更新涉及资源或热更新组件，关闭工程后再更新即可。',style='Muted.TLabel',wraplength=730,justify='left')

    def show(self,name):
        for key,page in self.pages.items():
            page.pack_forget();self.nav[key].configure(style='Selected.Nav.TButton' if key==name else 'Nav.TButton')
        self.pages[name].pack(fill='both',expand=True)

    def save_settings(self):
        try:core.write_json(STATE/'settings.json',{'project':self.project.get(),'engine':self.engine.get(),'auto_check':self.auto_check.get()})
        except OSError:self.status.set('无法保存启动器设置，请检查本机目录权限。')

    def refresh_project(self):
        if not self.project.get():self.installed.set('未选择工程');return
        try:
            info=core.project_info(self.project.get());self.installed.set(info['version'] or '尚未安装')
            found=core.detect_engine(info)
            if found and not self.engine.get():self.engine.set(str(found))
            self.save_settings()
        except Exception as error:self.installed.set('路径无效');self.status.set(str(error))

    def browse_project(self):
        path=filedialog.askopenfilename(title='选择 Unreal 工程',filetypes=[('Unreal 工程','*.uproject')])
        if path:self.project.set(path);self.engine.set('');self.refresh_project()

    def browse_engine(self):
        path=filedialog.askopenfilename(title='选择 UnrealEditor.exe',filetypes=[('Unreal 编辑器','UnrealEditor.exe')])
        if path:
            try:core.validate_engine(path);self.engine.set(path);self.save_settings()
            except Exception as error:messagebox.showerror('引擎不匹配',str(error),parent=self)

    def worker(self,job,done,mutation=True):
        if mutation and self.busy:return
        if mutation:
            self.busy=True
            for w in self.control_widgets:w.configure(state='disabled')
        def run():
            try:self.events.put(('done',done,job(),mutation))
            except Exception as error:
                self.events.put(('error',str(error),traceback.format_exc(),mutation))
        threading.Thread(target=run,daemon=True).start()

    def pump(self):
        try:
            while True:
                event=self.events.get_nowait()
                if event[0]=='progress':self.progress['value']=event[1]*100;continue
                if event[-1]:
                    self.busy=False
                    for w in self.control_widgets:w.configure(state='normal')
                if event[0]=='done':
                    try:event[1](event[2])
                    except Exception as error:
                        self.status.set(str(error));messagebox.showerror('操作未完成',str(error),parent=self)
                else:
                    self.status.set(event[1]);self.progress['value']=0
                    if event[-1]:messagebox.showerror('操作未完成',event[1],parent=self)
                    try:
                        STATE.mkdir(parents=True,exist_ok=True)
                        with (STATE/'launcher.log').open('a',encoding='utf-8') as log:log.write(time.strftime('%Y-%m-%d %H:%M:%S')+'\n'+event[2]+'\n')
                    except OSError:pass
        except queue.Empty:pass
        if not self.closing:self._pump_after=self.after(80,self.pump)

    def check(self):
        if self.checking:return
        self.checking=True;self.update_status.set('正在连接 GitHub…');self.remote.set('检查中')
        def job():
            try:return {'feed':core.check_updates()}
            except Exception as error:return {'error':str(error) if isinstance(error,RuntimeError) else core.network_error(error)}
        def done(result):
            self.checking=False;self.check_time.set('检查时间：'+time.strftime('%m-%d %H:%M'))
            if 'error' in result:
                self.last_update_error=result['error']
                cached='；保留上次获取的版本 '+self.feed['plugin']['version'] if self.feed else ''
                self.update_status.set('暂时无法检查更新'+cached+'。');self.remote.set(self.feed['plugin']['version']+'（上次）' if self.feed else '暂未获取')
                self.notes.set(result['error']+'\n\n可点击“安装内置版本”，或通过“浏览器下载”获取完整启动器 ZIP。')
                try:core.write_json(STATE/'update-error.json',{'time':time.time(),'version':VERSION,'error':result['error']})
                except OSError:pass
                return
            self.last_update_error='';self.feed=result['feed'];self.remote.set(self.feed['plugin']['version'])
            installed=self.installed.get()
            new=installed in ('未选择工程','尚未安装','路径无效') or core.version_tuple(self.feed['plugin']['version'])>core.version_tuple(installed)
            self.update_status.set(('可安装新版插件 '+self.feed['plugin']['version']) if new else '当前插件已是最新版本。')
            self.notes.set(self.feed.get('notes','无更新说明。'))
        self.worker(job,done,mutation=False)

    def package_choice(self,offline_only=False):
        offline=core.read_json(BASE/'offline/manifest.json')
        candidates=[(core.version_tuple(offline['plugin']['version']),'offline',offline['plugin'])]
        if self.feed and not offline_only:
            if core.version_tuple(self.feed.get('minimum_launcher','1.1.0'))>core.version_tuple(VERSION):
                raise ValueError('请先点击“更新启动器并重开”，再安装此插件版本。')
            candidates.append((core.version_tuple(self.feed['plugin']['version']),'online',self.feed['plugin']))
        return max(candidates,key=lambda item:item[0])[1:]

    def install(self,offline_only=False):
        try:
            info=core.project_info(self.project.get());engine=self.engine.get()
            if engine:core.validate_engine(engine)
            else:raise ValueError('请选择对应的 UnrealEditor.exe，确认 UE 版本后再安装。')
            origin,item=self.package_choice(offline_only);project=str(info['file']);self.save_settings()
        except Exception as error:messagebox.showerror('请先完成设置',str(error),parent=self);return
        self.status.set('正在校验并安装插件，请稍等…');self.progress['value']=0
        def job():
            archive=BASE/'offline'/item['file'] if origin=='offline' else core.download(item,STATE/'Downloads',lambda v:self.events.put(('progress',v)))
            return core.install(project,archive,item,engine_path=engine)
        def done(result):
            self.progress['value']=100;self.refresh_project()
            self.status.set({'hot_reloaded':'已完成热更新，UE 面板已刷新，当前模型参数保留。',
                'restart_required':'插件已更新。首次启用热更新连接，请保存并重启 UE 一次。',
                'installed':'安装完成，点击“启动 UE / 独立工作台”开始制作。'}[result['mode']])
        self.worker(job,done)

    def export_diagnostic(self):
        try:
            engine=str(core.validate_engine(self.engine.get()))
            offline=core.read_json(BASE/'offline/manifest.json')['plugin'];archive=BASE/'offline'/offline['file']
            if core.digest(archive)!=offline['sha256']:raise ValueError('内置安装包校验失败，请重新完整解压启动器。')
        except Exception as error:messagebox.showerror('诊断未完成',str(error),parent=self);return
        path=filedialog.asksaveasfilename(title='保存引擎与更新诊断（不含项目资产）',defaultextension='.json',
            initialfile='VFXMesh-diagnostic.json',filetypes=[('诊断报告','*.json')])
        if not path:return
        update_error=self.last_update_error
        def job():
            report=core.diagnostic_report(engine,archive);report['update_error']=update_error
            core.write_json(path,report);return report
        def done(report):
            self.status.set('诊断报告已保存：'+path)
            messagebox.showinfo('诊断报告已保存','引擎校验'+('通过。' if report['engine']['compatible'] else '发现差异。')+'\n'+path+'\n\n报告仅包含版本、文件名、指纹和更新错误；不会自动上传，也不会修改工程。',parent=self)
        self.status.set('正在读取引擎指纹并生成诊断报告…');self.worker(job,done)

    def check_compatibility(self):
        try:
            engine=str(core.validate_engine(self.engine.get()))
            item=core.read_json(BASE/'offline/manifest.json')['plugin'];archive=BASE/'offline'/item['file']
            if core.digest(archive)!=item['sha256']:raise ValueError('内置安装包校验失败，请重新完整解压启动器。')
        except Exception as error:messagebox.showerror('无法检测',str(error),parent=self);return
        def done(report):
            result=report['engine'];window=tk.Toplevel(self);window.title('引擎兼容性检测');window.geometry('790x560');window.minsize(650,440)
            window.transient(self);window.configure(bg=BG)
            frame=ttk.Frame(window,padding=20);frame.pack(fill='both',expand=True)
            self.label(frame,'检测通过' if result['compatible'] else '需要处理兼容性差异','Heading.TLabel')
            content='检测的插件：'+report['plugin_version']+'（启动器内置包）\n引擎：'+engine+'\nBuildId：'+result['build_id']+'\n\n'
            content+=report['solution']+'\n\n文件检查：\n'
            for f in result['files']:
                content+=({'match':'通过','missing':'缺失','different':'内容不同'}[f['status']])+'  '+Path(f['file']).name+'\n'
                if f['status']!='match':content+='  预期 SHA-256：'+f['expected_sha256']+'\n  当前 SHA-256：'+str(f['actual_sha256'] or '文件不存在')+'\n'
            actions=ttk.Frame(frame);actions.pack(side='bottom',fill='x')
            body=ttk.Frame(frame);body.pack(fill='both',expand=True,pady=12)
            text=tk.Text(body,wrap='word',bg=CARD,fg=TEXT,relief='flat',font=('Microsoft YaHei UI',10),padx=10,pady=10)
            scroll=ttk.Scrollbar(body,command=text.yview);scroll.pack(side='right',fill='y')
            text.configure(yscrollcommand=scroll.set);text.pack(fill='both',expand=True);text.insert('1.0',content);text.configure(state='disabled')
            def save():
                path=filedialog.asksaveasfilename(parent=window,title='保存兼容性诊断',defaultextension='.json',initialfile='VFXMesh-diagnostic.json',filetypes=[('诊断报告','*.json')])
                if path:
                    try:core.write_json(path,dict(report,update_error=self.last_update_error));self.status.set('诊断报告已保存：'+path)
                    except OSError as error:messagebox.showerror('保存失败',str(error),parent=window)
            self.button(actions,'导出这份检测报告',save).pack(side='left')
            self.button(actions,'关闭',window.destroy).pack(side='right')
            self.status.set('兼容性检测通过。' if result['compatible'] else '已列出不匹配文件和解决方案；工程未修改。')
        self.status.set('正在只读检测引擎与内置插件的兼容性…');self.worker(lambda:core.diagnostic_report(engine,archive),done)

    def launch(self):
        project,engine=self.project.get(),self.engine.get();self.save_settings();self.status.set('正在打开你的工程与网格面板…')
        self.worker(lambda:core.launch(project,engine),lambda result:self.status.set('已打开网格面板。' if result=='opened' else 'UE 正在启动，加载完成后会自动打开网格面板。'))

    def restore(self):
        if not messagebox.askyesno('恢复上次备份','将恢复当前工程的上一版插件，并备份现在的版本。请先保存并关闭该工程。',parent=self):return
        project=self.project.get()
        def done(version):self.refresh_project();self.status.set('已恢复插件 '+version+'，可以重新启动 UE。')
        self.worker(lambda:core.restore_backup(project),done)

    def open_folder(self,kind):
        try:
            info=core.project_info(self.project.get());path=info['plugin'] if kind=='plugin' else info['saved']/'backups'
            if not path.is_dir():raise ValueError('目录还未创建，请先安装或更新插件。')
            os.startfile(str(path))
        except Exception as error:messagebox.showinfo('目录',str(error),parent=self)

    def open_guide(self):
        path=BASE/'使用教学.html'
        if path.is_file():webbrowser.open(path.as_uri())
        else:webbrowser.open(core.WEB+'/blob/main/docs/使用教学.md')

    def update_launcher(self):
        if not self.feed:messagebox.showinfo('检查更新','请先点击“立即检查”。',parent=self);return
        item=self.feed['launcher']
        if core.version_tuple(item['version'])<=core.version_tuple(VERSION):
            self.status.set('启动器已经是最新版本。');return
        def job():
            archive=core.download(item,STATE/'Downloads',lambda v:self.events.put(('progress',v)))
            target=STATE/'Versions'/(item['version']+'-'+str(int(time.time())))
            if target.exists():raise ValueError('此版本正在处理，请稍后再试。')
            root=core.safe_extract(archive,target,'VFXMeshLauncher')
            if core.read_json(root/'version.json')['version']!=item['version']:raise ValueError('启动器版本校验失败。')
            exe=root/'VFX特效网格启动器.exe'
            if not exe.is_file():raise ValueError('启动器安装包不完整。')
            return {'exe':str(exe),'version':item['version'],'sha256':core.digest(exe)}
        def done(result):
            self.save_settings();subprocess.Popen([result['exe']])
            core.write_json(STATE/'preferred-launcher.json',result);self.close()
        self.status.set('正在下载并准备新版启动器…');self.worker(job,done)

    def close(self):
        if self.busy:messagebox.showinfo('请稍等','正在安装或更新，请完成后再关闭启动器。',parent=self);return
        self.save_settings();self.closing=True
        for handle in (self._pump_after,self._check_after):
            if handle:
                try:self.after_cancel(handle)
                except tk.TclError:pass
        self.destroy()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',default='');parser.add_argument('--no-auto-check',action='store_true')
    parser.add_argument('--self-test',metavar='REPORT')
    args=parser.parse_args()
    if args.self_test:
        feed=core.read_json(BASE/'offline/manifest.json');item=feed['plugin'];archive=BASE/'offline'/item['file']
        report={'version':VERSION,'offline_checksum':core.digest(archive)==item['sha256'],'guide':(BASE/'使用教学.html').is_file(),'tk':str(tk.TkVersion)}
        core.write_json(args.self_test,report);return
    if prefer_updated_launcher():return
    app=App(args.project,False if args.no_auto_check else None);app.mainloop()


if __name__=='__main__':main()
