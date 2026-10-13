"""Limit plugin writes to ordinary paths in the currently open project."""
from pathlib import Path
import stat
import unreal


def project_path(path):
    root=Path(unreal.Paths.project_dir()).resolve()
    path=Path(path).absolute()
    if not path.is_relative_to(root):raise ValueError('目标不在当前工程内，已取消写入。')
    for item in (path,*path.parents):
        if item==root:break
        try:info=item.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):
            raise ValueError('目标路径包含链接或重解析目录，已取消写入，避免影响其他工程。')
    if not path.resolve().is_relative_to(root):raise ValueError('目标指向其他工程，已取消写入。')
    return path


def content_path(value):
    package,dot,obj=str(value).partition('.')
    parts=package.split('/')
    if len(parts)<3 or parts[:2]!=['','Game'] or any(not p or p in ('.','..') or ':' in p or '\\' in p for p in parts[2:]) or (dot and obj!=parts[-1]):
        raise ValueError('只允许修改当前工程 Content 下的目标资产。')
    base=Path(unreal.Paths.project_dir()).resolve()/'Content'
    target=project_path(base.joinpath(*parts[2:]))
    for suffix in ('.uasset','.uexp','.ubulk','.uptnl'):project_path(target.with_suffix(suffix))
    return package
