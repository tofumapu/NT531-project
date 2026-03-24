from flask import Flask, render_template, request, make_response, g
from redis import Redis
import os
import socket
import json
import logging

option_a = os.getenv('OPTION_A', "Kem Chanh")
option_b = os.getenv('OPTION_B', "Kem Dâu")
hostname = socket.gethostname()

app = Flask(__name__)

gunicorn_error_logger = logging.getLogger('gunicorn.error')
app.logger.handlers.extend(gunicorn_error_logger.handlers)
app.logger.setLevel(logging.INFO)

def get_redis():
    if not hasattr(g, 'redis'):
        g.redis = Redis(host="redis", db=0, socket_timeout=5)
    return g.redis

@app.route("/", methods=['POST','GET'])
def hello():
    voter_id = request.cookies.get('voter_id') or os.urandom(8).hex()
    weather_report = None

    if request.method == 'POST':
        redis = get_redis()
        weather_report = request.form['vote']
        app.logger.info('Dự báo nhận được: %s', weather_report)
        
        data = json.dumps({'voter_id': voter_id, 'vote': weather_report})
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

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=80, debug=True, threaded=True)