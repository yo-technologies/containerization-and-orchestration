#!/usr/bin/env python3
"""Demonstrate one alert with a bounded API outage and restore normal Helm values."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import subprocess
import time

LAB = Path(__file__).resolve().parents[1]
KUBE = ['kubectl', '--kubeconfig', str(Path.home()/'.local/state/container-lab/lab4.kubeconfig'), '--context', 'kind-shop-lab4']
HELM = ['helm', 'upgrade', 'shop', str(LAB/'helm/shop'), '--kubeconfig', KUBE[2], '--kube-context', 'kind-shop-lab4', '-n', 'shop', '--reset-values']

def command(args, timeout=60):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=timeout)

def now():
    return datetime.now(timezone.utc).isoformat()

def alerts():
    path = '/api/v1/namespaces/observability/services/http:prometheus-server:80/proxy/api/v1/alerts'
    data = json.loads(command(KUBE+['--request-timeout=25s', 'get', '--raw', path], 30))
    if data.get('status') != 'success':
        raise RuntimeError(data)
    return [a for a in data['data']['alerts'] if a['labels'].get('alertname') == 'ShopAPIUnavailable']

def main(output):
    if output.exists():
        raise FileExistsError(f'Choose a new evidence filename: {output}')
    pods = json.loads(command(KUBE+['-n','shop','get','pods','-l','app.kubernetes.io/name=api','-o','json']))['items']
    active = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
    if len(active) != 3 or not all(any(c['type']=='Ready' and c['status']=='True' for c in p['status'].get('conditions',[])) for p in active):
        raise RuntimeError('Expected three healthy API replicas before the demo')
    result = {'started_at':now(), 'alert':'ShopAPIUnavailable', 'samples':[], 'firing_observed':False, 'resolved_observed':False}
    output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    try:
        print('Applying api.replicas=0 through Helm. This intentionally interrupts order requests.',flush=True)
        result['outage_helm'] = command(HELM+['-f',str(LAB/'experiments/alert-api-down.yaml')],90)
        deadline = time.monotonic()+240
        while time.monotonic()<deadline:
            rows=alerts();result['samples'].append({'time':now(),'alerts':rows});save()
            print('ShopAPIUnavailable:', ', '.join(a['state'] for a in rows) or 'inactive',flush=True)
            if any(a['state']=='firing' for a in rows):
                result['firing_observed']=True
                break
            time.sleep(5)
        if not result['firing_observed']:
            raise RuntimeError('Alert did not reach Firing within four minutes')
    finally:
        print('Restoring normal Helm values and three API replicas.',flush=True)
        try:
            result['restore_helm']=command(HELM+['--wait','--timeout','5m'],330)
            result['restored_at']=now()
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                rows=alerts();result['samples'].append({'time':now(),'phase':'restored','alerts':rows})
                if not rows:
                    result['resolved_observed']=True
                    break
                time.sleep(5)
            result['pods_after']=json.loads(command(KUBE+['-n','shop','get','pods','-o','json']))
        except BaseException as error:
            result['restore_error']=str(error)
            raise
        finally:
            result['completed_at']=now();save()
    if not result['resolved_observed']:
        raise RuntimeError('API restored, but alert has not cleared; inspect saved evidence')
    print(json.dumps({'firing_observed':result['firing_observed'],'resolved_observed':result['resolved_observed'],'evidence':str(output)},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=LAB/'evidence/part-9-alert-demo.json')
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    main(parser.parse_args().output)
