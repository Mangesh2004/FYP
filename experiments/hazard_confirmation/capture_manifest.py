"""Bind accepted native episodes to their exact production/test source files."""
import datetime
import hashlib
import json
from pathlib import Path

root=Path('D:/FYP/experiments/hazard_confirmation')
previous=json.loads((root/'verified/source_manifest.json').read_text())
hashes={name:hashlib.file_digest(open(name,'rb'),'sha256').hexdigest()
        for name in previous['sha256']}
report=dict(at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),sha256=hashes,
    hazard_matrix_max_time_s=600,standard_fault_max_time_s=300)
(root/'accepted/source_manifest.json').write_text(json.dumps(report,indent=2))
print('Saved',len(hashes),'source hashes.')
