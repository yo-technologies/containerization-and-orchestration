import argparse, importlib.util, tempfile, unittest
from unittest.mock import patch
from pathlib import Path
spec=importlib.util.spec_from_file_location('gate',Path(__file__).with_name('gate.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class GateSafety(unittest.TestCase):
    def create(self):
        args=argparse.Namespace(output='/tmp/unused',client='',phase='test',interval=15,max_seconds=500)
        with patch.object(module.Gate,'read_weight',return_value=10): gate=module.Gate(args)
        gate.changed=0;gate.events=[]
        gate.event=lambda action,**kw: gate.events.append((action,gate.weight,kw))
        def change(value): gate.weight=value;gate.healthy=0;gate.changed=clock[0]
        gate.set_weight=change
        return gate
    def run_gate(self, observations):
        gate=self.create(); observations=iter(observations)
        def observe():
            value=next(observations)
            if isinstance(value,Exception): raise value
            return value
        gate.observe=observe
        def sleep(seconds): clock[0]+=seconds
        with patch.object(module.time,'time',lambda:clock[0]),patch.object(module.time,'sleep',sleep):
            result=gate.run()
        return result,gate
    def setUp(self):
        global clock;clock=[100]
    def test_missing_metrics_never_promote(self):
        result,g=self.run_gate([ValueError('no metric')]*3)
        self.assertEqual(g.weight,0);self.assertEqual(result,2)
        self.assertFalse(any(e[0]=='promote' for e in g.events))
    def test_bad_release_rolls_back(self):
        result,g=self.run_gate([{'requests':60,'error_rate':1.,'p95_ms':4}])
        self.assertEqual((result,g.weight),(0,0))
        self.assertEqual(g.events[-1][0],'rollback-unhealthy')
    def test_one_good_sample_cannot_promote(self):
        result,g=self.run_gate([{'requests':60,'error_rate':0.,'p95_ms':4},ValueError('gone'),ValueError('gone'),ValueError('gone')])
        self.assertEqual(g.weight,0)
        self.assertFalse(any(e[0]=='promote' for e in g.events))
    def test_good_release_verifies_100_percent(self):
        sample={'requests':60,'error_rate':0.,'p95_ms':4}
        result,g=self.run_gate([sample]*30)
        self.assertEqual((result,g.weight),(0,100))
        self.assertEqual([e[1] for e in g.events if e[0]=='promote'],[25,50,100])
        self.assertEqual(g.events[-1][0],'promoted-and-verified')
    def test_high_latency_rolls_back(self):
        result,g=self.run_gate([{'requests':60,'error_rate':0.,'p95_ms':300}])
        self.assertEqual(g.weight,0)

unittest.main() if __name__=='__main__' else None
