"""Emit promtool fixtures from the actual application queries (JSON is YAML)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
import app


LABELS = 'monitor_project="p",monitor_node="n"'
MODEL = LABELS + ',model_name="m"'


def histogram(reset=False, multiplier=1):
    series = []
    for worker in range(multiplier):
        for le, count in (("0.01", 20), ("0.02", 60), ("0.03", 90), ("0.05", 100), ("+Inf", 100)):
            series.append({
                "series": f'vllm:request_time_per_output_token_seconds_bucket{{job="vllm",{MODEL},worker="{worker}",le="{le}"}}',
                "values": f'{count * 10 if reset else 0} {count} {count * 2} {count * 3} {count * 3}',
            })
    return series


def check(expr, value, at="3s", labels=LABELS):
    return {"expr": expr, "eval_time": at, "exp_samples": [] if value is None else [
        {"labels": "{" + labels + "}", "value": value}]}


def case(name, series, checks):
    return {"name": name, "interval": "3s", "input_series": series, "promql_expr_test": checks}


speed = app.QUERIES["output_tps"]
tpot = app.QUERIES["tpot"]
up = {"series": f'up{{job="vllm",{LABELS}}}', "values": "1 1 1 1 1"}
# A short window exercises the same subquery without promtool's sample limit.
averages = app.average_queries("12s")
tests = [
    case("P95 reciprocal is 25 tok/s; no new completions produces no speed", histogram(), [
        check(tpot, .04), check(speed, 25), check(speed, None, "12s")]),
    case("reset counters retain request speed", histogram(reset=True), [check(speed, 25)]),
    case("concurrency does not multiply per-request speed", histogram(multiplier=2), [check(speed, 25)]),
    case("missing TPOT does not fall back to token throughput; activity remains valid", [
        {"series": f'vllm:generation_tokens_total{{job="vllm",{LABELS}}}', "values": "0 300"}], [
            check(speed, None), check(app.QUERIES["activity"], 1)]),
    case("zero TPOT does not divide by zero", [
        {"series": f'vllm:request_time_per_output_token_seconds_bucket{{job="vllm",{MODEL},le="{le}"}}',
         "values": "0 100"} for le in ("0", "+Inf")], [check(speed, None)]),
    case("average samples use decode speed and exclude empty observations", histogram() + [up], [
        check(averages["output_average"], 25, "12s", MODEL),
        check(averages["output_active_seconds"], 9, "12s", MODEL),
        check(averages["output_observed_seconds"], 9, "12s", MODEL)]),
    case("failed scrape is excluded from averages", histogram() + [{**up, "values": "0 0 0 0 0"}], [
        check(averages["output_average"], None, "12s")]),
    case("stale buckets do not supply fresh decode samples", [
        {**series, "values": " ".join(series["values"].split()[:2]) + " _ _ _"}
        for series in histogram()], [
            check(app.decode_speed(app.request_tpot(app.GROUP + ',model_name', 6)), None, "12s")]),
]

if __name__ == "__main__":
    print(json.dumps({"evaluation_interval": "3s", "tests": tests}, indent=2))
