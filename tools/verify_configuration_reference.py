"""Prove the selectable expanded reference still builds the aff3574 MILP."""
import argparse,hashlib,io,json,subprocess,sys,tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser();p.add_argument('--worker',action='store_true');p.add_argument('--baseline',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.worker:
        sys.path.insert(0,str(a.baseline or ROOT))
        import emst
        if a.baseline:
            original=emst.Market.add_configured_thermal
            def compatible(self,*args,**kwargs):kwargs.pop('configuration_formulation',None);return original(self,*args,**kwargs)
            emst.Market.add_configured_thermal=compatible
        sys.path.insert(0,str(ROOT))
        from tools.benchmark_configurations import capture
        from examples.synthetic_configured_fleet import synthetic_case
        model=capture(synthetic_case('small'),'reference')['problem']
        structure=dict(variables=[(v.name,v.cat,v.lowBound,v.upBound) for v in model.variables()],
            objective=(model.objective.constant,sorted((v.name,c) for v,c in model.objective.items() if c)),
            constraints=sorted((c.sense,c.constant,tuple(sorted((v.name,n) for v,n in c.items() if n))) for c in model.constraints.values()))
        raw=json.dumps(structure,sort_keys=True)
        a.output.write_text(json.dumps(dict(sha256=hashlib.sha256(raw.encode()).hexdigest(),variables=len(model.variables()),constraints=len(model.constraints)),indent=2));return
    root=ROOT/'tmp/configurations-aff3574-reference';root.mkdir(parents=True,exist_ok=True)
    archive=subprocess.check_output(['git','archive','aff3574','emst'],cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for entry in tar.getmembers():
            if not (root/entry.name).resolve().is_relative_to(root.resolve()):raise ValueError('Unsafe archive path')
        tar.extractall(root,filter='data')
    a.output.mkdir(parents=True,exist_ok=True)
    for label,baseline in (('original',root),('reference',None)):
        command=[sys.executable,str(Path(__file__).resolve()),'--worker','--output',str(a.output/(label+'.json'))]
        if baseline:command+=['--baseline',str(baseline)]
        subprocess.run(command,check=True)
    original=json.loads((a.output/'original.json').read_text());current=json.loads((a.output/'reference.json').read_text())
    assert original==current,(original,current)
    (a.output/'verification.json').write_text(json.dumps(dict(passed=True,reference_commit='aff3574',**current),indent=2))
    print('Reference model matches aff3574 exactly:',current)

if __name__=='__main__':main()
