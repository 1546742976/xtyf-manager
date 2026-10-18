"""Compile the existing website templates into an entirely local client UI.

Only templates and public static assets are read. No Flask app, database or user
data is imported. Unsupported Jinja syntax fails the build instead of silently
shipping a page that differs from the website.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from jinja2 import Environment, nodes

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "clients" / "frontend"
DIST = SOURCE / "dist"


def js(value):
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


class Compiler:
    """Small, fail-closed compiler for the Jinja nodes used by this project."""
    def expr(self, node):
        if isinstance(node, nodes.Const): return js(node.value)
        if isinstance(node, nodes.Name): return f"c[{js(node.name)}]"
        if isinstance(node, nodes.Getattr): return f"get({self.expr(node.node)},{js(node.attr)})"
        if isinstance(node, nodes.Getitem):
            if isinstance(node.arg, nodes.Slice):
                start = self.expr(node.arg.start) if node.arg.start else "0"
                end = self.expr(node.arg.stop) if node.arg.stop else "undefined"
                if node.arg.step: raise ValueError("Stepped slices are unsupported")
                return f"String({self.expr(node.node)}??'').slice({start},{end})"
            return f"get({self.expr(node.node)},{self.expr(node.arg)})"
        if isinstance(node, (nodes.List, nodes.Tuple)):
            return "[" + ",".join(map(self.expr, node.items)) + "]"
        if isinstance(node, nodes.CondExpr): return f"({self.expr(node.test)}?{self.expr(node.expr1)}:{self.expr(node.expr2)})"
        if isinstance(node, nodes.And): return f"({self.expr(node.left)}&&{self.expr(node.right)})"
        if isinstance(node, nodes.Or): return f"({self.expr(node.left)}||{self.expr(node.right)})"
        if isinstance(node, nodes.Not): return f"!({self.expr(node.node)})"
        if isinstance(node, nodes.Compare):
            left, parts = self.expr(node.expr), []
            for operation in node.ops:
                right = self.expr(operation.expr)
                if operation.op == "in": parts.append(f"({right}).includes({left})")
                elif operation.op == "notin": parts.append(f"!({right}).includes({left})")
                else: parts.append(f"({left}{ {'eq':'===','ne':'!==','gt':'>','gteq':'>=','lt':'<','lteq':'<='}[operation.op]}{right})")
                left = right
            return "(" + "&&".join(parts) + ")"
        if isinstance(node, nodes.Filter):
            if node.name != "tojson": raise ValueError(f"Unsupported filter {node.name}")
            return f"safeJSON({self.expr(node.node)})"
        if isinstance(node, nodes.Call):
            if not isinstance(node.node, nodes.Name): raise ValueError("Only named helpers may be called")
            name = node.node.name
            if name == "csrf_token": return "''"  # Native transport owns CSRF.
            if name != "url_for": raise ValueError(f"Unsupported helper {name}")
            kwargs = "{" + ",".join(js(k.key)+":"+self.expr(k.value) for k in node.kwargs) + "}"
            return "urlFor(" + ",".join([*(self.expr(a) for a in node.args), kwargs]) + ")"
        raise ValueError(f"Unsupported Jinja expression {type(node).__name__}")

    def assign(self, target, value):
        if isinstance(target, nodes.Name): return f"c[{js(target.name)}]={value};"
        if isinstance(target, nodes.Tuple):
            return "".join(self.assign(item, f"{value}[{i}]") for i, item in enumerate(target.items))
        raise ValueError("Unsupported assignment")

    def body(self, values):
        out = []
        for node in values:
            if isinstance(node, nodes.Output):
                for child in node.nodes:
                    value = js(child.data) if isinstance(child, nodes.TemplateData) else self.expr(child)
                    if not isinstance(child, nodes.TemplateData) and not (isinstance(child, nodes.Filter) and child.name == "tojson"):
                        value = "esc(" + value + ")"
                    out.append(f"o+={value};")
            elif isinstance(node, nodes.If):
                out.append(f"if({self.expr(node.test)}){{{self.body(node.body)}}}")
                for branch in node.elif_: out.append(f"else if({self.expr(branch.test)}){{{self.body(branch.body)}}}")
                if node.else_: out.append("else{"+self.body(node.else_)+"}")
            elif isinstance(node, nodes.For):
                if node.test or node.else_ or node.recursive: raise ValueError("Unsupported extended loop")
                out.append(f"for(const item of {self.expr(node.iter)}){{let c={{...cOuter}};{self.assign(node.target,'item')}{self.body(node.body)}}}")
                # Each loop gets its own lexical parent, including nested loops.
                out[-1] = "{const cOuter=c;"+out[-1]+"}"
            elif isinstance(node, nodes.Assign): out.append(self.assign(node.target, self.expr(node.node)))
            elif isinstance(node, nodes.Include): out.append(f"o+=render({self.expr(node.template)},c,b);")
            elif isinstance(node, nodes.Block): out.append(f"o+=(b[{js(node.name)}]||function(c,b){{let o='';{self.body(node.body)}return o;}})(c,b);")
            elif isinstance(node, nodes.Extends): continue
            else: raise ValueError(f"Unsupported Jinja statement {type(node).__name__}")
        return "\n".join(out)

    def template(self, tree):
        parents = list(tree.find_all(nodes.Extends))
        if parents:
            blocks = ",".join(js(b.name)+":function(c,b){let o='';"+self.body(b.body)+"return o;}" for b in tree.find_all(nodes.Block))
            return f"function(c,b){{return render({self.expr(parents[0].template)},c,{{{blocks},...b}});}}"
        return "function(c,b){let o='';"+self.body(tree.body)+"return o;}"


def adapt_script(text):
    # Route state belongs to the shell; WebView location remains the packaged URL.
    text = re.sub(r"(?<![\w.])(?:window\.)?location\.", "window.XTYFLocation.", text)
    text = re.sub(r"(?<![\w.])history\.", "window.XTYFHistory.", text)
    text = text.replace("请在新标签页重新登录。", "请在应用内重新登录。")
    return text


def adapt_template(name, text):
    if name == "_security_head.html":
        text = text.replace('<script src="{{ url_for(\'static\', filename=\'security.js\') }}"></script>', '')
    if name == "funds.html":
        text = re.sub(r'<link rel="stylesheet" href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/css/bootstrap.min.css"\s+onerror="[^"]*">', '<link rel="stylesheet" href="/vendor/bootstrap/bootstrap.min.css">', text)
        text = re.sub(r'<link rel="stylesheet" href="https://cdn.bootcdn.net/ajax/libs/bootstrap-icons/1.11.3/font/bootstrap-icons.min.css"\s+onerror="[^"]*">', '<link rel="stylesheet" href="/vendor/bootstrap-icons/bootstrap-icons.min.css">', text)
        text = re.sub(r'<script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.3/js/bootstrap.bundle.min.js"\s+onerror="[^"]*"></script>', '<script src="/vendor/bootstrap/bootstrap.bundle.min.js"></script>', text)
    if name == "email_verify.html":
        text = text.replace('target="_blank" rel="noopener"', '').replace('在新标签页登录', '在应用内登录')
    text = re.sub(r"(<script\b[^>]*>)([\s\S]*?)(</script>)", lambda m:m[1]+adapt_script(m[2])+m[3], text)
    return text


def build(output=DIST):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    env, compiler = Environment(), Compiler()
    templates = {}
    for path in sorted((ROOT / "templates").glob("*.html")):
        templates[path.name] = compiler.template(env.parse(adapt_template(path.name, path.read_text(encoding="utf-8-sig"))))
    runtime = (SOURCE / "template-runtime.js").read_text(encoding="utf-8")
    compiled = runtime.replace("/* COMPILED_TEMPLATES */", ",\n".join(js(name)+":"+value for name,value in templates.items()))
    (output / "templates.js").write_text(compiled, encoding="utf-8")
    for path in (ROOT / "static").rglob("*"):
        if not path.is_file(): continue
        destination = output / "static" / path.relative_to(ROOT / "static")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".js":
            content = adapt_script(path.read_text(encoding="utf-8-sig"))
            if path.name == "business-ui.js":
                start, end = content.index('  async function download('), content.index('  document.addEventListener("click"')
                content = content[:start] + '  async function download(url,filename="download"){return action("download:"+url,[],()=>window.XTYF.download(url,filename));}\n' + content[end:]
            if path.name == "invoices.js":
                content = content.replace("new EventSource('/invoices/api/job/stream/'+encodeURIComponent(id))", "new XTYFJobPoller(id)")
                content = content.replace("handleFiles(e.dataTransfer.files)", "window.XTYF.pickInvoiceFiles()")
            if path.name == "discussions.js":
                content = content.replace("url.origin===window.XTYFLocation.origin&&", "window.XTYF.isLocal(url)&&")
                start = "const native=document.createElement('a');native.href=link.href;native.download=link.download;document.body.append(native);native.click();native.remove();feedback.textContent='下载已开始，请查看浏览器下载列表。';"
                content = content.replace(start,"const saved=await window.XTYF.download(link.href,link.download);feedback.textContent=saved.cancelled?'已取消保存。':'附件已保存。';")
            destination.write_text(content, encoding="utf-8")
        else: shutil.copyfile(path, destination)
    for name in ("index.html", "shell.css", "shell.js", "update-runtime.js", "bridge.js", "page-runtime.js", "pdf-viewer.html", "pdf-viewer.js", "external.html"):
        shutil.copyfile(SOURCE / name, output / name)
    lock = json.loads((SOURCE / "vendor-lock.json").read_text(encoding="utf-8"))
    for name, expected in lock["files"].items():
        path = SOURCE / "vendor" / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected: raise ValueError(f"Vendor checksum mismatch: {name}")
        destination = output / "vendor" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    config = json.loads((ROOT / "clients" / "config.json").read_text(encoding="utf-8"))
    manifest = {"protocolVersion":1,"version":config["version"],"templates":sorted(templates),"files":{},"sizes":{}}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "asset-manifest.json":
            name = path.relative_to(output).as_posix()
            manifest["files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest["sizes"][name] = path.stat().st_size
    (output / "asset-manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n", encoding="utf-8")
    print(f"Local frontend: {len(templates)} templates, {len(manifest['files'])} assets -> {output}")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DIST)
    build(parser.parse_args().output_dir)
