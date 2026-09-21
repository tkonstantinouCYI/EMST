"""Single-command reproduction after installing requirements-lock.txt."""
import subprocess
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for script in ('run_experiments.py', 'audit_results.py', 'audit_auction_prices.py', 'audit_resolution.py', 'robustness_analysis.py', 'plot_results.py'):
    result = subprocess.run([sys.executable, str(HERE/script)], cwd=ROOT, check=True, capture_output=True, text=True)
    print(result.stdout)
    if script == 'audit_results.py':
        (HERE/'results/audit.txt').write_text(result.stdout)
subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests', str(HERE/'test_tie_policy.py')], cwd=ROOT, check=True)
print('Reproduction complete: results/ and plots/ updated. Timings depend on the machine.')
