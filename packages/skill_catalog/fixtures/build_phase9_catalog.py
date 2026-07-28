"""Deterministically regenerate the three immutable first-party Phase 9 assets."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from autocad_contracts.phase9_contracts import canonical_skill_manifest_digest, canonical_workflow_definition_digest
ROOT = Path(__file__).resolve().parents[1]
SKILLS = [("mechanical.auto-dimension-overall", "mechanical", "autocad.write", "run_planner"), ("drawing.cleanup-audit", "drawing", "autocad.read", "run_planner"), ("mechanical.plate-hole-pattern", "mechanical", "autocad.write", "render_template")]
def dump(p, x): p.write_text(json.dumps(x, sort_keys=True, separators=(",",":"), ensure_ascii=False)+"\n", encoding="utf-8")
def sha(b): return "sha256:"+hashlib.sha256(b).hexdigest()
def schema(): return {"type":"object","properties":{},"additionalProperties":False}
def step(i, kind, deps): return {"step_id":i,"kind":kind,"depends_on":deps,"input_bindings":{},"output_schema":schema(),"timeout_seconds":300,"retry_class":"deterministic" if kind in {"run_planner","render_template"} else "none"}
def main():
    workflows=[]; manifests=[]
    for skill_id, domain, scope, pure_kind in SKILLS:
        folder=ROOT/"skills"/skill_id/"1.0.0"; folder.mkdir(parents=True,exist_ok=True)
        if skill_id == "drawing.cleanup-audit": steps=[step("query","query",[]),step("pure",pure_kind,["query"]),step("report","emit_report",["pure"]),step("finish","finish",["report"])]
        else: steps=[step("pure",pure_kind,[]),step("prepare","prepare_program",["pure"]),step("preview","preview_program",["prepare"]),step("commit","request_commit",["preview"]),step("job","wait_job",["commit"]),step("validate","validate_receipt",["job"]),step("finish","finish",["validate"])]
        workflow={"schema_version":"cad.workflow-definition/1","workflow_id":skill_id,"version":"1.0.0","steps":steps}; workflow["definition_digest"]=canonical_workflow_definition_digest(workflow); dump(folder/"workflow.json",workflow); workflows.append(workflow)
        guide=(folder/"guide.md").read_bytes(); guide_digest=sha(guide)
        ref={"ref_id":(skill_id+"/1"),"version":"1.0.0","digest":sha((skill_id+"/1").encode())}
        manifest={"schema_version":"cad.skill/1","skill_id":skill_id,"version":"1.0.0","title":skill_id,"summary":"Bounded first-party Phase 9 reference workflow.","domain":domain,"tags":["phase9"],"input_schema_ref":"input.schema.1","output_schema_ref":"output.schema.1","input_schema":schema(),"output_schema":schema(),"workflow_definition":{"workflow_id":skill_id,"version":"1.0.0","digest":workflow["definition_digest"]},"required_scopes":["autocad.read",scope] if scope=="autocad.write" else [scope],"required_capabilities":[],"required_operation_packs":[],"risk_floor":"low","assurance_floor":"user_recent_auth","planner":ref if pure_kind=="run_planner" else None,"templates":[ref] if pure_kind=="render_template" else [],"validation_profiles":["geometry.basic.1","document.revision.1"] if scope=="autocad.write" else [],"budgets":{"max_entities":512},"support_policy":{"mode":"dry_run" if scope=="autocad.read" else "lab_commit"},"guide_digest":guide_digest}
        manifest["manifest_digest"]=canonical_skill_manifest_digest(manifest); dump(folder/"skill.json",manifest); manifests.append(manifest)
    assets={}
    for p in sorted(ROOT.glob("skills/*/1.0.0/*")): assets[p.relative_to(ROOT).as_posix()]=sha(p.read_bytes())
    release={"skills":manifests,"workflows":workflows,"channels":[{"skill_id":x[0],"channel":"default","default_version":"1.0.0","status":"active"} for x in SKILLS]}
    release["release_digest"]=sha(json.dumps(release,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode())
    release["assets"]=assets; dump(ROOT/"catalog.json",release)
if __name__=="__main__": main()
