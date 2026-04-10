from flask import Flask, render_template, request, make_response, g, Response
from redis import Redis
import os
import socket
import json
import logging
import time
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from prometheus_client import CollectorRegistry, multiprocess

option_a = os.getenv('OPTION_A', "Kem Chanh")
option_b = os.getenv('OPTION_B', "Kem Dâu")
hostname = socket.gethostname()

app = Flask(__name__)

gunicorn_error_logger = logging.getLogger('gunicorn.error')
app.logger.handlers.extend(gunicorn_error_logger.handlers)
app.logger.setLevel(logging.INFO)

REQUEST_COUNT = Counter(
    'vote_http_requests_total',
    'Total HTTP requests handled by the vote service',
    ['method', 'endpoint', 'status']
)
REQUEST_LATENCY = Histogram(
    'vote_http_request_duration_seconds',
    'HTTP request latency for the vote service',
    ['method', 'endpoint']
)
VOTE_SUBMISSIONS = Counter(
    'vote_vote_submissions_total',
    'Total vote submissions by option',
    ['vote']
)


def _request_endpoint():
    if request.url_rule and request.url_rule.rule:
        return request.url_rule.rule
    return request.path

def get_redis():
    if not hasattr(g, 'redis'):
        g.redis = Redis(host="redis", db=0, socket_timeout=5)
    return g.redis


@app.before_request
def before_request():
    g.request_start_time = time.perf_counter()


@app.after_request
def after_request(response):
    endpoint = _request_endpoint()
    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=endpoint,
        status=response.status_code
    ).inc()

    start_time = getattr(g, 'request_start_time', None)
    if start_time is not None:
        REQUEST_LATENCY.labels(
            method=request.method,
            endpoint=endpoint
        ).observe(time.perf_counter() - start_time)

    return response

@app.route("/", methods=['POST','GET'])
def hello():
    voter_id = request.cookies.get('voter_id') or os.urandom(8).hex()
    weather_report = None

    if request.method == 'POST':
        redis = get_redis()
        weather_report = request.form['vote']
        app.logger.info('Dự báo nhận được: %s', weather_report)
        data = json.dumps({'voter_id': voter_id, 'vote': weather_report})
        VOTE_SUBMISSIONS.labels(vote=weather_report).inc()
        redis.rpush('votes', data)

    resp = make_response(render_template(
        'index.html',
        option_a=option_a,
        option_b=option_b,
        hostname=hostname,
        vote=weather_report,
    ))
    resp.set_cookie('voter_id', voter_id)
    return resp


@app.route("/metrics")
def metrics():
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        output = generate_latest(registry)
    else:
        output = generate_latest()

    return Response(output, mimetype=CONTENT_TYPE_LATEST)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=80, debug=True, threaded=True)
