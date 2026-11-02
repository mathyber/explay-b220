"""Assemble the small, register-based B22V v1 language used by the native loader."""
import argparse
from pathlib import Path
import shlex
import struct

EVENTS=dict(UP=0xfa01,DOWN=0xfa02,LEFT=0xfa03,RIGHT=0xfa04,OK=0xfa1b,OPEN=0xf021)
OPS={'halt':0,'set':1,'add':2,'mov':3,'jmp':4,'jeq':5,'jne':6,'jlt':7,
     'clear':8,'rect':9,'text':10,'event':11,'yield':12,'restart':13}
ARITY=dict(halt=0,set=2,add=2,mov=2,jmp=1,jeq=3,jne=3,jlt=3,clear=1,rect=1,text=3,event=1,restart=0)
ARITY['yield']=1


def assemble(source):
    instructions=[];labels={};strings=bytearray()
    for lineno,line in enumerate(source.splitlines(),1):
        lexer=shlex.shlex(line,posix=True);lexer.whitespace_split=True;lexer.commenters=';'
        words=list(lexer)
        if not words:continue
        if words[0].endswith(':'):
            name=words.pop(0)[:-1]
            if not name or name in labels:raise ValueError(f'Line {lineno}: duplicate/empty label')
            labels[name]=len(instructions)
        if not words:continue
        op=words.pop(0).lower()
        if op not in OPS or len(words)!=ARITY[op]:raise ValueError(f'Line {lineno}: invalid instruction/argument count')
        instructions.append((lineno,op,words))
    if not 1<=len(instructions)<=256:raise ValueError('Expected 1..256 instructions')
    def reg(s):
        if len(s)!=2 or s[0].lower()!='r' or s[1] not in '01234567':raise ValueError(f'Invalid register {s}')
        return int(s[1])
    def integer(s):
        value=EVENTS[s] if s in EVENTS else int(s,0)
        if not -(1<<31)<=value<1<<32:raise ValueError('Immediate is outside 32 bits')
        return value&0xffffffff
    def target(s):
        value=labels[s] if s in labels else int(s,0)
        if not 0<=value<len(instructions):raise ValueError('Branch target outside code')
        return value
    code=bytearray()
    for lineno,op,args in instructions:
        a=b=value=0
        try:
            if op in ('set','add'):a,value=reg(args[0]),integer(args[1])
            elif op=='mov':a,b=map(reg,args)
            elif op in ('jmp','yield'):value=target(args[0])
            elif op in ('jeq','jne','jlt'):a,b,value=reg(args[0]),reg(args[1]),target(args[2])
            elif op=='clear':
                value=integer(args[0])
                if value>65535:raise ValueError('RGB565 color exceeds 16 bits')
            elif op in ('rect','event'):
                a=reg(args[0])
                if op=='rect' and a>3:raise ValueError('Rectangle needs five consecutive registers')
            elif op=='text':
                a,b=reg(args[0]),reg(args[1])
                raw=args[2].encode('ascii')
                if len(raw)>64 or any(c<32 or c>126 for c in raw):raise ValueError('Text must be 0..64 printable ASCII characters')
                value=len(strings);strings.extend(raw+b'\0')
        except (ValueError,UnicodeError) as exc:raise ValueError(f'Line {lineno}: {exc}') from exc
        code.extend(struct.pack('>4BI',OPS[op],a,b,0,value))
    result=struct.pack('>4sHHHHI',b'B22V',1,16,len(instructions),0,len(strings))+code+strings
    if len(result)>4096:raise ValueError('Application exceeds the 4096-byte loader limit')
    return bytes(result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    try:
        if args.source.resolve()==args.output.resolve():raise ValueError('Output would overwrite the source')
        data=assemble(args.source.read_text(encoding='utf-8-sig'))
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_bytes(data)
    except (ValueError,OSError) as exc:parser.error(str(exc))
    print(f'{args.output}: {len(data)} bytes')


if __name__=='__main__':main()
