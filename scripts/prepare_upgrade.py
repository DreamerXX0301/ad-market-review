#!/usr/bin/env python3
"""Create an offline, reviewable upgrade plan; never execute it.

No Docker, application imports, database access, key reads or network calls.
All image references are immutable digests. Every restored copy has a unique
Compose project, and the candidate starts only API/init, never a worker.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'shixu-offline-upgrade-plan-v1'
_PROJECT = re.compile(r'[a-z0-9][a-z0-9_-]{0,48}')
_PART = r'[a-z0-9]+(?:[._-][a-z0-9]+)*'
_IMAGE = re.compile(_PART+r'(?::[0-9]{1,5})?(?:/'+_PART+r')*(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?@sha256:[0-9a-f]{64}')


def project(value):
    if not isinstance(value, str) or not _PROJECT.fullmatch(value):
        raise ValueError('项目名需为1至49位小写字母、数字、下划线或连字符')
    return value


def image(value):
    if (not isinstance(value, str) or not _IMAGE.fullmatch(value)
            or '..' in value.split('@', 1)[0] or '//' in value or '://' in value):
        raise ValueError('镜像必须为明确的 repository@sha256:64位小写摘要，不能仅用可覆盖标签')
    return value


def absolute_path(value):
    if not isinstance(value, (str, Path)) or any(c in str(value) for c in '\x00\n\r\t$\'"'):
        raise ValueError('路径含不支持的控制或环境插值字符')
    path = Path(value).expanduser()
    if not path.is_absolute(): raise ValueError('请使用明确的绝对路径')
    return path.resolve()


def port(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 65535:
        raise ValueError('HTTP端口需要1024至65535的整数')
    return value


def _command(phase, title, args, gate):
    return {'phase': phase, 'title': title, 'requires_manual_gate': gate,
            'argv': [str(arg) for arg in args], 'display': shlex.join(map(str, args))}


def make_plan(*, current_project, candidate_project, target_project, rollback_project,
              current_image, candidate_image, backup_root, master_key_file,
              output, candidate_port=8789, live_port=8788, candidate_commit=None):
    projects = [project(p) for p in (current_project, candidate_project, target_project, rollback_project)]
    if len(set(projects)) != len(projects):
        raise ValueError('现用、预演、新版和回滚项目必须各不相同；禁止共享现用卷')
    current_image = image(current_image); candidate_image = image(candidate_image)
    backup_root = absolute_path(backup_root); master = absolute_path(master_key_file)
    output = absolute_path(output); port(candidate_port); port(live_port)
    if candidate_port == live_port: raise ValueError('预演端口必须不同于现用端口')
    if backup_root == output or backup_root.is_relative_to(output) or output.is_relative_to(backup_root):
        raise ValueError('计划目录与备份目录必须独立，不能互相嵌套')
    if master.is_relative_to(output) or master.is_relative_to(backup_root):
        raise ValueError('主密钥须在计划与备份目录之外独立保存')
    if candidate_commit is not None and not re.fullmatch(r'[0-9a-f]{40}', candidate_commit):
        raise ValueError('候选提交需为完整40位小写Git SHA')
    preview_backup = backup_root/'preview'; final_backup = backup_root/'cutover'
    compose = ROOT/'deploy/compose.release.yaml'
    def dc(name, env):
        # Compose shell variables override --env-file. Drop these inherited
        # values so an old interactive export cannot redirect the reviewed plan.
        return ['env','-u','SHIXU_IMAGE_REF','-u','SHIXU_HTTP_PORT','-u','SHIXU_MASTER_KEY_FILE',
                'docker','compose','--env-file',output/env,'-p',name,'-f',compose]
    old_dc = dc(current_project, 'current.env')
    preview_dc = dc(candidate_project, 'candidate.env')
    target_dc = dc(target_project, 'target.env')
    rollback_dc = dc(rollback_project, 'rollback.env')
    archive = ['python3', ROOT/'scripts/docker_archive.py']
    commands = [
        _command('inspect', '人工核对原项目确是Docker部署、五类卷标签和端口，不能对宿主进程套用此计划',
            ['docker','ps','--filter','label=com.docker.compose.project='+current_project], 'operator-verifies-source-project'),
        _command('inspect', '核对候选digest、平台与发行记录中的commit；脚本没有拉取镜像',
            ['docker','image','inspect',candidate_image], 'operator-verifies-release-image-and-commit'),
        _command('preview', '旧版持续运行时创建独立预演备份，等待同步与凭据修改空闲',
            archive+['--image',current_image,'backup','--project',current_project,'--output',preview_backup], 'operator-authorizes-readonly-preview-backup'),
        _command('preview', '只恢复到全新预演卷；不启动服务、不恢复主密钥',
            archive+['--image',current_image,'restore',preview_backup,'--new-project',candidate_project], 'target-volumes-and-containers-must-not-exist'),
        _command('preview', '仅预演API和init；迁移仅作用于恢复副本，worker默认profile未开启',
            preview_dc+['up','-d','--no-build','api'], 'restored-preview-volumes-and-original-master-confirmed'),
        _command('preview', '仅确认进程健康；还须人工只读核对业务数量、数据日期、策略和证据',
            ['curl','--fail','http://127.0.0.1:'+str(candidate_port)+'/health'], 'candidate-readonly-qa'),
        _command('cutover', '预演通过后人工进入维护窗，停止现用API和唯一worker；不删除任何卷',
            old_dc+['stop','api','worker'], 'explicit-maintenance-window-after-preview-acceptance'),
        _command('cutover', '停止写入后制作最终备份，保留旧镜像摘要和原独立主密钥',
            archive+['--image',current_image,'backup','--project',current_project,'--output',final_backup], 'all-old-writers-confirmed-stopped'),
        _command('cutover', '最终备份恢复至另一套全新新版卷；不复用已迁移的预演副本',
            archive+['--image',current_image,'restore',final_backup,'--new-project',target_project], 'new-target-volumes-and-containers-must-not-exist'),
        _command('cutover', '新版API+init在新版卷迁移并占用已释放的业务端口，仍不启动worker',
            target_dc+['up','-d','--no-build','api'], 'old-api-and-worker-stopped-and-final-backup-verified'),
        _command('cutover', '切换后健康检查；人工确认业务与策略版本和原库对应',
            ['curl','--fail','http://127.0.0.1:'+str(live_port)+'/health'], 'operator-accepts-target-data-and-credentials'),
        _command('cutover', '最后明确开启唯一新版worker；恢复备份的排队任务仍需手动决定',
            target_dc+['--profile','worker','up','-d','--no-build','worker'], 'old-worker-confirmed-stopped-and-new-api-accepted'),
        _command('rollback', '仅需要回滚时停止新版写入者，保留新版卷以备追回切换后数据',
            target_dc+['stop','api','worker'], 'operator-accepts-rpo-and-authorizes-rollback'),
        _command('rollback', '从切换前备份恢复到第三套卷，绝不让旧镜像读取已迁移新版业务库',
            archive+['--image',current_image,'restore',final_backup,'--new-project',rollback_project], 'rollback-volumes-and-containers-must-not-exist'),
        _command('rollback', '旧digest API+init只挂恢复到旧schema的回滚卷',
            rollback_dc+['up','-d','--no-build','api'], 'old-backup-and-original-key-verified'),
        _command('rollback', '回滚业务核验完成后再开启唯一旧worker',
            rollback_dc+['--profile','worker','up','-d','--no-build','worker'], 'all-other-workers-confirmed-stopped'),
    ]
    return {'format': FORMAT, 'created_at': datetime.now(timezone.utc).isoformat(),
        'executed': False, 'scope': 'Docker命名卷部署的离线操作计划，不适用于正在本机dev.sh启动的实例',
        'projects': dict(zip(('current','candidate','target','rollback'),projects)),
        'images': {'current':current_image,'candidate':candidate_image,'candidate_commit':candidate_commit},
        'ports': {'live':live_port,'candidate':candidate_port},
        'paths': {'output':str(output),'backup_root':str(backup_root),'master_key_file':str(master),
                  'release_compose':str(compose)},
        'checks_not_performed': ['没有连接Docker、拉取或检查镜像','没有访问数据库、现用服务或卷',
            '没有读取或验证主密钥内容','没有执行备份、迁移、停止、切换或回滚',
            '没有验证当前端口可用性、项目卷身份或候选发行commit与image对应关系'],
        'rollback': {'backup':str(final_backup),'restores_independent_volumes':True,
                     'post_cutover_writes_preserved_automatically':False},
        'commands':commands}


def write_plan(plan):
    output = Path(plan['paths']['output'])
    if output.exists(): raise ValueError('计划输出目录必须不存在，不覆盖已有计划')
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.shixu-upgrade-plan-',dir=output.parent))
    temporary.chmod(0o700)
    try:
        (temporary/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
        for name, image_ref, http_port in (
            ('current',plan['images']['current'],plan['ports']['live']),
            ('candidate',plan['images']['candidate'],plan['ports']['candidate']),
            ('target',plan['images']['candidate'],plan['ports']['live']),
            ('rollback',plan['images']['current'],plan['ports']['live'])):
            text = ('SHIXU_IMAGE_REF='+image_ref+'\nSHIXU_HTTP_PORT='+str(http_port)
                +'\nSHIXU_MASTER_KEY_FILE='+json.dumps(plan['paths']['master_key_file'])+'\n')
            (temporary/(name+'.env')).write_text(text)
        readme = ['# 离线升级计划','',
            '这里只生成操作计划，没有执行任何命令。不要整体复制执行；逐阶段人工核对每项前置条件。',
            '仅适用于Compose命名卷安装；现用本机8788进程不属于此计划操作对象。',
            '主密钥仅记录文件路径，未读取正文。所有恢复均使用全新项目和卷。',
            '回滚恢复切换前备份，切换后的新增写入不会自动迁回；旧新版卷都保留。','']
        for command in plan['commands']:
            readme.extend(['## '+command['phase']+' · '+command['title'],'',
                '人工门槛：`'+command['requires_manual_gate']+'`','',
                '```bash',command['display'],'```',''])
        (temporary/'README.md').write_text('\n'.join(readme))
        for path in temporary.iterdir(): path.chmod(0o600)
        # Exclusive mkdir also closes the check-to-publish race: rename alone
        # could replace a concurrently created empty plan directory.
        output.mkdir(mode=0o700)
        for path in temporary.iterdir(): os.replace(path,output/path.name)
    finally:
        if temporary.exists():
            for path in temporary.iterdir(): path.unlink()
            temporary.rmdir()
    return {'output':str(output),'format':FORMAT,'executed':False,'commands_planned':len(plan['commands'])}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('current-project','candidate-project','target-project','rollback-project',
                 'current-image','candidate-image','backup-root','master-key-file','output'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--candidate-port',type=int,default=8789)
    parser.add_argument('--live-port',type=int,default=8788)
    parser.add_argument('--candidate-commit')
    args=vars(parser.parse_args())
    print(json.dumps(write_plan(make_plan(**args)),ensure_ascii=False,indent=2))


if __name__=='__main__': main()
