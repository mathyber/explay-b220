"""Create a bounded Ghidra pilot project and export known original PS functions."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from emulator.firmware import ROOT,load_firmware
from .setup_reverse_tools import GHIDRA,JDK,DIRECTORY

OUTPUT=ROOT/'analysis/reverse'


def environment():
    env=os.environ.copy();env['JAVA_HOME']=str(JDK)
    folders={n:DIRECTORY/'ghidra-local'/n for n in ('home','settings','cache','temp')}
    for folder in folders.values():folder.mkdir(parents=True,exist_ok=True)
    # LaunchSupport locates its Java-home cache via Windows environment paths,
    # before the main application's settingsdir override takes effect.
    env['APPDATA']=str(folders['settings'])
    env['LOCALAPPDATA']=str(folders['cache'])
    options={'user.home':folders['home'],'application.settingsdir':folders['settings'],
             'application.cachedir':folders['cache'],'application.tempdir':folders['temp'],'java.io.tmpdir':folders['temp']}
    env['JAVA_TOOL_OPTIONS']=' '.join(f'-D{k}={v.as_posix()}' for k,v in options.items())
    return env


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rebuild',action='store_true',help='Replace the imported pilot program, losing manual annotations')
    args=parser.parse_args()
    if not (GHIDRA/'support/analyzeHeadless.bat').exists() or not (JDK/'bin/javac.exe').exists():
        parser.error('Run python -m tools.setup_reverse_tools first')
    profile=json.loads((ROOT/'emulator/profiles/b220-l7a.json').read_text())
    image,identity=load_firmware(profile);source=Path(identity['source'])
    projects=OUTPUT/'projects';projects.mkdir(parents=True,exist_ok=True)
    name='B220-original-pilot';project=projects/(name+'.gpr')
    if project.exists() and not args.rebuild:
        print(f'Project already exists, keeping annotations: {project}');return
    exports=OUTPUT/'original';exports.mkdir(parents=True,exist_ok=True)
    command=[str(GHIDRA/'support/analyzeHeadless.bat'),str(projects),name,
             '-import',str(source),'-loader','BinaryLoader','-loader-baseAddr','0x0',
             '-processor','ARM:BE:32:v4t','-cspec','default','-noanalysis',
             '-scriptPath',str(ROOT/'tools/ghidra'),'-postScript','B220Pilot.java',str(exports),
             '-log',str(OUTPUT/'headless.log'),'-scriptlog',str(OUTPUT/'scripts.log')]
    if args.rebuild:command.append('-overwrite')
    result=subprocess.run(command,cwd=ROOT,env=environment())
    if result.returncode:parser.error(f'Ghidra exited with {result.returncode}; see analysis/reverse/headless.log')
    status=exports/'status.txt'
    if not status.exists() or 'decompiled=9' not in status.read_text():parser.error('Pilot export incomplete; see analysis/reverse/scripts.log')
    if source.read_bytes()!=image:parser.error('Original PS changed during analysis')
    (OUTPUT/'manifest.json').write_text(json.dumps(dict(source=identity,project=str(project),language='ARM:BE:32:v4t',
        exports=str(exports),scope='Nine bounded original functions; manual modes/names and initial RAM copy map',original_ps_unchanged=True),indent=2)+'\n',encoding='utf-8')
    print(f'Project: {project}\nPseudocode: {exports}\n{status.read_text()}')


if __name__=='__main__':main()
