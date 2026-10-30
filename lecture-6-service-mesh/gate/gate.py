"""Prometheus-driven canary. Helm is the only writer of the traffic split."""
import argparse, json, math, os, subprocess, time, urllib.parse, urllib.request
from pathlib import Path

class Gate:
    def __init__(self, args):
        self.args=args; self.healthy=0; self.missing=0; self.changed=time.time(); self.weight=self.read_weight()
        self.events=[]
    def helm(self,*extra):
        return subprocess.check_output(['helm','--kubeconfig',self.args.kubeconfig,'--kube-context',self.args.context,*extra],text=True)
    def read_weight(self):
        values=json.loads(self.helm('get','values','shop','-n','mesh-shop','-o','json'))
        return int(values['traffic']['v2Weight'])
    def set_weight(self,value):
        self.helm('upgrade','shop',self.args.chart,'-n','mesh-shop','--reuse-values','--set',f'traffic.v2Weight={value}','--wait','--timeout','90s')
        if self.read_weight()!=value: raise RuntimeError('Helm traffic weight did not converge')
        self.weight=value; self.changed=time.time(); self.healthy=0
    def scalar(self,expr):
        url=self.args.prometheus+'/api/v1/query?'+urllib.parse.urlencode({'query':expr})
        response=json.load(urllib.request.urlopen(url,timeout=5))
        values=response.get('data',{}).get('result',[])
        if response.get('status')!='success' or len(values)!=1: raise ValueError('missing or ambiguous metric')
        v=float(values[0]['value'][1])
        if not math.isfinite(v): raise ValueError('non-finite metric')
        return v
    def observe(self):
        scope='destination_service_namespace="mesh-shop",destination_version="v2"'
        total=f'sum(rate(istio_requests_total{{{scope}}}[30s]))'
        error=f'(sum(rate(istio_requests_total{{{scope},response_code=~"5.."}}[30s])) or vector(0)) / ({total})'
        latency=f'histogram_quantile(0.95,sum by(le)(rate(istio_request_duration_milliseconds_bucket{{{scope}}}[30s])))'
        up=self.scalar('max(up{namespace="mesh-shop",service="shop-waypoint-metrics"})')
        age=self.scalar('time() - max(timestamp(istio_requests_total{'+scope+'}))')
        if up!=1 or age>15: raise ValueError('stale/unavailable waypoint')
        return {'requests':self.scalar('sum(increase(istio_requests_total{'+scope+'}[30s]))'),
                'error_rate':self.scalar(error), 'p95_ms':self.scalar(latency)}
    def event(self,action,**extra):
        data={'time':time.time(),'action':action,'weight':self.weight,'phase':self.args.phase,**extra}
        self.events.append(data); print(json.dumps(data),flush=True)
        Path(self.args.output).write_text(json.dumps(self.events,indent=2)+'\n')
        if self.args.client:
            try:
                req=urllib.request.Request(self.args.client+'/event',data=json.dumps({'gate_weight':self.weight,'gate_phase':self.args.phase}).encode(),headers={'Content-Type':'application/json'})
                urllib.request.urlopen(req,timeout=3).close()
            except Exception as exc: print('metrics publication: '+str(exc),flush=True)
    def run(self):
        deadline=time.time()+self.args.max_seconds
        self.event('start')
        while time.time()<deadline:
            time.sleep(self.args.interval)
            try:
                sample=self.observe(); self.missing=0
            except Exception as exc:
                self.missing+=1; self.healthy=0
                self.event('hold-no-telemetry',reason=str(exc))
                if self.missing>=3:
                    self.set_weight(0); self.event('rollback-no-telemetry'); return 2
                continue
            if time.time()-self.changed<40 or sample['requests']<20:
                self.healthy=0; self.event('hold-warmup',**sample); continue
            if sample['error_rate']>.05 or sample['p95_ms']>250:
                self.set_weight(0); self.event('rollback-unhealthy',**sample); return 0
            self.healthy+=1
            if self.healthy<2:
                self.event('hold-confirmation',**sample); continue
            if self.weight==100:
                self.event('promoted-and-verified',**sample); return 0
            next_weight=next(w for w in [10,25,50,100] if w>self.weight)
            self.set_weight(next_weight); self.event('promote',**sample)
        self.set_weight(0); self.event('rollback-deadline'); return 2

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--kubeconfig',default='.state/kubeconfig'); p.add_argument('--context',default='kind-shop-mesh')
    p.add_argument('--chart',default='helm/shop'); p.add_argument('--prometheus',default='http://127.0.0.1:29090')
    p.add_argument('--client',default='http://127.0.0.1:29100'); p.add_argument('--interval',type=float,default=15)
    p.add_argument('--max-seconds',type=int,default=600); p.add_argument('--output',default='evidence/gate.json')
    p.add_argument('--phase',default='good'); a=p.parse_args(); raise SystemExit(Gate(a).run())
