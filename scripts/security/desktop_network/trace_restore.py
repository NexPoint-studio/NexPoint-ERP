"""Observe exception types in the failing QA restore test without changing behavior."""
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[3]
events = []

def trace(frame, event, arg):
    if frame.f_code.co_name != 'apply_pending_restore':
        return None
    if event == 'exception':
        value = arg[1]
        events.append({'line': frame.f_lineno, 'exception': type(value).__name__,
                       'errno': getattr(value, 'errno', None),
                       'winerror': getattr(value, 'winerror', None)})
    return trace

sys.argv = [__file__, '-k', 'pending_restore_applies_offline',
            '--basetemp', str(ROOT / 'artifacts/security/desktop/restore_trace_temp'),
            '--junitxml', str(ROOT / 'artifacts/security/desktop/restore_trace.xml')]
sys.settrace(trace)
try:
    runpy.run_path(str(Path(__file__).with_name('run_discovery.py')), run_name='__main__')
finally:
    sys.settrace(None)
    (ROOT / 'artifacts/security/desktop/restore-exceptions.json').write_text(
        json.dumps(events, indent=2), encoding='utf-8')
