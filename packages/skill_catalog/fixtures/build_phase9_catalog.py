"""Deterministically regenerate the three immutable first-party Phase 9 assets."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from autocad_contracts.phase9_contracts import canonical_skill_manifest_digest, canonical_workflow_definition_digest
ROOT = Path(__file__).resolve().parents[1]
SKILLS = [("mechanical.auto-dimension-overall", "mechanical", "autocad.write", "run_planner"), ("drawing.cleanup-audit", "drawing", "autocad.read", "run_planner"), ("mechanical.plate-hole-pattern", "mechanical", "autocad.write", "render_template")]
def dump(p, x): p.write_text(json.dumps(x, sort_keys=True, separators=(",",":"), ensure_ascii=False)+"\n", encoding="utf-8")
def sha(b): return "sha256:"+hashlib.sha256(b).hexdigest()
def schema(properties): return {"type":"object","properties":properties,"required":list(properties),"additionalProperties":False}
def ref(step_id): return {"source_step_id":step_id,"output_path":"result"}
def step(i, kind, deps, bindings=None): return {"step_id":i,"kind":kind,"depends_on":deps,"input_bindings":bindings or {},"output_schema":schema({"result":{"type":"object","properties":{},"additionalProperties":False}}),"timeout_seconds":300,"retry_class":"deterministic" if kind in {"run_planner","render_template"} else "none"}
def main():
    workflows=[]; manifests=[]
    for skill_id, domain, scope, pure_kind in SKILLS:
        folder=ROOT/"skills"/skill_id/"1.0.0"; folder.mkdir(parents=True,exist_ok=True)
        if skill_id == "drawing.cleanup-audit": steps=[step("query","query",[]),step("pure",pure_kind,["query"],{"entities":ref("query")}),step("report","emit_report",["pure"],{"audit":ref("pure")}),step("review","wait_user_input",["report"],{"report":ref("report")}),step("finish","finish",["review"])]
        elif skill_id.startswith("mechanical.auto"):
            steps=[step("observe","observe",[]),step("query","query",["observe"],{"snapshot":ref("observe")}),step("pure",pure_kind,["query"],{"entities":ref("query")}),step("prepare","prepare_program",["pure"],{"program":ref("pure")}),step("preview","preview_program",["prepare"],{"program":ref("prepare")}),step("review","wait_user_input",["preview"],{"preview":ref("preview")}),step("revision","wait_program_revision",["review"],{"review":ref("review")}),step("commit","request_commit",["revision"],{"program":ref("revision")}),step("job","wait_job",["commit"],{"intent":ref("commit")}),step("validate","validate_receipt",["job"],{"job":ref("job")}),step("finish","finish",["validate"])]
        else: steps=[step("pure",pure_kind,[]),step("prepare","prepare_program",["pure"],{"program":ref("pure")}),step("preview","preview_program",["prepare"],{"program":ref("prepare")}),step("commit","request_commit",["preview"],{"preview":ref("preview")}),step("job","wait_job",["commit"],{"intent":ref("commit")}),step("validate","validate_receipt",["job"],{"job":ref("job")}),step("finish","finish",["validate"])]
        workflow={"schema_version":"cad.workflow-definition/1","workflow_id":skill_id,"version":"1.0.0","steps":steps}; workflow["definition_digest"]=canonical_workflow_definition_digest(workflow); dump(folder/"workflow.json",workflow); workflows.append(workflow)
        guide=(folder/"guide.md").read_bytes(); guide_digest=sha(guide)
        catalog_ref={"ref_id":(skill_id+"/1"),"version":"1.0.0","digest":sha((skill_id+"/1").encode())}
        fields={"source_snapshot_id":{"type":"string","minLength":1},"document_revision":{"type":"string","minLength":1},"layer":{"type":"string","minLength":1}}
        if skill_id.startswith("mechanical.auto"): fields.update({"entity_ids":{"type":"array","items":{"type":"string"},"minItems":1},"entities":{"type":"array","items":{"type":"object"},"minItems":1},"profile":{"const":"mechanical_mm"},"offset":{"type":"number","minimum":0.001}})
        elif skill_id.startswith("drawing."): fields.update({"page_size":{"type":"integer","minimum":1,"maximum":512},"max_candidates":{"type":"integer","minimum":1,"maximum":128}})
        else: fields.update({"width":{"type":"number","minimum":0.001},"height":{"type":"number","minimum":0.001},"hole_diameter":{"type":"number","minimum":0.001},"rows":{"type":"integer","minimum":1,"maximum":32},"columns":{"type":"integer","minimum":1,"maximum":32},"margin_x":{"type":"number","minimum":0.001},"margin_y":{"type":"number","minimum":0.001},"include_overall_dimensions":{"type":"boolean"}})
        manifest={"schema_version":"cad.skill/1","skill_id":skill_id,"version":"1.0.0","title":skill_id,"summary":"Bounded first-party Phase 9 reference workflow.","domain":domain,"tags":["phase9"],"input_schema_ref":"input.schema.1","output_schema_ref":"output.schema.1","input_schema":schema(fields),"output_schema":schema({"status":{"type":"string","minLength":1},"artifact_digest":{"type":"string","minLength":1}}),"workflow_definition":{"workflow_id":skill_id,"version":"1.0.0","digest":workflow["definition_digest"]},"required_scopes":["autocad.read",scope] if scope=="autocad.write" else [scope],"required_capabilities":["cad.program.v1.compile"] if scope=="autocad.write" else [],"required_operation_packs":["cad.program/1.0-create-core"] if scope=="autocad.write" else [],"risk_floor":"medium" if scope=="autocad.write" else "low","assurance_floor":"user_recent_auth","planner":catalog_ref if pure_kind=="run_planner" else None,"templates":[catalog_ref] if pure_kind=="render_template" else [],"validation_profiles":["geometry.basic.1","document.revision.1"] if scope=="autocad.write" else [],"budgets":{"max_entities":512},"support_policy":{"mode":"dry_run" if scope=="autocad.read" else "lab_commit"},"guide_digest":guide_digest}
        manifest["manifest_digest"]=canonical_skill_manifest_digest(manifest); dump(folder/"skill.json",manifest); manifests.append(manifest)
    assets={}
    for p in sorted(ROOT.glob("skills/*/1.0.0/*")): assets[p.relative_to(ROOT).as_posix()]=sha(p.read_bytes())
    release={"skills":manifests,"workflows":workflows,"channels":[{"skill_id":x[0],"channel":"default","default_version":"1.0.0","status":"active"} for x in SKILLS]}
    release["release_digest"]=sha(json.dumps(release,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode())
    release["assets"]=assets; dump(ROOT/"catalog.json",release)
if __name__=="__main__": main()
