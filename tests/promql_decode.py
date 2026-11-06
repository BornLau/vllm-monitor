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
        for suffix, value in (("sum", 2), ("count", 100)):
            series.append({
                "series": f'vllm:request_time_per_output_token_seconds_{suffix}{{job="vllm",{MODEL},worker="{worker}"}}',
                "values": f'{value * 10 if reset else 0} {value} {value * 2} {value * 3} {value * 3}',
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
    case("mean reciprocal is 50 tok/s; no new completions produces no speed", histogram(), [
        check(tpot, .02), check(speed, 50), check(speed, None, "12s")]),
    case("reset counters retain request speed", histogram(reset=True), [check(speed, 50)]),
    case("concurrency does not multiply per-request speed", histogram(multiplier=2), [check(speed, 50)]),
    case("mean is weighted by completed requests, not worker count", [
        {"series": f'vllm:request_time_per_output_token_seconds_{suffix}{{job="vllm",{MODEL},worker="{worker}"}}',
         "values": f"0 {value}"}
        for worker, total, count in ((0, 1, 100), (1, 7, 300))
        for suffix, value in (("sum", total), ("count", count))], [
            check(tpot, .02), check(speed, 50)]),
    case("unpaired sum or count cannot distort the mean", histogram() + [
        {"series": f'vllm:request_time_per_output_token_seconds_sum{{job="vllm",{MODEL},worker="sum_only"}}',
         "values": "0 900"},
        {"series": f'vllm:request_time_per_output_token_seconds_count{{job="vllm",{MODEL},worker="count_only"}}',
         "values": "0 900"}], [check(tpot, .02), check(speed, 50)]),
    case("missing TPOT does not fall back to token throughput; activity remains valid", [
        {"series": f'vllm:generation_tokens_total{{job="vllm",{LABELS}}}', "values": "0 300"}], [
            check(speed, None), check(app.QUERIES["activity"], 1),
            check(app.QUERIES["generation_tps"], 100)]),
    case("total throughput sums per-series rates after resets and preserves idle zero", [
        {"series": f'vllm:generation_tokens_total{{job="vllm",{LABELS},worker="0"}}', "values": "900 300 300"},
        {"series": f'vllm:generation_tokens_total{{job="vllm",{LABELS},worker="1"}}', "values": "0 600 600"}], [
            check(app.QUERIES["generation_tps"], 300),
            check(app.QUERIES["generation_tps"], 0, "6s")]),
    case("zero TPOT does not divide by zero", [
        {"series": f'vllm:request_time_per_output_token_seconds_{suffix}{{job="vllm",{MODEL}}}',
         "values": f"0 {value}"} for suffix, value in (("sum", 0), ("count", 100))], [check(speed, None)]),
    case("average samples use decode speed and exclude empty observations", histogram() + [up], [
        check(averages["output_average"], 50, "12s", MODEL),
        check(averages["output_active_seconds"], 9, "12s", MODEL),
        check(averages["output_observed_seconds"], 9, "12s", MODEL)]),
    case("failed scrape is excluded from averages", histogram() + [{**up, "values": "0 0 0 0 0"}], [
        check(averages["output_average"], None, "12s")]),
    case("stale sum/count samples do not supply fresh decode samples", [
        {**series, "values": " ".join(series["values"].split()[:2]) + " _ _ _"}
        for series in histogram()], [
            check(app.decode_speed(app.request_tpot(app.GROUP + ',model_name', 6)), None, "12s")]),
]

if __name__ == "__main__":
    print(json.dumps({"evaluation_interval": "3s", "tests": tests}, indent=2))
