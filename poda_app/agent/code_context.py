"""Cortex/Quartz-inspired, non-executing source-context tool for code agents.

Pure AST in Python; line-based declarations for other languages. Never loads,
imports, evaluates or runs target files. Existing path grants stay authoritative.
"""
from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any

from . import grants


def inspect_code(path: str) -> dict[str,Any]:
    try:
        target,_=grants.resolve(path,"read")
        if not target.is_file() or target.stat().st_size>500_000:
            raise ValueError("Select a regular source file smaller than 500 KiB")
        if target.suffix.lower() not in {".py",".js",".jsx",".ts",".tsx",".rs",".go",".java",".c",".cpp",".h",".cs"}:
            raise ValueError("Unsupported source language")
        raw=target.read_bytes()
        content=raw.decode("utf-8")
        symbols=[]
        if target.suffix.lower()==".py":
            tree=ast.parse(content,filename=str(target))
            for child in ast.walk(tree):
                if isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                    symbols.append({"name":child.name,"kind":type(child).__name__,"line":child.lineno,
                                    "end_line":getattr(child,"end_lineno",child.lineno)})
            dependencies=[{"module":x.module,"line":x.lineno} for x in ast.walk(tree)
                          if isinstance(x,ast.ImportFrom)]
        else:
            pattern=re.compile(r"^\s*(?:export\s+)?(?:pub\s+)?(?:async\s+)?(?:function|class|interface|struct|enum|fn|type|def)\s+([A-Za-z_][\w]*)",re.M)
            symbols=[{"name":m.group(1),"kind":"declaration","line":content.count("\n",0,m.start())+1}
                     for m in pattern.finditer(content)]
            dependencies=[]
        return {"ok":True,"result":{"path":str(target),"sha256":hashlib.sha256(raw).hexdigest(),
            "symbols":symbols[:150],"dependencies":dependencies[:75],"truncated":len(symbols)>150,
            "note":"Static source inspection only; references and build output require independent verification"},
            "error":None,"verification":"read and parsed granted local source"}
    except (PermissionError,ValueError,UnicodeError,SyntaxError,OSError) as exc:
        return {"ok":False,"result":None,"error":str(exc),"verification":"no code executed"}
