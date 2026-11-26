"""CI-only client version injection and deterministic Android version codes."""
from __future__ import annotations
import argparse,json,os,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clients.release import sync_versions
STABLE=re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
BETA=re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-beta\.([1-9]\d*)$")
def version_code(version):
    base=version.split("-")[0]
    match=BETA.fullmatch(version)
    if not STABLE.fullmatch(base) or (version!=base and not match):
        raise ValueError("Invalid client version "+version)
    major,minor,patch=map(int,base.split("."))
    beta=int(match[4]) if match else None
    if not 1<=major<=19 or minor>99 or patch>99 or (beta is not None and beta>9998):
        raise ValueError("Version exceeds reserved Android code range")
    return major*100_000_000+minor*1_000_000+patch*10_000+(beta if beta is not None else 9999)
def parse_refs(refs,base):
    prefix="refs/tags/v"+base+"-beta."
    indices=[]
    for line in refs.splitlines():
        ref=line.split()[-1] if line.split() else ""
        if ref.startswith(prefix):
            suffix=ref[len(prefix):]
            if re.fullmatch(r"[1-9]\d*",suffix):indices.append(int(suffix))
    return max(indices,default=0)
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("apply","select","prepare"))
    parser.add_argument("--version")
    parser.add_argument("--output")
    args=parser.parse_args()
    config=json.loads((ROOT/"clients/config.json").read_text(encoding="utf-8"))
    plan_path=ROOT/"clients/release-plan.json"
    plan=json.loads(plan_path.read_text(encoding="utf-8"))
    target=plan["nextVersion"]
    if not STABLE.fullmatch(target):raise ValueError("Invalid nextVersion")
    if args.command=="apply":
        version=args.version or os.environ.get("XTYF_VERSION","")
        code=version_code(version)
        if os.environ.get("XTYF_CODE") and int(os.environ["XTYF_CODE"])!=code:raise ValueError("Android code mismatch")
        if version.split("-")[0]!=target and version!=config["version"]:
            raise ValueError("Build version outside approved plan")
        sync_versions(ROOT,{**config,"version":version,"versionCode":code})
        print("Injected",version,code)
    elif args.command=="select":
        if config["version"]==target:raise ValueError("Next version must differ from current stable")
        marker=ROOT/".github/release-intent.json"
        intent=json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
        stable=intent.get("version")==config["version"] and bool(STABLE.fullmatch(config["version"]))
        completed=False
        sha=os.environ.get("GITHUB_SHA","")
        repo=os.environ.get("GITHUB_REPOSITORY","")
        if not re.fullmatch(r"[0-9a-f]{40}",sha) or not repo:
            raise RuntimeError("Missing main source SHA / repository")
        if stable:
            # A stable release must be approved through a merged release/X.Y.Z PR.
            # Merely modifying release-intent.json on a regular main commit is not approval.
            sha=os.environ.get("GITHUB_SHA","")
            repo=os.environ.get("GITHUB_REPOSITORY","")
            if not re.fullmatch(r"[0-9a-f]{40}",sha) or not repo:
                raise RuntimeError("Missing main commit identity for stable release")
            prs=json.loads(subprocess.check_output(
                ["gh","api",f"repos/{repo}/commits/{sha}/pulls"],text=True))
            stable=any(p.get("merged_at") and p.get("base",{}).get("ref")=="main"
                       and p.get("head",{}).get("ref")=="release/"+config["version"]
                       for p in prs)
            if stable:
                proc=subprocess.run(["gh","release","view","v"+config["version"],"--json","assets","--jq",".assets | length"],capture_output=True,text=True)
                if proc.returncode==0:
                    count=int(proc.stdout.strip())
                    if count not in (0,9):raise ValueError("Partial stable release: investigate before retrying")
                    completed=count==9
                elif "release not found" not in proc.stderr.lower() and "404" not in proc.stderr:
                    raise RuntimeError(proc.stderr)
        if stable:
            if config["versionCode"]!=version_code(config["version"]):
                raise ValueError("Stable Android versionCode mismatch")
            version=config["version"]
        else:
            if tuple(map(int,target.split(".")))<=tuple(map(int,config["version"].split("."))):
                raise ValueError("Next beta target must exceed current stable")
            refs=subprocess.check_output(["git","ls-remote","--tags","--refs","origin","refs/tags/v"+target+"-beta.*"],text=True)
            prefix="refs/tags/v"+target+"-beta."
            same=[]
            for line in refs.splitlines():
                fields=line.split()
                if len(fields)==2 and fields[0]==sha and fields[1].startswith(prefix):
                    suffix=fields[1][len(prefix):]
                    if re.fullmatch(r"[1-9]\d*",suffix):same.append(int(suffix))
            # An Actions rerun reuses the tag associated with this exact source.
            number=min(same) if same else parse_refs(refs,target)+1
            version=target+"-beta."+str(number)
            tag="v"+version
            proc=subprocess.run(["gh","release","view",tag,"--json","assets,isPrerelease,isDraft",
                                 "--jq",'{count:(.assets|length),draft:.isDraft,prerelease:.isPrerelease}'],
                                capture_output=True,text=True)
            if proc.returncode==0:
                state=json.loads(proc.stdout)
                if state["draft"] or not state["prerelease"] or state["count"] not in (0,9):
                    raise ValueError("Existing beta release cannot be safely reused")
                completed=state["count"]==9
                if not same:raise ValueError("Beta tag belongs to a different source commit")
            elif "release not found" not in proc.stderr.lower() and "404" not in proc.stderr:
                raise RuntimeError(proc.stderr)
        vals={"version":version,"tag":"v"+version,"channel":"stable" if stable else "prerelease","code":str(version_code(version)),"already_published":str(completed).lower()}
        output=Path(args.output or os.environ["GITHUB_OUTPUT"])
        with output.open("a",encoding="utf-8") as file:
            for name,value in vals.items():file.write(f"{name}={value}\n")
        print("Selected:",vals)
    else:
        version=args.version or target
        if version!=target or not STABLE.fullmatch(version):raise ValueError("Must prepare planned stable version")
        if tuple(map(int,version.split(".")))<=tuple(map(int,config["version"].split("."))):raise ValueError("Version is not newer")
        sync_versions(ROOT,{**config,"version":version,"versionCode":version_code(version)})
        major,minor,patch=map(int,version.split("."))
        plan["nextVersion"]=f"{major}.{minor}.{patch+1}"
        plan_path.write_text(json.dumps(plan,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
        (ROOT/".github/release-intent.json").write_text(json.dumps({"version":version,"approvedVia":"release-pr"},indent=2)+"\n",encoding="utf-8")
        print("Prepared release",version,"next beta",plan["nextVersion"])
if __name__=="__main__":main()
