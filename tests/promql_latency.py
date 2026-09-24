"""Executable PromQL cases for observed sum/count latency (JSON is YAML)."""
import json
from promql_decode import app, MODEL, up, check, case

cases = []
for name, base in [('tpot','request_time_per_output_token_seconds'), ('ttft','time_to_first_token_seconds')]:
    expr = app.latency_queries()[name]
    def counter(suffix, values, worker='a'):
        return dict(series=f'vllm:{base}_{suffix}{{job="vllm",{MODEL},worker="{worker}"}}', values=values)
    cases += [
        case(name+' observed mean and no new observations', [up,counter('sum','0 2 6 6 6'),counter('count','0 4 8 8 8')], [check(expr,.5,labels=MODEL),check(expr,1,'6s',MODEL),check(expr,None,'9s',MODEL)]),
        case(name+' counter reset', [up,counter('sum','100 2'),counter('count','200 4')], [check(expr,.5,labels=MODEL)]),
        case(name+' zero latency remains valid', [up,counter('sum','0 0'),counter('count','0 4')], [check(expr,0,labels=MODEL)]),
        case(name+' missing sum excludes paired count', [up,counter('sum','0 2'),counter('count','0 4'),counter('count','0 100','b')], [check(expr,.5,labels=MODEL)]),
        case(name+' missing count excludes paired sum', [up,counter('sum','0 2'),counter('count','0 4'),counter('sum','0 100','b')], [check(expr,.5,labels=MODEL)]),
        case(name+' stale observations excluded', [up,counter('sum','0 2 _ _ _'),counter('count','0 4 _ _ _')], [check(expr,None,'12s',MODEL)]),
        case(name+' failed scrape excluded', [{**up,'values':'0 0'},counter('sum','0 2'),counter('count','0 4')], [check(expr,None,labels=MODEL)]),
    ]
print(json.dumps(dict(evaluation_interval='3s',tests=cases)))
